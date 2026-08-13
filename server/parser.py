"""Biến PDF / text / arXiv thành danh sách block có cấu trúc.

Mục tiêu không phải là giữ layout (BabelDOC làm việc đó rồi) mà là lấy ra
đúng *đơn vị lập luận*: đoạn văn liền mạch, có nhãn section, tách khỏi
công thức / caption / tài liệu tham khảo — để tầng dịch có ngữ cảnh đúng.
"""

from __future__ import annotations

import io
import re
import unicodedata
from collections import Counter
from dataclasses import dataclass, asdict, field

import httpx

# ---------------------------------------------------------------- data model

BLOCK_TYPES = ("title", "heading", "para", "caption", "equation", "reference", "meta")


@dataclass
class Block:
    id: str
    type: str          # xem BLOCK_TYPES
    text: str
    section: str = ""  # tên section chứa block này
    level: int = 0     # cấp heading (1 = section, 2 = subsection)
    page: int = 0
    translate: bool = True  # False = giữ nguyên (công thức, tài liệu tham khảo)
    figure: str = ""   # id ảnh hình/bảng cắt ra từ PDF (chỉ block caption)
    figure_page: int = -1          # trang chứa hình, để mở lại khung mà chỉnh tay
    figure_rect: list | None = None  # [x0,y0,x1,y1] theo point của PDF
    figure_manual: bool = False    # người dùng đã tự chỉnh khung này chưa
    figure_source: str = "heuristic"  # "heuristic" | "model" | "manual"
    marker: str = ""   # dấu đầu mục nếu khối này là một mục trong danh sách
    # Người đọc tự ẩn khối rác còn sót (nhãn trục lạc ra từ hình, dòng chân
    # trang…). Ẩn chứ KHÔNG xoá: bản dịch đã trả tiền rồi, và người ta hay đổi
    # ý. Khối ẩn cũng không vào mẻ dịch nên không tốn thêm.
    hidden: bool = False

    def dict(self):
        return asdict(self)


# ---------------------------------------------------------------- text utils

_LIGATURES = {
    "ﬁ": "fi", "ﬂ": "fl", "ﬀ": "ff", "ﬃ": "ffi", "ﬄ": "ffl",
    "’": "'", "‘": "'", "“": '"', "”": '"',
    "–": "-", "—": "—", " ": " ",
}

# Tên mục không thể nhầm với thứ khác — luôn nhận là heading.
_SECTION_EXACT = (
    "abstract", "introduction", "background", "related work", "preliminaries",
    "methodology", "experiments", "experimental setup", "results", "evaluation",
    "discussion", "limitations", "conclusion", "conclusions", "future work",
    "acknowledgments", "acknowledgements", "references", "bibliography", "appendix",
)
# Cũng hay là tên mục, nhưng cũng hay là ô tiêu đề bảng ("Model", "Method").
# Chỉ nhận khi cỡ chữ lớn hơn thân bài.
_SECTION_MAYBE = (
    "method", "methods", "approach", "model", "models", "architecture",
    "experiment", "setup", "analysis", "ablation", "ablations", "dataset",
    "datasets", "baseline", "baselines", "task", "tasks",
)
_HEADING_WORDS = _SECTION_EXACT + _SECTION_MAYBE

_REF_START = re.compile(r"^\s*(references|bibliography)\s*$", re.I)

# Phụ lục nằm SAU mục tài liệu tham khảo trong hầu hết bài báo. Trước đây cờ
# `in_refs` bật ở "References" rồi không bao giờ tắt, nên toàn bộ phụ lục — kiến
# trúc mô hình, cấu hình huấn luyện, các ablation — bị đánh loại `reference`,
# tức là không dịch và bị màn đọc lọc bỏ. Đã đo trên một bài thật: 144/238 khối
# biến mất, người đọc chỉ còn thấy trơ mấy cái tiêu đề.
#
# Mẫu bắt: "Appendix A", "A Theia Model Architecture", "D.3.1 WidowX Arm…".
# Cố tình CHẶT — chỉ khớp khi chữ cái/số mục đứng đầu rồi tới chữ hoa, và cả
# dòng đủ ngắn để là tiêu đề. Dương tính giả ở đây tệ hơn âm tính giả: nhận nhầm
# một dòng trong mục tham khảo thành phụ lục là kéo cả trăm mục sách báo vào bài.
# "Appendix A", "Phụ lục B" — chữ, nên không phân biệt hoa thường.
_APPENDIX_WORD = re.compile(r"^\s*(?:appendix|phụ\s*lục)\b", re.I)
# "A Theia Model Architecture", "D.3.1 WidowX Arm Experiments".
# KHÔNG có dấu chấm ngay sau chữ cái đơn — đó chính là dạng của tên viết tắt
# trong mục tham khảo ("A. Radford, J. Kim…"), và nhận nhầm một mục là kéo cả
# trăm mục sách báo vào bài.
_APPENDIX_NUM = re.compile(r"^\s*[A-Z](?:\.\d+)*\s+[A-Z][A-Za-z]")


def _is_appendix_head(text: str) -> bool:
    """Dòng này có phải tiêu đề phụ lục không.

    Cố tình CHẶT: dương tính giả ở đây tệ hơn nhiều so với âm tính giả. Ngoài
    mẫu chữ còn ba chốt — đủ ngắn để là tiêu đề, không có dấu phẩy (mục tham
    khảo nào cũng đầy dấu phẩy), và không kết thúc bằng dấu chấm.
    """
    t = (text or "").strip()
    if not t or len(t) > 90 or len(t.split()) > 12:
        return False
    if _APPENDIX_WORD.match(t):
        return True
    return ("," not in t and not t.endswith(".")
            and bool(_APPENDIX_NUM.match(t)))
_NUM_HEADING = re.compile(r"^\s*(\d+(?:\.\d+)*)\.?\s+([A-Z][^.]{2,80})\s*$")
_ROMAN_HEADING = re.compile(r"^\s*([IVXLC]+)\.?\s+([A-Z][^.]{2,80})\s*$")
# "Table 2:" là caption; "Table 2 summarizes our results…" là câu văn — phải có
# dấu ngắt sau số, nếu không mọi đoạn nhắc tới bảng đều bị coi là chú thích.
_CAPTION = re.compile(r"^\s*(figure|fig\.?|table|algorithm|listing)\s*\d+\s*(?:[:.)\-–—]|$)", re.I)
# tem dọc ở lề trái bản arXiv — cỡ chữ to nên hay bị nhầm là tiêu đề
_STAMP = re.compile(r"^\s*(arxiv:|doi:|preprint|under review|published as|\d{4} ieee|acm isbn)", re.I)
# dòng bản quyền / thông tin kỷ yếu in ở đầu hoặc chân trang
_JOURNAL = re.compile(
    r"(proceedings of the|annual meeting of the|association for computational linguistics"
    r"|©\s*\d{4}|\(c\)\s*\d{4}|all rights reserved|licensed under|creative commons"
    r"|conference on|workshop on|advances in neural information|\bpages?\s+\d+[-–]\d+"
    r"|permission to make digital|isbn\s|issn\s)", re.I,
)
# chú thích chân trang kiểu "* Corresponding author", "1Our code can be found at…"
_FOOTNOTE = re.compile(r"^\s*([*†‡§¶]|\d{1,2}(?=[A-Z]))")
# khối đầu trang 1: tên tác giả, cơ quan, email — dịch chỉ tốn tiền
_AUTHORY = re.compile(r"@|\b(university|universit[ée]|institute|research|labs?|inc\.|corp\.|google|microsoft|meta|openai|deepmind|college|school of|department)\b", re.I)
_MATHY = re.compile(
    r"[=∑∏∫∂∇≤≥≈≠±×÷√∈∉⊆⊂∪∩∀∃→←↔⇒⇔·∘⊙⊗‖⟨⟩−∼≜≡"
    r"αβγδεζηθικλμνξπρσςτυφχψωΓΔΘΛΞΠΣΦΨΩ]"
)


# TeX đặt dấu mũ bằng một glyph riêng đứng trước chữ: `ˆ` + `a` chứ không phải `â`.
# Để nguyên thì bản dịch hiện ra "ˆa" và model đọc thành hai ký tự rời — mà `â`
# trong paper thường là "giá trị dự đoán", đọc sai là hiểu sai.
_ACCENTS = {
    "ˆ": "̂",  # ˆ  dấu mũ
    "˜": "̃",  # ˜  dấu ngã
    "¯": "̄",  # ¯  gạch ngang trên
    "˘": "̆",  # ˘  dấu trăng
    "˙": "̇",  # ˙  chấm trên
    "¨": "̈",  # ¨  hai chấm
    "˚": "̊",  # ˚  vòng tròn
    "ˇ": "̌",  # ˇ  dấu móc ngược
    "´": "́",  # ´  dấu sắc
}
_ACCENT_RE = re.compile("([" + "".join(_ACCENTS) + r"])[ \t]?([A-Za-z])")


_TRAIL_ACCENT = re.compile(rf"([A-Za-z])[ \t]*([{''.join(_ACCENTS)}̀-ͯ])")
_LOOSE_COMBINING = re.compile(r"[ \t]+([̀-ͯ])")


def _join_accents(s: str) -> str:
    """`ˆ`+`a` -> `â`. Phải chạy TRƯỚC NFKC.

    NFKC biến phần lớn các dấu rời này thành *dấu cách + dấu tổ hợp*, tức là tự
    chèn thêm một khoảng trắng vào giữa rồi mới ghép — chạy sau là hỏng.

    TeX đặt dấu cả hai phía: `ˆ`+`a` mà cũng có `T`+`˜`. Kiểu sau thường bị tách
    hẳn ra span riêng, nên còn phải gỡ khoảng trắng chen vào giữa chữ và dấu.
    """
    s = _ACCENT_RE.sub(lambda m: m.group(2) + _ACCENTS[m.group(1)], s)
    s = _TRAIL_ACCENT.sub(lambda m: m.group(1) + _ACCENTS.get(m.group(2), m.group(2)), s)
    return _LOOSE_COMBINING.sub(r"\1", s)


def clean_text(s: str) -> str:
    for a, b in _LIGATURES.items():
        s = s.replace(a, b)
    s = _join_accents(s)
    s = unicodedata.normalize("NFKC", s)
    # nối từ bị gạch nối cuối dòng: "trans-\nlation" -> "translation"
    s = re.sub(r"(\w)-\s*\n\s*(\w)", r"\1\2", s)
    s = re.sub(r"\s*\n\s*", " ", s)
    s = re.sub(r"[ \t]{2,}", " ", s)
    return s.strip()


def _is_prose(text: str) -> bool:
    """Có phải một đoạn văn xuôi thật không (khác với ô bảng / nhãn trong hình).

    Không dùng độ dài làm mốc: PyMuPDF gộp cả thân bảng thành một khối rất dài.
    Dấu hiệu phân biệt là mật độ chữ số — thân bảng dày số, văn xuôi thì không.
    """
    if len(text) <= 180:
        return False
    if sum(c.isdigit() for c in text) / len(text) > 0.10:
        return False
    return ". " in text or text.rstrip().endswith(".")


def _is_mathy(text: str) -> bool:
    if len(text) > 400:
        return False
    syms = len(_MATHY.findall(text))
    letters = sum(c.isalpha() for c in text)
    if syms >= 3 and syms > letters / 12:
        return True
    # dòng ngắn chỉ có ký hiệu + số
    return len(text) < 90 and syms >= 2 and letters < 20


