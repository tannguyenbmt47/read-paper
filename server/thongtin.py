"""Thông tin thư mục học của một bài: tác giả, năm, nơi đăng, DOI, arXiv.

Dùng cho thư viện kiểu Zotero. **Miễn phí, không cần key**: Semantic Scholar,
arXiv và Crossref đều mở. Thứ tự tra là thứ tự độ chắc chắn:

1. **Mã arXiv** (từ link nguồn, tên file `2604.00965v1.pdf`, hay dòng `arXiv:…`
   ở trang đầu) — khớp đúng một bài, không đoán.
2. **DOI** in ở trang đầu — cũng khớp đúng một bài.
3. **Tiêu đề** — khớp mờ, nên phải tự soát lại độ trùng tiêu đề (cùng luật với
   `survey/refs.resolve`): gắn nhầm tác giả của bài khác vào thư viện còn tệ
   hơn để trống, vì nhìn vẫn có vẻ đúng.

Semantic Scholar trả cả nơi đăng chính thức (ACL, NeurIPS…) cho bài có bản
arXiv, nên được hỏi trước. Nó hay từ chối khi gọi dồn (429) — lúc đó rơi về
arXiv / Crossref.
"""

from __future__ import annotations

import re

import httpx

S2 = "https://api.semanticscholar.org/graph/v1"
S2_FIELDS = "title,authors,year,venue,publicationVenue,externalIds,abstract,citationCount,url"
UA = {"User-Agent": "Loupe/1.0 (doc doc bai bao; local tool)"}
TIMEOUT = 20.0

_ARXIV_ID = re.compile(r"(?<![\d.])(\d{4}\.\d{4,5})(?:v\d+)?(?![\d])")
_ARXIV_CHU = re.compile(r"arxiv[:\s/]*(?:abs/|pdf/)?(\d{4}\.\d{4,5})", re.I)
_DOI = re.compile(r"\b(10\.\d{4,9}/[^\s\"<>,;]+[^\s\"<>,;.)\]])")


def _norm(t: str) -> str:
    return " ".join(re.sub(r"[^a-z0-9 ]+", " ", (t or "").lower()).split())


def _trung(a: str, b: str) -> float:
    """Tỉ lệ từ chung của hai tiêu đề đã chuẩn hoá."""
    x, y = set(_norm(a).split()), set(_norm(b).split())
    return len(x & y) / max(1, min(len(x), len(y)))


def _dau_bai(doc: dict, n: int = 15) -> str:
    return " ".join((b.get("text") or "") for b in (doc.get("blocks") or [])[:n])


def ma_arxiv(doc: dict) -> str:
    src = doc.get("source") or ""
    m = _ARXIV_CHU.search(src) or _ARXIV_ID.search(src.rsplit("/", 1)[-1])
    if not m:
        m = _ARXIV_CHU.search(_dau_bai(doc))
    return m.group(1) if m else ""


def ma_doi(doc: dict) -> str:
    m = _DOI.search(doc.get("source") or "") or _DOI.search(_dau_bai(doc))
    return m.group(1) if m else ""


def _tu_s2(p: dict) -> dict:
    ext = p.get("externalIds") or {}
    pv = p.get("publicationVenue") or {}
    venue = p.get("venue") or pv.get("name") or ""
    return {
        "title_goc": p.get("title") or "",
        "authors": [a.get("name") for a in (p.get("authors") or []) if a.get("name")][:40],
        "year": p.get("year"),
        "venue": venue[:160],
        "doi": ext.get("DOI") or "",
        "arxiv": ext.get("ArXiv") or "",
        "url": p.get("url") or "",
        "abstract": (p.get("abstract") or "")[:3000],
        "cites": p.get("citationCount") or 0,
    }


async def _s2(c: httpx.AsyncClient, path: str, params: dict) -> dict | None:
    try:
        r = await c.get(f"{S2}{path}", params=params, headers=UA, timeout=TIMEOUT)
    except Exception:  # noqa: BLE001 — mạng hỏng: coi như không có, thử nguồn khác
        return None
    if r.status_code != 200:
        return None
    try:
        return r.json()
    except ValueError:
        return None


async def _arxiv(c: httpx.AsyncClient, aid: str) -> dict | None:
    try:
        r = await c.get("https://export.arxiv.org/api/query",
                        params={"id_list": aid}, headers=UA, timeout=TIMEOUT)
    except Exception:  # noqa: BLE001
        return None
    if r.status_code != 200 or "<entry>" not in r.text:
        return None
    e = r.text.split("<entry>", 1)[1]

    def tag(name: str) -> str:
        m = re.search(rf"<{name}[^>]*>(.*?)</{name}>", e, re.S)
        return re.sub(r"\s+", " ", m.group(1)).strip() if m else ""

    pub = tag("published")
    return {
        "title_goc": tag("title"),
        "authors": re.findall(r"<name>(.*?)</name>", e)[:40],
        "year": int(pub[:4]) if pub[:4].isdigit() else None,
        "venue": "arXiv",
        "doi": tag("arxiv:doi"),
        "arxiv": aid,
        "url": f"https://arxiv.org/abs/{aid}",
        "abstract": tag("summary")[:3000],
    }


