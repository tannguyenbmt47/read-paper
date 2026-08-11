"""Cây RAPTOR cho từng bài: cụm các đoạn lại, tóm lược, rồi lặp lên tầng trên.

## Vấn đề mà cây giải quyết

Cắt bài thành đoạn rồi tìm theo đoạn chỉ trả lời được câu hỏi mà **câu trả lời
nằm gọn trong một đoạn**. Câu kiểu *"cách làm của bài này gồm mấy bước"* hay
*"bài này khác các bài trước ở đâu"* thì không có đoạn nào chứa sẵn câu trả lời —
nó rải ra khắp mục Method, và bộ tìm nào cũng trả về một mẩu ngẫu nhiên trong đó.

RAPTOR (ICLR 2024) vá chỗ này bằng cách dựng sẵn các mức trừu tượng: gom đoạn
thành cụm theo nội dung, cho model viết một bản tóm lược cho mỗi cụm, coi các bản
tóm lược đó là node mới, rồi lặp lại cho tới khi còn một node gốc cho cả bài.

## Truy vấn kiểu "collapsed tree"

Bài gốc so hai cách truy vấn và kết luận rõ: **đổ hết mọi node của mọi tầng vào
cùng một chỉ mục rồi tìm một lần** ăn đứt cách đi lần từ gốc xuống lá. Lý do là
người hỏi không biết trước câu trả lời nằm ở mức trừu tượng nào — hỏi một câu có
thể cần một con số ở lá và một bối cảnh ở tầng ba cùng lúc.

Nên ở đây **không có bảng riêng cho node cây**: tầng tóm lược ghi thẳng vào bảng
`chunk` với `level > 0`, dùng chung `chunk_fts` và chung bảng `emb`. Một truy vấn
chạm cả cây, không phải viết thêm đường nào.

## Cụm bằng gì

Bài gốc dùng UMAP + Gaussian Mixture chọn số cụm theo BIC. Ở đây thay bằng gom
cụm tham lam trên cosine, vì ba lý do:

- không phải thêm `scikit-learn` và `umap-learn` vào một công cụ chạy local;
- bài báo khoa học **đã có sẵn cấu trúc mục**, tức đã có một nửa lời giải mà UMAP
  phải đi tìm lại từ đầu — nên gom trong phạm vi mục rồi mới gom liên mục;
- một bài chỉ có ~100–250 node, ở cỡ đó thì khác biệt giữa các thuật toán gom cụm
  nhỏ hơn hẳn khác biệt do chất lượng bản tóm lược.

Không có embedding (`EMBED_BACKEND=off`) thì rơi về gom theo mục và theo thứ tự
đọc — cây vẫn dựng được, chỉ là ranh giới cụm thô hơn.
"""

from __future__ import annotations

import os

import numpy as np

from .. import llm
from . import db as sdb
from . import embed

FAST = os.getenv("SURVEY_FAST_MODEL") or llm.FAST_MODEL
NO_REASONING = {"enabled": False}

MIN_CLUSTER = 2          # cụm một node thì bản tóm lược chỉ là bản chép lại
MAX_CLUSTER = 6          # trên mức này bản tóm lược bắt đầu bỏ sót
MAX_LEVEL = 3            # bài dài nhất cũng chỉ tới tầng 3 là còn 1–2 node
STOP_NODES = 3           # còn ≤ ngần này node thì dừng, tầng trên không thêm gì
SUM_BATCH = 6            # số cụm gửi tóm lược mỗi lượt gọi

TREE_SYSTEM = """\
You compress groups of passages from one scientific paper into short summaries
that will be indexed for retrieval, sitting one level above the passages.

For each group, write:
  "sum"  — 60–110 words, English. What this group of passages collectively says.
           Keep every method name, dataset name, metric name and NUMBER that the
           passages state. A summary that drops the numbers is useless: the whole
           point is that a reader searching for that number finds this node.
  "ctx"  — ONE sentence (12–25 words) naming what part of the paper's argument
           this group covers, using the paper's own terminology.

Never invent. If the passages disagree or are fragmentary, say so plainly.
Do not write "This group of passages..." — state the content directly.

Chỉ trả lời bằng một object JSON hợp lệ, không kèm lời dẫn, không bọc trong ```.
{"groups": [{"i": 0, "sum": "...", "ctx": "..."}]}

Return one entry for EVERY group, keyed by the group index given.
"""


