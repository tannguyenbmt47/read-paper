"""Bản tổng hợp cả kho — thứ để **đọc mà hiểu lĩnh vực**, khác hẳn hỏi đáp.

## Vì sao cần, khi đã có hỏi đáp

Hỏi đáp giả định người dùng **đã biết phải hỏi gì**. Lúc mới bước vào một lĩnh
vực thì chưa biết: chưa biết có mấy hướng tiếp cận, chưa biết bài nào cãi bài
nào, nên câu hỏi đặt ra cũng chỉ chạm phần mình đã đoán được. Đó là vòng luẩn
quẩn, và nó làm công cụ trông vô dụng đúng lúc đáng lẽ nó có ích nhất.

Bản tổng hợp đi ngược lại: đọc cả kho **trước**, dựng sẵn mạch lập luận chung,
rồi người dùng đọc và từ đó mới biết cần hỏi gì.

## Vì sao nó rẻ

Toàn bộ nội dung vào là `corpus_digest` — phiếu của cả kho, ~600 token mỗi bài.
Năm mươi bài ≈ 30k token, và khối đó **đã nằm sẵn trong cache** vì mọi lượt hỏi
đáp đều dùng chung nó. Nên một bản tổng hợp chỉ trả tiền phần đầu ra.

## Lineage tính từ ĐỒ THỊ, không hỏi model

`lineage()` dựng phần "ai kế thừa ai" từ các cạnh `mở rộng` / `tốt hơn` /
`so sánh với` đã bóc lúc nạp bài — cùng lối với `section_icons()` và
`agenda_from_sections()` bên luồng slide. Hỏi model thì nó dựng quan hệ theo cảm
giác về những cái tên nó từng thấy, và không có mã đoạn nào để đối chiếu. Cạnh
trong đồ thị thì mỗi cái mang sẵn mã đoạn đã đọc ra nó, nên **bấm vào kiểm được**.
"""

from __future__ import annotations

import json
import os
import time

from .. import depth, llm
from . import db as sdb
from . import prompts, verify

STRONG = os.getenv("SURVEY_MODEL") or llm.DEFAULT_MODEL
LOW_REASONING = {"effort": "low"}

# Quan hệ nói lên chuyện "bài này đứng trên vai bài kia". Các quan hệ còn lại
# (`đánh giá trên`, `đo bằng`, `dùng`) nói về công cụ, không phải về kế thừa.
_LINEAGE_RELS = ("mở rộng", "tốt hơn", "kém hơn", "so sánh với")


def lineage(survey_id: str) -> list[dict]:
    """Quan hệ kế thừa giữa các bài, dựng từ đồ thị thực thể. Không gọi model."""
    ov = sdb.graph_overview(survey_id, limit=400)
    names = {e["id"]: e["name"] for e in ov["entities"]}
    papers = {p["id"]: p for p in sdb.list_papers(survey_id)}
    out = []
    for g in ov["edges"]:
        if g["rel"] not in _LINEAGE_RELS:
            continue
        p = papers.get(g["paper_id"])
        out.append({
            "src": names.get(g["src"], "?"),
            "dst": names.get(g["dst"], "?"),
            "rel": g["rel"],
            "paper": g["paper_id"],
            "paper_title": (p or {}).get("title", ""),
            "year": (p or {}).get("year"),
            "cite": g.get("chunk_id") or "",
            "note": g.get("note") or "",
        })
    # Bài mới nói về bài cũ thì thứ tự thời gian là mạch đọc tự nhiên.
    out.sort(key=lambda e: (e["year"] or 0, e["src"]))
    return out


def _edges_text(rows: list[dict], limit: int = 40) -> str:
    return "\n".join(
        f"- {r['src']} —{r['rel']}→ {r['dst']}"
        f" (theo {r['paper_title'][:44]}, {r['year'] or 'n.d.'})"
        + (f" [{r['cite']}]" if r["cite"] else "")
        + (f" — {r['note']}" if r["note"] else "")
        for r in rows[:limit])


