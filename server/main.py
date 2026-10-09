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

from . import db, layout, llm, parser, pipeline, prompts, slide, store, thongtin  # noqa: E402

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


_ASSETS = ("vendor/fonts.css", "style.css", "slide.css", "survey.css", "app.js", "survey.js",
           "thuvien.js", "slide-ve.js", "slide.js")


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


# ---------------------------------------------------- thư viện: chọn nhiều, thư mục

# Trần số bài một lượt chọn. Thư viện cỡ vài trăm bài thì "chọn hết" vẫn lọt;
# chặn là để một request lỗi không xoá cả kho trong một nhát.
_MAX_CHON = 500


def _ids(body: dict) -> list[str]:
    """Danh sách mã bài từ body, đã kiểm. Mã phải `isalnum` — cùng hàng rào chống
    path traversal với `store._check`, vì `store.delete` dựng đường dẫn file từ nó."""
    ids = body.get("ids")
    if not isinstance(ids, list) or not ids:
        raise HTTPException(400, "Chưa chọn bài nào")
    if len(ids) > _MAX_CHON:
        raise HTTPException(400, f"Chọn tối đa {_MAX_CHON} bài một lượt")
    if not all(isinstance(i, str) and i.isalnum() for i in ids):
        raise HTTPException(400, "Mã bài không hợp lệ")
    return list(dict.fromkeys(ids))          # bỏ trùng, giữ thứ tự


def _ten_thu_muc(body: dict, bo_qua: str | None = None) -> str:
    name = " ".join(str(body.get("name") or "").split())
    if not name:
        raise HTTPException(400, "Tên thư mục không được để trống")
    if len(name) > db.FOLDER_NAME_MAX:
        raise HTTPException(400, f"Tên thư mục dài quá {db.FOLDER_NAME_MAX} ký tự")
    # Không cho hai thư mục trùng tên (không phân biệt hoa thường): hai hộp cùng
    # nhãn "Robot" thì chuyển bài vào đâu cũng là đoán.
    if any(f["name"].lower() == name.lower() and f["id"] != bo_qua for f in db.list_folders()):
        raise HTTPException(409, f"Đã có thư mục tên \"{name}\"")
    return name


@app.get("/api/folders")
async def folders():
    return db.list_folders()


# ------------------------------------------- thư viện kiểu Zotero: thông tin & nhãn

_NGAM: set = set()      # giữ tham chiếu tới tác vụ ngầm, không thì GC dọn mất giữa chừng


def _lay_thong_tin_ngam(doc_id: str) -> None:
    # Tắt được (`META_LOOKUP=off`): bộ test không được gọi mạng ngoài.
    if os.getenv("META_LOOKUP", "on").lower() == "off":
        return

    async def chay():
        try:
            doc = store.load(doc_id)
            d, nguon = await thongtin.tim(doc)
            if d:
                db.set_meta(doc_id, d, nguon)
        except Exception as e:  # noqa: BLE001 — việc phụ, hỏng thì để nút lấy bù
            print(f"[thông tin] {doc_id}: {type(e).__name__}: {e}")
    t = asyncio.get_event_loop().create_task(chay())
    _NGAM.add(t)
    t.add_done_callback(_NGAM.discard)


@app.get("/api/doc/{doc_id}/meta")
async def get_meta(doc_id: str):
    if not store.exists(doc_id):
        raise HTTPException(404, "Không tìm thấy tài liệu")
    return db.get_meta(doc_id)


@app.post("/api/doc/{doc_id}/meta/fetch")
async def fetch_meta(doc_id: str):
    """Tra tác giả / năm / nơi đăng từ Semantic Scholar, arXiv, Crossref. Miễn phí.
    Trường người dùng đã sửa tay không bị ghi đè."""
    try:
        doc = store.load(doc_id)
    except KeyError:
        raise HTTPException(404, "Không tìm thấy tài liệu")
    d, nguon = await thongtin.tim(doc)
    if not d:
        raise HTTPException(404, "Không tìm ra thông tin bài này — tiêu đề có thể bị bóc sai, "
                                 "hoặc bài không có trên arXiv/Crossref. Bạn điền tay được.")
    return db.set_meta(doc_id, d, nguon)


@app.patch("/api/doc/{doc_id}/meta")
async def edit_meta(doc_id: str, body: dict = Body(...)):
    """Sửa tay thông tin bài. Tác giả nhận cả danh sách lẫn chuỗi ngăn bằng `;`."""
    if not store.exists(doc_id):
        raise HTTPException(404, "Không tìm thấy tài liệu")
    d = {k: v for k, v in body.items() if k in db.META_FIELDS}
    if isinstance(d.get("authors"), str):
        d["authors"] = [a.strip() for a in re.split(r"[;\n]", d["authors"]) if a.strip()]
    if "year" in d:
        y = str(d["year"] or "").strip()
        if y and not (y.isdigit() and 1900 <= int(y) <= 2100):
            raise HTTPException(400, "Năm phải là một số như 2024")
        d["year"] = int(y) if y else None
    return db.set_meta(doc_id, d, "tay", tay=True)


@app.post("/api/docs/meta/fetch-missing")
async def fetch_missing_meta():
    """Lấy bù thông tin cho mọi bài chưa có. Tuần tự, nghỉ giữa các lượt —
    Semantic Scholar từ chối khi gọi dồn."""
    xong, khong = 0, 0
    for r in db.list_docs():
        if r.get("meta"):
            continue
        d, nguon = await thongtin.tim(store.load(r["id"]))
        if d:
            db.set_meta(r["id"], d, nguon)
            xong += 1
        else:
            khong += 1
        await asyncio.sleep(1.0)
    return {"found": xong, "missing": khong}


@app.put("/api/doc/{doc_id}/tags")
async def put_tags(doc_id: str, body: dict = Body(...)):
    if not store.exists(doc_id):
        raise HTTPException(404, "Không tìm thấy tài liệu")
    tags = body.get("tags")
    if not isinstance(tags, list):
        raise HTTPException(400, "Cần danh sách nhãn")
    return {"tags": db.set_tags(doc_id, tags[:30]), "all": db.list_tags()}


@app.get("/api/tags")
async def all_tags():
    return db.list_tags()


@app.get("/api/docs/bibtex")
async def docs_bibtex(ids: str = "", folder: str = "", tag: str = ""):
    """Xuất BibTeX: theo danh sách mã, theo thư mục, theo nhãn, hoặc cả thư viện."""
    rows = db.list_docs()
    if ids:
        chon = {x for x in ids.split(",") if x.strip()}
        rows = [r for r in rows if r["id"] in chon]
    elif folder:
        rows = [r for r in rows if (r.get("folder_id") or "none") == folder]
    elif tag:
        rows = [r for r in rows if tag in (r.get("tags") or [])]
    bib = "\n".join(thongtin.bibtex(r, db.get_meta(r["id"])["data"]) for r in rows)
    return Response(bib or "% (không có bài nào)\n", media_type="application/x-bibtex",
                    headers={"Content-Disposition": 'attachment; filename="loupe.bib"'})


@app.post("/api/folders")
async def folder_create(body: dict = Body(...)):
    return db.create_folder(store.new_id(), _ten_thu_muc(body))


@app.patch("/api/folders/{folder_id}")
async def folder_rename(folder_id: str, body: dict = Body(...)):
    if not folder_id.isalnum() or not db.folder_exists(folder_id):
        raise HTTPException(404, "Không tìm thấy thư mục")
    name = _ten_thu_muc(body, bo_qua=folder_id)
    db.rename_folder(folder_id, name)
    return {"ok": True, "id": folder_id, "name": name}


@app.delete("/api/folders/{folder_id}")
async def folder_delete(folder_id: str):
    """Xoá THƯ MỤC, không xoá bài — bài bên trong về "Chưa xếp"."""
    if not folder_id.isalnum() or not db.folder_exists(folder_id):
        raise HTTPException(404, "Không tìm thấy thư mục")
    return {"ok": True, "returned": db.delete_folder(folder_id)}


@app.post("/api/docs/move")
async def docs_move(body: dict = Body(...)):
    """Chuyển nhiều bài vào một thư mục; `folder_id: null` là đưa về "Chưa xếp"."""
    ids = _ids(body)
    fid = body.get("folder_id")
    if fid is not None and (not isinstance(fid, str) or not fid.isalnum()
                            or not db.folder_exists(fid)):
        raise HTTPException(404, "Không tìm thấy thư mục")
    return {"ok": True, "moved": db.move_docs(ids, fid)}


