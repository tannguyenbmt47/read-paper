"""Điều phối các pass dịch.

Luồng:
    parse  ->  pass 1: brief + glossary (1 lần / bài)
           ->  pass 2: dịch từng mẻ (streaming)
           ->  pass 2b: soát lại (tuỳ chọn, chế độ "kỹ")
           ->  pass 3: giải thích từng đoạn (chạy khi người đọc bấm)
           ->  pass 4: dựng bộ slide trình bày (chạy khi người đọc bấm)

Điểm mấu chốt về chi phí: prefix hệ thống (luật dịch + TOÀN VĂN bài + brief +
glossary) là **byte-identical** ở mọi request của cùng một bài, và luôn đứng
trước phần thay đổi. Nhờ đó lần gọi đầu ghi cache, mọi lần sau đọc cache.
"""

from __future__ import annotations

import json
import re
from typing import AsyncIterator

from . import db, llm, prompts, store
from .parser import Block, chunk_blocks

LABEL = re.compile(r"<<<\s*([A-Za-z0-9_]+)\s*>>>")

# Tắt suy luận cho các lượt dịch: model suy luận có thể tiêu hết max_tokens vào
# phần nghĩ thầm rồi trả về rỗng. Pass đọc-toàn-bài và pass giải-thích thì vẫn để
# mặc định, vì ở đó suy luận thật sự có ích.
NO_REASONING = {"enabled": False}
# Pass đọc-toàn-bài và pass giải-thích có lợi từ suy luận, nhưng để mặc định thì
# model suy luận ăn hết ngân sách token rồi trả về JSON dở dang. Ghìm ở mức thấp.
LOW_REASONING = {"effort": "low"}

# Chữ Hán / Kana / Hangul. Model gốc Trung Quốc thỉnh thoảng trả về tiếng Trung
# dù prompt viết bằng tiếng Việt — phải bắt được chứ không thể tin vào lời dặn.
CJK = re.compile(r"[\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uac00-\ud7af]")

# dấu nhấn markdown lọt vào cột giải thích sẽ hiện ra nguyên dấu sao
_MD = re.compile(r"\*\*(.+?)\*\*|__(.+?)__|(?<!\w)\*(?!\s)(.+?)(?<!\s)\*(?!\w)")


def strip_md(text: str) -> str:
    """Bỏ dấu nhấn markdown, giữ nguyên chữ bên trong."""
    return _MD.sub(lambda m: m.group(1) or m.group(2) or m.group(3) or "", text)


def cjk_leak(out: str, src: str) -> bool:
    """Đầu ra có chữ Đông Á mà bản gốc không hề có -> model đã trả sai ngôn ngữ.

    So với bản gốc chứ không cấm tuyệt đối: bài về NLP đa ngữ có thể trích dẫn
    tiếng Trung/Nhật thật, và bản dịch giữ lại nguyên văn là đúng.
    """
    got = set(CJK.findall(out))
    return bool(got - set(CJK.findall(src)))


# ------------------------------------------------------------------ helpers


def full_source_text(blocks: list[dict], limit: int = 400_000) -> str:
    """Toàn văn bài, có mã block, dùng làm ngữ cảnh dùng chung (được cache)."""
    out = []
    for b in blocks:
        if b["type"] == "reference":
            continue
        tag = b["type"]
        out.append(f"<<<{b['id']}>>> [{tag}] {b['text']}")
    text = "\n\n".join(out)
    return text[:limit]


def cached_prefix(doc: dict) -> str:
    """Phần system prompt phải giống hệt nhau giữa mọi request của bài này."""
    parts = [prompts.TRANSLATION_RULES]

    brief = doc.get("brief") or {}
    if brief:
        chain = "\n".join(
            f"  {i+1}. [{s.get('role','')}] {s.get('step','')}"
            for i, s in enumerate(brief.get("argument_chain", []))
        )
        parts.append(
            "## Bối cảnh bài báo (dùng để dịch cho đúng ý, không được chép vào bản dịch)\n"
            f"- Chốt lại: {brief.get('one_line','')}\n"
            f"- Bài toán: {brief.get('problem','')}\n"
            f"- Khoảng trống: {brief.get('gap','')}\n"
            f"- Ý tưởng: {brief.get('idea','')}\n"
            f"- Cách làm: {brief.get('method','')}\n"
            f"- Bằng chứng: {brief.get('evidence','')}\n"
            f"### Mạch lập luận\n{chain}"
        )

    gl = brief.get("glossary") or []
    if gl:
        rows = "\n".join(
            f"- {g['en']} → " + ("GIỮ NGUYÊN TIẾNG ANH" if g.get("keep_en") else g.get("vi", ""))
            for g in gl
        )
        parts.append("## BẢNG THUẬT NGỮ ĐÃ CHỐT (bắt buộc dùng thống nhất)\n" + rows)

    parts.append(
        "## TOÀN VĂN BÀI BÁO (bản gốc, để tra ngữ cảnh khi dịch từng phần)\n"
        + full_source_text(doc["blocks"])
    )
    return "\n\n---\n\n".join(parts)


def _parse_labeled(text: str) -> dict[str, str]:
    """Bóc `<<<id>>> nội dung` thành dict, chịu được đầu ra bị cắt giữa chừng."""
    out: dict[str, str] = {}
    matches = list(LABEL.finditer(text))
    for i, m in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        out[m.group(1)] = text[m.end():end].strip()
    return out


def build_doc(doc_id: str, title: str, blocks: list[Block], source: str, model: str) -> dict:
    import time
    return {
        "id": doc_id,
        "title": title,
        "source": source,
        "model": model,
        "blocks": [b.dict() for b in blocks],
        "brief": None,
        "plain": {},   # cột diễn giải cho người chưa có nền
        "prepared": False,   # bước 1 đã được người dùng xác nhận chưa
        "translations": {},
        "notes": {},
        "usage": llm.Usage().dict(),
        "created_at": time.time(),
        "updated_at": time.time(),
    }


async def estimate(doc: dict) -> dict:
    """Ước lượng khối lượng và chi phí của bước 2, tính trước khi tiêu đồng nào.

    Đây là lý do bước tiền xử lý đáng tách riêng: nhìn được cấu trúc bóc ra có
    đúng không, và biết trước sẽ tốn bao nhiêu, rồi mới quyết định dịch.
    """
    todo = [b for b in doc["blocks"] if b.get("translate")]
    src_chars = sum(len(b["text"]) for b in todo)
    doc_chars = len(full_source_text(doc["blocks"]))

    # ~3.6 ký tự/token cho tiếng Anh học thuật; tiếng Việt dài hơn ~1.25 lần
    src_tok = src_chars / 3.6
    out_tok = src_tok * 1.25
    n_chunks = max(len(plan_chunks(doc)), 1)
    ctx_tok = doc_chars / 3.6

    # lượt đầu ghi cache, các lượt sau đọc cache (rẻ ~10x)
    prompt_tok = ctx_tok * (1 + 0.1 * n_chunks) + src_tok
    brief_out = 3000

    price = None
    try:
        for m in await llm.list_models():
            if m.get("id") == doc["model"]:
                p = m.get("pricing") or {}
                price = (float(p.get("prompt") or 0), float(p.get("completion") or 0))
                break
    except Exception:  # noqa: BLE001
        price = None

    cost = None
    if price and (price[0] or price[1]):
        cost = prompt_tok * price[0] + (out_tok + brief_out) * price[1]

    return {
        "blocks_total": len(doc["blocks"]),
        "blocks_to_translate": len(todo),
        "figures": sum(1 for b in doc["blocks"] if b.get("figure")),
        "source_chars": src_chars,
        "chunks": n_chunks,
        "prompt_tokens": round(prompt_tok),
        "output_tokens": round(out_tok + brief_out),
        "cost_usd": round(cost, 4) if cost is not None else None,
        "model": doc["model"],
    }


def plan_chunks(doc: dict) -> list[list[dict]]:
    blocks = [Block(**b) for b in doc["blocks"]]
    return [[b.dict() for b in group] for group in chunk_blocks(blocks)]


def _bump_usage(doc_id: str, raw_json: str) -> dict:
    import json
    doc = store.load(doc_id)
    total = llm.Usage(**doc.get("usage", {}))
    total.add(llm.Usage(**json.loads(raw_json)))
    doc["usage"] = total.dict()
    store.save(doc)
    return total.dict()


# ------------------------------------- pass 0: căn chỉnh text bóc từ PDF

# Khối đáng đưa đi dọn. Bỏ reference (không dịch) và meta (tên tác giả, email —
# dọn chỉ tổ hỏng).
RELAYOUT_TYPES = ("para", "caption", "equation", "heading")

_TYPE_TAG = re.compile(r"^\s*\[(?:para|heading|caption|equation|meta|reference)\]\s*")


def _alnum(s: str) -> "Counter":
    from collections import Counter
    return Counter(c.lower() for c in s if c.isalnum())


def content_kept(src: str, out: str) -> bool:
    """Bản dọn có đúng bằng bản gốc về chữ và số không.

    Đây là chốt chặn của cả pass: model chỉ được sắp xếp lại và thêm bớt khoảng
    trắng / dấu ngoặc đánh dấu. Thêm một chữ hay nuốt một số là hỏng — mà hỏng
    kiểu đó rất khó phát hiện bằng mắt, nên phải chặn bằng máy.
    """
    if not out.strip():
        return False
    return _alnum(src) == _alnum(out) and not cjk_leak(out, src)


