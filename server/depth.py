"""Chốt chặn ĐỘ SÂU — bắt câu nghe như giải thích nhưng không giải thích gì.

Dùng chung cho ba chỗ: bản tổng hợp kho survey, câu trả lời hỏi đáp, và slide.
Không phụ thuộc vào luồng nào, nên đặt ở cấp `server/`.

## Vì sao cần một chốt chặn riêng cho độ sâu

`content_kept()` chặn model **bịa nội dung**. `check_answer()` chặn nó **bịa số và
bịa nguồn**. Không cái nào chặn được kiểu hỏng phổ biến nhất và khó thấy nhất:
câu đúng sự thật, có trích dẫn đàng hoàng, mà **không mang thông tin nào**.

    "CIRAG dùng cơ chế construction-integration để cải thiện chất lượng truy hồi."

Câu này không sai. Nó cũng không nói gì: người đọc xong vẫn không biết cơ chế đó
làm gì, vì sao nó cải thiện, và khi nào nó hỏng. Nó chỉ **đặt tên** cho hiện
tượng rồi dừng lại.

Feynman gọi đúng chỗ này trong bài giảng về sách giáo khoa khoa học: một cuốn
viết *"năng lượng làm cho nó chuyển động"*. Câu đó không sai và cũng vô dụng —
thay chữ "năng lượng" bằng chữ "wakalixes" thì nghĩa không đổi chút nào. **Phép
thử: thay thuật ngữ bằng một từ vô nghĩa; nếu câu vẫn "đúng" như cũ thì nó chưa
giải thích gì.**

Ba nguyên tắc, dựng thành ba phép kiểm:

1. **Cơ chế, không phải nhãn.** Giải thích phải nói dữ liệu đi qua những bước
   nào và vì sao bước đó tạo ra kết quả ấy. `_mechanism_missing()`.
2. **Không câu độn.** Tiếng Việt học thuật có một kho cụm từ nghe trang trọng mà
   rỗng: "đóng vai trò quan trọng", "mang lại hiệu quả cao", "góp phần nâng cao".
   `_filler()`.
3. **Không giải thích vòng tròn.** Định nghĩa dùng lại chính từ đang định nghĩa.
   `_circular()`.

**Cảnh báo chứ không chặn**, như mọi chốt chặn khác trong repo này: đây là phép
đo gần đúng trên ngôn ngữ tự nhiên, và cắt mất một câu đúng thì tệ hơn hiện nó
kèm cờ. Nhưng cảnh báo phải neo đúng chỗ để người đọc soát được.
"""

from __future__ import annotations

import re
import unicodedata

# --------------------------------------------------------------- câu độn
#
# Cụm từ chỉ làm câu dài ra. Chúng khác "từ nối" ở chỗ: bỏ đi thì nghĩa không
# mất gì. Danh sách cố ý ngắn và chắc — thà bỏ sót còn hơn kêu oan, vì chốt chặn
# kêu oan vài lần là người dùng thôi đọc nó.
FILLER = (
    "đóng vai trò quan trọng", "đóng vai trò then chốt", "vô cùng quan trọng",
    "mang lại hiệu quả cao", "mang lại nhiều lợi ích", "đạt hiệu quả tốt",
    "góp phần nâng cao", "góp phần cải thiện", "góp phần quan trọng",
    "là một trong những", "có ý nghĩa quan trọng", "hết sức cần thiết",
    "đáng chú ý là", "nhìn chung có thể thấy", "có thể thấy rằng",
    "nói chung là", "về cơ bản thì", "trong bối cảnh hiện nay",
    "ngày càng trở nên", "không thể phủ nhận",
    "chưa xác định", "không rõ", "cần nghiên cứu thêm để làm rõ",
)

# Động từ "cải thiện" mà không nói cải thiện BẰNG CÁCH NÀO. Chúng không sai,
# chúng chỉ là chỗ mà lời giải thích lẽ ra phải bắt đầu.
VAGUE_VERBS = (
    "cải thiện", "nâng cao", "tăng cường", "tối ưu hoá", "tối ưu hóa",
    "đẩy mạnh", "hoàn thiện", "phát huy", "khắc phục hạn chế",
    "giải quyết vấn đề", "xử lý tốt hơn", "hiệu quả hơn",
)

