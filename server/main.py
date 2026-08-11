from __future__ import annotations

import asyncio
import json
import os
import re
import threading
import time
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from fastapi import Body, FastAPI, File, Form, HTTPException, UploadFile  # noqa: E402
from fastapi import Response  # noqa: E402
from fastapi.responses import FileResponse, PlainTextResponse, StreamingResponse  # noqa: E402
from fastapi.staticfiles import StaticFiles  # noqa: E402

from . import db, layout, llm, parser, pipeline, store  # noqa: E402
from . import slide_theme as theme  # noqa: E402
from . import slide_fit  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / "web"

app = FastAPI(title="Loupe")

MODEL_CHOICES = [
    {"id": "~deepseek/deepseek-v4-flash-latest",
     "label": "DeepSeek V4 Flash — rẻ nhất, 1M context ($0.09/$0.18 mỗi triệu token)"},
    {"id": "deepseek/deepseek-v4-pro",
     "label": "DeepSeek V4 Pro — khá hơn, vẫn rẻ ($0.43/$0.87)"},
    {"id": "anthropic/claude-sonnet-4.5",
     "label": "Claude Sonnet 4.5 — tiếng Việt mượt nhất ($3/$15)"},
    {"id": "anthropic/claude-opus-4.1", "label": "Claude Opus 4.1 — kỹ nhất, đắt nhất"},
    {"id": "openai/gpt-5.6-terra",
     "label": "GPT-5.6 Terra — 1M context ($1/$6)"},
    {"id": "openai/gpt-5.6-terra-pro",
     "label": "GPT-5.6 Terra Pro — cùng giá Terra, suy luận sâu hơn ($1/$6)"},
    {"id": "openai/gpt-5.6-luna",
     "label": "GPT-5.6 Luna — rẻ, 1M context ($0.10/$0.60)"},
    {"id": "google/gemini-2.5-pro", "label": "Gemini 2.5 Pro — context lớn, giá tốt"},
    {"id": "google/gemini-2.5-flash", "label": "Gemini 2.5 Flash — rẻ, nhanh"},
    {"id": "qwen/qwen-max", "label": "Qwen Max — tiếng Việt khá"},
]


@app.on_event("startup")
async def _startup():
    n = store.migrate_json()
    if n:
        print(f"[db] đã chuyển {n} bài từ file JSON sang SQLite")


@app.on_event("startup")
async def _warm_layout():
    """Trả trước chi phí nạp model bố cục, chạy nền để không chặn server."""
    if layout.available():
        threading.Thread(target=layout.warmup, daemon=True).start()


def _sse(event: str, data: str) -> str:
    return f"event: {event}\ndata: {data}\n\n"


def _with_chunks(doc: dict) -> dict:
    """Gắn kế hoạch chia mẻ vào doc trước khi trả về.

    Không lưu xuống DB: kế hoạch phụ thuộc nội dung khối, mà khối thì sửa được
    ở bước 1 — tính lại mỗi lần rẻ hơn nhiều so với nguy cơ trả về kế hoạch cũ.
    Giao diện dựa vào `chunk_ids` để biết mẻ nào đã dịch xong mà bỏ qua.
    """
    chunks = pipeline.plan_chunks(doc)
    doc["chunks"] = len(chunks)
    doc["chunk_ids"] = [[b["id"] for b in c] for c in chunks]
    # chỉ stat một file — giao diện cần biết có mở được khung PDF gốc hay không
    doc["has_pdf"] = store.pdf_path(doc["id"]) is not None
    return doc


# ----------------------------------------------------------------- trang web


_ASSETS = ("style.css", "survey.css", "app.js", "survey.js")


def _asset_tag() -> str:
    """Vân tay của bộ file tĩnh — đổi mỗi khi một file trong đó được sửa."""
    stamp = "".join(str((WEB / f).stat().st_mtime_ns) for f in _ASSETS if (WEB / f).exists())
    return db.sha(stamp)[:10]


@app.get("/")
async def index():
    """Trang chính, kèm đánh dấu phiên bản vào đường dẫn CSS/JS.

    `Cache-Control: no-cache` ở `_NoCacheStatic` chỉ có tác dụng cho những lần
    tải **về sau**. Bản đã nằm sẵn trong cache của trình duyệt được lấy về khi
    chưa có chỉ dẫn nào, nên trình duyệt tự đoán thời hạn và giữ nó lại — người
    dùng vẫn nhận CSS cũ dù server đã sửa. Đã vấp đúng vậy, hai lần.

    Đổi URL là cách duy nhất chắc chắn: `style.css?v=abc123` là một khoá cache
    khác hẳn, không có bản cũ nào để mà lấy. Vân tay tính từ `mtime` nên sửa file
    là tự đổi, không phải nhớ tăng số tay.

    Bản thân trang này thì `no-store`: nó nhỏ, và nó là chỗ chứa các đường dẫn
    có vân tay — cache nó lại thì vân tay mới không bao giờ tới được trình duyệt.
    """
    html = (WEB / "index.html").read_text(encoding="utf-8")
    tag = _asset_tag()
    for f in _ASSETS:
        html = html.replace(f'"/{f}"', f'"/{f}?v={tag}"')
    return Response(html, media_type="text/html; charset=utf-8",
                    headers={"Cache-Control": "no-store"})


# -------------------------------------------------------------------- config


@app.get("/api/config")
async def config():
    return {
        "model": llm.DEFAULT_MODEL,
        "models": MODEL_CHOICES,
        "has_key": bool(os.getenv("OPENROUTER_API_KEY")),
        "layout_model": layout.available(),
    }


@app.get("/api/db/stats")
async def db_stats():
    return db.stats()


@app.get("/api/docs")
async def docs():
    return store.list_docs()


@app.delete("/api/doc/{doc_id}")
async def drop(doc_id: str):
    store.delete(doc_id)
    return {"ok": True}


@app.get("/api/doc/{doc_id}/sections")
async def sections(doc_id: str):
    """Các mục của bài, kèm số khối và ước lượng chi phí dịch RIÊNG từng mục.

    Có nó thì người đọc chọn được "chỉ dịch phần Cách làm và Kết quả" thay vì
    trả tiền cho cả bài — kể cả phần tham khảo và phụ lục họ không định đọc.
    """
    try:
        doc = store.load(doc_id)
    except KeyError:
        raise HTTPException(404, "Không tìm thấy tài liệu")

    price = None
    try:
        for m in await llm.list_models():
            if m.get("id") == doc["model"]:
                p = m.get("pricing") or {}
                price = float(p.get("completion") or 0)
                break
    except Exception:  # noqa: BLE001
        price = None

    tr = doc.get("translations") or {}
    out: list[dict] = []
    cur = None
    for b in doc["blocks"]:
        if b["type"] == "reference" or b.get("hidden"):
            continue
        if b["type"] == "heading" or cur is None:
            cur = {"name": b["text"] if b["type"] == "heading" else "(mở đầu)",
                   "first": b["id"], "ids": [], "chars": 0, "done": 0}
            out.append(cur)
            if b["type"] == "heading":
                continue
        if not b.get("translate"):
            continue
        cur["ids"].append(b["id"])
        cur["chars"] += len(b["text"] or "")
        if tr.get(b["id"]):
            cur["done"] += 1

    for sec in out:
        # tiếng Việt dài hơn tiếng Anh ~1,25 lần; ~3,6 ký tự một token
        out_tok = sec["chars"] / 3.6 * 1.25
        sec["blocks"] = len(sec["ids"])
        sec["cost_usd"] = round(out_tok * price, 4) if price else None
    return {"sections": [s for s in out if s["blocks"]], "model": doc["model"]}


@app.get("/api/doc/{doc_id}/estimate")
async def estimate(doc_id: str):
    """Bước 1: báo cáo tiền xử lý — bóc ra được gì, sắp tốn bao nhiêu."""
    try:
        doc = store.load(doc_id)
    except KeyError:
        raise HTTPException(404, "Không tìm thấy tài liệu")
    return await pipeline.estimate(doc)


@app.patch("/api/doc/{doc_id}/blocks")
async def edit_blocks(doc_id: str, body: dict = Body(...)):
    """Sửa kết quả tiền xử lý trước khi dịch.

    `drop`: bỏ hẳn khối khỏi bài. `skip`/`keep`: giữ khối nhưng không dịch / dịch.
    `drop_figure`: bỏ ảnh cắt sai, giữ nguyên caption.
    `hide`/`unhide`: ẩn khỏi mạch đọc mà vẫn giữ nguyên bản dịch — dùng cho rác
    còn sót như nhãn trục lạc ra từ hình hay dòng chân trang.
    """
    try:
        doc = store.load(doc_id)
    except KeyError:
        raise HTTPException(404, "Không tìm thấy tài liệu")

    drop = set(body.get("drop") or [])
    skip = set(body.get("skip") or [])
    keep = set(body.get("keep") or [])
    drop_fig = set(body.get("drop_figure") or [])
    hide = set(body.get("hide") or [])
    unhide = set(body.get("unhide") or [])

    if drop:
        doc["blocks"] = [b for b in doc["blocks"] if b["id"] not in drop]
        for bid in drop:
            doc["translations"].pop(bid, None)
            doc["notes"].pop(bid, None)
        pipeline.mark_stale(doc, drop)
    for b in doc["blocks"]:
        if b["id"] in skip:
            b["translate"] = False
        elif b["id"] in keep:
            b["translate"] = True
        # Ẩn là chuyện hiển thị, KHÔNG đụng vào bản dịch đã có — người đọc bỏ
        # nhầm rồi hiện lại thì không phải trả tiền dịch lần nữa.
        if b["id"] in hide:
            b["hidden"] = True
        elif b["id"] in unhide:
            b["hidden"] = False
        if b["id"] in drop_fig or b["id"] in drop:
            if b.get("figure"):
                store.delete_image(doc_id, b["figure"])
                pipeline.mark_stale(doc, {b["figure"]})
            b["figure"] = ""
    store.save(doc)
    return _with_chunks(doc)


def _forget(doc: dict, ids) -> None:
    """Bỏ bản dịch / diễn giải / ghi chú của những khối vừa bị sửa nội dung.

    Giữ lại là nguy hiểm hơn mất: bản dịch cũ ứng với đoạn văn cũ, để nguyên thì
    người đọc đối chiếu hai cột sẽ thấy chúng không khớp nhau mà không hiểu vì sao.

    Slide thì chỉ gắn cờ chứ không xoá — người dùng có thể đã sửa tay trên đó.
    """
    for bid in ids:
        doc["translations"].pop(bid, None)
        doc["notes"].pop(bid, None)
        (doc.get("plain") or {}).pop(bid, None)
        # Vệt bôi neo theo khoảng ký tự trong khối. Khối đổi chữ thì khoảng đó
        # trỏ vào chỗ khác — giữ lại còn tệ hơn mất, vì người đọc thấy vàng ở
        # một đoạn chẳng liên quan gì tới ghi chú của chính mình.
        (doc.get("highlights") or {}).pop(bid, None)
    pipeline.mark_stale(doc, ids)


def _fresh_block_id(doc: dict) -> str:
    """Mã khối mới chắc chắn không đụng mã nào đang có (parser đánh b1, b2…)."""
    used = {b["id"] for b in doc["blocks"]}
    n = 1 + max((int(m.group(1)) for b in used
                 if (m := re.fullmatch(r"b(\d+)", b))), default=len(used))
    while f"b{n}" in used:
        n += 1
    return f"b{n}"


@app.post("/api/doc/{doc_id}/blocks/merge")
async def merge_blocks(doc_id: str, body: dict = Body(...)):
    """Gộp các khối liền nhau thành một.

    PDF hai cột hay cắt một đoạn làm đôi ở chỗ nhảy cột hoặc sang trang. Để rời
    thì mỗi nửa được dịch riêng, mất hẳn quan hệ giữa hai vế của câu.
    """
    try:
        doc = store.load(doc_id)
    except KeyError:
        raise HTTPException(404, "Không tìm thấy tài liệu")

    ids = [str(i) for i in (body.get("ids") or [])]
    if len(ids) < 2:
        raise HTTPException(400, "Cần ít nhất hai khối để gộp")

    pos = {b["id"]: i for i, b in enumerate(doc["blocks"])}
    if any(i not in pos for i in ids):
        raise HTTPException(404, "Có mã khối không tồn tại")
    idx = sorted(pos[i] for i in ids)
    if idx != list(range(idx[0], idx[0] + len(idx))):
        raise HTTPException(400, "Chỉ gộp được các khối nằm liền nhau")

    blocks = doc["blocks"]
    head = blocks[idx[0]]
    text = head["text"]
    for j in idx[1:]:
        nxt = blocks[j]["text"]
        if text.endswith("-"):
            # PDF ngắt từ có gạch nối cuối dòng -> nối thẳng
            text = text[:-1] + nxt
        elif parser._CONT.match(nxt):
            # chỉ số gắn liền vào ký hiệu đứng trước: `{s^{k}` + `_{k=1}`
            text = text.rstrip() + nxt.lstrip()
        else:
            text = f"{text} {nxt}"
        if not head.get("figure") and blocks[j].get("figure"):
            for k in ("figure", "figure_page", "figure_rect", "figure_manual", "figure_source"):
                head[k] = blocks[j].get(k)
    head["text"] = text

    gone = {blocks[j]["id"] for j in idx[1:]}
    for bid in gone:
        b = next(x for x in blocks if x["id"] == bid)
        if b.get("figure") and b["figure"] != head.get("figure"):
            store.delete_image(doc_id, b["figure"])
    doc["blocks"] = [b for b in blocks if b["id"] not in gone]
    _forget(doc, gone | {head["id"]})
    store.save(doc)
    return _with_chunks(doc)