@app.post("/api/docs/delete")
async def docs_delete(body: dict = Body(...)):
    """Xoá nhiều bài trong MỘT lượt — một hộp thoại hỏi, một request.

    Xoá từng bài một qua `DELETE /api/doc/{id}` thì giữa chừng mất mạng là thư
    viện còn một nửa, và người dùng không biết nửa nào. Ở đây bài nào không còn
    (đã xoá ở tab khác) thì bỏ qua chứ không làm hỏng cả lượt."""
    ids = _ids(body)
    xoa = 0
    for i in ids:
        if store.exists(i):
            store.delete(i)
            xoa += 1
    return {"ok": True, "deleted": xoa}


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
async def estimate(doc_id: str, mode: str = "both"):
    """Bước 1: báo cáo tiền xử lý — bóc ra được gì, sắp tốn bao nhiêu."""
    try:
        doc = store.load(doc_id)
    except KeyError:
        raise HTTPException(404, "Không tìm thấy tài liệu")
    # `mode` quyết định số cột sẽ sinh, tức gần gấp đôi chi phí đầu ra giữa
    # "chỉ dịch" và "dịch + giải thích". Không truyền vào thì ước tính bỏ sót
    # nguyên một cột.
    return await pipeline.estimate(doc, mode=mode)


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
    # Đường bóc nào đã chạy. Trước đây chỗ này rơi về heuristic **im lặng**:
    # người dùng bấm Bóc lại, thấy "xong", mà kết quả kém hẳn — công thức không
    # có ảnh nên hiện ra bằng chữ toán vỡ nát — và không có dấu hiệu nào để
    # đoán ra. Đo trên bài SONIC: 8 công thức kèm ảnh tụt còn 3 và không ảnh nào.
    # Cùng họ với mấy cái bẫy "hỏng câm" khác trong dự án này, nên phải nói ra.
    why_fallback = "" if layout.available() else "máy chưa cài/đã tắt mô hình bố cục"
    if layout.available():
        try:
            import tempfile
            with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
                f.write(data)
                tmp = f.name
            read = await loop.run_in_executor(None, layout.read, tmp)
            os.unlink(tmp)
            new_title, blocks, imgs = await loop.run_in_executor(
                None, lambda: parser.blocks_from_layout(
                    read["items"], data, regions=read["regions"]))
            better = await loop.run_in_executor(
                None, parser.apply_layout, blocks, read["regions"], data)
            if better:
                imgs.update(better)
        except Exception as e:  # noqa: BLE001 — mô hình hỏng thì rơi về heuristic
            print(f"[reparse] mô hình bố cục lỗi, dùng heuristic: {e}")
            why_fallback = f"mô hình bố cục lỗi ({type(e).__name__})"
            blocks = []
    if len(blocks) < 10:
        if not why_fallback:
            why_fallback = f"mô hình chỉ ra {len(blocks)} khối, quá ít nên không tin được"
        new_title, blocks, imgs = await loop.run_in_executor(None, parser.parse_pdf, data)

    if not blocks:
        raise HTTPException(422, "Bóc lại không ra khối nào — giữ nguyên bản cũ.")

    # Tiêu đề bóc lại được thì cũng nên sửa — nhưng CHỈ khi bản cũ là một mẩu
    # cụt của bản mới. Tiêu đề đoán từ khối đầu trang hay mất dòng đầu: bài GCR
    # lưu thành "Question Answering", đúng là đuôi của "Ground, Cover, and
    # Refine: … for Long-Video Question Answering". Bóc lại vốn sửa được chỗ đó
    # nhưng lại vứt tiêu đề mới đi, nên bài mang tên sai vĩnh viễn — mà tên sai
    # thì hỏng cả danh sách bài, bản xuất ra, slide tiêu đề, và phần tra Semantic
    # Scholar bên kho survey.
    #
    # Chỉ vá đúng ca cụt đuôi, không đụng tới tên người dùng tự đặt: có nút đổi
    # tên rồi, ghi đè lựa chọn của họ là lỗi nặng hơn hẳn cái nó sửa.
    stats = _ghep_ban_boc(doc, blocks, imgs, new_title, why_fallback, data)
    return {"doc": _with_chunks(doc), "stats": stats}


def _ghep_ban_boc(doc: dict, blocks: list, imgs: dict, new_title: str,
                  why_fallback: str, data: bytes) -> dict:
    """Ghép một bản bóc MỚI vào bài đã có, giữ bản dịch theo NỘI DUNG, rồi lưu.

    Dùng chung cho `reparse` (bóc lại chính file cũ) và cho "ghi đè" lúc nạp bài
    trùng (bóc file MỚI — arXiv v2, bản PDF sửa lại — vào bài cũ). Cả hai cùng
    một yêu cầu: đoạn nào văn bản không đổi thì giữ nguyên mã, bản dịch, ghi chú,
    vệt bôi; đoạn mới hay đã sửa thì chờ dịch. Xem `pipeline.reparse_merge`.
    """
    fixed_title = ""
    cur = (doc.get("title") or "").strip()
    nt = (new_title or "").strip()
    if nt and cur and nt != cur and len(nt) > len(cur) and cur.lower() in nt.lower():
        doc["title"] = nt
        fixed_title = nt

    doc["layout_model"] = not why_fallback
    stats = pipeline.reparse_merge(doc, [b.dict() for b in blocks])
    stats["layout_used"] = not why_fallback
    if why_fallback:
        stats["fallback_why"] = why_fallback
    if fixed_title:
        stats["title_fixed"] = fixed_title

    # Ảnh của bản bóc mới mang tên theo mã của BẢN BÓC MỚI, còn `reparse_merge`
    # vừa trả khối về mã CŨ. Để nguyên thì khối `b94` trỏ ảnh `b103`, trong khi
    # ảnh tên `b94` lại là của khối khác — và slide hỏi "hình b94" theo mã khối
    # (model thấy bài dưới dạng `<<<b94>>>`) nhận NHẦM HÌNH, hoặc khung rỗng nếu
    # tên ấy không có file. Đo trên CIRAG sau vài lần bóc lại: 31/31 khối lệch,
    # 3 slide hiện sai hình (báo cáo 9-10, S1/S2/S16). Đổi tên ảnh theo mã khối
    # là xoá hẳn chuyện hai thứ mã: mã nào model nói ra cũng chỉ còn một nghĩa.
    anh: dict[str, bytes] = {}
    for b in doc["blocks"]:
        f = b.get("figure")
        if f and imgs and f in imgs:
            anh[b["id"]] = imgs[f]
            b["figure"] = b["id"]
    store.save(doc)
    if anh:
        store.save_images(doc["id"], anh)
    # Bản bóc mới thay luôn bản trong cache, để lần sau nạp cùng file được bản tốt.
    if data:          # bài dán bằng văn bản thì không có file để khoá cache
        db.put_parse(db.sha(data), doc.get("title", ""), [b.dict() for b in blocks],
                     layout.available())
    return stats


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


@app.patch("/api/doc/{doc_id}/title")
async def set_title(doc_id: str, body: dict = Body(...)):
    """Đổi tên bài. **Không gọi model, miễn phí.**

    Tiêu đề đoán từ khối đầu trang nên hay sai — dính tên hội nghị, dính số
    trang, hoặc cụt còn vài chữ. Nó hiện ở danh sách bài, ở đầu bản xuất ra và ở
    tiêu đề slide, nên sai một chỗ là sai khắp nơi.

    Chỉ đổi nhãn, không đụng nội dung: `doc["title"]` không nằm trong
    `cached_prefix` nên không hỏng cache dịch, và không có bản dịch nào phải bỏ.
    """
    title = (body.get("title") or "").strip()
    if not title:
        raise HTTPException(400, "Tên bài không được để trống")
    try:
        doc = store.update(doc_id, title=title[:300])
    except KeyError:
        raise HTTPException(404, "Không tìm thấy tài liệu")
    return {"ok": True, "title": doc["title"]}


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


# Macro TeX → ký tự thật. **Bản sao của `TEX` bên `web/app.js`** — bản đang đọc
# trên màn hình và file xuất ra là hai đoạn code dựng cùng một nội dung, nên hai
# bảng phải khớp từng khoá. `test_bang_macro_tex_khop_nhau_giua_app_va_export`
# canh chỗ này; đừng sửa một bên.
_TEX = {
    "alpha": "α",
    "beta": "β",
    "gamma": "γ",
    "delta": "δ",
    "epsilon": "ε",
    "varepsilon": "ε",
    "zeta": "ζ",
    "eta": "η",
    "theta": "θ",
    "vartheta": "ϑ",
    "iota": "ι",
    "kappa": "κ",
    "lambda": "λ",
    "mu": "μ",
    "nu": "ν",
    "xi": "ξ",
    "pi": "π",
    "rho": "ρ",
    "sigma": "σ",
    "tau": "τ",
    "upsilon": "υ",
    "phi": "φ",
    "varphi": "φ",
    "chi": "χ",
    "psi": "ψ",
    "omega": "ω",
    "Gamma": "Γ",
    "Delta": "Δ",
    "Theta": "Θ",
    "Lambda": "Λ",
    "Xi": "Ξ",
    "Pi": "Π",
    "Sigma": "Σ",
    "Phi": "Φ",
    "Psi": "Ψ",
    "Omega": "Ω",
    "in": "∈",
    "notin": "∉",
    "ni": "∋",
    "subset": "⊂",
    "subseteq": "⊆",
    "supset": "⊃",
    "supseteq": "⊇",
    "cup": "∪",
    "cap": "∩",
    "emptyset": "∅",
    "setminus": "∖",
    "leq": "≤",
    "le": "≤",
    "geq": "≥",
    "ge": "≥",
    "neq": "≠",
    "ne": "≠",
    "approx": "≈",
    "sim": "∼",
    "simeq": "≃",
    "equiv": "≡",
    "propto": "∝",
    "ll": "≪",
    "gg": "≫",
    "to": "→",
    "rightarrow": "→",
    "Rightarrow": "⇒",
    "leftarrow": "←",
    "Leftarrow": "⇐",
    "leftrightarrow": "↔",
    "mapsto": "↦",
    "implies": "⇒",
    "iff": "⇔",
    "times": "×",
    "div": "÷",
    "cdot": "·",
    "cdots": "⋯",
    "ldots": "…",
    "dots": "…",
    "pm": "±",
    "mp": "∓",
    "ast": "∗",
    "star": "⋆",
    "circ": "∘",
    "bullet": "∙",
    "sum": "∑",
    "prod": "∏",
    "int": "∫",
    "partial": "∂",
    "nabla": "∇",
    "infty": "∞",
    "forall": "∀",
    "exists": "∃",
    "neg": "¬",
    "lnot": "¬",
    "land": "∧",
    "lor": "∨",
    "wedge": "∧",
    "vee": "∨",
    "oplus": "⊕",
    "otimes": "⊗",
    "perp": "⊥",
    "angle": "∠",
    "sqrt": "√",
    "top": "⊤",
    "bot": "⊥",
    "mid": "|",
    "parallel": "∥",
    "langle": "⟨",
    "rangle": "⟩",
    "lVert": "‖",
    "rVert": "‖",
    "quad": " ",
    "qquad": "  ",
    ",": " ",
    ";": " ",
    ":": " ",
    "!": "",
}

_TEX_ACCENT = {
    "hat": "\u0302",
    "tilde": "\u0303",
    "bar": "\u0304",
    "overline": "\u0304",
    "dot": "\u0307",
    "ddot": "\u0308",
    "vec": "\u20D7",
    "check": "\u030C",
}


