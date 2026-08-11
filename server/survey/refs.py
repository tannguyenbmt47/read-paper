"""Hồ sơ đối chiếu: bài này đứng ở đâu so với những bài nó dẫn — **giá $0**.

Muốn hiểu một bài phương pháp thì phải biết nó khác những bài trước ở chỗ nào.
Cách hiển nhiên là tải về ba mươi bài tham khảo rồi bắt model đọc hết. Cách đó
**không khả thi**: ba mươi PDF, ba mươi lượt bóc, và vài trăm nghìn token đọc
vào cho một bài giảng.

Cách này khai thác một thứ đã có sẵn và không ai dùng: **chính tác giả đã viết
ra vì sao họ dẫn bài kia.** Câu văn quanh chỗ trích dẫn (*citation context*) nói
đúng cái ý mà bài này lấy từ bài đó — không hơn. Đó chính là "chỉ lấy ý cần
lấy", và nó đã nằm sẵn trong bài, không phải sinh ra.

Semantic Scholar phát không thứ đó qua Graph API, **không cần key**:

| Trường | Được gì | Thay cho |
|---|---|---|
| `contexts` | nguyên văn câu chứa chỗ trích dẫn | đọc cả bài được dẫn |
| `intents` | background / methodology / result | đoán vai trò của bài đó |
| `isInfluential` | bài này có thật sự dựa vào bài kia không | đếm số lần dẫn |
| `tldr` | tóm tắt một câu, model SciTLDR dựng sẵn | một lượt gọi model mỗi bài |

Đo trên bài LAPA (2410.11758): 63 tham khảo, **58 có câu trích dẫn**, 4 được
đánh dấu ảnh hưởng — lấy về hết bằng **hai** request HTTP, không đồng nào.

`intents` phụ thuộc việc S2 có toàn văn bài dẫn hay không, và với bản tiền ấn
arXiv thì thường **rỗng** (đo đúng trên LAPA: 0/63). Nên xếp hạng không được
dựa vào nó — `contexts` mới là tín hiệu chắc, còn `intents` chỉ là gia vị.
"""

from __future__ import annotations

import asyncio
import json
import re
import time

import httpx

from . import db

# CHỈ ASCII — xem chú thích ở `sources.UA`, một chữ có dấu ở đây làm request
# chết trước khi rời máy.
UA = {"User-Agent": "Loupe-Survey/1.0 (local paper reading tool)"}
API = "https://api.semanticscholar.org/graph/v1"
TIMEOUT = 30

# Không key thì S2 cho 100 request / 5 phút. Ta tiêu đúng 2 request mỗi bài, nên
# giới hạn đó rộng rãi — nhưng vẫn phải nghỉ giữa các lần thử lại.
RETRY = 3
BACKOFF = 2.0

REF_FIELDS = ("title,year,abstract,citationCount,externalIds,authors,venue,"
              "contexts,intents,isInfluential")
BATCH_FIELDS = "title,tldr,abstract,year,citationCount,venue,externalIds"

# Bao nhiêu bài dẫn được đưa vào hồ sơ. Mười là chỗ cân bằng: đủ để nói "khác
# nhóm A ở chỗ này, khác nhóm B ở chỗ kia", mà vẫn gọn để nhét vừa một prompt
# cùng với toàn văn bài chính.
TOP_REFS = 12

_ARXIV = re.compile(r"arxiv\.org/(?:abs|pdf)/([0-9]{4}\.[0-9]{4,5})", re.I)
_ARXIV_BARE = re.compile(r"\b([0-9]{4}\.[0-9]{4,5})\b")


def _norm_title(t: str) -> str:
    return " ".join(re.sub(r"[^a-z0-9 ]+", " ", (t or "").lower()).split())


def arxiv_id(paper: dict) -> str:
    """Mã arXiv suy từ url hoặc doi, nếu có. Rẻ hơn hẳn việc khớp theo tiêu đề."""
    for field in ("url", "doi"):
        m = _ARXIV.search(paper.get(field) or "") or _ARXIV_BARE.search(paper.get(field) or "")
        if m:
            return m.group(1)
    return ""