@app.post("/api/doc/{doc_id}/blocks/split")
async def split_block(doc_id: str, body: dict = Body(...)):
    """Tách một khối làm hai tại vị trí con trỏ.

    Ngược lại của gộp: parser đôi khi dính hai đoạn thành một khi khoảng cách
    dòng không đủ rõ để nhận ra ranh giới đoạn.
    """
    try:
        doc = store.load(doc_id)
    except KeyError:
        raise HTTPException(404, "Không tìm thấy tài liệu")

    bid = str(body.get("id") or "")
    i = next((k for k, b in enumerate(doc["blocks"]) if b["id"] == bid), None)
    if i is None:
        raise HTTPException(404, "Không có khối này")
    try:
        off = int(body.get("offset"))
    except (TypeError, ValueError):
        raise HTTPException(400, "Thiếu vị trí cắt")

    blk = doc["blocks"][i]
    left, right = blk["text"][:off].strip(), blk["text"][off:].strip()
    if not left or not right:
        raise HTTPException(400, "Vị trí cắt nằm ở đầu hoặc cuối khối — không tách được")

    tail = dict(blk)
    tail["id"] = _fresh_block_id(doc)
    tail["text"] = right
    # ảnh gắn với khối gốc, nửa sau không mang theo
    for k, v in (("figure", ""), ("figure_page", -1), ("figure_rect", None),
                 ("figure_manual", False), ("figure_source", "heuristic")):
        tail[k] = v
    blk["text"] = left

    doc["blocks"].insert(i + 1, tail)
    _forget(doc, {bid})
    store.save(doc)
    return _with_chunks(doc)


@app.post("/api/doc/{doc_id}/relayout")
async def relayout(doc_id: str):
    """Nhờ model rẻ dọn lại text bóc từ PDF. Đây là chỗ duy nhất ở bước 1 tốn tiền."""
    if not store.exists(doc_id):
        raise HTTPException(404, "Không tìm thấy tài liệu")
    try:
        stats, run, total = await pipeline.relayout(doc_id)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"{type(e).__name__}: {e}")
    return {"stats": stats, "run": run, "total": total,
            "doc": _with_chunks(store.load(doc_id))}


@app.patch("/api/doc/{doc_id}/translation")
async def edit_translation(doc_id: str, body: dict = Body(...)):
    """Sửa tay bản dịch hoặc phần diễn giải của một khối. **Miễn phí.**

    Bản sửa được ghi cả vào **bộ nhớ dịch**, nên nó không chỉ sửa cho bài này:
    đoạn y hệt ở bài khác, hoặc chính bài này sau khi bóc lại, sẽ lấy đúng bản
    người dùng đã sửa chứ không quay về bản máy dịch. Sửa một lần, giữ mãi.

    Nhận **văn bản thô** (giữ nguyên `^{…}` / `_{…}`), không nhận HTML: cột hiển
    thị đã đi qua `sci()` nên nó có `<sup>`, `<sub>` và thẻ `<a>` cho tham chiếu
    hình — lấy HTML đó làm nội dung lưu là mỗi lần sửa lại nhân thêm một lớp thẻ.
    """
    try:
        doc = store.load(doc_id)
    except KeyError:
        raise HTTPException(404, "Không tìm thấy tài liệu") from None

    bid = str(body.get("block_id") or "")
    blk = next((b for b in doc["blocks"] if b["id"] == bid), None)
    if blk is None:
        raise HTTPException(404, "Không có khối này")

    changed = []
    if "vi" in body:
        vi = str(body["vi"]).strip()
        if vi:
            doc["translations"][bid] = vi
        else:
            doc["translations"].pop(bid, None)
        changed.append("vi")
    if "plain" in body:
        pl = str(body["plain"]).strip()
        if pl:
            doc["plain"][bid] = pl
        else:
            doc["plain"].pop(bid, None)
        changed.append("plain")
    if not changed:
        raise HTTPException(400, "Không có gì để sửa")

    # Slide dựa trên khối này thì gắn cờ — nội dung nó trích đã đổi.
    pipeline.mark_stale(doc, {bid})
    store.save(doc)

    db.tm_put([(blk.get("text") or "",
                doc["translations"].get(bid, ""),
                doc["plain"].get(bid, ""))], doc.get("model") or llm.DEFAULT_MODEL)
    return {"ok": True, "block_id": bid, "changed": changed,
            "vi": doc["translations"].get(bid, ""), "plain": doc["plain"].get(bid, "")}


@app.post("/api/doc/{doc_id}/reparse")
async def reparse(doc_id: str):
    """Bóc lại bài từ file PDF gốc, giữ nguyên bản dịch và ghi chú. **Miễn phí.**

    Dùng khi bộ bóc khá lên: bản vá nhặt lại chữ mô hình bố cục bỏ sót thu về ~7
    điểm phần trăm số từ trên bài hai cột. Bỏ qua `parse_cache` — chính cache đó
    là thứ giữ bài ở lại với bản bóc cũ.
    """
    doc = store.load(doc_id)
    pdf = store.pdf_path(doc_id)
    if pdf is None:
        raise HTTPException(400, "Bài này không có file PDF gốc (nạp bằng văn bản dán "
                                 "hoặc file đã bị xoá) nên không bóc lại được.")
    data = pdf.read_bytes()
    loop = asyncio.get_running_loop()

    blocks: list = []
    imgs: dict = {}
    if layout.available():
        try:
            import tempfile
            with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
                f.write(data)
                tmp = f.name
            read = await loop.run_in_executor(None, layout.read, tmp)
            os.unlink(tmp)
            _t, blocks, imgs = await loop.run_in_executor(
                None, lambda: parser.blocks_from_layout(
                    read["items"], data, regions=read["regions"]))
            better = await loop.run_in_executor(
                None, parser.apply_layout, blocks, read["regions"], data)
            if better:
                imgs.update(better)
        except Exception as e:  # noqa: BLE001 — mô hình hỏng thì rơi về heuristic
            print(f"[reparse] mô hình bố cục lỗi, dùng heuristic: {e}")
            blocks = []
    if len(blocks) < 10:
        _t, blocks, imgs = await loop.run_in_executor(None, parser.parse_pdf, data)

    if not blocks:
        raise HTTPException(422, "Bóc lại không ra khối nào — giữ nguyên bản cũ.")

    stats = pipeline.reparse_merge(doc, [b.dict() for b in blocks])
    store.save(doc)

    # Ảnh cắt theo mã khối, mà mã khối vừa đổi cho phần mới — ghi lại toàn bộ.
    if imgs:
        store.save_images(doc_id, imgs)
    # Bản bóc mới thay luôn bản trong cache, để lần sau nạp cùng file được bản tốt.
    db.put_parse(db.sha(data), doc.get("title", ""), [b.dict() for b in blocks],
                 layout.available())
    return {"doc": _with_chunks(doc), "stats": stats}


@app.post("/api/doc/{doc_id}/confirm")
async def confirm(doc_id: str):
    """Chốt bước 1, mở đường sang bước 2."""
    try:
        return store.update(doc_id, prepared=True)
    except KeyError:
        raise HTTPException(404, "Không tìm thấy tài liệu")


# Không ràng vào MODEL_CHOICES: model trong .env có thể nằm ngoài danh sách, và
# OpenRouter thêm model mới liên tục. Chỉ chặn chuỗi rác.
_MODEL_ID = re.compile(r"^~?[\w.\-]+/[\w.\-:]+$")


@app.patch("/api/doc/{doc_id}/model")
async def set_model(doc_id: str, body: dict = Body(...)):
    """Đổi model cho những lượt gọi sau.

    Phần đã dịch giữ nguyên — bộ nhớ dịch khoá theo (đoạn, model) nên đổi model
    chỉ ảnh hưởng các mẻ chưa dịch. Brief và glossary đã chốt cũng giữ nguyên,
    vì chúng là ngữ cảnh dùng chung chứ không phải bản dịch.
    """
    model = (body.get("model") or "").strip()
    if not _MODEL_ID.match(model):
        raise HTTPException(400, "Tên model không hợp lệ")
    try:
        doc = store.update(doc_id, model=model)
    except KeyError:
        raise HTTPException(404, "Không tìm thấy tài liệu")
    return {"ok": True, "model": doc["model"]}


@app.get("/api/doc/{doc_id}/pdfinfo")
async def pdf_info(doc_id: str):
    """Số trang của PDF gốc. Chỉ gọi khi người đọc mở khung PDF, nên không đưa
    vào `_with_chunks` — mở file PDF ở mọi lần lấy tài liệu là phí."""
    p = store.pdf_path(doc_id)
    if p is None:
        raise HTTPException(404, "Bài này không có file PDF gốc")

    def work() -> int:
        import fitz
        with fitz.open(p) as d:
            return len(d)

    return {"pages": await asyncio.get_running_loop().run_in_executor(None, work)}


@app.get("/api/doc/{doc_id}/page/{pno}.png")
async def page_image(doc_id: str, pno: int, dpi: int = 110):
    """Render nguyên một trang PDF — nền để người dùng kéo khung cắt."""
    p = store.pdf_path(doc_id)
    if p is None:
        raise HTTPException(404, "Bài này không có file PDF gốc (nhập bằng cách dán văn bản)")

    def work() -> tuple[bytes, float, float]:
        import fitz
        with fitz.open(p) as d:
            if not 0 <= pno < len(d):
                raise IndexError
            page = d[pno]
            pix = page.get_pixmap(dpi=max(40, min(dpi, 200)))
            return pix.tobytes("png"), page.rect.width, page.rect.height

    try:
        png, w, h = await asyncio.get_running_loop().run_in_executor(None, work)
    except IndexError:
        raise HTTPException(404, "Không có trang này")
    # gửi kèm kích thước trang theo point để phía trình duyệt quy đổi toạ độ
    return Response(png, media_type="image/png", headers={
        "X-Page-Width": str(w), "X-Page-Height": str(h),
        "Access-Control-Expose-Headers": "X-Page-Width, X-Page-Height",
        "Cache-Control": "public, max-age=3600",
    })


@app.post("/api/doc/{doc_id}/crop/{block_id}")
async def crop(doc_id: str, block_id: str, body: dict = Body(...)):
    """Cắt lại hình theo khung người dùng tự kéo. Toạ độ tính bằng point của PDF."""
    try:
        doc = store.load(doc_id)
    except KeyError:
        raise HTTPException(404, "Không tìm thấy tài liệu")
    blk = next((b for b in doc["blocks"] if b["id"] == block_id), None)
    if blk is None:
        raise HTTPException(404, "Không có khối này")
    p = store.pdf_path(doc_id)
    if p is None:
        raise HTTPException(400, "Bài này không có file PDF gốc")

    try:
        pno = int(body["page"])
        x0, y0, x1, y1 = (float(v) for v in body["rect"])
    except (KeyError, TypeError, ValueError):
        raise HTTPException(400, "Thiếu page hoặc rect [x0,y0,x1,y1]")
    if x1 - x0 < 8 or y1 - y0 < 8:
        raise HTTPException(400, "Khung quá nhỏ")

    def work() -> bytes:
        import fitz
        with fitz.open(p) as d:
            page = d[pno]
            r = fitz.Rect(x0, y0, x1, y1) & page.rect
            if r.is_empty:
                raise ValueError("khung nằm ngoài trang")
            return parser.render_rect(page, r, dpi=int(body.get("dpi") or 160))

    try:
        png = await asyncio.get_running_loop().run_in_executor(None, work)
    except (IndexError, ValueError) as e:
        raise HTTPException(400, f"Không cắt được: {e}")

    store.save_images(doc_id, {block_id: png})
    blk["figure"] = block_id
    blk["figure_page"] = pno
    blk["figure_rect"] = [round(x0, 1), round(y0, 1), round(x1, 1), round(y1, 1)]
    blk["figure_manual"] = True
    blk["figure_source"] = "manual"
    store.save(doc)
    return {"ok": True, "block": blk}


@app.get("/api/doc/{doc_id}/figsizes")
async def figure_sizes(doc_id: str):
    """Tỉ lệ ngang/dọc của mọi ảnh trong bài.

    Màn slide cần biết ảnh ngang hay vuông mới chọn được bố cục. Trước đây nó
    tải **cả 22 ảnh (589 KB)** về chỉ để đọc `naturalWidth` — trong khi PIL ở
    server chỉ cần đọc phần header là ra kích thước. Một request nhỏ thay cho
    hai chục request ảnh.
    """
    try:
        doc = store.load(doc_id)
    except KeyError:
        raise HTTPException(404, "Không tìm thấy tài liệu")
    out: dict[str, float] = {}
    for b in doc["blocks"]:
        fid = b.get("figure")
        if not fid or fid in out:
            continue
        _, ratio = pipeline.figure_shape(doc_id, fid)
        if ratio:
            out[fid] = round(ratio, 4)
    return {"ratios": out}


@app.get("/api/doc/{doc_id}/img/{block_id}.png")
async def figure(doc_id: str, block_id: str):
    p = store.image_path(doc_id, block_id)
    if p is None:
        raise HTTPException(404, "Không có hình cho khối này")
    return FileResponse(p, media_type="image/png",
                        headers={"Cache-Control": "public, max-age=86400"})


@app.get("/api/doc/{doc_id}")
async def get_doc(doc_id: str):
    try:
        doc = store.load(doc_id)
    except KeyError:
        raise HTTPException(404, "Không tìm thấy tài liệu")
    return _with_chunks(doc)


# -------------------------------------------------------------------- import