def _tu_crossref(w: dict) -> dict:
    parts = ((w.get("issued") or {}).get("date-parts") or [[None]])[0]
    return {
        "title_goc": " ".join(w.get("title") or [])[:400],
        "authors": [f"{a.get('given', '')} {a.get('family', '')}".strip()
                    for a in (w.get("author") or []) if a.get("family")][:40],
        "year": parts[0] if parts and isinstance(parts[0], int) else None,
        "venue": " ".join(w.get("container-title") or [])[:160],
        "doi": w.get("DOI") or "",
        "url": w.get("URL") or "",
        "abstract": re.sub(r"<[^>]+>", " ", w.get("abstract") or "")[:3000],
        "cites": w.get("is-referenced-by-count") or 0,
    }


async def tim(doc: dict) -> tuple[dict, str]:
    """Trả (thông tin, nguồn). Không tìm ra thì ({}, "")."""
    aid, doi = ma_arxiv(doc), ma_doi(doc)
    title = (doc.get("title") or "").strip()
    async with httpx.AsyncClient(follow_redirects=True) as c:
        # 1–2. mã chắc chắn: arXiv, DOI
        for key in ([f"arXiv:{aid}"] if aid else []) + ([f"DOI:{doi}"] if doi else []):
            p = await _s2(c, f"/paper/{key}", {"fields": S2_FIELDS})
            if p and p.get("title"):
                d = _tu_s2(p)
                if aid and not d["arxiv"]:
                    d["arxiv"] = aid
                return d, "semanticscholar"
        if aid and (d := await _arxiv(c, aid)):
            return d, "arxiv"
        if doi:
            try:
                r = await c.get(f"https://api.crossref.org/works/{doi}", headers=UA, timeout=TIMEOUT)
                if r.status_code == 200:
                    return _tu_crossref((r.json() or {}).get("message") or {}), "crossref"
            except Exception:  # noqa: BLE001
                pass
        # 3. tiêu đề — khớp mờ, PHẢI tự soát
        if len(title) < 16 or len(title.split()) < 3:
            return {}, ""
        got = await _s2(c, "/paper/search/match", {"query": title, "fields": S2_FIELDS})
        rows = (got or {}).get("data") or []
        if rows and _trung(title, rows[0].get("title") or "") >= 0.75:
            return _tu_s2(rows[0]), "semanticscholar"
        try:
            r = await c.get("https://api.crossref.org/works", headers=UA, timeout=TIMEOUT,
                            params={"query.bibliographic": title, "rows": "3"})
            items = ((r.json() or {}).get("message") or {}).get("items") or [] if r.status_code == 200 else []
        except Exception:  # noqa: BLE001
            items = []
        for w in items:
            if _trung(title, " ".join(w.get("title") or [])) >= 0.75:
                return _tu_crossref(w), "crossref"
    return {}, ""


# --------------------------------------------------------------- BibTeX

_BIB_BO_DAU = str.maketrans("đĐ", "dD")


def _khoa_bib(meta: dict, title: str) -> str:
    import unicodedata
    ho = ""
    if meta.get("authors"):
        ho = meta["authors"][0].split()[-1]
    tu = next((w for w in re.findall(r"[A-Za-z]{4,}", title or "")
               if w.lower() not in {"with", "from", "that", "this", "using", "towards"}), "paper")
    k = f"{ho}{meta.get('year') or ''}{tu}".translate(_BIB_BO_DAU)
    k = unicodedata.normalize("NFKD", k).encode("ascii", "ignore").decode()
    return re.sub(r"[^A-Za-z0-9]", "", k).lower() or "paper"


def _bib_esc(s) -> str:
    return str(s or "").replace("\\", "\\\\").replace("{", "\\{").replace("}", "\\}")


def bibtex(doc_row: dict, meta: dict) -> str:
    """Một mục BibTeX từ thông tin đã có. Thiếu trường nào thì bỏ trường đó."""
    title = meta.get("title_goc") or doc_row.get("title") or ""
    venue = meta.get("venue") or ""
    loai = "article" if venue and venue.lower() not in ("arxiv", "arxiv.org") else "misc"
    f = [("title", "{" + _bib_esc(title) + "}")]
    if meta.get("authors"):
        f.append(("author", _bib_esc(" and ".join(meta["authors"]))))
    if meta.get("year"):
        f.append(("year", str(meta["year"])))
    if venue and loai == "article":
        f.append(("journal", _bib_esc(venue)))
    if meta.get("doi"):
        f.append(("doi", _bib_esc(meta["doi"])))
    if meta.get("arxiv"):
        f += [("eprint", meta["arxiv"]), ("archivePrefix", "arXiv")]
    if meta.get("url"):
        f.append(("url", _bib_esc(meta["url"])))
    body = ",\n".join(f"  {k} = {{{v}}}" if not v.startswith("{") else f"  {k} = {v}" for k, v in f)
    return f"@{loai}{{{_khoa_bib(meta, title)},\n{body}\n}}\n"