async def build(survey_id: str):
    """Async generator phát SSE: ("stage"|"done"|"error", payload)."""
    papers = sdb.list_papers(survey_id)
    carded = [p for p in papers if p.get("card")]
    if not carded:
        yield "error", {"msg": "Chưa bài nào có phiếu. Bơm nội dung cho vài bài trước "
                               "— bản tổng hợp dựng từ phiếu, không có phiếu thì không có gì để đọc."}
        return

    t0 = time.time()
    yield "stage", {"msg": f"đọc phiếu của {len(carded)} bài", "pct": 10}

    lin = lineage(survey_id)
    yield "stage", {"msg": f"dựng quan hệ kế thừa — {len(lin)} liên hệ", "pct": 25}

    strong, _fast = sdb.models_of(survey_id)
    digest = prompts.corpus_digest(papers)
    sysmsg = llm.system_message(digest, prompts.SYNTH_SYSTEM, model=strong)
    yield "stage", {"msg": "tổng hợp mạch lập luận", "pct": 40}

    raw, usage = await llm.complete(
        [sysmsg, {"role": "user",
                  "content": prompts.synth_user("", _edges_text(lin), len(carded))}],
        model=strong, session_id=survey_id, max_tokens=9000,
        temperature=0.35, reasoning=LOW_REASONING)

    try:
        data = llm.extract_json(raw)
    except Exception as e:                  # noqa: BLE001
        yield "error", {"msg": f"Model trả về không phải JSON hợp lệ: {e}"}
        return

    yield "stage", {"msg": "soát trích dẫn và số liệu", "pct": 85}
    data = _clean(survey_id, data, lin)
    data["warns"] = check(survey_id, data)
    data["created_at"] = time.time()
    data["cost"] = round(usage.cost, 5)
    data["papers"] = len(carded)
    data["model"] = strong
    sdb.save_synth(survey_id, data)

    yield "done", {"synth": data, "cost": round(usage.cost, 5),
                   "usage": usage.dict(), "secs": round(time.time() - t0, 1)}


def _clean(survey_id: str, d: dict, lin: list[dict]) -> dict:
    """Chuẩn hoá hình dạng, đổi nhãn ngắn về mã thật, gắn lineage đã tính sẵn.

    Model không được phép quyết phần `lineage`: nó tính từ đồ thị, mỗi liên hệ
    mang mã đoạn nên bấm vào kiểm được.
    """
    papers = sdb.list_papers(survey_id)
    # {P1 → mã thật}. Nhận cả mã thật phòng khi model chép được đúng mã dài.
    back = {v: k for k, v in prompts.paper_labels(papers).items()}
    real = {p["id"] for p in papers}

    def pid(x):
        x = str(x or "").strip()
        return back.get(x, x if x in real else x)

    def s(x):
        return str(x or "").strip()

    def lst(x):
        return x if isinstance(x, list) else []

    out = {
        "title": s(d.get("title")) or "Tổng hợp kho tài liệu",
        "scope": s(d.get("scope")),
        "problem": {
            "statement": s((d.get("problem") or {}).get("statement")),
            "why_hard": s((d.get("problem") or {}).get("why_hard")),
            "framings": [
                {"name": s(f.get("name")), "desc": s(f.get("desc")),
                 "papers": [pid(p) for p in lst(f.get("papers"))]}
                for f in lst((d.get("problem") or {}).get("framings")) if isinstance(f, dict)],
        },
        "approaches": [
            {"name": s(a.get("name")), "idea": s(a.get("idea")),
             "mechanism": s(a.get("mechanism")), "bet": s(a.get("bet")),
             "cost": s(a.get("cost")), "falsify": s(a.get("falsify")),
             "papers": [pid(p) for p in lst(a.get("papers"))],
             "evidence": [{"claim": s(e.get("claim")), "cite": s(e.get("cite"))}
                          for e in lst(a.get("evidence")) if isinstance(e, dict)]}
            for a in lst(d.get("approaches")) if isinstance(a, dict)],
        "novelty": [
            {"paper": pid(n.get("paper")), "new": s(n.get("new")),
             "assembled": s(n.get("assembled")), "cite": s(n.get("cite"))}
            for n in lst(d.get("novelty")) if isinstance(n, dict)],
        "tensions": [
            {"about": s(t.get("about")), "why": s(t.get("why")),
             "sides": [{"papers": [pid(p) for p in lst(x.get("papers"))],
                        "claim": s(x.get("claim")), "cite": s(x.get("cite"))}
                       for x in lst(t.get("sides")) if isinstance(x, dict)]}
            for t in lst(d.get("tensions")) if isinstance(t, dict)],
        "gaps": [{"gap": s(g.get("gap")), "why": s(g.get("why"))}
                 for g in lst(d.get("gaps")) if isinstance(g, dict)],
        "read_order": [{"paper": pid(r.get("paper")), "why": s(r.get("why"))}
                       for r in lst(d.get("read_order")) if isinstance(r, dict)],
        "lineage": lin,
    }
    # Tên bài để giao diện hiện thay cho mã — mã chỉ hợp để bấm, không hợp để đọc.
    out["paper_names"] = {p["id"]: (p.get("title") or "")[:120] for p in papers}
    return out


