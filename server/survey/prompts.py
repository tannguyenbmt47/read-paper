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
    """Chỉ liệt kê MÃ đoạn — toàn văn đã nằm sẵn trong prefix được cache.

    Bản trước chép lại tối đa 900 ký tự của từng đoạn vào message `user`, tức
    gửi bài lần thứ hai ở chỗ **không có cache**. Đo trên 4 bài: 204.520 ký tự
    lặp ≈ 56.800 token giá đầy đủ, khoảng **17k token thừa mỗi bài** — bằng
    đúng một bản sao của cả bài mỗi lần nạp.

    `card_user` và `graph._user` vốn đã làm đúng: chúng truyền chuỗi rỗng vì
    nội dung đã ở prefix. Đây là chỗ duy nhất không đối xứng.
    """
    lines = []
    for it in items:
        sec = it.get("section") or "không rõ mục"
        lines.append(f"[{it['ord']}] ({sec})")
    return ("PASSAGES TO SITUATE — full text of each is in the paper above, "
            "marked <<<…c{ord}>>>:\n" + "\n".join(lines))


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


# ====================================================================== bài giảng
#
# Pass thứ bảy, và là pass duy nhất có mục tiêu KHÁC hẳn sáu pass trên: sáu pass
# kia trả lời câu hỏi, pass này làm cho một người **đọc hiểu được cả bài**.
#
# Ba kết quả nghiên cứu quyết định cấu trúc dưới đây, không phải khẩu vị:
#
# 1. **Thứ chặn người đọc là kiến thức nền không được nói ra**, không phải câu
#    dài hay từ khó. Tác giả viết cho đồng nghiệp cùng ngành nên cố ý bỏ qua
#    phần "ai cũng biết" — mà người đọc mới thì không biết. Đây là *curse of
#    knowledge*, và nó là lý do mục `prereq` đứng ĐẦU chứ không phải cuối.
# 2. **Paper Plain (TOCHI 2023)** đo được: tóm lược tại chỗ + bộ câu hỏi dẫn
#    đường + định nghĩa thuật ngữ làm người không chuyên đọc bài y khoa dễ hơn
#    hẳn **mà không giảm mức hiểu**. Ba thứ đó thành `prereq`, `check`, `terms`.
# 3. **Hỏi "vì sao" bắt người học phải dựng lời giải thích** thay vì đọc lướt
#    (elaborative interrogation / self-explanation). Nên `mechanism` bắt buộc
#    mỗi bước phải nói vì sao bước ấy cần, và `check` là câu hỏi tự kiểm chứ
#    không phải bản tóm tắt thứ hai.
#
# Mục `compare` là chỗ tiết kiệm lớn nhất của cả cơ chế: nó KHÔNG đọc bài được
# dẫn, mà đọc **câu văn trong chính bài này ở chỗ trích dẫn** (xem `refs.py`).
# Tác giả đã tự nói ra họ lấy gì từ bài kia; ta chỉ việc dùng lại.

