"""Bộ slide trình bày — bản dựng lại từ đầu (v2).

Bản cũ hỏng ở ba chỗ, và bản này nhắm đúng ba chỗ đó:

- **Chán vì một khuôn.** 12/16 slide cùng dáng "tiêu đề + 3 thẻ pastel". Ở đây
  mỗi slide mang một VAI trong lập luận của bài — vấn đề, khoảng trống, thuộc
  tính cần có, ý tưởng cốt lõi, cơ chế, ví dụ chạy tay, bằng chứng, con số, giới
  hạn, đúc kết — và mỗi vai có MỘT bố cục riêng. Bộ slide đổi dáng theo đúng
  mạch bài, nên không lặp. Chuỗi vai lấy từ skill viết tài liệu kỹ thuật tiếng
  Việt: Vấn đề → Khoảng trống → Thuộc tính cần có → Ý tưởng cốt lõi → Cơ chế.
- **Đắt và rối vì nhiều bước.** Bản cũ: soạn dàn ý (tốn tiền) → duyệt → dựng
  slide theo mẻ (tốn tiền) → form sửa 7 ô. Ở đây: MỘT lượt gọi model đi sau
  `cached_prefix` (toàn văn bài đã nằm trong cache nếu vừa dịch), ra thẳng nội
  dung từng slide. Sửa ngay trên slide.
- **Ba bộ dựng lệch nhau.** Bản cũ vẽ slide ở ba nơi (app, file HTML, PowerPoint)
  và mỗi lần sửa phải sửa cả ba. Ở đây server CHỈ lo dữ liệu; vẽ slide là việc
  của một hàm JS duy nhất (`web/slide-ve.js`), dùng chung cho xem trước, trình
  chiếu và file tải về.

Slide mở đầu và slide lộ trình KHÔNG hỏi model: chúng dựng từ thông tin bài
(tác giả, năm, nơi đăng — `db.get_meta`) và từ chính các slide còn lại.
"""

from __future__ import annotations

import asyncio
import re
import time
import unicodedata

from . import db, llm, store
from .depth import DEPTH_RULES

# ---------------------------------------------------------------- vai & khuôn

# Vai → trường nội dung model phải viết. Thứ tự liệt kê cũng là thứ tự mạch
# trình bày chuẩn của một bài phương pháp.
VAI = {
    "van_de": "Vấn đề — tình huống thực tế và vì sao nó khó",
    "khoang_trong": "Khoảng trống — cách đang làm hỏng ở đâu, trong kịch bản nào",
    "yeu_cau": "Thuộc tính cần có — lời giải phải đạt những tiêu chí gì",
    "y_tuong": "Ý tưởng cốt lõi — trực giác giúp đạt các tiêu chí đó",
    "co_che": "Cơ chế — các bước chạy từ đầu vào tới đầu ra",
    "cong_thuc": "Công thức — biểu thức chính và vai trò từng thành phần",
    "vi_du": "Ví dụ chạy tay — một đầu vào cụ thể có thật trong bài, đi qua từng bước",
    "so_sanh": "Khác gì cách cũ — bảng đối chiếu các cách làm theo từng tiêu chí",
    "thiet_lap": "Thiết lập thí nghiệm — dữ liệu, đối chứng, thước đo",
    "bang_chung": "Bằng chứng — một hình/bảng trong bài và điều nó cho thấy",
    "so_lieu": "Con số chính — một kết quả kèm mốc so sánh",
    "gioi_han": "Giới hạn — tác giả tự nhận và điểm đáng ngờ",
    "dong_lai": "Đúc kết — ba điều mang về và một câu hỏi thảo luận",
    "doan_dich": "Trích đoạn — nguyên văn bản dịch của 1–3 đoạn trong bài, kèm ý chính",
}

# Bốn chặng của lộ trình — slide lộ trình và dấu "2/4 · Cơ chế" ở góc slide đều
# tính từ đây, không hỏi model.
CHANG = [
    ("Bài toán", ("van_de", "khoang_trong", "yeu_cau")),
    ("Cách làm", ("y_tuong", "co_che", "cong_thuc", "vi_du", "so_sanh")),
    ("Bằng chứng", ("thiet_lap", "bang_chung", "so_lieu")),
    ("Giới hạn & đúc kết", ("gioi_han", "dong_lai")),
]

# Số slide nội dung (không tính mở đầu và lộ trình) theo độ dài buổi nói.
# Bản đầu (8/11/14) bị chê sơ sài: cả phần phương pháp của CIRAG gói trong MỘT
# slide ba bước. Giờ mỗi thành phần có slide riêng, cộng công thức, thiết lập
# thí nghiệm và bảng đối chiếu — chừng một slide mỗi phút.
SO_SLIDE = {10: 10, 15: 14, 20: 18, 30: 28, 45: 42}

# Từ mức này trở lên là bộ CHI TIẾT: đi lần lượt từng mục của bài, phần lớn slide
# là trích đoạn nguyên văn bản dịch. Một lượt gọi cho 40+ slide vượt trần thời
# gian (V4 Pro: 205 giây cho 17 slide), nên chia hai bước BÊN TRONG — lên khung
# (chỉ mã đoạn, đầu ra ngắn) rồi viết chữ theo mẻ song song. Người dùng không
# phải duyệt gì ở giữa.
CHI_TIET_TU = 20

# Trường chữ của từng vai — để soát số liệu, gom chữ, và để PATCH biết trường
# nào người dùng được sửa.
_CHU = {
    "van_de": ("cau", "vi_du"),
    "khoang_trong": ("cach_cu", "hong", "he_qua"),
    "yeu_cau": (),
    "y_tuong": ("cau", "vi_sao"),
    "co_che": ("dan",),
    "cong_thuc": ("truc_giac", "bieu_thuc", "danh_doi"),
    "vi_du": ("dau_vao", "dau_ra"),
    "so_sanh": ("ket_luan",),
    "thiet_lap": ("mo_hinh_nen",),
    "bang_chung": ("doc_hinh", "ket_luan"),
    "so_lieu": ("gia_tri", "nhan", "moc", "y_nghia"),
    "gioi_han": (),
    "dong_lai": ("cau_hoi",),
    "doan_dich": ("y_chinh",),
}

