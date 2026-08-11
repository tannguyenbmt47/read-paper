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

from .. import depth, llm, pipeline
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
    return out


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

    def add(i: int, kind: str, msg: str) -> None:
        warns.append({"i": i, "kind": kind, "msg": msg,
                      "text": sents[i]["text"][:160] if 0 <= i < len(sents) else ""})

    for s in sents:
        body = _URLISH.sub(" ", s["text"])
        nums = [n for n in _NUM.findall(body)]
        cites = s["cites"]

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
         {"role": "user", "content": prompts.entail_user(items[:40])}],
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
                "text": items[j]["text"][:160],
            })
    return out, usage
