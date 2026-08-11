"""Nạp một bài vào kho: PDF → đoạn → ngữ cảnh → vector → cây → phiếu → đồ thị.

Đây là chỗ quyết định chi phí của cả hệ thống, nên đọc bảng này trước khi sửa:

| Bước                            | Chạy bằng      | Giá/bài |
|---------------------------------|----------------|---------|
| bóc PDF, cắt đoạn, chỉ mục FTS5 | —              | **$0**  |
| vector hoá (`embed`)            | GPU tại chỗ    | **$0**  |
| ngữ cảnh hoá (`add_context`)    | model rẻ       | ~$0.010 |
| cây RAPTOR (`tree.build`)       | model rẻ       | ~$0.008 |
| bóc phiếu (`make_card`)         | model rẻ       | ~$0.010 |
| đồ thị (`graph.extract`)        | model rẻ       | ~$0.006 |

≈ **$0.034 một bài, trả một lần**. Sau đó mọi câu hỏi đều rẻ vì đã có phiếu, có
cây và có đồ thị.

Bóc PDF miễn phí và **dùng lại `db.parse_cache` của luồng đọc-hiểu**: PDF nào đã
nạp vào màn đọc thì nạp sang kho survey không chạy lại PyMuPDF lần nào.

Bốn bước tốn tiền đều đi sau **cùng một** `cached_prefix` chứa toàn văn bài, nên
tính cả bốn vẫn chỉ trả tiền đọc toàn văn khoảng một lần (`session_id=paper_id`
giữ sticky routing — thiếu nó là request rơi sang provider endpoint khác và cache
gần như không hit).
"""

from __future__ import annotations

import asyncio
import os
import re

from .. import db as maindb
from .. import llm, parser, store
from . import db as sdb
from . import embed, graph, prompts, tree

# Model rẻ cho mọi việc trong file này. Bóc phiếu và ngữ cảnh hoá là việc rút gọn
# và phân loại, không phải việc suy luận — model mạnh ở đây chỉ tốn tiền.
FAST = os.getenv("SURVEY_FAST_MODEL") or llm.FAST_MODEL

# Suy luận thấp: DeepSeek V4 / GPT-5.x để mặc định sẽ tiêu sạch max_tokens vào
# phần nghĩ thầm rồi trả JSON dở dang (cùng lý do với `pipeline.NO_REASONING`).
NO_REASONING = {"enabled": False}

TARGET_CHARS = 1200      # cỡ một đoạn, tính bằng ký tự
OVERLAP_CHARS = 180      # chồng lấn ~15%: câu chốt nằm ở mép đoạn thì vẫn tìm ra
MAX_CHARS = 2200         # trần cứng, trên mức này thì cắt bất kể mạch
CTX_BATCH = 40           # số đoạn gửi ngữ cảnh hoá mỗi lượt

# Loại khối không đem đi tìm kiếm: mục tham khảo là danh sách tên, tìm trúng nó
# chỉ tổ đẩy đoạn thật ra khỏi top-k.
SKIP_TYPES = {"reference", "meta"}

# Tên mục tham khảo, dùng khi bộ bóc không gắn được nhãn `reference`.
_REF_SECTION = re.compile(r"^\s*(\d+\s*\.?\s*)?(references?|bibliography|"
                          r"tài liệu tham khảo)\s*$", re.I)

# Một DÒNG trông như một mục trong danh sách tham khảo. Đòi có năm, cộng thêm ít
# nhất một dấu hiệu thư mục — chỉ có năm thôi thì câu "vào năm 2020, …" cũng dính.
_REF_LINE = re.compile(
    r"(?=.*\b(19|20)\d{2}\b)"
    r"(?=.*(arxiv|in proceedings|proc\.|pp\.\s*\d|conference|journal|"
    r"transactions|advances in|preprint|doi:|\bvol\.|\bed(s?)\.|^\[\d+\]|"
    r"^[A-Z][a-zÀ-ỹ]+,\s*[A-Z]\.))", re.I | re.M)

