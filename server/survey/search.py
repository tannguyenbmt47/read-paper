"""Bộ tìm lai: BM25 + dense + cây + đồ thị, trộn bằng RRF, lọc bằng hai tầng rerank.

## Đường đi của một truy vấn

    câu hỏi (tiếng Việt)
      │
      ├─ lập kế hoạch  ─→ câu hỏi con · từ khoá EN/VI · pseudo_doc (query2doc)
      │
      ├─ BM25   trên mọi biến thể truy vấn ─┐
      ├─ dense  trên câu hỏi + pseudo_doc  ─┼─→ RRF (k=60) ─→ ~60 ứng viên
      │                                     │
      │  (cả hai chạy trên MỌI TẦNG của cây cùng lúc — "collapsed tree")
      │
      ├─ mở rộng theo đồ thị: đi một bước từ thực thể trong ứng viên
      │
      ├─ cross-encoder  60 → 20   (miễn phí, chạy trên GPU)
      ├─ chấm bằng model 20 → 10  (biết CÂU HỎI CON, cross-encoder thì không)
      └─ trần đa dạng ≤3 đoạn/bài

## Vì sao trộn bằng RRF chứ không cộng điểm

BM25 trả điểm âm không chặn trên, cosine trả [-1,1], cross-encoder trả logit —
cộng thẳng thì thang nào to hơn thang đó thắng, và mỗi lần đổi model là phải
chỉnh lại hệ số. RRF chỉ đọc **thứ hạng**, nên cắm thêm hay rút bớt một bộ tìm
không phải chỉnh gì. Nó cũng là chỗ cho phép `EMBED_BACKEND=off` chạy được mà
không rẽ nhánh code: thiếu một danh sách thì RRF cộng ít số hạng hơn, thế thôi.

## Vì sao có hai tầng rerank

Cross-encoder chấm **độ liên quan** rất chuẩn và miễn phí, nhưng nó chỉ thấy một
cặp (câu hỏi, đoạn) — nó không biết đây là câu hỏi con thứ mấy, và không biết
những đoạn khác đã phủ được gì. Tầng bằng model thì biết cả hai, nên nó lọc theo
**độ hữu ích cho bảng kiểm** chứ không chỉ độ liên quan. Chạy cross-encoder trước
để tầng tốn tiền chỉ phải nhìn 20 ứng viên thay vì 60.
"""

from __future__ import annotations

import os

from .. import llm
from . import db as sdb
from . import embed, graph, prompts

FAST = os.getenv("SURVEY_FAST_MODEL") or llm.FAST_MODEL
NO_REASONING = {"enabled": False}

RRF_K = 60           # hằng chuẩn của RRF; nhỏ hơn thì hạng đầu áp đảo quá mức
POOL = 60            # số ứng viên sau khi trộn
CROSS_KEEP = 20      # cross-encoder giữ lại bao nhiêu cho tầng model
PER_PAPER = 3        # trần đa dạng
BM25_LIMIT = 30      # mỗi biến thể truy vấn lấy về bấy nhiêu


# ------------------------------------------------------------ lập kế hoạch


async def plan(survey_id: str, question: str, digest: str,
               prev_terms: list[str] | None = None,
               fast: str = "") -> tuple[dict, llm.Usage]:
    """Tách câu hỏi con + sinh biến thể truy vấn + `pseudo_doc`.

    `digest` (phiếu toàn kho) đứng ở vị trí cached prefix nên lần hỏi thứ hai trở
    đi gần như không tốn phần input. `session_id=survey_id` giữ sticky routing —
    thiếu nó thì request rơi sang provider endpoint khác và cache không hit.
    """
    fast = fast or FAST
    sysmsg = llm.system_message(digest, prompts.PLAN_SYSTEM, model=fast)
    raw, usage = await llm.complete(
        [sysmsg, {"role": "user",
                  "content": prompts.plan_user(question, "", prev_terms)}],
        model=fast, session_id=survey_id, max_tokens=1800,
        temperature=0.3, reasoning=NO_REASONING)
    try:
        data = llm.extract_json(raw)
    except Exception:                       # noqa: BLE001
        data = {}
    return _clean_plan(question, data), usage