async def relayout(doc_id: str, batch_chars: int = 12_000) -> tuple[dict, dict, dict]:
    """Nhờ model rẻ dọn lại text bóc từ PDF. Trả (thống kê, chi phí lượt, cộng dồn).

    Chạy bằng `OR_MODEL_FAST` — việc này là chuẩn hoá chuỗi, không cần model
    mạnh. Mọi thay đổi đều phải qua `content_kept()`; không qua thì giữ bản gốc.
    """
    import json

    doc = store.load(doc_id)
    # KHÔNG lọc theo `translate`: công thức luôn có translate=False mà chính nó
    # mới là thứ cần dọn nhất.
    todo = [b for b in doc["blocks"] if b["type"] in RELAYOUT_TYPES and b["text"]]
    if not todo:
        return {"checked": 0, "changed": 0, "rejected": 0}, llm.Usage().dict(), doc["usage"]

    batches, cur, size = [], [], 0
    for b in todo:
        if cur and size + len(b["text"]) > batch_chars:
            batches.append(cur)
            cur, size = [], 0
        cur.append(b)
        size += len(b["text"])
    if cur:
        batches.append(cur)

    model = llm.FAST_MODEL
    usage = llm.Usage()
    fixed: dict[str, str] = {}
    rejected = 0

    for group in batches:
        raw, u = await llm.complete(
            [{"role": "system", "content": prompts.RELAYOUT_SYSTEM},
             {"role": "user", "content": prompts.relayout_user(group)}],
            model=model, session_id=doc_id, max_tokens=16000, temperature=0.0,
            reasoning=NO_REASONING,
        )
        usage.add(u)
        got = _parse_labeled(raw)
        by_id = {b["id"]: b["text"] for b in group}
        for bid, new in got.items():
            src = by_id.get(bid)
            if src is None:
                continue
            # model đôi khi vẫn tự thêm nhãn loại khối vào đầu — gỡ ra cho khỏi
            # bị chốt chặn chặn oan
            new = _TYPE_TAG.sub("", new).strip()
            if new == src:
                continue
            if content_kept(src, new):
                fixed[bid] = new
            else:
                rejected += 1

    if fixed:
        doc = store.load(doc_id)
        for b in doc["blocks"]:
            if b["id"] in fixed:
                b["text"] = fixed[b["id"]]
        # text đổi thì bản dịch cũ không còn ứng với nó nữa
        for bid in fixed:
            doc["translations"].pop(bid, None)
            doc["notes"].pop(bid, None)
            (doc.get("plain") or {}).pop(bid, None)
        total = llm.Usage(**doc.get("usage", {}))
        total.add(usage)
        doc["usage"] = total.dict()
        store.save(doc)
    else:
        total = llm.Usage(**doc.get("usage", {}))
        total.add(usage)
        doc["usage"] = total.dict()
        store.save(doc)

    return ({"checked": len(todo), "changed": len(fixed), "rejected": rejected,
             "model": model},
            usage.dict(), total.dict())


# ------------------------------------------------------ pass 1: brief+glossary


async def run_brief(doc_id: str) -> tuple[dict, dict, dict]:
    """Trả về (brief, chi phí lượt này, chi phí cộng dồn của bài)."""
    doc = store.load(doc_id)
    text = full_source_text(doc["blocks"], limit=300_000)
    raw, usage = await llm.complete(
        [
            {"role": "system", "content": prompts.BRIEF_SYSTEM},
            {"role": "user", "content": prompts.brief_user(doc.get("title", ""), text)},
        ],
        model=doc["model"],
        session_id=doc_id,
        max_tokens=20000,
        temperature=0.3,
        reasoning=LOW_REASONING,
    )
    if cjk_leak(raw, text):
        raw, u2 = await llm.complete(
            [
                {"role": "system", "content": prompts.BRIEF_SYSTEM},
                {"role": "user", "content": prompts.brief_user(doc.get("title", ""), text)
                 + "\n\nLẦN TRƯỚC BẠN ĐÃ TRẢ VỀ TIẾNG TRUNG. Viết lại toàn bộ bằng"
                   " TIẾNG VIỆT, không một chữ Hán nào."},
            ],
            model=doc["model"], session_id=doc_id, max_tokens=20000, temperature=0.2,
            reasoning=LOW_REASONING,
        )
        usage.add(u2)

    brief = llm.extract_json(raw)
    brief.setdefault("glossary", [])
    brief.setdefault("argument_chain", [])
    doc["brief"] = brief
    total = llm.Usage(**doc.get("usage", {}))
    total.add(usage)
    doc["usage"] = total.dict()
    store.save(doc)
    return brief, usage.dict(), total.dict()


# ------------------------------------------------------------- pass 2: dịch


async def stream_chunk(
    doc_id: str, chunk_index: int, *, refine: bool = False, mode: str = "both",
    only: set[str] | None = None,
) -> AsyncIterator[tuple[str, str]]:
    """Yield các event ('block', json) / ('usage', json) / ('done', json).

    `only`: chỉ dịch những khối có mã trong tập này. Dùng cho dịch từng phần —
    prefix bài vẫn nằm trong cache nên tiền chủ yếu ở token ĐẦU RA, dịch ít khối
    là trả ít thật. Bỏ trống thì dịch cả mẻ như cũ.
    """
    import json

    doc = store.load(doc_id)
    chunks = plan_chunks(doc)
    if chunk_index >= len(chunks):
        yield "done", json.dumps({"chunk": chunk_index, "total": len(chunks)})
        return

    items = chunks[chunk_index]
    if only:
        items = [it for it in items if it["id"] in only]
        if not items:
            yield "done", json.dumps({"chunk": chunk_index, "total": len(chunks),
                                      "skipped": True})
            return

    # --- bộ nhớ dịch: đoạn nào dịch rồi thì lấy lại, khỏi gọi model ---
    def satisfied(it: dict, hit: dict) -> bool:
        """Bản lưu có đủ những cột mà lần này cần không."""
        if mode in ("vi", "both") and not hit.get("vi"):
            return False
        needs_plain = it["type"] in ("para", "caption")
        if mode in ("plain", "both") and needs_plain and not hit.get("plain"):
            return False
        return True

    hits = db.tm_get([it["text"] for it in items], doc["model"])
    reused: list[dict] = []
    todo: list[dict] = []
    for it in items:
        h = hits.get(it["text"])
        (reused if h and satisfied(it, h) else todo).append(it)

    if reused:
        doc_now = store.load(doc_id)
        for it in reused:
            h = hits[it["text"]]
            if h.get("vi"):
                doc_now["translations"][it["id"]] = h["vi"]
                yield "block", json.dumps({"id": it["id"], "vi": h["vi"], "cached": True})
            if h.get("plain"):
                doc_now.setdefault("plain", {})[it["id"]] = h["plain"]
                yield "block", json.dumps({"id": it["id"], "plain": h["plain"], "cached": True})
        store.save(doc_now)

    if not todo:      # cả mẻ đã có sẵn -> không gọi model, không tốn đồng nào
        yield "done", json.dumps({
            "chunk": chunk_index, "total": len(chunks),
            "usage": doc.get("usage", {}), "run": {"cost": 0.0},
            "reused": len(reused), "generated": 0,
        })
        return

    items = todo
    prefix = cached_prefix(doc)
    # mode: "vi" = chỉ dịch · "plain" = chỉ diễn giải · "both" = cả hai
    if mode == "plain":
        task = prompts.PLAIN_ONLY_TASK
    else:
        task = prompts.TRANSLATE_TASK + (prompts.PLAIN_TASK if mode == "both" else "")
    sysmsg = llm.system_message(prefix, task, model=doc["model"])

    def split(parsed: dict[str, str]) -> tuple[dict, dict]:
        """Tách nhãn `b12` (bản dịch) khỏi nhãn `b12_g` (diễn giải)."""
        vi, gl = {}, {}
        for k, v in parsed.items():
            if k.endswith("_g"):
                gl[k[:-2]] = v
            else:
                vi[k] = v
        return vi, gl

    buf = ""
    emitted: set[str] = set()
    usage_json = "{}"

    async for kind, payload in llm.stream_text(
        [sysmsg, {"role": "user", "content": prompts.translate_user(items)}],
        model=doc["model"],
        session_id=doc_id,
        max_tokens=24000 if mode == "both" else 16000,
        temperature=0.2,
        reasoning=NO_REASONING,
    ):
        if kind == "usage":
            usage_json = payload
            continue
        buf += payload
        # phát block ngay khi nhãn kế tiếp xuất hiện => đã xong block trước
        parsed = _parse_labeled(buf)
        labels = list(parsed.keys())
        for key in labels[:-1]:
            if key in emitted:
                continue
            emitted.add(key)
            if key.endswith("_g"):
                yield "block", json.dumps({"id": key[:-2], "plain": strip_md(parsed[key])})
            else:
                yield "block", json.dumps({"id": key, "vi": parsed[key]})

    parsed = _parse_labeled(buf)
    for key, val in parsed.items():
        if key in emitted:
            continue
        if key.endswith("_g"):
            yield "block", json.dumps({"id": key[:-2], "plain": strip_md(val)})
        else:
            yield "block", json.dumps({"id": key, "vi": val})
    final, plains = split(parsed)

    # Model hay bỏ sót ô diễn giải cuối cùng của mẻ. Vá lại đúng những ô thiếu —
    # rẻ hơn nhiều so với chạy lại cả mẻ, và người đọc không bị thủng cột.
    if mode in ("both", "plain"):
        need = [it for it in items
                if it["type"] in ("para", "caption") and it["id"] not in plains]
        if need:
            yield "status", json.dumps({"msg": f"Bổ sung {len(need)} ô giải thích còn thiếu…"})
            fix_msg = llm.system_message(prefix, prompts.PLAIN_ONLY_TASK, model=doc["model"])
            raw, fu = await llm.complete(
                [fix_msg, {"role": "user", "content": prompts.translate_user(need)}],
                model=doc["model"], session_id=doc_id,
                max_tokens=12000, temperature=0.2, reasoning=NO_REASONING,
            )
            for key, val in _parse_labeled(raw).items():
                bid = key[:-2] if key.endswith("_g") else key
                if bid in plains or not val.strip():
                    continue
                plains[bid] = strip_md(val)
                yield "block", json.dumps({"id": bid, "plain": plains[bid]})
            u = llm.Usage(**json.loads(usage_json))
            u.add(fu)
            usage_json = json.dumps(u.dict())

    if refine and final and mode != "plain":
        yield "status", json.dumps({"msg": "Đang soát lại bản dịch…"})
        rmsg = llm.system_message(prefix, prompts.REFLECT_TASK, model=doc["model"])
        raw, ru = await llm.complete(
            [rmsg, {"role": "user", "content": prompts.reflect_user(items, final)}],
            model=doc["model"],
            session_id=doc_id,
            max_tokens=16000,
            temperature=0.1,
            reasoning=NO_REASONING,
        )
        fixed = _parse_labeled(raw)
        for bid, vi in fixed.items():
            if vi and vi != final.get(bid):
                final[bid] = vi
                yield "block", json.dumps({"id": bid, "vi": vi, "refined": True})
        u = llm.Usage(**json.loads(usage_json))
        u.add(ru)
        usage_json = json.dumps(u.dict())

    # ghi vào bộ nhớ dịch để lần sau — và bài sau — không phải dịch lại
    by_id = {it["id"]: it["text"] for it in items}
    db.tm_put([(by_id[bid], final.get(bid, ""), plains.get(bid, ""))
               for bid in set(final) | set(plains) if bid in by_id], doc["model"])

    doc = store.load(doc_id)
    doc["translations"].update(final)
    if plains:
        doc.setdefault("plain", {}).update({k: strip_md(v) for k, v in plains.items()})
    store.save(doc)
    total = _bump_usage(doc_id, usage_json)

    yield "done", json.dumps({
        "chunk": chunk_index,
        "total": len(chunks),
        "usage": total,                      # cộng dồn cả bài
        "run": json.loads(usage_json),       # riêng mẻ vừa dịch
        "reused": len(reused),               # lấy lại từ bộ nhớ dịch, miễn phí
        "generated": len(items),
    })


