"""Bài giảng: viết một bài báo ra thành thứ đọc từ đầu đến cuối là hiểu được.

Khác ba pass còn lại của kho survey ở chỗ **mục tiêu**. Hỏi đáp trả lời câu hỏi
đã có; tổng hợp dựng bức tranh chung của cả kho; bài giảng làm một việc mà cả
hai không làm được: đưa một người **chưa biết gì về nhánh hẹp này** đi tới chỗ
hiểu được đúng bài này, kể cả cơ chế bên trong.

## Chi phí — chỗ này mới là thiết kế, không phải tính năng

| Việc | Cách làm | Giá |
|---|---|---|
| biết mỗi bài được dẫn nói gì | `tldr` dựng sẵn của Semantic Scholar | **$0** |
| biết bài này **lấy gì** từ bài đó | câu trích dẫn nguyên văn trong chính bài này | **$0** |
| toàn văn bài đọc vào | prefix cache, cố định byte giữa mọi mẻ | ~1 lần |
| viết 8 mục | 4 mẻ nhỏ, `mechanism` đi riêng | 4 lượt |
| đào sâu chỗ nông | 1 lượt, **chỉ** cho mục bị chấm | ≤1 lượt |

Đo thật trên bài LAPA (101 đoạn, 20k token toàn văn): **175 giây, $0,0099** cho
5.750 từ — trong đó 93.952/117.704 token đọc vào là **lấy từ cache**. Chia mẻ
nhỏ gần như miễn phí chính là nhờ chỗ đó.

Cách hiển nhiên cho phần đối chiếu là tải về ba mươi bài tham khảo rồi bắt model
đọc hết. Cách đó tốn gấp vài chục lần và **vẫn tệ hơn**, vì đọc cả bài được dẫn
thì model phải tự đoán bài chính đã lấy ý nào — trong khi tác giả đã viết sẵn
câu trả lời ở ngay chỗ trích dẫn. Xem `refs.py`.

## Vòng đào sâu

Model viết nông không phải vì thiếu năng lực mà vì **câu nông đọc vẫn trôi**:
*"cơ chế tích hợp giúp cải thiện chất lượng"* không sai, và không mang tin gì.
`depth.check_text()` bắt đúng loại câu đó bằng bốn phép kiểm cơ học, và mục nào
bị bắt thì **được viết lại kèm đúng lời chê**, một lần. Chê chung chung ("hãy
viết sâu hơn") thì model viết dài hơn chứ không sâu hơn; chỉ vào câu cụ thể và
nói nó hỏng ở đâu thì mới sửa được.

Một lượt chứ không lặp tới khi sạch: mỗi vòng thêm tiền, mà cảnh báo còn lại vẫn
hiện cho người dùng thấy — họ tự đọc và tự đánh giá được, giống mọi chốt chặn
khác trong công cụ này (**cảnh báo chứ không chặn**).
"""

from __future__ import annotations

import json
import re
import time

from .. import db as maindb
from .. import depth, llm
from ..pipeline import _NUM, _URLISH, _norm_num
from . import db as sdb
from . import ingest, prompts, refs, verify

# **Tắt hẳn nghĩ thầm.** Đã vấp thật, hai mẻ trên bốn: model chạy 76 giây rồi
# trả về chuỗi RỖNG — `extract_json` ném "không tìm thấy JSON" vì trong phản hồi
# không có lấy một dấu `{`. Token của `max_tokens` bị phần nghĩ thầm ăn sạch
# trước khi tới phần viết. Cùng cái bẫy đã ghi trong CLAUDE.md cho DeepSeek V4 /
# GPT-5.x ở luồng dịch, chỉ khác là nó cũng xảy ra với model đang dùng.
#
# Bỏ nghĩ thầm không làm bài giảng nông đi: chỗ quyết định độ sâu là `SECTIONS`
# và `DEPTH_RULES` trong prompt, cộng vòng viết lại — chứ không phải token nghĩ
# thầm. Và ở đây nghĩ thầm còn TRANH chỗ với chính phần cần dài.
NO_REASONING = {"enabled": False}