SLIDE_TASK = """\
## Việc lúc này: viết nội dung cho bộ slide trình bày bài báo trên

Người trình bày là nghiên cứu sinh / kỹ sư, nói cho một nhóm đồng nghiệp người
Việt trong buổi seminar. Mỗi slide mang đúng MỘT vai trong lập luận của bài, và
vai quyết định bố cục — nên chọn vai cho đúng, đừng ép mọi thứ thành gạch đầu dòng.

### Các vai và trường phải viết

Trần số chữ ghi trong ngoặc là trần CỨNG: slide là thứ chiếu lên cho cả phòng
đọc, chữ thân không nhỏ hơn 24px, nên vượt trần là tràn khung. Phần giải thích
dài hơn thì đưa vào `loi_noi`, không nhồi lên mặt slide.

- `van_de` — `cau` (vấn đề và vì sao nó quan trọng, ≤45 chữ), `vi_du` (MỘT ví dụ
  cụ thể, ≤35 chữ).
- `khoang_trong` — `cach_cu` (cách đang làm, ≤30 chữ), `hong` (nó hỏng ở đâu,
  trong kịch bản nào, ≤35 chữ), `he_qua` (hệ quả đo được hoặc quan sát được, ≤25 chữ).
- `yeu_cau` — `tieu_chi`: 2–4 mục `{"ten": "3–7 chữ", "vi_sao": "≤22 chữ"}` — lời
  giải phải đạt gì để lấp khoảng trống. Đây là bước hay bị bỏ sót nhất.
- `y_tuong` — `cau` (trực giác cốt lõi trong MỘT câu, ≤30 chữ), `vi_sao` (vì sao
  trực giác ấy đáp ứng được các tiêu chí, ≤35 chữ).
- `co_che` — MỘT thành phần của phương pháp. `dan` (thành phần này nhận gì, trả
  ra gì, ≤25 chữ), `buoc`: 3–4 mục `{"ten": "2–5 chữ", "mo_ta": "≤25 chữ, nói
  bước này làm gì VÀ vì sao cần"}`. Gắn `hinh` nếu có hình vẽ thành phần ấy
  (khi đó tối đa 3 bước, mỗi `mo_ta` ≤20 chữ).
- `cong_thuc` — `hinh` (mã khối CÔNG THỨC trong danh mục công thức dưới; không có
  thì để rỗng và viết `bieu_thuc` bằng `x_{t}`, `x^{2}`), `truc_giac` (công thức
  này tính cái gì và vì sao cần nó, ≤30 chữ), `thanh_phan`: 2–5 mục
  `{"ky_hieu": "α", "y_nghia": "≤18 chữ, vai trò của nó, không chỉ tên"}`,
  `danh_doi` (tăng/giảm thành phần nào thì được gì mất gì, ≤25 chữ).
- `vi_du` — `dau_vao` (≤25 chữ), `buoc`: 3–4 mục `{"ten", "mo_ta": "≤18 chữ"}`
  cho biết đầu vào ấy biến đổi ra sao, `dau_ra` (≤15 chữ), `minh_hoa` (true/false).
  Gắn `hinh` thì tối đa 3 bước, `dau_vao` ≤18 chữ.
- `so_sanh` — `cot`: 3–4 cách làm (tên ngắn), cột CUỐI là phương pháp của bài.
  `hang`: 3–5 mục `{"tieu_chi": "3–7 chữ", "o": ["có", "không", "một phần", …]}`
  với `o` đúng bằng số cột, mỗi ô ≤4 chữ. Tiêu chí nên lấy từ slide `yeu_cau`.
  `ket_luan` (điều bảng cho thấy, ≤25 chữ). Chỉ ghi điều bài nói về các cách đó.
- `thiet_lap` — `du_lieu`: 2–4 mục `{"ten": "tên tập", "mo_ta": "≤15 chữ, loại câu
  hỏi/quy mô"}`, `doi_chung`: 2–6 tên baseline, `do_do`: 1–3 mục `{"ten": "EM",
  "y_nghia": "≤15 chữ, đo cái gì"}`, `mo_hinh_nen` (mô hình nền dùng, ≤15 chữ).
- `bang_chung` — `hinh` (BẮT BUỘC, mã khối của hình/bảng trong danh mục dưới),
  `doc_hinh` (cách đọc hình: trục/cột là gì, nhìn vào đâu, ≤28 chữ),
  `ket_luan` (điều hình cho thấy, ≤25 chữ), `so`: 0–2 mục
  `{"gia_tri": "61,4", "nhan": "F1 trên 2WikiMQA", "moc": "so với 57,1 của KiRAG"}`.
- `so_lieu` — `gia_tri` (một con số), `nhan` (đo cái gì), `moc` (so với gì: baseline,
  benchmark, mô hình nền), `y_nghia` (mức chênh ấy nói lên điều gì, ≤30 chữ),
  `so_sanh`: 2–5 mục `{"nhan": "KiRAG", "gia_tri": "57,1", "cua_bai": false}` để vẽ
  biểu đồ cột (mục của bài có `"cua_bai": true`). Chỉ dùng số có trong CHỮ của bài,
  cùng một thước đo, cùng một tập dữ liệu.
- `gioi_han` — `muc`: 2–4 mục `{"ten": "3–8 chữ", "he_qua": "≤25 chữ"}`.
- `doan_dich` — trích NGUYÊN VĂN bản dịch lên slide, dùng khi câu chữ của bài quan
  trọng (định nghĩa, bước của phương pháp, kết quả chính, giới hạn tác giả tự nêu).
  `doan`: 1–3 mã khối LIỀN NHAU trong cùng một mục (công cụ tự chép bản dịch của
  chúng vào slide, bạn KHÔNG chép lại chữ), `y_chinh` (điều cần nhớ từ đoạn này,
  ≤20 chữ, không lặp lại câu trong đoạn, không nhắc lại tiêu đề), `nhan_manh`: 1–3 cụm ≤12 chữ CHÉP
  NGUYÊN VĂN từ bản dịch của đoạn để tô sáng, `hinh` nếu đoạn nói về một hình.
- `dong_lai` — `y`: đúng 3 điều mang về (mỗi điều ≤25 chữ), `cau_hoi`: một câu hỏi
  thảo luận mở cho người nghe (≤30 chữ).

Mọi slide có thêm `tieu_de` (một câu KHẲNG ĐỊNH điều slide chứng minh, 6–14 chữ,
không phải nhãn chủ đề, KHÔNG có dấu hai chấm), `nguon` (mã các khối trong bài
làm căn cứ), và `loi_noi` (lời người trình bày nói ở slide này, 60–110 chữ, nói
tự nhiên — đây là chỗ cho phần giải thích dài).

### Trung thực về ví dụ và con số (bắt buộc)

- Bạn KHÔNG nhìn thấy nội dung ảnh của hình/bảng, chỉ thấy chú thích của nó. Vì
  vậy MỌI con số phải lấy từ CHỮ của bài. Không đọc số "từ bảng", không tự tính
  hiệu hai số rồi ghi như số của bài.
- Ví dụ (`van_de.vi_du`, slide `vi_du`) phải là ví dụ CÓ TRONG CHỮ của bài. Bài
  có ví dụ dạng bảng "Case Study" thì gắn bảng đó vào `hinh` và mô tả các bước ở
  mức cơ chế, không bịa tên, số, thực thể cụ thể của bảng.
- Bài không có ví dụ bằng chữ mà vẫn cần minh hoạ thì được tự dựng một ví dụ
  ĐƠN GIẢN, nhưng phải đặt `"minh_hoa": true` (slide sẽ ghi rõ "minh hoạ, không
  lấy từ bài") và không gắn tên tập dữ liệu nào cho nó. Ví dụ lấy từ bài thì
  `"minh_hoa": false`.

### Mạch và nhịp

- Đi theo thứ tự: vấn đề → khoảng trống → thuộc tính cần có → ý tưởng cốt lõi →
  cơ chế → (ví dụ chạy tay) → bằng chứng → giới hạn → đúc kết. Slide `dong_lai`
  luôn là slide cuối.
- Hai slide liền nhau KHÔNG cùng vai, trừ `bang_chung` (tối đa 3 liền nhau).
- Phần phương pháp là phần chính của buổi nói, đừng nén nó. Phương pháp có nhiều
  thành phần thì MỖI thành phần một slide `co_che` (tối đa 4), mỗi thành phần
  có công thức quan trọng thì thêm một slide `cong_thuc` ngay sau nó. Rồi một
  slide `vi_du` đi qua cả hệ thống.
- Bộ nào cũng có `thiet_lap` đứng trước slide bằng chứng đầu tiên — không biết
  dữ liệu, đối chứng và thước đo thì người nghe không đọc được bảng kết quả.
  Có `so_sanh` khi bài đối chiếu với các hướng làm trước.
- Dùng hình nhiều: ít nhất MỘT NỬA số slide gắn `hinh` khi danh mục đủ hình.
  Mỗi hình chỉ gắn MỘT slide trong cả bộ.
  `van_de` gắn hình minh hoạ bài toán (thường là Figure 1), `y_tuong` gắn hình
  tổng quan, `co_che` gắn hình của thành phần, `vi_du` gắn bảng Case Study.
- Có ít nhất một `co_che` hoặc `vi_du` đi hết cơ chế bằng ví dụ thật — người
  nghe phải kể lại được cách nó chạy, không chỉ cái tên.
- Bằng chứng: ưu tiên bảng so sánh có baseline, rồi ablation, rồi biểu đồ phân
  tích. Mỗi hình chỉ dùng một lần. KHÔNG gắn hình nếu chú thích của nó không nói
  đúng điều tiêu đề khẳng định.

### Văn phong (bắt buộc)

- Tiếng Việt học thuật, đủ chủ vị. Thuật ngữ đã giữ tiếng Anh trong bảng thuật
  ngữ thì giữ nguyên; viết tắt (ICI, KD…) phải được gọi đủ tên ở lần đầu xuất hiện.
- KHÔNG dùng dấu chấm phẩy. KHÔNG dùng dấu hai chấm để cắt đôi câu, kể cả
  trong tiêu đề ("Hai điểm mù: A và B" → "Hai điểm mù là A và B"). KHÔNG dùng
  "tức", "tức là", "nghĩa là" để vá phần giải thích. KHÔNG ẩn dụ, không từ đệm
  ("rõ ràng", "vô cùng", "hoàn toàn").
- Mọi con số phải có mặt trong bài, và đi kèm mốc so sánh (baseline, benchmark,
  mô hình nền). Phân biệt điểm phần trăm với phần trăm tương đối. Không dùng
  "chứng minh" khi bằng chứng chỉ ở mức "cho thấy".
- Ký hiệu toán viết bằng lời trong câu; nếu bắt buộc thì dùng `x_{t}`, `x^{2}`,
  `h_{t+1}` (luôn có ngoặc nhọn), không LaTeX.

""" + DEPTH_RULES + """

### Đầu ra

Chỉ trả về JSON, không giải thích:
{"slides": [{"vai": "van_de", "tieu_de": "…", "cau": "…", "vi_du": "…",
  "hinh": "b9", "nguon": ["b12"], "loi_noi": "…"}, …]}
"""


