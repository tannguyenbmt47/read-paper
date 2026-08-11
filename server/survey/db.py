"""Lưu trữ cho kho survey — dùng chung file `data/papers.db` với luồng đọc-hiểu.

Chung file nhưng **không chung bảng nào**: `documents` / `parse_cache` / `tm` của
luồng cũ không bị đụng tới. Chung file để đỡ phải quản hai đường dẫn, hai bản sao
lưu, hai kết nối — và để `parse_cache` dùng lại được: PDF nào đã nạp vào màn đọc
thì nạp vào kho survey là miễn phí, không chạy lại PyMuPDF.

Điểm dễ hỏng nhất ở đây là `chunk_fts`: nó là bảng **external content**
(`content='chunk'`), tức FTS5 không giữ bản sao nội dung mà đọc ngược về bảng
`chunk` qua rowid. Đổi lấy dung lượng bằng một nghĩa vụ: **mọi lần ghi vào
`chunk` phải kèm lệnh tương ứng vào `chunk_fts`**, nếu không chỉ mục và dữ liệu
lệch nhau âm thầm — tìm ra rowid không còn tồn tại, hoặc tìm không ra đoạn vẫn
đang nằm đó. Vì thế module này không cho ai ghi thẳng: chỉ có `put_chunks()` và
`drop_paper()`, cả hai đều đã lo phần chỉ mục.
"""

from __future__ import annotations

import json
import re
import time
import uuid

from .. import db

