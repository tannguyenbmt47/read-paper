"""Đồ thị tri thức liên bài — dựng theo lối *lazy*, không dựng bản tóm lược cộng đồng.

## Cây lo trong một bài, đồ thị lo giữa các bài

`tree.py` cho mỗi bài một cây trừu tượng, nên câu hỏi *"bài này làm gì"* trả lời
tốt. Nhưng câu hỏi mà công cụ survey sinh ra để phục vụ lại là loại khác:

  - *"những bài nào cùng đánh giá trên HotpotQA, và ai hơn ai"*
  - *"phương pháp X được bài nào mở rộng"*
  - *"kho này có bao nhiêu hướng tiếp cận, chia thế nào"*

Không cây nào trả lời được, vì câu trả lời **nằm giữa các bài** chứ không nằm
trong bài nào. Đó là chỗ cần cạnh nối: `(CIRAG) --đánh giá trên--> (HotpotQA)` và
`(CIRAG) --tốt hơn--> (DPR)`, mỗi cạnh mang mã đoạn đã đọc ra nó nên **trích dẫn
được**, không phải suy đoán.

## Vì sao không dựng GraphRAG đầy đủ

GraphRAG bản đầy đủ của Microsoft, sau khi bóc thực thể, còn chạy phát hiện cộng
đồng rồi cho model viết bản tóm lược cho **mọi** cộng đồng ở **mọi** tầng. Đó là
phần chiếm gần hết chi phí nạp, và phần lớn bản tóm lược ấy không bao giờ được
đọc tới. Chính Microsoft sau đó ra LazyGraphRAG, hoãn phần đó lại và đưa chi phí
nạp về **0,1%** của bản đầy đủ — ngang với RAG vector thường.

Ở đây làm đúng theo hướng đó: nạp bài thì chỉ bóc thực thể + quan hệ (**một lượt
gọi model rẻ cho mỗi bài**), còn việc gom nhóm và tóm lược thì để lúc hỏi mới
làm, và chỉ làm cho vùng đồ thị mà câu hỏi chạm tới.

## Đồ thị dùng để MỞ RỘNG, không dùng để thay thế

Có bằng chứng đo được cho cả hai phía: hỏi tra cứu một chi tiết thì RAG vector
thường **hơn** đồ thị (F1 64,8 so với 63,0), còn hỏi bắc cầu nhiều chặng thì đồ
thị hơn (70,3 so với 67,0). Nên ở `search.py`, đồ thị không phải một đường tìm
song song mà là **một bước mở rộng sau khi đã tìm**: lấy thực thể trong các đoạn
tìm được, đi một bước sang hàng xóm, kéo về những đoạn nói về hàng xóm đó. Câu
hỏi tra cứu thì bước này gần như không thêm gì; câu hỏi bắc cầu thì nó chính là
chặng thứ hai.
"""

from __future__ import annotations

import os

from .. import llm
from . import db as sdb

FAST = os.getenv("SURVEY_FAST_MODEL") or llm.FAST_MODEL
NO_REASONING = {"enabled": False}

# Loại thực thể cố ý hẹp. Cho model tự do đặt loại thì mỗi bài đẻ ra một hệ phân
# loại riêng, và cùng một thứ ở hai bài thành hai node không bao giờ gộp được.
KINDS = ("method", "dataset", "metric", "task", "model", "concept", "org")

# Quan hệ cũng đóng, vì cùng lý do — và vì đây là những quan hệ mà một bài báo
# thật sự phát biểu ra, chứ không phải quan hệ suy diễn.
RELS = ("đánh giá trên", "tốt hơn", "kém hơn", "mở rộng", "dùng",
        "là một phần của", "so sánh với", "đo bằng")