def _danh_muc_hinh(doc: dict) -> list[dict]:
    """Hình/bảng có ảnh thật, theo MÃ KHỐI (model thấy bài dưới dạng `<<<b94>>>`)."""
    tr = doc.get("translations") or {}
    out = []
    for b in doc["blocks"]:
        if b.get("type") == "equation" or not b.get("figure"):
            continue
        if store.image_path(doc["id"], b["figure"]) is None:
            continue
        out.append({"id": b["id"], "trang": (b.get("page") or 0) + 1,
                    "chu_thich": (tr.get(b["id"]) or b.get("text") or "")[:220]})
    return out


def _danh_muc_cong_thuc(doc: dict) -> list[dict]:
    """Công thức hiển thị có ảnh cắt sẵn — cho vai `cong_thuc`."""
    out = []
    for b in doc["blocks"]:
        if b.get("type") != "equation" or not b.get("figure") or b.get("hidden"):
            continue
        if store.image_path(doc["id"], b["figure"]) is None:
            continue
        out.append({"id": b["id"], "trang": (b.get("page") or 0) + 1,
                    "chu": " ".join((b.get("text") or "").split())[:140]})
    return out


def _user(doc: dict, so: int) -> str:
    br = doc.get("brief") or {}
    hinh = _danh_muc_hinh(doc)
    ct = _danh_muc_cong_thuc(doc)
    chuoi = "\n".join(f"- [{x.get('role', '')}] {x.get('step', '')}"
                      for x in br.get("argument_chain") or [])
    dm = "\n".join(f"- {h['id']} (trang {h['trang']}): {h['chu_thich']}" for h in hinh) \
        or "(bài không có hình/bảng nào cắt được — dùng vai không cần hình)"
    return (f"Viết khoảng {so} slide nội dung (chưa tính slide mở đầu và lộ trình — "
            "hai slide đó công cụ tự dựng).\n\n"
            f"## Mạch lập luận đã chốt của bài\n{chuoi or '(chưa có)'}\n\n"
            f"## Danh mục hình/bảng dùng được (mã khối · trang · chú thích)\n{dm}\n\n"
            "## Danh mục công thức có ảnh (mã khối · trang · chữ bóc được, có thể vỡ)\n"
            + ("\n".join(f"- {c['id']} (trang {c['trang']}): {c['chu']}" for c in ct)
               or "(không có — slide `cong_thuc` viết `bieu_thuc` bằng chữ)") + "\n")


# ---------------------------------------------------------------- chuẩn hoá

def _sach(t) -> str:
    """Áp luật văn phong kiểu cơ học: bỏ dấu chấm phẩy (skill cấm trong văn xuôi),
    gộp khoảng trắng, viết hoa chữ đầu (trừ từ có chữ hoa giữa như `iRAG`)."""
    from .pipeline import _viet_hoa_dau

    t = unicodedata.normalize("NFC", " ".join(str(t or "").split()))
    # Dấu tổ hợp lơ lửng (không đứng sau chữ cái): đã gặp "632 ± ́251" — model
    # chèn U+0301 sau dấu cách, hiện thành dấu sắc lạc giữa con số.
    t = re.sub(r"(?<![^\W\d_])[\u0300-\u036f]+", "", t)
    t = re.sub(r"\s*;\s*(\S)", lambda m: ". " + m.group(1).upper(), t)
    # Dấu câu thừa ở đầu: model viết `"y_chinh": ": Trong môi trường ảo…"` (12/62
    # slide trích đoạn trên World Models), hiện ra dấu hai chấm lơ lửng đầu dòng.
    t = re.sub(r"^[\s:;,.\-–—]+", "", t)
    return _viet_hoa_dau(t)


def _chu_slide(s: dict) -> str:
    """Toàn bộ chữ hiện trên mặt slide — để soát số liệu."""
    parts = [s.get("tieu_de", "")] + [s.get(k, "") for k in _CHU.get(s.get("vai"), ())]
    for k in ("tieu_chi", "buoc", "muc", "du_lieu", "do_do", "thanh_phan", "so", "so_sanh"):
        for it in s.get(k) or []:
            parts += [str(v) for v in it.values() if isinstance(v, str)]
    for it in s.get("hang") or []:
        parts += [it.get("tieu_chi", "")] + list(it.get("o") or [])
    parts += list(s.get("y") or []) + list(s.get("cot") or []) + list(s.get("doi_chung") or [])
    return " ".join(str(p) for p in parts if p)


def _chu_khoi(doc: dict, bid: str) -> tuple[str, bool]:
    """Bản dịch của một khối; chưa dịch thì bản gốc (cờ thứ hai = là bản gốc)."""
    vi = ((doc.get("translations") or {}).get(bid) or "").strip()
    if vi:
        return vi, False
    b = next((x for x in doc["blocks"] if x["id"] == bid), None)
    return ((b or {}).get("text") or "").strip(), True