def _looks_like_heading(text: str, rel_size: float, bold: bool) -> tuple[bool, int]:
    t = text.strip()
    if len(t) > 110 or not t:
        return False, 0
    low = t.lower().rstrip(":.")
    if low in _SECTION_EXACT:
        return True, 1
    if low in _SECTION_MAYBE:
        # "Model" một mình thường là ô tiêu đề bảng; chỉ là mục nếu chữ to hơn thân bài
        return (rel_size >= 1.05 or bold and rel_size >= 1.02), 1
    m = _NUM_HEADING.match(t) or _ROMAN_HEADING.match(t)
    if m:
        # "835 Teutberga" khớp mẫu nhưng không phải tên mục — số mục của một bài
        # báo không bao giờ lên tới hàng trăm. Đây thường là ô bảng hoặc chú thích.
        head = m.group(1).split(".")[0]
        if head.isdigit() and int(head) > 40:
            return False, 0
        depth = m.group(1).count(".") + 1
        return True, min(depth, 3)
    if any(low.startswith(w + " ") for w in _HEADING_WORDS) and len(t) < 60:
        return True, 1
    # một chữ trơ trọi thường là ô tiêu đề bảng, không phải mục của bài
    if len(t.split()) < 2:
        return False, 0
    if (rel_size >= 1.12 or (bold and rel_size >= 1.02)) and not t.endswith("."):
        return True, 1 if rel_size >= 1.25 else 2
    return False, 0


# ---------------------------------------------------------------- PDF parsing


# Chỉ số không bao giờ mở đầu một đoạn văn — gặp dấu hiệu này là chắc chắn
# block trước bị cắt ngang chứ không phải đoạn mới.
_CONT = re.compile(r"^\s*[_^]\{")

# Dấu đầu mục. Cố ý KHÔNG nhận gạch ngang: `−E` trong công thức và từ bị ngắt
# gạch nối cuối dòng đều mở đầu bằng gạch, nhận vào là hỏng nhiều hơn được.
_BULLET_CH = "•‣▪◦∙"
_LIST_START = re.compile(
    rf"^\s*(?:([{_BULLET_CH}])|(\(?\d{{1,2}}[.)])|(\([ivxIVX]{{1,4}}\)))\s+(?=\S)"
)


def _list_items(lines: list[tuple[str, tuple]]) -> list[tuple[str, list]] | None:
    """Tách một block thành các mục danh sách, hoặc None nếu không phải danh sách.

    Danh sách bị nén thành một đoạn chạy dài là mất mát thật: người đọc không
    còn thấy đây là ba ý song song, và tầng dịch cũng phải đoán chỗ nào hết mục
    này sang mục kia.

    Mục rỗng marker đứng đầu là câu dẫn nhập ("Đóng góp của chúng tôi gồm:").
    """
    starts = []
    for i, (txt, bbox) in enumerate(lines):
        m = _LIST_START.match(txt)
        if m:
            starts.append((i, m.group(1) or m.group(2) or m.group(3), bbox[0], m.end()))
    if len(starts) < 2:
        return None

    bullets = [s for s in starts if s[1] in _BULLET_CH]
    if len(bullets) >= 2:
        keep = bullets
    else:
        # Đánh số thì dễ nhầm với liệt kê trong câu ("(i) cách này, (ii) cách kia").
        # Đòi hai dấu hiệu nữa: số chạy liên tiếp, và các mục thẳng hàng mép trái.
        nums = [(i, mk, x, e, int(d.group()))
                for i, mk, x, e in starts if (d := re.search(r"\d+", mk))]
        if len(nums) < 2:
            return None
        seq = [n[4] for n in nums]
        if seq != list(range(seq[0], seq[0] + len(seq))):
            return None
        if max(n[2] for n in nums) - min(n[2] for n in nums) > 2.5:
            return None
        keep = [(i, mk, x, e) for i, mk, x, e, _ in nums]

    out: list[tuple[str, list]] = []
    if keep[0][0] > 0:                       # câu dẫn nhập trước mục đầu tiên
        out.append(("", lines[: keep[0][0]]))
    for k, (i, marker, _x, end) in enumerate(keep):
        stop = keep[k + 1][0] if k + 1 < len(keep) else len(lines)
        body = [(lines[i][0][end:], lines[i][1])] + lines[i + 1 : stop]
        out.append((marker, body))
    return out


def _stitch(items: list[dict], covered: set[int]) -> list[dict]:
    """Nối lại đoạn bị PDF cắt ngang ở chỗ có công thức nằm trong dòng.

    PyMuPDF tách block mỗi khi baseline nhảy, mà chỉ số dưới thì baseline nhảy
    thật. Hậu quả: `D = {d_{i}}^{N}` kết thúc một block, còn `_{i=1}, the
    objective…` mở đầu block sau — câu bị chẻ đôi đúng giữa mệnh đề. Mỗi nửa rồi
    sẽ được dịch riêng, mất hẳn quan hệ giữa hai vế.

    Nối không chèn khoảng trắng: `{s^{k}` + `_{k=1}.` phải thành `{s^{k}_{k=1}.`
    """
    out: list[dict] = []
    for it in items:
        prev = out[-1] if out else None
        if (prev is not None
                and _CONT.match(it["text"])
                and prev["sort"][:2] == it["sort"][:2]          # cùng trang, cùng cột
                and prev.get("idx") not in covered
                and it.get("idx") not in covered):
            prev["text"] = prev["text"].rstrip() + it["text"].lstrip()
            pb, ib = prev["bbox"], it["bbox"]
            prev["bbox"] = (min(pb[0], ib[0]), min(pb[1], ib[1]),
                            max(pb[2], ib[2]), max(pb[3], ib[3]))
            prev["nlines"] = prev.get("nlines", 1) + it.get("nlines", 1)
            continue
        out.append(it)
    return out


def _span_key(s: dict) -> tuple:
    """Nhận dạng một span bền qua các lần mở lại tài liệu."""
    b = s["bbox"]
    return (round(b[0], 1), round(b[1], 1), round(b[2], 1), s.get("text", ""))


def _spans_in(page, rect: tuple) -> list[dict]:
    """Span có TÂM nằm trong khung.

    Phép chứa chứ không phải phép giao: `get_text(clip=…)` của PyMuPDF nhận cả
    span chỉ chạm mép, nên vùng công thức sẽ nuốt luôn chữ của đoạn văn bên cạnh.
    """
    out = []
    for b in page.get_text("dict")["blocks"]:
        if b.get("type") != 0:
            continue
        for l in b.get("lines", []):
            for s in l.get("spans", []):
                if not s.get("text"):
                    continue
                x0, y0, x1, y1 = s["bbox"]
                cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
                if rect[0] <= cx <= rect[2] and rect[1] <= cy <= rect[3]:
                    out.append(s)
    return out


def _rows(spans: list[dict]) -> list[list[dict]]:
    """Gom span thành dòng logic, trong mỗi dòng sắp trái sang phải.

    Dòng dựng theo **baseline của span cỡ thường**, rồi mới gắn span nhỏ vào
    dòng có baseline gần nhất. Gom theo tâm dọc là sai: chỉ số dưới có tâm thấp
    hơn hẳn nên tách thành dòng riêng, và khi nối lại thì mọi chỉ số của công
    thức bị dồn hết xuống cuối — `(r, T̃, a) = (x, T̂, I, H). (1) t Tt t+1 KD`.
    """
    if not spans:
        return []
    sized = [s for s in spans if len(s["text"].strip()) >= 2] or spans
    body = max(s["size"] for s in sized)
    big = [s for s in spans if s["size"] >= body - 0.6]
    small = [s for s in spans if s["size"] < body - 0.6]
    if not big:
        big, small = spans, []

    rows: list[tuple[float, list[dict]]] = []
    for s in sorted(big, key=lambda s: s["origin"][1]):
        y = s["origin"][1]
        if rows and abs(y - rows[-1][0]) <= body * 0.5:
            rows[-1][1].append(s)
        else:
            rows.append((y, [s]))
    for s in small:                       # chỉ số bám vào dòng gần nhất
        k = min(range(len(rows)), key=lambda i: abs(rows[i][0] - s["origin"][1]))
        rows[k][1].append(s)
    return [sorted(r[1], key=lambda s: s["bbox"][0]) for r in rows]


def text_from_spans(spans: list[dict]) -> str:
    """Ghép span thành chữ, giữ chỉ số trên/dưới."""
    return clean_text("\n".join(_line_text({"spans": r}) for r in _rows(spans)))


def assign_spans(page, boxes: list[tuple]) -> list[list[dict]]:
    """Chia span của một trang về đúng khối, mỗi span thuộc đúng MỘT khối.

    Khung của mô hình có thể chồng lên nhau — khung công thức thường trùm lên cả
    dòng đầu của đoạn văn ngay dưới. Cho span vào khung **nhỏ nhất** chứa tâm nó
    thì mỗi mẩu chữ chỉ được đếm một lần, và luôn về đúng khối cụ thể nhất.
    """
    out: list[list[dict]] = [[] for _ in boxes]
    order = sorted(range(len(boxes)),
                   key=lambda i: (boxes[i][2] - boxes[i][0]) * (boxes[i][3] - boxes[i][1]))
    leftover: list[tuple[int, dict]] = []
    seq = 0
    for b in page.get_text("dict")["blocks"]:
        if b.get("type") != 0:
            continue
        for l in b.get("lines", []):
            for s in l.get("spans", []):
                if not s.get("text"):
                    continue
                seq += 1
                s["_seq"] = seq
                x0, y0, x1, y1 = s["bbox"]
                cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
                for i in order:
                    r = boxes[i]
                    if r[0] <= cx <= r[2] and r[1] <= cy <= r[3]:
                        out[i].append(s)
                        break
                else:
                    leftover.append((seq, s))

    # Lượt vét: span không có khung nào chứa TÂM nó thì thử gán theo **phần diện
    # tích chồng lấn lớn nhất**.
    #
    # Vì sao cần: khung của mô hình bám rất sát chữ, nên mép trên của khung
    # thường cắt ngang dòng đầu tiên. Đo trên bài GCR: khung abstract bắt đầu ở
    # y=249,4 còn dòng đầu nằm ở y=244,5–253,5, tức tâm ở 249,0 — **cao hơn mép
    # khung đúng 0,4pt**. Cả dòng "Long-video question answering requires
    # identifying sparse yet" rơi ra ngoài và biến mất khỏi bài, dù docling đã
    # bóc nó đúng.
    #
    # Lượt này chạy SAU và chỉ nhận span đã trượt hết ở lượt một, nên không đổi
    # một phép gán đúng nào. Span thật sự không chạm khung nào vẫn rơi xuống
    # `recover_uncovered()` như cũ.
    touched: set[int] = set()
    for _seq, s in leftover:
        x0, y0, x1, y1 = s["bbox"]
        best, best_area = -1, 0.0
        for i, r in enumerate(boxes):
            w = min(x1, r[2]) - max(x0, r[0])
            h = min(y1, r[3]) - max(y0, r[1])
            if w > 0 and h > 0 and (a := w * h) > best_area:
                best, best_area = i, a
        # Đòi chồng ít nhất một phần ba span: chạm mép một chút thì chưa đủ để
        # kết luận nó thuộc về khung đó.
        if best >= 0 and best_area >= 0.33 * max(1e-6, (x1 - x0) * (y1 - y0)):
            out[best].append(s)
            touched.add(best)

    # Span nhặt ở lượt hai được nối vào CUỐI danh sách, nên phải sắp lại về đúng
    # thứ tự PyMuPDF đọc ra. Không sắp thì dòng đầu của đoạn nằm ở cuối khối —
    # các tầng dưới có sắp lại theo hình học nên bài vẫn ra đúng, nhưng để hàm
    # này trả về thứ tự sai là đặt sẵn một cái bẫy cho lần sửa sau.
    for i in touched:
        out[i].sort(key=lambda x: x["_seq"])
    return out