SCHEMA = """
-- Một dự án survey: một chủ đề, một kho bài, một bộ cột trích xuất.
CREATE TABLE IF NOT EXISTS survey (
    id         TEXT PRIMARY KEY,
    name       TEXT,
    topic      TEXT,
    facets     TEXT,                 -- JSON: cột của bảng trích xuất
    budget_usd REAL DEFAULT 0.5,     -- trần mỗi câu hỏi
    synth      TEXT,                 -- JSON bản tổng hợp cả kho
    synth_fp   TEXT,                 -- vân tay kho lúc dựng bản đó
    -- Model chọn riêng cho kho này. Rỗng = theo biến môi trường.
    -- `model` chỉ dùng ở khâu tổng hợp câu trả lời và dựng bản tổng hợp;
    -- `fast_model` dùng cho mọi khâu còn lại (nạp bài, lập kế hoạch, chấm lại,
    -- đọc đoạn) — đó là chỗ số lượt gọi nhiều gấp bội nên cũng là chỗ quyết
    -- định hoá đơn.
    model      TEXT,
    fast_model TEXT,
    created_at REAL,
    updated_at REAL
);

CREATE TABLE IF NOT EXISTS paper (
    id           TEXT PRIMARY KEY,   -- isalnum(), như doc_id
    survey_id    TEXT NOT NULL,
    sha256       TEXT,
    title        TEXT,
    authors      TEXT,
    year         INTEGER,
    venue        TEXT,
    doi          TEXT,
    url          TEXT,
    source       TEXT,
    n_pages      INTEGER DEFAULT 0,
    cites        INTEGER DEFAULT 0,  -- số trích dẫn, dùng ở khâu rerank
    status       TEXT,               -- new|parsed|indexed|carded|abstract_only|failed
    err          TEXT,
    card         TEXT,               -- JSON phiếu trích xuất
    loupe_doc_id TEXT,               -- bài này cũng có trong màn đọc? (id ở bảng documents)
    created_at   REAL,
    updated_at   REAL
);
CREATE INDEX IF NOT EXISTS idx_paper_survey ON paper(survey_id, created_at);
CREATE INDEX IF NOT EXISTS idx_paper_sha    ON paper(survey_id, sha256);

-- Node của cây RAPTOR. Lá (level 0) là đoạn cắt từ bài; level 1,2,3… là bản tóm
-- lược của một cụm node bên dưới. Cả cây nằm chung MỘT bảng và chung MỘT chỉ mục
-- vì cách truy vấn hiệu quả nhất là "collapsed tree": tìm trên mọi tầng cùng lúc,
-- không đi từng tầng. Câu hỏi thường cần cả chi tiết lẫn tổng quan, và bắt bộ tìm
-- chọn tầng trước khi biết câu hỏi là tự trói tay.
CREATE TABLE IF NOT EXISTS chunk (
    id       TEXT PRIMARY KEY,       -- lá "<paper_id>c<n>", tóm lược "<paper_id>s<n>"
    paper_id TEXT NOT NULL,
    ord      INTEGER,
    section  TEXT,
    page     INTEGER,
    kind     TEXT,                   -- abstract|para|table|figcap|equation|list|summary
    text     TEXT,                   -- nguyên văn, tiếng gốc
    ctx      TEXT,                   -- câu ngữ cảnh do model sinh
    vi       TEXT,                   -- bản dịch, nếu bài đã có trong màn đọc
    level    INTEGER DEFAULT 0,      -- 0 = lá, ≥1 = tầng tóm lược
    children TEXT,                   -- JSON [chunk_id] mà node này tóm lược
    -- Tiêu đề bài, chép lại ở đây dù đã có trong `paper`. Cố ý dư thừa: xem
    -- phần "bẫy external content" ở khối chú thích dưới bảng `chunk_fts`.
    title    TEXT
);
CREATE INDEX IF NOT EXISTS idx_chunk_paper ON chunk(paper_id, ord);
CREATE INDEX IF NOT EXISTS idx_chunk_level ON chunk(paper_id, level);

-- Vector của từng node. Blob float32 thô: 50 bài × 200 node × 1024 chiều ≈ 40MB,
-- nhỏ hơn hẳn cái giá phải trả khi thêm một cơ sở dữ liệu vector vào công cụ chạy
-- local. Quét toàn bộ bằng numpy cho 10k vector mất ~5ms, không cần chỉ mục ANN.
CREATE TABLE IF NOT EXISTS emb (
    chunk_id TEXT PRIMARY KEY,
    model    TEXT,
    dim      INTEGER,
    vec      BLOB
);

-- Đồ thị tri thức, dựng theo lối LazyGraphRAG: bóc thực thể + quan hệ một lượt
-- bằng model rẻ, KHÔNG dựng sẵn bản tóm lược cộng đồng. Bản tóm lược cộng đồng
-- là phần chiếm gần hết chi phí của GraphRAG đầy đủ, mà phần lớn không bao giờ
-- được đọc tới.
CREATE TABLE IF NOT EXISTS entity (
    id        TEXT PRIMARY KEY,      -- sha ngắn của (survey_id, tên đã chuẩn hoá)
    survey_id TEXT NOT NULL,
    name      TEXT,                  -- tên hiển thị, giữ nguyên dạng trong bài
    norm      TEXT,                  -- tên đã chuẩn hoá để gộp trùng
    kind      TEXT,                  -- method|dataset|metric|task|baseline|model|org|concept
    papers    INTEGER DEFAULT 0      -- số bài nhắc tới, dùng để xếp hạng
);
CREATE INDEX IF NOT EXISTS idx_entity_survey ON entity(survey_id, norm);

CREATE TABLE IF NOT EXISTS mention (
    entity_id TEXT NOT NULL,
    chunk_id  TEXT NOT NULL,
    paper_id  TEXT NOT NULL,
    PRIMARY KEY (entity_id, chunk_id)
);
CREATE INDEX IF NOT EXISTS idx_mention_chunk ON mention(chunk_id);

CREATE TABLE IF NOT EXISTS edge (
    survey_id TEXT NOT NULL,
    src       TEXT NOT NULL,         -- entity_id
    dst       TEXT NOT NULL,         -- entity_id
    rel       TEXT,                  -- đánh giá trên | tốt hơn | mở rộng | dùng | là một phần của
    paper_id  TEXT,
    chunk_id  TEXT,                  -- nơi đọc ra quan hệ này — trích dẫn được
    note      TEXT,
    PRIMARY KEY (survey_id, src, dst, rel, paper_id)
);
CREATE INDEX IF NOT EXISTS idx_edge_src ON edge(survey_id, src);
CREATE INDEX IF NOT EXISTS idx_edge_dst ON edge(survey_id, dst);

-- Chỉ mục toàn văn. `remove_diacritics 2` bóc dấu tiếng Việt đúng cách (bản 1 bỏ
-- sót các ký tự tổ hợp), nên "tăng cường" và "tang cuong" tìm ra như nhau.
--
-- MỌI CỘT Ở ĐÂY PHẢI LÀ MỘT CỘT CÓ THẬT TRONG BẢNG `chunk`, cùng tên.
--
-- Đây là bẫy đã làm hỏng cơ sở dữ liệu một lần trong lúc viết, và nó hỏng câm:
-- không lỗi lúc ghi, chỉ ném "database disk image is malformed" ở một lệnh xoá
-- nào đó rất lâu sau. Bảng external content không giữ nội dung, nên lệnh xoá
-- phải mang **đúng từng byte** nội dung cũ để FTS5 tìm ra token mà gỡ. Lúc đầu
-- `title` được truyền vào như một tham số rời (lấy từ bảng `paper`), nên chỉ cần
-- tiêu đề bài đổi giữa lúc ghi và lúc xoá là hai bên lệch nhau và chỉ mục vỡ.
--
-- Vì thế `title` được chép hẳn vào bảng `chunk`, dù `paper.title` đã có. Đổi lấy
-- một chút dư thừa để lấy hai thứ: nội dung chỉ mục trở thành **hàm thuần của
-- một dòng `chunk`** (lệch là chuyện không thể xảy ra), và `rebuild` chạy được —
-- xem `reindex()`.
CREATE VIRTUAL TABLE IF NOT EXISTS chunk_fts USING fts5(
    text, ctx, vi, title, section,
    content='chunk', content_rowid='rowid',
    tokenize='unicode61 remove_diacritics 2'
);

CREATE TABLE IF NOT EXISTS run (
    id         TEXT PRIMARY KEY,
    survey_id  TEXT NOT NULL,
    question   TEXT,
    answer     TEXT,
    steps      TEXT,                 -- JSON nhật ký từng vòng
    cites      TEXT,                 -- JSON [chunk_id]
    warns      TEXT,                 -- JSON [cảnh báo của check_answer]
    usage      TEXT,                 -- JSON
    cost       REAL DEFAULT 0,
    created_at REAL
);
CREATE INDEX IF NOT EXISTS idx_run_survey ON run(survey_id, created_at DESC);

-- Hỏi lại đúng câu cũ khi kho chưa đổi thì lấy lại miễn phí.
CREATE TABLE IF NOT EXISTS qcache (
    key        TEXT PRIMARY KEY,     -- sha(câu hỏi | vân tay kho)
    run_id     TEXT,
    created_at REAL
);
"""

# `title` không nằm trong bảng `chunk` — nó thuộc `paper`. Bảng external content
# vẫn phải có đủ cột, nên cột này luôn rỗng ở phía FTS5 và ta nhồi tiêu đề vào
# `ctx` lúc ghi. Giữ cột lại để trọng số bm25() còn chỗ mà đếm.
FTS_COLS = ("text", "ctx", "vi", "title", "section")

_ready = False


# Cột thêm về sau. `CREATE TABLE IF NOT EXISTS` không đụng vào bảng đã tồn tại,
# nên kho đã tạo trước đó sẽ thiếu cột nếu không tự thêm ở đây — cùng cái bẫy đã
# vấp với cột `slides` của bảng `documents`.
_ADDED_COLS = (("chunk", "level", "INTEGER DEFAULT 0"),
               ("chunk", "children", "TEXT"),
               ("chunk", "title", "TEXT"),
               ("survey", "synth", "TEXT"),
               ("survey", "synth_fp", "TEXT"),
               ("survey", "model", "TEXT"),
               ("survey", "fast_model", "TEXT"))