def _trich(doc: dict, s: dict) -> tuple[list[dict], list[str]]:
    """Dựng phần trích nguyên văn cho slide `doan_dich` từ mã khối model chọn.

    Đây là toàn bộ lý do của vai này: chữ trên slide là BẢN DỊCH đã trả tiền, nên
    không có tầng tóm tắt nào làm rơi ý. Model chỉ chọn đoạn nào.
    """
    by_id = {b["id"]: b for b in doc["blocks"]}
    ids = [str(x) for x in (s.get("doan") or []) if str(x) in by_id
           and by_id[str(x)].get("type") in ("para", "caption", "list", "table", "heading")
           and not by_id[str(x)].get("hidden")][:4]
    ids.sort(key=lambda i: doc["blocks"].index(by_id[i]))
    trich, canh = [], []
    for i in ids:
        chu, goc = _chu_khoi(doc, i)
        if chu:
            trich.append({"id": i, "chu": chu})
            if goc:
                canh.append("Đoạn này chưa dịch nên slide hiện bản gốc — dịch đoạn đó rồi sửa lại.")
    return trich, canh


def chuan_hoa(doc: dict, s: dict) -> dict | None:
    """Dọn một slide model trả về. Trả None nếu vai không hợp lệ.

    Ghi cảnh báo vào `canh_bao` (cảnh báo chứ không chặn — người dùng có màn hình
    để tự sửa, và cắt mất một slide còn tệ hơn hiện nó kèm cờ).
    """
    from .pipeline import so_bia

    vai = s.get("vai")
    if vai not in VAI:
        return None
    out = {"vai": vai, "tieu_de": _sach(s.get("tieu_de")).rstrip(".")}
    for k in _CHU[vai]:
        out[k] = _sach(s.get(k))
    for k, truong in (("tieu_chi", ("ten", "vi_sao")), ("buoc", ("ten", "mo_ta")),
                      ("muc", ("ten", "he_qua")), ("so", ("gia_tri", "nhan", "moc")),
                      ("du_lieu", ("ten", "mo_ta")), ("do_do", ("ten", "y_nghia")),
                      ("thanh_phan", ("ky_hieu", "y_nghia"))):
        if isinstance(s.get(k), list):
            out[k] = [{f: _sach(it.get(f)) if f != "ky_hieu" else " ".join(str(it.get(f) or "").split())
                       for f in truong}
                      for it in s[k][:5] if isinstance(it, dict) and any(it.get(f) for f in truong)]
    if vai == "so_lieu" and isinstance(s.get("so_sanh"), list):
        out["so_sanh"] = [{"nhan": _sach(it.get("nhan")), "gia_tri": _sach(it.get("gia_tri")),
                           "cua_bai": bool(it.get("cua_bai"))}
                          for it in s["so_sanh"][:5] if isinstance(it, dict) and it.get("gia_tri")]
    if vai == "thiet_lap":
        out["doi_chung"] = [_sach(x) for x in (s.get("doi_chung") or []) if str(x).strip()][:6]
    if vai == "so_sanh":
        cot = [_sach(x) for x in (s.get("cot") or []) if str(x).strip()][:4]
        hang = []
        for it in (s.get("hang") or [])[:5]:
            if isinstance(it, dict) and it.get("tieu_chi"):
                o = [_sach(x) for x in (it.get("o") or [])][:len(cot)]
                hang.append({"tieu_chi": _sach(it["tieu_chi"]), "o": o + [""] * (len(cot) - len(o))})
        out["cot"], out["hang"] = cot, hang
    if vai == "dong_lai":
        out["y"] = [_sach(x) for x in (s.get("y") or []) if str(x).strip()][:3]
    if vai in ("van_de", "vi_du"):
        out["minh_hoa"] = bool(s.get("minh_hoa"))
    if vai == "doan_dich":
        # Trích đoạn đã dựng sẵn (viết lại một slide, lượt viết chữ của bộ chi tiết)
        # thì giữ nguyên; chưa có thì dựng từ mã đoạn model chọn.
        if isinstance(s.get("trich"), list) and s["trich"]:
            out["trich"] = [{"id": str(t.get("id", "")), "chu": str(t.get("chu", ""))}
                            for t in s["trich"] if isinstance(t, dict) and t.get("chu")]
            canh_trich = []
        else:
            out["trich"], canh_trich = _trich(doc, s)
        if not out["trich"]:
            return None
        if s.get("tiep"):
            out["tiep"] = list(s["tiep"])[:2]
        toan = unicodedata.normalize("NFC", " ".join(t["chu"] for t in out["trich"]))
        out["nhan_manh"] = [p for p in (" ".join(str(x).split()) for x in (s.get("nhan_manh") or []))
                            if 2 < len(p) <= 140 and unicodedata.normalize("NFC", p) in toan][:3]
    out["loi_noi"] = " ".join(str(s.get("loi_noi") or "").split())

    by_id = {b["id"]: b for b in doc["blocks"]}
    by_anh = {b.get("figure"): b["id"] for b in doc["blocks"] if b.get("figure")}
    canh = []
    h = str(s.get("hinh") or "").strip()
    if h:
        # Model nói mã KHỐI; lỡ nói mã ẢNH thì quy về khối của ảnh đó.
        h = h if by_id.get(h, {}).get("figure") else by_anh.get(h, "")
        la_ct = bool(h) and by_id[h].get("type") == "equation"
        if not h or la_ct != (vai == "cong_thuc"):
            canh.append("Hình model chọn không có trong bài — đã bỏ." if not h else
                        "Model gắn nhầm loại hình (công thức ↔ hình/bảng) — đã bỏ.")
            h = ""
    out["hinh"] = h
    if vai == "bang_chung" and not h:
        canh.append("Slide bằng chứng chưa có hình — bấm vào khung để chọn hình trong bài.")
    out["nguon"] = [str(x) for x in (s.get("nguon") or []) if str(x) in by_id][:8]
    if vai == "doan_dich":
        out["nguon"] = list(dict.fromkeys([t["id"] for t in out["trich"]] + out["nguon"]))[:8]
        canh += canh_trich

    # Chip số trên slide bằng chứng: số không có ở đâu trong bài thì BỎ chip, không
    # chỉ cảnh báo. Model không thấy ảnh bảng nên hay "đọc" số từ bảng — đo trên
    # CIRAG: chip "10.1 (61.4 xuống 51.3)" trong khi bảng ghi 68.1 → 59.7. Chip là
    # thứ to và xanh nhất slide, để số bịa ở đó là gán kết quả giả cho tác giả thật.
    bo_chip = []
    for it in list(out.get("so") or []):
        bia = so_bia(doc, out["nguon"], " ".join(it.values()))
        if bia:
            out["so"].remove(it)
            bo_chip += bia
    if bo_chip:
        canh.append("Đã bỏ ô số không có trong chữ của bài: " + ", ".join(bo_chip) + ".")

    bia = so_bia(doc, out["nguon"], _chu_slide(out))
    if bia:
        canh.append("Số không có ở đâu trong bài: " + ", ".join(bia) + ".")
    out["canh_bao"] = canh
    return out


