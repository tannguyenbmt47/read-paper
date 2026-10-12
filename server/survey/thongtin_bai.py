"""Bổ sung thông tin thư mục (năm, tác giả, nơi đăng, arXiv/DOI) cho bài trong kho.

## Vì sao cần

Báo cáo 11/10 gặp hai lỗi cùng một gốc:

- Bản tổng hợp viết *"không có năm công bố ghi rõ"* về World Models (2018), vì
  cả năm bài của kho đều trống cột `year` — phiếu không có năm thì model không
  có năm để viết.
- Bài giảng báo *"tiêu đề quá ngắn để tra (“World Models”) — sửa lại tiêu đề"*,
  mà "World Models" là tên THẬT của bài. Bộ tra Semantic Scholar chặn tiêu đề
  ngắn (đúng: "Question Answering" khớp nghìn bài) và không có mã arXiv để đi
  đường khác.

Trong khi bài lấy từ phần Dịch đã có sẵn đủ thông tin ở `doc_meta` (Thư viện tự
tra lúc nạp), chỉ là kho survey chưa lấy sang. Bài nạp thẳng vào kho thì tra
bằng chính `thongtin.tim` của Thư viện — một bộ tra, không đẻ bộ thứ hai.

Chỉ ĐIỀN chỗ trống, không ghi đè: tiêu đề, tác giả người dùng đã sửa tay
(`PATCH …/paper`) phải giữ nguyên.
"""

from __future__ import annotations

import os

from . import db as sdb


def _bat() -> bool:
    # Bộ test không được gọi mạng ngoài — cùng công tắc với Thư viện.
    return os.getenv("META_LOOKUP", "on").lower() != "off"


def _tac_gia(ds) -> str:
    ds = [a for a in (ds or []) if a]
    return ", ".join(ds[:6]) + (" và cộng sự" if len(ds) > 6 else "")


def _dien(p: dict, meta: dict) -> dict:
    """Những trường còn trống mà `meta` điền được."""
    moi: dict = {}
    if not p.get("year") and meta.get("year"):
        moi["year"] = int(meta["year"])
    if not (p.get("authors") or "").strip() and meta.get("authors"):
        moi["authors"] = _tac_gia(meta["authors"])
    if not (p.get("venue") or "").strip() and meta.get("venue"):
        moi["venue"] = str(meta["venue"])[:160]
    if not (p.get("doi") or "").strip() and meta.get("doi"):
        moi["doi"] = str(meta["doi"])
    if not (p.get("url") or "").strip():
        if meta.get("arxiv"):
            moi["url"] = f"https://arxiv.org/abs/{meta['arxiv']}"
        elif meta.get("url"):
            moi["url"] = str(meta["url"])
    return moi


def thieu(p: dict) -> bool:
    return not p.get("year") or not (p.get("url") or p.get("doi"))


async def bo_sung(pid: str) -> dict:
    """Điền thông tin còn thiếu cho một bài. Trả các trường đã điền ({} nếu không).

    Miễn phí: chỉ đọc DB của Thư viện hoặc gọi API công khai (S2, arXiv,
    Crossref). Hỏng mạng thì trả {} — thiếu thông tin không chặn việc gì."""
    p = sdb.load_paper(pid)
    if not p or not thieu(p):
        return {}
    meta: dict = {}
    if p.get("loupe_doc_id"):
        from .. import db as maindb
        meta = maindb.get_meta(p["loupe_doc_id"]).get("data") or {}
    if not meta and _bat():
        from .. import thongtin
        dau = sdb.paper_chunks(pid, level=0)[:15]
        doc = {"title": p.get("title") or "", "source": p.get("url") or p.get("source") or "",
               "blocks": [{"text": c.get("text") or ""} for c in dau]}
        try:
            meta, _nguon = await thongtin.tim(doc)
        except Exception:                   # noqa: BLE001 — mạng hỏng: để trống
            meta = {}
    moi = _dien(p, meta or {})
    if moi:
        sdb.update_paper(pid, **moi)
    return moi


async def bo_sung_kho(sid: str) -> int:
    """Điền cho mọi bài còn thiếu của một kho. Trả số bài đã điền được."""
    n = 0
    for p in sdb.list_papers(sid):
        if thieu(p) and await bo_sung(p["id"]):
            n += 1
    return n