def _math_tex(s: str) -> str:
    """LaTeX nội dòng → chữ thường + `<sup>`/`<sub>`. Nhận chuỗi ĐÃ escape.

    Bản sao của `mathTeX()` bên `web/app.js`, phải khớp từng luật và đúng thứ tự.
    """
    out = re.sub(r"\\(?:text|mathrm|mathit|mathbf|mathcal|mathbb|operatorname)\s*\{([^{}]*)\}",
                 r"\1", s)
    out = re.sub(r"\\frac\s*\{([^{}]*)\}\s*\{([^{}]*)\}", r"(\1)/(\2)", out)
    out = re.sub(r"\\([A-Za-z]+)\s*\{([^{}]*)\}",
                 lambda m: m.group(2) + _TEX_ACCENT[m.group(1)]
                 if m.group(1) in _TEX_ACCENT else m.group(0), out)
    out = re.sub(r"\\([{}|])", r"\1", out)
    out = re.sub(r"\\([A-Za-z]+)",
                 lambda m: _TEX.get(m.group(1), m.group(0)), out)
    out = re.sub(r"\\([,;:!])", lambda m: _TEX.get(m.group(1), " "), out)
    out = re.sub(r"\\\s", " ", out)
    # Chỉ số dạng ngoặc, LẶP từ trong ra ngoài: `[^{}]*` chỉ khớp lớp trong cùng,
    # nên `a^{(g_{DOC})}` làm một lượt thì `_{DOC}` bị ăn trước và ngoặc ngoài
    # không còn khớp — để lại nguyên dấu ngoặc giữa câu.
    for _ in range(4):
        truoc = out
        out = re.sub(r"\^\{([^{}]*)\}", r"<sup>\1</sup>", out)
        out = re.sub(r"_\{([^{}]*)\}", r"<sub>\1</sub>", out)
        if out == truoc:
            break
    out = re.sub(r"\^([A-Za-z0-9])", r"<sup>\1</sup>", out)
    out = re.sub(r"_([A-Za-z0-9])", r"<sub>\1</sub>", out)
    # Vét cuối cho chỉ số LỒNG: `a^{(g^\star)}` thì luật ngoặc ăn cả cục nên dấu
    # `^` bên trong còn nguyên. Chỉ chạy trong công thức.
    out = re.sub(r"\^([^\s{}<])", r"<sup>\1</sup>", out)
    out = re.sub(r"_([^\s{}<])", r"<sub>\1</sub>", out)
    return out.strip()


def _png_size(data: bytes) -> tuple[int, int]:
    """Bề ngang/cao của một PNG, đọc thẳng từ IHDR — không cần thư viện ảnh."""
    if len(data) < 24 or data[12:16] != b"IHDR":
        return (0, 0)
    return (int.from_bytes(data[16:20], "big"), int.from_bytes(data[20:24], "big"))


@app.post("/api/doc/{doc_id}/recrop")
async def recrop(doc_id: str):
    """Vẽ lại mọi ảnh đã cắt từ PDF gốc, theo đúng khung đã lưu. MIỄN PHÍ.

    Đây **không phải** `reparse`. Bóc lại thì dựng lại cả danh sách khối, nên nó
    kéo theo mọi rủi ro của việc đó — nhất là đường lùi heuristic khi thiếu
    docling, vốn đã làm bài SONIC tụt từ 8 công thức có ảnh xuống 3 và không ảnh
    nào. Ở đây khối **không đổi một chữ**: chỉ lấy `figure_page` + `figure_rect`
    đã lưu rồi vẽ lại pixel. Bản dịch, ghi chú, vệt bôi, `source_block_ids` của
    slide đều không bị chạm tới.

    Vì sao cần: `parser.dpi_for` nhắm 1600px ngang, nhưng bài nạp trước bản đó
    cắt ở DPI cứng nên khung hẹp chỉ ra vài trăm pixel — phóng lên là mờ nhoè,
    mà đọc được con số trên biểu đồ mới đúng là lý do người ta phóng. `parse_cache`
    khoá theo SHA của file PDF nên nạp lại cùng file **không** cắt lại; trước bản
    này không có đường nào chữa ngoài bóc lại cả bài.

    Nó cũng vá luôn ca ảnh **mất hẳn file**: mã khối trôi qua các lần bóc lại thì
    PNG trên đĩa còn tên cũ, khối mới trỏ vào file không tồn tại và người đọc
    thấy một ô trống. Vẽ lại theo khung đã lưu là làm file khớp lại với khối.
    """
    try:
        doc = store.load(doc_id)
    except KeyError:
        raise HTTPException(404, "Không tìm thấy tài liệu")
    p = store.pdf_path(doc_id)
    if p is None:
        raise HTTPException(400, "Bài này không có file PDF gốc, không cắt lại được")

    khung = [b for b in doc["blocks"]
             if b.get("figure") and b.get("figure_rect") and b.get("figure_page") is not None]
    if not khung:
        raise HTTPException(400, "Bài này không có ảnh nào cắt từ PDF")

    def work() -> tuple[dict[str, bytes], list[dict], list[str]]:
        import fitz

        pngs: dict[str, bytes] = {}
        do: list[dict] = []
        loi: list[str] = []
        with fitz.open(p) as d:
            for b in khung:
                try:
                    page = d[int(b["figure_page"])]
                    r = fitz.Rect(*b["figure_rect"]) & page.rect
                    if r.is_empty or r.width < 4 or r.height < 4:
                        loi.append(b["id"])
                        continue
                    png = parser.render_rect(page, r)
                except (IndexError, ValueError, TypeError):
                    loi.append(b["id"])
                    continue
                cu = store.image_path(doc_id, b["figure"])
                truoc = _png_size(cu.read_bytes()) if cu else (0, 0)
                pngs[b["id"]] = png
                do.append({"id": b["id"], "truoc": truoc[0], "sau": _png_size(png)[0]})
        return pngs, do, loi

    try:
        pngs, do, loi = await asyncio.get_running_loop().run_in_executor(None, work)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(400, f"Không cắt lại được: {e}")

    store.save_images(doc_id, pngs)
    # Mã khối có thể đã trôi so với tên file: gắn lại cho khớp.
    for b in khung:
        if b["id"] in pngs:
            b["figure"] = b["id"]
    store.save(doc)

    thieu = [d["id"] for d in do if not d["truoc"]]
    ro_hon = [d for d in do if d["truoc"] and d["sau"] > d["truoc"] * 1.2]
    return {
        "doc": _with_chunks(doc),
        "stats": {
            "images": len(pngs),
            "sharper": len(ro_hon),
            "restored": len(thieu),
            "failed": loi,
            "px_before": round(sum(d["truoc"] for d in do if d["truoc"])
                               / max(1, len([d for d in do if d["truoc"]]))),
            "px_after": round(sum(d["sau"] for d in do) / max(1, len(do))),
        },
    }


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
            # `dpi=None` = nhắm pixel (xem `parser.dpi_for`): khung người dùng
            # tự cắt cũng sẽ được phóng to xem, nên phải nét như khung tự động.
            dpi = body.get("dpi")
            return parser.render_rect(page, r, dpi=int(dpi) if dpi else None)

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


_ARXIV_ID = re.compile(r"(?<![\d.])(\d{4}\.\d{4,5})(?:v\d+)?(?![\d])")


def _chuan_ten(t: str) -> str:
    """Tiêu đề rút gọn để so: chữ thường, bỏ dấu câu, gộp khoảng trắng."""
    return " ".join(re.sub(r"[^\w\s]", " ", (t or "").lower()).split())


def _khoa_van_ban(raw: str) -> bytes:
    """Khoá nhận dạng của một bài NHẬP BẰNG VĂN BẢN (dán hoặc file .txt/.md).

    Chuẩn hoá khoảng trắng và kiểu xuống dòng trước khi băm: dán lại cùng bài từ
    một trình soạn thảo khác thì khoảng trắng cuối dòng, `\r\n` so với `\n`, dòng
    trống thừa đều khác — mà vẫn là cùng một bài. Có tiền tố `txt:` để không bao
    giờ trùng khoá với SHA của một file PDF.
    """
    return b"txt:" + " ".join((raw or "").split()).encode("utf-8")


def _tom_tat_trung(doc_id: str, kind: str) -> dict:
    """Những gì hộp thoại "bài trùng" cần để người dùng chọn có căn cứ: đã dịch
    bao nhiêu, đã tốn bao nhiêu, và họ phiên bản của nó gồm những bản nào."""
    rows = db.list_docs()
    r = next(x for x in rows if x["id"] == doc_id)
    goc_id = r.get("version_of")
    ho = sorted(((x["version"], x["id"]) for x in rows
                 if goc_id and x.get("version_of") == goc_id), key=lambda v: v[0])
    return {
        "kind": kind,                      # cung_file | cung_bai
        "id": r["id"],
        "title": r["title_vi"] or r["title"],
        "blocks": r["blocks"], "translated": r["translated"],
        "translatable": r["translatable"], "cost_usd": r["cost_usd"],
        "version": r.get("version"),
        "versions": [v for v, _ in ho],
    }


def _cung_bai(title: str, source: str) -> dict | None:
    """Cùng MỘT BÀI nhưng khác file — arXiv v1 với v2, hay bản PDF sửa lại.

    Phép dò theo SHA chỉ bắt được đúng một file; bản v2 của bài khác SHA nên lọt
    qua và thành một bài riêng không liên quan gì tới bản dịch v1 đã trả tiền.
    Hai dấu hiệu, dấu hiệu đầu đáng tin hơn:

    - **mã arXiv gốc** (bỏ hậu tố `v2`) trong nguồn — `arXiv:2604.00965` hay tên
      file `2604.00965v1.pdf`;
    - **tiêu đề** trùng sau khi chuẩn hoá, và đủ dài (≥25 ký tự) để không khớp
      nhầm mấy tiêu đề rác kiểu "(không tiêu đề)" hay "Introduction".

    Lấy bài HOẠT ĐỘNG GẦN NHẤT trong số khớp — thường là phiên bản mới nhất.
    Khớp nhầm thì người dùng vẫn có lối "Bài riêng", nên ở đây rộng tay được.
    """
    rows = db.list_docs()                  # đã xếp theo updated_at giảm dần
    m = _ARXIV_ID.search(source or "")
    if m:
        for r in rows:
            m2 = _ARXIV_ID.search(r.get("source") or "")
            if m2 and m2.group(1) == m.group(1):
                return _tom_tat_trung(r["id"], "cung_bai")
    t = _chuan_ten(title)
    if len(t) >= 25:
        for r in rows:
            if _chuan_ten(r.get("title")) == t:
                return _tom_tat_trung(r["id"], "cung_bai")
    return None