def _sap_lai(bo: list[dict]) -> list[dict]:
    """Giữ `dong_lai` ở cuối, và tách hai slide liền nhau cùng vai (trừ bằng chứng)
    bằng cách đẩy slide sau xuống chỗ hợp lệ gần nhất."""
    cuoi = [s for s in bo if s["vai"] == "dong_lai"][-1:]
    # Sắp ỔN ĐỊNH theo chặng: slide lộ trình gom vai theo `CHANG`, nên một slide
    # con số đứng sau slide giới hạn (đã gặp trên bài World Models) làm lộ trình
    # nói một đằng mà thứ tự chiếu một nẻo. Trong cùng chặng giữ thứ tự của model.
    chang = {v: i for i, (_, vs) in enumerate(CHANG) for v in vs}
    # Trích đoạn không thuộc chặng nào: nó đi theo slide đứng TRƯỚC nó trong thứ
    # tự model viết (đoạn trích minh hoạ cho slide ấy), không bị đẩy xuống cuối.
    khoa, cu = {}, 0
    for s in bo:
        cu = chang.get(s["vai"], cu)
        khoa[id(s)] = cu
    # `thiet_lap` luôn đứng đầu chặng Bằng chứng: chưa biết dữ liệu và thước đo
    # thì không đọc được bảng kết quả.
    con = sorted((s for s in bo if s["vai"] != "dong_lai"),
                 key=lambda s: (khoa[id(s)], s["vai"] != "thiet_lap"))
    for i in range(1, len(con)):
        # Cơ chế liền nhau là chủ ý (mỗi thành phần một slide, công thức bám ngay
        # sau thành phần của nó), trích đoạn liền nhau cũng vậy (đoạn văn liền
        # nhau trong bài) — tráo là phá đúng thứ tự ấy.
        if con[i]["vai"] == con[i - 1]["vai"] and con[i]["vai"] not in ("bang_chung", "co_che", "doan_dich"):
            # Chỉ tráo TRONG cùng chặng — tráo qua chặng là phá thứ tự vừa sắp.
            for j in range(i + 1, len(con)):
                if khoa[id(con[j])] != khoa[id(con[i])]:
                    break
                if con[j]["vai"] != con[i - 1]["vai"]:
                    con[i], con[j] = con[j], con[i]
                    break
    return con + cuoi


# Một hình gắn nhiều slide thì giữ ở slide cần nó nhất. Đã gặp trên CIRAG: sơ đồ
# tổng quan gắn cả slide ý tưởng lẫn hai slide cơ chế — ba slide liền cùng một
# hình. Slide ý tưởng đứng TRƯỚC nên "giữ chỗ đầu" là giữ nhầm chỗ.
_UU_TIEN_HINH = {"bang_chung": 0, "co_che": 1, "vi_du": 2, "cong_thuc": 3, "van_de": 4, "y_tuong": 5}


def _mot_hinh_mot_cho(bo: list[dict]) -> None:
    giu: dict[str, int] = {}
    for i, s in enumerate(bo):
        h = s.get("hinh")
        if not h:
            continue
        j = giu.get(h)
        if j is None or _UU_TIEN_HINH.get(s["vai"], 9) < _UU_TIEN_HINH.get(bo[j]["vai"], 9):
            giu[h] = i
    for i, s in enumerate(bo):
        if s.get("hinh") and giu[s["hinh"]] != i:
            s["hinh"] = ""
            if s["vai"] == "bang_chung":
                s.setdefault("canh_bao", []).append(
                    "Hình này đã dùng ở slide khác — bấm vào khung để chọn hình khác.")


def _soat_ca_bo(bo: list[dict]) -> None:
    """Phép kiểm mà từng slide không thấy được: bộ slide về bài phương pháp mà
    không có slide nào đi hết cơ chế thì người nghe nắm được bài toán và kết quả
    nhưng không kể lại được cách nó chạy. Cảnh báo gắn vào slide đầu tiên."""
    if bo and not any(s["vai"] in ("co_che", "vi_du") and (s.get("buoc") or [])
                      for s in bo):
        bo[0].setdefault("canh_bao", []).append(
            "Bộ slide chưa có slide nào đi qua từng bước của cơ chế. Bấm “Viết lại” "
            "ở một slide cách làm, hoặc tạo lại cả bộ.")


# Trần chữ của MỘT slide trích đoạn — chữ thân ~24px trên khung 1280, vượt là tràn.
# Đo trên World Models: 620 ký tự chỉ lấp nửa khung, 650 ký tự kèm ý chính hai
# dòng thì vừa đầy — 850 là sát mép mà chưa tràn.
TRAN_TRICH = 850
TRAN_TRICH_HINH = 450
_CAU = re.compile(r"(?<=[.!?…])(?<!\bal\.)(?<!\bet\.)\s+(?=\S)")


def _tach_trich(bo: list[dict]) -> list[dict]:
    """Trích đoạn quá dài thì tách sang slide kế tiếp ở RANH GIỚI CÂU ("phần 1/2").

    Tách chứ không cắt: cắt là bỏ mất đúng phần ý mà vai này sinh ra để giữ.
    Hình, ý chính và lời nói ở lại phần đầu; cụm tô sáng đi theo phần chứa nó.
    """
    out = []
    for s in bo:
        tran = TRAN_TRICH_HINH if s.get("hinh") else TRAN_TRICH
        if s["vai"] != "doan_dich" or sum(len(t["chu"]) for t in s["trich"]) <= tran:
            out.append(s)
            continue
        cau = [(t["id"], c) for t in s["trich"] for c in _CAU.split(t["chu"]) if c.strip()]
        phan, cur, n = [], [], 0
        for bid, c in cau:
            if cur and n + len(c) > (tran if not phan else TRAN_TRICH):
                phan.append(cur)
                cur, n = [], 0
            cur.append((bid, c))
            n += len(c) + 1
        if cur:
            phan.append(cur)
        for k, p in enumerate(phan):
            trich = []
            for bid, c in p:
                if trich and trich[-1]["id"] == bid:
                    trich[-1]["chu"] += " " + c
                else:
                    trich.append({"id": bid, "chu": c})
            moi = {**s, "trich": trich, "tiep": [k + 1, len(phan)],
                   "canh_bao": list(s.get("canh_bao") or []) if k == 0 else []}
            if k:
                moi.update(hinh="", y_chinh="", loi_noi="")
            toan = " ".join(t["chu"] for t in trich)
            moi["nhan_manh"] = [x for x in s.get("nhan_manh") or [] if x in toan]
            out.append(moi)
    return out


def _cac_muc(doc: dict) -> list[tuple[str, list[str]]]:
    """(tên mục, mã các khối văn bản đã dịch thuộc mục) — theo tiêu đề của bài.
    Dừng ở phụ lục / tham khảo: mọi mục con sau đó (A.1, A.2…) cũng là phụ lục."""
    tr = doc.get("translations") or {}
    muc, cur = [], None
    for b in doc["blocks"]:
        if b.get("hidden") or b.get("type") in ("reference", "meta"):
            continue
        if b["type"] == "heading":
            ten = (tr.get(b["id"]) or b["text"]).strip()
            if _HET_THAN_BAI.search(ten):
                break
            cur = (ten, [])
            muc.append(cur)
        elif cur is not None and b.get("type") in ("para", "list") and tr.get(b["id"]):
            cur[1].append(b["id"])
    return [(ten, ids) for ten, ids in muc if ids]


_HET_THAN_BAI = re.compile(r"tham khảo|reference|lời cảm ơn|acknowledg|phụ lục|appendix", re.I)
# Tóm tắt đã nằm ở các slide mở; công trình liên quan được dặn gói trong 1–2 slide
# nên để trống là chủ ý — báo hai mục này là kêu oan.
_BO_QUA_MUC = re.compile(r"^\s*(tóm tắt|abstract)\b|công trình liên quan|related work", re.I)