# Kênh báo tiến trình cho lượt nạp bài. Client tự sinh mã việc, mở SSE trước,
# rồi mới POST — nhờ vậy không phải đổi hợp đồng của `/api/import` (vẫn trả về
# doc ở cuối) mà vẫn nói được nó đang làm gì.
#
# Cần vì bước chạy mô hình bố cục mất hàng chục giây tới vài phút, và trước đây
# giao diện chỉ hiện "Đang đọc tài liệu…" đứng im — không phân biệt được đang
# chạy hay đã treo.
_JOBS: dict[str, asyncio.Queue] = {}


def _say(job: str, stage: str, detail: str = "", pct: int | None = None) -> None:
    q = _JOBS.get(job or "")
    if q is None:
        return
    try:
        q.put_nowait({"stage": stage, "detail": detail, "pct": pct})
    except Exception:  # noqa: BLE001
        pass


@app.get("/api/import/{job}/progress")
async def import_progress(job: str):
    """Tiến trình của một lượt nạp. Mở TRƯỚC khi POST file lên."""
    if not job.isalnum() or len(job) > 40:
        raise HTTPException(400, "Mã việc không hợp lệ")
    q: asyncio.Queue = asyncio.Queue()
    _JOBS[job] = q

    async def gen():
        try:
            while True:
                try:
                    item = await asyncio.wait_for(q.get(), timeout=180)
                except asyncio.TimeoutError:
                    break                     # không ai dùng nữa, đừng giữ kết nối
                if item is None:
                    break
                yield _sse("step", json.dumps(item, ensure_ascii=False))
        finally:
            _JOBS.pop(job, None)

    return StreamingResponse(gen(), media_type="text/event-stream", headers={
        "Cache-Control": "no-cache", "X-Accel-Buffering": "no",
    })


@app.post("/api/import")
async def import_doc(
    file: UploadFile | None = File(None),
    text: str = Form(""),
    url: str = Form(""),
    title: str = Form(""),
    model: str = Form(""),
    use_layout: int = Form(1),
    job: str = Form(""),
):
    model = model or llm.DEFAULT_MODEL
    loop = asyncio.get_running_loop()
    _say(job, "Bắt đầu", "", 2)

    pdf_bytes: bytes | None = None
    if file is not None:
        data = await file.read()
        name = (file.filename or "").lower()
        if name.endswith(".pdf") or data[:5] == b"%PDF-":
            pdf_bytes = data
        if name.endswith(".pdf") or data[:5] == b"%PDF-":
            _say(job, "Bóc chữ từ PDF", f"{len(data)//1024} KB", 15)
            t, blocks, imgs = await loop.run_in_executor(None, parser.parse_pdf, data)
            source = file.filename or "upload.pdf"
        else:
            t, blocks, imgs = parser.parse_text(data.decode("utf-8", "replace"))
            source = file.filename or "upload.txt"
    elif url.strip():
        u = url.strip()
        if "arxiv.org" in u or parser._ARXIV.search(u):
            _say(job, "Tải bài từ arXiv", u, 6)
            aid, data = await parser.fetch_arxiv(u)
            source = f"arXiv:{aid}"
        else:
            _say(job, "Tải PDF về", u, 6)
            data = await parser.fetch_pdf_url(u)
            source = u
        pdf_bytes = data
        _say(job, "Bóc chữ từ PDF", f"{len(data)//1024} KB", 15)
        t, blocks, imgs = await loop.run_in_executor(None, parser.parse_pdf, data)
    elif text.strip():
        t, blocks, imgs = parser.parse_text(text)
        source = "dán trực tiếp"
    else:
        raise HTTPException(400, "Cần một trong: file PDF, đường dẫn, hoặc văn bản dán vào")

    if not blocks:
        _say(job, "Lỗi", "không trích được nội dung", None)
        if (q := _JOBS.get(job or "")) is not None:
            q.put_nowait(None)
        raise HTTPException(422, "Không trích được nội dung. PDF có thể là bản scan ảnh — cần OCR trước.")

    # Cùng một file PDF thì cấu trúc bóc ra và khung hình chắc chắn giống hệt.
    # Chạy lại PyMuPDF và mô hình bố cục chỉ tốn thời gian chứ không đổi kết quả.
    layout_used = False
    reused_from = None
    if pdf_bytes:
        file_sha = db.sha(pdf_bytes)
        cached = db.get_parse(file_sha)
        if cached:
            from .parser import Block
            t = cached["title"] or t
            blocks = [Block(**b) for b in cached["blocks"]]
            layout_used = cached["layout_model"]
            prior = db.doc_by_sha(file_sha)
            reused_from = prior["id"] if prior else None
            use_layout = 0          # đã có kết quả rồi, không chạy lại mô hình
            # Cache giữ khối chứ không giữ ảnh, mà `imgs` lúc này là ảnh của
            # đường heuristic — mã khối khác hẳn nên gắn vào là trỏ trượt hết.
            # Cắt lại từ khung đã lưu trong chính các khối vừa lấy ra.
            _say(job, "Dùng lại kết quả đã bóc",
                 "cùng file PDF, không chạy lại mô hình", 60)
            imgs = await loop.run_in_executor(None, parser.recrop, blocks, pdf_bytes)

    if pdf_bytes and use_layout and layout.available():
        try:
            import tempfile
            with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tf:
                tf.write(pdf_bytes)
                tmp = tf.name
            # MỘT lần convert cho cả cấu trúc văn bản lẫn vùng hình — phần đắt
            # nhất (chạy mô hình bố cục) trước giờ vẫn chạy, chỉ là bị vứt đi
            _say(job, "Chạy mô hình bố cục",
                 "bước lâu nhất — thường 10–60 giây tuỳ số trang", 30)
            read = await loop.run_in_executor(None, layout.read, tmp)
            os.unlink(tmp)

            if read["items"]:
                # mô hình quyết định khối nào ở đâu và là loại gì; PyMuPDF cấp glyph
                _say(job, "Dựng khối từ kết quả mô hình",
                     f"{len(read['items'])} vùng", 70)
                t2, blocks2, eq_imgs = await loop.run_in_executor(
                    None, lambda: parser.blocks_from_layout(
                        read["items"], pdf_bytes, regions=read["regions"]))
                if len(blocks2) >= 10:
                    t, blocks, imgs = (t2 or t), blocks2, eq_imgs
            better = await loop.run_in_executor(
                None, parser.apply_layout, blocks, read["regions"], pdf_bytes)
            if better:
                imgs.update(better)
            layout_used = True
        except Exception as e:  # noqa: BLE001
            print(f"[layout] bỏ qua, dùng heuristic: {type(e).__name__}: {e}")

    _say(job, "Cắt hình và bảng", f"{len(imgs)} ảnh", 85)
    doc = pipeline.build_doc(store.new_id(), title or t, blocks, source, model)
    doc["layout_model"] = layout_used
    if pdf_bytes:
        doc["sha256"] = file_sha
        if not reused_from:
            db.put_parse(file_sha, t, doc["blocks"], layout_used)
    doc["reused_parse"] = bool(reused_from)
    store.save_images(doc["id"], imgs)
    # chỉ chép bù những ảnh chưa cắt lại được — chép trước rồi ghi đè thì ảnh
    # của bài cũ (mã khối có thể khác) lấn át ảnh vừa cắt đúng
    if reused_from:
        store.copy_images(reused_from, doc["id"], skip=set(imgs))
    if pdf_bytes:
        store.save_pdf(doc["id"], pdf_bytes)
    store.save(doc)
    _say(job, "Xong", f"{len(blocks)} khối · {len(imgs)} hình", 100)
    # Nhường một nhịp cho vòng lặp đẩy bước cuối ra dây trước khi đóng kênh —
    # `put_nowait` không nhả quyền điều khiển, đóng ngay thì "Xong" chết trong
    # hàng đợi và người dùng không bao giờ thấy bước cuối.
    await asyncio.sleep(0.05)
    if (q := _JOBS.get(job or "")) is not None:
        q.put_nowait(None)          # đóng kênh, khỏi để client treo 3 phút
    return _with_chunks(doc)


# --------------------------------------------------------------------- brief


@app.post("/api/doc/{doc_id}/brief")
async def brief(doc_id: str):
    try:
        brief_obj, run, total = await pipeline.run_brief(doc_id)
        return {"brief": brief_obj, "run": run, "total": total}
    except KeyError:
        raise HTTPException(404, "Không tìm thấy tài liệu")
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"{type(e).__name__}: {e}")


# ------------------------------------------------------------------- dịch


@app.get("/api/doc/{doc_id}/translate")
async def translate(doc_id: str, chunk: int = 0, refine: int = 0,
                    mode: str = "both", only: str = ""):
    """`only`: danh sách mã khối, ngăn bằng dấu phẩy — dịch từng phần cho đỡ tốn."""
    if not store.exists(doc_id):
        raise HTTPException(404, "Không tìm thấy tài liệu")
    picked = {x for x in (only or "").split(",") if x.strip()}

    async def gen():
        try:
            async for kind, payload in pipeline.stream_chunk(
                doc_id, chunk, refine=bool(refine), mode=mode, only=picked or None
            ):
                yield _sse(kind, payload)
        except Exception as e:  # noqa: BLE001
            yield _sse("error", json.dumps({"message": f"{type(e).__name__}: {e}"}))

    return StreamingResponse(gen(), media_type="text/event-stream", headers={
        "Cache-Control": "no-cache", "X-Accel-Buffering": "no",
    })


# -------------------------------------------------------------- giải thích


@app.post("/api/doc/{doc_id}/explain/{block_id}")
async def explain(doc_id: str, block_id: str):
    try:
        note, run, total = await pipeline.explain_block(doc_id, block_id)
        return {"note": note, "run": run, "total": total}
    except KeyError:
        raise HTTPException(404, "Không tìm thấy đoạn này")
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"{type(e).__name__}: {e}")


# --------------------------------------------------------------- highlight


# Năm màu bôi. Ít màu thôi: nhiều quá thì chính người dùng cũng quên màu nào
# nghĩa là gì, và bài đọc thành cầu vồng.
HL_COLORS = ("y", "g", "b", "p", "v")


def _hl_all(doc: dict) -> list[dict]:
    return [h for lst in (doc.get("highlights") or {}).values() for h in lst]


@app.patch("/api/doc/{doc_id}/highlights")
async def edit_highlights(doc_id: str, body: dict = Body(...)):
    """Thêm / sửa ghi chú / xoá vệt bôi. Không tốn tiền.

    Neo theo (mã khối, cột, khoảng ký tự) TRONG VĂN BẢN THÔ, không phải trong
    HTML đã dựng: `sci()` chèn thêm `<sup>`, `<sub>` và thẻ `<a>` cho tham chiếu
    hình nên vị trí trong HTML lệch hẳn so với vị trí người đọc thấy. Lưu kèm cả
    đoạn chữ gốc để còn dò lại được khi khối bị sửa đôi chút.
    """
    try:
        doc = store.load(doc_id)
    except KeyError:
        raise HTTPException(404, "Không tìm thấy tài liệu")

    hl = doc.get("highlights") or {}

    if (add := body.get("add")) is not None:
        bid = str(add.get("block") or "")
        if not any(b["id"] == bid for b in doc["blocks"]):
            raise HTTPException(404, "Không tìm thấy khối này")
        col = add.get("col") if add.get("col") in ("en", "vi", "gl") else "vi"
        start, end = int(add.get("start", 0)), int(add.get("end", 0))
        if end <= start:
            raise HTTPException(400, "Khoảng bôi rỗng")
        used = {h["id"] for h in _hl_all(doc)}
        n = 1
        while f"h{n}" in used:
            n += 1
        color = add.get("color") if add.get("color") in HL_COLORS else "y"
        item = {"id": f"h{n}", "col": col, "color": color, "start": start, "end": end,
                "text": (add.get("text") or "")[:2000],
                "note": (add.get("note") or "")[:4000],
                "created_at": time.time()}
        hl.setdefault(bid, []).append(item)
        hl[bid].sort(key=lambda h: (h["col"], h["start"]))
        doc["highlights"] = hl
        store.save(doc)
        return {"highlights": hl, "new": item}

    if (up := body.get("update")) is not None:
        hid = str(up.get("id") or "")
        for lst in hl.values():
            for h in lst:
                if h["id"] == hid:
                    if "note" in up:
                        h["note"] = (up["note"] or "")[:4000]
                    if up.get("color") in HL_COLORS:
                        h["color"] = up["color"]
                    doc["highlights"] = hl
                    store.save(doc)
                    return {"highlights": hl, "item": h}
        raise HTTPException(404, "Không tìm thấy vệt bôi này")

    if (drop := body.get("drop")) is not None:
        gone = {str(x) for x in drop}
        for bid in list(hl):
            hl[bid] = [h for h in hl[bid] if h["id"] not in gone]
            if not hl[bid]:
                del hl[bid]
        doc["highlights"] = hl
        store.save(doc)
        return {"highlights": hl}

    raise HTTPException(400, "Không có thao tác nào: cần add, update hoặc drop")


@app.post("/api/doc/{doc_id}/highlights/{hl_id}/explain")
async def explain_highlight(doc_id: str, hl_id: str):
    """Nhờ model giải thích đúng đoạn vừa bôi. Tốn tiền, người dùng tự bấm."""
    try:
        note, run, total = await pipeline.explain_highlight(doc_id, hl_id)
        return {"note": note, "run": run, "total": total}
    except KeyError:
        raise HTTPException(404, "Không tìm thấy vệt bôi này")
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"{type(e).__name__}: {e}")


# -------------------------------------------------------------------- slide