def conn():
    """Kết nối dùng chung với `server/db.py`, đã tạo sẵn bảng của kho survey."""
    global _ready
    c = db.conn()
    if not _ready:
        c.executescript(SCHEMA)
        for table, col, decl in _ADDED_COLS:
            have = {r["name"] for r in c.execute(f"PRAGMA table_info({table})")}
            if col not in have:
                with c:
                    c.execute(f"ALTER TABLE {table} ADD COLUMN {col} {decl}")
        _ready = True
    return c


def new_id(prefix: str = "") -> str:
    """Mã ngắn, chỉ chữ và số — `store` dựng đường dẫn file từ mã nên phải sạch."""
    return f"{prefix}{uuid.uuid4().hex[:10]}"


def check_id(value: str) -> str:
    """Hàng rào chống path traversal, cùng luật với `store._check`."""
    if not value or not value.isalnum():
        raise ValueError("mã không hợp lệ")
    return value


def _now() -> float:
    return time.time()


def _loads(raw, default):
    try:
        return json.loads(raw) if raw else default
    except (TypeError, ValueError):
        return default


# ------------------------------------------------------------------ dự án

DEFAULT_FACETS = [
    {"key": "tldr_vi", "label": "Tóm tắt một câu"},
    {"key": "task", "label": "Bài toán"},
    {"key": "idea", "label": "Ý tưởng chính"},
    {"key": "method", "label": "Cách làm"},
    {"key": "datasets", "label": "Dữ liệu"},
    {"key": "metrics", "label": "Chỉ số đo"},
    {"key": "results", "label": "Kết quả chính"},
    {"key": "baselines", "label": "So với ai"},
    {"key": "limitations", "label": "Giới hạn"},
]


def create_survey(name: str, topic: str = "") -> dict:
    sid = new_id()
    now = _now()
    with conn() as c:
        c.execute(
            "INSERT INTO survey (id, name, topic, facets, budget_usd, created_at, updated_at)"
            " VALUES (?,?,?,?,?,?,?)",
            (sid, name or "Kho chưa đặt tên", topic,
             json.dumps(DEFAULT_FACETS, ensure_ascii=False), 0.5, now, now))
    return load_survey(sid)


def load_survey(sid: str) -> dict:
    row = conn().execute("SELECT * FROM survey WHERE id = ?", (check_id(sid),)).fetchone()
    if row is None:
        raise KeyError(sid)
    s = dict(row)
    s["facets"] = _loads(s["facets"], DEFAULT_FACETS)
    s["synth"] = _loads(s.get("synth"), None)
    # Bản tổng hợp dựng từ phiếu của cả kho, nên thêm/bớt bài là nó cũ đi. Không
    # xoá — công đọc của người dùng nằm trong đó, và bản cũ vẫn đúng phần lớn.
    # Chỉ gắn cờ, giống `mark_stale()` bên luồng slide.
    s["synth_stale"] = bool(s.get("synth")) and s.get("synth_fp") != corpus_fingerprint(sid)
    return s


def save_synth(sid: str, synth: dict) -> None:
    with conn() as c:
        c.execute("UPDATE survey SET synth = ?, synth_fp = ?, updated_at = ? WHERE id = ?",
                  (json.dumps(synth, ensure_ascii=False), corpus_fingerprint(sid),
                   _now(), check_id(sid)))


def list_surveys() -> list[dict]:
    c = conn()
    out = []
    for row in c.execute("SELECT * FROM survey ORDER BY updated_at DESC"):
        s = dict(row)
        s["facets"] = _loads(s["facets"], DEFAULT_FACETS)
        # Bản tổng hợp dài vài nghìn chữ — không nhét vào danh sách kho, chỉ báo
        # là có hay không. Danh sách này được gọi mỗi lần mở màn hình.
        s["has_synth"] = bool(s.pop("synth", None))
        s.pop("synth_fp", None)
        s["papers"] = c.execute(
            "SELECT COUNT(*) FROM paper WHERE survey_id = ?", (s["id"],)).fetchone()[0]
        s["chunks"] = c.execute(
            "SELECT COUNT(*) FROM chunk c JOIN paper p ON p.id = c.paper_id"
            " WHERE p.survey_id = ?", (s["id"],)).fetchone()[0]
        out.append(s)
    return out


def update_survey(sid: str, **fields) -> dict:
    allowed = {"name", "topic", "facets", "budget_usd", "model", "fast_model"}
    sets, vals = [], []
    for k, v in fields.items():
        if k not in allowed:
            continue
        sets.append(f"{k} = ?")
        vals.append(json.dumps(v, ensure_ascii=False) if k == "facets" else v)
    if sets:
        sets.append("updated_at = ?")
        vals += [_now(), check_id(sid)]
        with conn() as c:
            c.execute(f"UPDATE survey SET {', '.join(sets)} WHERE id = ?", vals)
    return load_survey(sid)


def models_of(sid: str) -> tuple[str, str]:
    """(model mạnh, model rẻ) của một kho. Trống thì rơi về biến môi trường.

    Tra ở đây chứ không đọc hằng số lúc import: hằng số chốt giá trị một lần khi
    server khởi động, nên đổi model trên giao diện sẽ không có tác dụng cho tới
    lần khởi động sau — thứ người dùng không có cách nào đoán ra.
    """
    import os

    from .. import llm
    row = conn().execute("SELECT model, fast_model FROM survey WHERE id = ?",
                         (check_id(sid),)).fetchone()
    strong = (row["model"] if row else "") or os.getenv("SURVEY_MODEL") or llm.DEFAULT_MODEL
    fast = (row["fast_model"] if row else "") or os.getenv("SURVEY_FAST_MODEL") or llm.FAST_MODEL
    return strong, fast