GRAPH_SYSTEM = """\
You extract a small, precise knowledge graph from ONE scientific paper, so that
it can be joined with graphs from other papers in the same collection.

Chỉ trả lời bằng một object JSON hợp lệ, không kèm lời dẫn, không bọc trong ```.

{
  "entities": [
    {"name": "exact name as written in the paper",
     "kind": "method|dataset|metric|task|model|concept|org",
     "chunks": ["id of up to 3 passages where it appears"]}
  ],
  "edges": [
    {"src": "entity name", "rel": "one of the allowed relations",
     "dst": "entity name", "chunk": "passage id stating this",
     "note": "≤12 words, in Vietnamese"}
  ]
}

Allowed relations, use EXACTLY these Vietnamese strings:
  "đánh giá trên"    method → dataset      (evaluated on)
  "tốt hơn"          method → method       (outperforms)
  "kém hơn"          method → method       (underperforms)
  "mở rộng"          method → method       (builds on / extends)
  "dùng"             method → model/method (uses as a component)
  "là một phần của"  method → method       (is a module of)
  "so sánh với"      method → method       (compared, no clear winner)
  "đo bằng"          task/dataset → metric (measured by)

Hard rules — these decide whether the graph is usable at all:

1. **Names must be the canonical surface form used in the literature**, not a
   description. "DPR" not "the dense retriever of Karpukhin et al."; "HotpotQA"
   not "the multi-hop dataset". A name that no other paper would write the same
   way cannot be joined across papers, and a graph that cannot be joined is
   worthless here.
2. **Never emit "our method", "the proposed approach", "this work".** Use the
   paper's own name for its system. If the paper truly gave it no name, skip it.
3. **Every edge must be stated by the paper**, and `chunk` must be the passage
   that states it. Do not infer edges from what you know about these systems.
4. Both `src` and `dst` of every edge must appear in `entities`.
5. 8–25 entities. Fewer is fine; padding the list with generic concepts
   ("deep learning", "neural network") makes every paper look connected to every
   other paper, which destroys the graph's usefulness.
"""


def _user(title: str, labeled: str) -> str:
    return (f"PAPER: {title or '(unknown)'}\n\n"
            f"=== FULL TEXT, EACH PASSAGE PREFIXED WITH ITS ID ===\n{labeled}")


async def extract(paper_id: str, title: str, prefix: str,
                  fast: str = "") -> tuple[dict, llm.Usage]:
    """Bóc thực thể + quan hệ cho một bài. Một lượt gọi, model rẻ.

    `prefix` là toàn văn bài có mã đoạn — cùng khối đã dùng cho pass ngữ cảnh hoá
    và pass phiếu, nên tới lượt này nó đã nằm sẵn trong cache của provider.
    """
    fast = fast or FAST
    sysmsg = llm.system_message(prefix, GRAPH_SYSTEM, model=fast)
    raw, usage = await llm.complete(
        [sysmsg, {"role": "user", "content": _user(title, "")}],
        model=fast, session_id=paper_id, max_tokens=4000,
        temperature=0.1, reasoning=NO_REASONING)
    try:
        data = llm.extract_json(raw)
    except Exception:                       # noqa: BLE001
        return {"entities": [], "edges": []}, usage
    return _clean(paper_id, data), usage


_JUNK = {"our method", "the proposed method", "this work", "the model", "our approach",
         "the paper", "our system", "proposed approach", "the method",
         "deep learning", "machine learning", "neural network", "ai", "nlp"}