@app.post("/api/doc/{doc_id}/outline")
async def make_outline(doc_id: str):
    """Bước 1: soạn nội dung buổi nói. Rẻ, và người dùng soát trước khi dựng."""
    try:
        outline, run, total = await pipeline.make_outline(doc_id)
        return {"outline": outline, "run": run, "total": total}
    except KeyError:
        raise HTTPException(404, "Không tìm thấy tài liệu")
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"{type(e).__name__}: {e}")


# Trường người dùng được sửa tay trong dàn ý. `source_block_ids` không có ở đây,
# cùng lý do với slide: nó là ràng buộc để soát số liệu, sửa được thì vô nghĩa.
_OUTLINE_EDITABLE = ("kind", "section", "message", "points", "evidence")


@app.patch("/api/doc/{doc_id}/outline")
async def edit_outline(doc_id: str, body: dict = Body(...)):
    """Sửa dàn ý bằng tay: sửa nội dung, đổi thứ tự, thêm, xoá. Không tốn tiền.

    Đây là màn soát của pass 4 — cùng vai trò với `#review` ở bước 1: model đề
    xuất, người dùng quyết. Sửa xong mới bấm dựng slide.
    """
    try:
        doc = store.load(doc_id)
    except KeyError:
        raise HTTPException(404, "Không tìm thấy tài liệu")

    slides = doc.get("slides") or {}
    outline = slides.get("outline")
    if not outline:
        raise HTTPException(404, "Bài này chưa có dàn ý")
    items = outline.setdefault("items", [])
    backs = outline.setdefault("backup", [])
    find = lambda oid: next(  # noqa: E731
        ((lst, i) for lst in (items, backs)
         for i, it in enumerate(lst) if it.get("id") == oid), None)

    if isinstance(body.get("item"), dict):
        oid = str(body["item"].get("id") or "")
        at = find(oid)
        if at is None:
            raise HTTPException(404, "Không tìm thấy mục này")
        lst, i = at
        for k in _OUTLINE_EDITABLE:
            if k in body["item"]:
                lst[i][k] = body["item"][k]
        lst[i]["edited"] = True

    if isinstance(body.get("sections"), list):
        outline["sections"] = body["sections"]
    if body.get("thesis") is not None:
        outline["thesis"] = str(body["thesis"])

    if body.get("drop"):
        at = find(str(body["drop"]))
        if at is not None:
            at[0].pop(at[1])

    if body.get("add"):                       # thêm mục trắng sau mục này
        at = find(str(body["add"]))
        if at is None:
            raise HTTPException(404, "Không tìm thấy mục này")
        lst, i = at
        lst.insert(i + 1, {"kind": "content", "section": lst[i].get("section", ""),
                           "message": "", "points": [],
                           "evidence": {"kind": "none", "figure": "", "what": ""},
                           "source_block_ids": [], "edited": True})

    if isinstance(body.get("move"), dict):    # đổi thứ tự trong cùng ngăn
        at = find(str(body["move"].get("id") or ""))
        if at is None:
            raise HTTPException(404, "Không tìm thấy mục này")
        lst, i = at
        j = max(0, min(len(lst) - 1, i + int(body["move"].get("by", 0))))
        lst.insert(j, lst.pop(i))

    if body.get("to") in ("items", "backup"):  # chuyển giữa chính và dự phòng
        at = find(str(body.get("id") or ""))
        if at is not None:
            dst = items if body["to"] == "items" else backs
            it = at[0].pop(at[1])
            dst.append(it)

    pipeline._number_outline(outline)
    pipeline.check_outline(doc, outline)
    slides["outline"] = outline
    doc["slides"] = slides
    store.save(doc)
    return {"outline": outline}


@app.get("/api/doc/{doc_id}/slides/build")
async def build_slides(doc_id: str):
    """Bước 2: dựng slide từ dàn ý đã duyệt, từng mẻ một, báo tiến trình qua SSE.

    Từng mẻ chứ không một lượt: mỗi slide được chia phần đầu ra rộng gấp nhiều
    lần nên viết được chi tiết, và mất kết nối giữa chừng thì phần đã dựng vẫn
    nằm trong DB.
    """
    if not store.exists(doc_id):
        raise HTTPException(404, "Không tìm thấy tài liệu")

    async def gen():
        try:
            async for ev, data in pipeline.render_deck(doc_id):
                yield _sse(ev, data)
        except KeyError:
            yield _sse("error", json.dumps(
                {"error": "Bài này chưa có dàn ý. Soạn nội dung trước đã."},
                ensure_ascii=False))
        except Exception as e:  # noqa: BLE001
            yield _sse("error", json.dumps({"error": f"{type(e).__name__}: {e}"},
                                           ensure_ascii=False))

    return StreamingResponse(gen(), media_type="text/event-stream", headers={
        "Cache-Control": "no-cache", "X-Accel-Buffering": "no",
    })


@app.post("/api/doc/{doc_id}/slides")
async def make_slides(doc_id: str):
    """Chạy liền cả hai bước. Đường tắt — không có chỗ soát dàn ý ở giữa."""
    try:
        slides, run, total = await pipeline.make_slides(doc_id)
        return {"slides": slides, "run": run, "total": total}
    except KeyError:
        raise HTTPException(404, "Không tìm thấy tài liệu")
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"{type(e).__name__}: {e}")


# Trường người dùng được sửa tay. Không cho sửa `source_block_ids` — đó là ràng
# buộc để soát số liệu, sửa được thì chốt chặn thành vô nghĩa.
_SLIDE_EDITABLE = ("headline", "sub", "eyebrow", "bullets", "cards", "callout",
                   "stats", "notes", "figure", "figure_note", "diagram",
                   "equation", "kind",
                   # Bố cục tự do: `free` bật/tắt, `boxes` là {tên phần: [x,y,w,h]}
                   # tính theo % khung slide nên đổi cỡ màn hình vẫn đúng.
                   "free", "boxes")


@app.patch("/api/doc/{doc_id}/slides")
async def edit_slides(doc_id: str, body: dict = Body(...)):
    """Sửa tay một slide, đổi thứ tự, xoá, hoặc chuyển giữa deck và backup.

    Không tốn tiền. Slide đã sửa tay được đánh dấu `edited` để lần dựng lại sau
    không xoá mất công sức của người dùng mà không báo.
    """
    try:
        doc = store.load(doc_id)
    except KeyError:
        raise HTTPException(404, "Không tìm thấy tài liệu")

    slides = doc.get("slides") or {}
    if not slides.get("deck") and not slides.get("backup"):
        raise HTTPException(400, "Bài này chưa có bộ slide nào")
    slides.setdefault("deck", [])
    slides.setdefault("backup", [])

    def find(sid: str):
        for key in ("deck", "backup"):
            for i, s in enumerate(slides[key]):
                if s.get("id") == sid:
                    return key, i
        return None

    if (order := body.get("order")) is not None:
        # đổi thứ tự / chuyển ngăn: gửi lên danh sách mã cho từng ngăn
        by_id = {s["id"]: s for key in ("deck", "backup") for s in slides[key]}
        for key in ("deck", "backup"):
            ids = [str(i) for i in (order.get(key) or []) if str(i) in by_id]
            slides[key] = [by_id[i] for i in ids]

    def fresh_sid() -> str:
        used = {s.get("id") for k in ("deck", "backup") for s in slides[k]}
        n = 1
        while f"s{n}" in used:
            n += 1
        return f"s{n}"

    # thêm slide trắng ngay sau slide đang chọn
    if (after := body.get("add")) is not None:
        w = find(str(after)) if after else None
        blank = {"id": fresh_sid(), "kind": "content", "eyebrow": "",
                 "headline": "Tiêu đề slide mới", "sub": "",
                 "cards": [], "bullets": [], "notes": "", "source_block_ids": [],
                 "edited": True}
        if w is None:
            slides["deck"].append(blank)
        else:
            slides[w[0]].insert(w[1] + 1, blank)
        doc["slides"] = slides
        store.save(doc)
        return {"slides": slides, "new_id": blank["id"]}

    # nhân đôi một slide, đặt ngay sau bản gốc
    if (dup := body.get("duplicate")) is not None:
        w = find(str(dup))
        if w is None:
            raise HTTPException(404, "Không tìm thấy slide này")
        import copy
        cp = copy.deepcopy(slides[w[0]][w[1]])
        cp["id"] = fresh_sid()
        cp["edited"] = True
        cp.pop("figure", None)      # ảnh gắn theo mã slide, đừng dùng chung
        cp.pop("illus", None)
        slides[w[0]].insert(w[1] + 1, cp)
        doc["slides"] = slides
        store.save(doc)
        return {"slides": slides, "new_id": cp["id"]}

    for sid in (str(i) for i in (body.get("drop") or [])):
        if (w := find(sid)) is not None:
            slides[w[0]].pop(w[1])

    if (patch := body.get("slide")) is not None:
        w = find(str(patch.get("id", "")))
        if w is None:
            raise HTTPException(404, "Không tìm thấy slide này")
        sl = slides[w[0]][w[1]]
        for k in _SLIDE_EDITABLE:
            if k in patch:
                sl[k] = patch[k]
        sl["edited"] = True
        sl.pop("stale", None)          # người dùng đã tự soát lại thì hết cũ
        pipeline.check_slides(doc, [sl])

    doc["slides"] = slides
    store.save(doc)
    return {"slides": slides}


@app.post("/api/doc/{doc_id}/slides/{slide_id}/image")
async def upload_slide_image(doc_id: str, slide_id: str, file: UploadFile = File(...)):
    """Nhận ảnh minh hoạ do người dùng tự làm và gắn vào slide.

    Công cụ cố tình KHÔNG tự sinh ảnh: một hình do AI vẽ cho bài báo khoa học
    trông rất có thẩm quyền mà không ai đối chiếu lại với bài — nguy hiểm hơn cả
    số liệu bịa, thứ mà `check_slides` soát từng con một. Người dùng tự làm, tự
    chịu trách nhiệm; công cụ chỉ đưa sẵn prompt và chỗ để thả ảnh vào.
    """
    if not slide_id.isalnum():
        raise HTTPException(400, "Mã slide không hợp lệ")
    try:
        doc = store.load(doc_id)
    except KeyError:
        raise HTTPException(404, "Không tìm thấy tài liệu")

    slides = doc.get("slides") or {}
    where = next(((k, i) for k in ("deck", "backup")
                  for i, s in enumerate(slides.get(k) or [])
                  if s.get("id") == slide_id), None)
    if where is None:
        raise HTTPException(404, "Không tìm thấy slide này")

    raw = await file.read()
    if not raw:
        raise HTTPException(400, "File rỗng")
    try:
        from PIL import Image
        import io as _io
        im = Image.open(_io.BytesIO(raw)).convert("RGB")
        if max(im.size) > 1400:
            r = 1400 / max(im.size)
            im = im.resize((round(im.width * r), round(im.height * r)), Image.LANCZOS)
        buf = _io.BytesIO()
        im.save(buf, "PNG", optimize=True)
        png = buf.getvalue()
    except Exception as e:  # noqa: BLE001
        raise HTTPException(400, f"Không đọc được ảnh: {e}")

    fid = f"art{slide_id}"
    store.save_images(doc_id, {fid: png})
    key, idx = where
    sl = slides[key][idx]
    sl["figure"] = fid
    sl["illus"] = True          # ảnh minh hoạ, không phải hình của bài báo
    doc["slides"] = slides
    store.save(doc)
    return {"slide": sl}


@app.post("/api/doc/{doc_id}/slides/{slide_id}/regen")
async def regen_slide(doc_id: str, slide_id: str, body: dict = Body(default={})):
    if not slide_id.isalnum():
        raise HTTPException(400, "Mã slide không hợp lệ")
    try:
        slide, run, total = await pipeline.regen_slide(
            doc_id, slide_id, (body or {}).get("hint", ""))
        return {"slide": slide, "run": run, "total": total}
    except KeyError:
        raise HTTPException(404, "Không tìm thấy slide này")
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"{type(e).__name__}: {e}")


# ------------------------------------------------------------------ hỏi đáp


@app.post("/api/doc/{doc_id}/ask")
async def ask(doc_id: str, body: dict = Body(...)):
    if not store.exists(doc_id):
        raise HTTPException(404, "Không tìm thấy tài liệu")
    question = (body.get("question") or "").strip()
    if not question:
        raise HTTPException(400, "Thiếu câu hỏi")
    history = body.get("history") or []

    async def gen():
        try:
            async for kind, payload in pipeline.ask(doc_id, question, history):
                yield _sse(kind, payload if kind != "delta" else json.dumps({"t": payload}))
        except Exception as e:  # noqa: BLE001
            yield _sse("error", json.dumps({"message": f"{type(e).__name__}: {e}"}))

    return StreamingResponse(gen(), media_type="text/event-stream", headers={
        "Cache-Control": "no-cache", "X-Accel-Buffering": "no",
    })


# ------------------------------------------------------------------- export


def _data_uri(doc_id: str, block_id: str) -> str:
    """Ảnh thành data: URI để file xuất ra đọc được khi không có server.

    Bản cũ ghi đường dẫn `/api/doc/…/img/…png` — mở file .md ngoài app thì mọi
    hình đều hỏng, vì đường dẫn đó chỉ có nghĩa khi server đang chạy.
    """
    import base64
    p = store.image_path(doc_id, block_id)
    if p is None:
        return ""
    return "data:image/png;base64," + base64.b64encode(p.read_bytes()).decode("ascii")


