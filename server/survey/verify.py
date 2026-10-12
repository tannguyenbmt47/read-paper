"""Chốt chặn của pass trả lời — bản sao của `content_kept()` và `check_slides()`.

Ý tưởng tốt nhất trong luồng đọc-hiểu là `content_kept()`: một phép kiểm **cơ
học**, chạy sau khi model trả lời, chặn nó bịa nội dung. Ở đây cần đúng thứ đó và
cần hơn, vì một con số bịa trong câu trả lời survey là **gán kết quả giả cho tác
giả thật**, mà nhìn bằng mắt thì không ai bắt được: câu văn trôi chảy, có mã
nguồn kèm sau, trông y hệt câu đúng.

Sáu phép kiểm, xếp theo mức độ bắt được thứ nguy hiểm:

1. **Số phải có mặt nguyên văn trong đoạn đã trích** — bắt bịa số liệu.
2. **Mã đoạn phải có thật và phải nằm trong tập vừa lấy về** — bắt bịa nguồn.
3. **Khẳng định phải có trích dẫn** — bắt câu trôi nổi không nguồn.
4. **Tên bài / năm nhắc trong câu trả lời phải khớp bảng `paper`** — bắt gán nhầm bài.
5. **Rò chữ Hán** — dùng lại `pipeline.cjk_leak()`.
6. **Chấm kéo theo bằng model (tuỳ chọn)** — bắt *trích dẫn hình thức*: có mã
   nguồn nhưng đoạn ấy không nói điều câu đó khẳng định.

**Cảnh báo chứ không chặn.** Khác `content_kept()` (chặn hẳn), vì ở đây người
dùng có màn hình để tự soát, và nuốt mất câu trả lời còn tệ hơn hiện nó kèm cờ
đỏ. Nhưng cảnh báo phải **đúng chỗ**: neo theo chỉ số câu, để giao diện tô được
đúng câu chứ không tô cả bài.
"""

from __future__ import annotations

import os
import re

from .. import depth, llm, pipeline, tieng_viet
from . import db as sdb
from . import prompts

FAST = os.getenv("SURVEY_FAST_MODEL") or llm.FAST_MODEL
NO_REASONING = {"enabled": False}

# Trích dẫn dạng [p3ab12c17] — mã đoạn, chữ và số. Bắt cả cụm [a][b] liền nhau.
CITE = re.compile(r"\[([A-Za-z0-9]{3,40})\]")

# Cắt câu. Không dùng `.` đơn thuần vì "62.3" và "et al." đều có dấu chấm; đòi
# thêm khoảng trắng và chữ hoa/xuống dòng phía sau thì hai trường hợp đó thoát.
SENT = re.compile(r"(?<=[.!?:;])\s+(?=[A-ZĐÀ-Ỹ0-9*#\-])|\n+")

# Dòng không phải khẳng định: tiêu đề, gạch đầu dòng rỗng, hàng kẻ bảng.
_SKIP_LINE = re.compile(r"^\s*(#{1,6}\s|\|[\s\-:|]*\||[-*>]\s*$|\s*$)")

# Số trong câu trả lời — dùng lại nguyên bộ của luồng slide, kể cả phần bỏ qua
# URL và định danh (`Qwen2.5-7B`, `2WikiMQA` không phải số liệu của bài).
_NUM = pipeline._NUM
_URLISH = pipeline._URLISH
_norm = pipeline._norm_num

# Bên NGUỒN thì bóc số rộng tay hơn: cho phép đuôi chữ (`100M`, `7B`, `62.3%`).
#
# Hai bên cố ý KHÔNG đối xứng. Bên câu trả lời phải chặt, vì ở đó ta đang hỏi
# "cái này có phải số liệu bịa không" và `Qwen2.5-7B` không được tính là số liệu.
# Bên nguồn thì chỉ là đống cỏ để tìm kim: bóc rộng hơn chỉ làm giảm báo động
# giả, không thể làm lọt số bịa.
#
# Đã báo oan đúng vì chỗ này: bài ghi `100M frames`, câu trả lời viết "100 triệu
# khung hình", và `_NUM` từ chối `100M` nên "100" thành số không có nguồn.
_NUM_SRC = re.compile(r"(?<![\w.,])(\d+(?:[.,]\d+)*)(?:[eE][-+]?\d+)?")