# Dấu hiệu **thư mục**: nơi công bố. Đây là chỗ phân biệt then chốt — đoạn văn
# bình thường trích dẫn theo kiểu "(Lewis et al., 2020)", nó KHÔNG ghi kèm nơi
# công bố. Chỉ mục tham khảo mới ghi.
_REF_VENUE = re.compile(
    r"(in proceedings|in european conference|in international conference|"
    r"advances in neural|arxiv:|arxiv preprint|springer|"
    r"journal of|transactions on|\bpp\.\s*\d|\b\d+:\s*\d+[-–]\d+)", re.I)

# "Wang, Z.; Yu, S.; Stengel-Eskin, E." — danh sách tác giả viết đầy đủ. Trích
# dẫn trong câu dùng "et al." chứ không liệt kê kiểu này.
_REF_AUTHORS = re.compile(r"[A-Z][a-zÀ-ỹ]+,\s*[A-Z]\.")

_YEAR = re.compile(r"\b(19|20)\d{2}\b")


def _is_ref_block(text: str) -> bool:
    """MỘT khối có phải mục thư mục không? Chấm từng khối, không chấm theo cụm.

    Phải chấm ở mức khối vì các khối được gộp lại thành đoạn trước khi đánh chỉ
    mục: chấm sau khi gộp thì một đoạn văn thật nằm cạnh thư mục sẽ bị vứt theo.
    Đã vấp đúng vậy — mất luôn câu kết luận của bài.

    Dấu hiệu bắt buộc là **nơi công bố**, vì đó là thứ văn xuôi không có. Trích
    dẫn trong câu ghi "(Yang et al., 2018)" chứ không ghi "In Proceedings of…".
    """
    if len(text) < 60 or not _YEAR.search(text) or not _REF_VENUE.search(text):
        return False
    return (bool(re.match(r"^\s*\[\d+\]", text))
            or bool(_REF_AUTHORS.search(text))
            or len(_REF_VENUE.findall(text)) >= 2)


def _looks_like_refs(text: str) -> bool:
    """Cả một cụm đã gộp có phải danh sách tham khảo không?

    Vì sao phải lọc: `parse_pdf` gắn nhãn `reference` khi tìm được tiêu đề mục
    tham khảo, nhưng có bài nó không tìm ra (chữ hoa nhỏ, hoặc tiêu đề nằm trong
    cột) — lúc đó cả thư mục rơi vào mục cuối cùng của bài. Đã đo trên bài thật:
    một mục thư mục đứng **hạng nhất** cho một câu hỏi về nội dung. Đoạn thư mục
    dày đặc từ khoá chủ đề mà không mang nội dung nào, nên nó là thứ gây nhiễu tệ
    nhất có thể có trong một chỉ mục.

    Hai phép chấm, vì thư mục về tới đây ở hai dạng khác nhau:

    1. **Theo dòng** — mỗi mục một dòng, dạng thường gặp.
    2. **Theo mật độ** — bài hai cột hay bị nối thành MỘT dòng dài không xuống
       dòng lần nào, lúc đó phép (1) không bao giờ chạm tới. Đã vấp đúng ca này.

    Điều KHÔNG được dùng làm dấu hiệu là mật độ năm hay "et al.": đoạn văn
    *"RAG tốt với truy vấn đơn (Lewis et al., 2020; Lin et al., 2024; Ram et al.,
    2023) nhưng…"* có mật độ năm **cao hơn** cả thư mục thật. Thứ phân biệt được
    là **nơi công bố** và **danh sách tác giả viết đầy đủ** — văn xuôi không có.
    """
    if len(text) < 120:
        return False

    lines = [ln.strip() for ln in text.split("\n") if len(ln.strip()) > 25]
    if len(lines) >= 3:
        hit = sum(1 for ln in lines if _REF_LINE.search(ln))
        if hit >= 3 and hit / len(lines) >= 0.6:
            return True

    venues = len(_REF_VENUE.findall(text))
    years = len(_YEAR.findall(text))
    authors = len(_REF_AUTHORS.findall(text))
    return (venues >= 3 and years >= 3) or authors >= 6


# ------------------------------------------------------------- cắt đoạn


