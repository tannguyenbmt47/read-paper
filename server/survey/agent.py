"""Vòng lặp đào sâu: lập kế hoạch → tìm → đọc → chấm thiếu → tìm tiếp → tổng hợp.

## Vì sao lặp, thay vì tìm một lần rồi trả lời

Đây là quyết định có bằng chứng, không phải khẩu vị. Trên bộ đo hỏi đáp bắc cầu
trong khoa học, vòng lặp tìm-đọc-tìm-tiếp đạt **80,9%**, còn đưa sẵn **đúng toàn
bộ bằng chứng chuẩn** cho model chỉ đạt **69,1%** (không ngữ cảnh: 37,2%). Lặp
thắng cả bằng chứng hoàn hảo, vì hai cơ chế:

- **Tự sửa hướng.** Vòng đầu model hay bám vào một giả thuyết yếu; sang vòng hai
  nó bỏ giả thuyết đó và khoá vào chuỗi thực thể đúng.
- **Giảm tải nhận thức.** Đọc 18 đoạn mỗi vòng rồi rút gọn thành phát hiện thì
  dễ hơn hẳn nuốt cả trăm đoạn một lúc.

## Bốn kiểu hỏng đã đo được, và chốt chặn tương ứng

| Kiểu hỏng | Thiệt hại | Chốt trong file này |
|---|---|---|
| **Thiếu phủ** — sót một chặng | 77,9% → 49,2% | bảng kiểm tường minh; mục trống được **ghi thẳng** vào câu trả lời |
| **Hỏng tổng hợp** — có bằng chứng mà ráp sai | **87,3% số câu sai** | tổng hợp bằng **model mạnh**, bằng chứng **nhóm theo câu hỏi con** |
| **Bám nhầm mồi** — khoá vào thực thể gần giống | −53,9 điểm | mỗi vòng bắt buộc mang **từ khoá mới**; bộ chấm-thiếu nhìn bảng kiểm, không nhìn bản nháp |
| **Dừng sớm quá tự tin** | → 61,5% | cấm dừng ở vòng 1 khi còn mục trống |

Con số vận hành cũng lấy từ đó: **tối đa 5 vòng**, mỗi vòng **10 đoạn mới + 2 đoạn
tốt nhất mang từ vòng trước**.

## Ngân sách

Kiểm **trước** mỗi lần gọi model, không phải sau — kiểm sau thì đã tiêu rồi. Hết
tiền thì dừng và **nói rõ đã dừng vì tiền**, chứ không im lặng trả lời cụt: câu
trả lời ngắn vì hết ngân sách và câu trả lời ngắn vì kho không có gì là hai
chuyện khác hẳn nhau, và người đọc phải phân biệt được.
"""

from __future__ import annotations

import json
import os
import time

from .. import llm
from . import db as sdb
from . import graph, prompts, search, verify

STRONG = os.getenv("SURVEY_MODEL") or llm.DEFAULT_MODEL
FAST = os.getenv("SURVEY_FAST_MODEL") or llm.FAST_MODEL

LOW_REASONING = {"effort": "low"}
NO_REASONING = {"enabled": False}

MAX_STEPS = 5        # ngân sách vòng, theo bài đo về multi-hop QA khoa học
PER_STEP = 10        # đoạn mới mỗi vòng
CARRY = 2            # đoạn tốt nhất mang sang vòng sau
DEFAULT_BUDGET = float(os.getenv("SURVEY_BUDGET") or 0.5)

# Chi phí ước lượng cho một lần gọi từng loại, dùng để kiểm ngân sách TRƯỚC khi
# gọi. Cố ý ước cao: dừng sớm hơn một chút thì người dùng bấm hỏi tiếp được, còn
# vượt trần thì tiền đã mất.
_EST = {"plan": 0.004, "rerank": 0.004, "read": 0.006, "answer": 0.05, "entail": 0.004}


def _sse(event: str, data) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