async def _get(client: httpx.AsyncClient, path: str, params: dict) -> dict | list | None:
    """GET có thử lại. Trả None khi chịu thua — thiếu hồ sơ đối chiếu thì bài
    giảng vẫn viết được, chỉ nhạt hơn; ném lỗi lên thì hỏng cả bài giảng."""
    for attempt in range(RETRY):
        try:
            r = await client.get(f"{API}{path}", params=params, headers=UA, timeout=TIMEOUT)
            if r.status_code == 429:            # quá tay, nghỉ rồi thử lại
                await asyncio.sleep(BACKOFF * (attempt + 1))
                continue
            if r.status_code >= 400:
                return None
            return r.json()
        except Exception:                       # noqa: BLE001 — mạng hỏng, xem như không có
            if attempt == RETRY - 1:
                return None
            await asyncio.sleep(BACKOFF * (attempt + 1))
    return None


async def resolve(client: httpx.AsyncClient, paper: dict) -> str:
    """Tìm mã S2 của bài. Ưu tiên arXiv id, không có thì khớp theo tiêu đề.

    `/paper/search/match` trả **đúng một** bài khớp nhất kèm `matchScore`, khác
    `/paper/search` vốn trả cả danh sách gần đúng. Ở đây ta cần đúng bài này
    chứ không cần bài na ná, nên `match` là endpoint đúng.
    """
    aid = arxiv_id(paper)
    if aid:
        return f"arXiv:{aid}"

    title = (paper.get("title") or "").strip()
    if len(title) < 12:
        return ""
    got = await _get(client, "/paper/search/match",
                     {"query": title, "fields": "title,externalIds"})
    rows = (got or {}).get("data") if isinstance(got, dict) else None
    if not rows:
        return ""
    hit = rows[0]
    # `match` khớp mờ, nên vẫn phải tự soát: tiêu đề trả về mà lệch hẳn thì thà
    # không có hồ sơ còn hơn dựng bài giảng đối chiếu với NHẦM bài.
    a, b = _norm_title(title), _norm_title(hit.get("title") or "")
    if not (a in b or b in a or _overlap(a, b) >= 0.6):
        return ""
    return hit.get("paperId") or ""


def _overlap(a: str, b: str) -> float:
    sa, sb = set(a.split()), set(b.split())
    return len(sa & sb) / max(1, min(len(sa), len(sb)))


def _score(ref: dict, in_corpus: bool) -> float:
    """Xếp hạng bài dẫn theo mức độ đáng đưa vào bài giảng.

    Số câu trích dẫn nặng nhất, vì nó đo trực tiếp thứ ta cần: bài chính **nói
    về** bài kia bao nhiêu. Số trích dẫn toàn cầu chỉ là mồi phụ (log, hệ số
    nhỏ) — bài kinh điển thì đáng nhắc, nhưng một bài nền tảng bị dẫn qua loa
    không giúp gì cho việc hiểu bài chính.
    """
    cited = ref.get("citedPaper") or {}
    n_ctx = len(ref.get("contexts") or [])
    s = 2.2 * min(n_ctx, 4)
    if ref.get("isInfluential"):
        s += 4.0
    if "methodology" in (ref.get("intents") or []):
        s += 1.5
    if "result" in (ref.get("intents") or []):
        s += 1.0
    if in_corpus:                       # bài này cũng nằm trong kho → đối chiếu được sâu
        s += 3.0
    import math
    s += 0.4 * math.log1p(cited.get("citationCount") or 0)
    return s


def _clean_ctx(s: str) -> str:
    """Câu trích dẫn bóc từ PDF nên dính lỗi bóc: ligature, gạch nối cuối dòng."""
    s = (s or "").replace("ﬁ", "fi").replace("ﬂ", "fl").replace("ﬀ", "ff")
    s = re.sub(r"-\s*\n\s*", "", s)
    return " ".join(s.split())


