"""Prompt cho kho survey. Đây là nơi quyết định chất lượng, phần còn lại là ống dẫn.

Sáu pass, xếp theo thứ tự tiền đi ra:

  CTX_SYSTEM     ngữ cảnh hoá từng đoạn trước khi đánh chỉ mục  (model rẻ, 1 lần/bài)
  CARD_SYSTEM    bóc phiếu có cấu trúc                          (model rẻ, 1 lần/bài)
  PLAN_SYSTEM    tách câu hỏi con + query2doc                   (model rẻ, 1 lần/câu hỏi)
  RERANK_SYSTEM  chấm lại ứng viên sau BM25                     (model rẻ, 1 lần/vòng)
  READ_SYSTEM    bóc phát hiện từ đoạn, và chấm còn thiếu gì    (model rẻ, 1 lần/vòng)
  ANSWER_SYSTEM  tổng hợp có trích dẫn                          (model MẠNH, 1 lần)

Chỉ pass cuối dùng model mạnh, vì hỏng-khâu-tổng-hợp chiếm 87,3% số câu sai trong
bài đo về multi-hop QA khoa học — còn tìm và đọc thì model rẻ làm được.
"""

from __future__ import annotations

import json

from ..depth import DEPTH_RULES

LANGUAGE_RULE = """\
Bạn viết cho người đọc Việt Nam. Mọi câu trả lời, giải thích, nhãn đều bằng tiếng
Việt tự nhiên. Thuật ngữ chuyên ngành, tên mô hình, tên tập dữ liệu, tên độ đo
thì GIỮ NGUYÊN TIẾNG ANH — cái phải chuẩn tiếng Việt là khung câu quanh nó.

Tuyệt đối không chèn chữ Hán, chữ Nhật, chữ Hàn.
"""

JSON_RULE = "Chỉ trả lời bằng một object JSON hợp lệ, không kèm lời dẫn, không bọc trong ```.\n"


# ------------------------------------------- pass A: ngữ cảnh hoá từng đoạn

CTX_SYSTEM = """\
You situate passages inside the paper they came from, so that a keyword search
engine can find them later.

The paper's full text is above. For each passage id given below, write ONE short
English sentence (12–30 words) that says what this passage is doing in the paper
and names the entities it is about.

That sentence is stored next to the passage and indexed alongside it. So it must
contain the words a reader would search for but which the passage itself omits —
the method name, the dataset, the task, the section's purpose.

Rules:
- Name the paper's method/system explicitly instead of "the proposed method".
- Name the dataset, benchmark or baseline if the passage reports a result.
- Do NOT repeat numbers from the passage; the passage itself is indexed too.
- Do NOT write "This passage/section..." — write the content directly.
- English only, even though the passage may be in another language.

Good:  Main results of CIRAG against DPR and Contriever baselines on HotpotQA and
       2WikiMQA multi-hop question answering.
Bad:   This section presents the experimental results of our proposed approach.

""" + JSON_RULE + """
{"ctx": {"<passage id>": "<one sentence>", ...}}

Return an entry for EVERY id given, in the same order.
"""


def ctx_user(items: list[dict]) -> str:
    """items: [{'ord': int, 'section': str, 'text': str}]"""
    lines = []
    for it in items:
        head = f"[{it['ord']}] ({it.get('section') or 'không rõ mục'})"
        lines.append(f"{head} {it['text'][:900]}")
    return "PASSAGES TO SITUATE:\n\n" + "\n\n".join(lines)


# --------------------------------------------------- pass B: bóc phiếu bài