# --------------------------------------------------- pass 3: giải thích đoạn


async def explain_block(doc_id: str, block_id: str) -> tuple[dict, dict, dict]:
    """Trả về (ghi chú, chi phí lượt này, chi phí cộng dồn của bài)."""
    doc = store.load(doc_id)
    blocks = doc["blocks"]
    idx = next((i for i, b in enumerate(blocks) if b["id"] == block_id), None)
    if idx is None:
        raise KeyError(block_id)

    def neighbour(step: int) -> str:
        j = idx + step
        while 0 <= j < len(blocks):
            if blocks[j]["type"] in ("para", "caption"):
                return blocks[j]["text"]
            j += step
        return ""

    # caption gần nhất trong cùng mục — hình mà người đọc đang nhìn thấy
    nearby = ""
    for step in (1, -1):
        j = idx + step
        while 0 <= j < len(blocks) and abs(j - idx) <= 4:
            if blocks[j]["type"] == "caption":
                nearby = blocks[j]["text"]
                break
            j += step
        if nearby:
            break

    block = blocks[idx]
    raw, usage = await llm.complete(
        [
            llm.system_message(cached_prefix(doc), prompts.EXPLAIN_SYSTEM, model=doc["model"]),
            {"role": "user", "content": prompts.explain_user(
                block, neighbour(-1), neighbour(1),
                doc["translations"].get(block_id, ""), nearby,
            )},
        ],
        model=doc["model"],
        session_id=doc_id,
        max_tokens=8000,
        temperature=0.4,
        reasoning=LOW_REASONING,
    )
    note = llm.extract_json(raw)
    doc = store.load(doc_id)
    doc["notes"][block_id] = note
    total = llm.Usage(**doc.get("usage", {}))
    total.add(usage)
    doc["usage"] = total.dict()
    store.save(doc)
    return note, usage.dict(), total.dict()


# ------------------------------------------------------------ pass 4: slide

# Nhãn chủ đề rỗng nghĩa. Model rơi vào mấy chữ này khi nó chưa quyết được slide
# muốn nói gì — đúng thứ mà kiểu slide khẳng-định-và-bằng-chứng sinh ra để chặn.
_EMPTY_HEADLINES = {
    "giới thiệu", "tổng quan", "bối cảnh", "phương pháp", "cách làm", "kết quả",
    "thực nghiệm", "thí nghiệm", "thảo luận", "kết luận", "công trình liên quan",
    "nội dung", "mục lục", "động lực", "giới hạn", "tóm tắt", "đặt vấn đề",
    "introduction", "background", "method", "methods", "results", "discussion",
    "conclusion", "related work", "outline", "motivation", "overview",
}

# Số trên slide: 43, 3.14, 1,5, 92%, 1e-4. Bỏ số dính liền chữ ở cả hai đầu
# (b12, GPT-4, 2WikiMQA, 52566rz) — đó là mã khối, tên model, tên tập dữ liệu và
# định danh, không phải số liệu của bài.
_NUM = re.compile(r"(?<![\w.,])\d+(?:[.,]\d+)*(?:[eE][-+]?\d+)?(?![\w])")

# Đường dẫn nuốt trọn: github.com/52566rz/CIRAG có "52566" nhưng đó là định danh
# kho mã, không phải con số cần đối chiếu với bài.
_URLISH = re.compile(r"\b(?:https?://|www\.|\S+\.(?:com|org|net|io|edu|gov)\b)\S*")

# Nhãn node Mermaid: phần trong `[...]`, `{...}` hoặc `(...)` của một node.
_MMD_LABEL = re.compile(r"[\[{(]([^\[\]{}()]*)[\]})]")


def _bad_mermaid_labels(code: str) -> bool:
    """Nhãn node có dấu nháy kép lồng nhau -> mermaid im lặng không vẽ ra gì.

    `A["nhãn"]` là dạng ĐÚNG mà DIAGRAM_RULES yêu cầu, nên không thể chỉ tìm dấu
    nháy. Cái hỏng là nháy nằm bên trong nhãn: `A["câu "trích" ở giữa"]` — tức là
    số dấu nháy trong một nhãn khác 0 và khác 2.
    """
    return any(lb.count('"') not in (0, 2) for lb in _MMD_LABEL.findall(code))

# `content` và `closing` là tên dùng trong dàn ý và trong prompt dựng slide.
# Thiếu chúng ở đây thì `check_slides` đổi hết về `point`, và hai phép kiểm đi
# theo tên loại — nhãn phần bắt buộc với `content`, miễn kiểm số với slide kết —
# im lặng mất tác dụng. Mấy tên còn lại là của bản đầu, giữ cho deck cũ.
SLIDE_KINDS = ("title", "agenda", "section", "content", "closing",
               "point", "figure", "equation", "diagram", "takeaway", "thanks")

# Ngân sách, lấy từ nghiên cứu về slide khẳng-định-và-bằng-chứng (xem CLAUDE.md)
# rồi **quy đổi cho tiếng Việt**: cùng nội dung, tiếng Việt dài hơn tiếng Anh
# 10–25%. Áp thẳng con số của tiếng Anh (≤25 chữ / ≤70 ký tự) thì model lược cả
# hư từ để lọt trần, ra thứ tiếng Việt điện tín không ai nói ra miệng.
# Prompt đặt MỤC TIÊU, mấy hằng này là chỗ thật sự vỡ bố cục. Để hai con số bằng
# nhau thì mọi slide sát mức đều kêu, mà chốt chặn kêu oan vài lần là người dùng
# thôi đọc nó — lúc đó cảnh báo thật cũng trôi theo.
# Đo từ chính bộ slide mẫu người dùng đưa (SR_presentation_20_slides.pdf):
# trung vị 90 chữ/slide, cao nhất 121 — và người dùng còn muốn chi tiết hơn
# nữa. Con số 21 chữ của
# Garner & Alley là cho slide KHÔNG có thẻ và KHÔNG phải chú giải hình tiếng
# Anh — áp thẳng vào thiết kế này thì slide trống trơn, đúng thứ người dùng đã
# phàn nàn hai lần. Mục tiêu trong prompt là ~85 chữ.
MAX_WORDS = 175
MAX_CARD_WORDS = 46   # một thẻ chứa được chừng này trước khi tràn khung
MAX_HEADLINE_CHARS = 105   # mục tiêu ≤85; trên 105 mới thật sự tràn dòng thứ ba
MAX_BULLETS = 5         # mục tiêu 3–4; mục lục được phép tới 5 phần
# Chú giải hình đếm RIÊNG, không cộng vào MAX_WORDS: nó là chú thích của hình,
# không phải chữ tranh chỗ với thông điệp. Con số 21 chữ/slide trong nghiên cứu
# gốc cũng không tính chú thích hình. Mục tiêu trong prompt là ≤35.
MAX_FNOTE_WORDS = 42
NOTES_RANGE = (80, 200)