@app.get("/api/doc/{doc_id}/export")
async def export(doc_id: str, mode: str = "bilingual", fmt: str = "md"):
    try:
        doc = store.load(doc_id)
    except KeyError:
        raise HTTPException(404, "Không tìm thấy tài liệu")

    if fmt == "pptx":
        from . import pptx_out
        slides = doc.get("slides") or {}
        if not (slides.get("deck") or slides.get("backup")):
            raise HTTPException(400, "Bài này chưa có bộ slide nào — bấm “Dựng slide” trước")
        data = pptx_out.build(doc)
        return Response(
            data,
            media_type="application/vnd.openxmlformats-officedocument.presentationml.presentation",
            headers={"Content-Disposition": f'attachment; filename="{doc_id}-slide.pptx"'},
        )
    if fmt in ("slides", "slides-pdf"):
        return _export_slides_html(doc, for_print=fmt == "slides-pdf")
    if fmt in ("html", "pdf"):
        return _export_html(doc, mode, for_print=fmt == "pdf")

    tr = doc["translations"]
    brief = doc.get("brief") or {}
    out: list[str] = [f"# {brief.get('title_vi') or doc.get('title') or 'Bài báo'}"]
    if doc.get("title") and brief.get("title_vi"):
        out.append(f"*{doc['title']}*")
    out.append(f"\n> Nguồn: {doc.get('source','')} · Dịch bằng `{doc.get('model','')}`\n")

    if brief:
        out.append("## Tóm lược\n")
        out.append(f"**Chốt lại:** {brief.get('one_line','')}\n")
        for k, label in (
            ("problem", "Bài toán"), ("gap", "Khoảng trống"), ("idea", "Ý tưởng"),
            ("method", "Cách làm"), ("evidence", "Bằng chứng"), ("limits", "Giới hạn"),
        ):
            if brief.get(k):
                out.append(f"- **{label}:** {brief[k]}")
        for key, label in (("argument_diagram", "Mạch lập luận"), ("method_diagram", "Cơ chế đề xuất")):
            if brief.get(key):
                out.append(f"\n### {label}\n")
                out.append("```mermaid\n" + brief[key].strip() + "\n```\n")
        if brief.get("argument_chain"):
            out.append("\n### Các bước lập luận\n")
            for i, s in enumerate(brief["argument_chain"], 1):
                out.append(f"{i}. *({s.get('role','')})* {s.get('step','')}")
        if brief.get("glossary"):
            out.append("\n### Bảng thuật ngữ\n")
            out.append("| Tiếng Anh | Tiếng Việt | Nghĩa |")
            out.append("|---|---|---|")
            for g in brief["glossary"]:
                vi = "*(giữ nguyên)*" if g.get("keep_en") else g.get("vi", "")
                out.append(f"| {g.get('en','')} | {vi} | {g.get('gloss','')} |")
        out.append("\n---\n")

    for b in doc["blocks"]:
        vi = tr.get(b["id"], "")
        if b["type"] in ("reference", "meta"):
            continue
        if b["type"] == "heading":
            head = "#" * min(max(b.get("level", 1) + 1, 2), 5)
            out.append(f"\n{head} {vi or b['text']}\n")
            continue
        if b["type"] == "equation":
            uri = _data_uri(doc_id, b["figure"]) if b.get("figure") else ""
            out.append(f"\n![công thức]({uri})\n" if uri else f"\n```\n{b['text']}\n```\n")
            continue
        if b.get("figure"):
            uri = _data_uri(doc_id, b["figure"])
            if uri:
                out.append(f"\n![{b['text'][:80]}]({uri})\n")
        # mục danh sách giữ nguyên dạng danh sách; bullet lạ quy về "-" cho Markdown
        mk = b.get("marker") or ""
        pre = ("- " if mk and not any(c.isdigit() for c in mk) else f"{mk} ") if mk else ""
        if mode == "vi":
            if vi:
                out.append(pre + vi + "\n")
        else:
            out.append(f"> {pre}{b['text']}\n")
            out.append(pre + (vi or "*(chưa dịch)*") + "\n")
        note = (doc.get("notes") or {}).get(b["id"])
        if note:
            out.append(f"\n**Giải thích:** {note.get('gist','')}")
            if note.get("unpack"):
                out.append(note["unpack"])
            if note.get("diagram"):
                out.append("```mermaid\n" + note["diagram"].strip() + "\n```")
            out.append("")

    name = f"{doc_id}-{'vi' if mode == 'vi' else 'song-ngu'}.md"
    return PlainTextResponse(
        "\n".join(out),
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{name}"'},
    )


_EXPORT_CSS = """
:root{--bg:#fbfaf7;--surface:#fff;--surface-2:#f4f2ed;--line:#e2ded4;--ink:#23211d;
--ink-2:#5d5850;--muted:#8b857a;--accent:#b0521f}
@media(prefers-color-scheme:dark){:root{--bg:#16151a;--surface:#1d1c22;--surface-2:#26252c;
--line:#35333c;--ink:#ece9e3;--ink-2:#b6b1a8;--muted:#857f76;--accent:#e08a52}}
*{box-sizing:border-box}
body{margin:0;padding:2rem 1.2rem 6rem;background:var(--bg);color:var(--ink);
font:16px/1.65 "Iowan Old Style",Palatino,Georgia,serif}
main{max-width:1100px;margin:0 auto}
h1{font-size:1.6rem;line-height:1.3;margin:0 0 .3rem}
h2,h3{font-family:ui-sans-serif,system-ui,sans-serif;margin:2rem 0 .6rem}
.sub{color:var(--muted);font-size:.9rem;font-family:ui-sans-serif,system-ui,sans-serif}
.meta{color:var(--muted);font-size:.8rem;font-family:ui-sans-serif,system-ui,sans-serif;
border-bottom:1px solid var(--line);padding-bottom:1rem;margin-bottom:1.5rem}
.card{background:var(--surface);border:1px solid var(--line);border-radius:10px;
padding:1rem 1.2rem;margin:1rem 0}
.card b{display:block;font-family:ui-sans-serif,system-ui,sans-serif;font-size:.72rem;
text-transform:uppercase;letter-spacing:.06em;color:var(--muted)}
.card p{margin:.15rem 0 .8rem}
.pull{font-size:1.05rem;border-left:3px solid var(--accent);padding-left:.8rem;margin:0 0 1rem}
table{border-collapse:collapse;width:100%;font-size:.9rem}
th,td{border:1px solid var(--line);padding:.4rem .6rem;text-align:left;vertical-align:top}
th{background:var(--surface-2);font-family:ui-sans-serif,system-ui,sans-serif;font-size:.8rem}
.pair{display:grid;grid-template-columns:1fr 1fr;gap:0 1.6rem;margin:0 0 1.1rem}
.one .pair{grid-template-columns:1fr}
.en{color:var(--muted);font-size:.94em}
.vi{color:var(--ink)}
.gl{grid-column:1/-1;background:var(--surface-2);border-radius:8px;padding:.6rem .8rem;
margin-top:.5rem;font-size:.9em;font-family:ui-sans-serif,system-ui,sans-serif}
.gl b{color:var(--muted);font-size:.72rem;text-transform:uppercase;letter-spacing:.06em}
.hd{grid-column:1/-1;font-family:ui-sans-serif,system-ui,sans-serif;font-weight:700;
margin:1.6rem 0 .4rem;font-size:1.1rem}
.eq{font-family:ui-monospace,monospace;font-size:.95em;overflow-x:auto}
.eqbox{grid-column:1/-1;text-align:center;background:var(--surface);
border:1px solid var(--line);border-left:3px solid var(--accent);
border-radius:8px;padding:.6rem .8rem;margin:.6rem 0}
.eqbox img{max-width:100%;height:auto}
figure{grid-column:1/-1;margin:.6rem 0;text-align:center;background:#f7f6f3;
border:1px solid var(--line);border-radius:8px;padding:.5rem}
figure img{max-width:100%;height:auto}
.note{grid-column:1/-1;border-left:3px solid var(--accent);padding:.5rem 0 .5rem .8rem;
margin:.6rem 0;font-size:.92em;font-family:ui-sans-serif,system-ui,sans-serif}
.note dt{color:var(--muted);font-size:.72rem;text-transform:uppercase;letter-spacing:.06em}
.note dd{margin:0 0 .6rem}
.pending{color:var(--muted);font-style:italic}
.pair.li .en,.pair.li .vi{position:relative;padding-left:1.35rem}
.li-mk{position:absolute;left:0;color:var(--muted)}
.mermaid{background:var(--surface);border:1px solid var(--line);border-radius:8px;
padding:.8rem;margin:.8rem 0;text-align:center}
@media(max-width:820px){.pair{grid-template-columns:1fr;gap:.35rem}
.en{padding-bottom:.35rem;border-bottom:1px dashed var(--line);margin-bottom:.4rem}}
@page{margin:1.5cm}
@media print{body{background:#fff;color:#000;padding:0;font-size:10.5pt}
main{max-width:none}.card,.mermaid,figure,.note{break-inside:avoid}
h1,h2,h3{break-after:avoid}.pair{break-inside:avoid}}
"""


