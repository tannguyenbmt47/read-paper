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
import unicodedata
from typing import AsyncIterator

from . import db, depth, llm, prompts, store
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


# Ký tự được phép có mặt trong bản dịch tiếng Việt mà bản gốc không có: chữ
# Latinh (kể cả dấu tiếng Việt), số, dấu câu, ký hiệu toán và chữ Hy Lạp — công
# thức dùng thật. Mọi thứ ngoài đây mà bản gốc không có là model trả về rác.
_OK_SCRIPT = re.compile(
    "["
    "\\t\\n\\r"
    "\\u0020-\\u024F"      # ASCII + Latinh mở rộng
    "\\u02B0-\\u02FF"      # dấu rời (TeX đặt mũ bằng `ˆ` đứng trước chữ)
    "\\u0300-\\u036F"      # dấu tổ hợp
    "\\u0370-\\u03FF"      # Hy Lạp — công thức dùng thật
    "\\u1D00-\\u1DBF"      # chữ cái dạng chỉ số trên/dưới — parser sinh ra thật
    "\\u1E00-\\u1EFF"      # Latinh mở rộng bổ sung (dấu tiếng Việt)
    "\\u2000-\\u206F"      # dấu câu
    "\\u2070-\\u209F"      # chỉ số trên/dưới
    "\\u20A0-\\u20CF"      # tiền tệ
    "\\u2100-\\u23FF"      # ký hiệu chữ, mũi tên, toán, kỹ thuật
    "\\u25A0-\\u26FF"      # hình học, ký hiệu khác
    # Ba dải toán còn lại. Thiếu chúng là chốt chặn bắt OAN, và bắt oan ở đây
    # tốn tiền thật: bản dịch sạch bị giữ ngoài `tm` nên mọi bài sau có đoạn y hệt
    # đều phải dịch lại. Đã bắt oan `⟨⟩` (U+27E8/27E9) — ngoặc nhọn toán học,
    # dùng cho tích trong và dãy, có mặt trong bài SONIC.
    "\\u27C0-\\u27EF"      # ký hiệu toán A (⟨ ⟩ ⟦ ⟧)
    "\\u2980-\\u29FF"      # ký hiệu toán B
    "\\u2A00-\\u2AFF"      # toán tử toán bổ sung (⨁ ⩽)
    "\\uFB00-\\uFB4F"      # ligature
    "]")


def script_leak(out: str, src: str) -> set[str]:
    """Ký tự thuộc hệ chữ lạ mà bản gốc không hề có — dấu hiệu model trả về rác.

    Tổng quát hơn `cjk_leak`, và cần đúng như vậy: đã gặp bản dịch chứa
    `띠ᥕᥕᥲᥕᥱ` thay cho chữ "bảo toàn". `띠` là Hangul nên `cjk_leak` bắt được,
    nhưng `ᥕᥲᥱ` là chữ **Limbu** — ngoài mọi dải mà `cjk_leak` biết. Liệt kê
    từng hệ chữ cấm là trò đuổi bắt không có hồi kết; liệt kê hệ chữ **được
    phép** thì mọi thứ lạ đều bị bắt, kể cả hệ chữ chưa ai gặp bao giờ.

    So với bản gốc chứ không cấm tuyệt đối: bài về NLP đa ngữ trích tiếng Trung,
    tiếng Ả Rập là chuyện thường, và bản dịch giữ nguyên nguyên văn là đúng.
    """
    bad = {c for c in out if not _OK_SCRIPT.match(c)}
    return bad - set(src)


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


def _label_re(ids) -> "re.Pattern | None":
    """Biểu thức dò nhãn, dựng từ ĐÚNG tập mã đã yêu cầu ở mẻ này.

    Không đoán "nhãn trông thế nào" — ta đã biết chính xác mẻ này gồm mã nào,
    nên chỉ việc tìm đúng những mã đó ở vị trí của một nhãn. Nhờ vậy dung được
    mọi biến thể mà model hay gõ ra, mà không có nguy cơ cắt nhầm giữa bài.

    Hai kiểu lệch đã gặp thật, mỗi kiểu đủ để phá cả giao thức:
      `<<<b4_g>>`  — thiếu đúng MỘT dấu `>`
      `### b9_g`   — model đổi hẳn sang dạng tiêu đề Markdown
    Cả hai đều làm `<<<id>>>` không khớp, và **20 nghìn ký tự của mười mấy khối
    dồn hết vào một ô**, im lặng.
    """
    ids = [i for i in ids if i]
    if not ids:
        return None
    alt = "|".join(re.escape(i) for i in sorted(ids, key=len, reverse=True))
    return re.compile(
        r"^[ \t]*(?:"
        r"<{2,4}[ \t]*(?P<a>" + alt + r")[ \t]*>{2,4}"       # <<<b12>>> và <<b12>>
        r"|#{1,6}[ \t]*(?P<b>" + alt + r")_?[ \t]*"           # ### b12
        r"|\*\*[ \t]*(?P<c>" + alt + r")[ \t]*\*\*"         # **b12**
        r"|\[[ \t]*(?P<d>" + alt + r")[ \t]*\]"              # [b12]
        r")[ \t]*:?[ \t]*$",
        re.M)


def _close_re(ids) -> "re.Pattern | None":
    """Dòng chỉ gồm một NHÃN ĐÓNG kiểu thẻ XML — `</b370_g>>>`, `<</b12>>`, `[/b12]`.

    Model đôi khi "đóng" khối như đóng thẻ HTML. Bộ dò nhãn mở không nhận dạng
    này (cũng không nên: nó không mở khối nào), nên trước đây nó lọt nguyên vào
    cuối ô — đã thấy `</<b370_g>>>` nằm ở dòng cuối cột giải thích bài CIRAG.

    Dựng từ đúng tập mã của mẻ, như `_label_re`, và cũng đòi đứng MỘT MÌNH trên
    dòng: một câu nhắc tới mã khối không bao giờ bị cắt nhầm.
    """
    ids = [i for i in ids if i]
    if not ids:
        return None
    alt = "|".join(re.escape(i) for i in sorted(ids, key=len, reverse=True))
    return re.compile(
        # `<{0,4}` SAU dấu `/`: dạng thật đo trên dữ liệu là `</<b370_g>>>` — model
        # mở lại ngoặc nhọn ngay sau dấu đóng. Mẫu đầu tiên chỉ nhận `</b370_g>>>`
        # và bỏ lọt cả 8 ô dính trong `data/`.
        r"^[ \t]*(?:<{1,4}[ \t]*/[ \t]*<{0,4}[ \t]*(?:" + alt + r")[ \t]*>{1,4}"
        r"|\[[ \t]*/[ \t]*(?:" + alt + r")[ \t]*\])[ \t]*$\n?",
        re.M)


def _parse_labeled(text: str, ids=None) -> dict[str, str]:
    """Bóc `<<<id>>> nội dung` thành dict, chịu được đầu ra bị cắt giữa chừng.

    Có `ids` thì dò theo đúng tập mã đó (xem `_label_re`) — chắc hơn hẳn. Không
    có thì rơi về dạng `<<<id>>>` thuần, cho những chỗ gọi chưa biết trước mã.
    Nhãn ĐÓNG (`</b12>>>`) thì gỡ khỏi nội dung, xem `_close_re`.
    """
    rx = _label_re(ids) if ids else LABEL
    dong = _close_re(ids) if ids else None
    trong_me = set(ids or ())
    out: dict[str, str] = {}
    matches = list(rx.finditer(text))
    for i, m in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        key = next((g for g in m.groups() if g), None) if ids else m.group(1)
        if key:
            than = text[m.end():end]
            if trong_me:
                # Nhãn của khối NGOÀI mẻ đứng đầu dòng = model bắt đầu CHÉP LẠI
                # toàn văn bài trong prefix (cùng dạng `<<<id>>> [loại: … | mục:
                # …]`). Cắt ô tại đó. Đo trên `data/`: 3 ô giải thích, ô lớn nhất
                # 66.205 ký tự chứa 111 khối nguyên văn tiếng Anh — `_label_re`
                # chỉ dò mã TRONG mẻ nên mọi nhãn lạ dồn hết vào ô trước nó.
                for n in _NHAN_BAT_KY.finditer(than):
                    if n.group(1) not in trong_me:
                        than = than[:n.start()]
                        break
            if dong is not None:
                than = dong.sub("", than)
            out[key] = than.strip()
    return out