class Budget:
    """Trần chi phí cho một lượt hỏi, kiểm trước mỗi lần gọi."""

    def __init__(self, cap: float):
        self.cap = max(0.0, float(cap or 0))
        self.spent = 0.0
        self.stopped = ""

    def can(self, kind: str) -> bool:
        if self.cap <= 0:
            return True
        return self.spent + _EST.get(kind, 0.01) <= self.cap

    def add(self, usage: llm.Usage) -> None:
        self.spent += usage.cost or 0.0

    @property
    def left(self) -> float:
        return max(0.0, self.cap - self.spent) if self.cap > 0 else -1.0


def digest_of(survey_id: str) -> str:
    """Phiếu toàn kho — cached prefix của cơ chế survey.

    Phải **cố định byte giữa mọi câu hỏi** của cùng một dự án. Nhét câu hỏi, thời
    gian, hay số vòng vào đây là hỏng cache và chi phí nhân lên nhiều lần — đúng
    cái bẫy đã ghi trong CLAUDE.md cho `cached_prefix` của luồng dịch.
    """
    return prompts.corpus_digest(sdb.list_papers(survey_id))


async def deep_dive(survey_id: str, question: str, *, budget: float | None = None,
                    entail: bool = True, use_cache: bool = True):
    """Async generator phát SSE. Xem docstring đầu file cho ý nghĩa từng chốt."""
    t0 = time.time()
    # Model lấy từ kho, tra tại đây chứ không đọc hằng số module: hằng số chốt
    # một lần lúc server khởi động, nên đổi model trên giao diện sẽ không có tác
    # dụng cho tới lần khởi động sau.
    strong, fast = sdb.models_of(survey_id)
    cap = DEFAULT_BUDGET if budget is None else float(budget)
    bud = Budget(cap)
    total = llm.Usage()

    # --- cache: hỏi lại đúng câu cũ khi kho chưa đổi thì miễn phí -----------
    key = sdb.qcache_key(survey_id, question)
    if use_cache and (hit := sdb.qcache_get(key)) is not None:
        yield _sse("cached", {"run_id": hit["id"], "cost": 0.0})
        yield _sse("plan", {"sub_questions": hit["steps"][0].get("sub_questions", [])
                            if hit["steps"] else []})
        yield _sse("answer", {"text": hit["answer"], "done": True})
        yield _sse("check", {"warns": hit["warns"]})
        yield _sse("usage", {"run": {"cost": 0.0}, "cached": True})
        yield _sse("done", {"run_id": hit["id"], "cost": 0.0,
                            "secs": round(time.time() - t0, 1)})
        return

    digest = digest_of(survey_id)
    if not sdb.count_chunks(survey_id):
        yield _sse("error", {"msg": "Kho chưa có bài nào được đánh chỉ mục."})
        return

    steps: list[dict] = []
    findings: list[dict] = []          # mọi phát hiện, tích luỹ qua các vòng
    covered: set[str] = set()
    seen: set[str] = set()             # đoạn đã đọc, không đọc lại
    carried: list[dict] = []           # đoạn tốt nhất mang sang vòng sau
    used_terms: list[str] = []
    all_edges: list[dict] = []
    plan_obj: dict = {}

    for step in range(1, MAX_STEPS + 1):
        # ---------------------------------------------------- lập kế hoạch
        if not bud.can("plan"):
            bud.stopped = "hết ngân sách trước khi lập kế hoạch vòng " + str(step)
            break
        p, u = await search.plan(survey_id, question, digest,
                                 prev_terms=used_terms if step > 1 else None, fast=fast)
        bud.add(u)
        total.add(u)
        if step == 1:
            plan_obj = p
            yield _sse("plan", {"intent": p["intent"],
                                "sub_questions": p["sub_questions"],
                                "queries": [q["q"] for q in p["queries"]],
                                "pseudo_doc": p["pseudo_doc"]})
        else:
            # Bảng kiểm KHÔNG đổi giữa các vòng: đổi nó là mục cũ biến mất và
            # "đã phủ hết" trở thành chuyện tự phong.
            p["sub_questions"] = plan_obj["sub_questions"]
        used_terms.extend(search.terms_of(p))

        # ---------------------------------------------------------- tìm
        got = await search.retrieve(survey_id, p, exclude=seen)
        yield _sse("search", {"step": step, "queries": [q["q"] for q in p["queries"]],
                              "found": len(got["hits"]), "lists": got["lists"],
                              "edges": len(got["edges"])})
        all_edges.extend(got["edges"])
        if not got["hits"]:
            if step == 1:
                yield _sse("gap", {"step": step, "missing": [
                    {"id": s["id"], "why": "không tìm được đoạn nào"}
                    for s in p["sub_questions"]]})
            break

        # -------------------------------------------------------- rerank
        if not bud.can("rerank"):
            bud.stopped = f"hết ngân sách ở bước lọc vòng {step}"
            break
        hits, u = await search.rerank(question, p["sub_questions"], got["hits"],
                                      keep=PER_STEP, session=survey_id, fast=fast)
        bud.add(u)
        total.add(u)
        # Mang theo đoạn tốt nhất của vòng trước: giữ mạch giữa các chặng, và
        # tránh việc vòng sau quên mất chặng đầu đã tìm thấy gì.
        batch = carried + [h for h in hits if h["id"] not in seen]
        seen.update(h["id"] for h in batch)
        yield _sse("hits", {"step": step, "hits": [_slim(h) for h in batch]})

        # ---------------------------------------------------------- đọc
        if not bud.can("read"):
            bud.stopped = f"hết ngân sách ở bước đọc vòng {step}"
            break
        found, cov, missing, u = await _read(survey_id, question, p["sub_questions"],
                                             batch, used_terms, sorted(covered), fast)
        bud.add(u)
        total.add(u)
        findings.extend(found)
        covered.update(cov)
        yield _sse("read", {"step": step, "findings": found,
                            "covered": sorted(covered)})

        steps.append({"step": step, "queries": [q["q"] for q in p["queries"]],
                      "sub_questions": p["sub_questions"], "found": len(batch),
                      "findings": found, "covered": sorted(covered),
                      "missing": missing, "cost": round(bud.spent, 5)})

        carried = sorted(hits, key=lambda h: -h.get("grade", 0))[:CARRY]

        # ------------------------------------------------- dừng hay đi tiếp
        gaps = [s for s in plan_obj["sub_questions"] if s["id"] not in covered]
        yield _sse("gap", {"step": step, "missing": missing,
                           "open": [g["id"] for g in gaps],
                           "spent": round(bud.spent, 5), "left": round(bud.left, 5)})

        if not gaps:
            # Cấm dừng ở vòng 1: mô hình có xu hướng tự tin quá sớm, và dừng sớm
            # khi chưa đủ phủ kéo độ chính xác xuống 61,5%. Một vòng nữa rẻ hơn
            # nhiều so với một câu trả lời trống ruột.
            if step > 1:
                break
        if not missing:
            break
        if not bud.can("plan"):
            bud.stopped = "hết ngân sách sau vòng " + str(step)
            break

    if not findings and not bud.stopped:
        yield _sse("error", {"msg": "Không tìm được bằng chứng nào trong kho cho câu hỏi này."})

    # ------------------------------------------------------------ tổng hợp
    subs = plan_obj.get("sub_questions") or [{"id": "q1", "ask": question, "need": ""}]
    groups: dict[str, list[dict]] = {}
    for f in findings:
        groups.setdefault(f.get("for") or "q1", []).append(f)
    evidence = list(sdb.get_chunks(sorted(seen)).values())

    if not bud.can("answer"):
        bud.stopped = bud.stopped or "hết ngân sách trước khi tổng hợp"
    yield _sse("synth", {"evidence": len(evidence), "findings": len(findings),
                         "stopped": bud.stopped, "model": strong})

    answer = ""
    async for kind, payload in _synthesise(survey_id, question, subs, sorted(covered),
                                           groups, evidence, all_edges, bud.stopped, strong):
        if kind == "delta":
            answer += payload
            yield _sse("answer", {"text": payload})
        elif kind == "usage":
            u = llm.Usage(**json.loads(payload))
            bud.add(u)
            total.add(u)
    yield _sse("answer", {"text": "", "done": True})

    # ---------------------------------------------------------- kiểm chứng
    warns = verify.check_answer(survey_id, answer, sorted(seen), subs, sorted(covered))
    if entail and bud.can("entail") and answer:
        extra, u = await verify.check_entailment(answer, sorted(seen),
                                                 session=survey_id, fast=fast)
        bud.add(u)
        total.add(u)
        warns.extend(extra)
    yield _sse("check", {"warns": warns})

    rid = sdb.save_run(survey_id, question, answer, steps, sorted(seen), warns,
                       total.dict(), round(total.cost, 5))
    if use_cache and answer and not bud.stopped:
        sdb.qcache_put(key, rid)

    yield _sse("usage", {"run": total.dict()})
    yield _sse("done", {"run_id": rid, "cost": round(total.cost, 5),
                        "steps": len(steps), "warns": len(warns),
                        "stopped": bud.stopped, "secs": round(time.time() - t0, 1)})