def delete_survey(sid: str) -> None:
    check_id(sid)
    for p in list_papers(sid):
        drop_paper(p["id"])
    with conn() as c:
        c.execute("DELETE FROM run WHERE survey_id = ?", (sid,))
        c.execute("DELETE FROM survey WHERE id = ?", (sid,))


# -------------------------------------------------------------------- bài


def add_paper(survey_id: str, **f) -> str:
    pid = f.get("id") or new_id("p")
    check_id(pid)
    now = _now()
    with conn() as c:
        c.execute(
            "INSERT OR REPLACE INTO paper (id, survey_id, sha256, title, authors, year,"
            " venue, doi, url, source, n_pages, cites, status, err, card, loupe_doc_id,"
            " created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (pid, check_id(survey_id), f.get("sha256"), f.get("title", ""),
             f.get("authors", ""), f.get("year"), f.get("venue", ""), f.get("doi", ""),
             f.get("url", ""), f.get("source", ""), f.get("n_pages", 0),
             f.get("cites", 0), f.get("status", "new"), f.get("err", ""),
             json.dumps(f["card"], ensure_ascii=False) if f.get("card") else None,
             f.get("loupe_doc_id", ""), now, now))
    return pid


def update_paper(pid: str, **fields) -> None:
    allowed = {"sha256", "title", "authors", "year", "venue", "doi", "url", "source",
               "n_pages", "cites", "status", "err", "card", "loupe_doc_id"}
    sets, vals = [], []
    for k, v in fields.items():
        if k not in allowed:
            continue
        sets.append(f"{k} = ?")
        vals.append(json.dumps(v, ensure_ascii=False) if k == "card" and v is not None else v)
    if not sets:
        return
    c = conn()
    old_title = _paper_title(c, check_id(pid))
    sets.append("updated_at = ?")
    vals += [_now(), pid]
    with c:
        c.execute(f"UPDATE paper SET {', '.join(sets)} WHERE id = ?", vals)

    # Tiêu đề nằm trong chỉ mục toàn văn (và trong cột `chunk.title` để chỉ mục
    # luôn khớp), nên đổi tiêu đề mà không đồng bộ xuống là tìm theo tên bài ra
    # tên cũ mãi mãi.
    new_title = fields.get("title")
    if new_title is not None and new_title != old_title:
        with c:
            c.execute("UPDATE chunk SET title = ? WHERE paper_id = ?", (new_title, pid))
        reindex()


def _row_to_paper(row) -> dict:
    p = dict(row)
    p["card"] = _loads(p["card"], None)
    return p


def load_paper(pid: str) -> dict:
    row = conn().execute("SELECT * FROM paper WHERE id = ?", (check_id(pid),)).fetchone()
    if row is None:
        raise KeyError(pid)
    return _row_to_paper(row)


def list_papers(survey_id: str) -> list[dict]:
    rows = conn().execute(
        "SELECT * FROM paper WHERE survey_id = ? ORDER BY created_at",
        (check_id(survey_id),)).fetchall()
    return [_row_to_paper(r) for r in rows]


def paper_by_sha(survey_id: str, sha256: str) -> dict | None:
    row = conn().execute(
        "SELECT * FROM paper WHERE survey_id = ? AND sha256 = ? LIMIT 1",
        (check_id(survey_id), sha256)).fetchone()
    return _row_to_paper(row) if row else None


def drop_paper(pid: str) -> None:
    """Xoá bài kèm mọi đoạn của nó — và kèm cả chỉ mục của những đoạn đó."""
    check_id(pid)
    c = conn()
    rows = c.execute("SELECT rowid, * FROM chunk WHERE paper_id = ?", (pid,)).fetchall()
    with c:
        for r in rows:
            _fts_write(c, "delete", r["rowid"], r)
            c.execute("DELETE FROM emb WHERE chunk_id = ?", (r["id"],))
        c.execute("DELETE FROM chunk WHERE paper_id = ?", (pid,))
        c.execute("DELETE FROM mention WHERE paper_id = ?", (pid,))
        c.execute("DELETE FROM edge WHERE paper_id = ?", (pid,))
        c.execute("DELETE FROM paper WHERE id = ?", (pid,))
        # Thực thể không còn bài nào nhắc tới thì bỏ luôn, nếu không đồ thị đầy
        # node mồ côi và bảng tra tên trả về thứ không dẫn tới đâu.
        # Đếm giống hệt `put_graph` — hai chỗ lệch luật thì xoá nhầm thực thể
        # đang còn cạnh, và cạnh đó biến mất theo.
        c.execute(
            "UPDATE entity SET papers = ("
            "  SELECT COUNT(DISTINCT pid) FROM ("
            "    SELECT paper_id AS pid FROM mention WHERE mention.entity_id = entity.id"
            "    UNION SELECT paper_id FROM edge WHERE edge.src = entity.id"
            "    UNION SELECT paper_id FROM edge WHERE edge.dst = entity.id))")
        c.execute("DELETE FROM entity WHERE papers = 0")


# ------------------------------------------------------------------ đoạn
#
# Bảng external content: FTS5 chỉ giữ chỉ mục đảo, nội dung nằm ở bảng `chunk`.
# Xoá một dòng thì phải tự tay đẩy bản ghi 'delete' mang **đúng nội dung cũ** vào
# `chunk_fts`, nếu không token của dòng đó còn nằm lại trong chỉ mục vĩnh viễn.


_FTS_FIELDS = ("text", "ctx", "vi", "title", "section")