CARD_SYSTEM = LANGUAGE_RULE + """\
Bạn rút một bài báo khoa học thành **phiếu tra cứu**: nhỏ, dày đặc thông tin, đủ
để so sánh bài này với ba mươi bài khác mà không phải mở lại toàn văn.

Toàn văn bài nằm ở trên. Mỗi đoạn có mã dạng `<<<p7c12>>>` — mã đó dùng để trích
dẫn, phải chép lại đúng, không được bịa mã không có.

""" + JSON_RULE + """
{
  "title_vi": "Tiêu đề dịch sang tiếng Việt.",
  "tldr_vi": "MỘT câu: bài này chứng minh hoặc đề xuất điều gì. Viết cho người biết ngành nhưng chưa đọc bài.",
  "task": "Bài toán/nhiệm vụ cụ thể, ví dụ 'multi-hop QA trên văn bản mở'. Ngắn.",
  "domain": "Lĩnh vực, ví dụ 'NLP / retrieval' hoặc 'robot learning'.",
  "problem": "Vấn đề tác giả nhắm tới, 1-2 câu.",
  "gap": "Cách làm trước đó thiếu đúng chỗ nào. 1-2 câu. Đây là chỗ hay bị viết chung chung — phải chỉ ra điểm cụ thể.",
  "idea": "Ý tưởng cốt lõi nói bằng ngôn ngữ thường, 1-2 câu.",
  "method": "Cơ chế cụ thể: dữ liệu vào gì, qua những bước nào, ra gì. 2-4 câu. Đủ để hiểu cách chạy, không chỉ tên gọi.",
  "datasets": ["Tên tập dữ liệu đúng như bài viết"],
  "metrics": ["Tên độ đo đúng như bài viết"],
  "baselines": ["Tên phương pháp được đem ra so sánh"],
  "results": [
    {"claim": "Một kết quả, viết thành câu có chủ ngữ.",
     "number": "Con số CHÍNH XÁC như trong bài, giữ nguyên dấu thập phân. Rỗng nếu kết quả không phải số.",
     "chunk": "mã đoạn chứa con số này"}
  ],
  "limitations": "Giới hạn — cả phần tác giả tự nhận lẫn phần bạn thấy mà họ không nói. 1-3 câu.",
  "novelty": "Đóng góp mới thật sự nằm ở đâu, phân biệt với phần chỉ là ghép các thứ có sẵn. 1-2 câu.",
  "contribution_type": "một trong: phương pháp mới | tập dữ liệu | khảo sát | phân tích thực nghiệm | lý thuyết | hệ thống",
  "keywords_en": ["5-10 từ khoá tiếng Anh đúng như giới trong ngành gọi, dùng để tìm kiếm"],
  "code_url": "URL mã nguồn nếu bài có nêu, ngược lại để rỗng."
}

Ba luật bắt buộc:

1. **Không bịa.** Trường nào bài không nói thì để chuỗi rỗng hoặc mảng rỗng.
   Viết "chưa xác định", "không rõ", "N/A" vào cho đủ chỗ là sai — ô trống là
   thông tin thật, câu độn thì không.
2. **Mọi con số trong `results` phải xuất hiện nguyên văn trong đoạn đã khai ở
   `chunk`.** Không làm tròn, không quy đổi, không ước lượng.
3. `keywords_en` là từ giới trong ngành thật sự dùng khi tìm bài — không phải từ
   chung chung ("machine learning", "deep learning") mà là từ phân biệt được bài
   này với bài khác ("multi-hop retrieval", "construction-integration model").
"""


def card_user(title: str, labeled_text: str) -> str:
    return (f"TIÊU ĐỀ (bóc từ PDF, có thể sai): {title or '(không rõ)'}\n\n"
            f"=== TOÀN VĂN BÀI, MỖI ĐOẠN CÓ MÃ ===\n{labeled_text}")


# ----------------------------------- pass C: lập kế hoạch tìm + query2doc