# Slide phải có thứ để mắt bám vào. Hai loại dưới đây được phép chỉ có chữ, vì
# chúng vốn là danh sách: thiết lập thí nghiệm và phần giới hạn.
_VISUAL_OPTIONAL = ("title", "agenda", "section", "thanks", "closing", "point")


def _norm_num(s: str) -> str:
    """`1,5` và `1.5` là một số. Bỏ dấu phân cách để so cho khớp."""
    return s.replace(",", ".").rstrip("0").rstrip(".") if "." in s or "," in s else s


def check_slides(doc: dict, deck: list[dict]) -> list[dict]:
    """Soát cơ học từng slide, gắn `warn` — bản sao của `content_kept()` cho slide.

    Khác `content_kept()` một điểm quan trọng: ở đây **cảnh báo chứ không chặn**.
    Pass dọn chữ không có ai soát nên phải chặn thẳng tay; còn slide thì người
    dùng có màn hình để tự sửa, mà cắt mất một slide còn tệ hơn là hiện nó ra kèm
    cờ đỏ.

    Phép kiểm đáng giá nhất là ràng buộc số liệu: một con số bịa trên slide là
    gán kết quả giả cho tác giả thật, và bằng mắt thường thì không ai bắt được.
    """
    text_of = {b["id"]: b["text"] or "" for b in doc["blocks"]}
    tr = doc.get("translations") or {}
    have_fig = {b.get("figure") for b in doc["blocks"] if b.get("figure")}
    used_fig: set[str] = set()

    for sl in deck:
        warn: list[str] = []
        kind = sl.get("kind") or "point"
        if kind not in SLIDE_KINDS:
            sl["kind"] = kind = "point"

        head = (sl.get("headline") or "").strip()
        bullets = [b for b in (sl.get("bullets") or []) if (b or "").strip()]
        sl["bullets"] = bullets

        # --- tiêu đề phải là câu khẳng định, không phải nhãn chủ đề
        if not head:
            warn.append("Thiếu tiêu đề.")
        else:
            bare = head.rstrip(":.").strip().lower()
            if bare in _EMPTY_HEADLINES:
                warn.append(f"“{head}” là nhãn chủ đề, không phải khẳng định. "
                            "Viết lại thành câu nói ra điều slide muốn chứng minh.")
            if head.endswith(":"):
                warn.append("Tiêu đề kết thúc bằng dấu hai chấm — dấu hiệu của "
                            "nhãn chủ đề kèm một đống gạch đầu dòng.")
            if head.endswith("?"):
                warn.append("Tiêu đề là câu hỏi; câu hỏi hoãn thông điệp lại "
                            "thay vì nói ra.")
            if len(head) > MAX_HEADLINE_CHARS:
                warn.append(f"Tiêu đề {len(head)} ký tự, dài quá {MAX_HEADLINE_CHARS} "
                            "— sẽ tràn xuống dòng thứ ba trên slide.")

        # --- ngân sách chữ. Đếm cả chữ trong thẻ, vì thẻ mới là chỗ chứa nội
        # dung chính ở thiết kế này; chú giải hình vẫn đếm riêng.
        fnote = (sl.get("figure_note") or "").strip()
        sl["figure_note"] = fnote
        on_face = slide_text(sl)
        words = len(on_face.split()) - len(fnote.split())
        sl["words"] = max(words, 0)
        sl["fnote_words"] = len(fnote.split())

        # --- nhãn phần: thiếu thì slide mất mốc định vị trong buổi nói
        if kind == "content" and not (sl.get("eyebrow") or "").strip():
            warn.append("Thiếu nhãn phần (`eyebrow`) — người nghe mất mốc định vị.")
        if words > MAX_WORDS and kind not in ("title", "thanks", "closing"):
            warn.append(f"{words} chữ trên slide, quá trần {MAX_WORDS}. "
                        "Đẩy bớt xuống phần lời nói.")
        # Thẻ có `overflow:hidden` — quá dài là chữ bị cắt mà không ai thấy.
        # Ước lượng thô: thẻ rộng ~300px ở lưới 4 cột, ~19px/chữ nên mỗi dòng
        # khoảng 5 chữ; thẻ cao nhất chứa được chừng 46 chữ.
        for c in (sl.get("cards") or []):
            n = len(((c or {}).get("title") or "").split())
            n += sum(len((b or "").split()) for b in ((c or {}).get("bullets") or []))
            if n > MAX_CARD_WORDS:
                warn.append(f"Thẻ “{(c or {}).get('title','')[:24]}” {n} chữ, quá "
                            f"{MAX_CARD_WORDS} — sẽ bị cắt mất chữ khi hiện ra.")
                break

        if len(bullets) > MAX_BULLETS:
            warn.append(f"{len(bullets)} gạch đầu dòng, tối đa {MAX_BULLETS}.")
        # chú giải hình + gạch đầu dòng cùng dài là quay lại slide chi chít chữ
        if fnote and len(fnote.split()) > MAX_FNOTE_WORDS:
            warn.append(f"Chú giải hình {len(fnote.split())} chữ, quá "
                        f"{MAX_FNOTE_WORDS}. Bỏ cụm “Hình minh hoạ…”, "
                        "“Hãy nhìn vào…” và vào thẳng nhãn trục.")
        # Alley giới hạn call-out ở 1–2 cái mỗi slide: ba cái trở lên làm rối
        # và mất tác dụng của chính cái hình.
        # Giới hạn 2 call-out của Alley chỉ đúng khi chữ nằm CẠNH hình — lúc đó
        # chúng là chú thích chỉ vào hình. Ở bố cục ảnh-ngang thì chữ nằm TRÊN
        # hình thành một khối riêng, không tranh chỗ, nên áp luật đó là kêu oan.
        if (lay_now if (lay_now := slide_layout(sl, doc["id"])) else "") == "figside" \
                and len(bullets) > 2:
            warn.append(f"Chữ nằm cạnh hình mà tới {len(bullets)} gạch đầu dòng. "
                        "Ở bố cục này tối đa 2 call-out, và chúng phải chú vào "
                        "từng phần của hình.")

        # --- lời người nói. Vách ngăn chỉ cần một câu nối nên không áp mốc này.
        n_notes = len((sl.get("notes") or "").split())
        if kind not in ("title", "thanks", "closing", "section", "agenda"):
            if n_notes < NOTES_RANGE[0]:
                warn.append(f"Lời nói chỉ {n_notes} chữ — quá ngắn cho một phút.")
            elif n_notes > NOTES_RANGE[1]:
                warn.append(f"Lời nói {n_notes} chữ — dài hơn một phút nói.")

        # --- hình: có thật, đúng một cái, không dùng lại
        fig = (sl.get("figure") or "").strip()
        if fig:
            if fig not in have_fig or store.image_path(doc["id"], fig) is None:
                warn.append(f"Không có ảnh nào mang mã “{fig}”.")
                sl["figure"] = ""
            elif fig in used_fig:
                warn.append(f"Ảnh “{fig}” đã dùng ở slide trước. "
                            "Mỗi hình chỉ nên xuất hiện một lần.")
            else:
                used_fig.add(fig)

        # --- hình cắt từ bài là hình TIẾNG ANH: trục, nhãn, chú giải bên trong
        # đều không sửa được. Không có chú giải tiếng Việt thì người nghe không
        # biết nhìn vào đâu, và slide coi như trống dù đã có ảnh.
        if sl.get("figure") and not fnote and not sl.get("illus"):
            warn.append("Slide có hình nhưng thiếu chú giải tiếng Việt. Hình cắt "
                        "từ bài báo có nhãn tiếng Anh — cần 1–3 câu dịch nhãn "
                        "trục và chỉ rõ nhìn vào đâu.")
        elif fnote and not sl.get("figure"):
            sl["figure_note"] = fnote = ""

        # --- mỗi slide nội dung phải có một thứ để nhìn
        cards_n = len([c for c in (sl.get("cards") or []) if (c or {}).get("title")])
        has_visual = bool(sl.get("figure") or (sl.get("diagram") or "").strip()
                          or (sl.get("equation") or "").strip())
        # Bằng chứng và thẻ tranh nhau chiều cao của cùng một khung. Ba thẻ cộng
        # một sơ đồ thì sơ đồ chỉ còn ~140px — không tràn nên bộ đo im lặng, mà
        # nhìn thì nó bé bằng con tem. Chọn một trong hai.
        if has_visual and cards_n > 2 and not sl.get("equation"):
            warn.append(f"{cards_n} thẻ cùng với hình/sơ đồ trên một slide: "
                        "bằng chứng chỉ còn một vệt hẹp. Bớt còn 2 thẻ, hoặc bỏ "
                        "hình và để thẻ làm phần nhìn.")
        # Thẻ có nền màu, chip icon và tiêu đề đậm — tự nó đã là cấu trúc để mắt
        # bám vào. Đòi thêm hình ở slide bốn thẻ là đẩy model gắn sơ đồ trang trí.
        if not has_visual and not cards_n and kind not in _VISUAL_OPTIONAL:
            warn.append("Slide không có hình, sơ đồ hay công thức nào — chỉ một "
                        "câu chữ giữa slide trắng. Gắn hình từ danh mục, hoặc vẽ "
                        "một sơ đồ Mermaid.")

        # --- sơ đồ đúng cú pháp Mermaid mà DIAGRAM_RULES yêu cầu
        dia = (sl.get("diagram") or "").strip()
        if dia:
            if not dia.splitlines()[0].strip().startswith("flowchart"):
                warn.append("Sơ đồ không bắt đầu bằng `flowchart TD` hoặc "
                            "`flowchart LR` nên sẽ không vẽ ra.")
            if _bad_mermaid_labels(dia):
                warn.append("Nhãn sơ đồ có dấu ngoặc kép lồng nhau — mermaid sẽ lỗi.")
            # Slide có thẻ thì chỗ còn lại cho sơ đồ là dải ngang thấp. Sơ đồ
            # dựng dọc bị bóp còn một vệt hẹp giữa slide — không tràn khung nên
            # bộ đo không kêu, mà nhìn thì chữ trong node không đọc nổi.
            if sl.get("cards") and dia.splitlines()[0].strip().startswith("flowchart TD"):
                warn.append("Sơ đồ dựng dọc (`flowchart TD`) trên slide đã có thẻ: "
                            "chỗ còn lại là dải ngang thấp nên sơ đồ bị bóp hẹp. "
                            "Đổi sang `flowchart LR`, hoặc bỏ bớt thẻ.")

        # --- ràng buộc nguồn + ràng buộc số liệu
        src_ids = [str(i) for i in (sl.get("source_block_ids") or [])]
        src_ids = [i for i in src_ids if i in text_of]
        sl["source_block_ids"] = src_ids
        if kind in ("title", "thanks", "closing"):
            # hai loại này không lấy nội dung từ bài, nên không có gì để đối
            # chiếu: số trên đó là năm hội nghị, độ dài buổi nói, mã kho nguồn
            pass
        elif not src_ids and kind != "agenda":
            # mục lục nói về chính buổi nói, không trích gì từ bài
            warn.append("Slide không khai nguồn — không kiểm được số liệu.")
        else:
            pool = " ".join(text_of[i] + " " + tr.get(i, "") for i in src_ids)
            pool_nums = {_norm_num(m) for m in _NUM.findall(pool)}
            shown = _URLISH.sub(" ", head + " " + " ".join(bullets) + " " + fnote)
            orphan = sorted({
                m for m in _NUM.findall(shown)
                if _norm_num(m) not in pool_nums
            })
            # số thứ tự và phần trăm tròn trĩnh thì bỏ qua, ồn hơn là hữu ích
            orphan = [n for n in orphan if not (n.isdigit() and int(n) <= 12)]
            if orphan:
                warn.append("Số không có trong khối nguồn: " + ", ".join(orphan)
                            + ". Đối chiếu lại bài trước khi trình bày.")

        # Chốt chặn thật cho việc tràn khung: đo bằng metric font thật thay vì
        # đếm chữ. `.card` và `.slide` đều `overflow:hidden` nên chữ thừa biến
        # mất lặng lẽ — đếm chữ không bao giờ bắt được, đo mới bắt được.
        try:
            from . import slide_fit
            scale = slide_fit.autofit(sl, lay_now)
            ft = slide_fit.fit(sl, lay_now, scale)
            # Co hết cỡ vẫn tràn -> bỏ dần thứ ÍT QUAN TRỌNG NHẤT: chú thuật ngữ
            # trước (do công cụ tự gắn, không phải chữ người dùng viết), rồi mới
            # tới hộp chốt. Bỏ lặng lẽ thì tệ nên ghi vào `dropped` để báo lại.
            dropped = []
            for field, label in (("terms", "chú thuật ngữ"), ("callout", "hộp chốt")):
                if ft["over"] <= 0 or not sl.get(field):
                    continue
                stash = sl[field]
                sl[field] = [] if field == "terms" else None
                scale = slide_fit.autofit(sl, lay_now)
                ft2 = slide_fit.fit(sl, lay_now, scale)
                if ft2["over"] > 0 and field == "callout":
                    sl[field] = stash          # bỏ rồi vẫn tràn thì giữ lại
                else:
                    ft = ft2
                    dropped.append(label)
            ft["scale"] = scale
            ft["dropped"] = dropped
            sl["fit"] = ft
            if dropped:
                warn.append("Không đủ chỗ nên đã ẩn " + ", ".join(dropped)
                            + " trên slide này. Bớt một ý để lấy lại chỗ.")
            if ft["over"] > 0:
                # co hết cỡ vẫn không vừa -> vấn đề là nội dung, không phải cỡ chữ
                warn.append(f"Co chữ hết mức mà vẫn cao hơn khung {ft['over']}px "
                            "— phần dưới sẽ bị cắt. Bớt một thẻ, bớt ý trong thẻ, "
                            "hoặc bỏ hộp chốt.")
        except Exception:  # noqa: BLE001
            pass

        if cjk_leak(head + " ".join(bullets) + (sl.get("notes") or ""),
                    " ".join(text_of.values())):
            warn.append("Có chữ Hán/Kana lọt vào slide.")

        # Prompt vẽ minh hoạ, dựng sẵn từ nội dung slide. Công cụ không tự gọi
        # model vẽ — người dùng mang prompt này sang công cụ họ tin dùng.
        if not sl.get("figure") and not (sl.get("diagram") or "").strip():
            from .slide_theme import art_prompt
            sl["art_prompt"] = art_prompt(sl)
            # Chỗ trống thật sự còn lại. Slide đã kín thẻ và hộp chốt thì không
            # còn chỗ cho ảnh — hiện ô chờ cao 60px là mời người dùng làm một
            # việc vô nghĩa.
            sl["art_room"] = slide_fit.room_for_art(sl, lay_now, scale)
        else:
            sl.pop("art_prompt", None)
            sl.pop("art_room", None)

        sl["warn"] = warn

    _check_agenda(deck)
    return deck