def _soat_do_phu(doc: dict, bo: list[dict]) -> None:
    """Bộ CHI TIẾT phải đi qua mọi mục chính của bài — mục nào không có slide nào
    (không trích, không làm nguồn) thì báo, vì đó đúng là chỗ "mất ý"."""
    dung = {i for s in bo for i in (s.get("nguon") or []) + [t["id"] for t in s.get("trich") or []]}
    # Mục chỉ có một đoạn (thường là đoạn dẫn của mục cha) thì không đòi: các
    # mục con của nó mới là chỗ chứa nội dung.
    sot = [ten for ten, ids in _cac_muc(doc)
           if len(ids) >= 2 and not _BO_QUA_MUC.search(ten) and not dung.intersection(ids)]
    if sot and bo:
        bo[0].setdefault("canh_bao", []).append(
            "Chưa có slide nào cho mục: " + "; ".join(t[:60] for t in sot[:8])
            + (f" và {len(sot) - 8} mục khác" if len(sot) > 8 else "") + ".")


def _mo_dau(doc: dict) -> dict:
    """Slide mở đầu — dựng từ thông tin bài, không hỏi model."""
    from .pipeline import nguon_gon

    br = doc.get("brief") or {}
    m = db.get_meta(doc["id"])["data"]
    hinh = next((h["id"] for h in _danh_muc_hinh(doc)), "")
    tg = m.get("authors") or []
    return {
        "vai": "mo_dau",
        "tieu_de": br.get("title_vi") or doc.get("title") or "",
        "ten_goc": m.get("title_goc") or doc.get("title") or "",
        "tac_gia": ", ".join(tg[:4]) + (" và cộng sự" if len(tg) > 4 else ""),
        "noi_dang": " · ".join(str(x) for x in (m.get("venue"), m.get("year")) if x),
        "nguoi_noi": "",
        "nguon_bai": nguon_gon(doc.get("source") or ""),
        "hinh": hinh,
        "loi_noi": "",
        "canh_bao": [],
    }


def _lo_trinh() -> dict:
    """Slide lộ trình — nội dung tính lúc vẽ từ chính bộ slide (`web/slide-ve.js`)."""
    return {"vai": "lo_trinh", "tieu_de": "Lộ trình buổi trình bày", "loi_noi": "", "canh_bao": []}


def _danh_ma(bo: list[dict]) -> list[dict]:
    """Mã s1, s2… — `isalnum()` vì nó đi vào URL."""
    for i, s in enumerate(bo, 1):
        s["id"] = f"s{i}"
    return bo


def _cong_chi_phi(doc_id: str, usage) -> None:
    doc = store.load(doc_id)
    tong = llm.Usage(**doc.get("usage", {}))
    tong.add(usage)
    doc["usage"] = tong.dict()
    store.save(doc)


def tran_slide(n_chars: int) -> float:
    """Trần thời gian cho lượt sinh slide — cùng lối với `pipeline.tran_brief`."""
    return min(240.0, 90.0 + n_chars / 1000)


async def _goi(doc: dict, task: str, user: str, *, max_tokens: int, tran: float,
               usage) -> tuple[dict | None, str]:
    """Một lượt gọi model trả JSON, thử lại MỘT lần. Trả (dữ liệu | None, lý do hỏng).

    Tắt hẳn nghĩ thầm. Đo trên bài World Models với DeepSeek V4 Flash: mức "low"
    vẫn tiêu 12.812 token nghĩ thầm, chạm trần 16.000 rồi trả về CHUỖI RỖNG — người
    dùng chờ 360 giây, trả tiền hai lượt, nhận "không đọc được". Cùng bẫy đã ghi cho
    bài giảng của kho survey. Độ sâu ở đây đến từ khuôn vai + trần chữ, không từ
    token nghĩ thầm. Lượt hỏng vẫn cộng vào `usage` — nó vẫn bị tính tiền.
    """
    from .pipeline import NO_REASONING, cached_prefix

    sysmsg = llm.system_message(cached_prefix(doc), task, model=doc["model"])
    loi = ""
    for _ in range(2):
        try:
            raw, u = await asyncio.wait_for(llm.complete(
                [sysmsg, {"role": "user", "content": user}],
                model=doc["model"], session_id=doc["id"], max_tokens=max_tokens,
                temperature=0.4, reasoning=NO_REASONING), timeout=tran)
            usage.add(u)
            if not raw.strip():
                loi = "model trả về rỗng (hết trần token trước khi viết xong)"
                continue
            return llm.extract_json(raw), ""
        except asyncio.TimeoutError:
            loi = f"model không trả lời sau {int(tran)} giây"
        except ValueError:
            loi = "model trả về nội dung không đọc được"
    return None, loi


def _bao_hong(doc_id: str, usage, loi: str):
    from .pipeline import HetGio

    _cong_chi_phi(doc_id, usage)
    if loi.startswith("model không trả lời"):
        raise HetGio(loi + ", đã thử hai lần")
    raise ValueError(f"Chưa soạn được: {loi}, đã thử hai lần. "
                     "Thử lại, hoặc đổi model ở nút ▾ cạnh nút Dịch.")


async def _tao_mot_luot(doc: dict, so: int, usage) -> list[dict]:
    """Bộ ngắn (≤ `CHI_TIET_TU` slide): MỘT lượt viết cả bộ."""
    from .pipeline import full_source_text

    data, loi = await _goi(doc, SLIDE_TASK, _user(doc, so), max_tokens=20000,
                           tran=tran_slide(len(full_source_text(doc["blocks"]))), usage=usage)
    if data is None:
        _bao_hong(doc["id"], usage, loi)
    doc = store.load(doc["id"])
    bo = [x for x in (chuan_hoa(doc, s) for s in (data or {}).get("slides") or []
                      if isinstance(s, dict)) if x]
    return _tach_trich(_sap_lai(bo)) if bo else []


KHUNG_TASK = SLIDE_TASK + """
### Lần này chỉ LÊN KHUNG cho một bộ slide CHI TIẾT

Bộ này đi lần lượt TỪNG MỤC của bài theo đúng thứ tự trong bài, để người nghe
không bị rơi ý nào. Lượt này chỉ quyết định cấu trúc, chữ trên slide sẽ viết ở
lượt sau. Mỗi slide chỉ cần `vai`, `tieu_de`, `hinh`, `nguon`, và với
`doan_dich` thì thêm `doan`.

- Mở đầu bằng `van_de`, `khoang_trong`, `yeu_cau`, `y_tuong` (bức tranh chung),
  rồi đi qua từng mục của bài theo thứ tự.
- Phần phương pháp và thí nghiệm: MỖI đoạn quan trọng là một slide `doan_dich`
  (1–3 đoạn liền nhau trong cùng một mục). Chen `co_che` trước nhóm đoạn của mỗi
  thành phần, `cong_thuc` cho công thức chính, `thiet_lap` trước kết quả,
  `bang_chung` cho MỌI bảng/hình kết quả chính.
- Ít nhất MỘT NỬA số slide là `doan_dich`. Không bỏ trống mục nào của thân bài.
  Phần công trình liên quan gói gọn trong 1–2 slide. Không dùng thư mục tham khảo.
- Kết bằng `gioi_han` rồi `dong_lai`.
- Mỗi slide `doan_dich` có `muc_do`: 1 = thiếu nó người nghe hiểu sai hoặc thiếu
  ý chính của bài, 2 = nên có, 3 = chi tiết thêm. Bộ dài quá thì công cụ bỏ bớt
  mức 3 rồi mức 2, KHÔNG bao giờ bỏ mức 1 — nên chấm thật, đừng chấm hết là 1.

Chỉ trả về JSON:
{"slides": [{"vai": "doan_dich", "tieu_de": "…", "doan": ["b31", "b32"], "muc_do": 1, "hinh": ""},
            {"vai": "co_che", "tieu_de": "…", "nguon": ["b40"], "hinh": "b43"}, …]}
"""