PLAN_SYSTEM = LANGUAGE_RULE + """\
Bạn lập kế hoạch tìm kiếm cho một câu hỏi đặt trên kho bài báo khoa học.

Danh sách phiếu tóm tắt của cả kho nằm ở trên. **Dùng nó để tách câu hỏi**: tách
dựa trên những gì kho thật sự có, đừng tách chay theo cảm giác — tách chay thì
câu hỏi con trôi sang thứ không có trong kho, và mọi vòng tìm sau đó đi lạc theo.

""" + JSON_RULE + """
{
  "intent": "một trong: tra cứu | so sánh | tổng hợp | tìm bằng chứng | quy trình",
  "sub_questions": [
    {"id": "q1",
     "ask": "Một câu hỏi con, tự nó trả lời được, viết bằng tiếng Việt.",
     "need": "Cần tìm được thứ gì thì mới coi là đã trả lời được câu này."}
  ],
  "must_terms_en": ["thuật ngữ tiếng Anh BẮT BUỘC phải có trong đoạn tìm được"],
  "queries": [
    {"q": "chuỗi từ khoá để tìm, tiếng Anh", "for": "q1"},
    {"q": "chuỗi từ khoá để tìm, tiếng Việt", "for": "q1"}
  ],
  "pseudo_doc": "Xem luật ở dưới."
}

**`sub_questions` là bảng kiểm.** 2–5 mục, không hơn. Mỗi mục sẽ được chấm riêng
là đã có bằng chứng hay chưa, và mục nào không tìm ra sẽ được ghi thẳng vào câu
trả lời cuối là chưa tìm thấy. Nên tách sao cho từng mục kiểm được, đừng tách
thành những mục mơ hồ không bao giờ đóng lại được.

**`queries`**: 3–6 truy vấn. Phải có ít nhất một truy vấn tiếng Anh và một tiếng
Việt cho mỗi câu hỏi con quan trọng. Đây là **từ khoá cho bộ tìm BM25**, không
phải câu hỏi — viết cụm danh từ và thuật ngữ, bỏ hư từ.

**`pseudo_doc` là chỗ quan trọng nhất của pass này.** Viết một đoạn 60–100 từ
**tiếng Anh**, giả vờ như bạn đang trích một đoạn trong bài báo trả lời đúng câu
hỏi này — đúng giọng văn học thuật, đúng thuật ngữ mà một bài báo thật sẽ dùng.
Nó không cần đúng sự thật; việc của nó là mang đúng những từ mà đoạn cần tìm
đang chứa. Đây cũng là cách câu hỏi tiếng Việt tìm ra được bài tiếng Anh: bạn
viết bằng chính từ vựng của bài.
"""


def plan_user(question: str, digest: str, prev: list[str] | None = None) -> str:
    out = [f"CÂU HỎI: {question}"]
    if prev:
        out.append("Các từ khoá đã dùng ở vòng trước (LẦN NÀY PHẢI CÓ ÍT NHẤT MỘT "
                   "TỪ KHOÁ MỚI, không lặp lại nguyên si):\n- " + "\n- ".join(prev[:40]))
    if digest:
        out.append(digest)
    return "\n\n".join(out)


# ----------------------------------------------- pass D: chấm lại ứng viên

RERANK_SYSTEM = LANGUAGE_RULE + """\
Bạn chấm mức liên quan của từng đoạn với câu hỏi. Đây là bước lọc: BM25 đã lấy về
nhiều đoạn có từ khoá trùng nhưng nội dung lệch, việc của bạn là loại chúng.

Thang điểm:
  3 — trả lời thẳng câu hỏi, hoặc chứa đúng con số / khẳng định cần tìm
  2 — nói về đúng chủ đề và bổ trợ cho câu trả lời, nhưng không phải phần cốt lõi
  1 — cùng lĩnh vực, trùng từ khoá, nhưng không giúp trả lời
  0 — không liên quan

""" + JSON_RULE + """
{"grades": [{"id": "<mã đoạn>", "g": 3, "why": "≤10 từ"}]}

Chấm cho MỌI đoạn được đưa. Rộng tay ở mức 1 là hỏng cả bước lọc: đoạn chỉ trùng
từ khoá phải bị 0 chứ không phải 1.
"""


def rerank_user(question: str, subs: list[dict], cands: list[dict]) -> str:
    sub = "\n".join(f"- {s['id']}: {s['ask']}" for s in subs)
    body = []
    for c in cands:
        head = f"[{c['id']}] {c.get('paper_title','')[:70]} — {c.get('section') or ''}"
        body.append(f"{head}\n{(c.get('ctx') or '')}\n{c['text'][:700]}")
    return (f"CÂU HỎI: {question}\n\nCÁC CÂU HỎI CON:\n{sub}\n\n"
            f"=== ĐOẠN ỨNG VIÊN ===\n" + "\n\n".join(body))


