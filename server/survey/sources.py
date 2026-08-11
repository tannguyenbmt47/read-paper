"""Tìm bài trên web để nạp vào kho — ba nguồn học thuật không cần key, cộng web thường.

Thứ tự ưu tiên có lý do: với một kho survey, **metadata đúng quan trọng hơn số
lượng kết quả**. Năm xuất bản, hội nghị và số trích dẫn là đầu vào của khâu xếp
hạng ở `search.rerank`, mà kết quả tìm web thường không có mấy thứ đó — nó trả về
blog và trang giới thiệu.

| Nguồn | Key | Được gì |
|---|---|---|
| **arXiv** | không | toàn văn PDF mở, abstract, ngày, tác giả |
| **OpenAlex** | không | **số trích dẫn**, hội nghị/tạp chí, DOI, liên kết bản mở |
| **Crossref** | không | DOI chuẩn, tên hội nghị, ngày xuất bản chính thức |
| web thường | có | phần ngoài giới học thuật, khi thật sự cần |

Ba nguồn đầu chạy song song rồi **gộp trùng theo DOI và theo tiêu đề đã chuẩn
hoá** — cùng một bài thường có mặt ở cả ba, và danh sách đầy bản trùng thì người
dùng phải tự lọc bằng mắt trước khi tick.
"""

from __future__ import annotations

import asyncio
import os
import re

import httpx

# CHỈ ASCII. Giá trị header HTTP mã hoá bằng latin-1, nên một chữ có dấu tiếng
# Việt ở đây làm `UnicodeEncodeError` ném ra TRƯỚC khi request rời máy — và vì
# `find()` bắt mọi lỗi để một nguồn hỏng không làm chết cả ô tìm, cả ba nguồn
# cùng im lặng trả về rỗng. Đã vấp đúng vậy.
UA = {"User-Agent": "Loupe-Survey/1.0 (local paper reading tool)"}
TIMEOUT = 25

# OpenAlex và Crossref đều xếp hàng đợi ưu tiên cho request có email liên hệ.
# Không có thì vẫn chạy, chỉ chậm hơn lúc đông.
MAILTO = os.getenv("SURVEY_CONTACT_EMAIL") or ""

SEARCH_API_KEY = os.getenv("SEARCH_API_KEY") or ""
SEARCH_PROVIDER = (os.getenv("SEARCH_PROVIDER") or "brave").lower()


def _norm_title(t: str) -> str:
    return " ".join(re.sub(r"[^a-z0-9 ]+", " ", (t or "").lower()).split())


def _dedup(rows: list[dict]) -> list[dict]:
    """Gộp trùng, giữ bản ghi **đầy đủ nhất** chứ không phải bản gặp trước.

    arXiv cho PDF mở nhưng không cho số trích dẫn; OpenAlex thì ngược lại. Giữ
    bản gặp trước là mất một trong hai, nên phải trộn trường.
    """
    out: dict[str, dict] = {}
    for r in rows:
        key = (r.get("doi") or "").lower().strip() or _norm_title(r.get("title", ""))
        if not key:
            continue
        prev = out.get(key)
        if prev is None:
            out[key] = r
            continue
        for k, v in r.items():
            if v and not prev.get(k):
                prev[k] = v
            elif k == "cites" and (v or 0) > (prev.get("cites") or 0):
                prev[k] = v
        prev["source"] = f"{prev.get('source','')}+{r.get('source','')}".strip("+")
    return list(out.values())


def _rank(rows: list[dict]) -> list[dict]:
    """Xếp hạng trộn ba yếu tố, theo lối rerank có yếu tố thời gian của SurveyForge.

    Chỉ xếp theo độ liên quan thì bài mới toanh chưa ai trích luôn đứng ngang bài
    kinh điển; chỉ xếp theo trích dẫn thì kho toàn bài cũ. Trộn cả hai, cộng thêm
    ưu tiên nhỏ cho bài **có PDF mở** — bài không tải được thì nạp vào kho cũng
    chỉ có mỗi abstract.
    """
    import math

    def key(r: dict) -> float:
        cites = math.log1p(r.get("cites") or 0)
        year = r.get("year") or 0
        recency = max(0.0, (year - 2015) / 10.0) if year else 0.0
        return -(0.55 * cites + 0.30 * recency + (0.35 if r.get("pdf_url") else 0))

    return sorted(rows, key=key)


# ------------------------------------------------------------------ arXiv

_ATOM = re.compile(r"<entry>(.*?)</entry>", re.S)