def _export_html(doc: dict, mode: str, *, for_print: bool = False) -> Response:
    """Một file HTML tự chứa: ảnh nhúng base64, sơ đồ vẽ được, mở offline.

    Markdown giữ được nội dung nhưng mất bố cục song ngữ căn theo đoạn — thứ
    đáng giá nhất của công cụ này. Bản HTML giữ đúng lưới hai cột đó.

    `for_print=True` mở thẳng hộp in của trình duyệt để lưu ra PDF. Đây là đường
    duy nhất giữ được cả sơ đồ Mermaid (cần JS để vẽ) lẫn lưới hai cột (cần CSS
    grid) — thư viện PDF thuần Python không làm được cả hai.
    """
    import html as _h

    doc_id = doc["id"]
    tr = doc["translations"]
    plain = doc.get("plain") or {}
    notes = doc.get("notes") or {}
    brief = doc.get("brief") or {}
    one_col = mode == "vi"
    diagrams: list[str] = []

    def esc(s) -> str:
        return _h.escape(str(s or ""))

    def rich(s) -> str:
        """Như `esc` nhưng dựng `^{…}` / `_{…}` thành chỉ số trên/dưới thật.

        Marker là dạng để model đọc, file xuất ra là để người đọc nhìn. Chỉ dùng
        cho phần thân bài — KHÔNG dùng cho mã Mermaid, thuộc tính `alt` hay thẻ
        `<title>`, chèn thẻ vào những chỗ đó là hỏng.

        Dựng cả `**đậm**`, và **chỉ** dạng hai dấu sao. Bài báo dùng chữ đậm làm
        tiêu đề chạy đầu đoạn (*"**Dataset.** Chúng tôi huấn luyện…"*) nên bỏ nó
        đi là mất một tầng cấu trúc. Nhưng dấu `*` ĐƠN thì để nguyên: quét dữ
        liệu thật, cả hai chỗ dùng nó đều không phải chữ nghiêng — một là ký hiệu
        chú thích bảng (*"Dấu * biểu thị uniform frame sampling"*), một là phép
        nhân (`2 * 10^{−4}`). Dựng chúng thành `<em>` là hỏng cả hai.

        Phải khớp từng luật với `sci()` bên `web/app.js`, nếu không bản xuất ra
        khác bản đang đọc trên màn hình.
        """
        out = esc(s)
        out = re.sub(r"\^\{([^{}]*)\}", r"<sup>\1</sup>", out)
        out = re.sub(r"_\{([^{}]*)\}", r"<sub>\1</sub>", out)
        return re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", out)

    def mermaid(code: str, cap: str = "") -> str:
        if not (code or "").strip():
            return ""
        diagrams.append(code)
        figcap = f"<figcaption class='sub'>{esc(cap)}</figcaption>" if cap else ""
        return f"<div class='mermaid'>{esc(code.strip())}</div>{figcap}"

    title_vi = brief.get("title_vi") or doc.get("title") or "Bài báo"
    out = [f"<h1>{rich(title_vi)}</h1>"]
    if doc.get("title") and brief.get("title_vi"):
        out.append(f"<p class='sub'>{rich(doc['title'])}</p>")
    out.append(f"<p class='meta'>Nguồn: {esc(doc.get('source',''))} · "
               f"Dịch bằng {esc(doc.get('model',''))}</p>")

    if brief:
        out.append("<h2>Tóm lược</h2>")
        if brief.get("one_line"):
            out.append(f"<p class='pull'>{rich(brief['one_line'])}</p>")
        rows = "".join(
            f"<b>{label}</b><p>{rich(brief[k])}</p>"
            for k, label in (("problem", "Bài toán"), ("gap", "Khoảng trống"),
                             ("idea", "Ý tưởng"), ("method", "Cách làm"),
                             ("evidence", "Bằng chứng"), ("limits", "Giới hạn"))
            if brief.get(k))
        if rows:
            out.append(f"<div class='card'>{rows}</div>")
        for key, label in (("argument_diagram", "Mạch lập luận của bài"),
                           ("method_diagram", "Cơ chế bài đề xuất")):
            out.append(mermaid(brief.get(key, ""), label))
        if brief.get("argument_chain"):
            out.append("<h3>Các bước lập luận</h3><ol>")
            for s in brief["argument_chain"]:
                out.append(f"<li><i>({esc(s.get('role',''))})</i> {rich(s.get('step',''))}</li>")
            out.append("</ol>")
        if brief.get("glossary"):
            out.append("<h3>Bảng thuật ngữ</h3><table>"
                       "<tr><th>Tiếng Anh</th><th>Tiếng Việt</th><th>Nghĩa</th></tr>")
            for g in brief["glossary"]:
                vi = "<i>giữ nguyên</i>" if g.get("keep_en") else rich(g.get("vi", ""))
                out.append(f"<tr><td>{rich(g.get('en',''))}</td><td>{vi}</td>"
                           f"<td>{rich(g.get('gloss',''))}</td></tr>")
            out.append("</table>")

    out.append("<h2>Nội dung</h2>")
    for b in doc["blocks"]:
        if b["type"] in ("reference", "meta"):
            continue
        vi = tr.get(b["id"], "")
        if b["type"] == "heading":
            out.append(f"<div class='pair'><div class='hd'>{rich(vi or b['text'])}</div></div>")
            continue
        if b["type"] == "equation":
            uri = _data_uri(doc_id, b["figure"]) if b.get("figure") else ""
            body = (f"<img src='{uri}' alt='công thức'>" if uri
                    else f"<div class='eq'>{rich(b['text'])}</div>")
            out.append(f"<div class='pair'><div class='eqbox'>{body}</div></div>")
            continue

        cells = []
        if b.get("figure"):
            uri = _data_uri(doc_id, b["figure"])
            if uri:
                cells.append(f"<figure><img src='{uri}' alt='{esc(b['text'][:90])}'></figure>")
        mk = f"<span class='li-mk'>{esc(b.get('marker'))}</span>" if b.get("marker") else ""
        if not one_col:
            cells.append(f"<div class='en'>{mk}{rich(b['text'])}</div>")
        vi_cell = rich(vi) if vi else "<span class='pending'>chưa dịch</span>"
        cells.append(f"<div class='vi'>{mk}{vi_cell}</div>")
        gl = plain.get(b["id"])
        if gl:
            cells.append(f"<div class='gl'><b>Giải thích</b><br>{rich(gl)}</div>")
        n = notes.get(b["id"])
        if n:
            dl = "".join(
                f"<dt>{label}</dt><dd>{rich(n[k])}</dd>"
                for k, label in (("gist", "Ý chính"), ("role", "Vai trò trong bài"),
                                 ("link_back", "Nối với đoạn trước"),
                                 ("unpack", "Giải thích chi tiết"), ("analogy", "Hình dung"),
                                 ("caution", "Cần lưu ý"), ("check", "Tự kiểm tra"))
                if n.get(k))
            cells.append(f"<div class='note'>{mermaid(n.get('diagram',''))}<dl>{dl}</dl></div>")
        out.append(f"<div class='pair{' li' if b.get('marker') else ''}'>{''.join(cells)}</div>")

    # Mermaid nặng 3.5MB — chỉ nhúng khi bài thật sự có sơ đồ để vẽ
    script = ""
    if diagrams:
        js = (WEB / "vendor" / "mermaid.min.js").read_text(encoding="utf-8")
        # bản in dùng theme sáng: nền tối in ra vừa xấu vừa tốn mực
        theme = ("'neutral'" if for_print
                 else "matchMedia('(prefers-color-scheme:dark)').matches?'dark':'neutral'")
        script = (f"<script>{js}</script><script>"
                  f"mermaid.initialize({{startOnLoad:false,securityLevel:'strict',theme:{theme},"
                  "flowchart:{curve:'basis',htmlLabels:false}});"
                  "window.__ready=mermaid.run().catch(()=>{});</script>")
    if for_print:
        # phải đợi sơ đồ vẽ xong rồi mới in, không thì PDF ra toàn ô trống
        script += ("<script>addEventListener('load',()=>"
                   "Promise.resolve(window.__ready).then(()=>setTimeout(print,300)));</script>")

    page = (f"<!doctype html><html lang='vi'><head><meta charset='utf-8'>"
            f"<meta name='viewport' content='width=device-width,initial-scale=1'>"
            f"<title>{esc(title_vi)}</title><style>{_EXPORT_CSS}</style></head>"
            f"<body class='{'one' if one_col else ''}'><main>{''.join(out)}</main>{script}</body></html>")

    if for_print:
        # inline chứ không attachment — phải hiện ra trong tab thì mới in được
        return Response(page, media_type="text/html; charset=utf-8")
    name = f"{doc_id}-{'vi' if one_col else 'song-ngu'}.html"
    return Response(page, media_type="text/html; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{name}"'})


# --------------------------------------------------------------- xuất slide

# Cỡ chữ quy từ bảng của Alley (28pt tiêu đề / 24pt thân / 18pt phụ / 14pt trích
# dẫn, trên slide cao 540pt) sang tỉ lệ chiều cao, rồi nhân với khung 1280×720.
# **24px là sàn tuyệt đối** — nhỏ hơn thì người ngồi cuối phòng không đọc được.
#
# `line-height` không dưới 1.3 là luật riêng cho tiếng Việt: dấu chồng (ế, ộ, ữ)
# bị cắt ngọn ở 1.0–1.15, thấy rõ nhất ở tiêu đề. Cũng vì thế mà không viết hoa
# toàn bộ và không siết letter-spacing.
# Ba thứ bị chỉ đích danh là dấu hiệu "slide do AI làm": **nền màu kem**, **hoa
# văn serif nghiêng**, và **thanh màu kẻ dọc cạnh mỗi ô chữ**. Gạch chân màu dưới
# tiêu đề cũng vậy — nên dùng khoảng trắng thay thế. Bản CSS này cố ý không có
# thứ nào trong số đó: nền trắng thật, một màu nhấn duy nhất dùng đúng hai chỗ
# (slide chốt lại và vạch trên slide tiêu đề), còn lại là khoảng trắng.
_SLIDES_CSS = """
:root{--page:#e9eaec;--slide:#fff;--ink:#0f172a;--ink-2:#1e293b;--muted:#64748b;
--accent:#2563eb;--frame:#16264a;--rule:#e6e9ee}
*{box-sizing:border-box}
body{margin:0;background:var(--page);color:var(--ink);
font:16px/1.5 "Helvetica Neue",Arial,ui-sans-serif,system-ui,sans-serif}
/* Thanh công cụ nổi ở góc dưới-trái, KHÔNG dính trên đầu: dính trên đầu thì nó
   che đúng dòng tiêu đề của slide đang cuộn qua — dòng quan trọng nhất. Góc
   dưới-trái trống, vì chân slide bám phải. */
.bar{position:fixed;left:14px;bottom:14px;z-index:5;display:flex;gap:1rem;
align-items:center;background:var(--slide);border:1px solid var(--rule);
border-radius:10px;padding:.55rem .9rem;box-shadow:0 3px 14px rgba(0,0,0,.16);
font-size:.85rem;color:var(--ink-2)}
@media print{.bar{display:none}}
.bar label{display:flex;gap:.35rem;align-items:center;cursor:pointer}
.wrap{padding:1.5rem 0 4rem}
/* `--s` do `slide_fit.autofit()` ĐO ra cho từng slide — đo, co, đo lại tới khi
   vừa khung. Thay cho việc chỉnh hằng số cỡ chữ bằng tay. */
.slide{width:1280px;height:720px;background:var(--slide);color:var(--ink);
padding:44px 60px 72px;position:relative;overflow:hidden;display:flex;
flex-direction:column;margin:0 auto 1.5rem;border-radius:2px;
box-shadow:0 1px 3px rgba(0,0,0,.08),0 8px 24px rgba(0,0,0,.10);
transform:scale(var(--fit,1));transform-origin:top center}

/* ---- đầu slide: nhãn phần · tiêu đề · dòng phụ ---- */
/* Sàn tuyệt đối cho chữ nhỏ: tự co bao nhiêu cũng không được xuống dưới mức
   đọc nổi. Để `max()` cho bộ dựng hình chặn, không tính tay ở Python. */
.eyebrow{font-size:max(13px,calc(var(--s,1)*15px));font-weight:700;letter-spacing:.16em;text-transform:uppercase;
color:var(--accent);margin:0 0 10px}
.slide h2{font-size:calc(var(--s,1)*42px);line-height:1.16;font-weight:800;margin:0;
letter-spacing:-.02em;max-width:23em}
.sub{font-size:max(16px,calc(var(--s,1)*20px));line-height:1.45;color:var(--muted);margin:10px 0 0;max-width:46em}
.head{flex:none;margin-bottom:18px}

/* ---- thẻ nội dung: nền pastel, chip icon, tiêu đề đậm ---- */
/* `flex:none`: lưới thẻ tự cao theo nội dung. Để `flex:1` thì nó bị chia phần
   chiều cao còn lại rồi `overflow:hidden` cắt cụt chữ ở đáy thẻ — lỗi này chỉ
   nhìn ảnh chụp mới thấy, đo chiều cao không ra. */
.cards{display:grid;gap:16px;flex:none;align-content:start}
.card{overflow:visible}
.cards.n2{grid-template-columns:1fr 1fr}
.cards.n3{grid-template-columns:repeat(3,1fr)}
.cards.n4{grid-template-columns:repeat(4,1fr)}
.card{border-radius:12px;padding:18px;display:flex;flex-direction:column;gap:10px;
min-height:0;overflow:hidden}
.card-h{display:flex;align-items:center;gap:12px;flex:none}
.chip{width:38px;height:38px;border-radius:9px;display:flex;align-items:center;
justify-content:center;flex:none}
.card-t{font-size:calc(var(--s,1)*21px);font-weight:700;line-height:1.25;color:var(--ink)}
.card-m{font-size:max(12px,calc(var(--s,1)*14px));color:var(--muted);font-weight:500;margin-left:6px;
letter-spacing:.04em;text-transform:uppercase}
.card ul{margin:0;padding:0;list-style:none;display:flex;flex-direction:column;gap:8px}
.card li{font-size:max(15px,calc(var(--s,1)*18px));line-height:1.5;color:var(--ink-2);padding-left:16px;position:relative}
.card li::before{content:"";position:absolute;left:0;top:.62em;width:5px;height:5px;
border-radius:50%;background:var(--accent);opacity:.55}

/* ---- gạch đầu dòng trần, khi nội dung không chia được thành thẻ ---- */
/* `flex:none` cùng lý do với `.cards`: để `flex:1` thì khối chữ tranh chiều cao
   với hình, và hình bị bóp còn vài chục pixel — bảng số liệu thành không đọc
   nổi. Chữ lấy đúng chiều cao của nó, phần còn lại dành hết cho hình. */
.plain{flex:none;display:flex;flex-direction:column;gap:14px}
.plain li{font-size:max(16px,calc(var(--s,1)*21px));line-height:1.55;color:var(--ink-2);list-style:none;
padding-left:20px;position:relative}
.plain li::before{content:"";position:absolute;left:0;top:.62em;width:6px;height:6px;
border-radius:50%;background:var(--accent);opacity:.6}
.plain ul{margin:0;padding:0}

/* ---- hình: khung navy dày + chú thích nghiêng ---- */
figure{margin:0;display:flex;flex-direction:column;gap:10px;flex:1;min-height:200px}
.frame{border:3px solid var(--frame);border-radius:6px;padding:6px;background:#fff;
flex:1;min-height:180px;display:flex;align-items:center;justify-content:center;
overflow:hidden}
.frame img{max-width:100%;max-height:100%;object-fit:contain}
figcaption{font-size:max(15px,calc(var(--s,1)*17px));line-height:1.4;font-style:italic;color:var(--muted);
text-align:center;flex:none}
/* Mermaid tự đặt width/height và `style="max-width:…"` ngay trên thẻ svg, đè
   mọi ràng buộc của khung. Phải ép bằng !important, nếu không sơ đồ phình ra
   tràn khỏi cả slide và đè lên tiêu đề. */
/* Ô chờ ảnh — chỉ hiện trên bản xem trước, KHÔNG in ra và không vào file xuất
   cho người khác xem. Nó là lời nhắc cho người dựng slide, không phải nội dung. */
.artslot{flex:1;min-height:120px;border:2px dashed #cbd5e1;border-radius:12px;
display:flex;flex-direction:column;align-items:center;justify-content:center;
gap:6px;color:#94a3b8}
.artslot span{font-size:19px;font-weight:600}
.artslot em{font-size:15px;font-style:normal}
@media print{.artslot{display:none}}
.mermaid{flex:1;min-height:0;min-width:0;display:flex;align-items:center;
justify-content:center;overflow:hidden}
/* `width:auto` để svg vẽ ở CỠ TỰ NHIÊN của nó — mermaid sinh ra sơ đồ chừng
   vài trăm pixel, nên trên slide 1280px nó thành một vệt bé tí giữa khung dù
   còn thừa chỗ. Svg của mermaid có `viewBox`, nên đặt 100% cả hai chiều là nó
   tự co giãn vừa khung mà vẫn giữ đúng tỉ lệ (`preserveAspectRatio` mặc định).
   Khung cha đã chặn chiều cao nên không sợ phình ra. */
.mermaid svg{max-width:100%!important;max-height:100%!important;
width:100%!important;height:100%!important}
/* Mermaid PHỚT LỜ `themeVariables` (đã thử: vẫn trả #ececff / mediumpurple của
   theme mặc định), nên ép màu bằng CSS — cách này không phụ thuộc vào API cấu
   hình của thư viện, và kiểm lại được bằng ảnh chụp. */
.mermaid .node rect,.mermaid .node polygon,.mermaid .node circle,
.mermaid .node ellipse,.mermaid .node path{
fill:#e9eefc!important;stroke:#2563eb!important;stroke-width:1.5px!important}
.mermaid .cluster rect{fill:#f6f8fd!important;stroke:#c7d2e8!important}
.mermaid .nodeLabel,.mermaid .label,.mermaid text{fill:#0f172a!important;color:#0f172a!important}
.mermaid .edgePath .path,.mermaid .flowchart-link,.mermaid path.path{
stroke:#64748b!important;stroke-width:1.6px!important}
.mermaid marker path,.mermaid .arrowheadPath,.mermaid marker *{
fill:#64748b!important;stroke:#64748b!important}
.mermaid .edgeLabel,.mermaid .edgeLabel rect{fill:#fff!important;background:#fff!important}
.mermaid .edgeLabel text,.mermaid .edgeLabel span{fill:#334155!important;color:#334155!important}

/* Công thức phải co theo vòng autofit như mọi thứ khác. Để cứng 26px thì slide
   có công thức không bao giờ vừa được, dù mọi phần khác đã co hết cỡ. */
.eq{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;
font-size:max(18px,calc(var(--s,1)*26px));
text-align:center;padding:max(12px,calc(var(--s,1)*22px));
background:#e9eefc;border-radius:12px;flex:none}

/* ---- bố cục ---- */
.body{flex:1;min-height:0;display:flex;flex-direction:column;gap:18px}
/* `.vis` bọc hình HOẶC sơ đồ. Không cho nó co lại thì `flex:1` của `figure` và
   `max-height:100%` của svg đều đo theo một cha cao tự do, tức là không đo gì
   cả: ảnh hiện ở cỡ gốc, sơ đồ mermaid phình tới 4000px. Đã vấp thật —
   10/20 slide tràn khung, và bộ đo bằng Python không thấy vì nó không mô phỏng
   chỗ này.
   Nhưng `min-height:0` thì thành lỗi ngược lại: thẻ ăn hết chiều cao và sơ đồ
   co còn một vệt vài chục pixel — chặn được tràn mà bằng chứng thành vô hình,
   tệ hơn hẳn. Bằng chứng mới là lý do slide tồn tại, nên nó giữ tối thiểu 32%;
   nhồi thêm chữ thì slide tràn và `check_slides` kêu, đúng thứ cần xảy ra. */
.vis{flex:1;min-height:38%;min-width:0;display:flex;flex-direction:column}
.two{flex:1;min-height:0;min-width:0;display:grid;grid-template-columns:minmax(0,44fr) minmax(0,56fr);
gap:36px;align-items:stretch}
.two>*{min-width:0;min-height:0;display:flex;flex-direction:column;gap:16px;justify-content:center}
.L-figwide .body{gap:16px}

/* ---- số liệu lớn ---- */
.stats{display:flex;gap:56px;flex:none;flex-wrap:wrap}
.stat-v{font-size:calc(var(--s,1)*40px);font-weight:800;color:var(--accent);line-height:1.1;letter-spacing:-.02em}
.stat-l{font-size:16px;line-height:1.4;color:var(--muted);margin-top:4px;max-width:16em}

/* ---- hộp chốt lại chạy hết bề ngang ---- */
.callout{border-radius:12px;background:#e0e9fd;padding:15px 22px;display:flex;
gap:16px;align-items:center;flex:none}
.callout .chip{width:38px;height:38px;border-radius:9px}
.callout b{display:block;font-size:calc(var(--s,1)*21px);font-weight:700;line-height:1.3}
.callout span{display:block;font-size:max(14px,calc(var(--s,1)*17px));line-height:1.4;color:var(--muted);margin-top:3px}

/* ---- chú thích thuật ngữ mới, ghép tự động từ bảng thuật ngữ ---- */
/* Chú thuật ngữ và chân slide nằm cùng một hàng ngang: chú giải bám trái và
   chỉ chiếm 62%, chân slide bám phải — không bao giờ đè nhau. */
.terms{flex:none;display:flex;gap:28px;border-top:1px solid var(--rule);
padding-top:10px;max-width:62%}
.terms div{font-size:max(13px,calc(var(--s,1)*15px));line-height:1.4;color:var(--muted);
max-width:100%;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.terms b{color:var(--accent);font-weight:700}

/* ---- chân slide ---- */
.foot{position:absolute;right:60px;bottom:22px;text-align:right;
font-size:13px;color:var(--muted);max-width:34%;overflow:hidden;
text-overflow:ellipsis;white-space:nowrap}

/* ---- slide tiêu đề và vách ngăn: nền vạch chéo ---- */
.deco{position:absolute;inset:0;pointer-events:none;background:
repeating-linear-gradient(48deg,rgba(37,99,235,.10) 0 3px,transparent 3px 15px);
-webkit-mask-image:linear-gradient(105deg,transparent 42%,#000 100%);
mask-image:linear-gradient(105deg,transparent 42%,#000 100%)}
.L-title{justify-content:center}
.L-title h1{font-size:52px;line-height:1.14;font-weight:800;margin:0 0 18px;
letter-spacing:-.025em;max-width:17em;position:relative}
.L-title .sub{font-size:22px;margin:0 0 46px;max-width:36em;position:relative}
.L-title .who{font-size:18px;line-height:1.65;color:var(--ink-2);position:relative}
.L-title .who .dim{color:var(--muted)}
.L-section{justify-content:center;align-items:center;text-align:center}
.L-section .eyebrow{margin-bottom:16px;position:relative}
.L-section h2{font-size:52px;font-weight:800;margin:0;max-width:16em;position:relative}
.L-section .sub{margin:16px auto 0;position:relative}
/* ảnh minh hoạ trên vách ngăn và slide tiêu đề — thuần trang trí, cỡ vừa phải */
.art{position:relative;margin-top:26px;display:flex;justify-content:center}
.art img{max-height:210px;max-width:36%;object-fit:contain}
.L-title .art{position:absolute;right:70px;top:50%;transform:translateY(-50%);margin:0}
.L-title .art img{max-height:330px;max-width:330px}
.L-closing{justify-content:center;align-items:center;text-align:center}
.L-closing h2{font-size:52px;font-weight:800;margin:0 0 14px}

/* ---- mục lục: badge số vuông bo góc ---- */
.L-agenda .ag{display:flex;flex-direction:column;gap:16px;flex:1;
min-height:0;justify-content:center}
.ag-row{display:grid;grid-template-columns:54px 1fr;gap:22px;align-items:center;
border-radius:12px;padding:14px 20px}
.ag-n{width:54px;height:54px;border-radius:12px;display:flex;align-items:center;
justify-content:center;color:#fff;font-size:24px;font-weight:800}
.ag-t{font-size:23px;font-weight:700;line-height:1.3}
.ag-d{font-size:18px;line-height:1.4;color:var(--muted);margin-top:3px}

/* lời người nói: mặc định ẩn, bật lên thì mỗi slide kèm một trang lời nói */
.notes{display:none}
.with-notes .notes{display:block;width:1280px;height:720px;background:var(--slide);
padding:60px 72px;margin:0 auto 1.5rem;font-size:26px;line-height:1.5;
border-radius:2px;box-shadow:0 1px 3px rgba(0,0,0,.08);
transform:scale(var(--fit,1));transform-origin:top center}
.notes b{display:block;font-size:18px;text-transform:uppercase;letter-spacing:.09em;
color:var(--muted);margin-bottom:22px;font-weight:700}
.bk{width:1280px;margin:2.5rem auto 1rem;font-size:18px;color:var(--muted);
text-transform:uppercase;letter-spacing:.1em;transform:scale(var(--fit,1));
transform-origin:top center}

/* ---- bố cục tự do: từng phần tự định vị bằng % khung slide ---- */
/* Chỉ bật cho slide người dùng tự chọn. Mặc định vẫn là luồng flexbox tự sắp —
   đó là thứ giữ cả bộ nhất quán và là chỗ autofit bám vào.
   `display:contents` cho các khối trung gian là điểm mấu chốt: nếu để `.body`
   có khung riêng (dù static hay absolute) thì phần trăm của con tính theo NÓ
   chứ không theo slide, và mọi thứ dồn về góc trên trái. */
.slide.is-free .body,.slide.is-free .cols,.slide.is-free .cards,
.slide.is-free .ag{display:contents}
.slide.is-free .part{position:absolute;margin:0;overflow:hidden}
.slide.is-free .card,.slide.is-free .ag-row{height:100%}
.slide.is-free .vis{display:flex;align-items:center;justify-content:center}
.slide.is-free .vis figure,.slide.is-free .vis .mermaid{height:100%;flex:1}
.slide.is-free .artslot{height:100%}
@page{size:338.7mm 190.5mm;margin:0}
@media print{
body{background:#fff}
.bar,.bk{display:none}
.wrap{padding:0}
.slide{transform:none;margin:0;border-radius:0;box-shadow:none;
break-after:page;break-inside:avoid}
.with-notes .notes{transform:none;margin:0;border-radius:0;box-shadow:none;
break-after:page;break-inside:avoid}
}
"""


def _clip(s: str, n: int) -> str:
    """Cắt ở ranh giới từ. Cắt giữa chữ ra “trả lời câu hỏ” — trông như lỗi."""
    s = (s or "").strip()
    if len(s) <= n:
        return s
    cut = s[:n].rsplit(" ", 1)[0] or s[:n]
    return cut.rstrip(" ,;:.") + "…"


def _export_slides_html(doc: dict, *, for_print: bool = False) -> Response:
    """Bộ slide thành một file HTML tự chứa, in ra PDF được.

    Đi cùng đường với `_export_html`: ảnh nhúng base64, mermaid chỉ nhúng khi bài
    thật sự có sơ đồ, và bản in đợi `window.__ready` rồi mới mở hộp in. Khác một
    chỗ: mỗi `.slide` cao đúng một trang ngang nên `break-after:page` cho ra đúng
    một slide một trang.

    Không dùng reveal.js hay thư viện slide nào: công thức đã lưu sẵn dạng
    `^{…}` / `_{…}` nên `rich()` dựng được, tức là không cần KaTeX kèm bộ font.
    """
    import html as _h

    doc_id = doc["id"]
    brief = doc.get("brief") or {}
    slides = doc.get("slides") or {}
    deck = list(slides.get("deck") or [])
    backup = list(slides.get("backup") or [])
    if not deck and not backup:
        raise HTTPException(400, "Bài này chưa có bộ slide nào — bấm “Dựng slide” trước")

    diagrams: list[str] = []

    def esc(s) -> str:
        return _h.escape(str(s or ""))

    def rich(s) -> str:
        """Như `esc` nhưng dựng `^{…}` / `_{…}` thành chỉ số trên/dưới thật.

        Chỉ dùng cho chữ trên slide — KHÔNG dùng cho mã Mermaid hay `alt`.
        """
        out = esc(s)
        out = re.sub(r"\^\{([^{}]*)\}", r"<sup>\1</sup>", out)
        return re.sub(r"_\{([^{}]*)\}", r"<sub>\1</sub>", out)

    title_vi = brief.get("title_vi") or doc.get("title") or "Bài báo"
    foot = esc(_clip(title_vi, 70))
    ctx_title_en = doc.get("title") or ""
    # `venue_guess` có khi là cả một câu — trên slide tiêu đề nó chỉ được một dòng
    venue = _clip(brief.get("venue_guess") or "", 80)
    # thuật ngữ lần đầu xuất hiện thì gắn chú thích, lấy từ bảng đã chốt ở brief
    pipeline.attach_terms(doc, deck)
    pipeline.attach_terms(doc, backup)

    def one(sl: dict, no: str) -> str:
        lay = pipeline.slide_layout(sl, doc_id)
        head = sl.get("headline") or ""
        cards = [c for c in (sl.get("cards") or []) if (c or {}).get("title")]
        bl = [b for b in (sl.get("bullets") or []) if (b or "").strip()]

        def chip(name: str, i: int, sz: int = 22) -> str:
            svg = theme.icon_svg(name, sz)
            if not svg:
                return ""
            return f"<span class='chip' style='background:{theme.chip_color(i)}'>{svg}</span>"

        def card_html(c: dict, i: int) -> str:
            items = "".join(f"<li>{rich(x)}</li>"
                            for x in (c.get("bullets") or []) if (x or "").strip())
            meta = f"<span class='card-m'>{esc(c.get('meta'))}</span>" if c.get("meta") else ""
            return (f"<div class='card part' data-part=\"card{i}\" "
                    f"style='background:{theme.card_tint(i)}'>"
                    f"<div class='card-h'>{chip(c.get('icon'), i)}"
                    f"<div><span class='card-t'>{rich(c.get('title'))}</span>{meta}</div></div>"
                    f"{f'<ul>{items}</ul>' if items else ''}</div>")

        def cards_html() -> str:
            if not cards:
                return ""
            n = min(len(cards), 4)
            inner = "".join(card_html(c, i) for i, c in enumerate(cards))
            return f"<div class='cards n{n}'>{inner}</div>"

        def plain_html() -> str:
            if not bl:
                return ""
            return ("<div class='plain part' data-part=\"bullets\"><ul>"
                    + "".join(f"<li>{rich(b)}</li>" for b in bl) + "</ul></div>")

        def placeholder_html() -> str:
            """Ô chờ ảnh — chỉ hiện khi slide CÒN CHỖ thật cho một tấm ảnh.

            Slide đã kín thẻ và hộp chốt thì chỗ trống chỉ còn vài chục pixel;
            hiện ô chờ ở đó là mời người dùng bỏ ảnh vào một khe không nhìn ra
            gì. `slide_fit.room_for_art()` đo chỗ trống thật rồi mới quyết.
            """
            room = int(sl.get("art_room") or 0)
            if room < slide_fit.MIN_ART_H:
                return ""
            return (f"<div class='artslot' style='min-height:{min(room, 300)}px'>"
                    "<span>Chỗ dành cho ảnh minh hoạ</span>"
                    "<em>Prompt có sẵn ở ô sửa slide — tự tạo rồi tải lên</em></div>")

        def visual_html() -> str:
            return f"<div class='vis part' data-part=\"visual\">{_vis_inner()}</div>"

        def _vis_inner() -> str:
            out = []
            if (fig := sl.get("figure")) and (uri := _data_uri(doc_id, fig)):
                note = (sl.get("figure_note") or "").strip()
                # Ảnh AI vẽ phải nói rõ nó là minh hoạ — người xem không được
                # nhầm nó với hình thật của tác giả.
                if sl.get("illus"):
                    note = ("Hình minh hoạ khái niệm, không phải hình trong bài báo."
                            + (f" {note}" if note else ""))
                cap = f"<figcaption>{rich(note)}</figcaption>" if note else ""
                out.append(f"<figure><div class='frame'><img src='{uri}' "
                           f"alt='{esc(_clip(head, 90))}'></div>{cap}</figure>")
            if (dia := (sl.get("diagram") or "").strip()):
                diagrams.append(dia)
                out.append(f"<div class='mermaid'>{esc(dia)}</div>")
            if (eq := (sl.get("equation") or "").strip()):
                out.append(f"<div class='eq'>{rich(eq)}</div>")
            return "".join(out)

        def stats_html() -> str:
            st = [s for s in (sl.get("stats") or []) if (s or {}).get("value")]
            if not st:
                return ""
            inner = "".join(f"<div><div class='stat-v'>{rich(s.get('value'))}</div>"
                            f"<div class='stat-l'>{rich(s.get('label'))}</div></div>"
                            for s in st[:2])
            return f"<div class='stats part' data-part=\"stats\">{inner}</div>"

        def callout_html() -> str:
            co = sl.get("callout") or {}
            if not (co.get("title") or co.get("body")):
                return ""
            body = f"<span>{rich(co.get('body'))}</span>" if co.get("body") else ""
            return (f"<div class='callout part' data-part=\"callout\">"
                    f"{chip(co.get('icon') or 'check', 0, 19)}"
                    f"<div><b>{rich(co.get('title'))}</b>{body}</div></div>")

        def terms_html() -> str:
            tm = sl.get("terms") or []
            if not tm:
                return ""
            inner = "".join(f"<div><b>{esc(t['en'])}</b> — {rich(t['gloss'])}</div>"
                            for t in tm)
            return f"<div class='terms part' data-part=\"terms\">{inner}</div>"

        def header() -> str:
            # số phần đứng trước tên phần: đọc giữa chừng vẫn biết mình đang ở
            # đâu trong lộ trình mà mục lục đã hứa
            eb_txt = (sl.get("eyebrow") or "").strip()
            if eb_txt and sl.get("_secno"):
                eb_txt = f"{sl['_secno']} · {eb_txt}"
            eb = f"<p class='eyebrow'>{esc(eb_txt)}</p>" if eb_txt else ""
            sub = (f"<p class='sub'>{rich(sl.get('sub'))}</p>"
                   if (sl.get("sub") or "").strip() else "")
            return f"<div class='head part' data-part=\"head\">{eb}<h2>{rich(head)}</h2>{sub}</div>"

        # ------------------------------------------------------------ bố cục
        if lay == "title":
            art = ""
            if (fg := sl.get("figure")) and (u := _data_uri(doc_id, fg)):
                art = (f"<div class='art part' data-part=\"visual\">"
                       f"<img src='{u}' alt=''></div>")
            sub = (f"<p class='sub'>{rich(sl.get('sub') or ctx_title_en)}</p>")
            who = "<br>".join(x for x in (
                esc(venue), f"<span class='dim'>{esc(_clip(doc.get('source',''), 90))}</span>"
            ) if x)
            inner = ("<div class='deco'></div>"
                     "<div class='part' data-part=\"head\">"
                     f"<p class='eyebrow'>{esc(sl.get('eyebrow') or 'BÁO CÁO SEMINAR')}</p>"
                     f"<h1>{rich(head or title_vi)}</h1>{sub}"
                     f"<p class='who'>{who}</p></div>{art}")
        elif lay == "section":
            sub = (f"<p class='sub'>{rich(sl.get('sub'))}</p>"
                   if (sl.get("sub") or "").strip() else "")
            # vách ngăn được phép mang ảnh minh hoạ — đây là chỗ an toàn nhất
            # cho ảnh AI vẽ, vì nó thuần trang trí, không đóng vai bằng chứng
            art = ""
            if (fg := sl.get("figure")) and (u := _data_uri(doc_id, fg)):
                art = (f"<div class='art part' data-part=\"visual\">"
                       f"<img src='{u}' alt=''></div>")
            inner = ("<div class='deco'></div>"
                     "<div class='part' data-part=\"head\">"
                     f"<p class='eyebrow'>{esc(sl.get('eyebrow') or 'PHẦN')}</p>"
                     f"<h2>{rich(head)}</h2>{sub}</div>{art}")
        elif lay == "agenda":
            rows = []
            for i, c in enumerate(cards):
                desc = next((x for x in (c.get("bullets") or []) if (x or "").strip()), "")
                rows.append(
                    f"<div class='ag-row part' data-part=\"ag{i}\" "
                    f"style='background:{theme.card_tint(i)}'>"
                    f"<span class='ag-n' style='background:{theme.chip_color(i)}'>{i+1}</span>"
                    f"<div><div class='ag-t'>{rich(c.get('title'))}</div>"
                    f"{f'<div class=ag-d>{rich(desc)}</div>' if desc else ''}</div></div>")
            inner = header() + f"<div class='ag'>{''.join(rows)}</div>"
        elif lay == "closing":
            inner = (f"<h2>{rich(head)}</h2>"
                     + (f"<p class='sub'>{rich(sl.get('sub'))}</p>"
                        if (sl.get("sub") or "").strip() else "")
                     + plain_html())
        elif lay == "figwide":
            # ảnh ngang: chữ ở trên, ảnh tràn cả bề ngang ở dưới
            inner = (header() + "<div class='body'>"
                     + (cards_html() or plain_html()) + visual_html()
                     + stats_html() + callout_html() + terms_html() + "</div>")
        elif lay in ("figside", "split"):
            left = (cards_html() or plain_html()) + stats_html()
            inner = (header() + f"<div class='body'>"
                     f"<div class='two'><div>{left}</div>"
                     f"<div>{visual_html()}</div></div>"
                     + callout_html() + terms_html() + "</div>")
        elif lay == "figfull":
            inner = (header() + f"<div class='body'>{visual_html()}"
                     + callout_html() + terms_html() + "</div>")
        elif lay == "cards":
            inner = (header() + f"<div class='body'>{cards_html()}{stats_html()}"
                     f"{placeholder_html()}" + callout_html() + terms_html() + "</div>")
        else:
            inner = (header() + f"<div class='body'>{plain_html()}{stats_html()}"
                     + callout_html() + terms_html() + "</div>")

        foot_html = ("" if lay == "title"
                     else f"<div class='foot'>{foot} · {esc(no)}</div>")
        sc = slide_fit.autofit(sl, lay)
        st = f" style='--s:{sc}'" if sc < 1 else ""
        # Bố cục tự do: mỗi phần mang toạ độ % của riêng nó. Tính theo % nên
        # slide co giãn thế nào cũng giữ đúng tỉ lệ người dùng đã kéo.
        cls = f"slide L-{lay}"
        if sl.get("free") and isinstance(sl.get("boxes"), dict):
            cls += " is-free"
            for key, b in sl["boxes"].items():
                if not (isinstance(b, (list, tuple)) and len(b) == 4):
                    continue
                # bỏ đuôi .0 cho gọn: `left:5%` chứ không phải `left:5.0%`
                x, y, w, h = (f"{float(v):.3f}".rstrip("0").rstrip(".") for v in b)
                inner = inner.replace(
                    f'data-part="{key}"',
                    f'data-part="{key}" style="left:{x}%;top:{y}%;'
                    f'width:{w}%;height:{h}%"', 1)
        out = f"<section class='{cls}'{st}>{inner}{foot_html}</section>"
        if (nt := (sl.get("notes") or "").strip()):
            out += f"<div class='notes'><b>Lời nói · slide {esc(no)}</b>{rich(nt)}</div>"
        return out

    # "PHẦN 2 / 3" trên vách ngăn — tính ở đây chứ không hỏi model, nó không đếm
    # được số vách ngăn cuối cùng còn lại sau khi người dùng xoá bớt slide
    pipeline.number_sections(deck)
    pipeline.number_sections(backup)

    pages = [one(sl, str(i)) for i, sl in enumerate(deck, 1)]
    if backup:
        pages.append("<p class='bk'>Slide dự phòng — dùng khi có câu hỏi</p>")
        pages += [one(sl, f"D{i}") for i, sl in enumerate(backup, 1)]

    bar = ("<div class='bar'><label><input type='checkbox' id='nt'> "
           "Kèm lời người nói</label>"
           "<button onclick='print()'>In / lưu PDF</button>"
           "<span>Mỗi slide in ra đúng một trang ngang.</span></div>"
           "<script>nt.onchange=()=>document.body.classList.toggle("
           "'with-notes',nt.checked)</script>")

    autofit = """<script>
/* Tự co cho vừa khung — đúng thuật toán `normAutofit fontScale` của PowerPoint:
   ĐO bằng chính bộ dựng hình, giảm cỡ chữ, đo lại. `slide_fit.py` bên server chỉ
   còn là ước lượng để cảnh báo và để quyết chỗ đặt ảnh; nó là bản mô phỏng
   flexbox viết tay nên luôn thiếu một thứ gì đó (gap, min-height, margin gộp).
   Chỗ này thì không đoán: trình duyệt nói tràn là tràn. */
(function () {
  var LO = 0.7, STEP = 0.03;
  function fit(el) {
    var s = parseFloat(el.style.getPropertyValue('--s')) || 1, n = 0;
    while (el.scrollHeight > el.clientHeight + 1 && s > LO && n++ < 30) {
      s = Math.max(LO, s - STEP);
      el.style.setProperty('--s', s.toFixed(3));
    }
  }
  function all() { document.querySelectorAll('.slide').forEach(fit); }
  if (document.fonts && document.fonts.ready) document.fonts.ready.then(all);
  addEventListener('load', all);
  all();
})();
</script>"""

    # co slide cho vừa bề ngang cửa sổ; bản in giữ nguyên 1280px của @page
    fit = ("<script>const fit=()=>document.documentElement.style.setProperty("
           "'--fit',Math.min(1,(innerWidth-48)/1280));fit();addEventListener("
           "'resize',fit);</script>")

    # Mermaid nặng 3.5MB — chỉ nhúng khi bộ slide thật sự có sơ đồ
    script = ""
    if diagrams:
        js = (WEB / "vendor" / "mermaid.min.js").read_text(encoding="utf-8")
        script = (f"<script>{js}</script><script>"
                  "mermaid.initialize({startOnLoad:false,securityLevel:'strict',"
                  "theme:'base',themeVariables:{"
                  "primaryColor:'#e9eefc',primaryBorderColor:'#2563eb',"
                  "primaryTextColor:'#0f172a',lineColor:'#64748b',"
                  "secondaryColor:'#ddf3f5',tertiaryColor:'#e4f5ea',"
                  "fontFamily:'Helvetica Neue,Arial,sans-serif',fontSize:'15px'},"
                  "flowchart:{curve:'basis',htmlLabels:false}});"
                  "window.__ready=mermaid.run().catch(()=>{});</script>")
    if for_print:
        # phải đợi sơ đồ vẽ xong rồi mới in, không thì slide ra toàn ô trống
        script += ("<script>addEventListener('load',()=>"
                   "Promise.resolve(window.__ready).then(()=>setTimeout(print,300)));</script>")

    page = (f"<!doctype html><html lang='vi'><head><meta charset='utf-8'>"
            f"<meta name='viewport' content='width=device-width,initial-scale=1'>"
            f"<title>{esc(title_vi)} — slide</title><style>{_SLIDES_CSS}</style></head>"
            f"<body>{bar}<div class='wrap'>{''.join(pages)}</div>"
            f"{fit}{script}{autofit}</body></html>")

    if for_print:
        # inline chứ không attachment — phải hiện ra trong tab thì mới in được
        return Response(page, media_type="text/html; charset=utf-8")
    return Response(page, media_type="text/html; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{doc_id}-slide.html"'})


# Kho survey — cơ chế thứ hai, tách hẳn khỏi luồng đọc-hiểu ở trên. Phải gắn
# TRƯỚC dòng mount bên dưới, nếu không bộ phục vụ file tĩnh nuốt hết và trả 404.
from . import survey_api  # noqa: E402

app.include_router(survey_api.router)


class _NoCacheStatic(StaticFiles):
    """File tĩnh luôn phải hỏi lại server xem có bản mới không.

    `StaticFiles` mặc định chỉ gửi `last-modified` + `etag` mà **không** gửi
    `Cache-Control`. Thiếu chỉ dẫn, trình duyệt tự đoán bằng heuristic và giữ bản
    cũ lại một lúc — nên sau khi sửa CSS/JS, người dùng nhận HTML mới kèm CSS cũ:
    giao diện vỡ tan mà không có lỗi nào. Đã vấp đúng vậy.

    `no-cache` KHÔNG phải là không cache: bản cũ vẫn nằm trên đĩa, trình duyệt
    chỉ hỏi lại một câu và nhận `304 Not Modified` nếu file không đổi. Với công
    cụ chạy trên máy mình thì giá của câu hỏi đó bằng không.
    """

    async def get_response(self, path, scope):
        resp = await super().get_response(path, scope)
        resp.headers.setdefault("Cache-Control", "no-cache")
        return resp


app.mount("/", _NoCacheStatic(directory=WEB), name="web")