# ------------------------------ pass E: đọc đoạn, bóc phát hiện, chấm thiếu

READ_SYSTEM = LANGUAGE_RULE + """\
Bạn đọc các đoạn vừa tìm được và bóc ra những phát hiện có ích, rồi chấm xem bảng
kiểm còn thiếu mục nào.

""" + JSON_RULE + """
{
  "findings": [
    {"for": "q1",
     "finding": "Một phát hiện, viết thành câu hoàn chỉnh bằng tiếng Việt.",
     "quote": "Trích NGUYÊN VĂN từ đoạn, ≤35 từ, không sửa một chữ.",
     "chunk": "mã đoạn chứa câu trích trên"}
  ],
  "covered": ["mã câu hỏi con đã có đủ bằng chứng để trả lời"],
  "missing": [
    {"id": "q2",
     "why": "Còn thiếu chính xác cái gì.",
     "next_q": "Từ khoá tìm cho vòng sau, phải KHÁC những từ đã dùng."}
  ]
}

Bốn luật:

1. **`quote` phải chép nguyên văn** từ đoạn có mã ở `chunk`. Đây là chỗ về sau bị
   máy đối chiếu lại; chép sai một chữ là bị bắt.
2. **Không suy diễn ra ngoài đoạn.** Đoạn nói gì thì ghi nấy. Điều bạn biết sẵn
   mà đoạn không nói thì không phải phát hiện.
3. **Xếp một câu hỏi con vào `covered` chỉ khi bằng chứng thật sự trả lời được
   nó**, không phải khi có đoạn nói loanh quanh chủ đề đó. Xếp rộng tay ở đây là
   dừng tìm sớm, và câu trả lời cuối sẽ trống ruột đúng ở chỗ quan trọng.
4. **`next_q` phải mang từ khoá mới.** Lặp lại từ khoá vòng trước là vòng sau tìm
   ra đúng những đoạn cũ, tốn tiền mà không thêm gì.

Không có gì đáng bóc thì trả `findings` rỗng. Đó là thông tin thật và có ích.
"""


def read_user(question: str, subs: list[dict], hits: list[dict],
              used_terms: list[str], covered: list[str]) -> str:
    sub = "\n".join(
        f"- {s['id']}: {s['ask']}  (cần: {s.get('need','')})"
        + ("   ← ĐÃ CÓ BẰNG CHỨNG" if s["id"] in covered else "")
        for s in subs)
    body = []
    for h in hits:
        head = (f"[{h['id']}] {h.get('paper_title','')[:70]}"
                f" ({h.get('year') or 'n/a'}) — {h.get('section') or ''} tr.{h.get('page') or '?'}")
        body.append(f"{head}\n{h['text'][:1600]}")
    parts = [f"CÂU HỎI GỐC: {question}", f"BẢNG KIỂM:\n{sub}"]
    if used_terms:
        parts.append("Từ khoá đã dùng: " + ", ".join(used_terms[:40]))
    parts.append("=== ĐOẠN ĐỌC ĐƯỢC ===\n" + "\n\n".join(body))
    return "\n\n".join(parts)


# ------------------------------------------------ pass F: tổng hợp trả lời