@app.post("/api/import")
async def import_doc(
    file: UploadFile | None = File(None),
    text: str = Form(""),
    url: str = Form(""),
    title: str = Form(""),
    model: str = Form(""),
    use_layout: int = Form(1),
    force: int = Form(0),
    che_do: str = Form(""),
    goc: str = Form(""),
    job: str = Form(""),
):
    """Nạp một bài. Gặp bài TRÙNG thì dừng lại hỏi, qua `che_do`:

    - `""`        — dò trùng (mặc định). Trùng thì trả `{"duplicate": …}`.
    - `"moi"`     — nạp thành bài riêng, bỏ qua phép dò (`force=1` cũ).
    - `"phien_ban"` — nạp thành bài mới, gắn làm phiên bản kế tiếp của `goc`.
    - `"ghi_de"`  — bóc file mới VÀO bài `goc`: đoạn không đổi giữ bản dịch.
    """
    model = model or llm.DEFAULT_MODEL
    loop = asyncio.get_running_loop()
    _say(job, "Bắt đầu", "", 2)
    if force and not che_do:
        che_do = "moi"
    if che_do not in ("", "moi", "phien_ban", "ghi_de"):
        raise HTTPException(400, "Chế độ nạp không hợp lệ")
    if che_do in ("phien_ban", "ghi_de"):
        if not goc.isalnum() or not store.exists(goc):
            raise HTTPException(404, "Không tìm thấy bài gốc để ghi đè hay gắn phiên bản")
    do_trung = che_do == ""

    def _da_co(data: bytes) -> dict | None:
        """Bài này đã nằm trong thư viện chưa? Khoá theo SHA của chính file PDF.

        Nạp trùng không hỏng gì về kỹ thuật — `parse_cache` làm bước bóc gần như
        miễn phí — nhưng nó đẻ ra một bản thứ hai **trống rỗng** cạnh bản đã
        dịch, và người dùng mở nhầm bản mới thì tưởng mất sạch bản dịch đã trả
        tiền. Đo trên `data/` thật: có bài nằm ba bản.

        Hỏi TRƯỚC khi bóc, không phải sau: bóc xong mới hỏi thì đã chạy mô hình
        bố cục (6–196 giây) cho một thứ người dùng sắp bỏ đi.
        """
        if not do_trung:
            return None
        prior = db.doc_by_sha(db.sha(data))
        if not prior:
            return None
        return _tom_tat_trung(prior["id"], "cung_file")

    pdf_bytes: bytes | None = None
    khoa_txt: bytes | None = None   # khoá nhận dạng của bài nhập bằng văn bản
    kieu_nguon = "pdf"          # pdf | text — quyết định câu báo lỗi ở dưới
    try:
        if file is not None:
            data = await file.read()
            # Kiểm NGAY, trước khi bóc và trước khi tạo bất cứ bản ghi nào. Đuôi
            # file không đủ: `paper.docx` từng lọt qua và thành một khối 9.800 ký
            # tự byte rác, có báo giá dịch và được lưu vĩnh viễn vào kho.
            loai = parser.kiem_nguon(data, file.filename or "")
            if loai == "pdf":
                if (cu := _da_co(data)):
                    _say(job, "Bài này đã có trong thư viện", cu["title"], 100)
                    if (q := _JOBS.get(job or "")) is not None:
                        q.put_nowait(None)
                    return {"duplicate": cu}
                pdf_bytes = data
                _say(job, "Bóc chữ từ PDF", f"{len(data)//1024} KB", 15)
                t, blocks, imgs = await loop.run_in_executor(None, parser.parse_pdf, data)
                source = file.filename or "upload.pdf"
            else:
                kieu_nguon = "text"
                raw_txt = data.decode("utf-8", "replace")
                khoa_txt = _khoa_van_ban(raw_txt)
                if (cu := _da_co(khoa_txt)):
                    _say(job, "Bài này đã có trong thư viện", cu["title"], 100)
                    if (q := _JOBS.get(job or "")) is not None:
                        q.put_nowait(None)
                    return {"duplicate": cu}
                t, blocks, imgs = parser.parse_text(raw_txt, name=file.filename or "")
                source = file.filename or "upload.txt"
        elif url.strip():
            u = url.strip()
            if "arxiv.org" in u or parser._ARXIV.search(u):
                _say(job, "Tải bài từ arXiv", u, 6)
                aid, data = await parser.fetch_arxiv(u)
                source = f"arXiv:{aid}"
            elif u.lower().startswith(("http://", "https://")):
                _say(job, "Tải PDF về", u, 6)
                data = await parser.fetch_pdf_url(u)
                source = u
            else:
                # Chuỗi không phải link cũng không phải mã arXiv. Trước đây nó
                # chạy thẳng vào `fetch_pdf_url` rồi nổ thành 500.
                raise parser.NguonHong(
                    "Không nhận ra mã arXiv hay link PDF. Ví dụ hợp lệ: "
                    "1706.03762 · arXiv:1706.03762v7 · https://…/paper.pdf")
            if (cu := _da_co(data)):
                _say(job, "Bài này đã có trong thư viện", cu["title"], 100)
                if (q := _JOBS.get(job or "")) is not None:
                    q.put_nowait(None)
                return {"duplicate": cu}
            pdf_bytes = data
            _say(job, "Bóc chữ từ PDF", f"{len(data)//1024} KB", 15)
            t, blocks, imgs = await loop.run_in_executor(None, parser.parse_pdf, data)
        elif text.strip():
            kieu_nguon = "text"
            khoa_txt = _khoa_van_ban(text)
            if (cu := _da_co(khoa_txt)):
                _say(job, "Bài này đã có trong thư viện", cu["title"], 100)
                if (q := _JOBS.get(job or "")) is not None:
                    q.put_nowait(None)
                return {"duplicate": cu}
            t, blocks, imgs = parser.parse_text(text, name="dán")
            source = "dán trực tiếp"
        else:
            raise HTTPException(400, "Cần một trong: file PDF, đường dẫn, hoặc văn bản dán vào")
    except parser.NguonHong as e:
        _say(job, "Lỗi", str(e), None)
        if (q := _JOBS.get(job or "")) is not None:
            q.put_nowait(None)
        raise HTTPException(400, str(e))

    # Cùng bài, khác file. Dò ở đây — sau bước bóc nhanh (vài giây, đã có tiêu
    # đề), TRƯỚC mô hình bố cục (6–196 giây) — vì cần tiêu đề mới dò được, mà
    # chạy mô hình cho một thứ người dùng có thể bỏ đi là phí.
    if do_trung and blocks:
        cu_bai = _cung_bai(title or t, source)
        if cu_bai:
            _say(job, "Có vẻ bài này đã có trong thư viện", cu_bai["title"], 100)
            if (q := _JOBS.get(job or "")) is not None:
                q.put_nowait(None)
            return {"duplicate": cu_bai}

    if not blocks:
        _say(job, "Lỗi", "không trích được nội dung", None)
        if (q := _JOBS.get(job or "")) is not None:
            q.put_nowait(None)
        # Câu báo phải theo ĐÚNG loại nguồn. Tải `empty.txt` lên mà bị khuyên "cần
        # OCR" thì lời khuyên vô nghĩa và người dùng mất thì giờ đi tìm OCR.
        raise HTTPException(422, "Không trích được nội dung. PDF có thể là bản scan "
                                 "ảnh — cần OCR trước."
                            if kieu_nguon == "pdf" else
                            "Không trích được nội dung — nguồn không có chữ nào đọc được.")

    # Cùng một file PDF thì cấu trúc bóc ra và khung hình chắc chắn giống hệt.
    # Chạy lại PyMuPDF và mô hình bố cục chỉ tốn thời gian chứ không đổi kết quả.
    layout_used = False
    reused_from = None
    if pdf_bytes:
        file_sha = db.sha(pdf_bytes)
        # Ghi đè thì KHÔNG lấy cache: ghi đè bằng chính file cũ nghĩa là "bóc lại
        # bằng bộ bóc mới nhất", mà cache khoá theo SHA giữ đúng bản bóc cũ — cùng
        # lý do `reparse` bỏ qua nó.
        cached = db.get_parse(file_sha) if che_do != "ghi_de" else None
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
            # Rơi về đường lùi phải NÓI RA, không chỉ ghi log. `reparse` đã báo
            # từ lâu (`layout_used` / `fallback_why`) nhưng đường NẠP BÀI thì
            # chưa, nên người dùng nạp xong thấy "xong" mà công thức mất sạch và
            # dấu hiệu duy nhất nằm trong log server.
            #
            # Đã vấp đúng vậy ba lần liên tiếp khi dựng ảnh Docker kèm MinerU:
            # thiếu `libxcb.so.1`, rồi thiếu `six`, rồi `parse_cache` trả lại
            # bản heuristic cũ. Mỗi lần đều "thành công" trong 1–2 giây.
            print(f"[layout] bỏ qua, dùng heuristic: {type(e).__name__}: {e}")
            _say(job, "⚠ Không dùng được mô hình bố cục",
                 f"{type(e).__name__}: {e} — bóc bằng đường lùi, "
                 "công thức sẽ không được cắt thành ảnh")

    # Chỉ báo khi CÓ PDF mà mô hình không chạy (#21): văn bản dán / Markdown vốn
    # không qua mô hình bố cục, báo "đường lùi" ở đó là doạ người dùng vô cớ.
    if pdf_bytes and use_layout and not layout_used and layout.available():
        _say(job, "⚠ Bóc bằng đường lùi", "mô hình bố cục không chạy được")

    _say(job, "Cắt hình và bảng", f"{len(imgs)} ảnh", 85)
    if che_do == "ghi_de":
        # Bóc file mới VÀO bài cũ. Ghép theo NỘI DUNG (`reparse_merge`): đoạn
        # không đổi giữ mã, bản dịch, ghi chú, vệt bôi; đoạn mới chờ dịch. Giữ
        # tên bài người dùng đã đặt; file PDF gốc thay bằng file mới để lần "Bóc
        # lại" sau đọc đúng bản này.
        cu = store.load(goc)
        _say(job, "Ghép vào bài cũ", "đoạn không đổi giữ nguyên bản dịch", 92)
        if pdf_bytes:
            store.save_pdf(goc, pdf_bytes)
            cu["sha256"] = file_sha
        elif khoa_txt:
            cu["sha256"] = db.sha(khoa_txt)
        stats = _ghep_ban_boc(cu, blocks, imgs, t,
                              "" if layout_used else "không dùng mô hình bố cục",
                              pdf_bytes or b"")
        _say(job, "Xong", f"giữ {stats.get('kept', 0)} đoạn · {stats.get('new', 0)} đoạn mới", 100)
        await asyncio.sleep(0.05)
        if (q := _JOBS.get(job or "")) is not None:
            q.put_nowait(None)
        return {**_with_chunks(cu), "ghi_de": stats}
    doc = pipeline.build_doc(store.new_id(), title or t, blocks, source, model)
    doc["layout_model"] = layout_used
    if khoa_txt:
        doc["sha256"] = db.sha(khoa_txt)
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
    if che_do == "phien_ban":
        doc["version"] = db.link_version(doc["id"], goc)
    # Thư viện kiểu Zotero: tự lấy tác giả / năm / nơi đăng — NGẦM, không bắt
    # người dùng chờ mạng ngoài. Hỏng thì thôi, nút "Lấy thông tin" lấy bù được.
    _lay_thong_tin_ngam(doc["id"])
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
    except pipeline.HetGio as e:
        raise HTTPException(504, f"Quá giờ khi dựng tóm lược — {e}. Chưa bị tính "
                                 "tiền cho phần chưa sinh ra; thử lại thường được, "
                                 "hoặc đổi sang model khác.")
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