# Trần đầu ra mỗi mẻ. Đủ rộng cho `mechanism` — mục dài nhất, và nó đi một mình
# nên được trọn cả trần này — mà vẫn về đích trong 300s timeout của `llm`.
MAX_TOKENS = 6000

# Số cảnh báo độ sâu tối đa nhồi vào lượt viết lại. Kể hết ra thì lời chê loãng
# và model sửa hình thức cho qua thay vì sửa nội dung.
MAX_REDO = 4


def _fingerprint(paper: dict, dos: dict) -> str:
    """Vân tay nguồn. Đổi là bài giảng cũ đã lệch với bài — gắn cờ, không xoá."""
    return maindb.sha("|".join([
        paper.get("sha256") or "",
        str(paper.get("n_pages") or 0),
        str(len((dos or {}).get("refs") or [])),
    ]))


def stale(paper: dict) -> bool:
    lec = sdb._loads(paper.get("lecture"), None)
    if not lec:
        return False
    return bool(paper.get("lecture_fp")) and lec.get("fp") != paper.get("lecture_fp")


async def build(paper_id: str, *, deepen: bool = True):
    """Async generator phát SSE: ("stage"|"section"|"done"|"error", payload)."""
    paper = sdb.load_paper(paper_id)
    sid = paper["survey_id"]
    t0 = time.time()

    chunks = sdb.paper_chunks(paper_id, level=0)
    if not chunks:
        yield "error", {"msg": "Bài này chưa có nội dung đã bóc. Nạp lại PDF, hoặc "
                               "bài chỉ có abstract thì không đủ để dựng bài giảng."}
        return

    strong, fast = sdb.models_of(sid)

    # ---- hồ sơ đối chiếu: miễn phí, nhưng đi mạng nên báo cho người dùng biết
    yield "stage", {"msg": "lấy hồ sơ đối chiếu từ Semantic Scholar (miễn phí)", "pct": 8}
    try:
        dos = await refs.ensure(paper, refs.corpus_titles(sid, skip=paper_id))
    except Exception:                      # noqa: BLE001 — thiếu hồ sơ vẫn viết được
        dos = {}
    n_ref = len(dos.get("refs") or [])
    yield "stage", {
        "msg": (f"{n_ref} bài được dẫn, kèm câu trích dẫn nguyên văn — $0"
                if n_ref else
                "không tra được trên Semantic Scholar; phần đối chiếu sẽ mỏng"),
        "pct": 16}

    # ---- prefix: toàn văn MỘT bài, cố định byte giữa mọi mẻ của bài này
    prefix = ingest.labeled_text(paper_id, chunks)
    sysmsg = llm.system_message(prefix, prompts.LECTURE_SYSTEM, model=strong)
    dtext = prompts.dossier_text(dos)
    ids = {c["id"] for c in chunks}

    out: dict = {}
    usage = llm.Usage()
    n_batch = len(prompts.LECTURE_BATCHES)

    for bi, names in enumerate(prompts.LECTURE_BATCHES):
        titles = ", ".join(prompts.SECTIONS[n]["title"].lower() for n in names)
        yield "stage", {"msg": f"viết {titles}", "pct": 20 + int(52 * bi / n_batch)}
        try:
            got, u = await _one(sysmsg, paper, names, dtext, strong, paper_id)
        except Exception as e:             # noqa: BLE001 — mẻ hỏng thì giữ mẻ đã xong
            usage.add(getattr(e, "usage", None) or llm.Usage())
            yield "stage", {"msg": f"mẻ {bi + 1} không viết được: {e}", "pct": 20}
            continue
        usage.add(u)
        for n in names:
            if n in got:
                out[n] = got[n]
                yield "section", {"name": n, "data": got[n],
                                  "cost": round(usage.cost, 5)}

    if not out:
        yield "error", {"msg": "Model không trả về mục nào. Thử lại, hoặc đổi model."}
        return

    # ---- đào sâu: chỉ những mục bị chấm là nông, và kèm đúng lời chê
    warns = check(out, ids, paper_id)
    shallow = _shallow(warns)
    if deepen and shallow:
        names = tuple(n for n in shallow if n in prompts.SECTIONS)
        yield "stage", {"msg": "đào sâu " + ", ".join(
            prompts.SECTIONS[n]["title"].lower() for n in names), "pct": 78}
        try:
            got, u = await _one(sysmsg, paper, names, dtext, strong, paper_id,
                                redo=shallow)
            usage.add(u)
            for n in names:
                if n in got:
                    out[n] = got[n]
                    yield "section", {"name": n, "data": got[n], "redone": True,
                                      "cost": round(usage.cost, 5)}
            warns = check(out, ids, paper_id)
        except Exception:                  # noqa: BLE001 — giữ bản đầu, cảnh báo vẫn hiện
            pass

    yield "stage", {"msg": "soát số liệu và độ sâu", "pct": 92}

    # Mục thiếu phải nằm trong KẾT QUẢ, không chỉ thoáng qua một dòng tiến trình:
    # mở lại bài giảng ngày hôm sau thì dòng đó đã trôi mất, mà một bài giảng
    # thiếu hẳn mục `mechanism` nhìn vẫn có vẻ đầy đủ. Đặt sau vòng đào sâu để
    # phép soát cuối cùng không xoá mất nó.
    for n in prompts.SECTIONS:
        if n not in out:
            warns.append({"section": n, "kind": "thiếu_mục",
                          "msg": f"Mục “{prompts.SECTIONS[n]['title']}” không dựng "
                                 "được — bấm Dựng lại để viết nốt"})

    lec = {
        "sections": out,
        "warns": warns,
        "refs": dos.get("refs") or [],
        "s2_id": dos.get("s2_id") or "",
        "n_refs_total": dos.get("n_refs") or 0,
        "model": strong,
        "cost": round(usage.cost, 5),
        "created_at": time.time(),
        "fp": _fingerprint(paper, dos),
    }
    sdb.update_paper(paper_id, lecture=json.dumps(lec, ensure_ascii=False),
                     lecture_fp=lec["fp"])
    yield "done", {"lecture": lec, "cost": round(usage.cost, 5),
                   "usage": usage.dict(), "secs": round(time.time() - t0, 1)}