def _user(groups: list[list[dict]]) -> str:
    out = []
    for i, g in enumerate(groups):
        body = "\n\n".join(f"- ({n.get('section') or '?'}) {n['text'][:1100]}" for n in g)
        out.append(f"=== GROUP {i} ===\n{body}")
    return "\n\n".join(out)


# ------------------------------------------------------------- gom cụm


def _cluster(nodes: list[dict], mat: np.ndarray | None) -> list[list[int]]:
    """Trả về các cụm, mỗi cụm là danh sách chỉ số trong `nodes`.

    Gom **trong phạm vi từng mục trước**: hai đoạn cùng mục Method gần nhau hơn
    hẳn một đoạn Method với một đoạn Results dù cosine có nói gì. Chỉ khi một mục
    nhỏ hơn `MIN_CLUSTER` mới cho nó nhập với mục kề bên.
    """
    by_sec: list[tuple[str, list[int]]] = []
    for i, n in enumerate(nodes):
        sec = n.get("section") or ""
        if by_sec and by_sec[-1][0] == sec:
            by_sec[-1][1].append(i)
        else:
            by_sec.append((sec, [i]))

    # Mục quá ngắn thì nhập vào mục trước — tránh đẻ ra cụm một node.
    merged: list[list[int]] = []
    for _sec, idxs in by_sec:
        if merged and len(idxs) < MIN_CLUSTER:
            merged[-1].extend(idxs)
        else:
            merged.append(list(idxs))

    out: list[list[int]] = []
    for group in merged:
        out.extend(_split_group(group, mat))
    return [g for g in out if g]


def _split_group(idxs: list[int], mat: np.ndarray | None) -> list[list[int]]:
    """Chia một mục dài thành cụm ≤ MAX_CLUSTER, cắt ở chỗ nội dung đổi hướng."""
    if len(idxs) <= MAX_CLUSTER:
        return [idxs]
    if mat is None or mat.size == 0:
        return [idxs[i:i + MAX_CLUSTER] for i in range(0, len(idxs), MAX_CLUSTER)]

    # Độ giống giữa hai node liền kề. Chỗ trũng = chỗ mạch văn chuyển ý, và đó là
    # chỗ nên cắt — cắt đều đặn mỗi MAX_CLUSTER node thì hay cắt giữa một ý.
    sims = [float(mat[idxs[i]] @ mat[idxs[i + 1]]) for i in range(len(idxs) - 1)]
    want = max(1, round(len(idxs) / MAX_CLUSTER))
    if want <= 1:
        return [idxs]
    order = sorted(range(len(sims)), key=lambda i: sims[i])

    cuts: list[int] = []
    for pos in order:
        if len(cuts) >= want - 1:
            break
        # Giữ mọi cụm ≥ MIN_CLUSTER: cắt sát nhau quá thì đẻ ra cụm lẻ loi.
        if all(abs(pos - c) >= MIN_CLUSTER for c in cuts) and \
           pos + 1 >= MIN_CLUSTER and len(idxs) - pos - 1 >= MIN_CLUSTER:
            cuts.append(pos)
    cuts.sort()

    out, start = [], 0
    for cpos in cuts:
        out.append(idxs[start:cpos + 1])
        start = cpos + 1
    out.append(idxs[start:])
    return [g for g in out if g]


# ------------------------------------------------------------ dựng cây