LECTURE_SYSTEM = LANGUAGE_RULE + """\
Bạn đang viết **bài giảng** về một bài báo khoa học: một người đọc từ đầu đến
cuối là hiểu được bài đó, không cần ai giảng thêm.

Bạn KHÔNG viết tóm tắt. Tóm tắt trả lời "bài này nói gì"; bài giảng trả lời
"**vì sao nó phải làm như vậy, và nó chạy ra sao**".

## Người đọc của bạn

Một người học cùng lĩnh vực rộng nhưng **chưa làm đúng nhánh hẹp này**: đọc được
ký hiệu toán, biết các khái niệm nền phổ thông của ngành, nhưng chưa từng đọc
những bài mà bài này giả định là ai cũng đã đọc.

Sai lầm phải tránh bằng mọi giá là **viết như tác giả viết**. Tác giả bỏ qua
phần "ai trong nhánh này cũng biết" — mà đó chính là chỗ người đọc của bạn gãy.
Chỗ nào bài báo nói vắn tắt vì cho là hiển nhiên, bạn phải dừng lại và nói rõ.

## Luật viết

- **Ký hiệu phải được đọc thành lời.** Gặp `z_t ~ q(·|x_t)` thì viết ra: đây là
  gì, chỉ số chạy trên cái gì, tại sao cần chỉ số đó.
- **Mỗi khái niệm mới xuất hiện lần đầu phải kèm một câu nói nó LÀM GÌ**, không
  phải nó tên gì.
- **Thuật ngữ tiếng Anh giữ nguyên**, kèm giải thích tiếng Việt lần đầu:
  "latent action (hành động ẩn — vector mã hoá phần thay đổi giữa hai khung
  hình)". Đừng dịch thuật ngữ ra tiếng Việt tự chế.
- **Câu văn học thuật hoàn chỉnh**, không viết kiểu tít báo, không gạch đầu dòng
  cụt. Đủ hư từ ("của", "trong", "so với", "khi") để câu đúng ngữ pháp.
- Không mở bài bằng "Trong bài báo này, các tác giả…" — vào thẳng nội dung.

""" + DEPTH_RULES + """

## Ràng buộc về sự thật

- **Mọi con số phải có mặt nguyên văn trong bài.** Không suy ra, không làm tròn
  khác đi, không ghép hai số thành một tỉ lệ mới.
- **Mỗi mục phải khai `source` — danh sách mã đoạn** (chính là mã ghi trong
  `<<<…>>>` ở phần TOÀN VĂN bên trên, chép nguyên si) mà
  nội dung mục đó dựa vào. Mã phải có thật trong phần TOÀN VĂN bên trên.
- Bài không nói thì **viết là bài không nói**. Đừng lấp bằng kiến thức chung của
  bạn; người đọc không phân biệt được đâu là bài, đâu là bạn.
- Phần đối chiếu chỉ được dùng **HỒ SƠ ĐỐI CHIẾU** đưa kèm. Câu trích dẫn trong
  hồ sơ là do máy bóc tự động nên **có thể lệch**: chỉ khẳng định điều mà câu
  trích dẫn thật sự chứa. Không chắc thì nói về bài được dẫn ở mức tóm tắt.
"""