def _cites(d: dict) -> list[tuple[str, str]]:
    """Mọi (mã đoạn, câu chứa nó) trong bản tổng hợp, để đem đi soát."""
    out = []
    for a in d.get("approaches", []):
        for e in a.get("evidence", []):
            if e.get("cite"):
                out.append((e["cite"], e.get("claim", "")))
    for n in d.get("novelty", []):
        if n.get("cite"):
            out.append((n["cite"], f"{n.get('new','')} {n.get('assembled','')}"))
    for t in d.get("tensions", []):
        for s in t.get("sides", []):
            if s.get("cite"):
                out.append((s["cite"], s.get("claim", "")))
    return out


def check(survey_id: str, d: dict) -> list[dict]:
    """Chốt chặn — cùng triết lý `check_answer`: cảnh báo chứ không chặn.

    Bản tổng hợp nguy hiểm hơn một câu trả lời lẻ, vì người đọc dùng nó làm bản
    đồ để đi tiếp: một hướng tiếp cận bịa ra sẽ kéo họ đi sai suốt buổi đọc. Nên
    ba phép kiểm đầu nhắm đúng vào chuyện bịa.
    """
    warns: list[dict] = []
    papers = {p["id"]: p for p in sdb.list_papers(survey_id)}
    cites = _cites(d)
    rows = sdb.get_chunks([c for c, _ in cites])

    # 1) mã đoạn phải có thật
    for cid, claim in cites:
        if cid not in rows:
            warns.append({"kind": "cite_lạ", "msg": f"trích dẫn [{cid}] không có trong kho",
                          "text": claim[:140]})

    # 2) số trong câu phải có mặt nguyên văn trong đoạn đã trích
    for cid, claim in cites:
        row = rows.get(cid)
        if not row or not claim:
            continue
        body = verify._URLISH.sub(" ", claim)
        nums = verify._NUM.findall(body)
        if not nums:
            continue
        have = verify.source_numbers(f"{row['text']} {row.get('vi') or ''}")
        miss = [n for n in nums if verify._norm(n) not in have]
        if miss:
            warns.append({"kind": "số_bịa",
                          "msg": f"số {', '.join(miss[:3])} không có trong đoạn [{cid}]",
                          "text": claim[:140]})

    # 3) mã bài nhắc tới phải có thật
    for key, items in (("approaches", d.get("approaches", [])),
                       ("novelty", d.get("novelty", [])),
                       ("read_order", d.get("read_order", []))):
        for it in items:
            ids = it.get("papers") or ([it["paper"]] if it.get("paper") else [])
            for pid in ids:
                if pid not in papers:
                    warns.append({"kind": "bài_lạ",
                                  "msg": f"nhắc tới mã bài [{pid}] không có trong kho",
                                  "text": (it.get("name") or it.get("new") or "")[:140]})

    # 4) mỗi bài có phiếu nên xuất hiện ở ít nhất một hướng tiếp cận — bài bị bỏ
    #    quên là bản tổng hợp chưa đọc hết kho, mà nhìn thì không thấy.
    placed = {p for a in d.get("approaches", []) for p in a.get("papers", [])}
    forgotten = [p["id"] for p in papers.values() if p.get("card") and p["id"] not in placed]
    if forgotten:
        names = ", ".join((papers[p].get("title") or p)[:40] for p in forgotten[:4])
        warns.append({"kind": "bỏ_sót_bài",
                      "msg": f"{len(forgotten)} bài không nằm trong hướng tiếp cận nào: {names}",
                      "text": ""})

    # 5) độ sâu: câu nghe như giải thích mà không giải thích gì. Đây là kiểu
    #    hỏng khó thấy nhất — câu đúng, có trích dẫn, mà đọc xong không biết
    #    thêm điều gì. Xem `server/depth.py`.
    for a in d.get("approaches", []):
        warns += depth.check_text(a.get("mechanism", ""),
                                  label=f"cơ chế của “{a.get('name','')[:30]}”",
                                  term=a.get("name", ""))
        warns += depth.check_text(a.get("bet", ""),
                                  label=f"đặt cược của “{a.get('name','')[:30]}”")
        if not a.get("falsify"):
            warns.append({"kind": "thiếu_phản_chứng",
                          "msg": f"“{a.get('name','')[:40]}” không nêu điều gì sẽ "
                                 "chứng minh nó sai — giả định chưa đủ sắc",
                          "text": a.get("bet", "")[:140]})
    pb = d.get("problem") or {}
    warns += depth.check_text(pb.get("why_hard", ""), label="khó ở đâu")
    for g in d.get("gaps", []):
        warns += depth.check_text(g.get("why", ""), label="vì sao còn trống")

    # 6) mỗi hướng đúng một bài, và có từ ba hướng trở lên → gom cụm hỏng, bản
    #    tổng hợp thành mục lục. Đây chính là kiểu hỏng prompt đã cảnh báo.
    apps = d.get("approaches", [])
    if len(apps) >= 3 and all(len(a.get("papers", [])) <= 1 for a in apps):
        warns.append({"kind": "chưa_gom_được",
                      "msg": "mỗi hướng tiếp cận chỉ có một bài — nhiều khả năng "
                             "chưa đọc ra điểm chung, bản tổng hợp đang là mục lục",
                      "text": ""})
    return warns