async def _one(sysmsg, paper: dict, names: tuple[str, ...], dtext: str,
               model: str, paper_id: str, redo: dict | None = None):
    """Một mẻ, thử lại một lần nếu phản hồi không bóc ra JSON được.

    `session_id=paper_id` để OpenRouter giữ sticky routing cho prefix.

    Thử lại chứ không bỏ qua, vì bỏ qua **mất trắng cả mẻ**: người dùng trả tiền
    cho các mẻ khác rồi nhận về bài giảng thiếu ba mục, mà lý do chỉ thoáng qua
    trong một dòng tiến trình. Lần thử lại gần như miễn phí — prefix đã ấm nên
    chỉ trả tiền phần đầu ra.
    """
    user = prompts.lecture_user(paper.get("title") or "", paper.get("card"),
                                names, dtext, redo)
    usage = llm.Usage()
    last: Exception | None = None
    for attempt in range(2):
        raw, u = await llm.complete(
            [sysmsg, {"role": "user", "content": user}],
            model=model, session_id=paper_id, max_tokens=MAX_TOKENS,
            temperature=0.3, reasoning=NO_REASONING)
        usage.add(u)
        try:
            return llm.extract_json(raw), usage
        except Exception as e:             # noqa: BLE001 — rỗng hoặc cắt cụt
            last = e
    raise last or ValueError("model không trả về JSON")


# ------------------------------------------------------------- chốt chặn