# Từng mục một, và bản mô tả này chính là thứ model đọc. Viết rõ "mục này hỏng
# như thế nào" hiệu quả hơn hẳn viết "mục này nên có gì" — model đã biết cách
# viết hay, cái nó không biết là cái bẫy nào đang chờ.
SECTIONS: dict[str, dict] = {
    "prereq": {
        "title": "Cần biết trước",
        "spec": (
            "Ba đến năm thứ mà **bài báo giả định người đọc đã biết** và vì thế "
            "không giải thích. Mỗi thứ: tên (giữ thuật ngữ tiếng Anh) + 2–4 câu "
            "nói nó LÀM GÌ và vì sao bài này cần đến nó.\n"
            "Đây là mục quan trọng nhất của cả bài giảng: thiếu kiến thức nền "
            "mới là thứ làm người ta đọc không vào, chứ không phải câu dài.\n"
            "Chọn đúng thứ bài này THẬT SỰ dựa vào, không phải thứ phổ thông của "
            "ngành. 'Mạng nơ-ron là gì' thì không ai cần; "
            "'vì sao pretraining không nhãn lại thay được dữ liệu có nhãn' thì cần."),
        "shape": '{"items": [{"term": "…", "why": "…"}], "source": ["…"]}',
    },
    "problem": {
        "title": "Bài toán",
        "spec": (
            "Bài toán bài này giải, **kể bằng một tình huống cụ thể có thật trong "
            "bài** — một đầu vào thật, một thất bại thật. Không phát biểu trừu "
            "tượng kiểu 'vấn đề X còn nhiều thách thức'.\n"
            "Sau đó nói rõ **cái giá của việc không giải được**: thiếu nó thì "
            "người ta phải làm gì thay thế, và tốn kém ở đâu."),
        "shape": '{"body": "…", "source": ["…"]}',
    },
    "why_hard": {
        "title": "Vì sao cách hiển nhiên không xong",
        "spec": (
            "Cách mà một người thông minh sẽ nghĩ ra đầu tiên, và **chính xác nó "
            "vỡ ở đâu**. Đây là mục làm cho đóng góp của bài trở nên có nghĩa: "
            "không có nó thì phương pháp của bài chỉ là một cách làm trong vô số "
            "cách, và người đọc không thấy vì sao phải phức tạp đến thế.\n"
            "Nếu bài có nói về các hướng trước đó thì dùng đúng chỗ đó."),
        "shape": '{"body": "…", "source": ["…"]}',
    },
    "mechanism": {
        "title": "Cơ chế, chạy tay một ví dụ",
        "spec": (
            "**Mục dài nhất và quan trọng nhất.** Lấy MỘT đầu vào cụ thể có thật "
            "trong bài rồi đi hết đường của nó qua phương pháp, theo từng bước.\n"
            "Mỗi bước gồm ba phần, thiếu phần nào là hỏng cả mục:\n"
            "  `do` — bước này làm gì, với đại lượng nào, ra cái gì;\n"
            "  `why` — **vì sao bước này cần thiết**; bỏ nó đi thì hỏng chỗ nào;\n"
            "  `note` — ký hiệu/siêu tham số của bước này đọc thành lời (nếu có).\n"
            "Sáu đến mười bước. Đây là chỗ người nghe thường gật đầu suốt rồi ra "
            "về không kể lại được cho ai — vì phần giữa chỉ còn cái tên và một sơ "
            "đồ ba hộp. Đừng viết như vậy.\n"
            "Ưu tiên đầu vào **có thật trong bài**. Bài không kể ví dụ nào chạy "
            "hết đường thì được tự dựng một tình huống minh hoạ, nhưng phải **nói "
            "rõ đó là giả định** ngay ở `input` (\"giả sử…\", \"lấy ví dụ…\") và "
            "các con số trong đó phải **nhất quán với nhau**. Tuyệt đối không "
            "trình bày số tự nghĩ ra như thể là kết quả đo được của bài."),
        "shape": ('{"input": "…", "steps": [{"do": "…", "why": "…", "note": "…"}], '
                  '"source": ["…"]}'),
    },
    "compare": {
        "title": "Đặt cạnh những bài nó dẫn",
        "spec": (
            "Dùng **HỒ SƠ ĐỐI CHIẾU** kèm bên dưới. Với mỗi bài đáng nói (4–7 "
            "bài), viết:\n"
            "  `paper` — tên bài được dẫn;\n"
            "  `took` — bài này **lấy gì** từ đó (ý tưởng, thành phần, tập dữ liệu);\n"
            "  `differs` — và **khác ở chỗ nào**, cụ thể, không phải 'cải tiến hơn'.\n"
            "Câu trích dẫn trong hồ sơ là lời của chính tác giả bài này nói về "
            "bài kia — đó là bằng chứng tốt nhất bạn có, hãy bám vào nó.\n"
            "Cuối mục, `placement`: 2–4 câu đặt bài này vào mạch nghiên cứu — nó "
            "nối tiếp nhánh nào, và nó cãi lại giả định nào của nhánh đó."),
        "shape": ('{"items": [{"paper": "…", "took": "…", "differs": "…"}], '
                  '"placement": "…", "source": ["…"]}'),
    },
    "evidence": {
        "title": "Số liệu nói gì, và không nói gì",
        "spec": (
            "Hai đến bốn kết quả chính, **kèm con số nguyên văn** và điều kiện đo "
            "(tập dữ liệu nào, so với cái gì, đo bằng chỉ số nào).\n"
            "Rồi phần quan trọng hơn: `limits_of_evidence` — những gì các số này "
            "**không** chứng minh. Thí nghiệm chỉ chạy trên một tập? Baseline có "
            "được chỉnh ngang mức không? Chênh lệch có nằm trong dao động không?\n"
            "Đây là chỗ phân biệt người đọc hiểu bài với người đọc xong tin bài."),
        "shape": ('{"items": [{"claim": "…", "number": "…", "setting": "…"}], '
                  '"limits_of_evidence": "…", "source": ["…"]}'),
    },
    "limits": {
        "title": "Chỗ đáng ngờ",
        "spec": (
            "Ba đến năm điểm yếu thật. Ưu tiên **điều bài tự thừa nhận**, rồi mới "
            "đến điều bạn thấy được từ chính nội dung bài (giả định chưa kiểm, "
            "phạm vi thí nghiệm hẹp, chi phí không báo cáo).\n"
            "Mỗi điểm phải nói **hệ quả**: điểm yếu này làm kết luận nào yếu đi.\n"
            "Không viết những câu vô thưởng vô phạt kiểu 'cần thêm nghiên cứu'."),
        "shape": '{"items": [{"point": "…", "so_what": "…"}], "source": ["…"]}',
    },
    "check": {
        "title": "Tự kiểm tra",
        "spec": (
            "Bốn đến sáu câu hỏi để người đọc tự soát xem mình đã hiểu chưa, kèm "
            "đáp án ngắn.\n"
            "Phải là câu hỏi **vì sao / điều gì xảy ra nếu**, không phải câu hỏi "
            "tra cứu. 'Phương pháp tên gì' thì vô dụng; 'bỏ bước lượng tử hoá đi "
            "thì hỏng chỗ nào' mới buộc người đọc phải dựng lại lời giải thích — "
            "và chính việc dựng lại đó mới là lúc người ta học được.\n"
            "Ít nhất một câu phải hỏi vào chỗ dễ hiểu nhầm nhất của bài."),
        "shape": '{"items": [{"q": "…", "a": "…"}], "source": ["…"]}',
    },
}