@app.post("/api/doc/{doc_id}/retranslate/{block_id}")
async def retranslate(doc_id: str, block_id: str, body: dict = Body(default={})):
    """Dịch lại một khối. Tốn một lượt gọi model nhỏ.

    Có endpoint riêng vì cảnh báo rò hệ chữ của `stream_chunk` đã bảo người dùng
    *"dịch lại khối đó"* mà không có đường nào làm việc ấy — chỉ còn cách gõ tay
    cả đoạn hoặc dịch lại cả mẻ.
    """
    mode = body.get("mode") or "vi"
    if mode not in ("vi", "plain", "both"):
        raise HTTPException(400, "mode phải là vi, plain hoặc both")
    try:
        return await pipeline.retranslate_block(doc_id, block_id, mode)
    except KeyError:
        raise HTTPException(404, "Không tìm thấy đoạn này")
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"{type(e).__name__}: {e}")


@app.post("/api/doc/{doc_id}/insights")
async def insights(doc_id: str, level: str = ""):
    """Chọn câu đáng nhớ trong bản dịch. Tốn một lượt gọi model.

    Trả về **ứng viên**, không tự ghi vệt bôi: chỉ tầng hiển thị mới neo được
    (xem `pipeline.mark_insights`). Client dò `quote` trong ô đã dựng rồi gọi
    `PATCH …/highlights` với `add_many`.
    """
    try:
        if level and level not in prompts.INSIGHT_LEVELS:
            raise HTTPException(400, "Mật độ phải là thua, vua hoặc day")
        return await pipeline.mark_insights(doc_id, level)
    except KeyError:
        raise HTTPException(404, "Không tìm thấy tài liệu")
    except ValueError as e:
        raise HTTPException(400, str(e))
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

    # Ghi NHIỀU vệt trong một lượt. Đường `add` một-vệt-một-request là đúng cho
    # người dùng tự bôi, nhưng pass đánh dấu tự động trả về tới 18 vệt — gọi 18
    # lần là 18 lượt `store.save(doc)` ghi lại nguyên cả tài liệu.
    if (many := body.get("add_many")) is not None:
        ids = {b["id"] for b in doc["blocks"]}
        # Bấm nút đánh dấu lần hai thì phải THAY chỗ cũ, không cộng dồn — đã thấy
        # đúng câu đầu bài hiện ra hai lần. Nhưng chỉ dọn vệt do MÁY đặt (`auto`):
        # vệt người dùng tự tô là công sức của họ, cùng lý do `mark_stale` chỉ gắn
        # cờ chứ không xoá slide đã sửa tay.
        if body.get("replace_auto"):
            for bid in list(hl):
                hl[bid] = [h for h in hl[bid] if not h.get("auto")]
                if not hl[bid]:
                    del hl[bid]
        used = {h["id"] for h in _hl_all(doc)}
        n, them = 1, []
        for a in (many if isinstance(many, list) else [])[:100]:
            bid = str(a.get("block") or "")
            start, end = int(a.get("start", 0)), int(a.get("end", 0))
            if bid not in ids or end <= start:
                continue
            while f"h{n}" in used:
                n += 1
            used.add(f"h{n}")
            item = {"id": f"h{n}",
                    "col": a.get("col") if a.get("col") in ("en", "vi", "gl") else "vi",
                    "color": a.get("color") if a.get("color") in HL_COLORS else "y",
                    "start": start, "end": end,
                    "text": (a.get("text") or "")[:2000],
                    "note": (a.get("note") or "")[:4000],
                    "auto": bool(a.get("auto")),
                    "created_at": time.time()}
            hl.setdefault(bid, []).append(item)
            them.append(item)
        for bid in hl:
            hl[bid].sort(key=lambda h: (h["col"], h["start"]))
        doc["highlights"] = hl
        store.save(doc)
        return {"highlights": hl, "added": them}

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


# -------------------------------------------------------------- slide (v2)
#
# Dựng lại từ đầu — xem `server/slide.py`. Server chỉ lo DỮ LIỆU; vẽ slide là
# việc của một hàm JS duy nhất (`web/slide-ve.js`) dùng chung cho xem trước,
# trình chiếu và file tải về.


def _slides_ra(doc: dict) -> dict:
    import copy
    s = slide.lay(doc)
    return {**s, "bo": slide.kem_anh(doc, copy.deepcopy(s["bo"]))}


def _doc_slide(doc_id: str) -> dict:
    try:
        return store.load(doc_id)
    except KeyError:
        raise HTTPException(404, "Không tìm thấy tài liệu")


@app.get("/api/doc/{doc_id}/slides")
async def get_slides(doc_id: str):
    return _slides_ra(_doc_slide(doc_id))