# Nhãn rỗng nghĩa trong mục lục: người nghe đọc xong vẫn không biết sắp nghe gì.
_BARE_AGENDA = {"phương pháp", "cách làm", "kết quả", "thực nghiệm", "giới thiệu",
                "mở đầu", "kết luận", "thảo luận", "tổng quan", "bối cảnh",
                "giới hạn", "đánh giá", "động lực", "bài toán"}


def _check_agenda(deck: list[dict]) -> None:
    """Mục lục phải khớp với các vách ngăn phía sau, và không được là nhãn rỗng.

    Mục lục hứa với người nghe một lộ trình; vách ngăn là cột mốc trên lộ trình
    đó. Hai bên lệch nhau thì tấm bản đồ thành sai, tệ hơn là không có bản đồ.
    """
    agenda = next((s for s in deck if (s.get("kind") or "") == "agenda"), None)
    sections = [s for s in deck if (s.get("kind") or "") == "section"]

    for s in sections:
        if [b for b in (s.get("bullets") or []) if (b or "").strip()]:
            s.setdefault("warn", []).append(
                "Vách ngăn chỉ nên có tên phần — bỏ gạch đầu dòng đi.")

    if agenda is None:
        if sections:
            sections[0].setdefault("warn", []).append(
                "Có vách ngăn nhưng cả bộ không có slide mục lục nào.")
        return

    items = [(c.get("title") or "").strip()
             for c in (agenda.get("cards") or []) if (c or {}).get("title")]
    items += [b.strip() for b in (agenda.get("bullets") or []) if (b or "").strip()]
    if len(items) < 3:
        agenda.setdefault("warn", []).append(
            f"Mục lục chỉ {len(items)} mục — nên có 3–5 phần.")
    bare = [b for b in items if b.rstrip(".").lower() in _BARE_AGENDA]
    if bare:
        agenda.setdefault("warn", []).append(
            "Mục lục có nhãn rỗng nghĩa: " + ", ".join(f"“{b}”" for b in bare)
            + ". Viết thành cụm nói rõ phần đó bàn gì.")
    if sections and len(sections) > len(items):
        agenda.setdefault("warn", []).append(
            f"{len(sections)} vách ngăn nhưng mục lục chỉ liệt kê {len(items)} phần.")


def figure_shape(doc_id: str, fig: str) -> tuple[str, float]:
    """Đo ảnh thật rồi phân loại: ('wide' | 'square' | 'tall', tỉ lệ ngang/dọc).

    Bố cục phải biết hình rộng hay cao TRƯỚC khi xếp chỗ. Biểu đồ hai cột của
    bài báo thường rộng gấp ba lần chiều cao — nhét vào nửa slide thì chữ trong
    hình bé đến mức vô nghĩa. Ngược lại, một hình kiến trúc dựng dọc mà cho tràn
    khung thì thừa mênh mông hai bên.
    """
    p = store.image_path(doc_id, fig) if fig else None
    if p is None:
        return "none", 0.0
    try:
        from PIL import Image
        with Image.open(p) as im:
            w, h = im.size
    except Exception:  # noqa: BLE001
        return "square", 1.0
    r = (w / h) if h else 1.0
    return ("wide" if r >= 1.9 else "tall" if r <= 0.85 else "square"), r