def _fts_write(c, op: str, rowid: int, row) -> None:
    """Ghi hoặc gỡ một dòng khỏi chỉ mục, lấy giá trị **thẳng từ dòng `chunk`**.

    Cả `insert` lẫn `delete` đều đi qua đây và đều đọc cùng một nguồn, nên hai bên
    không thể lệch nhau. Đó là toàn bộ lý do hàm này tồn tại — xem khối chú thích
    ở `chunk_fts` trong SCHEMA cho câu chuyện đã hỏng như thế nào.

    Với `delete`, giá trị truyền vào phải là **nội dung CŨ** (đọc dòng ra trước
    khi UPDATE/DELETE), không phải nội dung mới.
    """
    vals = [(row[f] if f in row.keys() else "") or "" for f in _FTS_FIELDS]
    cols = ", ".join(_FTS_FIELDS)
    marks = ", ".join("?" * len(_FTS_FIELDS))
    if op == "insert":
        c.execute(f"INSERT INTO chunk_fts (rowid, {cols}) VALUES (?, {marks})",
                  (rowid, *vals))
    else:
        c.execute(f"INSERT INTO chunk_fts (chunk_fts, rowid, {cols})"
                  f" VALUES ('delete', ?, {marks})", (rowid, *vals))


def _paper_title(c, paper_id: str) -> str:
    row = c.execute("SELECT title FROM paper WHERE id = ?", (paper_id,)).fetchone()
    return (row["title"] if row else "") or ""


def reindex() -> int:
    """Dựng lại toàn bộ chỉ mục toàn văn từ bảng `chunk`.

    Chạy được vì mọi cột của `chunk_fts` đều là cột có thật trong `chunk` — đó là
    phần thưởng của việc chép `title` xuống. Dùng hai chỗ:

    - **sửa chữa**: chỉ mục lệch vì bất cứ lý do gì thì một lệnh này đưa nó về
      đúng, không phải nạp lại bài nào;
    - **sau khi đổi tiêu đề bài**, vì tiêu đề nằm trong chỉ mục.

    Không có bản chỉ dựng lại một bài: FTS5 chỉ cho `rebuild` cả bảng. Với cỡ kho
    vài chục bài thì việc đó tính bằng giây, nên không đáng viết đường riêng.
    """
    c = conn()
    with c:
        c.execute("INSERT INTO chunk_fts (chunk_fts) VALUES ('rebuild')")
    return c.execute("SELECT COUNT(*) FROM chunk").fetchone()[0]


def integrity() -> str:
    """Soát chỉ mục. Trả chuỗi rỗng nếu lành, ngược lại là lý do.

    `with c:` không phải trang trí: câu lệnh soát này là DML dưới mắt sqlite3, nên
    thiếu nó thì một giao dịch ghi mở ra và **không bao giờ đóng**. Kết nối là
    thread-local, nên luồng khác đụng vào sẽ nhận "database is locked" ở một chỗ
    chẳng liên quan gì — đã vấp đúng như vậy khi soát bằng TestClient.
    """
    try:
        c = conn()
        with c:
            c.execute("INSERT INTO chunk_fts (chunk_fts) VALUES ('integrity-check')")
        return ""
    except Exception as e:                  # noqa: BLE001
        return f"{type(e).__name__}: {e}"[:200]


def put_chunks(paper_id: str, chunks: list[dict], title: str = "") -> None:
    """Ghi lại toàn bộ **lá** của một bài, chỉ mục đi kèm trong cùng giao dịch.

    `chunks`: [{ord, section, page, kind, text, ctx, vi}] — `id` tự sinh theo
    `<paper_id>c<ord>` để trích dẫn trong câu trả lời đọc được bằng mắt.

    Xoá sạch cả cây tóm lược cũ: cây dựng từ lá, lá đổi thì cây cũ hết đúng.
    """
    check_id(paper_id)
    c = conn()
    old = c.execute("SELECT rowid, * FROM chunk WHERE paper_id = ?", (paper_id,)).fetchall()
    ptitle = title or _paper_title(c, paper_id)
    with c:
        # Gỡ bản cũ khỏi chỉ mục TRƯỚC khi xoá khỏi bảng: bản ghi 'delete' cần
        # đúng nội dung cũ để FTS5 tìm ra token, mà nội dung đó chỉ còn ở đây.
        for r in old:
            _fts_write(c, "delete", r["rowid"], r)
            c.execute("DELETE FROM emb WHERE chunk_id = ?", (r["id"],))
        c.execute("DELETE FROM chunk WHERE paper_id = ?", (paper_id,))
        for ch in chunks:
            cid = f"{paper_id}c{ch['ord']}"
            cur = c.execute(
                "INSERT INTO chunk (id, paper_id, ord, section, page, kind, text, ctx,"
                " vi, level, children, title) VALUES (?,?,?,?,?,?,?,?,?,0,NULL,?)",
                (cid, paper_id, ch["ord"], ch.get("section", ""), ch.get("page", 0),
                 ch.get("kind", "para"), ch.get("text", ""), ch.get("ctx", ""),
                 ch.get("vi", ""), ptitle))
            _fts_write(c, "insert", cur.lastrowid,
                       c.execute("SELECT * FROM chunk WHERE rowid = ?",
                                 (cur.lastrowid,)).fetchone())


def put_summaries(paper_id: str, nodes: list[dict]) -> None:
    """Ghi các tầng tóm lược của cây RAPTOR, thay hết tầng cũ.

    `nodes`: [{ord, level, section, text, ctx, children: [chunk_id]}].
    Mã node là `<paper_id>s<ord>` — chữ `s` phân biệt với lá `c`, và vẫn `isalnum()`
    nên vẫn đi qua được hàng rào `check_id`.
    """
    check_id(paper_id)
    c = conn()
    ptitle = _paper_title(c, paper_id)
    old = c.execute("SELECT rowid, * FROM chunk WHERE paper_id = ? AND level > 0",
                    (paper_id,)).fetchall()
    with c:
        for r in old:
            _fts_write(c, "delete", r["rowid"], r)
            c.execute("DELETE FROM emb WHERE chunk_id = ?", (r["id"],))
        c.execute("DELETE FROM chunk WHERE paper_id = ? AND level > 0", (paper_id,))
        for n in nodes:
            cid = f"{paper_id}s{n['ord']}"
            cur = c.execute(
                "INSERT INTO chunk (id, paper_id, ord, section, page, kind, text, ctx,"
                " vi, level, children, title) VALUES (?,?,?,?,0,'summary',?,?,'',?,?,?)",
                (cid, paper_id, n["ord"], n.get("section", ""), n.get("text", ""),
                 n.get("ctx", ""), n["level"],
                 json.dumps(n.get("children") or [], ensure_ascii=False), ptitle))
            _fts_write(c, "insert", cur.lastrowid,
                       c.execute("SELECT * FROM chunk WHERE rowid = ?",
                                 (cur.lastrowid,)).fetchone())