async def build(paper_id: str, *, say=None, fast: str = "") -> dict:
    """Dựng cây tóm lược cho một bài. Trả {levels, nodes, cost}."""
    leaves = sdb.paper_chunks(paper_id, level=0)
    if len(leaves) <= STOP_NODES:
        sdb.put_summaries(paper_id, [])
        return {"levels": 0, "nodes": 0, "cost": 0.0, "usage": llm.Usage().dict()}

    usage = llm.Usage()
    made: list[dict] = []
    cur = [{"id": ch["id"], "text": ch["text"], "section": ch.get("section", "")}
           for ch in leaves]
    ord_n = 0

    for level in range(1, MAX_LEVEL + 1):
        if len(cur) <= STOP_NODES:
            break
        mat = await _matrix(paper_id, cur, leaves_only=(level == 1))
        groups_idx = _cluster(cur, mat)
        if len(groups_idx) >= len(cur):     # không gom được gì thì dừng, đừng lặp vô ích
            break
        groups = [[cur[i] for i in g] for g in groups_idx]
        if say:
            say(f"cây tầng {level}: {len(cur)} node → {len(groups)} cụm")

        sums, u = await _summarise(paper_id, groups, fast)
        usage.add(u)

        nxt = []
        for g, s in zip(groups, sums):
            ord_n += 1
            node = {
                "ord": ord_n,
                "level": level,
                "section": g[0].get("section", ""),
                "text": s["sum"],
                "ctx": s["ctx"],
                "children": [n["id"] for n in g],
            }
            made.append(node)
            nxt.append({"id": f"{paper_id}s{ord_n}", "text": s["sum"],
                        "section": node["section"]})
        cur = nxt

    sdb.put_summaries(paper_id, made)
    return {"levels": made[-1]["level"] if made else 0, "nodes": len(made),
            "cost": round(usage.cost, 5), "usage": usage.dict()}


async def _matrix(paper_id: str, nodes: list[dict],
                  leaves_only: bool) -> np.ndarray | None:
    """Vector của các node đang xét.

    Tầng 1 gom các **lá**, mà lá đã được vector hoá lúc nạp bài — lấy lại từ DB
    thay vì tính lại. Tầng trên gom các bản tóm lược vừa sinh ra trong lượt này,
    chưa có trong DB nên phải tính tại chỗ.
    """
    if not embed.enabled():
        return None
    if leaves_only:
        ids, blobs, dim = sdb.paper_vecs(paper_id, embed.MODEL_NAME)
        have = dict(zip(ids, blobs))
        if have and all(n["id"] in have for n in nodes):
            return embed.unpack([have[n["id"]] for n in nodes], dim)
    return await embed.encode(
        [embed.as_passage(n.get("section", ""), "", n["text"]) for n in nodes])


async def _summarise(paper_id: str, groups: list[list[dict]],
                     fast: str = "") -> tuple[list[dict], llm.Usage]:
    usage = llm.Usage()
    out: list[dict] = [{"sum": "", "ctx": ""} for _ in groups]
    for i in range(0, len(groups), SUM_BATCH):
        batch = groups[i:i + SUM_BATCH]
        raw, u = await llm.complete(
            [{"role": "system", "content": TREE_SYSTEM},
             {"role": "user", "content": _user(batch)}],
            model=fast or FAST, session_id=paper_id, max_tokens=4000,
            temperature=0.2, reasoning=NO_REASONING)
        usage.add(u)
        try:
            got = llm.extract_json(raw).get("groups") or []
        except Exception:              # noqa: BLE001
            got = []
        for g in got:
            try:
                j = i + int(g["i"])
            except (KeyError, TypeError, ValueError):
                continue
            if 0 <= j < len(out):
                out[j] = {"sum": str(g.get("sum", "")).strip(),
                          "ctx": str(g.get("ctx", "")).strip()}

    # Cụm nào model bỏ sót thì ghép thẳng phần đầu các node con. Kém hơn hẳn bản
    # tóm lược thật, nhưng để trống thì node ấy vô hình với bộ tìm.
    for j, g in enumerate(groups):
        if not out[j]["sum"]:
            out[j] = {"sum": " ".join(n["text"][:220] for n in g)[:1200],
                      "ctx": g[0].get("section", "")}
    return out, usage