def slide_layout(sl: dict, doc_id: str = "") -> str:
    """Chọn bố cục cho slide, suy ra TỪ NỘI DUNG chứ không hỏi model.

    Deck chuyên nghiệp dùng 3–5 kiểu bố cục; một kiểu cho cả bộ là thứ làm hai
    mươi slide trông giống hệt nhau. Nhưng để model tự khai bố cục thì nó khai
    sai — nó không biết cuối cùng slide có bao nhiêu chữ. Suy ra từ nội dung thì
    không sai được.

    Bản xem trước trong app (`slideLayout()` bên `app.js`) phải theo đúng luật
    này, nếu không xem trước nói dối.
    """
    kind = sl.get("kind") or "content"
    if kind in ("title", "agenda", "section"):
        return kind
    if kind in ("closing", "thanks"):
        return "closing"

    drawn = bool((sl.get("diagram") or "").strip() or (sl.get("equation") or "").strip())
    cards = [c for c in (sl.get("cards") or []) if (c or {}).get("title")]
    text = bool(cards or [b for b in (sl.get("bullets") or []) if (b or "").strip()])

    # THẺ KHÔNG BAO GIỜ vào cột hẹp. Cột chữ của bố cục hai cột chỉ rộng 44%,
    # nhét ba thẻ vào đó thì mỗi thẻ còn ~90px và chữ vỡ một từ một dòng — đo
    # bằng `slide_fit` thấy thừa tới 596px trên khung 620px. Có thẻ thì thẻ trải
    # hết bề ngang ở trên, thứ để nhìn nằm dưới.
    has_vis = bool(sl.get("figure") or drawn)
    if cards and has_vis:
        return "figwide"
    if sl.get("figure"):
        shape, _ = figure_shape(doc_id, sl["figure"])
        if shape == "wide":
            return "figwide"
        return "figside" if text else "figfull"
    if drawn:
        return "split" if text else "figfull"
    if cards:
        return "cards"
    return "list"


def slide_text(sl: dict) -> str:
    """Toàn bộ chữ hiện trên mặt slide — dùng để soát và để dò thuật ngữ."""
    parts = [sl.get("headline") or "", sl.get("sub") or "",
             sl.get("figure_note") or ""]
    parts += [b for b in (sl.get("bullets") or []) if b]
    for c in sl.get("cards") or []:
        parts += [(c or {}).get("title") or "", (c or {}).get("meta") or ""]
        parts += [b for b in ((c or {}).get("bullets") or []) if b]
    for st in sl.get("stats") or []:
        parts += [(st or {}).get("value") or "", (st or {}).get("label") or ""]
    co = sl.get("callout") or {}
    parts += [co.get("title") or "", co.get("body") or ""]
    return " ".join(parts)


def attach_terms(doc: dict, deck: list[dict]) -> None:
    """Thuật ngữ xuất hiện LẦN ĐẦU trên slide nào thì gắn chú thích vào slide đó.

    Người nghe gặp `provenance mapping` lần đầu giữa buổi mà không ai giải nghĩa
    thì phần còn lại của slide trôi qua vô ích. Bảng thuật ngữ đã chốt ở `brief`
    có sẵn `gloss` cho từng từ, nên chỗ này **không tốn thêm lượt gọi model** và
    lại chắc chắn nhất quán với bản dịch.

    Chỉ gắn tối đa 2 thuật ngữ mỗi slide: ba dòng chú thích trở lên thì nó thành
    một khối chữ thứ hai, đúng thứ đang tránh.
    """
    gl = [g for g in ((doc.get("brief") or {}).get("glossary") or [])
          if (g.get("en") or "").strip() and (g.get("gloss") or "").strip()]
    # từ dài trước: khớp "chain of thought" trước khi khớp "chain"
    gl.sort(key=lambda g: -len(g["en"]))
    seen: set[str] = set()
    for sl in deck:
        if (sl.get("kind") or "") in ("title", "section", "agenda"):
            sl["terms"] = []
            continue
        text = slide_text(sl).lower()
        hits = []
        for g in gl:
            en = g["en"].strip()
            key = en.lower()
            if key in seen or key not in text:
                continue
            if not re.search(r"(?<!\w)" + re.escape(key) + r"(?!\w)", text):
                continue
            seen.add(key)
            hits.append({"en": en, "gloss": g["gloss"].strip()})
            if len(hits) == 1:
                break
        sl["terms"] = hits


def number_sections(deck: list[dict]) -> None:
    """Đánh số phần cho vách ngăn và cho nhãn đầu mục của mọi slide trong phần.

    Mục lục đánh 01, 02, 03; vách ngăn ghi "PHẦN 2 / 3". Nhưng slide nội dung
    thì chỉ có tên phần trơn, nên đọc giữa chừng không biết mình đang ở đâu
    trong lộ trình vừa hứa. Gắn cùng con số đó vào nhãn: "2 · CƠ CHẾ CIRAG".

    Tính từ deck chứ không hỏi model — người dùng xoá hay đổi thứ tự slide thì
    số phải tự đúng theo.
    """
    secs = [s for s in deck if (s.get("kind") or "") == "section"]
    total = len(secs)
    cur = 0
    for sl in deck:
        sl.pop("_secno", None)
        if (sl.get("kind") or "") == "section":
            cur += 1
            sl["_secno"] = cur
            sl["eyebrow"] = f"PHẦN {cur} / {total}"
        elif cur and (sl.get("kind") or "content") == "content":
            sl["_secno"] = cur


def section_icons(deck: list[dict]) -> dict[str, str]:
    """Mỗi vách ngăn lấy hình ĐẦU TIÊN của phần nó mở ra, làm biểu tượng.

    Alley khuyên đúng việc này: mục lục dùng chính hình đầu của mỗi phần, rồi
    hình đó xuất hiện lại ở vách ngăn — người nghe thấy hình quen là biết mình
    vừa sang phần mới. Tính từ deck chứ không hỏi model: model không đoán được
    người dùng sẽ xoá hay đổi thứ tự slide nào.

    Trả về {mã slide vách ngăn: mã hình}.
    """
    out: dict[str, str] = {}
    cur: str | None = None
    for sl in deck:
        if (sl.get("kind") or "") == "section":
            cur = sl.get("id")
            continue
        if cur and sl.get("figure") and cur not in out:
            out[cur] = sl["figure"]
    return out


def agenda_from_sections(sl: dict, outline: dict) -> dict:
    """Tên phần trên mục lục lấy thẳng từ dàn ý, không hỏi model.

    Cùng lý do với `section_icons()`: người dùng đã chốt tên phần ở bước soạn
    nội dung, model không có quyền đặt lại. Hỏi nó thì nó rơi về nhãn rỗng
    (`Thực nghiệm`, `Kết luận`) — đã vấp thật — và mục lục lệch với các vách
    ngăn phía sau, tức người nghe mất luôn tấm bản đồ.
    """
    names = [(x.get("name") or "").strip()
             for x in (outline.get("sections") or []) if (x.get("name") or "").strip()]
    if not names:
        return sl
    cards = [c for c in (sl.get("cards") or []) if isinstance(c, dict)]
    out = []
    for i, name in enumerate(names):
        c = dict(cards[i]) if i < len(cards) else {}
        c["title"] = name
        out.append(c)
    sl["cards"] = out
    return sl


def _figure_catalog(doc: dict) -> list[dict]:
    """Hình/bảng thật sự cắt được ra file, kèm chú thích tiếng Việt để model chọn."""
    tr = doc.get("translations") or {}
    out = []
    for b in doc["blocks"]:
        fig = b.get("figure")
        if not fig or b["type"] == "equation":
            continue
        if store.image_path(doc["id"], fig) is None:
            continue
        out.append({"id": fig, "page": b.get("page"),
                    "caption": tr.get(b["id"]) or b.get("text") or ""})
    return out


def _number_slides(payload: dict) -> dict:
    """Đánh mã s1, s2… cho cả hai ngăn. Mã phải `isalnum()` — nó đi vào URL."""
    n = 1
    for key in ("deck", "backup"):
        for sl in payload.get(key) or []:
            sl["id"] = f"s{n}"
            n += 1
    return payload


# ============================================ bước 1 của pass 4: soạn nội dung

# Số mục dựng trong MỘT lượt gọi ở bước 2. Mẻ nhỏ là chỗ "chi tiết" thật sự đến
# từ: dựng cả hai mươi slide trong một lượt thì mỗi slide được chia chưa tới một
# nghìn token đầu ra, và model tự cắt cho vừa — ra thứ nhạt đều. Bốn mục một lượt
# thì mỗi slide rộng gấp năm. Prefix vẫn ấm nên phần input gần như không tốn thêm.
RENDER_BATCH = 4


def _number_outline(outline: dict) -> dict:
    """Đánh mã o1, o2… cho từng mục. Mã đi vào URL nên phải `isalnum()`."""
    n = 1
    for key in ("items", "backup"):
        for it in outline.get(key) or []:
            it["id"] = f"o{n}"
            n += 1
    return outline


_OUTLINE_KINDS = ("title", "agenda", "section", "content", "closing")
# Mục vách ngăn và mục lục không lấy nội dung từ bài, nên không có gì để đối
# chiếu — cùng lý do với `title`/`thanks` bên `check_slides`.
_NO_SOURCE = ("title", "agenda", "section", "closing")