def _texts(name: str, data) -> list[tuple[str, str]]:
    """Bóc mọi câu văn xuôi của một mục thành (nhãn, text) để đem đi soát.

    Phải bóc theo hình dạng riêng của từng mục: `mechanism` giấu chữ trong
    `steps[].why`, `check` trong `items[].a`. Soát trên `json.dumps` thì dấu
    ngoặc và tên khoá lọt vào phép đếm và mọi phép kiểm đều lệch.
    """
    out: list[tuple[str, str]] = []
    if not isinstance(data, dict):
        return out
    if name == "mechanism":
        for i, s in enumerate(data.get("steps") or [], 1):
            if isinstance(s, dict):
                out.append((f"bước {i}", f"{s.get('do', '')} {s.get('why', '')}"))
    elif name == "prereq":
        for it in data.get("items") or []:
            if isinstance(it, dict):
                out.append((it.get("term", "")[:30], it.get("why", "")))
    elif name == "compare":
        for it in data.get("items") or []:
            if isinstance(it, dict):
                out.append((it.get("paper", "")[:30],
                            f"{it.get('took', '')} {it.get('differs', '')}"))
        out.append(("vị trí", data.get("placement", "")))
    elif name == "limits":
        for it in data.get("items") or []:
            if isinstance(it, dict):
                out.append((it.get("point", "")[:30], it.get("so_what", "")))
    elif name == "evidence":
        out.append(("giới hạn bằng chứng", data.get("limits_of_evidence", "")))
    elif name == "check":
        pass                    # câu hỏi tự kiểm cố ý ngắn, soát độ sâu là kêu oan
    else:
        out.append(("", data.get("body", "")))
    return [(a, b) for a, b in out if (b or "").strip()]


# Mốc thời gian và khoảng thời gian. `_NUM` bóc `[00:12:30-00:12:35]` thành SÁU
# số rời — "00", "12", "30", "00", "12", "35" — mà không con nào là số liệu của
# bài. Đo thật: một mục sinh 32 cảnh báo, phần lớn từ đúng chỗ này.
_TIMEISH = re.compile(r"\d{1,3}:\d{2}(?::\d{2})?(?:[.,]\d+)?")

# Mục nào có số là KHẲNG ĐỊNH VỀ BÀI, và mục nào có số là con số giả định để
# giảng. Ràng buộc số liệu sinh ra để chặn "gán kết quả giả cho tác giả thật";
# còn `mechanism` và `problem` cố ý kể một **tình huống ví dụ** — "giả sử video
# dài 10 phút, N = 600 khung hình" không phải kết quả của ai cả.
#
# Không phân biệt hai thứ đó thì chốt chặn kêu oan 32 lần cho một bài, và lúc ấy
# người dùng thôi đọc cảnh báo — cảnh báo THẬT ở `evidence` trôi theo. Cùng bài
# học với mấy hằng ngân sách của slide: chốt chặn kêu oan vài lần là hỏng.
CLAIM_SECTIONS = ("evidence", "compare", "limits", "why_hard", "prereq", "check")


def _numbers(name: str, data) -> list[str]:
    """Mọi con số xuất hiện trong một mục, để đối chiếu với bài."""
    if name == "evidence" and isinstance(data, dict):
        blob = " ".join(f"{i.get('claim', '')} {i.get('number', '')} {i.get('setting', '')}"
                        for i in (data.get("items") or []) if isinstance(i, dict))
        blob += " " + (data.get("limits_of_evidence") or "")
    else:
        blob = " ".join(t for _, t in _texts(name, data))
    blob = _TIMEISH.sub(" ", _URLISH.sub(" ", blob))
    return [m.group(0) for m in _NUM.finditer(blob)]