# Nhãn mở của BẤT KỲ khối nào, đứng đầu dòng — chỉ dùng để phát hiện nhãn ngoài
# mẻ (xem `_parse_labeled`), không dùng để tách ô.
_NHAN_BAT_KY = re.compile(r"^[ \t]*<{2,4}[ \t]*([A-Za-z]+\d+(?:_g)?)[ \t]*>{2,4}", re.M)


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


def chu_bi_bo_roi(doc: dict) -> tuple[int, int]:
    """Bao nhiêu ký tự trong PDF **thật sự bị bỏ rơi**, không tính phần bỏ có chủ ý.

    Đây là chốt chặn cho kiểu hỏng tệ nhất của bước bóc tách: **mất chữ im
    lặng**. Đã gặp thật — một bảng không viền trong bài hai cột biến mất hoàn
    toàn, không thành hình, không thành chữ, và Bước 1 vẫn báo "xong" kèm giá
    dịch. Người đọc không có cách nào biết mình vừa mất mấy con số.

    **Phải trừ phần bỏ CÓ CHỦ Ý, không thì nó báo động sai trên mọi bài.** Bản
    đầu chỉ lấy bội ký tự của toàn PDF trừ bội ký tự trong khối, và ra
    10.708 · 31.876 · 17.935 ký tự trên ba bài thật — gần như toàn bộ là chữ
    nằm trong hình (đã có trong ảnh) và tiêu đề chạy đầu trang. Một chốt chặn
    kêu oan trên mọi bài thì người dùng thôi đọc nó, và lúc đó cảnh báo thật
    cũng trôi theo — cùng bài học với mấy hằng ngân sách của slide.

    Nên đếm theo **vị trí**: một dòng chữ bị tính là bỏ rơi khi nó không nằm
    trong vùng hình nào, không nằm ở dải lề trên/dưới, và chữ của nó không có
    mặt trong khối nào. Trả 0 khi không có PDF gốc (bài dán, bài .md).
    """
    path = store.pdf_path(doc["id"])
    if path is None:
        return 0, 0

    LE = 0.055          # dải lề trên/dưới: tiêu đề chạy, số trang
    NO = 4.0            # nới vùng hình ra 4pt, vì khung bám sát chữ

    # vùng hình theo trang, lấy từ chính khung đã lưu
    vung: dict[int, list[tuple]] = {}
    for b in doc["blocks"]:
        r, pno = b.get("figure_rect"), b.get("figure_page")
        if r and pno is not None:
            vung.setdefault(int(pno), []).append(
                (r[0] - NO, r[1] - NO, r[2] + NO, r[3] + NO))

    # So bằng BỘI KÝ TỰ, không bằng phép "chuỗi con": một block của PyMuPDF trải
    # nhiều dòng, mà tầng bóc tách lại cắt và sắp lại, nên chuỗi của nó gần như
    # không bao giờ là chuỗi con liền mạch của khối đã dựng. Thử cách đó ra
    # 34.858 ký tự "bỏ rơi" trên một bài vốn phủ 99% — tức đo sai hoàn toàn.
    from collections import Counter

    def dem(t: str) -> Counter:
        return Counter(c.lower() for c in t if c.isalnum())

    # So với MỌI khối, kể cả `reference` và khối đã ẩn. `full_source_text()` cố
    # ý loại `reference` (nó là ngữ cảnh cho model, không cần thư mục), nhưng
    # dùng nó để đối chiếu thì **mỗi mục tham khảo thành một ký tự bị mất** —
    # đo trên SONIC: từ `arxiv` một mình bị tính mất 86 lần.
    giu = dem(" ".join(b["text"] for b in doc["blocks"])
              + " " + (doc.get("title") or ""))
    try:
        import fitz
        hop_le: Counter = Counter()
        with fitz.open(path) as d:
            # Tiêu đề chạy và số trang nhận bằng ĐỘ LẶP, không bằng dải lề: dải
            # 5,5% bỏ sót tiêu đề chạy của SONIC, và từ trong tên bài bị tính
            # mất 35–104 lần mỗi từ. Dòng nào xuất hiện ở ≥40% số trang thì là
            # thứ lặp theo trang, không phải nội dung.
            lap: Counter = Counter()
            for page in d:
                for blk in page.get_text("blocks"):
                    g = "".join(c.lower() for c in (blk[4] or "") if c.isalnum())
                    if 3 <= len(g) <= 120:
                        lap[g] += 1
            nguong = max(2, int(len(d) * 0.4))
            chay = {g for g, n in lap.items() if n >= nguong}

            for pno, page in enumerate(d):
                h = page.rect.height
                for x0, y0, x1, y1, txt, *_ in page.get_text("blocks"):
                    if not (txt or "").strip():
                        continue
                    g = "".join(c.lower() for c in txt if c.isalnum())
                    if g in chay:
                        continue                      # lặp theo trang
                    cy, cx = (y0 + y1) / 2, (x0 + x1) / 2
                    if cy < h * LE or cy > h * (1 - LE):
                        continue                      # lề: bỏ có chủ ý
                    if any(a <= cx <= c and b2 <= cy <= d2
                           for a, b2, c, d2 in vung.get(pno, [])):
                        continue                      # đã nằm trong ảnh
                    hop_le += dem(txt)
        # Trả cả TỈ LỆ, không chỉ con số tuyệt đối. 7.000 ký tự trên một bài
        # 70.000 ký tự là 10% — đáng biết; 7.000 trên một bài 500.000 thì không.
        # Và ngưỡng cảnh báo phải tính theo tỉ lệ, nếu không nó nổ trên mọi bài
        # dài và người dùng thôi đọc nó.
        tong = sum(hop_le.values())
        return sum((hop_le - giu).values()), tong
    except Exception:  # noqa: BLE001 — không đọc được thì đừng báo động sai
        return 0, 0