def _clean(paper_id: str, data: dict) -> dict:
    """Lọc trước khi ghi: tên rác và cạnh treo làm hỏng đồ thị nhanh hơn mọi thứ khác."""
    ents: dict[str, dict] = {}
    for e in (data.get("entities") or []):
        name = str(e.get("name") or "").strip()
        norm = sdb.norm_name(name)
        if not norm or norm in _JUNK or len(norm) < 2:
            continue
        kind = str(e.get("kind") or "concept").strip().lower()
        chunks = [c for c in (e.get("chunks") or [])
                  if isinstance(c, str) and c.startswith(paper_id) and c.isalnum()][:3]
        prev = ents.get(norm)
        if prev:
            prev["chunks"] = list(dict.fromkeys(prev["chunks"] + chunks))[:3]
            continue
        ents[norm] = {"name": name, "norm": norm,
                      "kind": kind if kind in KINDS else "concept", "chunks": chunks}

    edges = []
    seen = set()
    for g in (data.get("edges") or []):
        src, dst = sdb.norm_name(g.get("src") or ""), sdb.norm_name(g.get("dst") or "")
        rel = str(g.get("rel") or "").strip()
        # Cạnh treo (một đầu không có trong danh sách thực thể) là thứ hay gặp
        # nhất, và nó tạo ra node không có mention nào — vô hình mà vẫn chiếm chỗ.
        if src not in ents or dst not in ents or src == dst or rel not in RELS:
            continue
        key = (src, dst, rel)
        if key in seen:
            continue
        seen.add(key)
        chunk = g.get("chunk") or ""
        edges.append({"src": src, "dst": dst, "rel": rel,
                      "chunk": chunk if isinstance(chunk, str) and chunk.startswith(paper_id) else "",
                      "note": str(g.get("note") or "")[:120]})
    return {"entities": list(ents.values()), "edges": edges}


def save(survey_id: str, paper_id: str, graph: dict) -> None:
    sdb.put_graph(survey_id, paper_id, graph.get("entities") or [], graph.get("edges") or [])


# ------------------------------------------------- dùng lúc hỏi: mở rộng


def expand(survey_id: str, chunk_ids: list[str], terms: list[str],
           limit: int = 24) -> tuple[list[str], list[dict]]:
    """Từ các đoạn đã tìm được, đi một bước trong đồ thị, kéo về đoạn liên quan.

    Trả `(chunk_id thêm vào, các cạnh đã đi qua)`. Cạnh trả về để hiện cho người
    dùng thấy đường suy luận — "vì CIRAG tốt hơn DPR trên HotpotQA" là một mắt
    xích, và người đọc phải thấy được nó dựa vào đoạn nào.

    Đi **đúng một bước**. Hai bước thì với kho vài chục bài là chạm gần hết đồ
    thị, và mọi thứ liên quan tới mọi thứ nghĩa là không lọc được gì nữa.
    """
    ents = sdb.entities_of(chunk_ids)
    seeds = {e["id"] for lst in ents.values() for e in lst}
    for e in sdb.find_entities(survey_id, terms):
        seeds.add(e["id"])
    if not seeds:
        return [], []

    edges = sdb.neighbours(survey_id, list(seeds))
    if not edges:
        return [], []

    # Cạnh nối hai thực thể mà câu hỏi CHẠM CẢ HAI thì đáng tin hơn hẳn cạnh chỉ
    # chạm một đầu — nó đúng là mắt xích bắc cầu đang cần.
    def weight(e: dict) -> tuple:
        both = (e["src"] in seeds) and (e["dst"] in seeds)
        return (not both, -(e.get("year") or 0))

    edges.sort(key=weight)
    have = set(chunk_ids)
    add: list[str] = []
    used: list[dict] = []
    for e in edges:
        if len(add) >= limit:
            break
        used.append(e)
        cid = e.get("chunk_id") or ""
        if cid and cid not in have:
            have.add(cid)
            add.append(cid)
    return add, used[:limit]


def describe(edges: list[dict], limit: int = 20) -> str:
    """Các cạnh viết thành dòng đọc được, để nhét vào prompt tổng hợp."""
    out = []
    for e in edges[:limit]:
        note = f" — {e['note']}" if e.get("note") else ""
        cite = f" [{e['chunk_id']}]" if e.get("chunk_id") else ""
        out.append(f"- {e['src_name']} —{e['rel']}→ {e['dst_name']}"
                   f" (theo {e.get('paper_title', '')[:50]}){note}{cite}")
    return "\n".join(out)