def _flush(buf: list[dict], out: list[dict]) -> None:
    if not buf:
        return
    head = buf[0]
    text = "\n".join(b["text"] for b in buf).strip()
    buf.clear()
    # Đoạn hoá ra là thư mục thì bỏ hẳn, không đánh chỉ mục.
    if not text or _looks_like_refs(text):
        return
    out.append({
        "ord": len(out) + 1,
        "section": head.get("section", ""),
        "page": head.get("page", 0),
        "kind": head.get("kind", "para"),
        "text": text,
    })


def split_blocks(blocks: list[dict]) -> list[dict]:
    """Gom block thành đoạn đi tìm kiếm được, bám theo ranh giới mục.

    Ba luật, mỗi luật vá một kiểu tìm trượt đã thấy:

    - **Không cắt ngang bảng, công thức, caption.** Cắt đôi một bảng thì nửa đầu
      có tên cột, nửa sau có số — tìm ra nửa nào cũng vô dụng.
    - **Không nối qua ranh giới mục.** Câu cuối phần Method dính câu đầu phần
      Results thì đoạn ấy mang nhãn mục sai, và ngữ cảnh hoá ở bước sau cũng sai
      theo.
    - **Chồng lấn ở mép.** Câu chốt của một ý hay rơi đúng chỗ cắt; không chồng
      lấn thì nó mất ngữ cảnh ở cả hai đoạn.
    """
    out: list[dict] = []
    buf: list[dict] = []
    size = 0
    section = ""
    in_refs = False

    for b in blocks:
        if b.get("hidden") or b.get("type") in SKIP_TYPES:
            continue
        text = (b.get("text") or "").strip()
        if not text:
            continue

        btype = b.get("type", "para")
        if btype == "heading":
            _flush(buf, out)
            size = 0
            section = text
            # Vào mục tham khảo thì bỏ tới hết, trừ khi gặp Phụ lục — phụ lục
            # đứng SAU thư mục và là nội dung thật, phải giữ.
            if _REF_SECTION.match(text):
                in_refs = True
            elif re.match(r"^\s*(appendix|phụ lục|\d*\s*[A-Z]\b)", text, re.I):
                in_refs = False
            continue
        # Bỏ mục thư mục ở MỨC KHỐI, trước khi gộp — gộp rồi mới chấm thì đoạn
        # văn thật nằm cạnh thư mục bị vứt theo.
        if in_refs or _is_ref_block(text):
            continue

        sec = b.get("section") or section
        kind = {"caption": "figcap", "equation": "equation",
                "list": "list", "para": "para"}.get(btype, btype)
        item = {"text": text, "section": sec, "page": b.get("page", 0), "kind": kind}

        # Khối nguyên vẹn: gửi đi một mình, không gộp và không cắt.
        if kind in ("figcap", "equation") or len(text) > MAX_CHARS:
            _flush(buf, out)
            size = 0
            # Bộ lọc thư mục phải chạy Ở ĐÂY NỮA, không chỉ trong `_flush`.
            # Thư mục của bài hai cột hay bị nối thành một khối dài không xuống
            # dòng, tức là khối > MAX_CHARS — đúng nhánh này, và nó đi thẳng vào
            # `out` nên vòng qua hết mọi phép lọc. Đã lọt đúng như vậy một lần.
            if _looks_like_refs(text):
                continue
            for piece in _hard_split(text):
                if not _looks_like_refs(piece):
                    out.append({"ord": len(out) + 1, "section": sec,
                                "page": item["page"], "kind": kind, "text": piece})
            continue

        if buf and (size + len(text) > TARGET_CHARS or buf[0]["section"] != sec):
            tail = buf[-1]["text"][-OVERLAP_CHARS:] if buf[-1]["section"] == sec else ""
            _flush(buf, out)
            size = 0
            if tail and len(tail) > 40:
                # Mẩu chồng lấn chỉ để tìm ra, không phải để đọc — nên bám vào
                # đầu đoạn sau chứ không thành một đoạn riêng.
                buf.append({"text": "…" + tail, "section": sec,
                            "page": item["page"], "kind": "para"})
                size = len(tail)

        buf.append(item)
        size += len(text)

    _flush(buf, out)
    return out