async def dossier(paper: dict, corpus_titles: dict[str, str] | None = None,
                  top: int = TOP_REFS) -> dict:
    """Hồ sơ đối chiếu của một bài. **Không gọi model, không tốn tiền.**

    `corpus_titles` là {tiêu đề đã chuẩn hoá: paper_id} của các bài khác trong
    kho — bài dẫn nào cũng có trong kho thì đánh dấu `in_corpus`, vì lúc đó bài
    giảng đối chiếu được bằng nội dung thật chứ không chỉ bằng tóm tắt.
    """
    out: dict = {"s2_id": "", "n_refs": 0, "refs": [], "fetched_at": time.time()}
    corpus_titles = corpus_titles or {}

    async with httpx.AsyncClient(follow_redirects=True) as client:
        sid = await resolve(client, paper)
        if not sid:
            return out
        out["s2_id"] = sid

        got = await _get(client, f"/paper/{sid}/references",
                         {"fields": REF_FIELDS, "limit": 100})
        rows = (got or {}).get("data") if isinstance(got, dict) else None
        if not rows:
            return out
        out["n_refs"] = len(rows)

        ranked = []
        for r in rows:
            cited = r.get("citedPaper") or {}
            if not cited.get("title"):
                continue
            pid = corpus_titles.get(_norm_title(cited["title"]))
            ranked.append((_score(r, bool(pid)), r, pid))
        ranked.sort(key=lambda x: -x[0])
        keep = ranked[:top]

        # Một request lấy `tldr` cho cả mười hai bài. `tldr` do model SciTLDR
        # của S2 dựng sẵn, nên đây là chỗ ta KHÔNG phải gọi model để biết mỗi
        # bài dẫn nói gì — thay được đúng `top` lượt gọi.
        ids = [(r.get("citedPaper") or {}).get("paperId") for _, r, _ in keep]
        extra = await _batch(client, [i for i in ids if i])

    for _, r, pid in keep:
        cited = r.get("citedPaper") or {}
        s2id = cited.get("paperId") or ""
        more = extra.get(s2id, {})
        tldr = ((more.get("tldr") or {}) or {}).get("text") or ""
        ctxs = [_clean_ctx(c) for c in (r.get("contexts") or [])]
        ctxs = [c for c in ctxs if len(c) > 40][:3]
        out["refs"].append({
            "title": cited.get("title") or "",
            "year": cited.get("year"),
            "cites": cited.get("citationCount") or 0,
            "venue": cited.get("venue") or more.get("venue") or "",
            "arxiv": ((cited.get("externalIds") or {}).get("ArXiv") or ""),
            # `tldr` là tóm tắt sẵn có; thiếu thì rơi về câu đầu của abstract.
            "gist": tldr or _first_sentences(more.get("abstract") or cited.get("abstract") or ""),
            "why": ctxs,                # vì sao bài chính dẫn bài này — nguyên văn
            "intents": r.get("intents") or [],
            "influential": bool(r.get("isInfluential")),
            "paper_id": pid or "",      # có mặt trong kho thì trỏ sang
        })
    return out


async def _batch(client: httpx.AsyncClient, ids: list[str]) -> dict[str, dict]:
    """Lấy `tldr` + abstract cho nhiều bài trong MỘT request."""
    if not ids:
        return {}
    try:
        r = await client.post(f"{API}/paper/batch", params={"fields": BATCH_FIELDS},
                              json={"ids": ids}, headers=UA, timeout=TIMEOUT)
        if r.status_code >= 400:
            return {}
        rows = r.json()
    except Exception:                   # noqa: BLE001 — thiếu tldr thì rơi về abstract
        return {}
    if not isinstance(rows, list):
        return {}
    return {p["paperId"]: p for p in rows if isinstance(p, dict) and p.get("paperId")}


def _first_sentences(text: str, n: int = 2) -> str:
    parts = re.split(r"(?<=[.!?])\s+", " ".join((text or "").split()))
    return " ".join(parts[:n])[:400]


# ------------------------------------------------------------------ lưu

def save(paper_id: str, data: dict) -> None:
    db.update_paper(paper_id, refs=json.dumps(data, ensure_ascii=False))


def load(paper_id: str) -> dict:
    p = db.load_paper(paper_id)
    return db._loads(p.get("refs"), {}) if p else {}


async def ensure(paper: dict, corpus_titles: dict[str, str] | None = None,
                 max_age: float = 30 * 86400) -> dict:
    """Hồ sơ đã có và còn mới thì dùng lại; không thì lấy về rồi lưu.

    Hạn 30 ngày vì số trích dẫn có đổi, nhưng câu trích dẫn — thứ đáng giá nhất
    ở đây — thì nằm trong bài đã xuất bản và không đổi bao giờ.
    """
    have = db._loads(paper.get("refs"), {})
    if have.get("refs") and time.time() - (have.get("fetched_at") or 0) < max_age:
        return have
    got = await dossier(paper, corpus_titles)
    if got.get("refs") or got.get("s2_id"):
        save(paper["id"], got)
    return got


def corpus_titles(survey_id: str, skip: str = "") -> dict[str, str]:
    """{tiêu đề chuẩn hoá: paper_id} để nhận ra bài dẫn nào cũng có trong kho."""
    return {_norm_title(p["title"]): p["id"]
            for p in db.list_papers(survey_id)
            if p.get("title") and p["id"] != skip}