def check(sections: dict, chunk_ids: set[str], paper_id: str = "") -> list[dict]:
    """Soát bài giảng. **Cảnh báo chứ không chặn** — người dùng có màn hình để đọc.

    Ba loại, xếp theo mức thiệt hại:

    - **số bịa** — nặng nhất, vì bằng mắt không ai bắt được và nó gán kết quả
      giả cho tác giả thật. Mọi số phải có mặt nguyên văn trong nội dung bài.
    - **mã đoạn không có thật** — mục khai nguồn mà nguồn không tồn tại thì
      phần khai nguồn thành trang trí, và người đọc không kiểm lại được.
    - **nông** — câu đúng mà không mang tin. Đây là cái vòng đào sâu nhắm vào.
    """
    warns: list[dict] = []

    # Số liệu: đối chiếu với chính nội dung bài đã bóc. Truy theo `paper_id` chứ
    # không nhét từng mã đoạn vào `IN (…)` — bài dài có vài trăm đoạn, mà SQLite
    # chặn ở 999 tham số.
    have: set[str] = set()
    if paper_id:
        for row in sdb.conn().execute(
                "SELECT text FROM chunk WHERE paper_id = ?", (sdb.check_id(paper_id),)):
            # Phía NGUỒN bóc rộng tay hơn phía bài giảng — `verify.source_numbers`
            # nhận cả `100M`, `7B`, `1,000`. Dùng `_NUM` chặt ở đây thì 48 con số
            # CÓ THẬT trong bài không vào `have` (đo trên một bài: 110 so với
            # 158), và mỗi lần bài giảng nhắc lại chúng là một cảnh báo kêu oan.
            txt = _TIMEISH.sub(" ", row["text"] or "")
            have |= verify.source_numbers(txt)

    for name, data in sections.items():
        title = prompts.SECTIONS.get(name, {}).get("title", name)
        for num in (_numbers(name, data) if have and name in CLAIM_SECTIONS else []):
            if _norm_num(num) not in have:
                warns.append({"section": name, "kind": "số_không_có_trong_bài",
                              "msg": f"{title}: số “{num}” không tìm thấy nguyên văn "
                                     "trong nội dung đã bóc của bài"})
        src = data.get("source") if isinstance(data, dict) else None
        for cid in (src or []):
            if isinstance(cid, str) and cid not in chunk_ids:
                warns.append({"section": name, "kind": "mã_đoạn_không_có",
                              "msg": f"{title}: khai nguồn “{cid}” không phải đoạn "
                                     "của bài này"})
        for label, text in _texts(name, data):
            for w in depth.check_text(text, label=f"{title}{' · ' + label if label else ''}"):
                warns.append({"section": name, "kind": w["kind"], "msg": w["msg"],
                              "text": w.get("text", "")})

    return _gom(warns)


# Từ ngần này cảnh báo cùng (mục, loại) trở lên thì gộp thành một dòng.
GROUP_AT = 3
GROUP_SHOW = 4          # số ví dụ giữ lại trong dòng đã gộp


def _gom(warns: list[dict]) -> list[dict]:
    """Gộp cảnh báo cùng MỤC và cùng LOẠI thành một dòng kèm số lần.

    Đo trên một bài thật: **106 cảnh báo `mã_đoạn_không_có`**, riêng mục `check`
    có 71 mã — và chúng KHÁC nhau từng cái, nên gộp theo thông điệp không ăn gì.
    Ba cảnh báo thật (`số_không_có_trong_bài`, `thiếu_cơ_chế`) nằm lẫn trong đó
    và không ai nhìn thấy. Một chốt chặn kêu 106 lần thì người dùng thôi đọc nó,
    và cảnh báo thật trôi theo — bài học đã ghi trong CLAUDE.md.

    Gộp ở tầng dữ liệu chứ không chỉ ở giao diện: `warns` được ghi xuống DB, nên
    106 phần tử ấy còn theo bài giảng đi khắp nơi.
    """
    buckets: dict[tuple, list[dict]] = {}
    order: list[tuple] = []
    for w in warns:
        key = (w.get("section", ""), w["kind"])
        if key not in buckets:
            buckets[key] = []
            order.append(key)
        buckets[key].append(w)

    out: list[dict] = []
    for key in order:
        group = buckets[key]
        if len(group) < GROUP_AT:
            out.extend(group)
            continue
        head = dict(group[0])
        vi_du = [w["msg"] for w in group[:GROUP_SHOW]]
        con = len(group) - len(vi_du)
        head["msg"] = ("; ".join(vi_du)
                       + (f" — và {con} chỗ nữa cùng loại" if con else ""))
        head["n"] = len(group)
        out.append(head)
    return out


def _shallow(warns: list[dict]) -> dict[str, str]:
    """{tên mục: lời chê} cho vòng đào sâu — chỉ lấy cảnh báo về độ sâu.

    Số bịa và mã đoạn sai KHÔNG vào đây: viết lại không sửa được kiểu hỏng đó,
    nó cần người đọc nhìn vào bài. Nhồi chúng vào lời chê chỉ làm loãng.
    """
    deep_kinds = {"câu_độn", "nói_chung_chung", "thiếu_cơ_chế", "vòng_tròn"}
    out: dict[str, str] = {}
    for w in warns:
        if w["kind"] not in deep_kinds or len(out) >= MAX_REDO:
            continue
        name = w["section"]
        if name in out:
            continue
        out[name] = w["msg"] + (f' — câu: “{w["text"][:110]}”' if w.get("text") else "")
    return out