def _hard_split(text: str) -> list[str]:
    """Đoạn dài quá trần thì cắt theo câu, không cắt giữa câu."""
    if len(text) <= MAX_CHARS:
        return [text]
    parts, cur = [], ""
    for sent in re.split(r"(?<=[.!?。])\s+", text):
        if cur and len(cur) + len(sent) > TARGET_CHARS:
            parts.append(cur.strip())
            cur = ""
        cur += sent + " "
    if cur.strip():
        parts.append(cur.strip())
    return parts or [text[:MAX_CHARS]]


def labeled_text(paper_id: str, chunks: list[dict], limit: int = 300_000) -> str:
    """Toàn văn có mã đoạn — dùng làm cached prefix cho hai pass tốn tiền."""
    out = []
    for ch in chunks:
        sec = f" [{ch['section']}]" if ch.get("section") else ""
        out.append(f"<<<{paper_id}c{ch['ord']}>>>{sec} {ch['text']}")
    return "\n\n".join(out)[:limit]


# --------------------------------------------------------------- bóc PDF


async def parse_bytes(data: bytes, use_layout: bool = False) -> tuple[str, list[dict], int]:
    """Bóc PDF, ưu tiên cache của luồng đọc-hiểu. Trả (tiêu đề, blocks, số trang).

    `use_layout=False` là mặc định cho kho survey: mô hình bố cục chỉ làm khung
    cắt **hình** chính xác hơn, mà kho survey tìm bằng chữ. Bật nó lên cho 50 bài
    là trả hàng chục giây mỗi bài để lấy thứ không dùng đến.
    """
    sha = maindb.sha(data)
    cached = maindb.get_parse(sha)
    if cached:
        return cached["title"], cached["blocks"], _page_count(data)

    prev = os.environ.get("LAYOUT_BACKEND")
    if not use_layout:
        os.environ["LAYOUT_BACKEND"] = "off"
    try:
        loop = asyncio.get_running_loop()
        title, blocks, _imgs = await loop.run_in_executor(None, parser.parse_pdf, data)
    finally:
        if not use_layout:
            if prev is None:
                os.environ.pop("LAYOUT_BACKEND", None)
            else:
                os.environ["LAYOUT_BACKEND"] = prev

    dicts = [b.dict() for b in blocks]
    maindb.put_parse(sha, title, dicts, False)
    return title, dicts, _page_count(data)


def _page_count(data: bytes) -> int:
    try:
        import fitz
        with fitz.open(stream=data, filetype="pdf") as d:
            return d.page_count
    except Exception:      # noqa: BLE001 — số trang chỉ để hiển thị
        return 0


# ------------------------------------------------------- ngữ cảnh hoá đoạn


async def add_context(paper_id: str, title: str, chunks: list[dict],
                      prefix: str, fast: str = "") -> tuple[dict[int, str], llm.Usage]:
    """Sinh một câu ngữ cảnh tiếng Anh cho mỗi đoạn, rồi trả về theo `ord`.

    Đây là kỹ thuật *Contextual Retrieval*: chèn câu ngữ cảnh vào cạnh đoạn rồi
    mới đánh chỉ mục, giảm tỉ lệ tìm trượt ~67% khi đi kèm rerank.

    Vì sao nó ăn: đoạn *"we reach 62.3 EM"* tự nó không tìm ra được bằng bất cứ
    truy vấn tự nhiên nào — nó không chứa tên phương pháp, tên tập dữ liệu, hay
    chữ "result". Câu ngữ cảnh mang đúng những từ đó vào chỉ mục.

    Câu ngữ cảnh viết bằng **tiếng Anh** dù người dùng hỏi tiếng Việt, vì nó phải
    khớp từ vựng của chính bài báo; phần bắc cầu sang tiếng Việt do `pseudo_doc`
    ở `search.plan_query` lo.
    """
    fast = fast or FAST
    usage = llm.Usage()
    got: dict[int, str] = {}
    sysmsg = llm.system_message(prefix, prompts.CTX_SYSTEM, model=fast)

    for i in range(0, len(chunks), CTX_BATCH):
        batch = chunks[i:i + CTX_BATCH]
        raw, u = await llm.complete(
            [sysmsg, {"role": "user", "content": prompts.ctx_user(batch)}],
            model=fast, session_id=paper_id, max_tokens=4000,
            temperature=0.1, reasoning=NO_REASONING)
        usage.add(u)
        try:
            data = llm.extract_json(raw).get("ctx") or {}
        except Exception:      # noqa: BLE001 — thiếu ngữ cảnh thì đoạn vẫn tìm được
            continue
        for key, val in data.items():
            try:
                got[int(str(key).strip().lstrip("[").rstrip("]"))] = str(val).strip()
            except ValueError:
                continue

    # Đoạn nào model bỏ sót thì lấy tạm tiêu đề + tên mục. Kém hơn hẳn câu do
    # model viết, nhưng vẫn hơn để trống: ít nhất tên bài cũng vào được chỉ mục.
    for ch in chunks:
        if not got.get(ch["ord"]):
            got[ch["ord"]] = f"{title} — {ch.get('section') or ''}".strip(" —")
    return got, usage