def _clean_plan(question: str, data: dict) -> dict:
    subs = []
    for i, s in enumerate(data.get("sub_questions") or []):
        ask = str(s.get("ask") or "").strip()
        if ask:
            subs.append({"id": str(s.get("id") or f"q{i+1}"), "ask": ask,
                         "need": str(s.get("need") or "")})
    if not subs:
        # Không tách được thì chính câu hỏi là bảng kiểm một dòng. Đường này phải
        # chạy được, nếu không một lượt model hỏng là cả tính năng chết.
        subs = [{"id": "q1", "ask": question, "need": ""}]

    queries = []
    for q in data.get("queries") or []:
        if isinstance(q, dict):
            text, tag = str(q.get("q") or "").strip(), str(q.get("for") or "q1")
        else:
            text, tag = str(q).strip(), "q1"
        if text:
            queries.append({"q": text, "for": tag})
    if not any(q["q"].strip().lower() == question.strip().lower() for q in queries):
        queries.insert(0, {"q": question, "for": "q1"})

    return {
        "question": question,
        "intent": str(data.get("intent") or "tra cứu"),
        "sub_questions": subs[:5],
        "must_terms_en": [str(t) for t in (data.get("must_terms_en") or [])][:12],
        "queries": queries[:8],
        "pseudo_doc": str(data.get("pseudo_doc") or "").strip(),
    }


def terms_of(plan_obj: dict) -> list[str]:
    """Từ khoá đã dùng — vòng sau bắt buộc phải mang thêm từ mới so với danh sách này."""
    out = [q["q"] for q in plan_obj.get("queries") or []]
    out += list(plan_obj.get("must_terms_en") or [])
    return out


# ---------------------------------------------------------------- trộn RRF


def rrf(lists: list[list[str]], k: int = RRF_K) -> list[tuple[str, float]]:
    """Reciprocal Rank Fusion: điểm của một mã = Σ 1/(k + hạng trong từng danh sách).

    Chỉ đọc thứ hạng, không đọc điểm — nên trộn được BM25 (điểm âm), cosine
    ([-1,1]) và bất cứ bộ tìm nào cắm thêm về sau mà không phải chuẩn hoá gì.
    """
    score: dict[str, float] = {}
    for lst in lists:
        for rank, cid in enumerate(lst):
            score[cid] = score.get(cid, 0.0) + 1.0 / (k + rank + 1)
    return sorted(score.items(), key=lambda kv: -kv[1])


# ------------------------------------------------------------ tìm ứng viên


async def retrieve(survey_id: str, plan_obj: dict, *, pool: int = POOL,
                   exclude: set[str] | None = None) -> dict:
    """Chạy mọi bộ tìm, trộn, mở rộng theo đồ thị. **Không gọi model lần nào.**

    Trả `{"hits": [...], "edges": [...], "lists": {tên bộ tìm: số kết quả}}`.
    """
    exclude = exclude or set()
    lists: list[list[str]] = []
    counts: dict[str, int] = {}

    # --- BM25 trên từng biến thể truy vấn ---------------------------------
    variants = [q["q"] for q in plan_obj.get("queries") or []]
    if plan_obj.get("pseudo_doc"):
        # query2doc: đoạn văn giả tiếng Anh mang đúng từ vựng của bài báo. Đây là
        # chỗ câu hỏi tiếng Việt bắt được đoạn tiếng Anh mà không cần dịch tay.
        variants.append(plan_obj["pseudo_doc"])
    for v in variants:
        got = [cid for cid, _ in sdb.bm25(survey_id, v, limit=BM25_LIMIT)]
        if got:
            lists.append(got)
            counts["bm25"] = counts.get("bm25", 0) + len(got)

    # --- dense trên câu hỏi, câu hỏi con và pseudo_doc --------------------
    dense_qs = [plan_obj.get("question") or ""]
    dense_qs += [s["ask"] for s in plan_obj.get("sub_questions") or []]
    if plan_obj.get("pseudo_doc"):
        dense_qs.append(plan_obj["pseudo_doc"])
    dense_qs = [q for q in dense_qs if q.strip()][:6]
    if embed.enabled() and dense_qs:
        ids, blobs, dim = sdb.all_vecs(survey_id, embed.MODEL_NAME)
        if ids:
            qv = await embed.encode([embed.as_query(q) for q in dense_qs])
            if qv is not None:
                mat = embed.unpack(blobs, dim)
                for i in range(qv.shape[0]):
                    got = [cid for cid, _ in embed.top_k(qv[i], ids, mat, k=BM25_LIMIT)]
                    if got:
                        lists.append(got)
                        counts["dense"] = counts.get("dense", 0) + len(got)

    fused = [cid for cid, _ in rrf(lists) if cid not in exclude][:pool]

    # --- mở rộng theo đồ thị ---------------------------------------------
    terms = list(plan_obj.get("must_terms_en") or [])
    terms += [w for q in variants[:3] for w in q.split()[:6]]
    extra, edges = graph.expand(survey_id, fused[:20], terms)
    extra = [c for c in extra if c not in exclude and c not in set(fused)]
    if extra:
        counts["graph"] = len(extra)

    rows = sdb.get_chunks(fused + extra)
    hits = [rows[cid] for cid in fused + extra if cid in rows]
    return {"hits": hits, "edges": edges, "lists": counts}