def _tag(xml: str, name: str) -> str:
    m = re.search(rf"<{name}[^>]*>(.*?)</{name}>", xml, re.S)
    return re.sub(r"\s+", " ", m.group(1)).strip() if m else ""


async def arxiv(q: str, limit: int = 15) -> list[dict]:
    url = ("https://export.arxiv.org/api/query"
           f"?search_query=all:{httpx.QueryParams({'q': q})['q']}"
           f"&start=0&max_results={limit}&sortBy=relevance")
    async with httpx.AsyncClient(timeout=TIMEOUT, headers=UA, follow_redirects=True) as c:
        r = await c.get(url)
        r.raise_for_status()
    out = []
    for entry in _ATOM.findall(r.text):
        aid = _tag(entry, "id").rsplit("/", 1)[-1]
        pub = _tag(entry, "published")
        out.append({
            "title": _tag(entry, "title"),
            "authors": ", ".join(re.findall(r"<name>(.*?)</name>", entry))[:300],
            "year": int(pub[:4]) if pub[:4].isdigit() else None,
            "venue": "arXiv",
            "doi": _tag(entry, "arxiv:doi"),
            "url": f"https://arxiv.org/abs/{aid}",
            "pdf_url": f"https://arxiv.org/pdf/{aid}",
            "abstract": _tag(entry, "summary")[:1500],
            "cites": 0,
            "source": "arxiv",
        })
    return out


# --------------------------------------------------------------- OpenAlex


async def openalex(q: str, limit: int = 15) -> list[dict]:
    params = {"search": q, "per-page": str(limit)}
    if MAILTO:
        params["mailto"] = MAILTO
    async with httpx.AsyncClient(timeout=TIMEOUT, headers=UA, follow_redirects=True) as c:
        r = await c.get("https://api.openalex.org/works", params=params)
        r.raise_for_status()
    out = []
    for w in r.json().get("results", []):
        loc = w.get("primary_location") or {}
        best = w.get("best_oa_location") or {}
        out.append({
            "title": w.get("title") or "",
            "authors": ", ".join(
                (a.get("author") or {}).get("display_name", "")
                for a in (w.get("authorships") or [])[:8])[:300],
            "year": w.get("publication_year"),
            "venue": ((loc.get("source") or {}).get("display_name") or "")[:120],
            "doi": (w.get("doi") or "").replace("https://doi.org/", ""),
            "url": w.get("id") or "",
            "pdf_url": best.get("pdf_url") or "",
            "abstract": _inverted(w.get("abstract_inverted_index")),
            "cites": w.get("cited_by_count") or 0,
            "source": "openalex",
        })
    return out


def _inverted(idx: dict | None) -> str:
    """OpenAlex lưu abstract dạng chỉ mục đảo (từ → các vị trí). Dựng lại câu."""
    if not idx:
        return ""
    pos: list[tuple[int, str]] = []
    for word, spots in idx.items():
        pos.extend((int(p), word) for p in spots)
    pos.sort()
    return " ".join(w for _, w in pos)[:1500]


# ---------------------------------------------------------------- Crossref


async def crossref(q: str, limit: int = 15) -> list[dict]:
    params = {"query.bibliographic": q, "rows": str(limit), "select":
              "DOI,title,author,issued,container-title,abstract,is-referenced-by-count,URL"}
    if MAILTO:
        params["mailto"] = MAILTO
    async with httpx.AsyncClient(timeout=TIMEOUT, headers=UA, follow_redirects=True) as c:
        r = await c.get("https://api.crossref.org/works", params=params)
        r.raise_for_status()
    out = []
    for w in (r.json().get("message") or {}).get("items", []):
        parts = ((w.get("issued") or {}).get("date-parts") or [[None]])[0]
        out.append({
            "title": " ".join(w.get("title") or [])[:300],
            "authors": ", ".join(
                f"{a.get('given','')} {a.get('family','')}".strip()
                for a in (w.get("author") or [])[:8])[:300],
            "year": parts[0] if parts and isinstance(parts[0], int) else None,
            "venue": " ".join(w.get("container-title") or [])[:120],
            "doi": w.get("DOI") or "",
            "url": w.get("URL") or "",
            "pdf_url": "",
            "abstract": re.sub(r"<[^>]+>", " ", w.get("abstract") or "")[:1500],
            "cites": w.get("is-referenced-by-count") or 0,
            "source": "crossref",
        })
    return out


# ------------------------------------------------------------- web thường


def web_available() -> bool:
    return bool(SEARCH_API_KEY)