# ----------------------------------------------------------- bóc phiếu bài


async def make_card(paper_id: str, title: str, prefix: str,
                    fast: str = "") -> tuple[dict, llm.Usage]:
    """Rút cả bài thành phiếu ~600 token.

    Phiếu là chỗ tối ưu chi phí lớn nhất của hệ thống: nhờ nó mà phiếu của 50 bài
    nằm vừa một prompt, thành `corpus_digest` được cache, và câu hỏi so sánh chéo
    trả lời được mà không phải nạp toàn văn bài nào.
    """
    fast = fast or FAST
    sysmsg = llm.system_message(prefix, prompts.CARD_SYSTEM, model=fast)
    raw, usage = await llm.complete(
        [sysmsg, {"role": "user", "content": prompts.card_user(title, "")}],
        model=fast, session_id=paper_id, max_tokens=3000,
        temperature=0.2, reasoning=NO_REASONING)
    try:
        card = llm.extract_json(raw)
    except Exception:      # noqa: BLE001
        return {}, usage
    return card if isinstance(card, dict) else {}, usage


# ------------------------------------------------------------ nạp một bài


async def ingest_pdf(survey_id: str, data: bytes, source: str, *,
                     enrich: bool = True, use_layout: bool = False,
                     url: str = "", say=None) -> dict:
    """Nạp một PDF vào kho. Trả về bản ghi bài kèm chi phí đã tiêu.

    `enrich=False` thì bỏ hẳn hai bước tốn tiền — bài vẫn tìm được bằng BM25 trên
    văn bản gốc, chỉ là kém hơn và không có phiếu. Dùng khi muốn nạp thật nhanh
    rồi bóc phiếu sau.
    """
    def note(msg: str) -> None:
        if say:
            say(msg)

    sha = maindb.sha(data)
    if (dup := sdb.paper_by_sha(survey_id, sha)) is not None:
        note(f"đã có trong kho: {dup['title'][:60]}")
        return {**dup, "duplicate": True, "cost": 0.0}

    note("bóc chữ từ PDF")
    title, blocks, pages = await parse_bytes(data, use_layout=use_layout)
    chunks = split_blocks(blocks)
    if not chunks:
        raise ValueError("Không bóc được nội dung — PDF có thể là bản scan ảnh, cần OCR trước.")

    # Bài này đã có trong màn đọc? Nếu có thì lấy luôn bản dịch: kho tìm được
    # bằng tiếng Việt mà không tốn thêm đồng nào.
    prior = maindb.doc_by_sha(sha)
    if prior:
        _attach_translations(chunks, prior)
        title = title or prior.get("title", "")

    pid = sdb.add_paper(survey_id, sha256=sha, title=title, url=url, source=source,
                        n_pages=pages, status="parsed",
                        loupe_doc_id=prior["id"] if prior else "")

    usage = llm.Usage()
    _strong, fast = sdb.models_of(survey_id)
    prefix = labeled_text(pid, chunks)
    if enrich:
        note(f"ngữ cảnh hoá {len(chunks)} đoạn")
        ctx, u = await add_context(pid, title, chunks, prefix, fast)
        usage.add(u)
        for ch in chunks:
            ch["ctx"] = ctx.get(ch["ord"], "")

    sdb.put_chunks(pid, chunks, title=title)
    sdb.update_paper(pid, status="indexed")
    await vectorise(survey_id)          # miễn phí, chạy trên GPU

    if enrich:
        note("dựng cây tóm lược")
        t = await tree.build(pid, say=note, fast=fast)
        usage.add(llm.Usage(**t["usage"]))
        await vectorise(survey_id)      # tầng tóm lược cũng phải có vector

        note("bóc phiếu tóm tắt")
        card, u = await make_card(pid, title, prefix, fast)
        usage.add(u)
        if card:
            sdb.update_paper(pid, card=card, status="carded")

        note("dựng đồ thị thực thể")
        g, u = await graph.extract(pid, title, prefix, fast)
        usage.add(u)
        graph.save(survey_id, pid, g)

    note("xong")
    return {**sdb.load_paper(pid), "chunks": len(chunks),
            "cost": round(usage.cost, 5), "usage": usage.dict()}