# Mẻ nhỏ, và `mechanism` đi MỘT MÌNH. Hai lý do cùng chiều nhau:
#
# 1. **Mỗi mục được nhiều budget hơn, không phải ít.** Cùng bài học với
#    `RENDER_BATCH` bên pipeline: dựng cả tám mục một lượt thì mỗi mục được chia
#    chưa tới một nghìn token đầu ra và model tự cắt cho vừa. `mechanism` là mục
#    dài nhất và đáng đọc nhất — nhét nó cạnh ba mục khác là bóp đúng chỗ không
#    được bóp.
# 2. **Một request phải xong trong 300s** (`llm` timeout). Đã vấp: bốn mục với
#    9000 token đầu ra trong một lượt chạy quá 5 phút rồi bị cắt, và mẻ đó mất
#    trắng. Bốn mẻ nhỏ thì mẻ nào cũng về đích, mà prefix vẫn ấm nên phần đọc vào
#    gần như không tốn thêm — chia nhỏ ở đây gần như miễn phí.
LECTURE_BATCHES = (("prereq", "problem", "why_hard"),
                   ("mechanism",),
                   ("compare", "evidence"),
                   ("limits", "check"))


def dossier_text(dos: dict) -> str:
    """Hồ sơ đối chiếu dựng thành text cho prompt. Xem `refs.py` — $0 để lấy về."""
    refs = (dos or {}).get("refs") or []
    if not refs:
        return ""
    out = ["=== HỒ SƠ ĐỐI CHIẾU — các bài mà bài này dẫn ===",
           "Mỗi mục gồm: bài được dẫn là gì, và NGUYÊN VĂN câu trong bài này ở chỗ "
           "trích dẫn nó. Câu đó do máy bóc tự động, có thể lệch — chỉ khẳng định "
           "điều nó thật sự chứa."]
    for i, r in enumerate(refs, 1):
        head = f"[R{i}] {r['title']}"
        if r.get("year"):
            head += f" ({r['year']})"
        if r.get("influential"):
            head += "  ★ bài này dựa nhiều vào nó"
        if r.get("paper_id"):
            head += "  · cũng có trong kho"
        out.append(head)
        if r.get("gist"):
            out.append(f"    là gì: {r['gist']}")
        for w in (r.get("why") or []):
            out.append(f"    chỗ dẫn: “{w}”")
    return "\n".join(out)


def lecture_user(title: str, card: dict | None, names: tuple[str, ...],
                 dossier: str = "", redo: dict | None = None) -> str:
    """Phần thay đổi theo mẻ. Toàn văn bài nằm ở prefix, KHÔNG lặp lại ở đây."""
    parts = [f"Bài: {title}"]
    if card:
        keep = {k: card[k] for k in ("task", "problem", "idea", "method", "novelty")
                if card.get(k)}
        if keep:
            parts.append("Phiếu tóm tắt đã bóc trước đó (dùng làm định hướng, "
                         "nội dung thật vẫn lấy từ toàn văn):\n" + compact(keep))

    want = []
    for n in names:
        s = SECTIONS[n]
        want.append(f"### `{n}` — {s['title']}\n{s['spec']}\nDạng: {s['shape']}")
    parts.append("Viết " + str(len(names)) + " mục sau, mỗi mục một khoá trong "
                 "JSON trả về:\n\n" + "\n\n".join(want))

    if dossier and "compare" in names:
        parts.append(dossier)

    if redo:
        parts.append(
            "=== LẦN TRƯỚC BỊ CHẤM LÀ NÔNG, VIẾT LẠI ĐÚNG NHỮNG CHỖ NÀY ===\n"
            + "\n".join(f"- [{k}] {m}" for k, m in redo.items())
            + "\n\nGiữ nguyên phần đã đạt. Chỗ bị chấm thì viết lại cho tới cơ "
              "chế: nói bằng cách nào, chứ không nói rằng có.")

    parts.append(JSON_RULE + 'Trả về đúng dạng {"' + names[0] + '": …, …} '
                 "và không gì khác.")
    return "\n\n".join(parts)