def text_in_bbox(page, rect: tuple) -> str:
    """Chữ trong một vùng, khi không cần chia tranh chấp với vùng khác."""
    return text_from_spans(_spans_in(page, rect))


def _line_text(line: dict) -> str:
    """Ghép các span của một dòng, giữ lại chỉ số trên và chỉ số dưới.

    PDF không lưu "đây là chỉ số dưới" — nó chỉ vẽ chữ nhỏ hơn, thấp hơn một
    chút. Nối thẳng các span lại thì `D = {dᵢ}ᴺᵢ₌₁` biến thành `D = {di}N i=1`:
    thông tin cấu trúc mất sạch, và model dịch (hay người đọc) không còn cách
    nào khôi phục. Ghi lại thành `{d_{i}}^{N}_{i=1}` thì cả hai đều hiểu đúng.
    """
    spans = [s for s in line.get("spans", []) if s.get("text")]
    if not spans:
        return ""
    # Mốc cỡ chữ bỏ qua span một ký tự: dấu `{` `}` bao nhiều tầng của công thức
    # được vẽ ở cỡ rất lớn, lấy chúng làm mốc thì cả dòng bị coi là chỉ số.
    sized = [s for s in spans if len(s["text"].strip()) >= 2] or spans
    big = max(s["size"] for s in sized)
    # mốc baseline lấy theo các span cỡ chữ thường, không lấy theo chỉ số
    normal = [s["origin"][1] for s in spans if s["size"] >= big - 0.6]
    base = max(normal) if normal else spans[0]["origin"][1]

    out: list[str] = []
    level = 0                      # 0 bình thường · 1 chỉ số trên · -1 chỉ số dưới

    def switch(lv: int) -> None:
        nonlocal level
        if lv == level:
            return
        if level:
            out.append("}")
        if lv:
            out.append("^{" if lv > 0 else "_{")
        level = lv

    pending = ""                   # khoảng trắng chưa biết thuộc trong hay ngoài ngoặc
    prev_x1 = None
    for s in spans:
        txt = s["text"]
        if not txt.strip():
            pending += txt
            prev_x1 = s["bbox"][2]
            continue
        # PDF không lưu dấu cách — nó đặt glyph cách nhau ra. Nối span thẳng tuột
        # thì mất khoảng trắng ở chỗ đổi font: `=∅or`, `∈Dt`. Suy lại từ khe hở.
        if prev_x1 is not None and not pending and s["bbox"][0] - prev_x1 > s["size"] * 0.22:
            pending = " "
        prev_x1 = s["bbox"][2]
        small = s["size"] < big - 0.6
        dy = s["origin"][1] - base
        if not small:
            lv = 0
        elif s["flags"] & 1 or dy < -0.8:          # bit 0 của flags = chỉ số trên
            lv = 1
        elif dy > 0.8:
            lv = -1
        else:
            lv = 0
        if pending:
            # khoảng trắng chỉ nằm trong ngoặc khi hai bên cùng một mức, nếu không
            # thì `a^{(g) }based` sẽ nuốt mất dấu cách trước từ tiếp theo
            if lv != level:
                switch(0)
            out.append(pending)
            pending = ""
        switch(lv)
        out.append(txt)
    switch(0)
    return "".join(out) + pending


def _page_columns(rects, page_width: float) -> int:
    """Đoán số cột: nếu rất ít block cắt qua trục giữa thì coi là 2 cột."""
    if len(rects) < 8:
        return 1
    mid = page_width / 2
    crossing = sum(1 for r in rects if r[0] < mid - 12 and r[2] > mid + 12)
    return 1 if crossing > len(rects) * 0.25 else 2