def _slim(h: dict) -> dict:
    return {"id": h["id"], "paper_id": h["paper_id"], "title": h.get("paper_title", ""),
            "year": h.get("year"), "section": h.get("section", ""),
            "page": h.get("page"), "level": h.get("level", 0),
            "grade": h.get("grade"), "cross": h.get("cross"),
            "text": h["text"][:400]}


async def _read(survey_id: str, question: str, subs: list[dict], hits: list[dict],
                used: list[str], covered: list[str], fast: str = ""):
    """Bóc phát hiện + chấm bảng kiểm. Model rẻ: đây là việc rút gọn, không phải suy luận."""
    raw, usage = await llm.complete(
        [{"role": "system", "content": prompts.READ_SYSTEM},
         {"role": "user", "content": prompts.read_user(question, subs, hits, used, covered)}],
        model=fast or FAST, session_id=survey_id, max_tokens=3000,
        temperature=0.2, reasoning=NO_REASONING)
    try:
        data = llm.extract_json(raw)
    except Exception:                       # noqa: BLE001
        return [], [], [], usage

    ids = {h["id"] for h in hits}
    found = []
    for f in data.get("findings") or []:
        chunk = str(f.get("chunk") or "")
        # Phát hiện gắn vào một mã đoạn không nằm trong lô vừa đọc là bịa nguồn —
        # bỏ tại đây, đừng để nó chạy tiếp vào câu trả lời rồi mới bắt.
        if chunk in ids and str(f.get("finding") or "").strip():
            found.append({"for": str(f.get("for") or "q1"),
                          "finding": str(f["finding"]).strip(),
                          "quote": str(f.get("quote") or "").strip()[:400],
                          "chunk": chunk})
    covered_now = [str(c) for c in (data.get("covered") or [])]
    missing = [{"id": str(m.get("id") or ""), "why": str(m.get("why") or ""),
                "next_q": str(m.get("next_q") or "")}
               for m in (data.get("missing") or []) if isinstance(m, dict)]
    return found, covered_now, missing, usage


async def _synthesise(survey_id: str, question: str, subs: list[dict],
                      covered: list[str], groups: dict, evidence: list[dict],
                      edges: list[dict], stopped: str, strong: str = ""):
    """Tổng hợp bằng **model mạnh** — đây là chỗ 87,3% số câu sai được sinh ra.

    Bằng chứng vào prompt đã **nhóm sẵn theo câu hỏi con** (`prompts.answer_user`),
    chứ không đổ phẳng: đổ phẳng là bắt model vừa phân loại lại vừa viết, đúng
    chỗ nó hỏng.
    """
    user = prompts.answer_user(question, subs, covered, groups, evidence, stopped)
    if edges:
        user += ("\n\n=== QUAN HỆ ĐỌC ĐƯỢC TỪ ĐỒ THỊ (mỗi dòng có mã đoạn để trích) ===\n"
                 + graph.describe(edges))
    async for kind, payload in llm.stream_text(
            [{"role": "system", "content": prompts.ANSWER_SYSTEM},
             {"role": "user", "content": user}],
            model=strong or STRONG, session_id=survey_id, max_tokens=6000,
            temperature=0.3, reasoning=LOW_REASONING):
        yield kind, payload