def set_ctx(paper_id: str, ctx_by_ord: dict[int, str]) -> None:
    """Nhét câu ngữ cảnh vào các đoạn đã có, và đánh chỉ mục lại đúng chúng."""
    check_id(paper_id)
    c = conn()
    rows = c.execute("SELECT rowid, * FROM chunk WHERE paper_id = ? AND level = 0",
                     (paper_id,)).fetchall()
    with c:
        for r in rows:
            new = ctx_by_ord.get(r["ord"])
            if not new or new == (r["ctx"] or ""):
                continue
            _fts_write(c, "delete", r["rowid"], r)      # nội dung CŨ, đọc trước khi UPDATE
            c.execute("UPDATE chunk SET ctx = ? WHERE rowid = ?", (new, r["rowid"]))
            _fts_write(c, "insert", r["rowid"],
                       c.execute("SELECT * FROM chunk WHERE rowid = ?",
                                 (r["rowid"],)).fetchone())


def get_chunks(ids: list[str]) -> dict[str, dict]:
    """Tra hàng loạt theo mã đoạn, kèm metadata của bài để dựng trích dẫn."""
    if not ids:
        return {}
    c = conn()
    out: dict[str, dict] = {}
    ids = [i for i in ids if i and i.isalnum()]
    for i in range(0, len(ids), 400):     # SQLite giới hạn số tham số một câu lệnh
        batch = ids[i:i + 400]
        q = ("SELECT ch.*, p.title AS paper_title, p.year, p.survey_id, p.cites,"
             " p.loupe_doc_id FROM chunk ch JOIN paper p ON p.id = ch.paper_id"
             f" WHERE ch.id IN ({','.join('?' * len(batch))})")
        for row in c.execute(q, batch):
            d = dict(row)
            d["children"] = _loads(d.get("children"), [])
            out[row["id"]] = d
    return out


def paper_chunks(paper_id: str, level: int | None = 0) -> list[dict]:
    """`level=0` lấy lá, `level=None` lấy cả cây."""
    q = "SELECT * FROM chunk WHERE paper_id = ?"
    args: list = [check_id(paper_id)]
    if level is not None:
        q += " AND level = ?"
        args.append(level)
    return [dict(r) for r in conn().execute(q + " ORDER BY level, ord", args)]


def survey_chunk_ids(survey_id: str, level: int | None = None) -> list[str]:
    q = ("SELECT ch.id FROM chunk ch JOIN paper p ON p.id = ch.paper_id"
         " WHERE p.survey_id = ?")
    args: list = [check_id(survey_id)]
    if level is not None:
        q += " AND ch.level = ?"
        args.append(level)
    return [r[0] for r in conn().execute(q, args)]


# ------------------------------------------------------------ vector (dense)


def put_vecs(rows: list[tuple[str, bytes]], model: str, dim: int) -> None:
    if not rows:
        return
    with conn() as c:
        c.executemany(
            "INSERT OR REPLACE INTO emb (chunk_id, model, dim, vec) VALUES (?,?,?,?)",
            [(cid, model, dim, vec) for cid, vec in rows])


def missing_vecs(survey_id: str, model: str) -> list[dict]:
    """Node chưa có vector cho model này — nạp lại không phải tính lại từ đầu."""
    rows = conn().execute(
        "SELECT ch.id, ch.text, ch.ctx, ch.section FROM chunk ch"
        " JOIN paper p ON p.id = ch.paper_id"
        " LEFT JOIN emb e ON e.chunk_id = ch.id AND e.model = ?"
        " WHERE p.survey_id = ? AND e.chunk_id IS NULL",
        (model, check_id(survey_id))).fetchall()
    return [dict(r) for r in rows]


def all_vecs(survey_id: str, model: str) -> tuple[list[str], list[bytes], int]:
    rows = conn().execute(
        "SELECT e.chunk_id, e.vec, e.dim FROM emb e"
        " JOIN chunk ch ON ch.id = e.chunk_id"
        " JOIN paper p ON p.id = ch.paper_id"
        " WHERE p.survey_id = ? AND e.model = ?",
        (check_id(survey_id), model)).fetchall()
    if not rows:
        return [], [], 0
    return [r["chunk_id"] for r in rows], [r["vec"] for r in rows], rows[0]["dim"]


def paper_vecs(paper_id: str, model: str) -> tuple[list[str], list[bytes], int]:
    rows = conn().execute(
        "SELECT chunk_id, vec, dim FROM emb e JOIN chunk ch ON ch.id = e.chunk_id"
        " WHERE ch.paper_id = ? AND e.model = ? AND ch.level = 0 ORDER BY ch.ord",
        (check_id(paper_id), model)).fetchall()
    if not rows:
        return [], [], 0
    return [r["chunk_id"] for r in rows], [r["vec"] for r in rows], rows[0]["dim"]


# ----------------------------------------------------------------- đồ thị


def ent_id(survey_id: str, norm_name: str) -> str:
    return "e" + db.sha(f"{survey_id}|{norm_name}")[:14]


