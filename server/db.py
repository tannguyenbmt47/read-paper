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

-- Thư mục của thư viện. Quan hệ bài–thư mục nằm ở BẢNG RIÊNG chứ không phải
-- một cột `folder_id` trên `documents`: `save_doc` ghi bằng INSERT OR REPLACE
-- với danh sách cột cố định, nên cột nào nó không mang theo sẽ bị đặt lại về
-- mặc định mỗi lần lưu — tức mỗi lần lưu bản dịch là bài tự rơi khỏi thư mục,
-- im lặng. Bảng riêng thì `save_doc` không bao giờ chạm tới.
CREATE TABLE IF NOT EXISTS folders (
    id         TEXT PRIMARY KEY,
    name       TEXT NOT NULL,
    created_at REAL
);
CREATE TABLE IF NOT EXISTS doc_folder (
    doc_id    TEXT PRIMARY KEY,      -- mỗi bài nằm trong TỐI ĐA một thư mục
    folder_id TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_doc_folder ON doc_folder(folder_id);

-- Phiên bản của cùng một bài (arXiv v1 → v2, bản PDF sửa lại). Bảng riêng vì
-- cùng lý do với `doc_folder`: cột trên `documents` bị INSERT OR REPLACE của
-- `save_doc` đặt lại mỗi lần lưu.
CREATE TABLE IF NOT EXISTS doc_version (
    doc_id TEXT PRIMARY KEY,
    goc_id TEXT NOT NULL,          -- bài đầu tiên của họ phiên bản này
    so     INTEGER NOT NULL        -- 1, 2, 3…
);
CREATE INDEX IF NOT EXISTS idx_doc_version ON doc_version(goc_id);
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
        # Xoá bài phải kéo theo thứ trỏ tới nó (cùng luật với `drop_run` bên kho
        # survey): sót dòng này thì thư mục đếm cả bài đã xoá.
        c.execute("DELETE FROM doc_folder WHERE doc_id = ?", (doc_id,))
        c.execute("DELETE FROM doc_version WHERE doc_id = ?", (doc_id,))


# ------------------------------------------------------------ phiên bản

def link_version(new_id: str, goc_id: str) -> int:
    """Gắn `new_id` làm phiên bản kế tiếp của họ mà `goc_id` thuộc về.

    `goc_id` có thể là bất kỳ phiên bản nào trong họ — luôn quy về bài GỐC, nên
    nạp v3 từ v2 hay từ v1 đều ra cùng một họ. Bài gốc chưa có dòng nào thì nó
    thành v1. Trả về số phiên bản của bài mới. Cũng đưa bài mới vào đúng thư mục
    của bài gốc — hai phiên bản của một bài nằm hai chỗ là kiểu lộn xộn thư mục
    sinh ra để tránh.
    """
    with conn() as c:
        row = c.execute("SELECT goc_id FROM doc_version WHERE doc_id = ?", (goc_id,)).fetchone()
        root = row["goc_id"] if row else goc_id
        if not row:
            c.execute("INSERT OR IGNORE INTO doc_version (doc_id, goc_id, so) VALUES (?, ?, 1)",
                      (root, root))
        so = c.execute("SELECT COALESCE(MAX(so), 1) + 1 FROM doc_version WHERE goc_id = ?",
                       (root,)).fetchone()[0]
        c.execute("INSERT OR REPLACE INTO doc_version (doc_id, goc_id, so) VALUES (?, ?, ?)",
                  (new_id, root, so))
        tm = c.execute("SELECT folder_id FROM doc_folder WHERE doc_id = ?", (root,)).fetchone()
        if tm:
            c.execute("INSERT OR REPLACE INTO doc_folder (doc_id, folder_id) VALUES (?, ?)",
                      (new_id, tm["folder_id"]))
    return so


# ------------------------------------------------------------- thư mục

FOLDER_NAME_MAX = 80


def list_folders() -> list[dict]:
    """Thư mục kèm số bài, xếp theo tên (không phân biệt hoa thường)."""
    rows = conn().execute(
        "SELECT f.id, f.name, f.created_at, COUNT(d.id) AS n"
        " FROM folders f"
        " LEFT JOIN doc_folder m ON m.folder_id = f.id"
        " LEFT JOIN documents d ON d.id = m.doc_id"
        " GROUP BY f.id ORDER BY lower(f.name)").fetchall()
    return [dict(r) for r in rows]


def create_folder(folder_id: str, name: str) -> dict:
    with conn() as c:
        c.execute("INSERT INTO folders (id, name, created_at) VALUES (?, ?, ?)",
                  (folder_id, name, time.time()))
    return {"id": folder_id, "name": name, "n": 0}


def rename_folder(folder_id: str, name: str) -> bool:
    with conn() as c:
        return c.execute("UPDATE folders SET name = ? WHERE id = ?",
                         (name, folder_id)).rowcount > 0


def delete_folder(folder_id: str) -> int:
    """Xoá THƯ MỤC, không xoá bài. Bài bên trong về "Chưa xếp".

    Xoá một cái hộp mà kéo theo mọi thứ trong hộp là kiểu bất ngờ tệ nhất — nhất
    là khi thứ trong hộp là bản dịch đã trả tiền. Trả về số bài được trả lại.
    """
    with conn() as c:
        n = c.execute("DELETE FROM doc_folder WHERE folder_id = ?", (folder_id,)).rowcount
        c.execute("DELETE FROM folders WHERE id = ?", (folder_id,))
    return n


def folder_exists(folder_id: str) -> bool:
    return conn().execute("SELECT 1 FROM folders WHERE id = ?", (folder_id,)).fetchone() is not None


def move_docs(doc_ids: list[str], folder_id: str | None) -> int:
    """Chuyển bài vào thư mục; `None` là đưa về "Chưa xếp". Trả số bài đã chuyển."""
    with conn() as c:
        have = {r[0] for r in c.execute(
            f"SELECT id FROM documents WHERE id IN ({','.join('?' * len(doc_ids))})", doc_ids)}
        for d in have:
            if folder_id is None:
                c.execute("DELETE FROM doc_folder WHERE doc_id = ?", (d,))
            else:
                c.execute("INSERT OR REPLACE INTO doc_folder (doc_id, folder_id) VALUES (?, ?)",
                          (d, folder_id))
    return len(have)


def list_docs() -> list[dict]:
    """Danh sách bài — chỉ đọc cột cần, không nạp cả nội dung như bản JSON cũ."""
    rows = conn().execute(
        "SELECT d.id, d.title, d.title_vi, d.model, d.source, d.usage, d.created_at,"
        " d.updated_at, d.blocks, d.translations, m.folder_id, v.goc_id, v.so"
        " FROM documents d LEFT JOIN doc_folder m ON m.doc_id = d.id"
        " LEFT JOIN doc_version v ON v.doc_id = d.id"
        " ORDER BY d.updated_at DESC"
    ).fetchall()
    out = []
    for r in rows:
        blocks = json.loads(r["blocks"] or "[]")
        todo = [b for b in blocks if b.get("translate")]
        # `cost_usd` ở đây để hộp thoại xoá nói được MẤT GÌ — "bạn có chắc không"
        # mà không kèm cái giá thì người dùng không có cơ sở nào để chắc. Và để
        # danh sách sắp theo tiền đã bỏ ra.
        try:
            cost = float(json.loads(r["usage"] or "{}").get("cost") or 0.0)
        except (ValueError, TypeError):
            cost = 0.0
        out.append({
            "id": r["id"],
            "title": r["title"] or r["title_vi"] or "(không tiêu đề)",
            "title_vi": r["title_vi"] or "",
            "blocks": len(blocks),
            "translated": len(json.loads(r["translations"] or "{}")),
            "translatable": len(todo),
            "model": r["model"],
            "source": r["source"] or "",
            "cost_usd": round(cost, 5),
            "created_at": r["created_at"] or r["updated_at"] or 0,
            "folder_id": r["folder_id"],
            "version": r["so"],          # None = bài không có phiên bản nào khác
            "version_of": r["goc_id"],
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


def tm_drop(texts: list[str], model: str) -> int:
    """Bỏ hẳn mấy mục bộ nhớ dịch của những đoạn này.

    Cần cho đường **dịch lại một khối**: nếu không bỏ, lượt dịch lại lấy ngay
    bản cũ trong `tm` và trả về đúng cái rác người dùng vừa bấm để thay. Và bản
    rác nằm trong `tm` thì quay lại mãi mãi — mọi bài sau có đoạn y hệt đều nhận
    lại nó, miễn phí và im lặng (xem `pipeline.script_leak`).
    """
    keys = [tm_key(t, model) for t in texts if t]
    if not keys:
        return 0
    with conn() as c:
        cur = c.execute(f"DELETE FROM tm WHERE key IN ({','.join('?' * len(keys))})", keys)
    return cur.rowcount or 0


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