async def estimate(doc: dict, mode: str = "both") -> dict:
    """Ước lượng khối lượng và chi phí của bước 2, tính trước khi tiêu đồng nào.

    Đây là lý do bước tiền xử lý đáng tách riêng: nhìn được cấu trúc bóc ra có
    đúng không, và biết trước sẽ tốn bao nhiêu, rồi mới quyết định dịch.

    `mode` phải truyền vào: `"both"` sinh hai cột nên tốn gần gấp đôi `"vi"`.
    Con số này là thứ duy nhất người dùng dùng để quyết định, nên sai một chiều
    vài lần là họ thôi tin — và lúc đó cả kiến trúc hai bước mất giá trị.
    """
    todo = [b for b in doc["blocks"] if b.get("translate")]
    src_chars = sum(len(b["text"]) for b in todo)
    doc_chars = len(full_source_text(doc["blocks"]))

    # ~3.6 ký tự/token cho tiếng Anh học thuật; tiếng Việt dài hơn ~1.25 lần
    src_tok = src_chars / 3.6
    n_chunks = max(len(plan_chunks(doc)), 1)
    ctx_tok = doc_chars / 3.6

    # ĐẦU RA: phải tính theo SỐ CỘT sẽ sinh, không chỉ cột dịch.
    #
    # Bản đầu chỉ nhân 1,25 cho bản dịch, trong khi mặc định của màn đọc là hiện
    # cả hai cột — và cột giải thích còn dài hơn bản dịch (`PLAIN_TASK` đòi nói
    # cả vai trò của đoạn trong lập luận). Bỏ sót nguyên một cột.
    cols = 1.0 + (1.15 if mode in ("both", "plain") else 0.0)
    out_tok = src_tok * 1.25 * cols
    if doc.get("refine"):
        out_tok *= 1.7                       # pass soát lại, ô tick "Dịch kỹ"

    # ĐẦU VÀO: mỗi mẻ gửi lại TOÀN BỘ prefix, không phải 10%.
    #
    # Bản đầu giả định `ctx_tok * (1 + 0.1 * n_chunks)` — như thể chỉ 10% ngữ
    # cảnh được gửi lại mỗi mẻ. Thực tế mọi mẻ đều gửi nguyên `cached_prefix`;
    # nó được đọc từ cache nên RẺ hơn, nhưng vẫn tính tiền và vẫn phải đếm.
    # Đo trên bốn bài đã dịch xong: ước tính cũ thấp hơn thực chi 5,3–9,2 lần,
    # và **luôn lệch về phía rẻ** — kiểu sai tệ nhất cho một con số mà người
    # dùng dựa vào để quyết có tiêu tiền hay không.
    prompt_tok = ctx_tok * n_chunks + src_tok
    brief_out = 3000

    price = None
    try:
        price = await llm.gia_model(doc["model"])
    except Exception:  # noqa: BLE001
        price = None

    # Trả về một DẢI, không một con số. Ước tính token vốn có sai số ±40% (mật
    # độ ký tự/token khác nhau theo bài, model trả lời dài ngắn khác nhau), và
    # một con số duy nhất tạo ra ảo giác chính xác mà nó không có. Dải thì trung
    # thực, và người dùng vẫn quyết định được.
    cost = lo = hi = None
    if price and (price[0] or price[1]):
        cost = prompt_tok * price[0] + (out_tok + brief_out) * price[1]
        lo, hi = cost * 0.7, cost * 1.6

    # Ba con số khác nhau cùng hiện ở Bước 1 (tổng · sẽ dịch · hiển thị) mà
    # không chỗ nào nói chúng là gì — đo trên arXiv:1706.03762: 161 / 94 / 117.
    # Người dùng không có cách nào biết 67 khối còn lại đi đâu.
    vai: dict[str, int] = {}
    for b in doc["blocks"]:
        if b.get("hidden"):
            k = "đã ẩn"
        elif b.get("type") == "reference":
            k = "tài liệu tham khảo"
        elif not b.get("translate"):
            k = "không dịch (công thức, hình, nhiễu)"
        else:
            k = "sẽ dịch"
        vai[k] = vai.get(k, 0) + 1

    bo_roi, pdf_chars = chu_bi_bo_roi(doc)
    return {
        "blocks_total": len(doc["blocks"]),
        "blocks_to_translate": len(todo),
        "blocks_by_role": vai,
        "uncovered_chars": bo_roi,
        "pdf_chars": pdf_chars,
        "figures": sum(1 for b in doc["blocks"] if b.get("figure")),
        "source_chars": src_chars,
        "chunks": n_chunks,
        "prompt_tokens": round(prompt_tok),
        "output_tokens": round(out_tok + brief_out),
        "cost_usd": round(cost, 4) if cost is not None else None,
        "cost_low": round(lo, 4) if lo is not None else None,
        "cost_high": round(hi, 4) if hi is not None else None,
        # Ước tính này CHỈ tính lượt dịch. Brief, giải thích từng đoạn khi bấm 💡,
        # bôi vàng hỏi, dựng slide đều tính riêng — nói rõ để người dùng không
        # tưởng đây là tổng hoá đơn của cả bài.
        "covers": "lượt dịch" + (" + cột giải thích" if mode in ("both", "plain") else ""),
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
        by_id = {b["id"]: b["text"] for b in group}
        got = _parse_labeled(raw, list(by_id))
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


class HetGio(Exception):
    """Lượt gọi model quá trần thời gian của chính nó (khác lỗi mạng/lỗi model)."""


def tran_brief(n_chars: int) -> float:
    """Trần thời gian cho MỘT lượt dựng tóm lược, co theo độ dài bài.

    #2: bài dán 9 khối treo ở "Đang đọc toàn bài…" hơn 5 phút — provider nhận
    request rồi im, và trần duy nhất là 300 giây chung của `llm`. Bài ngắn thì
    tóm lược xong trong vài chục giây; chờ tới 300 giây là bắt người dùng ngồi
    nhìn một thứ đã hỏng. Bài dài (~100k ký tự) thật sự cần vài phút nên trần
    phải co giãn, không đặt một số cứng.
    """
    return min(240.0, 75.0 + n_chars / 1000)


async def run_brief(doc_id: str) -> tuple[dict, dict, dict]:
    """Trả về (brief, chi phí lượt này, chi phí cộng dồn của bài).

    Quá trần (`tran_brief`) thì gọi lại MỘT lần — lượt treo thường là do một
    endpoint của provider, lượt sau đi đường khác. Hỏng cả hai thì ném `HetGio`
    để giao diện nói rõ "quá giờ" và mời thử lại, thay vì treo vô hạn.
    """
    import asyncio

    doc = store.load(doc_id)
    text = full_source_text(doc["blocks"], limit=300_000)
    tran = tran_brief(len(text))
    msgs = [
        {"role": "system", "content": prompts.BRIEF_SYSTEM},
        {"role": "user", "content": prompts.brief_user(doc.get("title", ""), text)},
    ]
    for lan in range(2):
        try:
            raw, usage = await asyncio.wait_for(llm.complete(
                msgs, model=doc["model"], session_id=doc_id, max_tokens=20000,
                temperature=0.3, reasoning=LOW_REASONING), timeout=tran)
            break
        except asyncio.TimeoutError:
            if lan == 1:
                raise HetGio(f"model không trả lời sau {int(tran)} giây, đã thử hai lần")
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

    # Tập mã hợp lệ của mẻ này — dò nhãn theo đúng nó, xem `_label_re`.
    want_ids = [it["id"] for it in items] + [it["id"] + "_g" for it in items]
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
        parsed = _parse_labeled(buf, want_ids)
        labels = list(parsed.keys())
        for key in labels[:-1]:
            if key in emitted:
                continue
            emitted.add(key)
            if key.endswith("_g"):
                yield "block", json.dumps({"id": key[:-2], "plain": strip_md(parsed[key])})
            else:
                yield "block", json.dumps({"id": key, "vi": parsed[key]})

    parsed = _parse_labeled(buf, want_ids)
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
            for key, val in _parse_labeled(raw, want_ids).items():
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
        fixed = _parse_labeled(raw, want_ids)
        for bid, vi in fixed.items():
            if vi and vi != final.get(bid):
                final[bid] = vi
                yield "block", json.dumps({"id": bid, "vi": vi, "refined": True})
        u = llm.Usage(**json.loads(usage_json))
        u.add(ru)
        usage_json = json.dumps(u.dict())

    # ghi vào bộ nhớ dịch để lần sau — và bài sau — không phải dịch lại
    by_id = {it["id"]: it["text"] for it in items}
    # Soát rò hệ chữ TRƯỚC khi ghi vào bộ nhớ dịch. Đây là chỗ duy nhất phải
    # chặn cho bằng được: bản dịch rác nằm trong `doc` thì người đọc thấy và sửa
    # tay được, nhưng nằm trong `tm` thì nó **quay lại mãi mãi** — mọi bài sau
    # có đoạn y hệt đều nhận lại đúng cái rác đó, miễn phí và im lặng.
    #
    # Đã gặp thật: một đoạn dịch ra `띠ᥕᥕᥲᥕᥱ` thay cho chữ "bảo toàn".
    # Nhãn của khối KHÁC còn nằm trong nội dung nghĩa là bước tách nhãn đã hỏng
    # và mấy khối bị dồn vào một ô. Đã gặp: 20 nghìn ký tự của mười mấy khối dồn
    # vào `b7` vì model gõ `### b9_g` thay cho `<<<b9_g>>>`.
    leak_rx = _label_re(want_ids)
    dirty: dict[str, str] = {}
    for bid in set(final) | set(plains):
        if bid not in by_id:
            continue
        body = f"{final.get(bid, '')} {plains.get(bid, '')}"
        bad = script_leak(body, by_id[bid])
        if bad:
            dirty[bid] = "".join(sorted(bad))[:12]
        elif leak_rx and leak_rx.search(body):
            dirty[bid] = "nhãn lọt vào chữ"

    db.tm_put([(by_id[bid], final.get(bid, ""), plains.get(bid, ""))
               for bid in set(final) | set(plains)
               if bid in by_id and bid not in dirty], doc["model"])
    if dirty:
        nhan = [b for b, ly in dirty.items() if ly == "nhãn lọt vào chữ"]
        chu = {ly for ly in dirty.values() if ly != "nhãn lọt vào chữ"}
        vi_sao = []
        if chu:
            vi_sao.append("lẫn ký tự thuộc hệ chữ lạ (" + ", ".join(sorted(chu)[:3]) + ")")
        if nhan:
            vi_sao.append("còn sót nhãn khối, nghĩa là mấy khối bị dồn vào một ô")
        yield "warn", json.dumps({
            "kind": "rò_hệ_chữ",
            "blocks": list(dirty),
            "msg": ("Bản dịch của " + ", ".join(sorted(dirty)[:6]) + " "
                    + " và ".join(vi_sao)
                    + " — model trả về rác. Chưa ghi vào bộ nhớ dịch; sửa tay bằng nút ✎ "
                      "hoặc dịch lại khối đó."),
        })

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


async def retranslate_block(doc_id: str, block_id: str, mode: str = "vi") -> dict:
    """Dịch lại ĐÚNG MỘT khối, bỏ qua bộ nhớ dịch.

    `script_leak()` chặn rác **không cho vào `tm`** nhưng cố ý để nó nằm trong
    `doc`, vì người đọc thấy thì sửa được — và chính cảnh báo của `stream_chunk`
    bảo *"sửa tay bằng nút ✎ hoặc dịch lại khối đó"*. Nhưng trước bản này không
    có đường dịch lại một khối: hoặc gõ tay cả đoạn, hoặc dịch lại cả mẻ. Đã gặp
    thật trên bài CIRAG — một đoạn ra `либо thiếu thông tin để suy luận, либо
    nhận quá nhiều nhiễu`, chữ Cyrillic thay cho "hoặc".

    Hai chỗ bắt buộc:

    - **Bỏ mục `tm` của đoạn này TRƯỚC khi gọi model.** Không bỏ thì lượt dịch
      lại lấy ngay bản cũ và trả về đúng cái rác người dùng vừa bấm để thay.
    - **Nhiệt độ cao hơn lượt đầu.** Dịch lại ở đúng nhiệt độ cũ với đúng prompt
      cũ thì hay ra đúng kết quả cũ, tức người dùng trả tiền cho một lượt không
      đổi gì. Lượt hai (khi lượt một vẫn rò) nhích thêm lần nữa.

    Trả về `{vi, plain, warn, run, usage}`. Không chặn: nếu cả hai lượt vẫn rò
    hệ chữ thì vẫn trả bản mới nhất kèm `warn`, còn `tm` thì để trống — cùng
    triết lý "cảnh báo chứ không chặn" của `check_slides` / `check_answer`, vì
    người đọc có nút ✎ để tự sửa nốt.
    """
    doc = store.load(doc_id)
    blk = next((b for b in doc["blocks"] if b["id"] == block_id), None)
    if blk is None:
        raise KeyError(block_id)
    src = blk.get("text") or ""
    if not src.strip():
        raise ValueError("khối này không có chữ để dịch")

    # Bỏ bản cũ trong bộ nhớ dịch, nếu không lượt sau lại nhận đúng nó.
    db.tm_drop([src], doc["model"])

    item = {"id": block_id, "text": src, "type": blk.get("type", "para")}
    want_ids = [block_id, block_id + "_g"]
    prefix = cached_prefix(doc)
    if mode == "plain":
        task = prompts.PLAIN_ONLY_TASK
    else:
        task = prompts.TRANSLATE_TASK + (prompts.PLAIN_TASK if mode == "both" else "")
    sysmsg = llm.system_message(prefix, task, model=doc["model"])

    tong = llm.Usage()
    vi = pl = ""
    bad: set[str] = set()
    for lan in range(2):
        raw, ru = await llm.complete(
            [sysmsg, {"role": "user", "content": prompts.translate_user([item])}],
            model=doc["model"],
            session_id=doc_id,
            max_tokens=4000,
            temperature=0.4 + 0.2 * lan,
            reasoning=NO_REASONING,
        )
        tong.add(ru)
        parsed = _parse_labeled(raw, want_ids)
        # Model dịch một khối lẻ hay bỏ luôn nhãn — chỉ có một khối nên không
        # lẫn vào đâu được, lấy cả phần thân làm bản dịch.
        vi = parsed.get(block_id) or ("" if parsed else raw.strip())
        pl = strip_md(parsed.get(block_id + "_g", ""))
        bad = script_leak(f"{vi} {pl}", src)
        if not bad:
            break

    doc = store.load(doc_id)
    if vi:
        doc["translations"][block_id] = vi
    if pl:
        doc.setdefault("plain", {})[block_id] = pl
    # Vệt bôi neo theo khoảng ký tự trong bản dịch cũ — bản mới dài khác thì
    # khoảng đó trỏ vào chỗ khác. Cùng lý do với `_forget()`.
    (doc.get("highlights") or {}).pop(block_id, None)
    store.save(doc)

    if not bad and (vi or pl):
        db.tm_put([(src, vi, pl)], doc["model"])

    return {
        "vi": vi,
        "plain": pl,
        "warn": ("Bản dịch mới vẫn lẫn ký tự thuộc hệ chữ lạ ("
                 + "".join(sorted(bad))[:12] + ") — chưa ghi vào bộ nhớ dịch. "
                 "Sửa tay bằng nút ✎, hoặc đổi model rồi dịch lại." if bad else ""),
        "run": tong.dict(),
        "usage": _bump_usage(doc_id, json.dumps(tong.dict())),
    }


# Một phần tử `marks` trọn vẹn trong JSON bị cắt cụt. Không dùng regex để phân
# tích JSON nói chung — chỉ để **vớt** những object đã đóng ngoặc, khi phương án
# còn lại là vứt cả lượt gọi.
_MARK_OBJ = re.compile(r"\{[^{}]*\}")


# Thay thế kiểu chữ mà model hay tự làm khi "chép nguyên văn": nháy cong thành
# nháy thẳng, gạch ngang dài thành gạch nối, ba chấm rời. Đây là những phép đổi
# **không đụng tới chữ nào**, nên tha chúng vẫn giữ nguyên ý nghĩa của chốt chặn.
_TYPO_MAP = str.maketrans({
    "\u2018": "'", "\u2019": "'", "\u201c": '"', "\u201d": '"',
    "\u2013": "-", "\u2014": "-", "\u2212": "-", "\u00a0": " ",
})


def _chuan(t: str) -> tuple[str, list[int]]:
    """Chuẩn hoá nhẹ một chuỗi, kèm bản đồ về vị trí GỐC của từng ký tự.

    Gộp mọi dải khoảng trắng thành một dấu cách và quy các biến thể kiểu chữ về
    dạng thẳng. Bản đồ để sau khi tìm thấy trên chuỗi đã chuẩn hoá thì cắt lại
    được **đúng chuỗi gốc** — client neo vệt bôi bằng cách chạy `sci()` lên
    `quote` rồi dò trong ô, nên `quote` phải là chuỗi con thật của bản dịch.
    """
    out: list[str] = []
    idx: list[int] = []
    truoc_trang = False
    for i, ch in enumerate(t.translate(_TYPO_MAP)):
        if ch.isspace():
            if truoc_trang or not out:
                continue
            out.append(" ")
            idx.append(i)
            truoc_trang = True
        else:
            out.append(ch)
            idx.append(i)
            truoc_trang = False
    return "".join(out), idx


def tim_nguyen_van(quote: str, hay: str) -> str:
    """Tìm `quote` trong `hay`, trả về **chuỗi con gốc** hoặc rỗng nếu không có.

    Khớp đúng từng ký tự trước; không được thì thử trên bản đã chuẩn hoá nhẹ.
    Đo trên bài arXiv:2602.15922 ở mức vừa: 7 trên 39 câu trích bị chốt chặn loại
    vì lệch kiểu chữ chứ không lệch chữ.
    """
    if quote in hay:
        return quote
    nq, _ = _chuan(quote)
    nh, idx = _chuan(hay)
    if not nq:
        return ""
    i = nh.find(nq)
    if i < 0:
        return ""
    return hay[idx[i]:idx[i + len(nq) - 1] + 1]


# Kết câu: dấu chấm/hỏi/than theo sau bởi khoảng trắng, hoặc hết chuỗi.
_HET_CAU = re.compile(r"[.!?…]['\"\u2019\u201d)\]]*(?=\s|$)")


def _do_khoi(quote: str, by_id: dict[str, str]) -> str:
    """Câu trích này nằm ở khối nào — trả mã, hoặc rỗng nếu không ở đâu / ở nhiều chỗ.

    Nằm ở nhiều khối thì **bỏ**, không đoán: chọn bừa một khối là gắn vệt bôi vào
    chỗ người đọc không hề định đánh dấu, mà nhìn thì vẫn có vẻ đúng.
    """
    thay = [b for b, t in by_id.items() if tim_nguyen_van(quote, t)]
    return thay[0] if len(thay) == 1 else ""


def noi_het_cau(bat_dau: str, hay: str) -> str:
    """Từ mấy từ đầu câu, nối tới hết câu ấy trong `hay`.

    Model **chỉ được yêu cầu chép mấy từ đầu** (xem `INSIGHT_TASK`): chép nguyên
    một câu dài từ giữa khối 47 nghìn ký tự thì lệch một chữ là chuyện thường, và
    lệch là vệt bôi bị bỏ. Đo trên arXiv:2602.15922 ở mức vừa, hai lượt liên
    tiếp: 7 rồi 11 trên ~38 câu bị loại vì chép lệch — dao động lớn, tức đây là
    giới hạn của việc chép dài chứ không phải một lỗi lẻ.

    Nối ở server chứ không ở client, để `quote` trả về vẫn là **chuỗi con thật**
    của bản dịch — client neo bằng cách dò chuỗi đó trong ô đã dựng.
    """
    i = hay.find(bat_dau)
    if i < 0:
        return ""
    m = _HET_CAU.search(hay, i + len(bat_dau))
    return hay[i:m.end()] if m else hay[i:]


def _vot_marks(raw: str) -> list[dict]:
    """Bóc những mục `marks` đã trọn vẹn ra khỏi một JSON bị cắt cụt."""
    out: list[dict] = []
    for m in _MARK_OBJ.finditer(raw):
        try:
            o = json.loads(m.group(0), strict=False)
        except json.JSONDecodeError:
            try:
                o = json.loads(llm._va_escape(m.group(0)), strict=False)
            except json.JSONDecodeError:
                continue
        if isinstance(o, dict) and o.get("quote") and o.get("block"):
            out.append(o)
    return out


def _insight_context(doc: dict) -> str:
    """Tóm lược + bảng thuật ngữ, để pass đánh dấu biết bài này tranh luận điều gì.

    Rút từ `doc["brief"]` chứ không gọi lại model. Không có brief thì trả rỗng —
    pass vẫn chạy được, chỉ là model phải tự suy mạch bài từ bản dịch.
    """
    brief = doc.get("brief") or {}
    if not brief:
        return ""
    dong = [
        "## Bối cảnh bài báo",
        f"- Chốt lại: {brief.get('one_line', '')}",
        f"- Bài toán: {brief.get('problem', '')}",
        f"- Ý tưởng: {brief.get('idea', '')}",
        f"- Bằng chứng: {brief.get('evidence', '')}",
    ]
    chain = brief.get("argument_chain") or []
    if chain:
        dong.append("### Mạch lập luận")
        dong += [f"  {i + 1}. [{s.get('role', '')}] {s.get('step', '')}"
                 for i, s in enumerate(chain)]
    return "\n".join(dong) + "\n\n"


async def mark_insights(doc_id: str, level: str = "") -> dict:
    """Chọn ra những câu đáng nhớ trong bản dịch, kèm lý do. Một lượt gọi model.

    Đi sau `cached_prefix(doc)` nên toàn văn bài gốc gần như miễn phí — model cần
    nó để biết câu nào **quan trọng trong mạch bài**, chứ đọc mỗi bản dịch rời thì
    câu nào cũng na ná nhau. Phần thay đổi theo request là bản dịch, nằm ở message
    `user` (đo trên 6 bài thật: 10–21k token).

    **Không tự ghi vệt bôi.** Trả về danh sách ứng viên; tầng hiển thị mới neo
    được, vì vệt bôi neo theo khoảng ký tự trong **văn bản đã dựng** của một ô,
    mà `sci()` biến `^{N}` thành `<sup>N</sup>` — đo trên một câu thật: 71 ký tự
    lưu so với 54 ký tự hiển thị. Server tính `start`/`end` là lệch, và muốn tính
    đúng thì phải chép `sci()` sang Python — đúng cái bẫy "hai bản dựng cùng một
    thứ" đã ghi cho `renderMd`/`svMd` và cho ba bộ dựng slide.

    Chốt chặn của pass này: **`quote` phải có mặt nguyên văn trong bản dịch của
    đúng khối đó**. Model bịa một câu nghe hay nhưng bài không nói thì bị bỏ —
    cùng họ với ràng buộc số liệu của `check_slides` và `check_answer`.
    """
    doc = store.load(doc_id)
    tr = doc.get("translations") or {}
    items = [{"id": b["id"], "vi": tr[b["id"]]}
             for b in doc["blocks"]
             if b.get("type") in ("para", "caption") and (tr.get(b["id"]) or "").strip()]
    if not items:
        raise ValueError("bài này chưa dịch đoạn nào")

    # **Cố ý KHÔNG dùng `cached_prefix`.** Nó chứa toàn văn bài GỐC, mà phần
    # `user` ở đây đã là bản DỊCH của đúng bài ấy — gửi cả hai là gửi cùng một
    # bài hai lần. Đo trên CIRAG: prefix 23.722 token + bản dịch 15.755 token,
    # và `cached_tokens = 0` vì người dùng bấm nút này rất lâu sau lần dịch, lúc
    # prefix đã rơi khỏi cửa sổ cache — đúng cái bẫy đã ghi cho `explain_block`.
    #
    # Giữ lại tóm lược và bảng thuật ngữ (vài trăm token) vì chúng cho model biết
    # bài này rốt cuộc tranh luận điều gì, tức đúng thứ cần để chọn câu nào đáng
    # nhớ. Bỏ toàn văn gốc: bản dịch đã mang trọn nội dung đó.
    budget = prompts.insight_budget(len(items), level or prompts.INSIGHT_DEFAULT)
    msgs = [
        llm.system_message("", _insight_context(doc) + prompts.insight_task(budget),
                           model=doc["model"]),
        {"role": "user", "content": prompts.insight_user(items)},
    ]
    raw, usage = await llm.complete(
        msgs, model=doc["model"], session_id=doc_id,
        # Tắt hẳn nghĩ thầm, cùng lý do với `explain_block`: độ sâu ở đây đến từ
        # bảng hạn mức và danh sách "không đánh dấu" trong `INSIGHT_TASK`, không
        # đến từ token nghĩ thầm — mà nghĩ thầm thì tranh chỗ với phần cần viết.
        # Trần đầu ra co theo ngân sách. **Trần là mức CHẶN, không phải mức tính
        # tiền** — chỉ trả cho token thật sự sinh ra — nên rộng tay ở đây không
        # tốn gì, mà chật tay thì mất trắng cả lượt gọi. Ước 130 token/vệt đã
        # hụt thật: bài 162 đoạn ở mức vừa (40 vệt) cắt cụt JSON sau 94 giây,
        # vì câu tiếng Việt dài hơn ước tính. Đẩy lên 220.
        max_tokens=min(24000, 3000 + budget["total"] * 220),
        temperature=0.2, reasoning=NO_REASONING)

    try:
        data = llm.extract_json(raw)
    except ValueError:
        # Đầu ra bị cắt cụt thì **vớt lại những mục đã trọn vẹn** thay vì bỏ cả
        # lượt gọi đã trả tiền. Pass này trả về một DANH SÁCH, nên mất phần đuôi
        # chỉ là ít vệt hơn — khác hẳn pass trả về một object phải nguyên vẹn.
        data = {"marks": _vot_marks(raw)}
        if not data["marks"]:
            raise
    if not isinstance(data, dict):
        raise ValueError("model không trả về JSON hợp lệ")

    by_id = {it["id"]: it["vi"] for it in items}
    dem: dict[str, int] = {}
    da_lay: dict[str, list[tuple[int, int]]] = {}
    marks: list[dict] = []
    bo: list[str] = []
    for m in (data.get("marks") or []):
        bid = str(m.get("block") or "")
        kind = str(m.get("kind") or "")
        quote = (m.get("quote") or "").strip()
        if kind not in prompts.INSIGHT_KINDS or not quote:
            bo.append(f"{bid or '?'}: loại không hợp lệ")
            continue
        # **Mã khối do model khai chỉ là gợi ý, câu trích mới là thứ phải thật.**
        # Đo trên arXiv:2602.15922: trong 10 câu bị loại, 4 câu khai mã KHÔNG TỒN
        # TẠI trong bài (`b131`, `b123`, `b132`) và 2 câu khai nhầm khối — trong
        # khi chính câu ấy có thật ở khối khác. Cùng kiểu hỏng đã ghi cho kho
        # survey ("model viết ra mã 12 ký tự không tồn tại").
        #
        # Ta tự tra lại được, nên bỏ đi là vứt thứ đã trả tiền mà không được gì:
        # chốt chặn cần câu là **nguyên văn trong bài**, còn nó nằm ở khối nào thì
        # server biết rõ hơn model.
        if bid not in by_id:
            bid = _do_khoi(quote, by_id) or bid
            if bid not in by_id:
                bo.append(f"{bid or '?'}: mã khối không có trong bài")
                continue
        # chốt chặn: phải là chuỗi có thật trong bản dịch của đúng khối đó
        goc = quote
        quote = tim_nguyen_van(quote, by_id[bid])
        if not quote:                       # khai đúng mã có thật, nhưng nhầm khối
            khac = _do_khoi(goc, by_id)
            if khac:
                bid, quote = khac, tim_nguyen_van(goc, by_id[khac])
        if not quote:
            # Kèm luôn đoạn model gõ ra: "không khớp" mà không nói khớp hụt ở đâu
            # thì lần sau lại phải chạy thêm một lượt tốn tiền chỉ để biết.
            bo.append(f"{bid}: không khớp bản dịch — model gõ {goc[:80]!r}")
            continue
        quote = noi_het_cau(quote, by_id[bid]) or quote

        # Trần cho từng KHỐI — xem `prompts.INSIGHT_PER_BLOCK`. Ngân sách tổng
        # không chặn được chuyện dồn hết vào một đoạn: đo trên bài này, khối tóm
        # tắt từng nhận 4 vệt phủ 93% khối.
        da = da_lay.get(bid, [])
        if len(da) >= prompts.INSIGHT_PER_BLOCK:
            bo.append(f"{bid}: khối đã đủ {prompts.INSIGHT_PER_BLOCK} vệt")
            continue
        # Chỉ áp từ vệt THỨ HAI trở đi. Trần này sinh ra để chặn **dồn đống**,
        # không phải để từ chối một câu dài: khối 400 ký tự có một câu 250 ký tự
        # thì tự nó đã 62%. Áp cho cả vệt đầu thì bỏ mất 15 trên 40 câu — đo thật.
        phu = sum(e - b for b, e in da) + len(quote)
        if da and phu > len(by_id[bid]) * prompts.INSIGHT_BLOCK_FRAC:
            bo.append(f"{bid}: thêm vệt này là tô quá {int(prompts.INSIGHT_BLOCK_FRAC*100)}% khối")
            continue
        # hạn mức theo loại, co theo độ dài bài — xem `prompts.insight_budget`
        if dem.get(kind, 0) >= budget["per_kind"][kind]:
            continue
        # Hai câu trích CHỒNG nhau trong cùng một khối thì `wrapRange` lồng thẻ
        # `<mark>` vào nhau và vệt bôi hiện ra sai. Ở mức "vừa" trên bài 149 đoạn
        # đã có 5 khối mang từ hai vệt trở lên, nên đây là chuyện sẽ tới.
        #
        # So trên chuỗi THÔ chỉ để phát hiện chồng lấn — không dùng làm toạ độ
        # vệt bôi, vì toạ độ phải tính trên chữ đã dựng (xem docstring).
        d0 = by_id[bid].find(quote)
        d1 = d0 + len(quote)
        if any(d0 < e and d1 > b for b, e in da):
            bo.append(f"{bid}: câu trích chồng lên một vệt đã chọn")
            continue
        da_lay.setdefault(bid, []).append((d0, d1))
        dem[kind] = dem.get(kind, 0) + 1
        color, nhan = prompts.INSIGHT_KINDS[kind]
        marks.append({"block": bid, "kind": kind, "label": nhan, "color": color,
                      "quote": quote, "why": (m.get("why") or "").strip()[:400]})
        if len(marks) >= budget["total"]:
            break

    return {
        "marks": marks,
        "skipped": bo,
        "budget": budget["total"],
        "run": usage.dict(),
        "usage": _bump_usage(doc_id, json.dumps(usage.dict())),
    }


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
        # Đo thật một lượt: **145,7 giây** cho 1.383 token đầu ra. Phần lớn thời
        # gian đi vào nghĩ thầm, không vào phần viết — cùng cái bẫy đã ghi ở
        # `survey/lecture.py`, nơi `{"effort":"low"}` làm hai mẻ trên bốn chạy
        # 76 giây rồi trả về chuỗi RỖNG.
        #
        # Độ sâu của ghi chú đến từ CẤU TRÚC bắt buộc trong `EXPLAIN_SYSTEM`
        # (`gist` / `role` / `link_back` / `unpack` / `analogy` / `caution` /
        # `check`) chứ không đến từ token nghĩ thầm — mỗi trường đã hỏi đúng một
        # câu cụ thể, model không cần tự bày ra dàn ý nữa.
        #
        # `max_tokens` hạ từ 8000: đầu ra đo được là 1.383 token, để trần gấp
        # sáu lần chỉ mời model viết dài và nghĩ lâu.
        max_tokens=2500,
        temperature=0.4,
        reasoning=NO_REASONING,
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


def _norm_num(s: str) -> str:
    """`1,5` và `1.5` là một số. Bỏ dấu phân cách để so cho khớp."""
    return s.replace(",", ".").rstrip("0").rstrip(".") if "." in s or "," in s else s


_SO_CACHE: dict[tuple, set[str]] = {}


_CHU_HINH: dict[tuple, str] = {}


def chu_vung_hinh(doc: dict, bid: str) -> str:
    """Chữ nằm TRONG vùng một hình/bảng của PDF gốc, giữ theo từng dòng.

    Bảng được cắt thành ẢNH nên model chỉ thấy chú thích, rồi tự suy kết luận —
    subagent chỉ đọc slide bắt được "cải thiện ở mọi mức", "độ trễ thấp" trái với
    chính bảng. Nhưng PDF vẫn giữ lớp chữ của bảng; bóc nó ra là model đọc được
    số. Nhớ tạm ở mức module (mở PDF mỗi lần là chậm).
    """
    b = next((x for x in doc.get("blocks") or [] if x["id"] == bid), None)
    if not b or not b.get("figure_rect") or b.get("figure_page") is None:
        return ""
    key = (doc["id"], bid, tuple(round(v) for v in b["figure_rect"]))
    if key not in _CHU_HINH:
        chu = ""
        p = store.pdf_path(doc["id"])
        if p is not None:
            try:
                import fitz
                with fitz.open(p) as pdf:
                    tu = pdf[b["figure_page"]].get_text("words", clip=fitz.Rect(*b["figure_rect"]))
                dong: dict[int, list] = {}
                for w in tu:
                    dong.setdefault(round(w[3] / 3), []).append(w)
                chu = "\n".join(" ".join(w[4] for w in sorted(ws, key=lambda w: w[0]))
                                 for _, ws in sorted(dong.items()))
            except Exception:  # noqa: BLE001 — PDF hỏng/thiếu thì coi như không có chữ
                chu = ""
        if len(_CHU_HINH) > 2000:
            _CHU_HINH.clear()
        _CHU_HINH[key] = chu
    return _CHU_HINH[key]


def _so_toan_bai(doc: dict) -> set[str]:
    """Mọi con số có trong bài (bản gốc + bản dịch), đã chuẩn hoá.

    Nhớ tạm ở mức module, KHÔNG ghi vào `doc`: `doc` được trả về thành JSON, mà
    `set` thì không chuyển sang JSON được."""
    key = (doc.get("id"), len(doc.get("blocks") or []), len(doc.get("translations") or {}))
    if key not in _SO_CACHE:
        tr = doc.get("translations") or {}
        pool = " ".join((b.get("text") or "") + " " + (tr.get(b["id"]) or "")
                        for b in doc.get("blocks") or [])
        # Số trong BẢNG/HÌNH cũng là số của bài — model giờ được đọc chữ trong bảng,
        # không tính chúng thì mọi số đọc đúng từ bảng đều bị coi là bịa.
        pool += " " + " ".join(chu_vung_hinh(doc, b["id"]) for b in doc.get("blocks") or []
                               if b.get("figure") and b.get("type") != "equation")
        if len(_SO_CACHE) > 32:
            _SO_CACHE.clear()
        _SO_CACHE[key] = {_norm_num(m) for m in _NUM.findall(pool)}
    return _SO_CACHE[key]


def so_bia(doc: dict, src_ids: list[str], shown: str) -> list[str]:
    """Số trên slide/dàn ý KHÔNG có ở đâu trong bài — dấu hiệu số bịa.

    S21: bản đầu chỉ dò trong các khối nguồn được khai, nên báo "900 không có
    trong khối nguồn" trong khi bài ghi 900 ở ba chỗ khác. Cảnh báo kêu oan vài
    lần là người dùng thôi đọc, và lúc đó cảnh báo thật cũng trôi theo. Giờ dò
    trong khối nguồn trước, không thấy thì dò cả bài; chỉ báo khi không có ở đâu.
    """
    text_of = {b["id"]: b.get("text") or "" for b in doc.get("blocks") or []}
    tr = doc.get("translations") or {}
    pool = " ".join(text_of.get(i, "") + " " + (tr.get(i) or "") for i in src_ids)
    nums = {_norm_num(m) for m in _NUM.findall(pool)}
    shown = _URLISH.sub(" ", shown)
    out = sorted({m for m in _NUM.findall(shown) if _norm_num(m) not in nums})
    # số thứ tự và phần trăm tròn trĩnh thì bỏ qua, ồn hơn là hữu ích
    out = [n for n in out if not (n.isdigit() and int(n) <= 12)]
    if out:
        ca_bai = _so_toan_bai(doc)
        out = [n for n in out if _norm_num(n) not in ca_bai]
    return out


def nguon_gon(src: str) -> str:
    """Nguồn bài rút gọn cho dòng trích dẫn trên slide — bản server của
    `nguonGon()` bên `app.js`, phải khớp từng chữ. `arXiv:1706.03762` thay cho
    cả URL; URL khác thì bỏ giao thức, `www.` và đuôi `.pdf` (S6)."""
    src = (src or "").strip()
    m = re.search(r"(?:arxiv\.org/(?:abs|pdf)/|arxiv:\s*)(\d{4}\.\d{4,5})", src, re.I)
    if m:
        return "arXiv:" + m.group(1)
    src = re.sub(r"^https?://", "", src, flags=re.I)
    src = re.sub(r"^www\.", "", src, flags=re.I)
    return re.sub(r"\.pdf$", "", src, flags=re.I).rstrip("/")


def _viet_hoa_dau(t: str) -> str:
    """Viết hoa chữ đầu câu — nhưng chỉ khi từ đầu viết thường TOÀN BỘ.

    S5: gạch đầu dòng mở bằng "multi-hop question answering cần…", "open IE dựa
    trên…". Nhưng `iRAG`, `mHC`, `kNN` là tên riêng có chữ hoa bên trong — viết
    hoa chữ đầu là đổi tên người ta, nên từ nào có chữ hoa ở giữa thì để nguyên.
    """
    if not t:
        return t
    m = re.match(r"(\s*)(\S+)", t)
    if not m:
        return t
    tu = m.group(2)
    if not tu[0].islower() or any(ch.isupper() for ch in tu[1:]):
        return t
    # Chỉ viết hoa một TỪ chữ Latinh. Ký hiệu thì để nguyên: "τ=0.1" viết hoa ra
    # "Τ=0.1" (tau hoa — một ký hiệu khác), "x_{t}" ra "X_{t}" (biến khác). Đã gặp
    # thật trên slide bài World Models.
    loi = tu.rstrip(".,;:!?)\"'”’")
    if not re.fullmatch(r"[^\W\d_]+(?:-[^\W\d_]+)*", loi) or not all(
            "LATIN" in unicodedata.name(ch, "") for ch in loi if ch != "-"):
        return t
    return m.group(1) + tu[0].upper() + tu[1:] + t[m.end():]


# Dấu hiệu một slide đang ĐI HẾT CƠ CHẾ chứ chỉ nhắc tên nó: có bước, có nhân
# quả, có đầu vào cụ thể. Đòi nhiều dấu hiệu cùng lúc chứ không đòi một từ khoá,
# vì một từ khoá thì model học được cách rắc vào cho qua.
_STEPY = ("bước", "trước hết", "sau đó", "cuối cùng", "lần lượt", "mỗi vòng",
          "đầu vào", "đầu ra", "nhận", "trả về", "→")


def _gom_chu(x) -> str:
    """Mọi chuỗi trong một cấu trúc lồng (dict/list) — không phụ thuộc hình dạng
    slide, để dùng được cho cả bộ slide mới lẫn dữ liệu cũ."""
    if isinstance(x, str):
        return x
    if isinstance(x, dict):
        return " ".join(_gom_chu(v) for v in x.values())
    if isinstance(x, (list, tuple)):
        return " ".join(_gom_chu(v) for v in x)
    return ""


def _walks_mechanism(sl: dict) -> bool:
    """Slide này có đi hết một cơ chế không.

    Dùng `depth.STRICT_CAUSAL` chứ KHÔNG dùng `depth.CAUSAL`: bộ rộng chứa
    `khi`, `nếu`, `nên`, `trong khi` — hư từ có mặt trong gần như mọi câu tiếng
    Việt. Đo trên một bộ slide thật, đếm bằng bộ rộng cho kết quả **ngược**: hai
    slide mô tả cơ chế thì trượt, còn slide ablation không có cơ chế nào lại đạt.
    """
    t = _gom_chu(sl).lower()
    steps = sum(1 for k in _STEPY if k in t)
    causal = sum(1 for k in depth.STRICT_CAUSAL if k in t)
    return steps >= 2 and causal >= 2




def reparse_merge(doc: dict, new_blocks: list[dict]) -> dict:
    """Thay danh sách khối bằng bản bóc mới, **giữ nguyên mọi thứ đã trả tiền**.

    Bóc lại là cần thiết mỗi khi `parser.py` khá lên (bản vá nhặt lại chữ mô hình
    bố cục bỏ sót là ví dụ). Nhưng nạp lại bài từ đầu thì mất sạch bản dịch, ghi
    chú, vệt bôi vàng và bộ slide — cái giá đó lớn hơn phần chữ thu về, nên người
    dùng sẽ không bóc lại, và bản vá thành vô dụng với bài họ đang đọc.

    Cách giữ: **ghép theo NỘI DUNG, không theo vị trí.** Khối mới nào có văn bản
    trùng một khối cũ thì lấy lại đúng mã cũ, nên mọi thứ trỏ theo mã (bản dịch,
    diễn giải, ghi chú, vệt bôi, `source_block_ids` của slide) vẫn trỏ đúng chỗ.
    Ghép theo vị trí thì sai ngay: bản bóc mới chèn thêm khối, mọi chỉ số phía
    sau lệch đi một, và bản dịch dán vào nhầm đoạn — tệ hơn hẳn mất bản dịch, vì
    nhìn vẫn có vẻ đúng.

    Khối mới không khớp gì thì nhận mã mới và **chưa có bản dịch** — người dùng
    bấm dịch tiếp, và chỉ trả tiền cho đúng phần đó.

    Khối cũ biến mất khỏi bản bóc mới thì bản dịch của nó cũng bỏ theo. Không mất
    gì thật: `tm` khoá theo nội dung đoạn, nên nếu đoạn ấy quay lại ở lần bóc
    sau, bản dịch lấy lại miễn phí.
    """
    # Nội dung → DANH SÁCH mã cũ theo đúng thứ tự xuất hiện. Dùng dict một-một
    # thì khối thứ hai có cùng nội dung (công thức lặp, dòng ngắn giống nhau)
    # không khớp được với gì, phải mint mã mới — và mint lại **mỗi lần** bóc
    # lại, nên mã cứ phình ra dù nội dung y hệt. Khớp theo lần xuất hiện thì
    # bóc lại hai lần liên tiếp cho kết quả giống hệt nhau.
    old_by_text: dict[str, list[str]] = {}
    for b in doc.get("blocks") or []:
        key = db.norm(b.get("text") or "")
        if key:
            old_by_text.setdefault(key, []).append(b["id"])

    used: set[str] = set()
    n_max = 0
    for b in doc.get("blocks") or []:
        m = re.fullmatch(r"b(\d+)", b["id"])
        if m:
            n_max = max(n_max, int(m.group(1)))

    out: list[dict] = []
    kept = fresh = 0
    for nb in new_blocks:
        nb = dict(nb)
        key = db.norm(nb.get("text") or "")
        queue = old_by_text.get(key) or []
        old = ""
        while queue and not old:
            cand = queue.pop(0)
            if cand not in used:
                old = cand
        if old:
            nb["id"] = old
            used.add(old)
            kept += 1
        else:
            n_max += 1
            nb["id"] = f"b{n_max}"
            fresh += 1
        out.append(nb)

    doc["blocks"] = out
    alive = {b["id"] for b in out}
    dropped = [k for k in (doc.get("translations") or {}) if k not in alive]
    for key in ("translations", "plain", "notes", "highlights"):
        d = doc.get(key) or {}
        doc[key] = {k: v for k, v in d.items() if k in alive}

    todo = [b for b in out if b.get("translate") and not b.get("hidden")
            and b["id"] not in (doc.get("translations") or {})]
    return {"blocks": len(out), "kept": kept, "new": fresh,
            "dropped": len(dropped), "to_translate": len(todo)}


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

    # Gom lại để soát hệ chữ sau khi stream xong. Pass dịch đã có `script_leak`,
    # pass hỏi đáp thì chưa — và nó rò thật: một câu trả lời đúng nội dung có
    # chữ `तथा` (tiếng Hindi = "và") nằm giữa câu tiếng Việt.
    #
    # Soát SAU chứ không chặn giữa chừng: chữ đã hiện ra trên màn hình rồi, và
    # cắt ngang câu trả lời còn tệ hơn. Cùng triết lý "cảnh báo chứ không chặn"
    # của `check_slides` / `check_answer`.
    da_noi: list[str] = []
    async for kind, payload in llm.stream_text(
        msgs, model=doc["model"], session_id=doc_id, max_tokens=4000, temperature=0.4
    ):
        if kind == "usage":
            # trả cả lượt này lẫn cộng dồn, cùng dạng với pass dịch và pass giải
            # thích — hỏi đáp cũng tiêu tiền nên phải hiện ra chứ không nuốt đi
            total = _bump_usage(doc_id, payload)
            yield "usage", json.dumps({"run": json.loads(payload), "total": total})
            continue
        if kind == "delta":
            da_noi.append(payload)
        yield kind, payload

    bad = script_leak("".join(da_noi), full_source_text(doc["blocks"]))
    if bad:
        yield "warn", json.dumps({
            "chars": "".join(sorted(bad))[:12],
            "msg": "Câu trả lời lẫn ký tự thuộc hệ chữ lạ ("
                   + "".join(sorted(bad))[:12]
                   + ") — model trả về rác ở chỗ đó. Hỏi lại là thường hết.",
        })