VIET_TASK = SLIDE_TASK + """
### Lần này VIẾT CHỮ cho các slide đã lên khung

Giữ đúng `vai` và `hinh` đã cho. Tiêu đề chỉ được sửa câu chữ cho đúng văn phong,
không đổi ý. Slide `doan_dich` đã có sẵn phần trích nguyên văn (`trich`). KHÔNG
chép lại nó, chỉ viết `y_chinh`, `nhan_manh` (chép nguyên văn cụm trong `trich`)
và `loi_noi` (40–80 chữ, giải thích thêm và nối với slide trước).

Trả về JSON {"slides": [{"id": "t5", …các trường của vai…}, …]}, đủ MỌI slide
được giao, giữ nguyên `id`.
"""

ME_VIET = 8           # số slide mỗi lượt viết chữ
SONG_SONG_VIET = 3    # cùng trần với dịch song song: prefix đã ấm sau lượt lên khung


def _do_dai_doan(doc: dict, so: int) -> str:
    """Độ dài BẢN DỊCH của từng đoạn — để model tự tính số slide SAU KHI TÁCH.

    Không có nó, model nhìn bản gốc tiếng Anh (ngắn hơn bản dịch) rồi gom ba đoạn
    dài vào một slide; tách theo câu ra tới năm phần cùng một tiêu đề. Đo trên
    World Models: xin ~42 slide, nhận 82, riêng công trình liên quan 14 slide.
    """
    dong = [f"- {ten[:50]}: " + ", ".join(f"{i}={len((doc.get('translations') or {}).get(i, ''))}" for i in ids)
            for ten, ids in _cac_muc(doc)]
    return ("\n## Độ dài bản dịch của từng đoạn (mã=số ký tự), theo mục\n" + "\n".join(dong)
            + f"\n\nMột slide trích đoạn chứa tối đa ~{TRAN_TRICH} ký tự (có hình: ~{TRAN_TRICH_HINH}); "
            f"dài hơn thì công cụ tự tách sang slide sau. Tổng số slide SAU KHI TÁCH phải "
            f"khoảng {so}, nên chỉ trích những đoạn đáng trích, không trích mọi đoạn.\n")


def _cat_theo_muc_do(khung: list[dict], so: int) -> list[dict]:
    """Giữ bộ chi tiết quanh `so` slide: bỏ cả nhóm trích đoạn (mọi phần tách từ
    cùng một slide khung) mức 3 trước, rồi mức 2, xét từ CUỐI bộ ngược lên.

    Model quyết đoạn nào quan trọng (`muc_do`), máy giữ tổng số. Đo trên World
    Models: dù được đưa độ dài từng đoạn, model vẫn lên khung ra 78 slide khi xin
    ~42 — đếm là việc máy làm chắc hơn. Mức 1 không bao giờ bị bỏ.
    """
    tran = round(so * 1.15)
    for md in (3, 2):
        if len(khung) <= tran:
            break
        nhom = list(dict.fromkeys(x["_nhom"] for x in reversed(khung) if x.get("_md") == md))
        for n in nhom:
            if len(khung) <= tran:
                break
            khung = [x for x in khung if x["_nhom"] != n]
    return khung