def as_markdown(d: dict) -> str:
    """Xuất ra Markdown để dán vào ghi chú hay gửi cho người khác."""
    if not d:
        return ""
    nm = d.get("paper_names") or {}
    def pn(pid):
        return nm.get(pid) or pid

    out = [f"# {d.get('title','')}", "", d.get("scope", ""), ""]
    p = d.get("problem") or {}
    out += ["## Bài toán", "", p.get("statement", ""), "",
            f"**Khó ở đâu.** {p.get('why_hard','')}", ""]
    for f in p.get("framings", []):
        out.append(f"- **{f['name']}** — {f['desc']} ({', '.join(pn(x) for x in f['papers'])})")
    out += ["", "## Các hướng tiếp cận", ""]
    for a in d.get("approaches", []):
        out += [f"### {a['name']}", "",
                f"**Ý tưởng.** {a['idea']}", "",
                f"**Cơ chế.** {a['mechanism']}", "",
                f"**Đặt cược vào.** {a['bet']}", ""]
        if a.get("cost"):
            out += [f"**Cái giá.** {a['cost']}", ""]
        if a.get("papers"):
            out += ["Bài: " + ", ".join(pn(x) for x in a["papers"]), ""]
        for e in a.get("evidence", []):
            out.append(f"- {e['claim']} `[{e['cite']}]`")
        out.append("")
    if d.get("novelty"):
        out += ["## Tính mới", ""]
        for n in d["novelty"]:
            out.append(f"- **{pn(n['paper'])}** — {n['new']}"
                       + (f" *Phần ghép sẵn:* {n['assembled']}" if n.get("assembled") else "")
                       + (f" `[{n['cite']}]`" if n.get("cite") else ""))
        out.append("")
    if d.get("tensions"):
        out += ["## Chỗ các bài nói ngược nhau", ""]
        for t in d["tensions"]:
            out += [f"**{t['about']}**", ""]
            for s in t.get("sides", []):
                out.append(f"- {', '.join(pn(x) for x in s['papers'])}: {s['claim']}"
                           + (f" `[{s['cite']}]`" if s.get("cite") else ""))
            out += ["", f"*Vì sao khác nhau:* {t.get('why','')}", ""]
    if d.get("lineage"):
        out += ["## Kế thừa", ""]
        for g in d["lineage"]:
            out.append(f"- {g['src']} —{g['rel']}→ {g['dst']} "
                       f"({g['paper_title'][:44]})" + (f" `[{g['cite']}]`" if g.get("cite") else ""))
        out.append("")
    if d.get("gaps"):
        out += ["## Khoảng trống còn lại", ""]
        for g in d["gaps"]:
            out.append(f"- **{g['gap']}** — {g['why']}")
        out.append("")
    if d.get("read_order"):
        out += ["## Nên đọc theo thứ tự", ""]
        for i, r in enumerate(d["read_order"], 1):
            out.append(f"{i}. **{pn(r['paper'])}** — {r['why']}")
    return "\n".join(out)