async def web(q: str, limit: int = 10) -> list[dict]:
    """Nguồn web thường, chỉ chạy khi có `SEARCH_API_KEY`.

    Không có key thì trả danh sách rỗng chứ **không** ném lỗi — giao diện ẩn mục
    này đi, và ba nguồn học thuật ở trên vẫn chạy đủ.
    """
    if not SEARCH_API_KEY:
        return []
    try:
        if SEARCH_PROVIDER == "brave":
            rows = await _brave(q, limit)
        elif SEARCH_PROVIDER == "tavily":
            rows = await _tavily(q, limit)
        else:
            rows = await _serper(q, limit)
    except Exception:                       # noqa: BLE001 — nguồn phụ hỏng không được làm chết cả ô tìm
        return []
    return rows


async def _brave(q: str, limit: int) -> list[dict]:
    async with httpx.AsyncClient(timeout=TIMEOUT, follow_redirects=True) as c:
        r = await c.get("https://api.search.brave.com/res/v1/web/search",
                        params={"q": q, "count": str(limit)},
                        headers={**UA, "X-Subscription-Token": SEARCH_API_KEY,
                                 "Accept": "application/json"})
        r.raise_for_status()
    return [_web_row(x.get("title", ""), x.get("url", ""), x.get("description", ""))
            for x in (r.json().get("web") or {}).get("results", [])]


async def _tavily(q: str, limit: int) -> list[dict]:
    async with httpx.AsyncClient(timeout=TIMEOUT, follow_redirects=True) as c:
        r = await c.post("https://api.tavily.com/search",
                         json={"api_key": SEARCH_API_KEY, "query": q,
                               "max_results": limit})
        r.raise_for_status()
    return [_web_row(x.get("title", ""), x.get("url", ""), x.get("content", ""))
            for x in r.json().get("results", [])]


async def _serper(q: str, limit: int) -> list[dict]:
    async with httpx.AsyncClient(timeout=TIMEOUT, follow_redirects=True) as c:
        r = await c.post("https://google.serper.dev/search",
                         json={"q": q, "num": limit},
                         headers={**UA, "X-API-KEY": SEARCH_API_KEY})
        r.raise_for_status()
    return [_web_row(x.get("title", ""), x.get("link", ""), x.get("snippet", ""))
            for x in r.json().get("organic", [])]


def _web_row(title: str, url: str, snippet: str) -> dict:
    is_pdf = url.lower().endswith(".pdf") or "/pdf/" in url.lower()
    return {"title": title[:300], "authors": "", "year": None, "venue": "",
            "doi": "", "url": url, "pdf_url": url if is_pdf else "",
            "abstract": snippet[:1000], "cites": 0, "source": "web"}


# ------------------------------------------------------------------ gộp


async def find(q: str, *, limit: int = 20, use_web: bool = False) -> tuple[list[dict], list[str]]:
    """Tìm song song ở mọi nguồn, gộp trùng, xếp hạng. Trả `(kết quả, lỗi)`.

    Một nguồn hỏng (mạng chập, API đổi) **không được** làm hỏng cả ô tìm — nên
    `gather(return_exceptions=True)`. Nhưng lỗi phải **trả về cho người dùng
    thấy**, không được nuốt: bản đầu chỉ gắn lỗi vào từng dòng kết quả, nên khi
    cả ba nguồn cùng hỏng thì không có dòng nào để gắn và màn hình chỉ hiện
    "không tìm thấy bài nào" — sai hẳn nguyên nhân, và không ai lần ra được.
    """
    jobs = [arxiv(q, limit), openalex(q, limit), crossref(q, limit)]
    names = ["arxiv", "openalex", "crossref"]
    if use_web and SEARCH_API_KEY:
        jobs.append(web(q, limit))
        names.append("web")
    got = await asyncio.gather(*jobs, return_exceptions=True)

    rows: list[dict] = []
    errs: list[str] = []
    for name, res in zip(names, got):
        if isinstance(res, Exception):
            errs.append(f"{name}: {type(res).__name__}: {res}"[:160])
        else:
            rows.extend(res)

    return _rank(_dedup([r for r in rows if r.get("title")]))[:limit], errs


async def fetch_pdf(row: dict) -> bytes:
    """Tải PDF của một kết quả. Ném lỗi có chữ tiếng Việt nếu không có bản mở."""
    from .. import parser

    url = row.get("pdf_url") or ""
    if not url and "arxiv.org" in (row.get("url") or ""):
        _aid, data = await parser.fetch_arxiv(row["url"])
        return data
    if not url:
        raise ValueError("Bài này không có bản PDF mở. Tải tay rồi kéo file vào kho.")
    return await parser.fetch_pdf_url(url)