def source_numbers(text: str) -> set[str]:
    """Mọi số có thể có trong một đoạn nguồn, đã chuẩn hoá để đem đi so."""
    out = {_norm(m.group(1)) for m in _NUM_SRC.finditer(text or "")}
    # `1,000` trong bài và `1000` trong câu trả lời là một số. Bỏ dấu phân nhóm
    # hàng nghìn rồi thêm cả dạng đó vào, chứ đừng bắt hai bên viết giống nhau.
    out |= {_norm(m.group(1).replace(",", "").replace(".", ""))
            for m in _NUM_SRC.finditer(text or "") if "," in m.group(1) or "." in m.group(1)}
    # Bài viết số đếm bằng chữ ("on five tasks", "three seeds"), bản tiếng Việt
    # viết bằng số. Báo oan đúng ca này: "5 tác vụ" bị chấm là số không có nguồn.
    # Phía nguồn bóc rộng tay chỉ giảm báo động giả, không làm lọt số bịa.
    out |= {_SO_CHU[w] for w in _CHU_SO.findall((text or "").lower())}
    return out


_SO_CHU = {w: str(i) for i, w in enumerate(
    "zero one two three four five six seven eight nine ten eleven twelve".split())}
_SO_CHU.update(twenty="20", thirty="30", forty="40", fifty="50", hundred="100")
_CHU_SO = re.compile(r"\b(" + "|".join(_SO_CHU) + r")\b")

# Đơn vị đo dính liền sau số: "25.7mm" thì `_NUM` (cố ý chặt, xem trên) chỉ bóc
# được "25", trong khi bài ghi "25.7 mm" — báo oan. Chỉ tách những đơn vị đo vật
# lý: tách mọi chữ cái thì "Qwen2.5-7B" thành "7 B" và kích cỡ mô hình bị đếm là
# số liệu, đúng thứ `_NUM` sinh ra để tránh.
_DON_VI = re.compile(r"(\d)(mm|cm|km|ms|Hz|kHz|GHz|fps|FPS|ms|s|m|kg|g)\b")


def tach_don_vi(text: str) -> str:
    return _DON_VI.sub(r"\1 \2", text or "")


def split_sentences(answer: str) -> list[dict]:
    """Cắt câu trả lời thành câu, giữ vị trí ký tự để giao diện tô đúng chỗ."""
    out: list[dict] = []
    pos = 0
    for line in answer.split("\n"):
        if not _SKIP_LINE.match(line):
            start = pos
            for piece in SENT.split(line):
                piece = piece.strip()
                if not piece:
                    continue
                at = answer.find(piece, start)
                if at < 0:
                    at = start
                out.append({"i": len(out), "text": piece,
                            "start": at, "end": at + len(piece),
                            "cites": CITE.findall(piece)})
                start = at + len(piece)
        pos += len(line) + 1
    return out