# ---------------------------------------------------------------- rerank


async def rerank(question: str, subs: list[dict], hits: list[dict],
                 keep: int = 10, session: str = "",
                 fast: str = "") -> tuple[list[dict], llm.Usage]:
    """Hai tầng lọc, rồi áp trần đa dạng. Trả các đoạn đã chấm, tốt nhất trước."""
    usage = llm.Usage()
    if not hits:
        return [], usage

    # Tầng 1: cross-encoder, miễn phí. Không nạp được thì bỏ qua, giữ thứ tự RRF.
    cands = list(hits)
    scored = await embed.cross_score(
        question, [f"{h.get('ctx') or ''}\n{h['text']}" for h in hits])
    if scored:
        order = sorted(range(len(hits)), key=lambda i: -scored[i])
        cands = [dict(hits[i], cross=round(scored[i], 3)) for i in order][:CROSS_KEEP]

    # Tầng 2: model chấm theo bảng kiểm — thứ mà cross-encoder không nhìn thấy.
    grades: dict[str, int] = {}
    raw, u = await llm.complete(
        [{"role": "system", "content": prompts.RERANK_SYSTEM},
         {"role": "user", "content": prompts.rerank_user(question, subs, cands)}],
        model=fast or FAST, session_id=session or None, max_tokens=2000,
        temperature=0.0, reasoning=NO_REASONING)
    usage.add(u)
    try:
        for g in llm.extract_json(raw).get("grades") or []:
            grades[str(g.get("id"))] = int(g.get("g", 0))
    except Exception:                       # noqa: BLE001 — hỏng thì giữ thứ tự cũ
        pass

    for h in cands:
        h["grade"] = grades.get(h["id"], 2 if not grades else 1)
    kept = [h for h in cands if h["grade"] > 0] or cands[:keep]
    kept.sort(key=lambda h: (-h["grade"], -h.get("cross", 0.0)))
    return _diversify(kept, keep), usage


def _diversify(hits: list[dict], keep: int) -> list[dict]:
    """Trần ≤PER_PAPER đoạn mỗi bài.

    Thiếu phủ là kiểu hỏng nặng nhất của RAG nhiều chặng — sót một chặng thì độ
    chính xác rơi gần ba mươi điểm. Mười đoạn của cùng một bài là đúng cái bẫy
    đó: bộ tìm tưởng mình rất chắc chắn trong khi vừa bỏ qua toàn bộ phần còn
    lại của kho.
    """
    out: list[dict] = []
    spill: list[dict] = []
    per: dict[str, int] = {}
    for h in hits:
        pid = h["paper_id"]
        if per.get(pid, 0) < PER_PAPER:
            per[pid] = per.get(pid, 0) + 1
            out.append(h)
        else:
            spill.append(h)
        if len(out) >= keep:
            return out
    # Chưa đủ số lượng thì mới lấy tới phần vượt trần — thà lệch về một bài còn
    # hơn trả về ít bằng chứng hơn mức cần.
    return (out + spill)[:keep]


# ---------------------------------------------- tìm thuần, không gọi model


async def plain(survey_id: str, q: str, limit: int = 20) -> list[dict]:
    """Ô tìm kiếm: BM25 + dense trộn RRF. Không gọi model, nên **miễn phí**."""
    lists = [[cid for cid, _ in sdb.bm25(survey_id, q, limit=limit * 2)]]
    if embed.enabled():
        ids, blobs, dim = sdb.all_vecs(survey_id, embed.MODEL_NAME)
        if ids:
            qv = await embed.encode([embed.as_query(q)])
            if qv is not None:
                mat = embed.unpack(blobs, dim)
                lists.append([cid for cid, _ in embed.top_k(qv[0], ids, mat, k=limit * 2)])
    order = [cid for cid, _ in rrf(lists)][:limit]
    rows = sdb.get_chunks(order)
    return [rows[c] for c in order if c in rows]