# Dấu hiệu đang nói về CƠ CHẾ: quan hệ nhân quả, phương tiện, điều kiện, trình
# tự. Một câu tự nhận là giải thích mà không có dấu nào trong đây thì gần như
# chắc chắn nó chỉ đang đặt tên.
CAUSAL = (
    "vì", "bởi", "do đó", "nên ", "dẫn đến", "dẫn tới", "khiến", "làm cho",
    "bằng cách", "thông qua", "nhờ ", "nhờ đó", "kết quả là", "cho nên",
    "khi ", "nếu ", "trong khi", "thay vì", "trước hết", "sau đó", "cuối cùng",
    "từng bước", "lần lượt", "mỗi lần", "mỗi vòng", "tại mỗi",
    "tức là", "nghĩa là", "cụ thể", "ví dụ",
)

_WORD = re.compile(r"[0-9A-Za-zÀ-ỹà-ỹ]+", re.UNICODE)

# Từ chức năng — không tính khi đo trùng lặp giữa khái niệm và lời giải thích.
_STOP = {
    "là", "và", "của", "các", "những", "một", "có", "được", "cho", "với", "trong",
    "khi", "này", "đó", "thì", "mà", "để", "từ", "trên", "về", "như", "hay",
    "hoặc", "nếu", "vì", "nên", "đã", "sẽ", "cũng", "rất", "nhiều", "ít", "không",
    "the", "a", "an", "of", "and", "to", "in", "for", "on", "is", "are", "with",
}


def _norm(s: str) -> str:
    return unicodedata.normalize("NFC", (s or "")).lower()


def words(s: str) -> list[str]:
    return _WORD.findall(_norm(s))


def content_words(s: str) -> set[str]:
    return {w for w in words(s) if w not in _STOP and len(w) > 1}


# ------------------------------------------------------------ ba phép kiểm


def find_filler(text: str) -> list[str]:
    """Cụm rỗng có mặt trong câu."""
    low = _norm(text)
    return [f for f in FILLER if f in low]


def missing_mechanism(text: str, *, min_words: int = 12) -> bool:
    """Câu tự nhận là giải thích mà không có dấu hiệu nhân quả nào.

    Chỉ xét câu **đủ dài** — câu ngắn có thể là một khẳng định gọn gàng, và đòi
    quan hệ nhân quả ở đó là bắt viết dài dòng cho đủ hình thức.
    """
    w = words(text)
    if len(w) < min_words:
        return False
    low = _norm(text)
    if any(c in low for c in CAUSAL):
        return False
    # Số liệu cụ thể cũng là nội dung — một câu báo kết quả không cần nhân quả.
    return not re.search(r"\d", text)


def vague_claim(text: str) -> list[str]:
    """Động từ 'cải thiện' mà câu không nói bằng cách nào."""
    low = _norm(text)
    hits = [v for v in VAGUE_VERBS if v in low]
    if not hits:
        return []
    return [] if any(c in low for c in CAUSAL) else hits


def circular(term: str, explain: str, *, overlap: float = 0.6) -> bool:
    """Lời giải thích chỉ lặp lại chính khái niệm đang giải thích.

    Đo bằng tỉ lệ từ nội dung của `term` xuất hiện lại trong `explain`, và đòi
    `explain` không dài hơn nhiều — giải thích dài mà có nhắc lại tên thì bình
    thường, đó là cách viết mạch lạc. Vòng tròn là khi *gần như chỉ có* tên.
    """
    t, e = content_words(term), content_words(explain)
    if not t or not e:
        return False
    shared = len(t & e) / len(t)
    return shared >= overlap and len(e) <= len(t) * 2.5


# ------------------------------------------------------------- gộp lại