def check_answer(survey_id: str, answer: str, allowed: list[str],
                 subs: list[dict] | None = None,
                 covered: list[str] | None = None) -> list[dict]:
    """Soát cơ học. Trả danh sách cảnh báo, mỗi cái neo vào một câu.

    `allowed`: mã đoạn thật sự đã đưa cho model ở lượt này. Model trích một mã
    có thật trong kho nhưng **không** nằm trong tập này cũng là bịa — nó không
    hề đọc đoạn đó.
    """
    warns: list[dict] = []
    sents = split_sentences(answer)
    allow = set(allowed)
    rows = sdb.get_chunks(list(allow))
    papers = sdb.list_papers(survey_id)

    def add(i: int, kind: str, msg: str) -> None:
        warns.append({"i": i, "kind": kind, "msg": msg,
                      "text": depth.cat_gon(sents[i]["text"], 160) if 0 <= i < len(sents) else ""})

    for s in sents:
        # Số thứ tự đầu dòng ("1.", "2)") không phải số liệu: câu trả lời đánh số
        # các bước từng bị báo "có số liệu (1) mà không kèm trích dẫn" ba lần.
        nums = so_lieu(s["text"])
        # Năm đi sát tên bài là metadata đã biết, không phải số liệu thí nghiệm
        # bắt buộc phải xuất hiện trong chính đoạn trích ("World Models (2018)").
        plain = re.sub(r'[*`"“”]', "", s["text"])
        years = {str(p["year"]) for p in papers if p.get("year") and re.search(
            re.escape(_ten_dau(p.get("title") or "")) + r"\s*\(\s*" + str(p["year"]) + r"\s*\)", plain)}
        nums = [n for n in nums if n not in years]
        cites = s["cites"]
        if len(papers) > 1 and re.search(
                r"không có bài (?:nào )?khác (?:trong kho|ngoài)|kho chỉ có (?:một|1) bài", plain, re.I):
            add(s["i"], "khẳng_định_kho", "Kho có " + str(len(papers)) + " bài; chỉ tìm được một bài không có nghĩa các bài khác không tồn tại.")

        # (2) mã bịa hoặc mã không thuộc lượt này
        for c in cites:
            if c not in allow:
                add(s["i"], "cite_lạ",
                    f"trích dẫn [{c}] không nằm trong các đoạn đã lấy về ở lượt này")

        # (3) có số mà không có nguồn — nguy hiểm hơn hẳn câu chữ không nguồn
        if nums and not cites:
            add(s["i"], "số_không_nguồn",
                f"có số liệu ({', '.join(nums[:3])}) mà không kèm trích dẫn")

        # (1) số phải có mặt nguyên văn trong đoạn đã trích
        if nums and cites:
            pool = " ".join(
                f"{rows[c]['text']} {rows[c].get('vi') or ''}" for c in cites if c in rows)
            have = source_numbers(pool)
            missing = [n for n in nums if _norm(n) not in have]
            if missing:
                add(s["i"], "số_bịa",
                    f"số {', '.join(missing[:3])} không có trong đoạn đã trích "
                    f"({', '.join(cites[:3])})")

    # Độ sâu: câu dài, có trích dẫn, mà không nói cơ chế nào. Chỉ soát câu đủ
    # dài — câu ngắn có thể là một khẳng định gọn, đòi nhân quả ở đó là bắt viết
    # dài dòng cho đủ hình thức.
    for s2 in sents:
        for w in depth.check_text(s2["text"]):
            warns.append({"i": s2["i"], **w})

    # (4) tên bài và năm phải khớp kho
    _check_papers(survey_id, answer, warns)

    # (4b) câu nói về bài A mà trích đoạn của bài B — báo cáo 11/10: "Dans WAM,
    # le modèle doit aussi prédire…" trích đoạn của World Models. Người mới tin
    # trích dẫn và học sai bài.
    for i, ten, nguon, _pid in sai_bai(survey_id, answer, sents, rows):
        add(i, "sai_bài", f"câu nói về «{ten}» nhưng đoạn được trích thuộc «{nguon}»")

    # (4c) ngôn ngữ và chữ hỏng (xem `server/tieng_viet.py`)
    ngu = tieng_viet.sai_ngon_ngu(answer)
    if ngu:
        warns.append({"i": -1, "kind": "sai_ngôn_ngữ",
                      "msg": "câu trả lời không viết bằng tiếng Việt", "text": ""})
    else:
        for s3 in sents:
            for l in tieng_viet.loi_chu(s3["text"]):
                add(s3["i"], "chữ_hỏng", tieng_viet.ten_loi(l["loai"]) + f": “{l['doan'][:60]}”")

    # (5) rò chữ Đông Á
    src = " ".join(r["text"] for r in rows.values())
    if pipeline.cjk_leak(answer, src):
        warns.append({"i": -1, "kind": "chữ_hán",
                      "msg": "câu trả lời có chữ Đông Á mà tài liệu gốc không có",
                      "text": ""})

    # Mục chưa có bằng chứng thì câu trả lời BẮT BUỘC phải nói ra. Đây là chỗ
    # hỏng tệ nhất của công cụ: viết trơn tru đè lên chỗ trống, người đọc tưởng
    # đã có câu trả lời trong khi chưa hề có.
    if subs is not None:
        gaps = [s for s in subs if s["id"] not in set(covered or [])]
        if gaps and not re.search(r"chưa tìm thấy|không tìm thấy|chưa có bằng chứng",
                                  answer, re.I):
            warns.append({
                "i": -1, "kind": "giấu_thiếu",
                "msg": "còn " + str(len(gaps)) + " mục chưa có bằng chứng ("
                       + ", ".join(g["id"] for g in gaps)
                       + ") nhưng câu trả lời không nói ra",
                "text": ""})
    return warns