def check_outline(doc: dict, outline: dict) -> dict:
    """Soát dàn ý trước khi người dùng đọc — anh em của `check_slides()`.

    Bắt lỗi ở đây rẻ hơn hẳn: một con số bịa hay một nhãn chủ đề rỗng phát hiện
    lúc còn là dàn ý thì sửa một dòng, còn để nó đi qua bước dựng thì phải dựng
    lại cả slide. Vẫn **cảnh báo chứ không chặn**, vì người dùng có màn hình để
    tự sửa.
    """
    text_of = {b["id"]: b["text"] or "" for b in doc["blocks"]}
    tr = doc.get("translations") or {}
    have_fig = {b.get("figure") for b in doc["blocks"] if b.get("figure")}
    used_fig: set[str] = set()
    names = {(s.get("name") or "").strip() for s in outline.get("sections") or []}

    for it in (outline.get("items") or []) + (outline.get("backup") or []):
        warn: list[str] = []
        kind = it.get("kind") or "content"
        if kind not in _OUTLINE_KINDS:
            it["kind"] = kind = "content"

        msg = (it.get("message") or "").strip()
        it["message"] = msg
        pts = [p for p in (it.get("points") or []) if (p or "").strip()]
        it["points"] = pts

        if not msg:
            warn.append("Thiếu thông điệp.")
        else:
            bare = msg.rstrip(":.").strip().lower()
            if bare in _EMPTY_HEADLINES:
                warn.append(f"“{msg}” là nhãn chủ đề, không phải khẳng định. "
                            "Viết lại thành câu nói ra điều slide muốn chứng minh.")
            if msg.endswith(":"):
                warn.append("Thông điệp kết thúc bằng dấu hai chấm — dấu hiệu của "
                            "nhãn chủ đề kèm một đống gạch đầu dòng.")
            if msg.endswith("?"):
                warn.append("Thông điệp là câu hỏi; câu hỏi hoãn nội dung lại "
                            "thay vì nói ra.")

        if kind == "content":
            if not pts:
                warn.append("Không có ý nào. Bước dựng slide không nghĩ hộ nội "
                            "dung mới, nên mục rỗng ở đây thành slide rỗng.")
            elif len(pts) < 3:
                warn.append(f"Chỉ {len(pts)} ý — slide sẽ thưa. Nhắm 3–5 ý.")
            if (it.get("section") or "").strip() and names \
                    and it["section"].strip() not in names:
                warn.append(f"Phần “{it['section']}” không có trong mục lục.")

        # --- bằng chứng: mỗi mục nội dung phải có thứ để nhìn
        ev = it.get("evidence") or {}
        if not isinstance(ev, dict):
            ev = {}
        ev.setdefault("kind", "none")
        ev.setdefault("figure", "")
        ev.setdefault("what", "")
        it["evidence"] = ev
        fig = (ev.get("figure") or "").strip()
        if fig:
            if fig not in have_fig or store.image_path(doc["id"], fig) is None:
                warn.append(f"Không có ảnh nào mang mã “{fig}”.")
                ev["figure"] = fig = ""
            elif fig in used_fig:
                warn.append(f"Ảnh “{fig}” đã dùng ở mục trước. "
                            "Mỗi hình chỉ nên xuất hiện một lần.")
            else:
                used_fig.add(fig)
        if ev["kind"] == "figure" and not fig:
            ev["kind"] = "diagram" if ev.get("what") else "none"
        if kind == "content" and ev["kind"] == "none":
            warn.append("Chưa chọn bằng chứng — slide sẽ chỉ có chữ. Gắn một hình "
                        "trong danh mục, hoặc mô tả cơ chế để vẽ sơ đồ.")
        if ev["kind"] == "diagram" and not (ev.get("what") or "").strip():
            warn.append("Định vẽ sơ đồ mà chưa mô tả cơ chế cần vẽ.")

        # --- ràng buộc nguồn + ràng buộc số liệu, y như `check_slides`
        src = [str(i) for i in (it.get("source_block_ids") or []) if str(i) in text_of]
        it["source_block_ids"] = src
        if kind in _NO_SOURCE:
            pass
        elif not src:
            warn.append("Mục không khai nguồn — không kiểm được số liệu.")
        else:
            pool = " ".join(text_of[i] + " " + tr.get(i, "") for i in src)
            pool_nums = {_norm_num(m) for m in _NUM.findall(pool)}
            shown = _URLISH.sub(" ", msg + " " + " ".join(pts))
            orphan = sorted({m for m in _NUM.findall(shown)
                             if _norm_num(m) not in pool_nums})
            orphan = [n for n in orphan if not (n.isdigit() and int(n) <= 12)]
            if orphan:
                warn.append("Số không có trong khối nguồn: " + ", ".join(orphan)
                            + ". Đối chiếu lại bài trước khi dựng slide.")

        if cjk_leak(msg + " " + " ".join(pts), " ".join(text_of.values())):
            warn.append("Có chữ Hán/Kana lọt vào mục này.")

        it["warn"] = warn

    secs = outline.get("sections") or []
    if not 3 <= len(secs) <= 4:
        outline["warn"] = [f"Chia {len(secs)} phần. Buổi nói thường vừa 3–4 phần; "
                           "nhiều hơn là người nghe không giữ nổi bản đồ trong đầu."]
    else:
        outline["warn"] = []
    return outline


async def make_outline(doc_id: str) -> tuple[dict, dict, dict]:
    """Bước 1: soạn nội dung buổi nói. Trả (dàn ý, chi phí lượt này, cộng dồn).

    Rẻ hơn hẳn bước dựng slide vì đầu ra chỉ là chữ, không có sơ đồ, không có
    thẻ, không có lời người nói. Đi sau `cached_prefix(doc)` nên phần input gần
    như miễn phí — `minutes` và mọi thứ thay đổi theo request nằm ở message
    `user`, tuyệt đối không lẫn vào prefix.
    """
    import time

    doc = store.load(doc_id)
    brief = doc.get("brief") or {}
    user = prompts.outline_user(brief, doc["blocks"], doc.get("translations") or {},
                                _figure_catalog(doc))
    sysmsg = llm.system_message(cached_prefix(doc), prompts.OUTLINE_TASK,
                                model=doc["model"])
    raw, usage = await llm.complete(
        [sysmsg, {"role": "user", "content": user}],
        model=doc["model"], session_id=doc_id,
        max_tokens=16000, temperature=0.4, reasoning=LOW_REASONING,
    )
    if cjk_leak(raw, user):
        raw, u2 = await llm.complete(
            [sysmsg, {"role": "user", "content": user
                      + "\n\nLẦN TRƯỚC BẠN ĐÃ TRẢ VỀ TIẾNG TRUNG. Viết lại toàn bộ"
                        " bằng TIẾNG VIỆT, không một chữ Hán nào."}],
            model=doc["model"], session_id=doc_id,
            max_tokens=16000, temperature=0.2, reasoning=LOW_REASONING,
        )
        usage.add(u2)

    outline = llm.extract_json(raw)
    outline.setdefault("items", [])
    outline.setdefault("backup", [])
    outline.setdefault("sections", [])
    _number_outline(outline)

    doc = store.load(doc_id)
    check_outline(doc, outline)
    outline["created_at"] = time.time()
    slides = doc.get("slides") or {}
    slides["outline"] = outline
    doc["slides"] = slides
    total = llm.Usage(**doc.get("usage", {}))
    total.add(usage)
    doc["usage"] = total.dict()
    store.save(doc)
    return outline, usage.dict(), total.dict()