def check_text(text: str, *, label: str = "", term: str = "") -> list[dict]:
    """Soát một đoạn văn xuôi. Trả danh sách cảnh báo dạng {kind, msg, text}."""
    out: list[dict] = []
    text = (text or "").strip()
    if not text:
        return out

    if (f := find_filler(text)):
        out.append({"kind": "câu_độn",
                    "msg": f"{label + ': ' if label else ''}cụm rỗng — "
                           + ", ".join(f'"{x}"' for x in f[:3]),
                    "text": text[:160]})
    if (v := vague_claim(text)):
        out.append({"kind": "nói_chung_chung",
                    "msg": f"{label + ': ' if label else ''}“{v[0]}” mà không nói "
                           "bằng cách nào — đây là chỗ lời giải thích lẽ ra phải bắt đầu",
                    "text": text[:160]})
    elif missing_mechanism(text):
        out.append({"kind": "thiếu_cơ_chế",
                    "msg": f"{label + ': ' if label else ''}đủ dài để là một lời "
                           "giải thích nhưng không có quan hệ nhân quả nào, cũng không có số liệu",
                    "text": text[:160]})
    if term and circular(term, text):
        out.append({"kind": "vòng_tròn",
                    "msg": f"{label + ': ' if label else ''}lời giải thích chỉ lặp "
                           f"lại chính “{term[:40]}”",
                    "text": text[:160]})
    return out


# Luật viết, nhúng thẳng vào prompt. Một nguồn duy nhất cho cả ba pass, để tiêu
# chuẩn không trôi mỗi nơi một kiểu.
DEPTH_RULES = """\
## Tiêu chuẩn độ sâu

Feynman kể về một cuốn sách giáo khoa viết *"năng lượng làm cho nó chuyển
động"*. Câu đó không sai, và cũng vô dụng: thay chữ "năng lượng" bằng một từ vô
nghĩa thì câu vẫn "đúng" y như cũ.

**Phép thử bắt buộc cho mọi câu giải thích bạn viết:** thay thuật ngữ trong câu
bằng một từ vô nghĩa. Nếu câu vẫn nghe hợp lý thì bạn chưa giải thích gì — mới
chỉ đặt tên. Viết lại.

Bốn luật cụ thể:

1. **Cơ chế, không phải nhãn.** Nói dữ liệu đi qua những bước nào và **vì sao**
   bước đó tạo ra kết quả ấy.
   - Nông: *"CIRAG dùng cơ chế construction-integration để cải thiện truy hồi."*
   - Sâu: *"CIRAG dựng một mạng mệnh đề từ các đoạn lấy về, rồi cho chúng kích
     hoạt lẫn nhau qua vài vòng; mệnh đề nào không được mệnh đề nào khác đỡ thì
     tắt dần. Nhờ vậy đoạn nhiễu bị loại theo mức độ ăn khớp với phần còn lại,
     chứ không phải theo một ngưỡng điểm cố định."*

2. **Nói bằng cách nào, đừng chỉ nói tốt hơn.** Câu có "cải thiện", "nâng cao",
   "tăng cường", "tối ưu" mà không kèm cơ chế là câu chưa viết xong.

3. **Cụ thể trước, tổng quát sau.** Chỗ nào giải thích được bằng một ví dụ chạy
   tay thì đưa ví dụ đó ra: một truy vấn cụ thể, một con số cụ thể, một bước cụ
   thể. Trừu tượng mà không có chỗ bám thì người đọc gật đầu rồi quên ngay.

4. **Nói ra chỗ có thể sai.** Feynman: *"Nguyên tắc thứ nhất là đừng tự lừa mình
   — mà mình lại là người dễ lừa nhất."* Nêu điều kiện mà kết luận phụ thuộc
   vào, và nêu thứ sẽ chứng minh nó sai. Không có phần này thì bạn đang viết
   quảng cáo, không phải phân tích.

## Văn phong

Học thuật, logic, tiếng Việt tự nhiên. Câu đủ chủ vị. Thuật ngữ giữ tiếng Anh.

**Cấm hẳn** các cụm rỗng: "đóng vai trò quan trọng", "góp phần nâng cao",
"mang lại hiệu quả cao", "là một trong những", "đáng chú ý là", "nhìn chung có
thể thấy", "không thể phủ nhận". Bỏ chúng đi thì câu không mất nghĩa gì — nghĩa
là chúng chưa từng mang nghĩa nào.

Không có gì để nói thì **để trống**. Ô trống là thông tin thật; câu độn thì
không, và nó còn che mất chỗ trống đáng lẽ người đọc phải nhìn thấy.
"""