_YEAR = re.compile(r"\b(19|20)\d{2}\b")
_DAU_DONG = re.compile(r"^\s*(?:\d{1,2}[.)]|[-*•])(?:\s+|$)")
_VIET_TAT = re.compile(r"\(\s*(?:gọi tắt là\s+|viết tắt\s+)?([A-Z][A-Za-z0-9\-]{1,11})\s*\)")


def _ten_dau(title: str) -> str:
    """Phần tên của tiêu đề: trước dấu hai chấm, chữ hoa toàn bộ thì đổi về
    dạng thường để so (PDF hay in tiêu đề hoa)."""
    t = (title or "").split(":")[0].strip()
    # Một từ viết hoa là tên riêng ("SONIC"), giữ nguyên; cả câu viết hoa là lối
    # in của PDF ("LATENT ACTION PRETRAINING…"), đổi về dạng thường.
    return t.title() if t.isupper() and " " in t else t


def bi_danh(papers: list[dict], answer: str = "") -> dict[str, set[str]]:
    """Những cách câu văn gọi tên từng bài.

    - phần tên của tiêu đề, nếu ngắn (≤6 từ): "World Models", "SONIC";
    - chữ đầu của dãy từ viết hoa mở đầu tiêu đề: "World Action Models are…" →
      "WAM";
    - tên viết tắt chính câu trả lời tự định nghĩa: "Latent Action Pretraining
      (LAPA)" — tiêu đề không cho ra LAPA, nhưng model hay khai ngay lần đầu.
    """
    out: dict[str, set[str]] = {}
    for p in papers:
        t = _ten_dau(p.get("title") or "")
        a: set[str] = set()
        if t and len(t.split()) <= 6 and len(t) >= 4:
            a.add(t)
        dau = []
        for w in t.split():
            if w[:1].isupper():
                dau.append(w[0])
            else:
                break
        if len(dau) >= 2:
            a.add("".join(dau))
        out[p["id"]] = a
    for m in _VIET_TAT.finditer(answer or ""):
        # Tên phải đứng SÁT ngay trước ngoặc. Dò trong cả khúc trước đó thì
        # "World Models (…) và **World Action Models (DreamZero)**" gán nhầm
        # DreamZero cho bài World Models — đã ra đúng vậy trên câu trả lời thật.
        truoc = answer[max(0, m.start() - 90):m.start()].rstrip(" *_\"“”").lower()
        for pid, a in out.items():
            if any(len(x) >= 6 and truoc.endswith(x.lower()) for x in a):
                a.add(m.group(1))
    return out


def _nhac(text: str, ten: str) -> bool:
    return bool(re.search(r"(?<![\w-])" + re.escape(ten) + r"(?![\w-])", text))


def sai_bai(survey_id: str, answer: str, sents: list[dict], rows: dict) -> list[tuple]:
    """(chỉ số câu, tên bài được nhắc, tên bài của đoạn trích) cho mọi câu nhắc
    bài A mà KHÔNG trích đoạn nào của A, chỉ trích bài khác.

    Câu nhắc cả hai bài ("WAM mở rộng World Models [đoạn của World Models]") thì
    không bắt: có một bài khớp là đủ."""
    papers = sdb.list_papers(survey_id)
    ten = {p["id"]: _ten_dau(p.get("title") or "") for p in papers}
    bd = bi_danh(papers, answer)
    out = []
    for s in sents:
        nguon = {rows[c]["paper_id"] for c in s["cites"] if c in rows}
        if not nguon:
            continue
        nhac = {pid for pid, a in bd.items() if any(_nhac(s["text"], x) for x in a)}
        # Đoạn được trích tự nhắc tới bài A (bài B bàn về A) thì câu nói về A
        # mà trích B là hợp lệ.
        chu = " ".join(rows[c]["text"] for c in s["cites"] if c in rows)
        nhac = {pid for pid in nhac if not any(_nhac(chu, x) for x in bd[pid] if len(x) >= 3)}
        if nhac and not (nhac & nguon):
            pid = sorted(nhac)[0]
            ng = sorted(nguon)[0]
            out.append((s["i"], ten.get(pid, pid), ten.get(ng, ng), pid))
    return out