def put_graph(survey_id: str, paper_id: str, ents: list[dict], edges: list[dict]) -> None:
    """Ghi thực thể và quan hệ bóc từ một bài. Thực thể trùng tên thì gộp."""
    check_id(survey_id)
    with conn() as c:
        c.execute("DELETE FROM edge WHERE survey_id = ? AND paper_id = ?",
                  (survey_id, paper_id))
        c.execute("DELETE FROM mention WHERE paper_id = ?", (paper_id,))
        for e in ents:
            eid = ent_id(survey_id, e["norm"])
            c.execute(
                "INSERT INTO entity (id, survey_id, name, norm, kind, papers)"
                " VALUES (?,?,?,?,?,0) ON CONFLICT(id) DO UPDATE SET"
                "   kind = COALESCE(NULLIF(entity.kind,''), excluded.kind)",
                (eid, survey_id, e["name"], e["norm"], e.get("kind", "concept")))
            for cid in e.get("chunks") or []:
                c.execute("INSERT OR IGNORE INTO mention (entity_id, chunk_id, paper_id)"
                          " VALUES (?,?,?)", (eid, cid, paper_id))
        for g in edges:
            c.execute(
                "INSERT OR REPLACE INTO edge (survey_id, src, dst, rel, paper_id,"
                " chunk_id, note) VALUES (?,?,?,?,?,?,?)",
                (survey_id, ent_id(survey_id, g["src"]), ent_id(survey_id, g["dst"]),
                 g.get("rel", ""), paper_id, g.get("chunk", ""), g.get("note", "")))
        # `papers` là số bài nhắc tới — tính lại cho gọn thay vì cộng dồn, vì bài
        # bị xoá thì bộ đếm cộng dồn không bao giờ giảm.
        #
        # Đếm cả từ `edge`, không chỉ từ `mention`: model hay khai một thực thể
        # trong quan hệ mà quên gắn mã đoạn cho nó ở phần `entities`. Chỉ đếm
        # mention thì thực thể ấy có `papers = 0`, bị `graph_overview` lọc bỏ, và
        # **mọi cạnh chạm vào nó biến mất theo** — đồ thị mất quan hệ mà không
        # báo gì. Một bài phát biểu quan hệ về thực thể nào thì đúng là có nhắc
        # tới thực thể đó.
        c.execute(
            "UPDATE entity SET papers = ("
            "  SELECT COUNT(DISTINCT pid) FROM ("
            "    SELECT paper_id AS pid FROM mention WHERE mention.entity_id = entity.id"
            "    UNION SELECT paper_id FROM edge WHERE edge.src = entity.id"
            "    UNION SELECT paper_id FROM edge WHERE edge.dst = entity.id))"
            " WHERE survey_id = ?", (survey_id,))


def entities_of(chunk_ids: list[str]) -> dict[str, list[dict]]:
    """Thực thể xuất hiện trong từng đoạn — đầu vào của bước mở rộng theo đồ thị."""
    if not chunk_ids:
        return {}
    out: dict[str, list[dict]] = {}
    c = conn()
    for i in range(0, len(chunk_ids), 400):
        batch = chunk_ids[i:i + 400]
        q = ("SELECT m.chunk_id, e.id, e.name, e.kind, e.papers FROM mention m"
             " JOIN entity e ON e.id = m.entity_id"
             f" WHERE m.chunk_id IN ({','.join('?' * len(batch))})")
        for r in c.execute(q, batch):
            out.setdefault(r["chunk_id"], []).append(
                {"id": r["id"], "name": r["name"], "kind": r["kind"], "papers": r["papers"]})
    return out


def find_entities(survey_id: str, names: list[str], limit: int = 12) -> list[dict]:
    """Tra thực thể theo tên gần đúng — cầu nối từ từ khoá của câu hỏi vào đồ thị."""
    if not names:
        return []
    c = conn()
    seen: dict[str, dict] = {}
    for n in names[:20]:
        key = norm_name(n)
        if not key:
            continue
        for r in c.execute(
                "SELECT * FROM entity WHERE survey_id = ? AND (norm = ? OR norm LIKE ?)"
                " ORDER BY papers DESC LIMIT 5",
                (check_id(survey_id), key, f"%{key}%")):
            seen[r["id"]] = dict(r)
    return sorted(seen.values(), key=lambda e: -e["papers"])[:limit]


def neighbours(survey_id: str, entity_ids: list[str], limit: int = 60) -> list[dict]:
    """Cạnh chạm tới các thực thể này, kèm mã đoạn để còn trích dẫn được."""
    if not entity_ids:
        return []
    ids = entity_ids[:30]
    marks = ",".join("?" * len(ids))
    rows = conn().execute(
        f"SELECT e.*, s.name AS src_name, s.kind AS src_kind, d.name AS dst_name,"
        f" d.kind AS dst_kind, p.title AS paper_title, p.year FROM edge e"
        f" JOIN entity s ON s.id = e.src JOIN entity d ON d.id = e.dst"
        f" JOIN paper p ON p.id = e.paper_id"
        f" WHERE e.survey_id = ? AND (e.src IN ({marks}) OR e.dst IN ({marks}))"
        f" LIMIT ?", (check_id(survey_id), *ids, *ids, limit)).fetchall()
    return [dict(r) for r in rows]


def graph_overview(survey_id: str, limit: int = 60) -> dict:
    """Thực thể xuất hiện ở nhiều bài nhất + cạnh giữa chúng — dựng để vẽ."""
    c = conn()
    ents = [dict(r) for r in c.execute(
        "SELECT * FROM entity WHERE survey_id = ? AND papers > 0"
        " ORDER BY papers DESC, name LIMIT ?", (check_id(survey_id), limit))]
    ids = {e["id"] for e in ents}
    edges = []
    for r in c.execute(
            "SELECT e.src, e.dst, e.rel, e.paper_id, e.chunk_id, e.note FROM edge e"
            " WHERE e.survey_id = ?", (survey_id,)):
        if r["src"] in ids and r["dst"] in ids:
            edges.append(dict(r))
    return {"entities": ents, "edges": edges}


_NORM_STRIP = re.compile(r"[^a-z0-9À-ỹ ]+")