@app.get("/api/doc/{doc_id}/slides/gia")
async def slides_gia(doc_id: str, phut: int = 15):
    """Ước giá MỘT lượt sinh bộ slide. Trả dải, không một con số (cùng lối với
    ước giá dịch): đầu vào là cả prefix toàn văn. Đầu ra ~700 token mỗi slide —
    đo trên CIRAG: 9.539 token cho 12 slide, vì `loi_noi` 60–110 chữ cộng phần
    nghĩ thầm mức thấp. Bản đầu ước 260 và báo trần $0,036 cho lượt tốn $0,047."""
    doc = _doc_slide(doc_id)
    so = slide.SO_SLIDE.get(phut, 14)
    vao = len(pipeline.cached_prefix(doc)) / 3.6 + 2500
    ra = so * 700 + 1500
    if so > slide.CHI_TIET_TU:
        # Bộ chi tiết: một lượt lên khung + một lượt viết mỗi 8 slide, mỗi lượt đọc
        # lại prefix (phần lớn từ cache, tính ~30% giá). Trích đoạn rẻ hơn slide
        # thường vì chữ chép từ bản dịch, model chỉ viết ý chính + lời nói.
        me = -(-so // slide.ME_VIET)
        vao = vao * (1 + 0.3 * me) + 3000 * me
        ra = so * 700 + 2000   # đo: CIRAG 45 phút ra ~30k token cho 49 slide
    try:
        gia = next(((float((m.get("pricing") or {}).get("prompt") or 0),
                     float((m.get("pricing") or {}).get("completion") or 0))
                    for m in await llm.list_models() if m.get("id") == doc["model"]), None)
    except Exception:  # noqa: BLE001
        gia = None
    if not gia:
        return {"lo": None, "hi": None, "model": doc["model"]}
    c = vao * gia[0] + ra * gia[1]
    # Bộ chi tiết dao động mạnh hơn (số lượt viết tuỳ số slide sau khi tách).
    hi = 1.5 if so > slide.CHI_TIET_TU else 1.3
    return {"lo": round(c * 0.6, 4), "hi": round(c * hi, 4), "model": doc["model"]}


@app.post("/api/doc/{doc_id}/slides/tao")
async def slides_tao(doc_id: str, body: dict = Body(default={})):
    """Sinh cả bộ slide bằng MỘT lượt gọi model. Ghi đè bộ cũ."""
    doc = _doc_slide(doc_id)
    if not doc.get("brief"):
        raise HTTPException(400, "Bài chưa có tóm lược — bấm Dịch trước để tool đọc toàn bài.")
    try:
        _, run, total = await slide.tao(doc_id, int(body.get("phut") or 15))
    except pipeline.HetGio as e:
        raise HTTPException(504, f"Quá giờ khi soạn slide — {e}. Thử lại, hoặc đổi model.")
    except ValueError as e:
        raise HTTPException(502, str(e))
    return {"slides": _slides_ra(store.load(doc_id)), "run": run, "total": total}


@app.patch("/api/doc/{doc_id}/slides/{sid}")
async def slides_sua(doc_id: str, sid: str, body: dict = Body(...)):
    """Sửa tay một slide (chữ, hình, lời nói). Miễn phí."""
    doc = _doc_slide(doc_id)
    try:
        slide.sua(doc, sid, body)
    except KeyError:
        raise HTTPException(404, "Không có slide này")
    except ValueError as e:
        raise HTTPException(400, str(e))
    store.save(doc)
    return _slides_ra(doc)


@app.post("/api/doc/{doc_id}/slides/thu-tu")
async def slides_thu_tu(doc_id: str, body: dict = Body(...)):
    """Đổi thứ tự slide. Danh sách phải đúng là hoán vị của bộ hiện có."""
    doc = _doc_slide(doc_id)
    s = slide.lay(doc)
    ids = [str(x) for x in body.get("ids") or []]
    by = {x["id"]: x for x in s["bo"]}
    if sorted(ids) != sorted(by):
        raise HTTPException(400, "Danh sách slide không khớp bộ hiện có")
    s["bo"] = [by[i] for i in ids]
    doc["slides"] = s
    store.save(doc)
    return _slides_ra(doc)


@app.delete("/api/doc/{doc_id}/slides/{sid}")
async def slides_xoa(doc_id: str, sid: str):
    doc = _doc_slide(doc_id)
    s = slide.lay(doc)
    if not any(x["id"] == sid for x in s["bo"]):
        raise HTTPException(404, "Không có slide này")
    s["bo"] = [x for x in s["bo"] if x["id"] != sid]
    doc["slides"] = s
    store.save(doc)
    return _slides_ra(doc)


@app.post("/api/doc/{doc_id}/slides/{sid}/viet-lai")
async def slides_viet_lai(doc_id: str, sid: str, body: dict = Body(default={})):
    """Viết lại một slide, giữ vai. Rẻ: prefix toàn văn đi qua cache."""
    _doc_slide(doc_id)
    try:
        _, run, total = await slide.viet_lai(doc_id, sid, str(body.get("goi_y") or "")[:500])
    except KeyError:
        raise HTTPException(404, "Không viết lại được slide này")
    except ValueError as e:
        raise HTTPException(502, str(e))
    return {"slides": _slides_ra(store.load(doc_id)), "run": run, "total": total}



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

    if fmt in ("slides", "slides-pdf"):
        return _xuat_slide(doc, in_ra=fmt == "slides-pdf")
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


# Bản xuất bài dịch — cùng chất liệu "Sổ tay khoa học" với trang web: giấy ngà kẻ
# ô ở lề, nét mực, vệt dạ quang, giấy nhớ dán băng keo. Font đóng gói nhúng thẳng
# (`_font_nhung`) nên mở file ngoài app vẫn đúng chữ.
#
# Ba thứ cố ý KHÔNG có, vì là "dấu hiệu đồ AI làm" đã ghi trong CLAUDE.md: vạch
# màu kẻ dọc cạnh ô chữ (bản cũ có ở bốn chỗ: câu chốt, công thức, ghi chú, giải
# thích), nhãn viết hoa toàn bộ, nền kem kèm hoa văn serif nghiêng.
#
# `.pair.is-cont` / `.pair.in-flow` phải khớp `web/style.css` — bản xem trong app
# và file xuất ra là hai đoạn code dựng cùng một markup.
_EXPORT_CSS = """
:root{--giay:#fffcf3;--o-ly:rgba(39,83,192,.06);--muc:#1d1b18;--muc-2:#3b362d;--chi:#cdc2a6;
--mo:#675e4f;--xanh:#2753c0;--do:#c8322c;--da-quang:#ffd43b;--nho:#fff4c2;--bang-keo:rgba(214,196,150,.7);
--td:"Baloo 2","Be Vietnam Pro",system-ui,sans-serif;--than:"Be Vietnam Pro",system-ui,sans-serif;
--tay:"Patrick Hand","Be Vietnam Pro",sans-serif;--doc:"Literata",Georgia,serif;color-scheme:light}
*{box-sizing:border-box}
html{background:#efe9da}
body{margin:0;padding:2.4rem 1rem 6rem;color:var(--muc);font:16px/1.7 var(--doc);
background-color:#efe9da;background-image:linear-gradient(var(--o-ly) 1px,transparent 1px),
linear-gradient(90deg,var(--o-ly) 1px,transparent 1px);background-size:22px 22px}
main{max-width:1120px;margin:0 auto;background:var(--giay);border:2px solid var(--muc);
border-radius:6px;box-shadow:6px 6px 0 var(--muc);padding:3rem 3.2rem 4rem;position:relative}
main::before{content:"";position:absolute;top:0;bottom:0;left:2rem;border-left:1.5px solid rgba(200,50,44,.35)}
p{margin:0}
.tay{font-family:var(--tay);color:var(--xanh);font-size:1.08rem}
a{color:var(--xanh)}
/* ---- bìa */
.bia{margin:0 0 2.6rem}
.bia h1{font-family:var(--td);font-weight:800;font-size:2.15rem;line-height:1.25;margin:.3rem 0 .6rem;
text-decoration:underline;text-decoration-color:rgba(255,212,59,.85);text-decoration-thickness:.32em;
text-underline-offset:-.12em;text-decoration-skip-ink:none}
.goc{font-family:var(--than);color:var(--mo);font-size:1rem;line-height:1.5}
.tg{font-family:var(--than);font-weight:600;margin-top:.9rem}
.nd{font-family:var(--than);color:var(--muc-2);font-size:.95rem}
.meta{font-family:var(--tay);color:var(--mo);font-size:1rem;margin-top:1rem;padding-top:.7rem;
border-top:1.5px dashed var(--chi)}
/* ---- tiêu đề phần */
h2{font-family:var(--td);font-weight:800;font-size:1.55rem;line-height:1.3;margin:2.8rem 0 1rem}
h2 .so{display:inline-grid;place-items:center;width:2.1rem;height:2.1rem;border-radius:50%;
background:var(--da-quang);border:2px solid var(--muc);box-shadow:2px 2px 0 var(--muc);
font-size:1.05rem;margin-right:.6rem;vertical-align:.12em}
h3{font-family:var(--td);font-weight:700;font-size:1.15rem;margin:1.8rem 0 .6rem}
/* ---- tóm lược */
.chot{font-size:1.2rem;line-height:1.65;font-weight:600;margin:0 0 1.6rem}
.chot span{background:linear-gradient(transparent 58%,rgba(255,212,59,.75) 58%);
-webkit-box-decoration-break:clone;box-decoration-break:clone}
.mach{list-style:none;margin:0;padding:0 0 0 .2rem;display:grid;gap:1rem;position:relative}
.mach::before{content:"";position:absolute;left:1.05rem;top:.6rem;bottom:.6rem;border-left:2px dashed var(--chi)}
.mach li{display:grid;grid-template-columns:2.2rem minmax(0,1fr);gap:.9rem;position:relative}
.mach .n{width:2.2rem;height:2.2rem;display:grid;place-items:center;border-radius:50%;background:var(--giay);
border:2px solid var(--muc);font-family:var(--td);font-weight:800;position:relative;z-index:1}
.mach b{font-family:var(--td);font-size:1.05rem;display:block;line-height:1.4;margin-top:.2rem}
.mach p{color:var(--muc-2)}
.so-do{margin:1.6rem 0;background:#fff;border:2px solid var(--muc);border-radius:6px;
box-shadow:4px 4px 0 var(--muc);padding:1.4rem 1rem 1rem;position:relative;text-align:center}
.so-do .keo,figure .keo{position:absolute;top:-.7rem;left:42%;width:5rem;height:1.3rem;background:var(--bang-keo);transform:rotate(-3deg)}
.so-do figcaption{font-family:var(--tay);color:var(--mo);margin-top:.6rem}
table{border-collapse:collapse;width:100%;font-family:var(--than);font-size:.92rem}
th,td{border-bottom:1.5px dashed var(--chi);padding:.5rem .6rem;text-align:left;vertical-align:top}
th{font-family:var(--td);font-weight:700;border-bottom:2px solid var(--muc)}
td:first-child{font-weight:600}
.giu{font-family:var(--tay);color:var(--mo);white-space:nowrap}
table.tn th:nth-child(1){width:32%}table.tn th:nth-child(2){width:20%}
/* ---- mục lục */
.muc-luc{columns:2 18rem;column-gap:2.4rem;margin:0;padding:0;list-style:none;font-family:var(--than);font-size:.95rem}
.muc-luc li{break-inside:avoid;padding:.25rem 0;border-bottom:1px dotted var(--chi)}
.muc-luc li.c2{padding-left:1.2rem;font-size:.9rem}
.muc-luc li.c3{padding-left:2.4rem;font-size:.88rem;color:var(--muc-2)}
.muc-luc a{color:inherit;text-decoration:none}
.muc-luc a:hover{color:var(--xanh)}
/* ---- thân bài: cột gốc = bản in để đối chiếu, cột dịch = chữ để đọc */
.cot{display:grid;grid-template-columns:minmax(0,.86fr) minmax(0,1.14fr);gap:0 2.4rem;
font-family:var(--tay);color:var(--mo);font-size:1rem;margin:0 0 .8rem;padding-bottom:.4rem;
border-bottom:2px solid var(--muc)}
.one .cot{display:none}
.pair{display:grid;grid-template-columns:minmax(0,.86fr) minmax(0,1.14fr);gap:0 2.4rem;margin:0 0 1.15rem;position:relative}
.pair:has(>.en){background:repeating-linear-gradient(var(--chi) 0 6px,transparent 6px 11px) no-repeat
calc((100% - 2.4rem)*.43 + 1.2rem) 0/1.5px 100%}
.pair.is-cont,.pair.in-flow{margin-top:0;margin-bottom:.25rem}
.pair.is-cont .pending{display:none}
.one .pair{grid-template-columns:minmax(0,1fr)}
.en{font-family:var(--than);color:var(--mo);font-size:.88rem;line-height:1.65}
.vi{font-size:1.02rem}
.hd{grid-column:1/-1;font-family:var(--td);font-weight:800;font-size:1.3rem;line-height:1.35;margin:2rem 0 .3rem;scroll-margin-top:1rem}
.hd.c2{font-size:1.12rem;font-weight:700;margin-top:1.4rem}
.hd.c3{font-size:1.02rem;font-weight:700;margin-top:1.1rem}
.hd .goc-h{display:block;font-family:var(--than);font-weight:400;font-size:.8rem;color:var(--mo)}
.eqbox{grid-column:1/-1;text-align:center;background:#fff;border:1.5px solid var(--muc);border-radius:6px;
padding:.7rem .9rem;margin:.5rem 0}
.eqbox img{max-width:100%;height:auto}
.eq{font-family:var(--than);overflow-x:auto}
figure{grid-column:1/-1;margin:1rem 0 .9rem;text-align:center;background:#fff;border:2px solid var(--muc);
border-radius:6px;box-shadow:4px 4px 0 var(--muc);padding:1rem .8rem .7rem;position:relative}
figure img{max-width:100%;height:auto}
.imath{font-style:normal}
.imath sub,.imath sup{font-style:normal}
/* giải thích: giấy nhớ dán băng keo, chữ sans — khác hẳn cột dịch */
.gl{grid-column:1/-1;position:relative;background:var(--nho);border:1.5px solid var(--muc);
border-radius:3px 3px 14px 3px;box-shadow:3px 3px 0 var(--muc);padding:1.1rem 1.1rem .8rem;
margin:.7rem 0 .3rem;font-family:var(--than);font-size:.94rem;line-height:1.65;color:var(--muc-2)}
.gl::before{content:"";position:absolute;top:-.6rem;left:1.4rem;width:4.2rem;height:1.1rem;background:var(--bang-keo);transform:rotate(-4deg)}
.gl .tay{display:block;margin-bottom:.2rem}
.note{grid-column:1/-1;background:#fff;border:1.5px dashed var(--muc-2);border-radius:8px;
padding:1rem 1.2rem;margin:.7rem 0 .3rem;font-family:var(--than);font-size:.93rem}
.note>.tay{display:block;margin-bottom:.4rem}
.note dl{margin:0;display:grid;grid-template-columns:9.5rem minmax(0,1fr);gap:.35rem 1rem}
.note dt{font-family:var(--tay);color:var(--mo);font-size:1rem}
.note dd{margin:0}
.pending{color:var(--mo);font-style:italic;font-family:var(--than);font-size:.9rem}
.pair.li .en,.pair.li .vi{position:relative;padding-left:1.4rem}
.li-mk{position:absolute;left:0;color:var(--mo)}
.mermaid{text-align:center}
.cuoi{margin-top:3rem;padding-top:1rem;border-top:1.5px dashed var(--chi);font-family:var(--tay);color:var(--mo);text-align:center}
@media(max-width:820px){main{padding:2rem 1.2rem 3rem}main::before{display:none}
.pair,.cot{grid-template-columns:minmax(0,1fr)}.cot{display:none}.pair:has(>.en){background:none}
.en{padding-bottom:.4rem;border-bottom:1px dashed var(--chi);margin-bottom:.4rem}
.note dl{grid-template-columns:minmax(0,1fr)}}
@page{size:A4;margin:1.6cm 1.5cm 1.8cm;@bottom-center{content:counter(page);font:10pt "Be Vietnam Pro",sans-serif;color:#675e4f}}
@media print{html,body{background:#fff}body{padding:0;font-size:10.5pt}
main{max-width:none;border:0;box-shadow:none;padding:0;background:#fff}main::before{display:none}
.muc-luc a{text-decoration:none}
.so-do,figure,.note,.gl,.eqbox{break-inside:avoid}h2,h3,.hd{break-after:avoid}.pair{break-inside:avoid}
figure,.so-do{box-shadow:none}}
"""

# Mạch tóm lược theo đúng chuỗi lập luận của skill viết tài liệu kỹ thuật: vấn
# đề → khoảng trống → ý tưởng cốt lõi → cơ chế → bằng chứng → giới hạn.
_MACH = (("problem", "Vấn đề"), ("gap", "Khoảng trống"), ("idea", "Ý tưởng cốt lõi"),
         ("method", "Cách làm"), ("evidence", "Bằng chứng"), ("limits", "Giới hạn"))
_NHAN_GHI_CHU = (("gist", "Ý chính"), ("role", "Vai trò trong bài"),
                 ("link_back", "Nối với đoạn trước"), ("unpack", "Giải thích chi tiết"),
                 ("analogy", "Ví dụ trong bài"), ("caution", "Cần lưu ý"),
                 ("check", "Tự kiểm tra"))


def _export_html(doc: dict, mode: str, *, for_print: bool = False) -> Response:
    """Bản dịch thành MỘT file HTML tự chứa, đọc như một báo cáo: bìa, tóm lược
    theo mạch lập luận, mục lục, rồi thân bài song ngữ căn theo đoạn.

    Ảnh và font nhúng thẳng nên mở offline được. `for_print=True` mở luôn hộp in
    để lưu ra PDF — đường duy nhất giữ được cả sơ đồ Mermaid (cần JS để vẽ) lẫn
    lưới hai cột (cần CSS grid); thư viện PDF thuần Python không làm được cả hai.
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
        """Như `esc` nhưng dựng ký hiệu toán thành chữ thật: `\\(…\\)` qua
        `_math_tex`, `^{…}` / `_{…}` thành chỉ số, `**đậm**` thành chữ đậm.

        Phải khớp từng luật và đúng thứ tự với `sci()` bên `web/app.js`. Bản cũ
        thiếu bước `\\(…\\)` nên file xuất ra hiện nguyên `\\(Suf(a) \\in \\{0, 1\\}\\)`
        trong khi màn hình đã dựng đúng.

        Dấu `*` ĐƠN để nguyên: quét dữ liệu thật, cả hai chỗ dùng nó đều không phải
        chữ nghiêng (ký hiệu chú thích bảng, phép nhân `2 * 10^{−4}`).

        Chỉ dùng cho phần thân — KHÔNG dùng cho mã Mermaid, thuộc tính `alt` hay
        thẻ `<title>`, chèn thẻ vào những chỗ đó là hỏng.
        """
        out = esc(s)
        out = re.sub(r"\\\((.+?)\\\)", lambda m: f"<span class='imath'>{_math_tex(m.group(1))}</span>",
                     out, flags=re.S)
        out = re.sub(r"\^\{([^{}]*)\}", r"<sup>\1</sup>", out)
        out = re.sub(r"_\{([^{}]*)\}", r"<sub>\1</sub>", out)
        return re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", out)

    def so_do(code: str, cap: str = "") -> str:
        if not (code or "").strip():
            return ""
        diagrams.append(code)
        figcap = f"<figcaption>{esc(cap)}</figcaption>" if cap else ""
        return (f"<div class='so-do'><span class='keo'></span>"
                f"<div class='mermaid'>{esc(code.strip())}</div>{figcap}</div>")

    # ------------------------------------------------------------- bìa
    title_vi = brief.get("title_vi") or doc.get("title") or "Bài báo"
    meta = db.get_meta(doc_id)["data"]
    tg = meta.get("authors") or []
    out = ["<header class='bia'><p class='tay'>Bản dịch đọc hiểu</p>",
           f"<h1>{rich(title_vi)}</h1>"]
    goc = meta.get("title_goc") or (doc.get("title") if brief.get("title_vi") else "")
    if goc and goc != title_vi:
        out.append(f"<p class='goc'>{rich(goc)}</p>")
    if tg:
        out.append(f"<p class='tg'>{esc(', '.join(tg[:6]))}{' và cộng sự' if len(tg) > 6 else ''}</p>")
    nd = " · ".join(str(x) for x in (meta.get("venue"), meta.get("year")) if x)
    if nd:
        out.append(f"<p class='nd'>{esc(nd)}</p>")
    nguon = doc.get("source") or ""
    nguon_html = (f"<a href='{esc(nguon)}'>{esc(pipeline.nguon_gon(nguon))}</a>"
                  if nguon.startswith("http") else esc(pipeline.nguon_gon(nguon)))
    out.append(f"<p class='meta'>Nguồn {nguon_html} · Dịch bằng {esc(doc.get('model', ''))}"
               f" · Xuất ngày {time.strftime('%d/%m/%Y')}</p></header>")

    # ------------------------------------------------------------- tóm lược
    so_phan = 0

    def h2(ten: str) -> str:
        nonlocal so_phan
        so_phan += 1
        return f"<h2><span class='so'>{so_phan}</span>{esc(ten)}</h2>"

    if brief:
        out.append(h2("Tóm lược"))
        if brief.get("one_line"):
            out.append(f"<p class='chot'><span>{rich(brief['one_line'])}</span></p>")
        buoc = [(nhan, brief[k]) for k, nhan in _MACH if brief.get(k)]
        if buoc:
            out.append("<ol class='mach'>" + "".join(
                f"<li><span class='n'>{i}</span><div><b>{nhan}</b><p>{rich(nd)}</p></div></li>"
                for i, (nhan, nd) in enumerate(buoc, 1)) + "</ol>")
        for key, nhan in (("argument_diagram", "Mạch lập luận của bài"),
                          ("method_diagram", "Cơ chế bài đề xuất")):
            out.append(so_do(brief.get(key, ""), nhan))
        if brief.get("glossary"):
            out.append("<h3>Bảng thuật ngữ</h3><table class='tn'>"
                       "<tr><th>Tiếng Anh</th><th>Tiếng Việt</th><th>Nghĩa</th></tr>")
            for g in brief["glossary"]:
                vi = "<span class='giu'>giữ nguyên</span>" if g.get("keep_en") else rich(g.get("vi", ""))
                out.append(f"<tr><td>{rich(g.get('en', ''))}</td><td>{vi}</td>"
                           f"<td>{rich(g.get('gloss', ''))}</td></tr>")
            out.append("</table>")

    # ------------------------------------------------------------- mục lục
    _bl = doc["blocks"]
    tieu_de = [b for b in _bl if b["type"] == "heading" and not b.get("hidden")]
    if len(tieu_de) >= 3:
        out.append(h2("Mục lục"))
        out.append("<ul class='muc-luc'>" + "".join(
            f"<li class='c{min(max(b.get('level') or 1, 1), 3)}'>"
            f"<a href='#m-{esc(b['id'])}'>{rich(tr.get(b['id']) or b['text'])}</a></li>"
            for b in tieu_de) + "</ul>")

    # ------------------------------------------------------------- thân bài
    out.append(h2("Nội dung"))
    if not one_col:
        out.append("<div class='cot'><span>Bản gốc</span><span>Bản dịch</span></div>")
    for i, b in enumerate(_bl):
        if b["type"] in ("reference", "meta") or b.get("hidden"):
            continue
        vi = tr.get(b["id"], "")
        if b["type"] == "heading":
            cap = min(max(b.get("level") or 1, 1), 3)
            phu = (f"<span class='goc-h'>{rich(b['text'])}</span>"
                   if vi and not one_col and vi.strip() != b["text"].strip() else "")
            out.append(f"<div class='pair'><div class='hd c{cap}' id='m-{esc(b['id'])}'>"
                       f"{rich(vi or b['text'])}{phu}</div></div>")
            continue
        if b["type"] == "equation":
            uri = _data_uri(doc_id, b["figure"]) if b.get("figure") else ""
            body = (f"<img src='{uri}' alt='công thức'>" if uri
                    else f"<div class='eq'>{rich(b['text'])}</div>")
            # Công thức chen giữa hai nửa một đoạn: siết khoảng cách để ba khối
            # đọc ra liền như trang in. Cùng quy ước với `pairHTML` bên `app.js`.
            nxt = _bl[i + 1] if i + 1 < len(_bl) else None
            flow = " in-flow" if (nxt or {}).get("cont") else ""
            out.append(f"<div class='pair{flow}'><div class='eqbox'>{body}</div></div>")
            continue

        cells = []
        if b.get("figure"):
            uri = _data_uri(doc_id, b["figure"])
            if uri:
                cells.append(f"<figure><span class='keo'></span>"
                             f"<img src='{uri}' alt='{esc(b['text'][:90])}'></figure>")
        mk = f"<span class='li-mk'>{esc(b.get('marker'))}</span>" if b.get("marker") else ""
        if not one_col:
            cells.append(f"<div class='en'>{mk}{rich(b['text'])}</div>")
        vi_cell = rich(vi) if vi else "<span class='pending'>chưa dịch</span>"
        cells.append(f"<div class='vi'>{mk}{vi_cell}</div>")
        gl = plain.get(b["id"])
        if gl:
            cells.append(f"<div class='gl'><span class='tay'>Giải thích</span>{rich(gl)}</div>")
        n = notes.get(b["id"])
        if n:
            dl = "".join(f"<dt>{nhan}</dt><dd>{rich(n[k])}</dd>"
                         for k, nhan in _NHAN_GHI_CHU if n.get(k))
            cells.append(f"<div class='note'><span class='tay'>Ghi chú đọc hiểu</span>"
                         f"{so_do(n.get('diagram', ''), n.get('diagram_caption', ''))}<dl>{dl}</dl></div>")
        cls = (" li" if b.get("marker") else "") + (" is-cont" if b.get("cont") else "")
        out.append(f"<div class='pair{cls}'>{''.join(cells)}</div>")
    out.append("<p class='cuoi'>Hết bản dịch · tạo bằng Loupe</p>")

    # Mermaid nặng 3.5MB — chỉ nhúng khi bài thật sự có sơ đồ để vẽ
    script = ""
    if diagrams:
        js = (WEB / "vendor" / "mermaid.min.js").read_text(encoding="utf-8")
        script = (f"<script>{js}</script><script>"
                  "mermaid.initialize({startOnLoad:false,securityLevel:'strict',theme:'neutral',"
                  "fontFamily:'Be Vietnam Pro, sans-serif',flowchart:{curve:'basis',htmlLabels:false}});"
                  "window.__ready=mermaid.run().catch(()=>{});</script>")
    # Đợi font + sơ đồ rồi mới in — in sớm thì PDF ra ô trống và chữ font dự phòng.
    script += ("<script>addEventListener('load',()=>Promise.all([window.__ready,"
               "document.fonts&&document.fonts.ready]).then(()=>{window.__xong=true;"
               + ("setTimeout(print,300);" if for_print else "") + "}));</script>")

    page = (f"<!doctype html><html lang='vi'><head><meta charset='utf-8'>"
            f"<meta name='viewport' content='width=device-width,initial-scale=1'>"
            f"<title>{esc(title_vi)}</title><style>{_font_nhung()}{_EXPORT_CSS}</style></head>"
            f"<body class='{'one' if one_col else ''}'><main>{''.join(out)}</main>{script}</body></html>")

    if for_print:
        # inline chứ không attachment — phải hiện ra trong tab thì mới in được
        return Response(page, media_type="text/html; charset=utf-8")
    name = f"{doc_id}-{'vi' if one_col else 'song-ngu'}.html"
    return Response(page, media_type="text/html; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{name}"'})


def _font_nhung() -> str:
    """`vendor/fonts.css` với mọi `url(fonts/…)` thay bằng data URI — file slide tải
    về phải mở được khi không có mạng và không có server, mà vẫn đúng font sổ tay."""
    import base64
    css = (WEB / "vendor" / "fonts.css").read_text(encoding="utf-8")

    def thay(m):
        f = WEB / "vendor" / m.group(1)
        if not f.exists():
            return m.group(0)
        return "url(data:font/woff2;base64," + base64.b64encode(f.read_bytes()).decode() + ")"
    return re.sub(r"url\((?:/vendor/|\./)?(fonts/[^)\s'\"]+)\)", thay, css)


def _xuat_slide(doc: dict, *, in_ra: bool = False) -> Response:
    """Bộ slide thành MỘT file HTML tự chứa: trình chiếu offline, hoặc in ra PDF.

    Nhúng NGUYÊN VĂN `web/slide-ve.js` + `web/slide.css` — đúng bộ vẽ mà app dùng
    để xem trước và trình chiếu. Bản cũ có bộ dựng riêng cho file xuất (và một bộ
    nữa cho PowerPoint), lệch nhau mỗi lần sửa; giờ không còn gì để lệch.
    """
    import json as _json
    d = _slides_ra(doc)
    if not d["bo"]:
        raise HTTPException(400, "Bài này chưa có bộ slide — bấm “Tạo slide” trước")
    anh = {}
    for sl in d["bo"]:
        if sl.get("anh") and sl["anh"] not in anh:
            anh[sl["anh"]] = _data_uri(doc["id"], sl["anh"])
    du_lieu = _json.dumps({"bo": d["bo"], "anh": anh,
                           "ten_bai": (doc.get("brief") or {}).get("title_vi") or doc.get("title") or ""},
                          ensure_ascii=False).replace("</", "<\\/")
    ve = (WEB / "slide-ve.js").read_text(encoding="utf-8")
    css = (WEB / "slide.css").read_text(encoding="utf-8")
    import html as _html
    ten = _html.escape((doc.get("brief") or {}).get("title_vi") or doc.get("title") or "Bộ slide")
    html = f"""<!doctype html><html lang="vi"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>{ten} — slide</title>
<style>{_font_nhung()}
{css}
html,body{{margin:0;background:#2b2925}}
#ds{{display:grid;gap:28px;padding:28px;max-width:1280px;margin:0 auto}}
#ds .sld{{box-shadow:0 6px 24px rgba(0,0,0,.35)}}
#mo{{position:fixed;right:18px;top:14px;z-index:5;font:600 15px system-ui;padding:.5rem 1rem;border:2px solid #1d1b18;
  border-radius:10px;background:#ffd43b;cursor:pointer;box-shadow:3px 3px 0 #1d1b18}}
#chieu{{position:fixed;inset:0;background:#0b0d12;display:none;align-items:center;justify-content:center;z-index:9}}
#chieu.on{{display:flex}}
#chieu .khung{{width:min(100vw,calc(100vh*16/9))}}
#loi{{position:fixed;right:0;top:0;bottom:0;width:22rem;background:#fffcf3;padding:1.2rem;overflow:auto;
  font:16px/1.6 system-ui;display:none;z-index:10}}
#loi.on{{display:block}}
@page{{size:13.333in 7.5in;margin:0}}
@media print{{html,body{{background:#fff}}#ds{{display:block;padding:0;gap:0;max-width:none}}
  #ds .sld{{width:13.333in;box-shadow:none}}#mo,#chieu,#loi{{display:none!important}}}}
</style></head><body>
<button id="mo" title="Trình chiếu — ← → chuyển, S lời nói, Esc thoát">▶ Trình chiếu</button>
<div id="ds"></div><div id="chieu"><div class="khung"></div></div><aside id="loi"></aside>
<script>{ve}</script>
<script>
const D = {du_lieu};
const ctx = {{bo: D.bo, anh: (s) => D.anh[s.anh] || "", ten_bai: D.ten_bai}};
const ds = document.getElementById("ds");
ds.innerHTML = D.bo.map((s) => SlideVe.ve(s, ctx)).join("");
let i = 0;
const ch = document.getElementById("chieu"), loi = document.getElementById("loi");
function hien(k) {{
  i = Math.max(0, Math.min(k, D.bo.length - 1));
  ch.querySelector(".khung").innerHTML = SlideVe.ve(D.bo[i], ctx);
  SlideVe.vuaKhung(ch.querySelector(".sld"));
  loi.textContent = D.bo[i].loi_noi || "";
}}
document.getElementById("mo").onclick = () => {{ ch.classList.add("on"); hien(0);
  document.documentElement.requestFullscreen?.().catch(() => {{}}); }};
ch.onclick = () => hien(i + 1);
addEventListener("keydown", (e) => {{
  if (!ch.classList.contains("on")) return;
  if (["ArrowRight", "PageDown", " "].includes(e.key)) {{ e.preventDefault(); hien(i + 1); }}
  else if (["ArrowLeft", "PageUp"].includes(e.key)) hien(i - 1);
  else if (e.key === "Escape") {{ ch.classList.remove("on"); loi.classList.remove("on"); }}
  else if (e.key.toLowerCase() === "s") loi.classList.toggle("on");
}});
// Co chữ SAU khi font đã nạp — đo bằng font dự phòng thì co sai.
(document.fonts ? document.fonts.ready : Promise.resolve()).then(() => {{
  document.querySelectorAll("#ds .sld").forEach(SlideVe.vuaKhung);
  window.__ready = true;
  {"setTimeout(() => window.print(), 300);" if in_ra else ""}
}});
</script></body></html>"""
    return Response(html, media_type="text/html; charset=utf-8",
                    headers={} if in_ra else
                    {"Content-Disposition": f'attachment; filename="{doc["id"]}-slide.html"'})


def _clip(s: str, n: int) -> str:
    """Cắt ở ranh giới từ. Cắt giữa chữ ra “trả lời câu hỏ” — trông như lỗi."""
    s = (s or "").strip()
    if len(s) <= n:
        return s
    cut = s[:n].rsplit(" ", 1)[0] or s[:n]
    return cut.rstrip(" ,;:.") + "…"


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


# Trang giới thiệu + hướng dẫn sử dụng. Nằm ở `docs/` (không ở `web/`) để GitHub
# Pages phục vụ được nguyên thư mục đó; app chỉ gắn thêm để bấm "Hướng dẫn" trong
# app là mở được ngay, kể cả lúc không có mạng. Phải gắn TRƯỚC dòng "/".
DOCS = ROOT / "docs"
if DOCS.is_dir():
    app.mount("/gioi-thieu", _NoCacheStatic(directory=DOCS, html=True), name="docs")

app.mount("/", _NoCacheStatic(directory=WEB), name="web")
