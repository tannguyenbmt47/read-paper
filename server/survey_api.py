"""Route cho kho survey. Gắn vào app chính bằng `include_router`.

Phải gắn **trên** `app.mount("/", StaticFiles(...))` ở cuối `main.py`, nếu không
mọi đường dẫn ở đây bị bộ phục vụ file tĩnh nuốt và trả 404.

Ranh giới tiền, giữ cho rõ ở tầng route để người dùng biết bấm gì thì mất tiền:

  **miễn phí** — mọi thao tác đọc, `GET …/search`, `GET …/matrix`, `GET …/graph`,
                 `POST …/papers` với `enrich=0`, và mọi lần vector hoá (chạy GPU
                 tại máy).
  **tốn tiền** — `POST …/papers` với `enrich=1`, `POST …/paper/{id}/enrich`,
                 `POST …/paper/{id}/recard`, và `GET …/ask`.
"""

from __future__ import annotations

import asyncio
import json
import time

from fastapi import APIRouter, Body, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import PlainTextResponse, StreamingResponse

from . import store
from .survey import agent, embed
from .survey import db as sdb
from .survey import ingest, lecture, refs, search, sources, synth

router = APIRouter(prefix="/api/survey", tags=["survey"])

# Hàng đợi tiến trình nạp, mỗi kho một cái. Cùng khuôn với `_JOBS` của màn nạp
# bài ở main.py: mở SSE trước, POST file sau.
_JOBS: dict[str, asyncio.Queue] = {}


def _sse(event: str, data) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def _say(sid: str, msg: str, **extra) -> None:
    q = _JOBS.get(sid)
    if q is None:
        return
    try:
        q.put_nowait({"msg": msg, **extra})
    except Exception:                       # noqa: BLE001
        pass


def _need(sid: str) -> dict:
    try:
        return sdb.load_survey(sid)
    except (KeyError, ValueError):
        raise HTTPException(404, "Không tìm thấy kho này") from None


# ------------------------------------------------------------------ dự án


@router.get("s")
async def list_surveys():
    return {"surveys": sdb.list_surveys(),
            "embed": embed.status(), "rerank": embed.rerank_status(),
            "web_search": sources.web_available()}


@router.post("")
async def create(body: dict = Body(default={})):
    name = (body.get("name") or "").strip() or "Kho chưa đặt tên"
    return sdb.create_survey(name, (body.get("topic") or "").strip())


@router.get("/{sid}")
async def get_survey(sid: str):
    s = _need(sid)
    # Model **đã phân giải**, không phải giá trị thô trong bảng. Cột `model` để
    # trống nghĩa là "theo .env", mà người dùng không đọc được .env từ trình
    # duyệt — trả về đúng thứ sắp chạy, kèm nguồn của nó, để giao diện nói thật.
    strong, fast = sdb.models_of(sid)
    return {**s, "papers": sdb.list_papers(sid), "stats": sdb.stats(sid),
            "runs": sdb.list_runs(sid),
            "models": {"strong": strong, "fast": fast,
                       "strong_src": "kho" if s.get("model") else ".env",
                       "fast_src": "kho" if s.get("fast_model") else ".env"}}


@router.patch("/{sid}")
async def patch_survey(sid: str, body: dict = Body(...)):
    _need(sid)
    fields = {k: v for k, v in body.items()
              if k in ("name", "topic", "facets", "budget_usd")}
    return sdb.update_survey(sid, **fields)


@router.delete("/{sid}")
async def drop_survey(sid: str):
    _need(sid)
    sdb.delete_survey(sid)
    return {"ok": True}


# -------------------------------------------------------------- nạp bài