async def render_deck(doc_id: str):
    """Bước 2: dựng slide từ dàn ý đã duyệt, từng mẻ một.

    Sinh ra các cặp (tên sự kiện, JSON) để `main.py` đẩy thẳng ra SSE. Mỗi mẻ
    lưu ngay vào DB: mất kết nối giữa chừng thì phần đã dựng vẫn còn, bấm lại
    chỉ dựng nốt phần thiếu — cùng lối với dịch từng mẻ ở pass 2.

    Slide người dùng đã sửa tay (`edited`) thì **giữ nguyên, không dựng lại** —
    dựng đè lên là xoá công sức của họ mà không báo.
    """
    import time

    doc = store.load(doc_id)
    slides = doc.get("slides") or {}
    outline = slides.get("outline") or {}
    items = list(outline.get("items") or [])
    backs = list(outline.get("backup") or [])
    if not items:
        raise KeyError("outline")

    brief = doc.get("brief") or {}
    figs = _figure_catalog(doc)
    todo = [(it, "deck") for it in items] + [(it, "backup") for it in backs]

    # slide đã dựng từ trước, tra theo mã mục — để bấm lại chỉ dựng phần thiếu
    have = {sl.get("outline_id"): (sl, key)
            for key in ("deck", "backup")
            for sl in (slides.get(key) or []) if sl.get("outline_id")}
    keep = {oid for oid, (sl, _) in have.items() if sl.get("edited")}

    out: dict[str, list] = {"deck": [], "backup": []}
    used_figs: list[str] = []
    n_done = 0
    total = llm.Usage(**doc.get("usage", {}))
    yield "start", json.dumps({"total": len(todo)})

    for i in range(0, len(todo), RENDER_BATCH):
        lot = todo[i:i + RENDER_BATCH]
        # mục đã có slide và người dùng đã sửa tay -> chép lại, không gọi model
        san = [(it, key) for it, key in lot if it.get("id") not in keep]
        for it, key in lot:
            if it.get("id") in keep:
                sl = have[it["id"]][0]
                out[key].append(sl)
                if sl.get("figure"):
                    used_figs.append(sl["figure"])
                n_done += 1
        if not san:
            yield "batch", json.dumps({"done": n_done, "total": len(todo),
                                       "skipped": True})
            continue

        user = prompts.render_user(outline, [it for it, _ in san], brief,
                                   figs, used_figs)
        raw, usage = await llm.complete(
            [llm.system_message(cached_prefix(doc), prompts.SLIDES_TASK,
                                model=doc["model"]),
             {"role": "user", "content": user}],
            model=doc["model"], session_id=doc_id,
            max_tokens=9000, temperature=0.4, reasoning=LOW_REASONING,
        )
        payload = llm.extract_json(raw)
        made = payload.get("slides") or payload.get("deck") or []
        if isinstance(made, dict):
            made = [made]
        by_oid = {str(sl.get("outline_id")): sl for sl in made if isinstance(sl, dict)}

        for j, (it, key) in enumerate(san):
            sl = by_oid.get(it.get("id")) or (made[j] if j < len(made) else None)
            if not isinstance(sl, dict):
                # model bỏ sót mục này -> dựng tạm từ chính dàn ý, đừng mất mục
                sl = {"kind": it.get("kind") or "content",
                      "headline": it.get("message") or "",
                      "bullets": list(it.get("points") or []),
                      "figure": (it.get("evidence") or {}).get("figure") or "",
                      "notes": ""}
                sl["warn_pre"] = ["Model không dựng mục này; đây là bản chép "
                                  "thẳng từ dàn ý. Bấm làm lại slide này."]
            sl["outline_id"] = it.get("id")
            sl["source_block_ids"] = it.get("source_block_ids") or []
            sl.pop("edited", None)
            if sl.get("figure"):
                used_figs.append(sl["figure"])
            out[key].append(sl)
            n_done += 1

        total.add(usage)
        # lưu ngay từng mẻ: mất kết nối thì phần đã dựng vẫn còn
        doc = store.load(doc_id)
        cur = doc.get("slides") or {}
        cur["outline"] = outline
        cur["deck"] = list(out["deck"])
        cur["backup"] = list(out["backup"])
        cur["created_at"] = time.time()
        _number_slides(cur)
        for sl in cur["deck"]:
            if (sl.get("kind") or "") == "agenda":
                agenda_from_sections(sl, outline)
        check_slides(doc, cur["deck"])
        check_slides(doc, cur["backup"])
        doc["slides"] = cur
        doc["usage"] = total.dict()
        store.save(doc)
        yield "batch", json.dumps({"done": n_done, "total": len(todo),
                                   "run": usage.dict(), "sum": total.dict()},
                                  ensure_ascii=False)

    doc = store.load(doc_id)
    yield "done", json.dumps({"slides": doc.get("slides") or {},
                              "total": total.dict()}, ensure_ascii=False)


async def make_slides(doc_id: str) -> tuple[dict, dict, dict]:
    """Chạy liền cả hai bước — soạn nội dung rồi dựng slide. Không có chỗ soát.

    Giữ lại cho ai muốn một nút bấm là xong. Đường dùng thật là hai bước tách
    rời: `make_outline()` cho người dùng đọc và sửa, rồi `render_deck()` dựng
    từ dàn ý ĐÃ DUYỆT. Tách ra vì gộp lại thì model phải cùng lúc nghĩ nội dung
    và lo khuôn dạng, và phần lớn chú ý của nó rơi vào khuôn dạng.
    """
    _, run, _ = await make_outline(doc_id)
    async for ev, data in render_deck(doc_id):
        if ev == "done":
            payload = json.loads(data)
            return payload["slides"], run, payload["total"]
    doc = store.load(doc_id)
    return doc.get("slides") or {}, run, doc.get("usage") or {}


async def regen_slide(doc_id: str, slide_id: str, hint: str = "") -> tuple[dict, dict, dict]:
    """Dựng lại đúng một slide, giữ nguyên vị trí của nó trong bộ."""
    doc = store.load(doc_id)
    slides = doc.get("slides") or {}
    where = next(((k, i) for k in ("deck", "backup")
                  for i, s in enumerate(slides.get(k) or [])
                  if s.get("id") == slide_id), None)
    if where is None:
        raise KeyError(slide_id)
    key, idx = where
    old = slides[key][idx]

    raw, usage = await llm.complete(
        [
            llm.system_message(cached_prefix(doc), prompts.SLIDES_TASK, model=doc["model"]),
            {"role": "user", "content": prompts.slide_regen_user(
                old, doc.get("brief") or {}, hint)},
        ],
        model=doc["model"], session_id=doc_id,
        max_tokens=4000, temperature=0.5, reasoning=LOW_REASONING,
    )
    new = llm.extract_json(raw)
    if isinstance(new.get("deck"), list) and new["deck"]:
        new = new["deck"][0]            # model đôi khi vẫn bọc trong `deck`
    new["id"] = slide_id
    new.pop("edited", None)

    doc = store.load(doc_id)
    slides = doc.get("slides") or {"deck": [], "backup": []}
    check_slides(doc, [new])
    slides[key][idx] = new
    doc["slides"] = slides
    total = llm.Usage(**doc.get("usage", {}))
    total.add(usage)
    doc["usage"] = total.dict()
    store.save(doc)
    return new, usage.dict(), total.dict()


def mark_stale(doc: dict, ids) -> None:
    """Khối nguồn vừa bị sửa -> slide dựa trên nó không còn đáng tin.

    Cùng lý do với `_forget()` bên `main.py`: slide viết theo đoạn văn cũ mà giữ
    nguyên thì người dùng mang lên trình bày một điều bài không còn nói. Không
    xoá — chỉ gắn cờ, vì công sức sửa tay của họ nằm trong đó.
    """
    slides = doc.get("slides") or {}
    touched = set(ids)
    for key in ("deck", "backup"):
        for sl in slides.get(key) or []:
            if touched & set(sl.get("source_block_ids") or []):
                sl["stale"] = True
            elif sl.get("figure") and sl["figure"] in touched:
                sl["stale"] = True
    # Dàn ý cũng dựa trên chính các khối đó, và nó là thứ dựng ra slide — bỏ sót
    # thì lần dựng sau đẻ lại đúng cái slide đã sai.
    for it in (slides.get("outline") or {}).get("items") or []:
        if touched & set(it.get("source_block_ids") or []):
            it["stale"] = True
        elif (it.get("evidence") or {}).get("figure") in touched:
            it["stale"] = True


# ------------------------------------- giải thích đoạn người đọc bôi vàng


async def explain_highlight(doc_id: str, hl_id: str) -> tuple[str, dict, dict]:
    """Giải thích đúng đoạn người đọc bôi. Trả (ghi chú, chi phí lượt, cộng dồn).

    Đi sau `cached_prefix(doc)` như pass giải thích khối, nên toàn văn bài đã nằm
    trong phần cache ấm — mỗi lần bôi thêm gần như chỉ trả tiền đầu ra.
    """
    doc = store.load(doc_id)
    hit = next(((bid, h) for bid, lst in (doc.get("highlights") or {}).items()
                for h in lst if h.get("id") == hl_id), None)
    if hit is None:
        raise KeyError(hl_id)
    bid, h = hit
    blk = next((b for b in doc["blocks"] if b["id"] == bid), None)
    if blk is None:
        raise KeyError(bid)

    raw, usage = await llm.complete(
        [
            llm.system_message(cached_prefix(doc), prompts.HL_SYSTEM, model=doc["model"]),
            {"role": "user", "content": prompts.hl_user(
                h.get("text") or "", blk["text"],
                (doc.get("translations") or {}).get(bid, ""), blk.get("section") or "")},
        ],
        model=doc["model"], session_id=doc_id,
        max_tokens=1200, temperature=0.4, reasoning=LOW_REASONING,
    )
    note = strip_md(raw.strip())

    doc = store.load(doc_id)
    for lst in (doc.get("highlights") or {}).values():
        for x in lst:
            if x.get("id") == hl_id:
                x["note"] = note
    total = llm.Usage(**doc.get("usage", {}))
    total.add(usage)
    doc["usage"] = total.dict()
    store.save(doc)
    return note, usage.dict(), total.dict()


# ------------------------------------------------------------- hỏi đáp tự do


async def ask(doc_id: str, question: str, history: list[dict]) -> AsyncIterator[tuple[str, str]]:
    doc = store.load(doc_id)
    msgs = [llm.system_message(cached_prefix(doc), prompts.ASK_SYSTEM, model=doc["model"])]
    for turn in history[-8:]:
        if turn.get("role") in ("user", "assistant") and turn.get("content"):
            msgs.append({"role": turn["role"], "content": turn["content"]})
    msgs.append({"role": "user", "content": question})

    import json

    async for kind, payload in llm.stream_text(
        msgs, model=doc["model"], session_id=doc_id, max_tokens=4000, temperature=0.4
    ):
        if kind == "usage":
            # trả cả lượt này lẫn cộng dồn, cùng dạng với pass dịch và pass giải
            # thích — hỏi đáp cũng tiêu tiền nên phải hiện ra chứ không nuốt đi
            total = _bump_usage(doc_id, payload)
            yield "usage", json.dumps({"run": json.loads(payload), "total": total})
            continue
        yield kind, payload