_TU = re.compile(r"\w{3,}")
# Số KHÔNG phải số liệu: số mục/hình/bảng/bước ("Mục 3.1", "Bảng 2"), chỉ số
# trong công thức (`z_{t+1}`, `h_t`). Câu trả lời thật trên World Models bị báo
# oan 7 lần đúng vì mấy dạng này — "số 3.1 không có trong đoạn đã trích".
_SO_NHAN = re.compile(r"(?i)\b(?:mục|phần|chương|hình|bảng|bước|giai đoạn|vòng|"
                      r"section|sec\.|figure|fig\.|table|eq\.|equation|step|stage|"
                      r"công thức|phương trình|appendix|phụ lục)\s*\(?\d+(?:\.\d+)*\)?")
_CHI_SO = re.compile(r"_\{[^{}]*\}|_[A-Za-z0-9+\-]{1,4}")


def so_lieu(text: str) -> list[str]:
    """Số liệu của một câu, sau khi gỡ những số không phải số liệu."""
    body = _DAU_DONG.sub("", text or "")
    body = _SO_NHAN.sub(" ", _CHI_SO.sub(" ", body))
    return _NUM.findall(_URLISH.sub(" ", body))


def _diem(tu: set[str], r: dict) -> int:
    return len(tu & {w.lower() for w in _TU.findall(f"{r['text']} {r.get('vi') or ''}")})


def gan_nguon_cho_so(answer: str, allowed: list[str]) -> tuple[str, list[dict]]:
    """Thư ký, $0: câu (hay hàng bảng) đưa số mà không kèm trích dẫn thì tìm
    trong các đoạn ĐÃ LẤY VỀ ở lượt này một đoạn chứa ĐỦ mọi số của câu và trùng
    chữ nhiều nhất, rồi gắn mã đoạn đó vào. Không đoạn nào chứa đủ thì để nguyên
    — cảnh báo "số không nguồn" vẫn hiện, vì lúc ấy nó đúng."""
    rows = sdb.get_chunks(list(set(allowed)))
    log = []
    for s in reversed(split_sentences(answer)):
        nums = so_lieu(s["text"])
        if not nums or s["cites"]:
            continue
        want = {_norm(n) for n in nums}
        tu = {w.lower() for w in _TU.findall(s["text"])}
        best, diem = "", 0
        for cid, r in rows.items():
            if r.get("level") or not want <= source_numbers(f"{r['text']} {r.get('vi') or ''}"):
                continue
            d = _diem(tu, r)
            if d > diem:
                best, diem = cid, d
        if not best or diem < 2:
            continue
        t = s["text"]
        # Hàng bảng: mã đặt vào ô cuối, trước dấu `|` đóng hàng. Câu thường: mã
        # đặt trước dấu chấm câu cuối.
        m = re.search(r"\s*\|\s*$", t) or re.search(r"[.!?…]+$", t)
        moi = (t[:m.start()] + f" [{best}]" + t[m.start():]) if m else t + f" [{best}]"
        answer = answer[:s["start"]] + moi + answer[s["end"]:]
        log.append({"vai": "thu_ky", "viec": "gắn nguồn cho câu có số mà thiếu trích dẫn",
                    "truoc": "", "sau": best, "cau": depth.cat_gon(t, 120)})
    return answer, log


def doi_trich_sai_bai(survey_id: str, answer: str, allowed: list[str]) -> tuple[str, list[dict]]:
    """Thư ký, $0: câu nhắc bài A mà trích đoạn bài B thì đổi sang đoạn của A
    trong số các đoạn ĐÃ LẤY VỀ ở lượt này (đoạn ngoài lượt là model chưa đọc,
    trích nó là bịa theo kiểu khác). Chọn đoạn trùng nhiều từ nhất với câu, và
    phải phủ đủ số của câu. Không có đoạn nào hợp thì để nguyên, cảnh báo vẫn
    hiện và giao diện gạch chân câu đó."""
    rows = sdb.get_chunks(list(set(allowed)))
    sents = split_sentences(answer)
    log = []
    for i, _ten, _ng, pid in reversed(sai_bai(survey_id, answer, sents, rows)):
        s = sents[i]
        tu = {w.lower() for w in _TU.findall(s["text"])}
        nums = {_norm(n) for n in so_lieu(s["text"])}
        # Đoạn mới phải khớp câu TỐT HƠN đoạn đang trích. Câu nhắc bài A để đối
        # chiếu ("Ngoài bài World Models, kho có đoạn bàn về JEPA [đoạn WAM]")
        # vốn trích đúng; bản đầu đổi nó sang đoạn World Models và phép chấm kéo
        # theo bắt ngay "đoạn không nhắc đến JEPA".
        cu = max((_diem(tu, rows[c]) for c in s["cites"] if c in rows), default=0)
        best, diem = "", cu
        for cid, r in rows.items():
            if r["paper_id"] != pid or r.get("level"):
                continue
            chu = f"{r['text']} {r.get('vi') or ''}"
            if nums and not nums <= source_numbers(chu):
                continue
            d = _diem(tu, r)
            if d > diem:
                best, diem = cid, d
        if not best or diem < 3:
            continue
        moi = s["text"]
        for c in s["cites"]:
            if c in rows and rows[c]["paper_id"] != pid:
                moi = moi.replace(f"[{c}]", f"[{best}]", 1)
        moi = re.sub(r"(\[" + re.escape(best) + r"\])(?:\s*\[" + re.escape(best) + r"\])+", r"\1", moi)
        if moi != s["text"]:
            answer = answer[:s["start"]] + moi + answer[s["end"]:]
            log.append({"vai": "thu_ky", "viec": "đổi trích dẫn sang đúng bài được nhắc",
                        "truoc": ", ".join(s["cites"]), "sau": best,
                        "cau": depth.cat_gon(s["text"], 120)})
    return answer, log