@router.get("/{sid}/progress")
async def progress(sid: str):
    """Tiến trình nạp. Mở TRƯỚC khi POST file lên, giống màn nạp bài của luồng đọc."""
    _need(sid)
    q: asyncio.Queue = asyncio.Queue()
    _JOBS[sid] = q

    async def gen():
        try:
            while True:
                try:
                    item = await asyncio.wait_for(q.get(), timeout=600)
                except asyncio.TimeoutError:
                    break                   # không ai nghe nữa, đừng giữ kết nối
                if item is None:
                    break
                yield _sse("step", item)
        finally:
            _JOBS.pop(sid, None)

    return StreamingResponse(gen(), media_type="text/event-stream", headers={
        "Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@router.post("/{sid}/papers")
async def add_papers(sid: str, files: list[UploadFile] = File(default=[]),
                     enrich: int = Form(1), use_layout: int = Form(0)):
    """Nạp nhiều PDF một lượt.

    Nạp **tuần tự** chứ không song song, dù chậm hơn: bóc PDF ăn RAM theo cỡ file
    và vector hoá ăn VRAM, chạy bốn bài cùng lúc trên máy để bàn là đường ngắn
    nhất tới hết bộ nhớ giữa chừng — lúc đó mất cả lô chứ không mất một bài.
    """
    _need(sid)
    out, spent = [], 0.0
    for i, f in enumerate(files):
        name = f.filename or f"tài liệu {i+1}"
        _say(sid, f"[{i+1}/{len(files)}] {name}", pct=int(100 * i / max(1, len(files))))
        try:
            data = await f.read()
            if data[:5] != b"%PDF-" and not name.lower().endswith(".pdf"):
                raise ValueError("không phải file PDF")
            p = await ingest.ingest_pdf(
                sid, data, source=name, enrich=bool(enrich),
                use_layout=bool(use_layout),
                say=lambda m, n=name: _say(sid, f"{n}: {m}"))
            spent += p.get("cost", 0.0)
            out.append({"id": p["id"], "title": p["title"], "status": p["status"],
                        "duplicate": p.get("duplicate", False), "cost": p.get("cost", 0)})
        except Exception as e:               # noqa: BLE001 — một file hỏng không được làm hỏng cả lô
            _say(sid, f"{name}: LỖI — {e}")
            out.append({"title": name, "status": "failed", "err": str(e)[:200]})
    _say(sid, "xong", pct=100)
    if (q := _JOBS.get(sid)) is not None:
        q.put_nowait(None)
    return {"papers": out, "cost": round(spent, 5), "stats": sdb.stats(sid)}


@router.post("/{sid}/papers/url")
async def add_by_url(sid: str, body: dict = Body(...)):
    """Nạp theo đường dẫn arXiv hoặc URL PDF trực tiếp."""
    _need(sid)
    url = (body.get("url") or "").strip()
    if not url:
        raise HTTPException(400, "Thiếu đường dẫn")
    try:
        data = await sources.fetch_pdf({"pdf_url": url if url.endswith(".pdf") else "",
                                        "url": url})
        p = await ingest.ingest_pdf(sid, data, source=url, url=url,
                                    enrich=bool(body.get("enrich", 1)),
                                    say=lambda m: _say(sid, m))
    except Exception as e:                   # noqa: BLE001
        raise HTTPException(422, f"Không nạp được: {e}") from e
    return p


@router.post("/{sid}/papers/import")
async def import_from_loupe(sid: str, body: dict = Body(...)):
    """Kéo bài đã nạp ở màn đọc sang kho, **kèm cả bản dịch** — miễn phí."""
    _need(sid)
    ids = body.get("doc_ids") or []
    out = []
    for did in ids:
        try:
            out.append(await ingest.ingest_loupe_doc(sid, did))
        except Exception as e:               # noqa: BLE001
            out.append({"id": did, "status": "failed", "err": str(e)[:200]})
    return {"papers": out, "stats": sdb.stats(sid)}


@router.get("/{sid}/loupe-docs")
async def loupe_docs(sid: str):
    """Bài có sẵn ở màn đọc, kèm cờ đã nằm trong kho hay chưa."""
    _need(sid)
    have = {p["loupe_doc_id"] for p in sdb.list_papers(sid) if p.get("loupe_doc_id")}
    return {"docs": [{**d, "in_survey": d["id"] in have} for d in store.list_docs()]}


@router.post("/{sid}/paper/{pid}/enrich")
async def enrich(sid: str, pid: str):
    """Chạy phần tốn tiền cho một bài đã nạp thô. **Có giá.**"""
    _need(sid)
    try:
        return await ingest.enrich_paper(sid, pid, say=lambda m: _say(sid, f"{pid}: {m}"))
    except KeyError:
        raise HTTPException(404, "Không tìm thấy bài") from None
    except Exception as e:                   # noqa: BLE001
        raise HTTPException(502, f"Lỗi khi bơm nội dung: {e}") from e


@router.post("/{sid}/paper/{pid}/recard")
async def recard(sid: str, pid: str):
    _need(sid)
    try:
        return await ingest.recard(pid)
    except KeyError:
        raise HTTPException(404, "Không tìm thấy bài") from None
    except Exception as e:                   # noqa: BLE001
        raise HTTPException(502, f"Lỗi khi bóc lại phiếu: {e}") from e


@router.post("/{sid}/paper/{pid}/move")
async def move_paper(sid: str, pid: str, to: str = Body(..., embed=True)):
    """Chuyển bài sang kho khác. **Không bóc lại, không gọi model, miễn phí.**

    Cách chữa hiển nhiên cho việc nạp nhầm kho — xoá đi nạp lại — ném mất phần
    đắt nhất (phiếu, câu ngữ cảnh, cây tóm lược, vector, bài giảng) và tốn lại
    ~$0,034 mỗi bài.
    """
    _need(sid)
    p = sdb.load_paper(pid)
    if p["survey_id"] != sid:
        raise HTTPException(404, "Bài không thuộc kho này")
    try:
        got = sdb.move_paper(pid, to)
    except KeyError:
        raise HTTPException(404, "Không có kho đích") from None
    if not got.get("moved"):
        raise HTTPException(409, got.get("msg") or "Không chuyển được")
    return got


@router.delete("/{sid}/paper/{pid}")
async def drop_paper(sid: str, pid: str):
    _need(sid)
    sdb.drop_paper(pid)
    return {"ok": True, "stats": sdb.stats(sid)}


@router.get("/{sid}/paper/{pid}")
async def get_paper(sid: str, pid: str):
    _need(sid)
    try:
        p = sdb.load_paper(pid)
    except (KeyError, ValueError):
        raise HTTPException(404, "Không tìm thấy bài") from None
    return {**p, "chunks": sdb.paper_chunks(pid, level=None)}


# ------------------------------------------------------------- tìm kiếm


@router.get("/{sid}/search")
async def do_search(sid: str, q: str = Query(...), limit: int = 20):
    """Tìm thuần: BM25 + dense trộn RRF. **Không gọi model, miễn phí.**"""
    _need(sid)
    hits = await search.plain(sid, q, limit=limit)
    return {"hits": [{
        "id": h["id"], "paper_id": h["paper_id"], "title": h.get("paper_title", ""),
        "year": h.get("year"), "section": h.get("section", ""), "page": h.get("page"),
        "level": h.get("level", 0), "kind": h.get("kind"),
        "ctx": h.get("ctx", ""), "text": h["text"][:600],
        "vi": (h.get("vi") or "")[:600], "loupe_doc_id": h.get("loupe_doc_id", ""),
    } for h in hits]}


@router.get("/{sid}/chunk/{cid}")
async def get_chunk(sid: str, cid: str):
    """Mở đúng đoạn được trích dẫn, kèm hai đoạn kề để đọc có ngữ cảnh."""
    _need(sid)
    rows = sdb.get_chunks([cid])
    if cid not in rows:
        raise HTTPException(404, "Không tìm thấy đoạn")
    ch = rows[cid]
    around = [c for c in sdb.paper_chunks(ch["paper_id"], level=ch.get("level", 0))
              if abs((c.get("ord") or 0) - (ch.get("ord") or 0)) <= 1]
    return {"chunk": ch, "around": around}


# ------------------------------------------------------------- tổng hợp


@router.get("/{sid}/synthesis")
async def get_synth(sid: str, fmt: str = "json"):
    """Đọc bản tổng hợp đã dựng. **Không gọi model, miễn phí.**"""
    s = _need(sid)
    if fmt == "md":
        return PlainTextResponse(synth.as_markdown(s.get("synth") or {}),
                                 media_type="text/markdown")
    return {"synth": s.get("synth"), "stale": s.get("synth_stale", False),
            "carded": sdb.stats(sid)["carded"]}


@router.get("/{sid}/synthesis/build")
async def build_synth(sid: str):
    """Dựng lại bản tổng hợp, phát SSE. **Tốn tiền** — một lượt model mạnh."""
    _need(sid)

    async def gen():
        try:
            async for kind, payload in synth.build(sid):
                yield _sse(kind, payload)
        except Exception as e:               # noqa: BLE001
            yield _sse("error", {"msg": f"{type(e).__name__}: {e}"[:300]})

    return StreamingResponse(gen(), media_type="text/event-stream", headers={
        "Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# ------------------------------------------------------------- bài giảng
#
# Ba route, và ranh giới giữa chúng là ranh giới TIỀN — cùng lối với luồng
# đọc-hiểu (soát miễn phí trước, dịch tốn tiền sau):
#   GET  …/lecture        đọc bản đã dựng          — $0
#   GET  …/lecture/refs   hồ sơ đối chiếu          — $0, chỉ đi mạng
#   GET  …/lecture/build  dựng bài giảng           — TỐN TIỀN, SSE


@router.get("/{sid}/paper/{pid}/lecture")
async def get_lecture(sid: str, pid: str, fmt: str = "json"):
    """Đọc bài giảng đã dựng. **Không gọi model, miễn phí.**"""
    _need(sid)
    p = sdb.load_paper(pid)
    if p["survey_id"] != sid:
        raise HTTPException(404, "Bài không thuộc kho này")
    lec = sdb._loads(p.get("lecture"), {})
    if fmt == "md":
        if not lec:
            raise HTTPException(404, "Bài này chưa có bài giảng")
        return PlainTextResponse(lecture.as_markdown(p, lec),
                                 media_type="text/markdown")
    return {"lecture": lec, "stale": lecture.stale(p),
            "title": p.get("title"), "has_text": bool(sdb.paper_chunks(pid, level=0))}


@router.get("/{sid}/paper/{pid}/lecture/refs")
async def get_refs(sid: str, pid: str, force: bool = False):
    """Hồ sơ đối chiếu. **Miễn phí** — Semantic Scholar không cần key.

    Xem được trước khi bấm dựng, nên người dùng biết phần đối chiếu sẽ dày hay
    mỏng trước lúc tiêu tiền.
    """
    _need(sid)
    p = sdb.load_paper(pid)
    if p["survey_id"] != sid:
        raise HTTPException(404, "Bài không thuộc kho này")
    titles = refs.corpus_titles(sid, skip=pid)
    dos = (await refs.dossier(p, titles)) if force else (await refs.ensure(p, titles))
    if force and (dos.get("refs") or dos.get("s2_id")):
        refs.save(pid, dos)
    return dos


@router.get("/{sid}/paper/{pid}/lecture/build")
async def build_lecture(sid: str, pid: str, deepen: bool = True):
    """Dựng bài giảng, phát SSE. **Tốn tiền** — 2 lượt, cộng 1 lượt nếu đào sâu."""
    _need(sid)
    p = sdb.load_paper(pid)
    if p["survey_id"] != sid:
        raise HTTPException(404, "Bài không thuộc kho này")

    async def gen():
        try:
            async for kind, payload in lecture.build(pid, deepen=deepen):
                yield _sse(kind, payload)
        except Exception as e:               # noqa: BLE001
            yield _sse("error", {"msg": f"{type(e).__name__}: {e}"[:300]})

    return StreamingResponse(gen(), media_type="text/event-stream", headers={
        "Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@router.get("/{sid}/graph")
async def get_graph(sid: str, limit: int = 60):
    """Đồ thị thực thể để vẽ. Không gọi model."""
    _need(sid)
    return sdb.graph_overview(sid, limit=limit)


# ---------------------------------------------------------------- hỏi đáp


@router.get("/{sid}/ask")
async def ask(sid: str, q: str = Query(...), budget: float | None = None,
              entail: int = 1, cache: int = 1):
    """Vòng lặp đào sâu, phát SSE. **Tốn tiền** — trần lấy từ `budget` hoặc từ kho."""
    s = _need(sid)
    cap = s.get("budget_usd") if budget is None else budget

    async def gen():
        try:
            async for frame in agent.deep_dive(sid, q, budget=cap,
                                               entail=bool(entail), use_cache=bool(cache)):
                yield frame
        except Exception as e:               # noqa: BLE001
            yield _sse("error", {"msg": f"{type(e).__name__}: {e}"[:300]})

    return StreamingResponse(gen(), media_type="text/event-stream", headers={
        "Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@router.get("/{sid}/run/{rid}")
async def get_run(sid: str, rid: str):
    _need(sid)
    r = sdb.load_run(rid)
    if r is None:
        raise HTTPException(404, "Không tìm thấy lượt hỏi")
    return r


# ---------------------------------------------------- bảng trích xuất


def _cell(card: dict, key: str) -> str:
    v = (card or {}).get(key)
    if v is None:
        return ""
    if isinstance(v, list):
        if v and isinstance(v[0], dict):     # `results` là danh sách object
            return " · ".join(
                f"{x.get('claim','')}" + (f" ({x['number']})" if x.get("number") else "")
                for x in v[:3])
        return ", ".join(str(x) for x in v[:8])
    return str(v)


@router.get("/{sid}/matrix")
async def matrix(sid: str, fmt: str = "json"):
    """Bảng bài × facet, dựng thẳng từ cột `card`. **Không gọi model.**"""
    s = _need(sid)
    facets = s["facets"]
    rows = []
    for p in sdb.list_papers(sid):
        rows.append({
            "id": p["id"], "title": p.get("title") or "", "year": p.get("year"),
            "venue": p.get("venue") or "", "cites": p.get("cites") or 0,
            "status": p["status"], "has_card": bool(p.get("card")),
            "loupe_doc_id": p.get("loupe_doc_id") or "",
            "cells": {f["key"]: _cell(p.get("card") or {}, f["key"]) for f in facets},
        })

    if fmt == "json":
        return {"facets": facets, "rows": rows}

    head = ["Bài", "Năm"] + [f["label"] for f in facets]
    if fmt == "csv":
        import csv
        import io
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(head)
        for r in rows:
            w.writerow([r["title"], r["year"] or ""] + [r["cells"][f["key"]] for f in facets])
        # BOM để Excel mở ra không vỡ dấu tiếng Việt
        return PlainTextResponse("\ufeff" + buf.getvalue(), media_type="text/csv",
                                 headers={"Content-Disposition":
                                          f'attachment; filename="{sid}-matrix.csv"'})
    if fmt == "md":
        def esc(x):
            return str(x).replace("|", "\\|").replace("\n", " ")
        out = ["| " + " | ".join(head) + " |",
               "|" + "|".join("---" for _ in head) + "|"]
        for r in rows:
            out.append("| " + " | ".join(
                [esc(r["title"]), esc(r["year"] or "")]
                + [esc(r["cells"][f["key"]]) for f in facets]) + " |")
        return PlainTextResponse("\n".join(out), media_type="text/markdown")
    raise HTTPException(400, "fmt phải là json, csv hoặc md")


# ------------------------------------------------------------ tìm bài mới


@router.post("/{sid}/find")
async def find_papers(sid: str, body: dict = Body(...)):
    """Tìm bài trên arXiv / OpenAlex / Crossref (+ web nếu có key). Miễn phí."""
    _need(sid)
    q = (body.get("q") or "").strip()
    if not q:
        raise HTTPException(400, "Thiếu từ khoá tìm")
    rows, errs = await sources.find(q, limit=int(body.get("limit") or 20),
                                    use_web=bool(body.get("web")))
    have = {(p.get("doi") or "").lower() for p in sdb.list_papers(sid) if p.get("doi")}
    for r in rows:
        r["in_survey"] = bool(r.get("doi")) and r["doi"].lower() in have
    # `errs` đi kèm kể cả khi có kết quả: hai nguồn hỏng mà một nguồn chạy thì
    # danh sách trông vẫn bình thường, và người dùng không biết mình đang nhìn
    # một phần ba số bài đáng lẽ phải thấy.
    return {"results": rows, "errs": errs, "web_available": sources.web_available()}


@router.post("/{sid}/find/add")
async def add_found(sid: str, body: dict = Body(...)):
    """Nạp các bài đã tick từ kết quả tìm.

    Bài không có PDF mở vẫn nhận vào, nhưng **chỉ có abstract** và được đánh dấu
    `abstract_only` — để câu trả lời sau này không tưởng là đã đọc cả bài.
    """
    _need(sid)
    rows = body.get("items") or []
    enrich = bool(body.get("enrich", 1))
    out, spent = [], 0.0
    for i, row in enumerate(rows):
        title = row.get("title") or f"bài {i+1}"
        _say(sid, f"[{i+1}/{len(rows)}] {title[:60]}", pct=int(100 * i / max(1, len(rows))))
        try:
            data = await sources.fetch_pdf(row)
            p = await ingest.ingest_pdf(sid, data, source=row.get("source", "web"),
                                        url=row.get("url", ""), enrich=enrich,
                                        say=lambda m, t=title: _say(sid, f"{t[:40]}: {m}"))
            sdb.update_paper(p["id"], doi=row.get("doi", ""), venue=row.get("venue", ""),
                             year=row.get("year"), cites=row.get("cites") or 0,
                             authors=row.get("authors", ""),
                             title=p.get("title") or row.get("title", ""))
            spent += p.get("cost", 0.0)
            out.append({"id": p["id"], "title": p["title"], "status": p["status"]})
        except Exception as e:               # noqa: BLE001
            pid = _abstract_only(sid, row, str(e))
            out.append({"id": pid, "title": title, "status": "abstract_only",
                        "err": str(e)[:160]})
            _say(sid, f"{title[:40]}: không tải được PDF, chỉ nhận abstract")
    _say(sid, "xong", pct=100)
    if (q := _JOBS.get(sid)) is not None:
        q.put_nowait(None)
    return {"papers": out, "cost": round(spent, 5), "stats": sdb.stats(sid)}


def _abstract_only(sid: str, row: dict, err: str) -> str:
    """Không tải được PDF thì vẫn giữ abstract — có còn hơn không, nhưng phải đánh dấu."""
    from . import db as maindb

    abstract = (row.get("abstract") or "").strip()
    if not abstract:
        return ""
    pid = sdb.add_paper(
        sid, sha256=maindb.sha((row.get("doi") or row.get("url") or row.get("title") or "")),
        title=row.get("title", ""), authors=row.get("authors", ""),
        year=row.get("year"), venue=row.get("venue", ""), doi=row.get("doi", ""),
        url=row.get("url", ""), source=row.get("source", "web"),
        cites=row.get("cites") or 0, status="abstract_only", err=err[:200])
    sdb.put_chunks(pid, [{"ord": 1, "section": "Abstract", "page": 1, "kind": "abstract",
                          "text": abstract, "ctx": row.get("title", ""), "vi": ""}],
                   title=row.get("title", ""))
    return pid


@router.post("/{sid}/vectorise")
async def do_vectorise(sid: str):
    """Vector hoá lại các node còn thiếu. Chạy trên máy nên **miễn phí**."""
    _need(sid)
    t = time.time()
    n = await ingest.vectorise(sid)
    return {"vectorised": n, "secs": round(time.time() - t, 1), "embed": embed.status()}