def load(paper_id: str) -> dict:
    return sdb._loads(sdb.load_paper(paper_id).get("lecture"), {})


# In đậm chỉ có tác dụng khi nó NGẮN. Model hay viết `do` và `point` thành cả
# một đoạn, mà in đậm nguyên đoạn thì mắt không còn chỗ nào để bám — thành ra
# đúng bằng không in đậm gì, chỉ nặng hơn. Đo trên bài LAPA: bước 1 của
# `mechanism` dài 700 ký tự.
LEAD_MAX = 90


def _lead(text: str) -> str:
    text = (text or "").strip()
    return f"**{text}**" if 0 < len(text) <= LEAD_MAX else text


def as_markdown(paper: dict, lec: dict) -> str:
    """Xuất ra Markdown — bài giảng là thứ để đọc dài, nên phải mang đi được."""
    s = lec.get("sections") or {}
    out = [f"# {paper.get('title') or 'Bài giảng'}", ""]
    if paper.get("year") or paper.get("venue"):
        out.append(f"*{paper.get('venue') or ''} {paper.get('year') or ''}*".strip() + "\n")

    def head(name):
        out.append(f"\n## {prompts.SECTIONS[name]['title']}\n")

    if (d := s.get("prereq")):
        head("prereq")
        for it in d.get("items") or []:
            out.append(f"**{it.get('term', '')}** — {it.get('why', '')}\n")
    for name in ("problem", "why_hard"):
        if (d := s.get(name)):
            head(name)
            out.append((d.get("body") or "") + "\n")
    if (d := s.get("mechanism")):
        head("mechanism")
        if d.get("input"):
            out.append(f"**Đầu vào lấy làm ví dụ:** {d['input']}\n")
        for i, st in enumerate(d.get("steps") or [], 1):
            out.append(f"{i}. {_lead(st.get('do', ''))}")
            if st.get("why"):
                out.append(f"   *Vì sao cần:* {st['why']}")
            if st.get("note"):
                out.append(f"   *Ký hiệu:* {st['note']}")
            out.append("")
    if (d := s.get("compare")):
        head("compare")
        for it in d.get("items") or []:
            out.append(f"**{it.get('paper', '')}**  \n"
                       f"Lấy: {it.get('took', '')}  \n"
                       f"Khác: {it.get('differs', '')}\n")
        if d.get("placement"):
            out.append("\n" + d["placement"] + "\n")
    if (d := s.get("evidence")):
        head("evidence")
        for it in d.get("items") or []:
            out.append(f"- **{it.get('number', '')}** — {it.get('claim', '')} "
                       f"({it.get('setting', '')})")
        if d.get("limits_of_evidence"):
            out.append("\n**Các số này không chứng minh:** " + d["limits_of_evidence"] + "\n")
    if (d := s.get("limits")):
        head("limits")
        for it in d.get("items") or []:
            out.append(f"- {_lead(it.get('point', ''))} — {it.get('so_what', '')}")
        out.append("")
    if (d := s.get("check")):
        head("check")
        # Đáp án xuống CUỐI file, không nằm ngay dưới câu hỏi. Bản trong app đã
        # cẩn thận gập đáp án vào `<details>` vì chính lúc người đọc tự dựng lại
        # lời giải thích mới là lúc họ học được — rồi bản mang đi lại in đáp án
        # ngay dưới câu, làm mất sạch tác dụng đó.
        items = d.get("items") or []
        for i, it in enumerate(items, 1):
            out.append(f"{i}. {it.get('q', '')}")
        if items:
            out.append("\n<details>\n<summary>Đáp án</summary>\n")
            for i, it in enumerate(items, 1):
                out.append(f"{i}. {it.get('a', '')}")
            out.append("\n</details>")
    if lec.get("refs"):
        out.append("\n## Các bài được dẫn (dùng cho phần đối chiếu)\n")
        for r in lec["refs"]:
            mark = " ★" if r.get("influential") else ""
            out.append(f"- {r.get('title', '')} ({r.get('year') or '?'}){mark}")
    return "\n".join(out)