def _word_gaps(page, items: list[dict]) -> None:
    """Tính khoảng cách trung vị giữa các từ trong mỗi khối.

    Đây là dấu hiệu bắt thân bảng đáng tin nhất khi bảng không kẻ khung: các cột
    của bảng cách nhau hàng chục point, trong khi văn xuôi chỉ cách nhau vài
    point. Bảng tràn hết bề ngang cột và bắt đầu đúng mép cột thì mọi dấu hiệu
    khác đều chịu thua, riêng dấu hiệu này vẫn phân biệt được.
    """
    boxes = [(it["bbox"], it) for it in items]
    prev_end: dict[tuple, float] = {}
    gaps: dict[int, list[float]] = {}
    for x0, y0, x1, y1, _txt, bno, lno, _wno in page.get_text("words"):
        cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
        host = None
        for (bx0, by0, bx1, by1), it in boxes:
            if bx0 <= cx <= bx1 and by0 <= cy <= by1:
                host = it
                break
        if host is None:
            continue
        key = (id(host), bno, lno)
        if key in prev_end:
            gaps.setdefault(id(host), []).append(x0 - prev_end[key])
        prev_end[key] = x1
    for it in items:
        g = sorted(v for v in gaps.get(id(it), []) if v >= 0)
        it["wgap"] = g[len(g) // 2] if g else 0.0
        it["gaps"] = g
        it["nwords"] = len(g) + 1


def _doc_stats(items: list[dict]) -> dict:
    """Thống kê cách trình bày của cả tài liệu.

    Theo PDFFigures 2.0: phần lớn chữ trong một bài là chữ thân bài, và chữ thân
    bài trình bày nhất quán từ đầu tới cuối. Nên cái gì trình bày *lệch chuẩn* thì
    coi là chữ nằm trong hình. Ba con số dưới đây là "chuẩn" đó.
    """
    size = Counter()
    width = Counter()
    left = Counter()
    for it in items:
        w = round(it["bbox"][2] - it["bbox"][0])
        size[round(it["size"] * 2) / 2] += max(len(it["text"]), 1)
        if it.get("nlines", 1) >= 2:
            width[round(w / 3) * 3] += 1
        left[round(it["bbox"][0] / 2) * 2] += 1
    gaps = sorted(it["wgap"] for it in items if it.get("wgap"))
    return {
        "size": size.most_common(1)[0][0] if size else 10.0,
        "width": width.most_common(1)[0][0] if width else 0,
        # các mốc lề trái hay gặp = mép các cột
        "margins": [m for m, n in left.most_common(6) if n >= 3],
        "wgap": gaps[len(gaps) // 2] if gaps else 0.0,
    }


def _graphic_clusters(page) -> list:
    """Các cụm đồ hoạ trên trang (nét vẽ vector + ảnh nhúng, gộp cụm gần nhau)."""
    import fitz

    area = page.rect.get_area()
    rects = []
    try:
        drawings = page.get_drawings()
    except Exception:  # noqa: BLE001
        drawings = []
    for d in drawings:
        r = fitz.Rect(d["rect"])
        if r.is_empty or r.is_infinite or r.get_area() > area * 0.85:
            continue
        if r.width < 2 and r.height < 2:
            continue
        # Đường kẻ ngang của bảng có chiều cao đúng bằng 0. Không thổi lên một
        # chút thì chúng bị loại khỏi cụm đồ hoạ, và mọi bảng kẻ bằng đường kẻ
        # (booktabs) coi như không có hình để bám vào.
        if r.height < 2:
            r.y1 = r.y0 + 2
        if r.width < 2:
            r.x1 = r.x0 + 2
        rects.append(r)
    for img in page.get_images(full=True):
        try:
            for r in page.get_image_rects(img[0]):
                r = fitz.Rect(r)
                if not r.is_empty and r.height >= 8:
                    rects.append(r)
        except Exception:  # noqa: BLE001
            continue
    if not rects:
        return []

    # gộp bằng một lượt quét theo trục dọc — O(n log n), chịu được biểu đồ tán xạ
    # có hàng nghìn nét vẽ mà gộp từng cặp thì treo máy
    rects.sort(key=lambda r: r.y0)
    clusters = [fitz.Rect(rects[0])]
    for r in rects[1:]:
        cur = clusters[-1]
        # khoảng cách giữa hai đường kẻ của bảng cỡ một dòng, nên ngưỡng gộp
        # phải rộng hơn chiều cao một dòng
        if r.y0 <= cur.y1 + 26:
            clusters[-1] = cur | r
        else:
            clusters.append(fitz.Rect(r))
    return [c for c in clusters if c.height >= 14 and c.width >= 18]


def _mark_body_text(items: list[dict], clusters: list, stats: dict, page) -> None:
    """Gán it["body"]: chữ của bài, hay chữ nằm trong hình?

    Đây là chỗ quyết định toàn bộ chất lượng. Chữ trong sơ đồ ("Query",
    "Retrieval") và chữ trong ô bảng vẫn là khối text hợp lệ với PyMuPDF; nếu coi
    chúng là chữ của bài thì (a) chúng thành những "đoạn văn" một chữ, và (b)
    chúng chặn vùng cắt hình khiến ảnh cắt ra sai bét.
    """
    import fitz

    modal = stats["size"]
    modal_w = stats["width"]
    margins = stats["margins"]
    center = (page.rect.x0 + page.rect.x1) / 2

    for it in items:
        r = fitz.Rect(it["bbox"])
        txt = it["text"]

        # caption và tên mục luôn là chữ của bài — chúng phải chặn được vùng hình
        if _CAPTION.match(txt) or _NUM_HEADING.match(txt) \
                or txt.lower().rstrip(":.") in _SECTION_EXACT:
            it["body"] = True
            continue

        # 1. đè lên cụm đồ hoạ -> chữ trong hình
        overlap = max((abs((r & c).get_area()) for c in clusters
                       if r.intersects(c)), default=0.0)
        in_graphic = r.get_area() > 0 and overlap > r.get_area() * 0.5
        if in_graphic:
            it["body"] = False
            continue

        is_centered = abs((r.x0 + r.x1) / 2 - center) < 18 and r.width < page.rect.width * 0.92
        at_margin = any(abs(r.x0 - m) <= 4 for m in margins)

        # 2. chữ to hơn chuẩn mà lại canh lề hoặc canh giữa -> tiêu đề bài/mục
        if it["size"] > modal + 0.4 and (at_margin or is_centered):
            it["body"] = True
            continue
        # 3. chữ nhỏ hơn chuẩn -> chữ trong hình
        if it["size"] < modal - 0.6:
            it["body"] = False
            continue
        # 4. có nhiều khoảng cách giữa các từ rộng bất thường -> thân bảng.
        # Phải đếm tỉ lệ chứ không lấy trung vị: một dòng bảng chỉ có vài khoảng
        # rộng (chỗ ngăn cột) lẫn giữa nhiều khoảng hẹp, trung vị vẫn ra nhỏ.
        g = it.get("gaps") or []
        if stats["wgap"] and len(g) >= 4:
            wide_at = stats["wgap"] * 3 + 3
            if sum(1 for v in g if v > wide_at) / len(g) >= 0.25:
                it["body"] = False
                continue
        # 4. nhiều dòng và rộng đúng bằng bề ngang cột -> chữ của bài
        if it.get("nlines", 1) >= 2 and modal_w and abs(r.width - modal_w) <= 10:
            it["body"] = True
            continue
        # 5. còn lại: chỉ canh đúng mép cột mới là chữ của bài.
        # "Canh giữa" KHÔNG được tính ở đây — bảng và hình đều canh giữa, tính vào
        # là mọi bảng canh giữa đều thành chữ của bài và không cắt được ảnh nữa.
        # Canh giữa chỉ có nghĩa khi đi kèm cỡ chữ khác chuẩn, tức luật 2 ở trên.
        it["body"] = bool(at_margin)


def _snap_to_text(page, region, items: list[dict]):
    """Không để mép vùng cắt ngang một khối chữ.

    PDFFigures 2.0 dùng chính điều kiện này để lọc vùng đề xuất sai. Ở đây nó
    chữa lỗi cắt cụt: nhãn nằm rìa hình (chú giải, phần trăm trên biểu đồ) hay
    thò ra ngoài khung đồ hoạ, và mép vùng rơi đúng vào giữa chữ.

    Cách xử lý phụ thuộc khối bị cắt là loại gì:
      - chữ trong hình  → nới vùng ra để lấy trọn nó
      - chữ của bài     → thu vùng lại để loại hẳn nó
    """
    import fitz

    page_r = page.rect
    for _ in range(5):
        changed = False
        for o in items:
            r = fitz.Rect(o["bbox"])
            if not r.intersects(region):
                continue
            area = abs(r.get_area())
            if area <= 0:
                continue

            # Caption và đoạn văn xuôi **của bài** thì tuyệt đối không được nằm
            # trong ảnh, kể cả khi nằm TRỌN bên trong: nuốt trọn một caption còn
            # tệ hơn cắt đôi nó, vì caption biến mất hẳn khỏi bài.
            # Phải kèm điều kiện `body`: câu văn hoàn chỉnh nằm BÊN TRONG hình
            # (lời nhắc, chú giải khung) cũng là văn xuôi, nhưng nó thuộc về hình.
            if o.get("body") and (_CAPTION.match(o["text"]) or _is_prose(o["text"])):
                mid = (region.y0 + region.y1) / 2
                if (r.y0 + r.y1) / 2 <= mid:
                    new_y0 = min(r.y1 + 3, region.y1)
                    if new_y0 > region.y0 + 0.5:
                        region.y0, changed = new_y0, True
                else:
                    new_y1 = max(r.y0 - 3, region.y0)
                    if new_y1 < region.y1 - 0.5:
                        region.y1, changed = new_y1, True
                continue

            # Chữ trong hình bị mép cắt ngang -> nới ra lấy trọn nó
            if not o.get("body") and abs((r & region).get_area()) < area * 0.99:
                grown = (region | r) & page_r
                if abs(grown.get_area()) > abs(region.get_area()) + 1:
                    region, changed = grown, True
        if not changed:
            break
    return region


def _figure_region(page, cap: dict, items: list[dict], clusters: list):
    """Vùng hình/bảng ứng với một caption.

    Hai bước, đúng thứ tự của PDFFigures 2.0:
      1. Nới từ caption ra tới khối **chữ của bài** gần nhất — chữ trong hình
         không chặn, nên nới được qua cả nhãn và ô bảng.
      2. Nếu trong vùng đó có cụm đồ hoạ lớn, co lại quanh chính cụm đó. Đây là
         bước cứu ảnh khỏi dính tiêu đề bài báo: tiêu đề là chữ của bài nhưng
         nằm cách hình một quãng trắng, nên bị loại khỏi cụm.
    """
    import fitz

    x0, y0, x1, y1 = cap["bbox"]
    is_table = bool(re.match(r"^\s*table", cap["text"], re.I))
    col = cap.get("col_bounds") or (page.rect.x0, page.rect.x1)
    cx0, cx1 = col

    same_col = []
    for o in items:
        if o is cap:
            continue
        w = o["bbox"][2] - o["bbox"][0]
        if w > 0 and min(o["bbox"][2], cx1) - max(o["bbox"][0], cx0) > w * 0.5:
            same_col.append(o)

    body = [o for o in same_col if o.get("body")]
    up_stop = max((o["bbox"][3] for o in body if o["bbox"][3] <= y0 + 1),
                  default=page.rect.y0 + 16)
    down_stop = min((o["bbox"][1] for o in body if o["bbox"][1] >= y1 - 1),
                    default=page.rect.y1 - 16)

    x_lo = max(cx0 - 6, page.rect.x0)
    x_hi = min(cx1 + 6, page.rect.x1)

    for from_below in ((True, False) if is_table else (False, True)):
        top = y1 + 3 if from_below else up_stop + 4
        bot = down_stop - 4 if from_below else y0 - 3
        if bot - top < 30:
            continue
        band = fitz.Rect(x_lo, top, x_hi, bot)

        # bước 2: co quanh cụm đồ hoạ lớn nhất nằm trong dải
        inside = [c for c in clusters
                  if c.intersects(band) and abs((c & band).get_area()) > c.get_area() * 0.55]
        region = None
        if inside:
            main = max(inside, key=lambda c: c.get_area())
            region = fitz.Rect(main)
            for c in inside:  # gộp các cụm đồ hoạ khác sát bên (hình ghép trái/phải)
                if c.y0 <= region.y1 + 24 and c.y1 >= region.y0 - 24:
                    region |= c
            for o in same_col:  # kéo lại nhãn chữ dính sát hình
                r = fitz.Rect(o["bbox"])
                near = fitz.Rect(region.x0 - 12, region.y0 - 16,
                                 region.x1 + 12, region.y1 + 16)
                if r.intersects(near) and r.intersects(band) and not o.get("body"):
                    region |= r & band
            region &= band
        else:
            # bảng thuần chữ: gom các khối "chữ trong hình" trong dải
            figs = [fitz.Rect(o["bbox"]) for o in same_col
                    if not o.get("body")
                    and o["bbox"][1] >= band.y0 - 1 and o["bbox"][3] <= band.y1 + 1]
            if figs:
                region = figs[0]
                for r in figs[1:]:
                    region |= r

        if region is None or region.is_empty or region.height < 28:
            continue
        region = fitz.Rect(region.x0, max(region.y0 - 5, band.y0),
                           region.x1, min(region.y1 + 5, band.y1))
        region = _snap_to_text(page, region, same_col + [cap])
        return _widen(page, region, cap)

    # Dự phòng: bảng không kẻ khung, các cột lại nằm ở những khối riêng biệt nên
    # cả cụm đồ hoạ lẫn khoảng cách từ đều không bắt được. Lúc này quay về cách
    # thô: quét từ caption tới đoạn văn xuôi gần nhất. Chỉ chạy khi đường chính
    # đã thất bại, và chặn trần cứng để không tái diễn cảnh nuốt cả tiêu đề bài.
    for from_below in ((True, False) if is_table else (False, True)):
        if from_below:
            lo = y1 + 3
            hi = min((o["bbox"][1] for o in same_col
                      if o["bbox"][1] >= y1 - 1 and _is_prose(o["text"])),
                     default=page.rect.y1 - 16) - 4
        else:
            hi = y0 - 3
            lo = max((o["bbox"][3] for o in same_col
                      if o["bbox"][3] <= y0 + 1 and _is_prose(o["text"])),
                     default=page.rect.y0 + 16) + 4
        if hi - lo < 30 or hi - lo > page.rect.height * 0.45:
            continue
        inner = [o for o in same_col
                 if o["bbox"][1] >= lo - 2 and o["bbox"][3] <= hi + 2
                 and not _is_prose(o["text"])]
        if len(inner) < 2:
            continue
        region = fitz.Rect(min(o["bbox"][0] for o in inner), lo,
                           max(o["bbox"][2] for o in inner), hi)
        region = _snap_to_text(page, region, same_col + [cap])
        return _widen(page, region, cap)
    return None


def _widen(page, region, cap: dict):
    """Bề ngang cuối cùng = hợp của bề ngang nội dung và bề ngang caption.

    Ép theo mép cột là hỏng hai đầu: bảng tràn ra ngoài cột thì bị cắt mất mấy
    cột bên phải, còn hình hẹp hơn cột thì ảnh thừa một mảng lề trắng. Caption
    gần như luôn trải đúng bề ngang của khối hình mà nó chú thích, nên nó là mốc
    tốt hơn — và vẫn bao được phần nội dung nhô ra ngoài caption.
    """
    import fitz

    return fitz.Rect(
        max(min(region.x0, cap["bbox"][0]) - 6, page.rect.x0),
        region.y0,
        min(max(region.x1, cap["bbox"][2]) + 6, page.rect.x1),
        region.y1,
    )


def _looks_blank(pix) -> bool:
    """Ảnh render ra có phải chỉ toàn nền trống không.

    Không đếm số giá trị pixel khác nhau trên một mẫu thưa: sơ đồ nét mảnh trên
    nền trắng thì gần như mọi mẫu đều rơi vào nền, và hình hợp lệ bị loại oan.
    Đo tỉ lệ pixel lệch khỏi màu nền thì đúng bản chất hơn.
    """
    buf = pix.samples
    if not buf:
        return True
    step = max(len(buf) // 20000, 1)
    sample = buf[::step]
    if not sample:
        return True
    bg = Counter(sample).most_common(1)[0][0]
    ink = sum(1 for v in sample if abs(v - bg) > 12)
    return ink / len(sample) < 0.002


def _resolve_overlaps(proposals: dict, caps: list[dict]) -> dict:
    """Xử lý khi nhiều caption trên cùng trang cùng nhận về một vùng.

    Hai bảng nằm sát nhau thì cụm đồ hoạ của chúng dính làm một, và cả hai
    caption cùng trỏ vào đó — kết quả là hai chú thích khác nhau nhưng ảnh giống
    hệt nhau. PDFFigures 2.0 gọi bước xử lý này là tách vùng.

    Hai việc, theo thứ tự:
      1. Mỗi caption là một vách ngăn: vùng của caption này không được vượt qua
         caption khác. Đủ để tách hai bảng xếp chồng.
    Nếu vách ngăn vẫn không tách được (hai bảng xếp cạnh nhau theo chiều ngang,
    hoặc caption đặt lệch), ta **giữ cả hai** chứ không bỏ bớt: ở màn hình bước 1
    người dùng nhìn thấy hai ảnh giống nhau và tự kéo lại khung bằng nút ✂. Bỏ
    bớt thì họ mất hẳn một bảng mà không biết là đã mất.
    """
    import fitz

    by_idx = {c["idx"]: c for c in caps}
    out: dict = {}
    for idx, r in proposals.items():
        r = fitz.Rect(r)
        me = by_idx[idx]
        my_mid = (me["bbox"][1] + me["bbox"][3]) / 2
        for other in caps:
            if other["idx"] == idx:
                continue
            oy0, oy1 = other["bbox"][1], other["bbox"][3]
            if oy1 <= r.y0 or oy0 >= r.y1:
                continue  # không nằm trong vùng
            if (oy0 + oy1) / 2 < my_mid:
                r.y0 = max(r.y0, oy1 + 3)
            else:
                r.y1 = min(r.y1, oy0 - 3)
        if r.height >= 30 and not r.is_empty:
            out[idx] = r

    return out


def caption_key(text: str) -> str:
    """Khoá ghép caption với vùng do mô hình trả về: 'table1', 'fig3'…"""
    m = _CAPTION.match(text or "")
    if not m:
        return ""
    kind = m.group(1).lower().rstrip(".")
    kind = "fig" if kind.startswith("fig") else kind
    num = re.search(r"\d+", text[m.start():m.end() + 4])
    return f"{kind}{num.group(0)}" if num else ""


def apply_layout(blocks: list[Block], regions: list[dict], pdf_bytes: bytes,
                 dpi: int = 160) -> dict[str, bytes]:
    """Thay khung cắt heuristic bằng khung do mô hình bố cục trả về.

    Ghép theo nhãn caption trước ('Table 1' ↔ 'table1') vì đó là mối nối chắc
    nhất; nhãn nào không khớp thì mới ghép theo khoảng cách trên cùng trang.
    Chỉ đụng vào khung hình — cấu trúc đoạn/mục vẫn do parser lo, mô hình không
    làm phần đó tốt hơn.
    """
    import fitz

    caps = [b for b in blocks if b.type == "caption"]
    if not caps or not regions:
        return {}

    by_key: dict[str, dict] = {}
    for r in regions:
        k = caption_key(r.get("caption", ""))
        if k and k not in by_key:
            by_key[k] = r

    used: set[int] = set()
    pairs: list[tuple[Block, dict]] = []
    for b in caps:
        r = by_key.get(caption_key(b.text))
        if r is not None and id(r) not in used:
            used.add(id(r))
            pairs.append((b, r))

    # caption chưa ghép được -> lấy vùng chưa dùng, cùng trang, gần nhất theo chiều dọc
    for b in caps:
        if any(p[0] is b for p in pairs):
            continue
        cand = [r for r in regions if id(r) not in used and r["page"] == b.page]
        if not cand:
            continue
        cy = (b.figure_rect[1] + b.figure_rect[3]) / 2 if b.figure_rect else 0
        best = min(cand, key=lambda r: abs((r["bbox"][1] + r["bbox"][3]) / 2 - cy))
        used.add(id(best))
        pairs.append((b, best))

    out: dict[str, bytes] = {}
    with fitz.open(stream=pdf_bytes, filetype="pdf") as doc:
        for b, r in pairs:
            pno = r["page"]
            if not 0 <= pno < len(doc):
                continue
            page = doc[pno]
            # nới nhẹ: mô hình bám sát mép nội dung, thêm chút lề cho dễ nhìn
            rect = fitz.Rect(*r["bbox"]) + (-5, -5, 5, 5)
            rect &= page.rect
            if rect.is_empty or rect.height < 20:
                continue
            try:
                png = render_rect(page, rect, dpi=dpi)
            except Exception:  # noqa: BLE001
                continue
            if _looks_blank(page.get_pixmap(clip=rect, dpi=72)):
                continue
            out[b.id] = png
            b.figure = b.id
            b.figure_page = pno
            b.figure_rect = [round(v, 1) for v in rect]
            b.figure_source = "model"
    return out


def recrop(blocks: list[Block], pdf_bytes: bytes,
           fig_dpi: int = 160, eq_dpi: int = 200) -> dict[str, bytes]:
    """Cắt lại ảnh từ khung đã lưu sẵn trong từng block.

    `parse_cache` giữ cấu trúc khối (kèm `figure_page` và `figure_rect`) nhưng
    KHÔNG giữ ảnh. Nạp lại cùng một file PDF thì khối trỏ tới ảnh không tồn tại:
    khung vẫn đúng nên hộp chỉnh khung mở ra bình thường, chỉ có thẻ hình là
    trống trơn. Cắt lại từ khung đã lưu vừa nhanh vừa khớp mã khối tuyệt đối —
    chép ảnh của bài cũ thì hỏng ngay khi bài cũ đã bị xoá.
    """
    import fitz

    out: dict[str, bytes] = {}
    with fitz.open(stream=pdf_bytes, filetype="pdf") as doc:
        for b in blocks:
            if not b.figure or not b.figure_rect:
                continue
            if not 0 <= b.figure_page < len(doc):
                continue
            page = doc[b.figure_page]
            r = fitz.Rect(*b.figure_rect) & page.rect
            if r.is_empty or r.height < 6:
                continue
            try:
                out[b.figure] = render_rect(
                    page, r, dpi=eq_dpi if b.type == "equation" else fig_dpi)
            except Exception:  # noqa: BLE001
                continue
    return out


def render_rect(page, rect, dpi: int = 140) -> bytes:
    """Cắt một vùng của trang thành PNG. Dùng chung cho cắt tự động và cắt tay."""
    return page.get_pixmap(clip=rect, dpi=dpi).tobytes("png")


def _render_figures(
    doc, page_items: dict[int, list[dict]], stats: dict
) -> tuple[dict[int, bytes], set[int], dict[int, tuple]]:
    """Render vùng hình/bảng thành PNG.

    Trả về `({chỉ số item caption -> PNG}, {chỉ số item bị hình nuốt})`. Tập thứ
    hai quan trọng không kém: mọi khối đã bị xếp là "chữ trong hình" nằm trong
    vùng cắt đều biến mất khỏi mạch đọc, vì chúng đã có mặt trong ảnh rồi.
    """
    import fitz

    out: dict[int, bytes] = {}
    covered: set[int] = set()
    rects: dict[int, tuple] = {}
    for pno, items in page_items.items():
        page = doc[pno]
        clusters = _graphic_clusters(page)
        _mark_body_text(items, clusters, stats, page)

        caps = [it for it in items if _CAPTION.match(it["text"])]
        proposals = {}
        for cap in caps:
            r = _figure_region(page, cap, items, clusters)
            if r is not None and not r.is_empty and r.height >= 30:
                proposals[cap["idx"]] = r
        proposals = _resolve_overlaps(proposals, caps)

        for cap in caps:
            rect = proposals.get(cap["idx"])
            if rect is None:
                continue
            try:
                pix = page.get_pixmap(clip=rect, dpi=140)
            except Exception:  # noqa: BLE001
                continue
            if _looks_blank(pix):
                continue
            out[cap["idx"]] = pix.tobytes("png")
            rects[cap["idx"]] = (pno, [round(v, 1) for v in rect])
            for o in items:
                # Đoạn văn thật thì không bao giờ bị hình nuốt. Nhưng một nhãn
                # ngắn dù bị xếp nhầm là chữ của bài mà lại nằm trong vùng hình
                # thì vẫn nên biến mất — nó đã có trong ảnh rồi.
                if o is cap or _is_prose(o["text"]):
                    continue
                if o.get("body") and len(o["text"].split()) > 12:
                    continue
                ox0, oy0, ox1, oy1 = o["bbox"]
                cx, cy = (ox0 + ox1) / 2, (oy0 + oy1) / 2
                if rect.x0 <= cx <= rect.x1 and rect.y0 <= cy <= rect.y1:
                    covered.add(o["idx"])
    return out, covered, rects


def _running_headers(items: list[dict]) -> set[int]:
    """Chỉ số các khối là header/footer chạy suốt tài liệu.

    Hai dấu hiệu, dùng cả hai vì mỗi cái bắt được một loại:
      1. Cùng một dòng chữ lặp lại ở mép trên/dưới của ít nhất 2 trang.
      2. Dòng ở mép trang khớp mẫu kỷ yếu/bản quyền (chỉ in ở trang đầu nên
         cách 1 không bắt được).
    """
    drop: set[int] = set()
    edge: dict[str, set[int]] = {}

    for it in items:
        h = it.get("page_h") or 792.0
        y0, y1 = it["bbox"][1], it["bbox"][3]
        at_edge = y1 < h * 0.09 or y0 > h * 0.91
        if not at_edge:
            continue
        if _JOURNAL.search(it["text"]):
            drop.add(it["idx"])
            continue
        # bỏ chữ số để "trang 3"/"trang 4" quy về cùng một khoá
        key = re.sub(r"\d+", "#", it["text"]).strip().lower()
        if len(key) > 3:
            edge.setdefault(key, set()).add(it["page"])

    repeated = {k for k, pages in edge.items() if len(pages) >= 2}
    if repeated:
        for it in items:
            key = re.sub(r"\d+", "#", it["text"]).strip().lower()
            if key in repeated:
                drop.add(it["idx"])
    return drop


def parse_pdf(data: bytes) -> tuple[str, list[Block], dict[str, bytes]]:
    import fitz  # PyMuPDF

    doc = fitz.open(stream=data, filetype="pdf")
    raw: list[dict] = []
    sizes: Counter = Counter()
    per_page: dict[int, list[dict]] = {}

    for pno, page in enumerate(doc):
        pd = page.get_text("dict")
        pw = pd.get("width") or page.rect.width
        items = []
        for b in pd.get("blocks", []):
            if b.get("type") != 0:
                continue
            spans = [s for line in b.get("lines", []) for s in line.get("spans", [])]
            if not spans:
                continue
            size = round(max(s["size"] for s in spans), 1)
            bold = any("bold" in (s.get("font") or "").lower() for s in spans)
            for s in spans:
                sizes[round(s["size"], 1)] += len(s["text"])

            raw_lines = [(_line_text(l), l["bbox"]) for l in b["lines"]]
            parts = _list_items(raw_lines)
            # danh sách -> mỗi mục là một khối riêng, để cột song ngữ căn theo mục
            chunks = ([(marker, ls) for marker, ls in parts] if parts
                      else [("", raw_lines)])
            for marker, ls in chunks:
                text = clean_text("\n".join(t for t, _ in ls))
                if not text:
                    continue
                bb = (min(x[1][0] for x in ls), min(x[1][1] for x in ls),
                      max(x[1][2] for x in ls), max(x[1][3] for x in ls)) if parts else b["bbox"]
                items.append({
                    "text": text, "size": size, "bold": bold,
                    "bbox": bb, "page": pno, "page_h": page.rect.height,
                    "nlines": len(ls), "marker": marker,
                })
        _word_gaps(page, items)
        ncol = _page_columns([i["bbox"] for i in items], pw)
        for it in items:
            wide = it["bbox"][2] - it["bbox"][0] > pw * 0.62  # hình tràn 2 cột
            col = 0 if ncol == 1 else (0 if it["bbox"][0] < pw / 2 else 1)
            it["sort"] = (pno, col, round(it["bbox"][1], 1))
            if ncol == 1 or wide:
                it["col_bounds"] = (page.rect.x0 + 6, page.rect.x1 - 6)
            elif col == 0:
                it["col_bounds"] = (page.rect.x0 + 6, pw / 2 - 2)
            else:
                it["col_bounds"] = (pw / 2 + 2, page.rect.x1 - 6)
        per_page[pno] = items
        raw.extend(items)

    for i, it in enumerate(raw):
        it["idx"] = i
    stats = _doc_stats(raw)
    figures, covered, fig_rects = _render_figures(doc, per_page, stats)
    covered |= _running_headers(raw)
    doc.close()
    raw.sort(key=lambda i: i["sort"])
    body_size = sizes.most_common(1)[0][0] if sizes else 10.0

    # tiêu đề = block cỡ chữ lớn nhất ở đầu trang 1, bỏ qua tem arXiv/DOI ở lề
    page0 = [i for i in raw if i["page"] == 0 and not _STAMP.match(i["text"])]
    title = ""
    if page0:
        big = max(page0[:12], key=lambda i: i["size"])
        if big["size"] > body_size * 1.25:
            title = big["text"]

    raw = _stitch(raw, covered)
    blocks = _to_blocks(raw, body_size, title, covered)
    # gắn ảnh đã render vào đúng block caption sinh ra từ item đó
    named: dict[str, bytes] = {}
    for b in blocks:
        idx = int(b.figure) if b.figure and b.figure.isdigit() else None
        if idx is not None and idx in figures:
            named[b.id] = figures[idx]
            b.figure = b.id
            b.figure_page, b.figure_rect = fig_rects[idx]
        else:
            # caption không cắt được hình: vẫn nhớ trang để người dùng tự cắt tay
            b.figure = ""
            if b.type == "caption":
                b.figure_page = b.page

    # Hai phễu chạy cuối cùng, sau khi mọi khối đã có loại và có ảnh: gom mảnh
    # bị cắt giữa từ, rồi tắt cờ dịch cho khối rác. Cả hai đều nhắm vào cùng một
    # cái giá — mỗi khối là một lượt dịch cộng một lượt giải thích.
    stitch_hyphenated(blocks)
    mark_noise(blocks)
    return title, blocks, named


def _to_blocks(items: list[dict], body_size: float, title: str,
               drop: set[int] | None = None) -> list[Block]:
    blocks: list[Block] = []
    section = ""
    level = 0
    in_refs = False
    n = 0

    def nid() -> str:
        nonlocal n
        n += 1
        return f"b{n}"

    seen_abstract = False

    drop = drop or set()

    for it in items:
        text = it["text"]
        if not text or text == title:
            continue
        if it.get("idx") in drop:  # chữ trong hình, header/footer chạy suốt bài
            continue
        # nhãn rời rạc bị xếp là chữ trong hình nhưng nằm ngoài vùng đã cắt ảnh:
        # chỉ bỏ khi thật ngắn, vì phân loại có thể sai và mất một đoạn thì tệ hơn
        if not it.get("body", True) and len(text.split()) <= 12:
            continue
        if re.fullmatch(r"[\d\s\-–|]{1,12}", text):  # số trang
            continue
        # _JOURNAL chỉ được áp ở mép trang (do _running_headers lo) — mục tài liệu
        # tham khảo cũng đầy chữ "Proceedings of…", áp toàn cục là xoá oan hết
        if _STAMP.match(text):
            continue
        # chú thích chân trang: giữ lại để đọc nhưng không dịch, không chen vào mạch bài
        h = it.get("page_h") or 792.0
        if it["bbox"][1] > h * 0.80 and _FOOTNOTE.match(text) and len(text) < 400:
            blocks.append(Block(nid(), "meta", text, section, 0, it["page"], False))
            continue

        rel = it["size"] / body_size if body_size else 1.0
        if text.lower().lstrip().startswith("abstract"):
            seen_abstract = True

        # phần đầu trang 1 trước Abstract: tên tác giả, cơ quan, email — không dịch
        if not seen_abstract and it["page"] == 0 and (
            _AUTHORY.search(text) or (len(text) < 60 and len(text.split()) <= 6)
        ):
            blocks.append(Block(nid(), "meta", text, "", 0, 0, False))
            continue

        if _REF_START.match(text):
            in_refs = True
            blocks.append(Block(nid(), "heading", text, text, 1, it["page"], False))
            section, level = text, 1
            continue

        # Gặp tiêu đề phụ lục thì THOÁT khỏi vùng tài liệu tham khảo — phần sau
        # là nội dung thật của bài, phải dịch và phải hiện ra.
        if in_refs and rel >= 1.02 and _is_appendix_head(text):
            in_refs = False
            m = _NUM_LEVEL.match(text)
            lvl = (m.group(1).count(".") + 1) if m else 1
            blocks.append(Block(nid(), "heading", text, text, min(lvl, 3),
                                it["page"], True))
            section, level = text, min(lvl, 3)
            continue

        if in_refs:
            blocks.append(Block(nid(), "reference", text, "References", 0, it["page"], False))
            continue

        if _CAPTION.match(text):
            # tạm mang chỉ số item; parse_pdf sẽ đổi thành id block sau khi ghép ảnh
            blocks.append(Block(nid(), "caption", text, section, 0, it["page"], True,
                                figure=str(it.get("idx", ""))))
            continue

        # xét công thức TRƯỚC heading: dòng toán cỡ chữ lớn hay bị nhầm là mục
        if _is_mathy(text):
            blocks.append(Block(nid(), "equation", text, section, 0, it["page"], False))
            continue

        is_head, lvl = _looks_like_heading(text, rel, it["bold"])
        if is_head:
            section, level = text, lvl
            blocks.append(Block(nid(), "heading", text, text, lvl, it["page"], True))
            continue

        # Nối tiếp đoạn bị cắt ngang giữa cột/trang. Phần đuôi thường không bắt
        # đầu bằng chữ thường — rất hay là phần còn lại của một trích dẫn
        # ("… (Trivedi et al.," / "2023). Câu tiếp theo…") — nên nhận cả chữ số
        # và dấu mở/đóng ngoặc, nếu không đoạn sẽ đứt ngay giữa câu.
        # Không nối qua ranh giới mục danh sách: hai mục là hai ý song song, dính
        # lại là mất đúng cái cấu trúc vừa nhận ra được.
        marker = it.get("marker", "")
        if (blocks and blocks[-1].type == "para" and blocks[-1].section == section
                and not marker and not blocks[-1].marker):
            prev = blocks[-1].text
            if prev and not re.search(r"[.!?:;]['\")\]]?$", prev) and re.match(r"[a-z0-9(\[)\]]", text):
                blocks[-1].text = prev + " " + text
                continue

        blocks.append(Block(nid(), "para", text, section, 0, it["page"], True, marker=marker))

    return [b for b in blocks if len(b.text) > 1]


# ------------------------------------------- dựng khối từ cấu trúc Docling


def _body_size(doc) -> float:
    """Cỡ chữ thân bài = cỡ chiếm nhiều ký tự nhất trong cả tài liệu."""
    c: Counter = Counter()
    for page in doc:
        for b in page.get_text("dict")["blocks"]:
            if b.get("type") != 0:
                continue
            for l in b.get("lines", []):
                for s in l.get("spans", []):
                    c[round(s["size"], 1)] += len(s.get("text") or "")
    return c.most_common(1)[0][0] if c else 10.0


_NUM_LEVEL = re.compile(r"^\s*(\d+(?:\.\d+)*)")


def _trim_overlaps(boxes: list[tuple], keep: float = 0.55) -> list[tuple]:
    """Khung nào chờm xuống khung dưới thì cắt ngang ở đỉnh khung dưới.

    Khung công thức của mô hình hay cao quá tay — dấu ngoặc `{ }` nhiều tầng kéo
    khung trùm luôn dòng đầu của đoạn văn ngay dưới, thế là đoạn đó mất dòng đầu.
    Cắt xong thì tâm dấu ngoặc vẫn nằm trong khung công thức (nên ngoặc không
    mất), còn tâm dòng văn rơi ra ngoài, về đúng đoạn của nó.

    So theo hình học chứ không theo thứ tự mô hình trả về: chính thứ tự ấy đôi
    khi xếp cả cụm công thức xuống sau đoạn văn đứng dưới chúng.

    `keep`: không cắt quá tay — giữ lại ít nhất chừng này chiều cao khung gốc.
    """
    out = list(boxes)
    for i in range(len(out)):
        for j in range(len(out)):
            if i == j:
                continue
            a, b = out[i], out[j]
            if min(a[2], b[2]) - max(a[0], b[0]) <= 0:      # không cùng cột
                continue
            if not (a[1] < b[1] < a[3]):                    # a không chờm xuống b
                continue
            h = a[3] - a[1]
            if h > 0 and (b[1] - a[1]) / h >= keep:
                out[i] = (a[0], a[1], a[2], b[1])
    return out


def _widen_to_glyphs(page, rect, max_grow: float = 0.45):
    """Nới khung cắt cho ôm trọn mọi span nó chạm phải, để không cắt đôi chữ.

    Chỉ nới **ngang**, và chỉ khi phần nới thêm còn khiêm tốn (`max_grow` lần bề
    ngang khung). Không có trần thì một span dài chạm mép khung sẽ kéo khung ra
    hết cột, và ảnh công thức thành ảnh cả đoạn văn.
    """
    import fitz

    r = fitz.Rect(rect)
    x0, x1 = r.x0, r.x1
    limit = r.width * max_grow
    for b in page.get_text("dict")["blocks"]:
        if b.get("type") != 0:
            continue
        for l in b.get("lines", []):
            for s in l.get("spans", []):
                if not (s.get("text") or "").strip():
                    continue
                sx0, sy0, sx1, sy1 = s["bbox"]
                # chỉ xét span nằm trong dải dọc của khung và có phần chồng ngang
                if sy1 <= r.y0 or sy0 >= r.y1 or sx1 <= r.x0 or sx0 >= r.x1:
                    continue
                if r.x0 - sx0 <= limit:
                    x0 = min(x0, sx0)
                if sx1 - r.x1 <= limit:
                    x1 = max(x1, sx1)
    return fitz.Rect(x0 - 1, r.y0, x1 + 1, r.y1) & page.rect


def _uncovered(page, boxes: list[tuple], figs: list[tuple], body: float) -> list[dict]:
    """Span trên trang này không rơi vào khung nào của mô hình bố cục.

    Bỏ ngay hai loại: span nằm trong vùng hình/bảng (đó là chữ trong hình, vốn
    phải biến mất khỏi mạch đọc), và span có cỡ chữ khác hẳn thân bài (số trang,
    header, nhãn trục).
    """
    out = []
    for b in page.get_text("dict")["blocks"]:
        if b.get("type") != 0:
            continue
        for l in b.get("lines", []):
            for s in l.get("spans", []):
                if not (s.get("text") or "").strip():
                    continue
                x0, y0, x1, y1 = s["bbox"]
                cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
                if any(r[0] <= cx <= r[2] and r[1] <= cy <= r[3] for r in boxes):
                    continue
                if any(r[0] <= cx <= r[2] and r[1] <= cy <= r[3] for r in figs):
                    continue
                if not body * 0.86 <= s["size"] <= body * 1.16:
                    continue
                out.append(s)
    return out


def _loose_paras(spans: list[dict], page_width: float) -> list[tuple]:
    """Gom span rơi vãi thành đoạn: **tách cột trước**, rồi mới dựng dòng.

    Thứ tự đó bắt buộc. `_rows()` gom theo baseline trên cả trang, nên ở bài hai
    cột, một dòng bên trái và một dòng bên phải cùng độ cao thành MỘT dòng; ghép
    lại theo trục x là chữ hai cột cài răng lược vào nhau:

        "…static evidence repre-  summarized as follows:  sentation, failing…"

    Đã ra đúng như vậy ở bản đầu.
    """
    if not spans:
        return []

    # Có tách được thành hai cột không? Phép kiểm đúng câu hỏi cần trả lời:
    # **không span nào vắt qua đường giữa**, và có chữ ở cả hai bên. Dùng
    # `_page_columns` ở đây thì sai — nó tính trên khung KHỐI, còn ở đây chỉ có
    # span rời rạc nên nó luôn đoán một cột.
    mid = page_width / 2
    slack = page_width * 0.02
    crosses = sum(1 for s in spans
                  if s["bbox"][0] < mid - slack < mid + slack < s["bbox"][2])
    left = sum(1 for s in spans if s["bbox"][2] <= mid)
    right = sum(1 for s in spans if s["bbox"][0] >= mid)
    # Ngưỡng theo TỈ LỆ, không phải "có hay không". Một span vắt qua đường giữa
    # là chuyện bình thường — số trang nằm chính giữa chân trang là đủ. Bản đầu
    # dùng `any()` nên đúng một cái `26182` tắt luôn phép tách cột cho cả trang,
    # và chữ hai cột lại cài răng lược.
    ncol = 1 if (crosses > max(2, len(spans) * 0.10) or not (left and right)) else 2

    def col_of(s):
        return 0 if ncol == 1 else (0 if (s["bbox"][0] + s["bbox"][2]) / 2 < mid else 1)

    paras: list[list[dict]] = []
    for col in range(max(1, ncol)):
        got = [s for s in spans if col_of(s) == col]
        boxed = []
        for r in (r for r in _rows(got) if r):
            boxed.append({
                "box": (min(s["bbox"][0] for s in r), min(s["bbox"][1] for s in r),
                        max(s["bbox"][2] for s in r), max(s["bbox"][3] for s in r)),
                "col": col, "spans": r,
            })
            boxed[-1]["h"] = boxed[-1]["box"][3] - boxed[-1]["box"][1]
        boxed.sort(key=lambda d: d["box"][1])

        for d in boxed:
            prev = paras[-1][-1] if paras and paras[-1][-1]["col"] == col else None
            # Cách nhau chưa tới hai dòng thì vẫn là một đoạn. Xa hơn nghĩa là đã
            # sang khối khác — nối vào là dính hai đoạn rời làm một.
            if prev and 0 <= d["box"][1] - prev["box"][3] <= max(prev["h"], d["h"]) * 1.6:
                paras[-1].append(d)
            else:
                paras.append([d])

    out = []
    for grp in paras:
        sp = [s for d in grp for s in d["spans"]]
        out.append(((min(d["box"][0] for d in grp), min(d["box"][1] for d in grp),
                     max(d["box"][2] for d in grp), max(d["box"][3] for d in grp)),
                    text_from_spans(sp)))
    return out


def recover_uncovered(doc, items: list[dict], regions: list[dict] | None) -> list[dict]:
    """Nhặt lại phần chữ mà mô hình bố cục bỏ sót, dựng thành khối `para`.

    **Đây là chỗ mất chữ âm thầm.** `assign_spans()` gán mỗi span vào khung nhỏ
    nhất chứa tâm nó; span không thuộc khung nào thì rơi ra ngoài và không ai
    nhặt. Nếu docling bỏ sót một vùng chữ — chuyện xảy ra thường xuyên với đoạn
    vắt qua ranh giới cột — thì cả đoạn đó **biến mất khỏi bài mà không có lỗi
    nào**. Đo trên bài CIRAG: **20,4% số span rơi ngoài**, trong đó có cả đoạn
    thân bài; người đọc thấy một đoạn đứt giữa chừng ở chữ "Current" rồi nhảy
    sang ý khác.

    Chỗ này không thể sửa bằng cách tin mô hình hơn. Cách sửa là **đừng vứt**:
    lấy phần rơi ngoài, loại chữ trong hình và chữ khác cỡ thân bài, gom lại
    thành đoạn, rồi thả vào `items` như một khối bình thường. Từ đó trở đi nó đi
    chung đường với mọi khối khác — sắp thứ tự đọc, gán span, dựng `Block`.

    Đặt lọc chặt tay hơn `_is_prose` một chút: khối nhặt lại chỉ nhận nếu trông
    như văn xuôi. Dương tính giả ở đây tệ hơn âm tính giả — nhặt nhầm một mảnh
    bảng vào giữa bài thì mạch đọc gãy, còn bỏ sót thì chỉ như hiện trạng.
    """
    figs_by_page: dict[int, list[tuple]] = {}
    for r in regions or []:
        figs_by_page.setdefault(r["page"], []).append(tuple(r["bbox"]))
    boxes_by_page: dict[int, list[tuple]] = {}
    for it in items:
        boxes_by_page.setdefault(it["page"], []).append(tuple(it["bbox"]))

    body = _body_size(doc)
    extra: list[dict] = []
    for pno in range(doc.page_count):
        loose = _uncovered(doc[pno], boxes_by_page.get(pno, []),
                           figs_by_page.get(pno, []), body)
        if not loose:
            continue
        for bbox, text in _loose_paras(loose, doc[pno].rect.width):
            text = clean_text(text)
            if len(text) < 60 or not _is_prose(text):
                continue
            extra.append({"kind": "para", "text": text, "marker": "", "level": 0,
                          "page": pno, "bbox": list(bbox), "recovered": True})
    return items + extra


# ==================================================== phễu lọc sau khi bóc
#
# Hai việc chạy trên danh sách `Block` đã dựng xong, nên dùng chung được cho cả
# đường docling lẫn đường heuristic. Cả hai đều nhắm vào **cùng một cái giá**:
# mỗi khối là một đơn vị dịch và một đơn vị giải thích, nên một mảnh vụn không
# gom lại là hai lượt gọi model trả cho thứ vô nghĩa.

# Từ bị ngắt gạch nối cuối dòng: `differ-` + `ent`. Không nhận gạch ngang dài
# (– —) và không nhận khi phần trước gạch quá ngắn — `e-` trong `e-mail` hay
# `w/o-` không phải từ bị ngắt.
_HYPHEN_END = re.compile(r"[A-Za-zÀ-ỹ]{2,}-$")
# Đoạn nối tiếp bắt đầu bằng chữ thường: chữ hoa là câu mới, không phải phần đuôi.
_CONT_LOWER = re.compile(r"^[a-zà-ỹ]")

# Khối chen giữa mà một đoạn bị cắt có thể nhảy qua. Hình, bảng, công thức
# thường được xếp lên đầu cột nên nằm CHÈN vào giữa câu.
_INTERLEAVED = ("caption", "equation", "figure", "table")


def stitch_hyphenated(blocks: list[Block], max_gap: int = 4) -> int:
    """Nối lại đoạn bị cắt giữa từ, kể cả khi có hình chen vào giữa.

    Ở bài hai cột, hình và bảng được xếp lên đầu cột nên chúng chen vào **giữa
    câu**. Đo trên bài CIRAG: 6 đoạn kết thúc bằng `compo-`, `differ-`, `sen-`,
    `other-`, `re-`, `oth-` — mỗi mảnh thành một khối riêng, được dịch riêng, và
    model tự ghi vào phần giải thích rằng *"câu gốc bị cắt ngay sau khi nói Bảng
    3, nên chưa cho biết cụ thể"*. Vừa tốn tiền hai lượt vừa cho ra bản dịch
    không thể đúng được.

    Mốc nhận biết là **gạch nối cuối khối + chữ thường mở đầu khối nối tiếp**.
    Cả hai điều kiện đều bắt buộc: chỉ gạch nối thì `w/o Triple + Sentence-`
    cũng khớp, chỉ chữ thường thì mọi đoạn bắt đầu bằng `the` đều bị dính vào
    đoạn trước.

    Nối **không chèn khoảng trắng** và bỏ luôn dấu gạch: `differ-` + `ent` phải
    ra `different`, không phải `differ- ent`.

    Trả về số cặp đã nối.
    """
    joined = 0
    i = 0
    while i < len(blocks):
        b = blocks[i]
        if b.type != "para" or b.hidden or not _HYPHEN_END.search(b.text.rstrip()):
            i += 1
            continue
        # tìm đoạn văn kế tiếp, cho phép nhảy qua vài khối hình/bảng/công thức
        j, hopped = i + 1, 0
        while j < len(blocks) and hopped < max_gap:
            nxt = blocks[j]
            if nxt.type == "para" and not nxt.hidden:
                break
            if nxt.type not in _INTERLEAVED:
                j = len(blocks)          # gặp heading/mục khác thì thôi, đừng vắt qua
                break
            j += 1
            hopped += 1
        if j >= len(blocks) or blocks[j].type != "para":
            i += 1
            continue
        nxt = blocks[j]
        if not _CONT_LOWER.match(nxt.text.lstrip()):
            i += 1
            continue

        b.text = b.text.rstrip()[:-1] + nxt.text.lstrip()
        nxt.hidden = True
        nxt.translate = False
        nxt.text = ""                    # đã dời hết chữ sang khối trước
        joined += 1
        # KHÔNG tăng `i`: đoạn vừa nối có thể lại kết thúc bằng gạch nối nữa
    blocks[:] = [x for x in blocks if x.text.strip() or x.figure]
    return joined


# --------------------------------------------------------- lọc khối nhiễu

_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]{2,}")
_ORCID = re.compile(r"\b\d{4}-\d{4}-\d{4}-\d{3}[\dX]\b")
_AFFIL = re.compile(
    r"\b(University|Institute|Laborator|Academy|College|School of|Department of"
    r"|Corresponding author|equal contribution|Our code|available at)\b", re.I)
# Khối chỉ gồm số, dấu chấm câu, ký hiệu — "57.3%", "(4) ...", "1 2 3"
_ONLY_NUMS = re.compile(r"^[\s\d.,%()\[\]{}·•±+\-–—*/:;'\"^_~$#@&|\\<>=]+$")


def mark_noise(blocks: list[Block]) -> int:
    """Tắt cờ dịch cho khối rác. **Không xoá** — người đọc bật lại được.

    Mỗi khối là một đơn vị dịch và một đơn vị giải thích. Một dòng `57.3%` lạc
    ra từ bảng, một dòng email tác giả, một chú thích `^{1}Our code can be found
    via github.com/…` — mỗi cái tốn hai lượt gọi model cho thứ không ai đọc.
    Đo trên bài CIRAG: 27 khối dưới 90 ký tự, trong đó có `57.3%` (5 ký tự) và
    `(4) ...` (7 ký tự).

    Tắt cờ chứ không xoá, vì ranh giới "rác" không bao giờ chắc chắn: một dòng
    ngắn toàn số có thể là kết quả chính của bài. Người đọc thấy khối vẫn nằm
    đúng chỗ, bật lại bằng nút ⊘ nếu cần — cùng lối với `hidden`.
    """
    hit = 0
    for b in blocks:
        if b.type not in ("para", "meta") or not b.translate or b.figure:
            continue
        t = b.text.strip()
        if not t:
            continue
        # Chữ trần: bỏ đánh dấu chỉ số trên/dưới, rồi bỏ luôn **dấu chú thích
        # chân trang ở đầu dòng**. Không bỏ thì `^{1}Our code…` thành `1Our
        # code…` — chữ số dính liền chữ cái nên `\bOur code\b` không còn khớp,
        # và cả dòng chú thích lọt lưới.
        bare = re.sub(r"[\^_]\{([^}]*)\}", r"\1", t)
        bare = re.sub(r"^[\s\d*†‡§¶.)\]]+", "", bare).strip()
        noise = (
            len(bare) < 12                                    # mảnh vụn
            or _ONLY_NUMS.match(bare)                         # chỉ toàn số
            or (_EMAIL.search(bare) and len(bare) < 220)      # dòng email tác giả
            or _ORCID.search(bare)
            or (_AFFIL.search(bare) and len(bare) < 200)      # cơ quan / chú thích chân
        )
        if noise:
            b.translate = False
            hit += 1
    return hit


def blocks_from_layout(items: list[dict], pdf_bytes: bytes,
                       eq_dpi: int = 200,
                       regions: list[dict] | None = None,
                       ) -> tuple[str, list[Block], dict[str, bytes]]:
    """Dựng danh sách Block từ cấu trúc do mô hình bố cục trả về.

    Phân công: **mô hình quyết định khối nào đứng đâu và là loại gì**, còn
    **PyMuPDF cấp glyph** trong đúng vùng đó. Nhờ vậy bỏ được gần hết phần đoán:
    thứ tự đọc, ranh giới đoạn, nhận nhãn mục/chú thích/công thức/danh sách, và
    loại header-footer chạy suốt bài — tất cả đều do mô hình lo.

    Công thức được **cắt thành ảnh** ngay tại đây. Toán hai chiều không dựng lại
    trung thực được từ toạ độ glyph — phân số, ngoặc nhiều tầng, ma trận đều mất
    hình dạng. Ảnh thì đúng y bản in. Chữ vẫn giữ trong `text` để tầng dịch có
    ngữ cảnh, nhưng người đọc nhìn ảnh.

    Ảnh của hình/bảng thì `apply_layout()` gắn sau, dùng chung `regions` của
    cùng một lần convert.
    """
    import fitz

    blocks: list[Block] = []
    eq_imgs: dict[str, bytes] = {}
    eq_jobs: list[tuple] = []
    n = 0

    def nid() -> str:
        nonlocal n
        n += 1
        return f"b{n}"

    section, title = "", ""
    in_refs = seen_abstract = False

    with fitz.open(stream=pdf_bytes, filetype="pdf") as doc:
        body = _body_size(doc)

        # Nhặt lại phần chữ mô hình bố cục bỏ sót TRƯỚC khi sắp thứ tự đọc, để
        # khối nhặt được đi chung đường với mọi khối khác. Xem `recover_uncovered`.
        items = recover_uncovered(doc, items, regions)

        # Ranh giới khối của mô hình thì đáng tin, nhưng thứ tự đọc của nó thì
        # không phải lúc nào cũng đúng — nó hay dồn cả cụm công thức xuống cuối.
        # Khối đã không còn bị xé vụn nên sắp theo cột rồi theo chiều dọc là chắc.
        for pno in {it["page"] for it in items}:
            if not 0 <= pno < len(doc):
                continue
            pw = doc[pno].rect.width
            ncol = _page_columns([it["bbox"] for it in items if it["page"] == pno], pw)
            for it in items:
                if it["page"] == pno:
                    cx = (it["bbox"][0] + it["bbox"][2]) / 2
                    it["_col"] = 0 if ncol == 1 else (0 if cx < pw / 2 else 1)
        items = sorted(items, key=lambda it: (it["page"], it.get("_col", 0),
                                              round(it["bbox"][1], 1), it["bbox"][0]))

        # chia span của từng trang về đúng khối, một lần cho cả trang
        by_page: dict[int, list[int]] = {}
        for i, it in enumerate(items):
            if 0 <= it["page"] < len(doc):
                by_page.setdefault(it["page"], []).append(i)
        spans_of: dict[int, list[dict]] = {}
        for pno, idxs in by_page.items():
            boxes = _trim_overlaps([items[i]["bbox"] for i in idxs])
            for i, sp in zip(idxs, assign_spans(doc[pno], boxes)):
                spans_of[i] = sp

        # tiêu đề: khối chữ to nhất trong vài khối đầu trang 1
        best, best_sz = None, 0.0
        for i, it in enumerate(items):
            if it["page"] != 0 or i > 14:
                continue
            sz = max((s["size"] for s in spans_of.get(i, [])), default=0.0)
            if sz > best_sz:
                best, best_sz = i, sz
        if best is not None and best_sz > body * 1.25:
            title = text_from_spans(spans_of[best])

        for i, it in enumerate(items):
            pno = it["page"]
            if not 0 <= pno < len(doc):
                continue
            text = text_from_spans(spans_of.get(i, [])) or clean_text(it.get("text") or "")
            if len(text) < 2 or text == title:
                continue
            kind = it["kind"]

            if kind == "title":
                title = title or text
                continue

            # Phần đầu trang 1 kết thúc ở Abstract, hoặc ở mục được đánh số đầu
            # tiên với bài không có chữ "Abstract".
            if text.lower().lstrip().startswith("abstract") or (
                kind == "heading" and _NUM_HEADING.match(text)
            ):
                seen_abstract = True
            # Trước đó toàn là tên tác giả / cơ quan / email — giữ để đọc, không
            # dịch. Xét trước cả heading: mô hình hay gán nhãn mục cho dòng tác
            # giả in đậm, và `_AUTHORY` không bắt được dòng chỉ có tên người.
            if not seen_abstract and pno == 0 and kind != "caption":
                blocks.append(Block(nid(), "meta", text, "", 0, 0, False))
                continue

            if kind == "heading":
                if _REF_START.match(text):
                    in_refs = True
                elif in_refs and _is_appendix_head(text):
                    in_refs = False          # phụ lục: quay lại nội dung thật
                # Số mục nói cấp chính xác hơn nhãn của mô hình: "3.1" là cấp 2.
                m = _NUM_LEVEL.match(text)
                lvl = (m.group(1).count(".") + 1) if m else (it["level"] or 1)
                section = text
                blocks.append(Block(nid(), "heading", text, text, min(lvl, 3), pno, not in_refs))
                continue

            if in_refs or kind == "reference":
                blocks.append(Block(nid(), "reference", text, "References", 0, pno, False))
                continue

            if kind == "caption":
                blocks.append(Block(nid(), "caption", text, section, 0, pno, True))
                continue

            if kind in ("equation", "code"):
                b = Block(nid(), "equation", text, section, 0, pno, False)
                # Cắt theo đúng những span đã thuộc về khối này, không theo khung
                # của mô hình: khung ấy đã bị `_trim_overlaps` cắt bớt, mà dấu
                # ngoặc nhiều tầng thì thò ra ngoài phần bị cắt.
                sp = spans_of.get(i) or []
                if sp:
                    r = fitz.Rect(min(s["bbox"][0] for s in sp) - 3,
                                  min(s["bbox"][1] for s in sp) - 3,
                                  max(s["bbox"][2] for s in sp) + 3,
                                  max(s["bbox"][3] for s in sp) + 3) & doc[pno].rect
                    # Khung cắt KHÔNG BAO GIỜ được cắt đôi một chữ. Khung dựng
                    # từ span của riêng khối này, nên khi một mảnh dòng văn bên
                    # cạnh lọt vào khối thì mép trái rơi vào giữa từ: ảnh hiện ra
                    # "ere at step t…" thay vì "where at step t…". Nới khung ra
                    # cho ôm trọn mọi span mà nó chạm phải — thà thừa một chữ
                    # còn hơn thiếu nửa chữ, và người đọc còn nút ✂ để chỉnh.
                    r = _widen_to_glyphs(doc[pno], r)
                    if not r.is_empty and r.height >= 8:
                        # Khoá theo toạ độ + chữ, KHÔNG theo id() đối tượng:
                        # bản sao tài liệu sinh ra span mới hoàn toàn, so bằng
                        # id() thì không khớp cái nào và tô trắng cả công thức.
                        mine = {_span_key(s) for s in sp}
                        eq_jobs.append((b, pno, r, mine))
                        b.figure_page = pno
                        b.figure_rect = [round(v, 1) for v in r]
                        b.figure_source = "model"
                blocks.append(b)
                continue

            if kind == "footnote":
                blocks.append(Block(nid(), "meta", text, section, 0, pno, False))
                continue

            marker = it.get("marker") or ("•" if kind == "list_item" else "")
            blocks.append(Block(nid(), "para", text, section, 0, pno, True, marker=marker))

    # Cắt ảnh công thức: khung đúng bằng hộp bao những span thuộc về công thức,
    # nới thêm chút lề.
    #
    # Đã thử xoá chữ của khối bên cạnh lọt vào khung (draw_rect rồi redaction)
    # nhưng cả hai đều hỏng: dấu ngoặc nhiều tầng cao gần hai dòng nên nó CHỒNG
    # lên dòng văn bên cạnh, xoá dòng đó là mất luôn ngoặc — mà ngoặc mới là thứ
    # khó đọc nhất nếu thiếu. Thà để lọt một vệt chữ ở mép còn hơn mất ngoặc.
    for b, pno, r, _mine in eq_jobs:
        try:
            with fitz.open(stream=pdf_bytes, filetype="pdf") as work:
                eq_imgs[b.id] = render_rect(work[pno], r, dpi=eq_dpi)
            b.figure = b.id
        except Exception:  # noqa: BLE001
            b.figure_page, b.figure_rect = -1, None

    keep = [b for b in blocks if len(b.text) > 1 or b.figure]

    # Hai phễu chạy cuối cùng, sau khi mọi khối đã có loại và có ảnh: gom mảnh
    # bị cắt giữa từ, rồi tắt cờ dịch cho khối rác. Cả hai nhắm vào cùng một cái
    # giá — mỗi khối là MỘT lượt dịch cộng MỘT lượt giải thích, nên một mảnh vụn
    # không gom lại là hai lượt gọi model trả cho thứ không đọc được.
    stitch_hyphenated(keep)
    mark_noise(keep)

    ids = {b.id for b in keep}
    return title, keep, {k: v for k, v in eq_imgs.items() if k in ids}


# ---------------------------------------------------------------- plain text


def parse_text(raw: str) -> tuple[str, list[Block], dict[str, bytes]]:
    raw = raw.replace("\r\n", "\n")
    chunks = [c.strip() for c in re.split(r"\n\s*\n", raw) if c.strip()]
    blocks: list[Block] = []
    section = ""
    in_refs = False
    title = ""
    n = 0

    for i, c in enumerate(chunks):
        one = clean_text(c)
        n += 1
        bid = f"b{n}"
        if i == 0 and len(one) < 200 and not one.endswith("."):
            title = one
            continue
        if _REF_START.match(one):
            in_refs, section = True, one
            blocks.append(Block(bid, "heading", one, one, 1, 0, False))
            continue
        if in_refs:
            blocks.append(Block(bid, "reference", one, "References", 0, 0, False))
            continue
        is_head, lvl = _looks_like_heading(one, 1.0, False)
        if is_head:
            section = one
            blocks.append(Block(bid, "heading", one, one, lvl, 0, True))
            continue
        if _CAPTION.match(one):
            blocks.append(Block(bid, "caption", one, section, 0, 0, True))
            continue
        if _is_mathy(one):
            blocks.append(Block(bid, "equation", one, section, 0, 0, False))
            continue
        blocks.append(Block(bid, "para", one, section, 0, 0, True))

    stitch_hyphenated(blocks)
    mark_noise(blocks)
    return title, blocks, {}


# ---------------------------------------------------------------- arXiv

_ARXIV = re.compile(r"(\d{4}\.\d{4,5})(v\d+)?")


async def fetch_arxiv(url_or_id: str) -> tuple[str, bytes]:
    m = _ARXIV.search(url_or_id)
    if not m:
        raise ValueError("Không nhận ra mã arXiv (ví dụ hợp lệ: 1706.03762 hoặc arxiv.org/abs/1706.03762)")
    aid = m.group(0)
    pdf_url = f"https://arxiv.org/pdf/{aid}"
    async with httpx.AsyncClient(timeout=90, follow_redirects=True) as cl:
        r = await cl.get(pdf_url, headers={"User-Agent": "paper-reader-vi/1.0"})
        r.raise_for_status()
        return aid, r.content


async def fetch_pdf_url(url: str) -> bytes:
    async with httpx.AsyncClient(timeout=90, follow_redirects=True) as cl:
        r = await cl.get(url, headers={"User-Agent": "paper-reader-vi/1.0"})
        r.raise_for_status()
        return r.content


# ---------------------------------------------------------------- chunking


def chunk_blocks(blocks: list[Block], max_chars: int = 4500) -> list[list[Block]]:
    """Gom block thành mẻ dịch, ưu tiên không cắt ngang section.

    Mỗi mẻ đủ nhỏ để model dịch kỹ, đủ lớn để giữ mạch trong một section.
    """
    out: list[list[Block]] = []
    cur: list[Block] = []
    size = 0
    blocks = [b for b in blocks if not getattr(b, "hidden", False)]
    for b in blocks:
        if not b.translate:
            continue
        blen = len(b.text)
        starts_section = b.type == "heading" and b.level <= 1
        if cur and (size + blen > max_chars or (starts_section and size > max_chars * 0.4)):
            out.append(cur)
            cur, size = [], 0
        cur.append(b)
        size += blen
    if cur:
        out.append(cur)
    return out
