"""Lưu trữ bằng SQLite, kèm hai lớp chống làm lại việc đã làm.

Trước đây mỗi bài là một file JSON. Cách đó chạy được nhưng lặp việc rất nhiều:
nạp lại cùng một PDF là parse lại từ đầu, chạy lại mô hình bố cục, và **dịch lại
toàn bộ** dù từng chữ đều y hệt lần trước.

Hai bảng dưới đây bịt hai chỗ lặp đó:

  `parse_cache`  khoá theo SHA-256 của chính file PDF. Cùng một file thì cấu trúc
                 bóc ra và khung hình chắc chắn giống hệt — không có lý do gì
                 chạy lại PyMuPDF và mô hình bố cục.

  `tm`           bộ nhớ dịch, khoá theo SHA-256 của đoạn văn gốc + model. Đoạn
                 nào đã dịch rồi thì lấy lại miễn phí, dù nó thuộc bài khác hay
                 thuộc lần nạp trước của cùng bài.

SQLite là lựa chọn đúng cho công cụ chạy local một người: có sẵn trong Python,
một file duy nhất, ghi có giao dịch nên không hỏng dở như ghi file JSON.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import threading
import time
from pathlib import Path

DATA_DIR = Path(os.getenv("PAPER_DATA_DIR", Path(__file__).resolve().parent.parent / "data"))
DB_PATH = DATA_DIR / "papers.db"

_local = threading.local()

SCHEMA = """
CREATE TABLE IF NOT EXISTS documents (
    id           TEXT PRIMARY KEY,
    sha256       TEXT,
    title        TEXT,
    title_vi     TEXT,
    source       TEXT,
    model        TEXT,
    prepared     INTEGER DEFAULT 0,
    layout_model INTEGER DEFAULT 0,
    blocks       TEXT NOT NULL,   -- JSON
    brief        TEXT,            -- JSON
    translations TEXT NOT NULL,   -- JSON {block_id: vi}
    plain        TEXT NOT NULL,   -- JSON {block_id: giải thích}
    notes        TEXT NOT NULL,   -- JSON {block_id: {...}}
    slides       TEXT,            -- JSON {deck: [...], backup: [...], minutes: int}
    highlights   TEXT,            -- JSON {block_id: [{id, col, start, end, text, note}]}
    usage        TEXT NOT NULL,   -- JSON
    created_at   REAL,
    updated_at   REAL
);
CREATE INDEX IF NOT EXISTS idx_doc_updated ON documents(updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_doc_sha     ON documents(sha256);

-- Cấu trúc bóc từ một file PDF cụ thể. Cùng hash thì cùng kết quả.
CREATE TABLE IF NOT EXISTS parse_cache (
    sha256       TEXT PRIMARY KEY,
    title        TEXT,
    blocks       TEXT NOT NULL,   -- JSON
    layout_model INTEGER DEFAULT 0,
    created_at   REAL
);

-- Bộ nhớ dịch. Khoá gồm cả model vì mỗi model dịch một giọng khác nhau.
CREATE TABLE IF NOT EXISTS tm (
    key        TEXT PRIMARY KEY,   -- sha256(text) + '|' + model
    src        TEXT NOT NULL,
    model      TEXT NOT NULL,
    vi         TEXT,
    plain      TEXT,
    hits       INTEGER DEFAULT 0,
    created_at REAL
);
CREATE INDEX IF NOT EXISTS idx_tm_model ON tm(model);
"""


def conn() -> sqlite3.Connection:
    """Mỗi luồng một kết nối — SQLite không cho dùng chung giữa các luồng."""
    c = getattr(_local, "conn", None)
    if c is None:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        c = sqlite3.connect(DB_PATH, timeout=30, check_same_thread=False)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA journal_mode=WAL")     # đọc và ghi không chặn nhau
        c.execute("PRAGMA synchronous=NORMAL")
        c.executescript(SCHEMA)
        _migrate(c)
        _local.conn = c
    return c


# Cột thêm về sau. `CREATE TABLE IF NOT EXISTS` không đụng vào bảng đã tồn tại,
# nên bài cũ trong data/papers.db sẽ thiếu cột nếu không tự thêm ở đây.
_ADDED_COLS = (("documents", "slides", "TEXT"),
               ("documents", "highlights", "TEXT"))


def _migrate(c: sqlite3.Connection) -> None:
    for table, col, decl in _ADDED_COLS:
        have = {r["name"] for r in c.execute(f"PRAGMA table_info({table})")}
        if col not in have:
            with c:
                c.execute(f"ALTER TABLE {table} ADD COLUMN {col} {decl}")


def sha(data: bytes | str) -> str:
    if isinstance(data, str):
        data = data.encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def norm(text: str) -> str:
    """Chuẩn hoá nhẹ trước khi băm: khác nhau mỗi khoảng trắng thì vẫn là một đoạn."""
    return " ".join((text or "").split())


# --------------------------------------------------------------- tài liệu

_DOC_JSON = ("blocks", "brief", "translations", "plain", "notes", "slides",
             "highlights", "usage")


def _row_to_doc(row: sqlite3.Row) -> dict:
    doc = dict(row)
    for k in _DOC_JSON:
        doc[k] = json.loads(doc[k]) if doc[k] else ({} if k != "brief" else None)
    doc["prepared"] = bool(doc["prepared"])
    doc["layout_model"] = bool(doc["layout_model"])
    doc.pop("title_vi", None)
    return doc


def save_doc(doc: dict) -> None:
    c = conn()
    payload = {
        "id": doc["id"],
        "sha256": doc.get("sha256"),
        "title": doc.get("title", ""),
        "title_vi": ((doc.get("brief") or {}).get("title_vi") or ""),
        "source": doc.get("source", ""),
        "model": doc.get("model", ""),
        "prepared": int(bool(doc.get("prepared"))),
        "layout_model": int(bool(doc.get("layout_model"))),
        "created_at": doc.get("created_at") or time.time(),
        "updated_at": time.time(),
    }
    for k in _DOC_JSON:
        payload[k] = json.dumps(doc.get(k) if doc.get(k) is not None else
                                (None if k == "brief" else {}), ensure_ascii=False)
    cols = ", ".join(payload)
    marks = ", ".join(f":{k}" for k in payload)
    with c:
        c.execute(f"INSERT OR REPLACE INTO documents ({cols}) VALUES ({marks})", payload)


def load_doc(doc_id: str) -> dict:
    row = conn().execute("SELECT * FROM documents WHERE id = ?", (doc_id,)).fetchone()
    if row is None:
        raise KeyError(doc_id)
    return _row_to_doc(row)


def doc_exists(doc_id: str) -> bool:
    return conn().execute("SELECT 1 FROM documents WHERE id = ?", (doc_id,)).fetchone() is not None


def delete_doc(doc_id: str) -> None:
    with conn() as c:
        c.execute("DELETE FROM documents WHERE id = ?", (doc_id,))


def list_docs() -> list[dict]:
    """Danh sách bài — chỉ đọc cột cần, không nạp cả nội dung như bản JSON cũ."""
    rows = conn().execute(
        "SELECT id, title, title_vi, model, updated_at, blocks, translations"
        " FROM documents ORDER BY updated_at DESC"
    ).fetchall()
    out = []
    for r in rows:
        blocks = json.loads(r["blocks"] or "[]")
        todo = [b for b in blocks if b.get("translate")]
        out.append({
            "id": r["id"],
            "title": r["title"] or r["title_vi"] or "(không tiêu đề)",
            "title_vi": r["title_vi"] or "",
            "blocks": len(blocks),
            "translated": len(json.loads(r["translations"] or "{}")),
            "translatable": len(todo),
            "model": r["model"],
            "updated_at": r["updated_at"] or 0,
        })
    return out


def doc_by_sha(sha256: str) -> dict | None:
    row = conn().execute(
        "SELECT * FROM documents WHERE sha256 = ? ORDER BY updated_at DESC LIMIT 1",
        (sha256,)).fetchone()
    return _row_to_doc(row) if row else None


# ------------------------------------------------------ cache kết quả parse


def get_parse(sha256: str) -> dict | None:
    row = conn().execute("SELECT * FROM parse_cache WHERE sha256 = ?", (sha256,)).fetchone()
    if row is None:
        return None
    return {"title": row["title"], "blocks": json.loads(row["blocks"]),
            "layout_model": bool(row["layout_model"])}


def put_parse(sha256: str, title: str, blocks: list[dict], layout_model: bool) -> None:
    with conn() as c:
        c.execute(
            "INSERT OR REPLACE INTO parse_cache (sha256, title, blocks, layout_model, created_at)"
            " VALUES (?, ?, ?, ?, ?)",
            (sha256, title, json.dumps(blocks, ensure_ascii=False), int(layout_model), time.time()))


# ------------------------------------------------------------ bộ nhớ dịch


def tm_key(text: str, model: str) -> str:
    return f"{sha(norm(text))}|{model}"


def tm_get(texts: list[str], model: str) -> dict[str, dict]:
    """Tra hàng loạt. Trả {text gốc -> {'vi':…, 'plain':…}} cho những đoạn đã có."""
    if not texts:
        return {}
    keys = {tm_key(t, model): t for t in texts}
    out: dict[str, dict] = {}
    c = conn()
    items = list(keys)
    for i in range(0, len(items), 400):          # tránh vượt giới hạn tham số của SQLite
        batch = items[i:i + 400]
        q = f"SELECT key, vi, plain FROM tm WHERE key IN ({','.join('?' * len(batch))})"
        for row in c.execute(q, batch):
            out[keys[row["key"]]] = {"vi": row["vi"] or "", "plain": row["plain"] or ""}
    if out:
        hit_keys = [tm_key(t, model) for t in out]
        with c:
            c.executemany("UPDATE tm SET hits = hits + 1 WHERE key = ?",
                          [(k,) for k in hit_keys])
    return out


def tm_put(entries: list[tuple[str, str, str]], model: str) -> None:
    """entries: [(text gốc, bản dịch, diễn giải)]. Ô rỗng thì giữ giá trị cũ."""
    rows = [e for e in entries if e[0] and (e[1] or e[2])]
    if not rows:
        return
    now = time.time()
    with conn() as c:
        c.executemany(
            "INSERT INTO tm (key, src, model, vi, plain, created_at) VALUES (?,?,?,?,?,?)"
            " ON CONFLICT(key) DO UPDATE SET"
            "   vi    = COALESCE(NULLIF(excluded.vi, ''), tm.vi),"
            "   plain = COALESCE(NULLIF(excluded.plain, ''), tm.plain)",
            [(tm_key(src, model), norm(src), model, vi, pl, now) for src, vi, pl in rows])


def stats() -> dict:
    c = conn()
    one = lambda q: c.execute(q).fetchone()[0]  # noqa: E731
    return {
        "documents": one("SELECT COUNT(*) FROM documents"),
        "parse_cached": one("SELECT COUNT(*) FROM parse_cache"),
        "tm_entries": one("SELECT COUNT(*) FROM tm"),
        "tm_hits": one("SELECT COALESCE(SUM(hits), 0) FROM tm"),
        "db_bytes": DB_PATH.stat().st_size if DB_PATH.exists() else 0,
    }
