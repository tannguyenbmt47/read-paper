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
SO_SLIDE = {10: 10, 15: 14, 20: 18}

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
    # `thiet_lap` luôn đứng đầu chặng Bằng chứng: chưa biết dữ liệu và thước đo
    # thì không đọc được bảng kết quả.
    con = sorted((s for s in bo if s["vai"] != "dong_lai"),
                 key=lambda s: (chang.get(s["vai"], 9), s["vai"] != "thiet_lap"))
    for i in range(1, len(con)):
        # Cơ chế liền nhau là chủ ý (mỗi thành phần một slide, công thức bám ngay
        # sau thành phần của nó) — tráo là tách thành phần khỏi công thức của nó.
        if con[i]["vai"] == con[i - 1]["vai"] and con[i]["vai"] not in ("bang_chung", "co_che"):
            # Chỉ tráo TRONG cùng chặng — tráo qua chặng là phá thứ tự vừa sắp.
            for j in range(i + 1, len(con)):
                if chang.get(con[j]["vai"], 9) != chang.get(con[i]["vai"], 9):
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


async def tao(doc_id: str, phut: int = 15) -> tuple[dict, dict, dict]:
    """Sinh cả bộ slide: MỘT lượt gọi model. Trả (bộ slide, chi phí lượt này, cộng dồn)."""
    from .pipeline import NO_REASONING, HetGio, cached_prefix, full_source_text

    doc = store.load(doc_id)
    phut = phut if phut in SO_SLIDE else 15
    sysmsg = llm.system_message(cached_prefix(doc), SLIDE_TASK, model=doc["model"])
    user = _user(doc, SO_SLIDE[phut])
    tran = tran_slide(len(full_source_text(doc["blocks"])))
    # Tắt hẳn nghĩ thầm. Đo trên bài World Models với DeepSeek V4 Flash: mức
    # "low" vẫn tiêu 12.812 token nghĩ thầm, chạm trần 16.000 rồi trả về CHUỖI
    # RỖNG (finish_reason=length) — người dùng chờ 360 giây, trả tiền hai lượt,
    # nhận "không đọc được". Cùng bẫy đã ghi cho bài giảng của kho survey. Độ sâu
    # ở đây đến từ khuôn vai + trần chữ của `SLIDE_TASK`, không từ token nghĩ thầm.
    usage = llm.Usage()
    data, loi = None, ""
    for lan in range(2):
        try:
            raw, u = await asyncio.wait_for(llm.complete(
                [sysmsg, {"role": "user", "content": user}],
                model=doc["model"], session_id=doc_id, max_tokens=20000,
                temperature=0.4, reasoning=NO_REASONING), timeout=tran)
            usage.add(u)
            if not raw.strip():
                loi = "model trả về rỗng (hết trần token trước khi viết xong)"
                continue
            data = llm.extract_json(raw)
            break
        except asyncio.TimeoutError:
            loi = f"model không trả lời sau {int(tran)} giây"
        except ValueError:
            # JSON hỏng: thử lại MỘT lần (S11 của báo cáo test — lỗi thô cho người dùng)
            loi = "model trả về nội dung không đọc được"
    if data is None:
        # Lượt hỏng vẫn bị tính tiền — ghi vào chi phí của bài, không giấu.
        _cong_chi_phi(doc_id, usage)
        if loi.startswith("model không trả lời"):
            raise HetGio(loi + ", đã thử hai lần")
        raise ValueError(f"Chưa soạn được: {loi}, đã thử hai lần. "
                         "Thử lại, hoặc đổi model ở nút ▾ cạnh nút Dịch.")
    doc = store.load(doc_id)
    bo = [x for x in (chuan_hoa(doc, s) for s in (data or {}).get("slides") or []
                      if isinstance(s, dict)) if x]
    if not bo:
        raise ValueError("Model không trả về slide nào dùng được. Thử lại.")
    bo = _sap_lai(bo)
    _mot_hinh_mot_cho(bo)
    _soat_ca_bo(bo)
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
    s = chuan_hoa(doc, {**(moi[0] if moi else {}), "vai": cu["vai"]}) if moi else None
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
    return bo