async def _tao_chi_tiet(doc: dict, so: int, usage) -> list[dict]:
    """Bộ chi tiết: lên khung (chỉ mã đoạn) → tách trích đoạn dài → viết chữ theo
    mẻ song song. Mẻ viết hỏng thì slide vẫn còn khung, kèm cờ để viết lại."""
    import json

    from .pipeline import full_source_text

    tran = tran_slide(len(full_source_text(doc["blocks"])))
    data, loi = await _goi(doc, KHUNG_TASK, _user(doc, so) + _do_dai_doan(doc, so),
                           max_tokens=12000, tran=tran, usage=usage)
    if data is None:
        _bao_hong(doc["id"], usage, loi)
    doc = store.load(doc["id"])
    khung = []
    for x in (data or {}).get("slides") or []:
        if not isinstance(x, dict) or x.get("vai") not in VAI:
            continue
        c = chuan_hoa(doc, {"vai": x["vai"], "tieu_de": x.get("tieu_de"), "hinh": x.get("hinh"),
                            "nguon": x.get("nguon"), "doan": x.get("doan")})
        if c:
            try:
                md = min(3, max(1, int(x.get("muc_do") or 2)))
            except (TypeError, ValueError):
                md = 2
            c["_md"], c["_nhom"] = (md if c["vai"] == "doan_dich" else 0), len(khung)
            khung.append(c)
    if not khung:
        raise ValueError("Model không lên được khung slide nào dùng được. Thử lại.")
    # Thứ tự của bài, KHÔNG sắp theo chặng: bộ chi tiết đi theo mục của bài.
    khung = ([x for x in khung if x["vai"] != "dong_lai"]
             + [x for x in khung if x["vai"] == "dong_lai"][-1:])
    khung = _cat_theo_muc_do(_tach_trich(khung), so)
    for i, x in enumerate(khung, 1):
        x["_tam"] = f"t{i}"

    tieu = "\n".join(f"{x['_tam']}. [{x['vai']}] {x['tieu_de']}" for x in khung)
    gate = asyncio.Semaphore(SONG_SONG_VIET)

    async def viet(me: list[dict], lan: int = 0) -> dict:
        giao = [{"id": x["_tam"], "vai": x["vai"], "tieu_de": x["tieu_de"], "hinh": x.get("hinh", ""),
                 "nguon": x.get("nguon", []),
                 **({"trich": " ".join(t["chu"] for t in x["trich"])} if x["vai"] == "doan_dich" else {})}
                for x in me]
        user = (_user(doc, len(khung)) + "\n## Khung cả bộ (để biết slide trước/sau)\n" + tieu
                + "\n\n## Các slide cần viết chữ lượt này\n" + json.dumps(giao, ensure_ascii=False))
        async with gate:
            d, loi = await _goi(doc, VIET_TASK, user, max_tokens=8000, tran=min(240.0, tran), usage=usage)
        xong = {str(y.get("id")): y for y in (d or {}).get("slides") or [] if isinstance(y, dict)}
        thieu = [x for x in me if x["_tam"] not in xong]
        if thieu:
            print(f"[slide] {doc['id']}: mẻ viết thiếu {len(thieu)}/{len(me)} slide"
                  f"{' — ' + loi if loi else ''}", flush=True)
        # Hỏng thì CHIA ĐÔI thử lại một lần: một slide làm hỏng JSON không được kéo
        # theo cả bảy slide cùng mẻ (đã gặp: hai mẻ hỏng trọn, 16 slide chỉ còn khung).
        if thieu and lan == 0:
            nua = max(1, len(thieu) // 2)
            for d2 in await asyncio.gather(viet(thieu[:nua], 1), viet(thieu[nua:], 1) if thieu[nua:] else asyncio.sleep(0, {})):
                xong.update(d2 or {})
        return xong

    kq = await asyncio.gather(*(viet(khung[i:i + ME_VIET]) for i in range(0, len(khung), ME_VIET)))
    viet_xong = {k: v for d in kq for k, v in d.items()}
    doc = store.load(doc["id"])
    bo = []
    for x in khung:
        x.pop("_md", None)
        x.pop("_nhom", None)
        y = viet_xong.get(x.pop("_tam"))
        if not y:
            x.setdefault("canh_bao", []).append("Chưa viết xong chữ cho slide này. Bấm “Viết lại slide này”.")
            bo.append(x)
            continue
        giu = {"vai": x["vai"], "hinh": x.get("hinh", ""), "nguon": x.get("nguon", [])}
        if x["vai"] == "doan_dich":
            giu.update(trich=x["trich"], tiep=x.get("tiep"))
        c = chuan_hoa(doc, {**y, **giu, "tieu_de": y.get("tieu_de") or x["tieu_de"]})
        bo.append(c or x)
    return bo


async def tao(doc_id: str, phut: int = 15) -> tuple[dict, dict, dict]:
    """Sinh cả bộ slide. Trả (bộ slide, chi phí lượt này, cộng dồn)."""
    doc = store.load(doc_id)
    phut = phut if phut in SO_SLIDE else 15
    so = SO_SLIDE[phut]
    usage = llm.Usage()
    chi_tiet = so > CHI_TIET_TU
    bo = await (_tao_chi_tiet(doc, so, usage) if chi_tiet else _tao_mot_luot(doc, so, usage))
    if not bo:
        _cong_chi_phi(doc_id, usage)
        raise ValueError("Model không trả về slide nào dùng được. Thử lại.")
    doc = store.load(doc_id)
    _mot_hinh_mot_cho(bo)
    _soat_ca_bo(bo)
    if chi_tiet:
        _soat_do_phu(doc, bo)
    bo = _danh_ma([_mo_dau(doc), _lo_trinh()] + bo)
    tong = llm.Usage(**doc.get("usage", {}))
    tong.add(usage)
    doc["usage"] = tong.dict()
    doc["slides"] = {"v": 2, "bo": bo, "phut": phut, "tao_luc": time.time(),
                     "chi_phi": usage.dict()}
    store.save(doc)
    return doc["slides"], usage.dict(), tong.dict()


VIET_LAI_TASK = SLIDE_TASK + """
### Lần này chỉ viết lại MỘT slide

Giữ đúng vai đã cho. Trả về JSON {"slides": [một slide]}.
"""


async def viet_lai(doc_id: str, sid: str, goi_y: str = "") -> tuple[dict, dict, dict]:
    """Viết lại một slide, giữ vai. Rẻ: prefix vẫn đi qua cache."""
    from .pipeline import NO_REASONING, cached_prefix

    doc = store.load(doc_id)
    bo = lay(doc)["bo"]
    cu = next((s for s in bo if s["id"] == sid), None)
    if cu is None or cu["vai"] not in VAI:
        raise KeyError(sid)
    user = (_user(doc, 1) + f"\n## Slide cần viết lại (vai `{cu['vai']}`)\n"
            + str({k: v for k, v in cu.items() if k not in ("canh_bao", "id")})
            + (f"\n\nYêu cầu của người trình bày: {goi_y.strip()}" if goi_y.strip() else ""))
    raw, usage = await llm.complete(
        [llm.system_message(cached_prefix(doc), VIET_LAI_TASK, model=doc["model"]),
         {"role": "user", "content": user}],
        model=doc["model"], session_id=doc_id, max_tokens=3000,
        temperature=0.6, reasoning=NO_REASONING)
    moi = (llm.extract_json(raw) or {}).get("slides") or []
    doc = store.load(doc_id)
    giu = {"vai": cu["vai"]}
    if cu["vai"] == "doan_dich":
        # Trích đoạn là nguyên văn bản dịch — viết lại chỉ đổi ý chính, tô sáng, lời nói.
        giu.update(trich=cu.get("trich") or [], tiep=cu.get("tiep"), hinh=cu.get("hinh", ""),
                   nguon=cu.get("nguon") or [])
    s = chuan_hoa(doc, {**(moi[0] if moi else {}), **giu}) if moi else None
    if not s:
        raise ValueError("Model không trả về slide dùng được. Thử lại.")
    bo = lay(doc)["bo"]
    for i, x in enumerate(bo):
        if x["id"] == sid:
            s["id"] = sid
            bo[i] = s
    tong = llm.Usage(**doc.get("usage", {}))
    tong.add(usage)
    doc["usage"] = tong.dict()
    store.save(doc)
    return s, usage.dict(), tong.dict()


# ---------------------------------------------------------------- đọc & sửa

def lay(doc: dict) -> dict:
    """Bộ slide định dạng mới; định dạng cũ (v1) coi như chưa có."""
    s = doc.get("slides") or {}
    return s if s.get("v") == 2 else {"v": 2, "bo": [], "phut": 15}


# Trường người dùng được sửa tay. KHÔNG có `nguon`: sửa được nguồn thì phép soát
# số liệu thành vô nghĩa (cùng lý do bản cũ cấm sửa `source_block_ids`).
_SUA_DUOC = {"tieu_de", "loi_noi", "hinh", "minh_hoa", "du_lieu", "do_do", "thanh_phan",
             "trich", "nhan_manh",
             "so_sanh", "doi_chung", "cot", "hang", "ten_goc", "tac_gia", "noi_dang", "nguoi_noi",
             "y", "tieu_chi", "buoc", "muc", "so"} | {k for ks in _CHU.values() for k in ks}


def sua(doc: dict, sid: str, patch: dict) -> dict:
    """Sửa tay một slide. Ghi đè trường được phép, soát lại số liệu."""
    from .pipeline import so_bia

    bo = lay(doc)["bo"]
    s = next((x for x in bo if x["id"] == sid), None)
    if s is None:
        raise KeyError(sid)
    for k, v in patch.items():
        if k in _SUA_DUOC:
            s[k] = v
    if "hinh" in patch:
        h = str(patch["hinh"] or "")
        b = next((x for x in doc["blocks"] if x["id"] == h), None)
        if h and not (b and b.get("figure")):
            raise ValueError("Không có hình nào mang mã này")
        if h and (b.get("type") == "equation") != (s["vai"] == "cong_thuc"):
            raise ValueError("Slide công thức chỉ gắn ảnh công thức, slide khác chỉ gắn hình/bảng")
    if s["vai"] in VAI:
        s["canh_bao"] = [c for c in s.get("canh_bao") or [] if not c.startswith("Số không có")]
        bia = so_bia(doc, s.get("nguon") or [], _chu_slide(s))
        if bia:
            s["canh_bao"].append("Số không có ở đâu trong bài: " + ", ".join(bia) + ".")
    s["sua_tay"] = True
    return s


def kem_anh(doc: dict, bo: list[dict]) -> list[dict]:
    """Gắn tên file ảnh + nhãn ngắn cho trường `hinh` (mã khối) — bản trả về client."""
    by_id = {b["id"]: b for b in doc["blocks"]}
    for s in bo:
        b = by_id.get(s.get("hinh") or "")
        s["anh"] = b.get("figure") if b and b.get("figure") else ""
        s["anh_ver"] = "_".join(str(round(v)) for v in (b.get("figure_rect") or [])) if b else ""
        m = re.match(r"\s*((?:Figure|Fig\.?|Table|Hình|Bảng)\s*\d+)", (b or {}).get("text") or "", re.I)
        s["nhan_hinh"] = m.group(1) if m else ""
        if s.get("vai") == "doan_dich" and s.get("trich"):
            s["muc"] = _muc_cua(doc, s["trich"][0]["id"])
    return bo


def _muc_cua(doc: dict, bid: str) -> str:
    """Tên mục (bản dịch của tiêu đề gần nhất phía trên) chứa một khối."""
    tr = doc.get("translations") or {}
    ten = ""
    for b in doc["blocks"]:
        if b["type"] == "heading" and not b.get("hidden"):
            ten = (tr.get(b["id"]) or b["text"]).strip()
        if b["id"] == bid:
            return ten
    return ""