ANSWER_SYSTEM = LANGUAGE_RULE + """\
Bạn viết câu trả lời cuối cùng, dựa **chỉ** trên bằng chứng được đưa dưới đây.

## Trích dẫn

Mọi khẳng định lấy từ tài liệu phải kèm mã đoạn ngay sau nó, dạng `[p3c17]`.
Nhiều nguồn thì `[p3c17][p9c2]`. Mã phải là mã có trong phần bằng chứng — bịa mã
là bị máy bắt và câu đó bị gắn cờ đỏ trước mặt người đọc.

Con số thì bắt buộc phải có trích dẫn, và phải **chép đúng nguyên văn** con số
trong đoạn: không làm tròn, không đổi đơn vị, không "khoảng".

## Cấu trúc

Trả lời thẳng câu hỏi ngay đoạn đầu, 2–4 câu, không mở bài. Sau đó mới triển
khai. Dùng tiêu đề `##` khi câu trả lời có nhiều phần; dùng bảng khi so sánh từ
ba đối tượng trở lên.

## Chỗ không có bằng chứng — luật quan trọng nhất

Bảng kiểm ở dưới ghi rõ câu hỏi con nào **chưa tìm được bằng chứng**. Những mục
đó phải được nói ra, ở một mục riêng cuối câu trả lời:

> **Chưa tìm thấy trong kho:** … (nói rõ thiếu gì, và gợi ý cần thêm loại tài
> liệu nào)

Viết trơn tru đè lên chỗ trống là kiểu hỏng tệ nhất của công cụ này: người đọc
tưởng đã có câu trả lời trong khi chưa hề có. Thà nói thiếu.

## Mâu thuẫn giữa các bài

Hai bài nói ngược nhau thì **trình bày cả hai kèm trích dẫn**, chỉ ra chúng khác
nhau ở điều kiện nào (tập dữ liệu, cỡ mô hình, cách đo). Không tự chọn một bên.

"""  + DEPTH_RULES


def answer_user(question: str, subs: list[dict], covered: list[str],
                groups: dict, evidence: list[dict], stopped: str = "") -> str:
    """Bằng chứng **nhóm theo câu hỏi con**, không đổ phẳng.

    Hỏng khâu tổng hợp chiếm 87,3% số câu sai trong bài đo về multi-hop QA khoa
    học — đưa một đống phẳng là bắt model tự phân loại lại trong lúc đang phải
    viết, đúng chỗ nó hỏng.
    """
    out = [f"CÂU HỎI: {question}"]

    lines = []
    for s in subs:
        mark = "đã có bằng chứng" if s["id"] in covered else "CHƯA TÌM THẤY BẰNG CHỨNG"
        lines.append(f"- {s['id']}: {s['ask']} — {mark}")
    out.append("BẢNG KIỂM:\n" + "\n".join(lines))

    if stopped:
        out.append(f"LƯU Ý: quá trình tìm đã dừng sớm ({stopped}). Nói rõ điều này "
                   f"cho người đọc ở cuối câu trả lời.")

    by_id = {e["id"]: e for e in evidence}
    blocks = []
    for s in subs:
        found = groups.get(s["id"]) or []
        if not found:
            blocks.append(f"### {s['id']} — {s['ask']}\n(không có bằng chứng nào)")
            continue
        rows = []
        for f in found:
            e = by_id.get(f.get("chunk")) or {}
            head = (f"[{f.get('chunk')}] {e.get('paper_title','')[:80]}"
                    f" ({e.get('year') or 'n.d.'}) — {e.get('section') or ''}")
            rows.append(f"{head}\n  • {f.get('finding','')}\n  “{f.get('quote','')}”")
        blocks.append(f"### {s['id']} — {s['ask']}\n" + "\n\n".join(rows))
    out.append("=== BẰNG CHỨNG, NHÓM THEO CÂU HỎI CON ===\n\n" + "\n\n".join(blocks))

    if evidence:
        full = "\n\n".join(
            f"[{e['id']}] {e.get('paper_title','')[:80]} ({e.get('year') or 'n.d.'})"
            f" — {e.get('section') or ''}\n{e['text'][:1500]}"
            for e in evidence)
        out.append("=== NGUYÊN VĂN CÁC ĐOẠN ĐƯỢC TRÍCH ===\n" + full)
    return "\n\n".join(out)


# --------------------------------------------- kiểm chứng: chấm kéo theo