def norm_name(s: str) -> str:
    """Chuẩn hoá tên thực thể để gộp trùng: thường hoá, bỏ dấu câu, gộp khoảng trắng."""
    return " ".join(_NORM_STRIP.sub(" ", (s or "").lower()).split())[:80]


def count_chunks(survey_id: str) -> int:
    return conn().execute(
        "SELECT COUNT(*) FROM chunk ch JOIN paper p ON p.id = ch.paper_id"
        " WHERE p.survey_id = ?", (check_id(survey_id),)).fetchone()[0]


# --------------------------------------------------------------- tìm kiếm

# FTS5 coi các ký tự này là cú pháp truy vấn. Câu hỏi của người dùng không phải
# biểu thức truy vấn, nên bọc từng từ trong nháy kép và bỏ hẳn ký tự cú pháp —
# thiếu bước này thì một dấu ngoặc trong câu hỏi làm cả truy vấn ném lỗi.
_FTS_STRIP = re.compile(r'["*():^\-]+')


def fts_query(text: str) -> str:
    words = [w for w in _FTS_STRIP.sub(" ", text or "").split() if len(w) > 1]
    return " OR ".join(f'"{w}"' for w in words[:60])


def bm25(survey_id: str, query: str, limit: int = 30,
         weights: tuple = (4.0, 2.0, 2.0, 6.0, 1.0)) -> list[tuple[str, float]]:
    """Tìm BM25 trong phạm vi một dự án. Trả [(chunk_id, điểm)] tốt nhất trước.

    `bm25()` của SQLite trả điểm **âm**, càng nhỏ càng khớp — nên sắp tăng dần.
    Trọng số theo thứ tự cột của `chunk_fts`: text, ctx, vi, title, section.
    Tiêu đề nặng nhất vì hỏi tên phương pháp là trường hợp phổ biến nhất.
    """
    q = fts_query(query)
    if not q:
        return []
    sql = (
        "SELECT ch.id AS cid, bm25(chunk_fts, ?, ?, ?, ?, ?) AS score"
        " FROM chunk_fts JOIN chunk ch ON ch.rowid = chunk_fts.rowid"
        " JOIN paper p ON p.id = ch.paper_id"
        " WHERE chunk_fts MATCH ? AND p.survey_id = ?"
        " ORDER BY score LIMIT ?")
    try:
        rows = conn().execute(sql, (*weights, q, check_id(survey_id), limit)).fetchall()
    except Exception:            # noqa: BLE001 — truy vấn hỏng thì coi như không tìm ra
        return []
    return [(r["cid"], r["score"]) for r in rows]


# ------------------------------------------------------- lượt hỏi và cache


def save_run(survey_id: str, question: str, answer: str, steps: list, cites: list,
             warns: list, usage: dict, cost: float, run_id: str = "") -> str:
    rid = run_id or new_id("r")
    with conn() as c:
        c.execute(
            "INSERT OR REPLACE INTO run (id, survey_id, question, answer, steps, cites,"
            " warns, usage, cost, created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (rid, check_id(survey_id), question, answer,
             json.dumps(steps, ensure_ascii=False), json.dumps(cites, ensure_ascii=False),
             json.dumps(warns, ensure_ascii=False), json.dumps(usage, ensure_ascii=False),
             cost, _now()))
    return rid


def load_run(rid: str) -> dict | None:
    row = conn().execute("SELECT * FROM run WHERE id = ?", (check_id(rid),)).fetchone()
    if row is None:
        return None
    r = dict(row)
    for k in ("steps", "cites", "warns", "usage"):
        r[k] = _loads(r[k], [] if k != "usage" else {})
    return r


def list_runs(survey_id: str, limit: int = 40) -> list[dict]:
    rows = conn().execute(
        "SELECT id, question, cost, created_at, length(answer) AS n FROM run"
        " WHERE survey_id = ? ORDER BY created_at DESC LIMIT ?",
        (check_id(survey_id), limit)).fetchall()
    return [dict(r) for r in rows]


def corpus_fingerprint(survey_id: str) -> str:
    """Vân tay của kho: đổi khi thêm/bớt bài hoặc bóc lại phiếu.

    Dùng làm một nửa khoá của `qcache` — hỏi lại câu cũ mà kho chưa đổi thì trả
    lại kết quả cũ, đổi rồi thì hỏi lại thật.
    """
    rows = conn().execute(
        "SELECT id, updated_at, status FROM paper WHERE survey_id = ? ORDER BY id",
        (check_id(survey_id),)).fetchall()
    return db.sha("|".join(f"{r['id']}:{r['updated_at']}:{r['status']}" for r in rows))


def qcache_key(survey_id: str, question: str) -> str:
    return db.sha(f"{corpus_fingerprint(survey_id)}|{db.norm(question).lower()}")


def qcache_get(key: str) -> dict | None:
    row = conn().execute("SELECT run_id FROM qcache WHERE key = ?", (key,)).fetchone()
    return load_run(row["run_id"]) if row else None


def qcache_put(key: str, run_id: str) -> None:
    with conn() as c:
        c.execute("INSERT OR REPLACE INTO qcache (key, run_id, created_at) VALUES (?,?,?)",
                  (key, run_id, _now()))


def stats(survey_id: str) -> dict:
    c = conn()
    papers = list_papers(survey_id)
    return {
        "papers": len(papers),
        "indexed": sum(1 for p in papers if p["status"] in ("indexed", "carded")),
        "carded": sum(1 for p in papers if p.get("card")),
        "chunks": count_chunks(survey_id),
        "runs": c.execute("SELECT COUNT(*) FROM run WHERE survey_id = ?",
                          (check_id(survey_id),)).fetchone()[0],
        "spent": c.execute("SELECT COALESCE(SUM(cost), 0) FROM run WHERE survey_id = ?",
                           (survey_id,)).fetchone()[0],
    }