async def vectorise(survey_id: str, batch: int = 64) -> int:
    """Vector hoá những node chưa có vector. Chạy trên máy nên **miễn phí**.

    Gọi được nhiều lần: chỉ đụng vào node còn thiếu, nên nạp thêm bài hay dựng
    thêm tầng cây đều chỉ tính phần mới. Không nạp được model thì trả 0 và bộ tìm
    rơi về BM25 đơn thuần — kém hơn, nhưng không chết.
    """
    if not embed.enabled():
        return 0
    todo = sdb.missing_vecs(survey_id, embed.MODEL_NAME)
    if not todo:
        return 0
    done = 0
    for i in range(0, len(todo), batch):
        lot = todo[i:i + batch]
        vecs = await embed.encode(
            [embed.as_passage(r.get("section", ""), r.get("ctx", ""), r["text"]) for r in lot])
        if vecs is None:
            return done
        sdb.put_vecs(list(zip([r["id"] for r in lot], embed.pack(vecs))),
                     embed.MODEL_NAME, int(vecs.shape[1]))
        done += len(lot)
    return done


def _attach_translations(chunks: list[dict], doc: dict) -> None:
    """Ghép bản dịch của màn đọc vào cột `vi` của đoạn tương ứng.

    Ghép theo **nội dung khối**, không theo vị trí: hai đường cắt đoạn khác nhau
    (mẻ dịch vs đoạn tìm kiếm) nên chỉ số không khớp nhau. Một đoạn tìm kiếm gộp
    vài khối dịch, nên nối các bản dịch của những khối nằm trong nó.
    """
    tr = doc.get("translations") or {}
    if not tr:
        return
    by_text = {}
    for b in doc.get("blocks") or []:
        vi = tr.get(b["id"])
        if vi and b.get("text"):
            by_text[maindb.norm(b["text"])] = vi
    if not by_text:
        return
    for ch in chunks:
        parts = []
        for line in ch["text"].split("\n"):
            vi = by_text.get(maindb.norm(line))
            if vi:
                parts.append(vi)
        if parts:
            ch["vi"] = "\n".join(parts)


async def ingest_loupe_doc(survey_id: str, doc_id: str) -> dict:
    """Kéo một bài đã nạp ở màn đọc sang kho, kèm cả bản dịch.

    Không cần PDF: `documents.blocks` đã là kết quả bóc, và người dùng có thể đã
    sửa tay ở màn soát — bản đó đúng hơn bản bóc lại từ file.
    """
    doc = store.load(doc_id)
    sha = doc.get("sha256") or maindb.sha(doc_id)
    if (dup := sdb.paper_by_sha(survey_id, sha)) is not None:
        return {**dup, "duplicate": True, "cost": 0.0}

    chunks = split_blocks(doc.get("blocks") or [])
    if not chunks:
        raise ValueError("Bài này không có nội dung để đánh chỉ mục.")
    _attach_translations(chunks, doc)

    brief = doc.get("brief") or {}
    title = doc.get("title") or brief.get("title_vi") or ""
    pid = sdb.add_paper(survey_id, sha256=sha, title=title,
                        source=doc.get("source", ""), status="indexed",
                        loupe_doc_id=doc_id)
    for ch in chunks:
        ch["ctx"] = f"{title} — {ch.get('section') or ''}".strip(" —")
    sdb.put_chunks(pid, chunks, title=title)
    await vectorise(survey_id)

    # Bài đã có `brief` thì phiếu dựng thẳng từ đó — miễn phí, và nhất quán với
    # bảng thuật ngữ mà bản dịch đang dùng.
    if brief:
        sdb.update_paper(pid, card=_card_from_brief(brief, pid, chunks), status="carded")
    return {**sdb.load_paper(pid), "chunks": len(chunks), "cost": 0.0}