ENTAIL_SYSTEM = LANGUAGE_RULE + """\
Bạn đối chiếu từng câu trong một bản trả lời với đoạn tài liệu mà chính nó trích
dẫn, và chấm xem đoạn đó **có thật sự đỡ được câu đó không**.

Đây là chỗ bắt "trích dẫn hình thức": câu có mã nguồn kèm sau nên trông như đã
được chứng minh, nhưng nội dung đoạn không nói điều đó — nói về chuyện gần giống,
hoặc nói yếu hơn nhiều so với cách câu trả lời phát biểu.

""" + JSON_RULE + """
{"checks": [{"i": 0, "v": "yes|weak|no", "why": "≤12 từ, chỉ điền khi v khác yes"}]}

  yes  — đoạn nói đúng điều câu đó khẳng định
  weak — đoạn nói về đúng chuyện đó nhưng yếu hơn, hoặc chỉ đỡ được một phần
  no   — đoạn không đỡ được, hoặc nói ngược lại
"""


def entail_user(sentences: list[dict]) -> str:
    out = []
    for i, s in enumerate(sentences):
        src = "\n".join(f"  [{c['id']}] {c['text'][:900]}" for c in s["cited"])
        out.append(f"[{i}] CÂU: {s['text']}\nĐOẠN NÓ TRÍCH:\n{src}")
    return "\n\n".join(out)


# ---------------------------------------------------------- phiếu của kho


def paper_labels(papers: list[dict]) -> dict[str, str]:
    """Nhãn NGẮN cho từng bài: `P1`, `P2`… Trả {mã thật → nhãn}.

    Vì sao không dùng thẳng mã thật trong prompt: mã bài (`p50d58cb2d3`) và mã
    đoạn (`p50d58cb2d3c14`) chỉ khác nhau ở phần đuôi, nên model liên tục lẫn hai
    thứ và viết ra mã 12 ký tự không tồn tại. Đo trên bài thật: **6 trong 9 cảnh
    báo** của một bản tổng hợp là "mã bài không có trong kho", và vì không mã nào
    khớp nên cả phần hướng tiếp cận thành vô dụng.

    `P1` thì không thể nhầm với `p50d58cb2d3c14`, ngắn nên chép không sai, và đọc
    ra cũng dễ hơn. Xếp theo `id` chứ không theo thứ tự truyền vào, để nhãn cố
    định giữa các lần gọi — `corpus_digest` phải byte-identical thì cache mới hit.
    """
    return {p["id"]: f"P{i + 1}"
            for i, p in enumerate(sorted(papers, key=lambda x: x["id"]))}


def corpus_digest(papers: list[dict]) -> str:
    """Phiếu của cả kho, ghép thành một khối **cố định byte giữa mọi câu hỏi**.

    Đây là `cached_prefix` của cơ chế survey. Nhét bất cứ thứ gì thay đổi theo
    request vào đây (câu hỏi, thời gian, số vòng) là hỏng cache và chi phí nhân
    lên nhiều lần — đúng cái bẫy đã ghi trong CLAUDE.md cho luồng dịch.

    Bài xếp theo `id` chứ không theo thời gian cập nhật: xếp theo thời gian thì
    mở lại một bài cũng đảo thứ tự và cache trượt sạch.
    """
    lab = paper_labels(papers)
    rows = []
    for p in sorted(papers, key=lambda x: x["id"]):
        card = p.get("card") or {}
        if not card:
            continue
        bits = [f"[{lab[p['id']]}] {card.get('title_vi') or p.get('title') or ''}"
                f" ({p.get('year') or 'n.d.'}{', ' + p['venue'] if p.get('venue') else ''})"]
        for key, label in (("tldr_vi", "Chốt"), ("task", "Bài toán"), ("idea", "Ý tưởng"),
                           ("method", "Cách làm"), ("gap", "Khoảng trống"),
                           ("limitations", "Giới hạn")):
            if card.get(key):
                bits.append(f"  {label}: {card[key]}")
        for key, label in (("datasets", "Dữ liệu"), ("metrics", "Đo bằng"),
                           ("baselines", "So với"), ("keywords_en", "Từ khoá")):
            if card.get(key):
                bits.append(f"  {label}: {', '.join(str(x) for x in card[key][:12])}")
        for r in (card.get("results") or [])[:4]:
            num = f" ({r['number']})" if r.get("number") else ""
            bits.append(f"  Kết quả{num}: {r.get('claim','')} [{r.get('chunk','')}]")
        rows.append("\n".join(bits))
    if not rows:
        return ""
    return ("=== PHIẾU TÓM TẮT TOÀN KHO ===\n"
            "Mỗi bài một phiếu. Mã bài là dạng NGẮN `P1`, `P2`… ghi trong ngoặc vuông "
            "ở đầu mỗi phiếu. Mã đoạn thì dài và khác hẳn, dạng `p3f2a1c17`.\n"
            "Đừng lẫn hai loại: chỗ nào cần chỉ ra một BÀI thì viết `P1`, chỗ nào cần "
            "trích một ĐOẠN thì chép nguyên mã đoạn.\n\n"
            + "\n\n".join(rows))