def _check_papers(survey_id: str, answer: str, warns: list[dict]) -> None:
    """Năm nhắc kèm tên bài phải khớp năm thật của bài đó trong kho."""
    papers = sdb.list_papers(survey_id)
    for p in papers:
        title = (p.get("title") or "").strip()
        if len(title) < 12 or not p.get("year"):
            continue
        head = title[:40]
        at = answer.find(head)
        if at < 0:
            continue
        near = answer[max(0, at - 60):at + len(head) + 60]
        years = {int(m.group()) for m in _YEAR.finditer(near)}
        if years and p["year"] not in years:
            warns.append({
                "i": -1, "kind": "năm_lệch",
                "msg": f"“{head}…” trong kho ghi năm {p['year']}, câu trả lời ghi "
                       f"{', '.join(str(y) for y in sorted(years))}",
                "text": head})


# ------------------------------------------- chấm kéo theo (tuỳ chọn, tốn tiền)


async def check_entailment(answer: str, allowed: list[str],
                           session: str = "", fast: str = "") -> tuple[list[dict], llm.Usage]:
    """Chấm từng câu xem đoạn nó trích có thật sự đỡ được nó không.

    Đây là chỗ bắt thứ mà năm phép kiểm cơ học ở trên **không** bắt được: mã đoạn
    có thật, số liệu có thật, nhưng đoạn ấy nói về chuyện khác, hoặc nói yếu hơn
    nhiều so với cách câu trả lời phát biểu. Trích dẫn tạo cảm giác đã được chứng
    minh, và đó chính là chỗ nguy hiểm.
    """
    usage = llm.Usage()
    rows = sdb.get_chunks(list(set(allowed)))
    items = []
    for s in split_sentences(answer):
        cited = [{"id": c, "text": rows[c]["text"]} for c in s["cites"] if c in rows]
        if cited and len(s["text"]) > 40:
            items.append({**s, "cited": cited})
    if not items:
        return [], usage

    raw, u = await llm.complete(
        [{"role": "system", "content": prompts.ENTAIL_SYSTEM},
         {"role": "user", "content": prompts.entail_user(items[:40])
          + "\n\nTrường \"why\" viết bằng tiếng Việt."}],
        model=fast or FAST, session_id=session or None, max_tokens=2000,
        temperature=0.0, reasoning=NO_REASONING)
    usage.add(u)

    out: list[dict] = []
    try:
        checks = llm.extract_json(raw).get("checks") or []
    except Exception:                       # noqa: BLE001
        return [], usage
    for c in checks:
        try:
            j = int(c.get("i"))
        except (TypeError, ValueError):
            continue
        verdict = str(c.get("v") or "").lower()
        if verdict in ("no", "weak") and 0 <= j < len(items):
            out.append({
                "i": items[j]["i"],
                "kind": "không_được_đỡ" if verdict == "no" else "đỡ_yếu",
                "msg": str(c.get("why") or
                           ("đoạn được trích không nói điều này" if verdict == "no"
                            else "đoạn được trích chỉ đỡ được một phần")),
                "text": depth.cat_gon(items[j]["text"], 160),
            })
    return out, usage