async def enrich_paper(survey_id: str, paper_id: str, *, say=None) -> dict:
    """Chạy phần tốn tiền cho một bài đã nạp thô (ngữ cảnh → cây → phiếu → đồ thị).

    Tách khỏi `ingest_pdf` để nạp nhanh 50 bài trước rồi mới quyết bơm bài nào —
    nạp thô miễn phí, còn bơm thì có giá và phải do người dùng bấm.
    """
    def note(msg: str) -> None:
        if say:
            say(msg)

    p = sdb.load_paper(paper_id)
    chunks = sdb.paper_chunks(paper_id, level=0)
    if not chunks:
        raise ValueError("Bài này chưa có đoạn nào.")

    usage = llm.Usage()
    _strong, fast = sdb.models_of(survey_id)
    prefix = labeled_text(paper_id, chunks)

    note("ngữ cảnh hoá")
    ctx, u = await add_context(paper_id, p["title"], chunks, prefix, fast)
    usage.add(u)
    sdb.set_ctx(paper_id, ctx)

    note("dựng cây tóm lược")
    t = await tree.build(paper_id, say=note, fast=fast)
    usage.add(llm.Usage(**t["usage"]))
    await vectorise(survey_id)

    note("bóc phiếu")
    card, u = await make_card(paper_id, p["title"], prefix, fast)
    usage.add(u)
    if card:
        sdb.update_paper(paper_id, card=card, status="carded")

    note("dựng đồ thị")
    g, u = await graph.extract(paper_id, p["title"], prefix, fast)
    usage.add(u)
    graph.save(survey_id, paper_id, g)

    return {**sdb.load_paper(paper_id), "cost": round(usage.cost, 5),
            "usage": usage.dict(), "tree": t}


def _card_from_brief(brief: dict, pid: str, chunks: list[dict]) -> dict:
    return {
        "title_vi": brief.get("title_vi", ""),
        "tldr_vi": brief.get("one_line", ""),
        "task": "",
        "domain": brief.get("venue_guess", ""),
        "problem": brief.get("problem", ""),
        "gap": brief.get("gap", ""),
        "idea": brief.get("idea", ""),
        "method": brief.get("method", ""),
        "datasets": [], "metrics": [], "baselines": [],
        "results": [{"claim": brief.get("evidence", ""), "number": "",
                     "chunk": f"{pid}c{chunks[0]['ord']}" if chunks else ""}]
        if brief.get("evidence") else [],
        "limitations": brief.get("limits", ""),
        "novelty": "",
        "contribution_type": "",
        "keywords_en": [g["en"] for g in (brief.get("glossary") or [])[:10] if g.get("en")],
        "code_url": "",
        "from_brief": True,     # phiếu dựng từ brief, không phải do pass phiếu bóc
    }


async def recard(paper_id: str) -> dict:
    """Bóc lại phiếu cho một bài đã có trong kho (nút bấm riêng, có ghi giá)."""
    p = sdb.load_paper(paper_id)
    chunks = sdb.paper_chunks(paper_id)
    if not chunks:
        raise ValueError("Bài này chưa có đoạn nào để bóc phiếu.")
    prefix = labeled_text(paper_id, chunks)
    _strong, fast = sdb.models_of(p["survey_id"])
    card, usage = await make_card(paper_id, p["title"], prefix, fast)
    if card:
        sdb.update_paper(paper_id, card=card, status="carded")
    return {**sdb.load_paper(paper_id), "cost": round(usage.cost, 5), "usage": usage.dict()}