def compact(obj) -> str:
    return json.dumps(obj, ensure_ascii=False)


# ------------------------------------------------ pass G: tổng hợp cả kho

SYNTH_SYSTEM = LANGUAGE_RULE + """\
Bạn viết bản tổng hợp cho một người **mới bước vào lĩnh vực này**, dựa trên cả
kho tài liệu ở trên.

Đây không phải bản tóm tắt từng bài xếp cạnh nhau. Tóm tắt từng bài thì đọc xong
vẫn không hiểu lĩnh vực — người đọc nhận được N mẩu rời và phải tự ghép. Việc của
bạn là **ghép sẵn**: chỉ ra các bài đang cãi nhau về chuyện gì, chia thành mấy
hướng, mỗi hướng đặt cược vào giả định nào, và cái gì thật sự mới.

Phép thử: đọc xong bản này, người ta phải **đoán được bài tiếp theo trong lĩnh
vực sẽ làm gì**. Không đạt phép thử đó thì bản tổng hợp mới chỉ là mục lục.

""" + JSON_RULE + """
{
  "title": "Tên gọi ngắn cho lĩnh vực/chủ đề mà kho này bao phủ.",
  "scope": "Kho gồm gì: bao nhiêu bài, khoảng năm nào, và ĐIỀU GÌ NÓ KHÔNG BAO PHỦ. Nói rõ giới hạn — người đọc phải biết mình đang nhìn qua khe nào.",

  "problem": {
    "statement": "Bài toán chung mà các bài này cùng nhắm tới. 2-3 câu, viết bằng ngôn ngữ thường.",
    "why_hard": "Vì sao nó khó — khó ở đâu về mặt kỹ thuật, không phải 'vì chưa ai làm'.",
    "framings": [
      {"name": "Tên cách đặt vấn đề",
       "desc": "Nhóm này coi bài toán là gì. Cùng một hiện tượng nhưng đặt vấn đề khác nhau thì cách giải khác hẳn — đây là chỗ chia rẽ sâu nhất, sâu hơn cả khác biệt kỹ thuật.",
       "papers": ["mã bài"]}
    ]
  },

  "approaches": [
    {"name": "Tên hướng tiếp cận, đặt theo cơ chế chứ không theo tên bài.",
     "idea": "Ý tưởng cốt lõi trong một câu, nói bằng ngôn ngữ thường.",
     "mechanism": "Cơ chế cụ thể: dữ liệu vào gì, qua những bước nào, ra gì, và VÌ SAO bước đó tạo ra kết quả ấy. 3-5 câu. Người đọc phải kể lại được cho người khác nghe. Xem tiêu chuẩn độ sâu ở dưới.",
     "bet": "Hướng này ĐẶT CƯỢC vào giả định nào. Giả định đó sai thì hướng này sụp. Đây là trường quan trọng nhất của cả bản tổng hợp.",
     "falsify": "Quan sát nào sẽ chứng minh hướng này sai? Nêu một phép thử cụ thể, dù kho chưa ai làm. Không nghĩ ra được thì giả định ở `bet` chưa đủ sắc — viết lại nó.",
     "papers": ["mã bài"],
     "evidence": [{"claim": "Bằng chứng ủng hộ, có số nếu bài có số.", "cite": "mã đoạn"}],
     "cost": "Cái giá phải trả: tính toán, dữ liệu, giả thiết thêm, hay thứ nó đánh đổi đi."}
  ],

  "novelty": [
    {"paper": "mã bài",
     "new": "Cái THẬT SỰ mới của bài này — thứ mà bỏ đi thì bài không còn đóng góp gì.",
     "assembled": "Phần chỉ là ghép các thứ có sẵn. Ghi rõ ghép từ đâu. Để rỗng nếu không có.",
     "cite": "mã đoạn"}
  ],

  "tensions": [
    {"about": "Hai bên bất đồng về điều gì.",
     "sides": [{"papers": ["mã bài"], "claim": "Bên này nói gì.", "cite": "mã đoạn"}],
     "why": "Vì sao chúng khác nhau: khác tập dữ liệu, khác cỡ mô hình, khác cách đo, hay thật sự mâu thuẫn?"}
  ],

  "gaps": [
    {"gap": "Khoảng trống chưa ai lấp.",
     "why": "Vì sao nó còn trống — khó về kỹ thuật, thiếu dữ liệu, hay chỉ là chưa ai nghĩ tới."}
  ],

  "read_order": [
    {"paper": "mã bài", "why": "Đọc bài này trước/sau vì lý do gì."}
  ]
}

## Luật bắt buộc

1. **Hai loại mã, đừng lẫn.**
   - Chỗ nào chỉ ra một BÀI (`papers`, `paper`) thì viết mã ngắn: `P1`, `P2`…
     đúng như ghi ở đầu mỗi phiếu.
   - Chỗ nào TRÍCH DẪN (`cite`) thì chép nguyên mã đoạn dài, dạng `p3f2a1c17`.
   Cả hai phải có thật trong phiếu ở trên. Bịa mã là bị máy bắt và gắn cờ đỏ
   trước mặt người đọc.

2. **Con số phải chép nguyên văn** như trong phiếu. Không làm tròn, không quy đổi.

3. **Không suy ra ngoài kho.** Thứ bạn biết sẵn về lĩnh vực này mà kho không có
   thì không được đưa vào — trừ khi ghi rõ ở `scope` rằng kho thiếu phần đó.

4. **`approaches` gom theo CƠ CHẾ, không theo bài.** Hai bài cùng cơ chế thì
   chung một hướng. Mỗi bài một hướng là dấu hiệu bạn chưa đọc ra điểm chung —
   và bản tổng hợp lại thành mục lục.

5. **Kho nhỏ thì nói thẳng là nhỏ.** Dưới năm bài thì `scope` phải ghi rõ đây
   chưa đủ để kết luận về cả lĩnh vực, và `approaches` không được bịa ra nhiều
   hướng hơn số hướng thật sự có mặt.

6. **`bet`, `falsify` và `tensions` là chỗ bản tổng hợp có giá trị.** Trường nào
   cũng viết trung tính, ai cũng đúng, không ai sai — thì bạn vừa viết xong một
   bản tóm tắt. Chỉ ra chỗ các bài đặt cược khác nhau, và nói rõ khi bằng chứng
   không đủ để phân xử.

""" + DEPTH_RULES


def synth_user(digest: str, edges_text: str, n_papers: int) -> str:
    parts = [
        f"Kho có {n_papers} bài. Viết bản tổng hợp theo đúng cấu trúc JSON đã nêu.",
    ]
    if n_papers < 5:
        parts.append("LƯU Ý: kho này nhỏ. `scope` phải nói rõ điều đó, và đừng "
                     "dựng ra nhiều hướng tiếp cận hơn số hướng thật sự có.")
    if edges_text:
        parts.append("=== QUAN HỆ ĐÃ BÓC ĐƯỢC TỪ CÁC BÀI (dùng để dựng phần kế "
                     "thừa và phần bất đồng) ===\n" + edges_text)
    if digest:
        parts.append(digest)
    return "\n\n".join(parts)
