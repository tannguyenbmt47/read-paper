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
from .pipeline import chu_vung_hinh

# ---------------------------------------------------------------- vai & khuôn

# Vai → trường nội dung model phải viết. Thứ tự liệt kê cũng là thứ tự mạch
# trình bày chuẩn của một bài phương pháp.
VAI = {
    "van_de": "Bối cảnh — tình huống thực tế, nhận định và nhận xét về nó",
    "huong_nc": "Hướng nghiên cứu — một hướng làm trước: cách làm, giải quyết khía cạnh nào, vấn đề gì",
    "khai_niem": "Khái niệm — giải nghĩa một thuật ngữ/hành vi trước khi nó được dùng",
    "khoang_trong": "Khoảng trống — cách đang làm hỏng ở đâu, trong kịch bản nào",
    "yeu_cau": "Mong muốn — lời giải phải đạt những tiêu chí gì",
    "y_tuong": "Ý tưởng cốt lõi — trực giác giúp đạt các tiêu chí đó",
    "co_che": "Cơ chế — các bước chạy từ đầu vào tới đầu ra",
    "cong_thuc": "Công thức — biểu thức chính và vai trò từng thành phần",
    "vi_du": "Ví dụ chạy tay — một đầu vào cụ thể có thật trong bài, đi qua từng bước",
    "so_sanh": "Khác gì cách cũ — bảng đối chiếu các cách làm theo từng tiêu chí",
    "thiet_lap": "Thiết lập thí nghiệm — dữ liệu, đối chứng, thước đo",
    "cach_lam_tn": "Cách làm thí nghiệm — thí nghiệm kiểm câu hỏi gì và tiến hành từng bước ra sao",
    "bang_chung": "Bằng chứng — một hình/bảng trong bài và điều nó cho thấy",
    "so_lieu": "Con số chính — một kết quả kèm mốc so sánh",
    "gioi_han": "Giới hạn — tác giả tự nhận và điểm đáng ngờ",
    "dong_lai": "Đúc kết — ba điều mang về và một câu hỏi thảo luận",
    "doan_dich": "Trích đoạn — nguyên văn bản dịch của 1–3 đoạn trong bài, kèm ý chính",
}

# Sáu chặng của lộ trình, theo trình tự một buổi seminar nghiên cứu mà người
# dùng chốt: bài toán → hướng tiếp cận (related work) → phương pháp → thí nghiệm
# → kết quả & ablation → bổ sung. Slide lộ trình và dấu "3/6 · Phương pháp" ở
# góc slide đều tính từ đây, không hỏi model. `khai_niem` và `doan_dich` không
# thuộc chặng nào: chúng đi theo slide đứng trước (khái niệm được chèn ngay trước
# chỗ dùng nó, ở bất kỳ chặng nào).
CHANG = [
    ("Bài toán", ("van_de", "yeu_cau")),
    ("Hướng tiếp cận", ("huong_nc", "khoang_trong", "so_sanh")),
    ("Phương pháp", ("y_tuong", "co_che", "cong_thuc", "vi_du")),
    ("Thí nghiệm", ("thiet_lap", "cach_lam_tn")),
    ("Kết quả & ablation", ("bang_chung", "so_lieu")),
    ("Bổ sung", ("gioi_han", "dong_lai")),
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
    "van_de": ("cau", "nhan_dinh", "vi_du"),
    "huong_nc": ("cach_lam", "khia_canh", "han_che"),
    "khai_niem": ("thuat_ngu", "dinh_nghia", "vi_du", "vi_sao"),
    "cach_lam_tn": ("muc_dich",),
    "khoang_trong": ("cach_cu", "hong", "he_qua"),
    "yeu_cau": (),
    "y_tuong": ("cau", "vi_sao"),
    "co_che": ("dan", "co_so"),
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

MỌI slide BẮT BUỘC có `tieu_de`: một câu KHẲNG ĐỊNH điều slide chứng minh, 6–14
chữ, không phải nhãn chủ đề, không có dấu hai chấm. Thiếu tiêu đề là slide hỏng.

Trần số chữ ghi trong ngoặc là trần CỨNG (chữ thân không nhỏ hơn 24px, vượt trần
là tràn khung) — nhưng nó là TRẦN, không phải mục tiêu nén. Viết đủ để người
nghe hiểu, chưa tới trần thì không phải cắt.

### Diễn giải (bắt buộc — đây là chỗ bộ slide hay hỏng nhất)

Người nghe là kỹ sư thông minh nhưng KHÔNG ở trong nhánh này. Mặt slide phải tự
giải thích được, không trông vào lời người nói. Vì vậy:

- Mỗi ô viết thành CÂU TRỌN VẸN có chủ ngữ, động từ và quan hệ nhân quả. Cấm lối
  điện tín ghép cụm danh từ ("Mạng nhỏ thiếu sức biểu diễn", "Điều khiển nhỏ").
- Thuật ngữ chuyên ngành nào xuất hiện trên slide cũng phải được giải nghĩa NGAY
  TẠI CHỖ bằng lời thường (trong ngoặc, hoặc một mệnh đề ngay sau), trừ khi đã có
  slide `khai_niem` cho nó phía trước. Thuật ngữ không cần cho ý chính thì đừng đưa.
- Nói vấn đề theo trình tự: cái đó LÀ GÌ → nó LÀM gì / xảy ra gì → VÌ SAO điều
  đó là vấn đề (hậu quả cụ thể). Thiếu vế "vì sao" thì người nghe không biết vấn
  đề nằm ở đâu.
- Tên tiêu chí, tên bước ngắn được, nhưng phần mô tả đi kèm phải nói rõ tiêu chí
  ấy là gì và vì sao cần.

Ví dụ sửa (lấy từ một bộ slide thật bị chê "đọc không hiểu"):

| Viết điện tín (sai) | Viết diễn giải (đúng) |
|---|---|
| Cách đang làm: RL model-free thường dùng mạng nhỏ vài nghìn tham số. | Các thuật toán học tăng cường model-free (agent học thẳng từ điểm thưởng, không dựng mô hình của môi trường) thường chỉ huấn luyện được mạng nhỏ, cỡ vài nghìn tham số. |
| Hỏng ở đâu: Mạng nhỏ thiếu sức biểu diễn để xử lý quan sát pixel phức tạp. | Mạng nhỏ như vậy không đủ sức nén một khung hình pixel thành thông tin hữu ích, nên agent không hiểu được cảnh trước mặt. Muốn mạng lớn thì lại vướng: điểm thưởng quá thưa để chỉnh hàng triệu tham số. |
| Điều khiển nhỏ: Vùng tìm kiếm nhỏ để thuật toán RL hội tụ. | Bộ điều khiển (phần chọn hành động) cần rất ít tham số, để thuật toán tối ưu chỉ phải dò trong một không gian nhỏ và hội tụ được. |
| Ý tưởng: Dồn sức mạnh vào world model học không giám sát, giữ controller thật nhỏ. | Tách agent làm hai phần: một world model lớn tự học cách tóm tắt và dự đoán môi trường từ dữ liệu, không cần điểm thưởng; và một controller rất nhỏ chỉ việc chọn hành động dựa trên bản tóm tắt ấy. |

- `van_de` — BỐI CẢNH. `cau` (tình huống thực tế và vì sao nó quan trọng, ≤40
  chữ), `nhan_dinh` (nhận định / nhận xét của bài về tình huống ấy, ≤45 chữ),
  `vi_du` (MỘT ví dụ cụ thể, ≤45 chữ).
- `huong_nc` — MỘT hướng nghiên cứu trước bài này. `dai_dien`: 1–4 tên phương
  pháp tiêu biểu, `cach_lam` (hướng này làm thế nào, ≤53 chữ), `khia_canh` (nó
  tập trung giải quyết khía cạnh nào của bài toán, ≤30 chữ), `han_che` (vấn đề
  của nó, MÔ TẢ CHI TIẾT trong kịch bản nào thì hỏng và hỏng ra sao, ≤60 chữ).
- `khai_niem` — giải nghĩa MỘT thuật ngữ / hành vi / tên mô-đun TRƯỚC khi nó được
  dùng. `thuat_ngu` (tên, kèm tên đầy đủ nếu là viết tắt), `dinh_nghia` (nó là gì,
  ≤53 chữ), `vi_du` (một ví dụ cụ thể, ≤38 chữ), `vi_sao` (vì sao bài cần nó /
  thiếu nó thì hỏng ở đâu, ≤38 chữ).
- `khoang_trong` — `cach_cu` (cách đang làm, ≤45 chữ), `hong` (nó hỏng ở đâu,
  trong kịch bản nào, ≤53 chữ), `he_qua` (hệ quả đo được hoặc quan sát được, ≤38 chữ).
- `yeu_cau` — MONG MUỐN. `tieu_chi`: 2–4 mục `{"ten": "3–7 chữ", "vi_sao": "≤22
  chữ"}`, lời giải phải đạt gì. Đây là bước hay bị bỏ sót nhất.
- `y_tuong` — `cau` (trực giác cốt lõi trong MỘT câu, ≤45 chữ), `vi_sao` (vì sao
  trực giác ấy đáp ứng được các tiêu chí, ≤53 chữ).
- `co_che` — MỘT thành phần của phương pháp. `dan` (thành phần này nhận gì, trả
  ra gì, ≤38 chữ), `buoc`: 3–4 mục `{"ten": "2–5 chữ", "mo_ta": "≤32 chữ, nói
  bước này làm gì VÀ vì sao cần"}`, `co_so` (cơ sở lý luận của thiết kế này: dựa
  trên quan sát / lý thuyết / kết quả nào, ≤45 chữ). Đưa ra cái gì cũng phải kèm
  lý do. Gắn `hinh` nếu có hình vẽ thành phần ấy
  (khi đó tối đa 3 bước, mỗi `mo_ta` ≤30 chữ).
- `cong_thuc` — `hinh` (mã khối CÔNG THỨC trong danh mục công thức dưới; không có
  thì để rỗng và viết `bieu_thuc` bằng `x_{t}`, `x^{2}`), `truc_giac` (công thức
  này tính cái gì và vì sao cần nó, ≤45 chữ), `thanh_phan`: 2–5 mục
  `{"ky_hieu": "α", "y_nghia": "≤27 chữ, vai trò của nó, không chỉ tên"}`,
  `danh_doi` (tăng/giảm thành phần nào thì được gì mất gì, ≤38 chữ).
- `vi_du` — `dau_vao` (≤38 chữ), `buoc`: 3–4 mục `{"ten", "mo_ta": "≤27 chữ"}`
  cho biết đầu vào ấy biến đổi ra sao, `dau_ra` (≤23 chữ), `minh_hoa` (true/false).
  Gắn `hinh` thì tối đa 3 bước, `dau_vao` ≤27 chữ.
- `so_sanh` — `cot`: 3–4 cách làm (tên ngắn), cột CUỐI là phương pháp của bài.
  `hang`: 3–5 mục `{"tieu_chi": "3–7 chữ", "o": ["có", "không", "một phần", …]}`
  với `o` đúng bằng số cột, mỗi ô ≤4 chữ. Tiêu chí nên lấy từ slide `yeu_cau`.
  `ket_luan` (điều bảng cho thấy, ≤38 chữ). Chỉ ghi điều bài nói về các cách đó.
- `thiet_lap` — `du_lieu`: 2–4 mục `{"ten": "tên tập", "mo_ta": "≤23 chữ, loại câu
  hỏi/quy mô"}`, `doi_chung`: 2–6 tên baseline, `do_do`: 1–3 mục `{"ten": "EM",
  "y_nghia": "≤23 chữ, đo cái gì"}`, `mo_hinh_nen` (mô hình nền dùng, ≤23 chữ).
- `cach_lam_tn` — `muc_dich` (thí nghiệm này kiểm câu hỏi gì, ≤38 chữ), `buoc`:
  2–4 mục `{"ten": "2–5 chữ", "mo_ta": "≤33 chữ"}` (tiến hành ra sao: chia dữ
  liệu, chạy gì, đo gì, so với gì).
- `bang_chung` — `hinh` (BẮT BUỘC, mã khối của hình/bảng trong danh mục dưới),
  `doc_hinh` (cách đọc hình: trục/cột là gì, nhìn vào đâu, ≤42 chữ),
  `ket_luan` (kết quả này KHẲNG ĐỊNH điều gì cho luận điểm của bài, ≤38 chữ), `so`: 0–2 mục
  `{"gia_tri": "<số trong bài>", "nhan": "<thước đo> trên <tập dữ liệu>", "moc": "so với <số> của <baseline>"}`.
- `so_lieu` — `gia_tri` (một con số), `nhan` (đo cái gì), `moc` (so với gì: baseline,
  benchmark, mô hình nền), `y_nghia` (mức chênh ấy nói lên điều gì, ≤45 chữ),
  `so_sanh`: 2–5 mục `{"nhan": "<tên phương pháp>", "gia_tri": "<số trong bài>", "cua_bai": false}` để vẽ
  biểu đồ cột (mục của bài có `"cua_bai": true`). Chỉ dùng số có trong CHỮ của bài,
  cùng một thước đo, cùng một tập dữ liệu.
- `gioi_han` — `muc`: 2–4 mục `{"ten": "3–8 chữ", "he_qua": "≤38 chữ"}`.
- `doan_dich` — trích NGUYÊN VĂN bản dịch lên slide, dùng khi câu chữ của bài quan
  trọng (định nghĩa, bước của phương pháp, kết quả chính, giới hạn tác giả tự nêu).
  `doan`: 1–3 mã khối LIỀN NHAU trong cùng một mục (công cụ tự chép bản dịch của
  chúng vào slide, bạn KHÔNG chép lại chữ), `diem_chinh` (điều cần nhớ từ đoạn này,
  ≤30 chữ, không lặp lại câu trong đoạn, không nhắc lại tiêu đề), `nhan_manh`: 1–3 cụm ≤12 chữ CHÉP
  NGUYÊN VĂN từ bản dịch của đoạn để tô sáng, `hinh` nếu đoạn nói về một hình.
- `dong_lai` — `y`: đúng 3 điều mang về (mỗi điều ≤38 chữ), `cau_hoi`: một câu hỏi
  thảo luận mở cho người nghe (≤45 chữ).

Mọi slide có thêm `tieu_de` (một câu KHẲNG ĐỊNH điều slide chứng minh, 6–14 chữ,
không phải nhãn chủ đề, KHÔNG có dấu hai chấm), `nguon` (mã các khối trong bài
làm căn cứ), và `loi_noi` (lời người trình bày nói ở slide này, 40–80 chữ, nói
tự nhiên — chỉ nói điều mặt slide CHƯA nói, đừng đọc lại chữ trên slide).

### Trung thực về ví dụ và con số (bắt buộc)

- Bạn không thấy ẢNH của hình/bảng, nhưng danh mục có kèm CHỮ VÀ SỐ bóc từ bên
  trong hình/bảng (theo dòng; bảng là hàng phương pháp kèm các cột số). Số trong
  đó được dùng. Kết luận về một hình/bảng PHẢI khớp với các số ấy — đọc kỹ từng
  cột trước khi viết "vượt mọi", "luôn", "đều". Hình không có chữ bóc được thì chỉ
  nói điều chú thích và đoạn văn của bài nói. Không tự tính hiệu hai số rồi ghi
  như số của bài; nếu ghi mức chênh thì nói rõ so với cột/hàng nào.
- Ví dụ (`van_de.vi_du`, slide `vi_du`) phải là ví dụ CÓ TRONG CHỮ của bài. Bài
  có ví dụ dạng bảng "Case Study" thì gắn bảng đó vào `hinh` và mô tả các bước ở
  mức cơ chế, không bịa tên, số, thực thể cụ thể của bảng.
- Bài không có ví dụ bằng chữ mà vẫn cần minh hoạ thì được tự dựng một ví dụ
  ĐƠN GIẢN, nhưng phải đặt `"minh_hoa": true` (slide sẽ ghi rõ "minh hoạ, không
  lấy từ bài") và không gắn tên tập dữ liệu nào cho nó. Ví dụ lấy từ bài thì
  `"minh_hoa": false`.

### Mạch và nhịp

- Sáu chặng, đúng thứ tự:
  1. **Bài toán**: `van_de` (bối cảnh, nhận định, nhận xét) → `yeu_cau` (mong muốn).
  2. **Hướng tiếp cận**: các `huong_nc` (mỗi hướng một slide: làm gì, giải quyết
     khía cạnh nào, vấn đề chi tiết ra sao) → `khoang_trong` (điều mọi hướng ấy
     còn bỏ ngỏ) → `so_sanh` nếu bài đối chiếu.
  3. **Phương pháp**: `y_tuong` → mỗi thành phần một `co_che` (kèm cơ sở lý luận)
     → `cong_thuc` cho mô hình toán của nó → `vi_du` đi qua cả hệ thống.
  4. **Thí nghiệm**: `thiet_lap` (dữ liệu, đối chứng, thước đo) → `cach_lam_tn`
     (cách bố trí và tiến hành). BẮT BUỘC có cả hai.
  5. **Kết quả & ablation**: `bang_chung` cho kết quả chính rồi từng ablation,
     mỗi slide nói rõ nó KHẲNG ĐỊNH điều gì; `so_lieu` cho con số đinh.
  6. **Bổ sung**: `gioi_han` → `dong_lai` (luôn là slide cuối).
- KHÔNG dùng một thuật ngữ, viết tắt, tên mô-đun hay hành vi nào trước khi nó
  được giới thiệu. Gặp khái niệm mới mà người nghe chưa biết (tên mô-đun, chỉ
  số đo, hành vi như "từ chối trả lời", "cache"…) thì chèn một slide `khai_niem`
  NGAY TRƯỚC slide đầu tiên dùng nó.
- Hai slide liền nhau KHÔNG cùng vai, trừ `bang_chung` (tối đa 3 liền nhau).
- Phương pháp là phần chính của buổi nói, đừng nén nó. Phương pháp có nhiều
  thành phần thì MỖI thành phần một slide `co_che` (tối đa 4), mỗi thành phần
  có công thức quan trọng thì thêm một slide `cong_thuc` ngay sau nó. Rồi một
  slide `vi_du` đi qua cả hệ thống.
- Bộ nào cũng có `thiet_lap` đứng trước slide bằng chứng đầu tiên — không biết
  dữ liệu, đối chứng và thước đo thì người nghe không đọc được bảng kết quả.
- Dùng hình nhiều: ít nhất MỘT NỬA số slide gắn `hinh` khi danh mục đủ hình.
  Mỗi hình chỉ gắn MỘT slide trong cả bộ.
  `van_de` gắn hình minh hoạ bài toán (thường là Figure 1), `y_tuong` gắn hình
  tổng quan, `co_che` gắn hình của thành phần, `vi_du` gắn bảng Case Study.
- Có ít nhất một `co_che` hoặc `vi_du` đi hết cơ chế bằng ví dụ thật — người
  nghe phải kể lại được cách nó chạy, không chỉ cái tên.
- Bằng chứng: ưu tiên bảng so sánh có baseline, rồi ablation, rồi biểu đồ phân
  tích. `doc_hinh` và `ket_luan` CHỈ được nói điều CHỮ của bài (chú thích, đoạn bàn
  về hình) nói. Không đoán dạng biểu đồ (cột, tròn, đường) nếu chú thích không
  nói, không tự suy xu hướng — bạn không thấy ảnh, và đoán sai là gán cho tác giả
  một kết luận họ không đưa ra (đã gặp: "cải thiện ở mọi độ hạt" trong khi hình
  có một mức giảm, "độ trễ thấp hơn hầu hết baseline" trong khi hình ngược lại).
  Slide ghi rõ `ket_luan` là "Bài kết luận", nên nó phải là điều BÀI nói. Không tự
  thêm "luôn", "mọi", "vượt trội", "ít nhạy" khi câu của bài không nói đúng như vậy. Mỗi hình chỉ dùng một lần. KHÔNG gắn hình nếu chú thích của nó không nói
  đúng điều tiêu đề khẳng định.

### Văn phong (bắt buộc)

- Tiếng Việt học thuật, đủ chủ vị. Thuật ngữ đã giữ tiếng Anh trong bảng thuật
  ngữ thì giữ nguyên; viết tắt (ICI, KD…) phải được gọi đủ tên ở slide ĐẦU TIÊN
  nhắc tới nó. Mỗi mô-đun, thành phần, thước đo chỉ có MỘT tên trong cả bộ, đúng
  tên trong bảng thuật ngữ — ba tên cho một mô-đun là người nghe tưởng ba thứ.
- KHÔNG dùng dấu chấm phẩy. KHÔNG dùng dấu hai chấm để cắt đôi câu, kể cả
  trong tiêu đề ("Hai điểm mù: A và B" → "Hai điểm mù là A và B"). KHÔNG dùng
  "tức", "tức là", "nghĩa là" để vá phần giải thích. KHÔNG ẩn dụ, không từ đệm
  ("rõ ràng", "vô cùng", "hoàn toàn").
- Mọi con số phải có mặt trong bài, và đi kèm mốc so sánh (baseline, benchmark,
  mô hình nền). Phân biệt điểm phần trăm với phần trăm tương đối: hiệu của hai
  số phần trăm ghi là "điểm" ("+5,2 điểm R@3"), không ghi "%". Không dùng
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
                    "chu_thich": (tr.get(b["id"]) or b.get("text") or "")[:220],
                    "trong_hinh": _rut_gon(chu_vung_hinh(doc, b["id"]), 700)})
    return out


def _rut_gon(t: str, n: int) -> str:
    t = t.strip()
    return t if len(t) <= n else t[:n].rsplit(" ", 1)[0] + " …"


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
    dm = "\n".join(f"- {h['id']} (trang {h['trang']}): {h['chu_thich']}"
                   + ("\n  Chữ và số bóc từ trong hình/bảng (theo dòng):\n  "
                      + h["trong_hinh"].replace("\n", "\n  ") if h["trong_hinh"] else "")
                   for h in hinh) \
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


def _ds_dict(v) -> list[dict]:
    return [x for x in (v if isinstance(v, list) else []) if isinstance(x, dict)]


def _ds_chuoi(v) -> list[str]:
    """Trường danh sách chuỗi. Model hay trả MỘT chuỗi thay cho danh sách
    (`"dai_dien": "IRCoT"`), và lặp qua chuỗi là lặp từng KÝ TỰ — slide hiện
    "I• R• C• o". Chuỗi thì tách theo dấu phẩy."""
    if isinstance(v, str):
        v = re.split(r"\s*[,;]\s*", v)
    return [" ".join(str(x).split()) for x in (v or []) if str(x).strip()]


def _chu_slide(s: dict) -> str:
    """Toàn bộ chữ hiện trên mặt slide — để soát số liệu."""
    parts = [s.get("tieu_de", "")] + [s.get(k, "") for k in _CHU.get(s.get("vai"), ())]
    for k in ("tieu_chi", "buoc", "muc", "du_lieu", "do_do", "thanh_phan", "so", "so_sanh"):
        for it in s.get(k) or []:
            parts += [str(v) for v in it.values() if isinstance(v, str)]
    for it in s.get("hang") or []:
        parts += [it.get("tieu_chi", "")] + list(it.get("o") or [])
    parts += (list(s.get("y") or []) + list(s.get("cot") or []) + list(s.get("doi_chung") or [])
              + list(s.get("dai_dien") or []))
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

    Kéo theo CÔNG THỨC nằm giữa các đoạn được chọn, và công thức ngay sau một
    đoạn kết bằng dấu hai chấm. Công thức là khối riêng, nên bản đầu chỉ chép chữ
    và slide dừng ở "giáo viên sinh ra:" rồi bỏ trống — subagent chỉ đọc slide
    chấm slide đó "gần như không đọc được" (6 chỗ trên bộ CIRAG 45 phút).
    """
    blocks = doc["blocks"]
    vi_tri = {b["id"]: i for i, b in enumerate(blocks)}
    ids = [str(x) for x in (s.get("doan") or []) if str(x) in vi_tri
           and blocks[vi_tri[str(x)]].get("type") in ("para", "caption", "list", "table", "heading", "equation")
           and not blocks[vi_tri[str(x)]].get("hidden")][:4]
    if not ids:
        return [], []
    dau, cuoi = min(vi_tri[i] for i in ids), max(vi_tri[i] for i in ids)
    chon = set(ids)
    # công thức ngay sau đoạn cuối, nếu đoạn ấy dẫn vào nó bằng dấu hai chấm
    sau = cuoi + 1
    while sau < len(blocks) and blocks[sau].get("hidden"):
        sau += 1
    if (sau < len(blocks) and blocks[sau].get("type") == "equation"
            and _chu_khoi(doc, blocks[cuoi]["id"])[0].rstrip().endswith(":")):
        cuoi = sau
    trich, canh = [], []
    for b in blocks[dau:cuoi + 1]:
        if b.get("hidden"):
            continue
        if b.get("type") == "equation":
            if b.get("figure") and store.image_path(doc["id"], b["figure"]) is not None:
                trich.append({"id": b["id"], "cong_thuc": True, "chu": ""})
            continue
        if b["id"] not in chon:
            continue   # chỉ kéo công thức chen giữa, không kéo đoạn văn model không chọn
        chu, goc = _chu_khoi(doc, b["id"])
        if chu:
            trich.append({"id": b["id"], "chu": chu})
            if goc:
                canh.append("Đoạn này chưa dịch nên slide hiện bản gốc — dịch đoạn đó rồi sửa lại.")
    if not any(t["chu"] for t in trich):
        return [], canh
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
    thieu_td = not out["tieu_de"]
    for k in _CHU[vai]:
        out[k] = _sach(s.get(k))
        if k not in ("thuat_ngu", "bieu_thuc"):
            # Gạch ngang chèn mệnh đề ("…quyết định — mỗi phần…") → tách câu, theo
            # luật văn phong. Chỉ ở văn xuôi; tên riêng/biểu thức để nguyên.
            out[k] = re.sub(r"\s+[—–]\s+(\S)", lambda m: ". " + m.group(1).upper(), out[k])
    for k, truong in (("tieu_chi", ("ten", "vi_sao")), ("buoc", ("ten", "mo_ta")),
                      ("muc", ("ten", "he_qua")), ("so", ("gia_tri", "nhan", "moc")),
                      ("du_lieu", ("ten", "mo_ta")), ("do_do", ("ten", "y_nghia")),
                      ("thanh_phan", ("ky_hieu", "y_nghia"))):
        if isinstance(s.get(k), list):
            out[k] = [{f: _sach(it.get(f)) if f != "ky_hieu" else " ".join(str(it.get(f) or "").split())
                       for f in truong}
                      for it in s[k][:5] if isinstance(it, dict) and any(it.get(f) for f in truong)]
    if vai == "cong_thuc" and out.get("thanh_phan"):
        out["thanh_phan"] = out["thanh_phan"][:4]   # 5 ký hiệu làm tràn khung (đã đo)
    if vai == "so_lieu" and isinstance(s.get("so_sanh"), list):
        out["so_sanh"] = [{"nhan": _sach(it.get("nhan")), "gia_tri": _sach(it.get("gia_tri")),
                           "cua_bai": bool(it.get("cua_bai"))}
                          for it in _ds_dict(s["so_sanh"])[:5] if it.get("gia_tri")]
    if vai == "thiet_lap":
        out["doi_chung"] = _ds_chuoi(s.get("doi_chung"))[:6]
    if vai == "huong_nc":
        out["dai_dien"] = _ds_chuoi(s.get("dai_dien"))[:4]
    if vai == "khai_niem":
        out["thuat_ngu"] = " ".join(str(s.get("thuat_ngu") or "").split())   # tên riêng: không viết hoa lại
    if vai == "so_sanh":
        cot = [_sach(x) for x in _ds_chuoi(s.get("cot"))][:4]
        hang = []
        for it in (s.get("hang") or [])[:5]:
            if isinstance(it, dict) and it.get("tieu_chi"):
                o = [_sach(x) for x in (it.get("o") or [])][:len(cot)]
                hang.append({"tieu_chi": _sach(it["tieu_chi"]), "o": o + [""] * (len(cot) - len(o))})
        out["cot"], out["hang"] = cot, hang
    if vai == "doan_dich" and not s.get("y_chinh"):
        # Model viết `diem_chinh`, dữ liệu lưu `y_chinh`. Tên cũ làm V4 Flash mắc một
        # tật cố định: `"y_chinh":": "…"` (chèn `":` trước giá trị) — ra dấu hai chấm
        # lơ lửng ở 12/62 slide, và có lượt leo thang thành vòng lặp `":":":…` tới
        # hết trần token, hỏng trọn mẻ. Đổi tên trường model phải viết là hết.
        out["y_chinh"] = _sach(s.get("diem_chinh"))
    if vai == "dong_lai":
        out["y"] = [_sach(x) for x in _ds_chuoi(s.get("y"))][:3] if not isinstance(s.get("y"), str) \
            else [_sach(s["y"])]
    if vai in ("van_de", "vi_du"):
        out["minh_hoa"] = bool(s.get("minh_hoa"))
    if vai == "doan_dich":
        # Trích đoạn đã dựng sẵn (viết lại một slide, lượt viết chữ của bộ chi tiết)
        # thì giữ nguyên; chưa có thì dựng từ mã đoạn model chọn.
        if isinstance(s.get("trich"), list) and s["trich"]:
            out["trich"] = [{"id": str(t.get("id", "")), "chu": str(t.get("chu", "")),
                             **({"cong_thuc": True} if t.get("cong_thuc") else {})}
                            for t in s["trich"] if isinstance(t, dict) and (t.get("chu") or t.get("cong_thuc"))]
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
    if h and vai == "vi_du" and len(out.get("buoc") or []) > 3:
        # Ví dụ 4 bước + hình thì tràn khung (đo trên CIRAG), mà hình hay gặp ở đây
        # là bảng Case Study chữ nhỏ, thu vào nửa slide thì không đọc nổi. Các bước
        # đã kể trọn ví dụ — giữ bước, bỏ hình.
        canh.append("Đã bỏ hình khỏi slide ví dụ vì có hơn 3 bước — bớt một bước nếu muốn gắn lại hình.")
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
    # Ô số không có chữ số ("Giảm mạnh") là ô rỗng nghĩa: to và xanh nhất slide mà
    # không cho biết gì — subagent chỉ đọc slide bắt đúng hai ô như vậy.
    if out.get("so"):
        # Ô số phải MỞ ĐẦU bằng một con số. Lọc theo "có chữ số" thì "F1 cao nhất"
        # lọt (chữ số nằm trong F1), "Trung bình thấp hơn 4,3 F1" cũng lọt — đó là
        # câu, không phải ô số.
        out["so"] = [it for it in out["so"] if re.match(r"\s*[~≈+\-−×]?\s*\d", it.get("gia_tri", ""))]
    bo_chip = []
    # Cột biểu đồ của slide con số cũng vậy: một thanh dựng từ số bịa là hình ảnh
    # giả gán cho tác giả thật. Đã gặp: model chép nguyên số VÍ DỤ trong prompt
    # (61,4 / 57,1) thành số liệu của bài — ví dụ trong prompt giờ không còn số thật.
    for it in list(out.get("so_sanh") or []):
        bia = so_bia(doc, out["nguon"], it["gia_tri"])
        if bia:
            out["so_sanh"].remove(it)
            bo_chip += bia
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
    if thieu_td:
        # Model bỏ trống tiêu đề (đã gặp: 22/25 slide của một lượt V4 Flash) thì dựng
        # tạm từ câu đầu của nội dung — chữ giữ chỗ "Tiêu đề — một câu khẳng định"
        # lọt lên màn chiếu còn tệ hơn một tiêu đề tạm.
        nguon_td = next((str(out[k]) for k in _CHU[vai] if out.get(k)), "") or next(
            (t["chu"] for t in out.get("trich") or [] if t.get("chu")), "")
        cau = re.split(r"(?<=[.!?])\s", nguon_td.strip())[0].rstrip(".")
        tu = cau.split()
        out["tieu_de"] = " ".join(tu[:14]) + ("…" if len(tu) > 14 else "")
        canh.append("Model không viết tiêu đề cho slide này — tiêu đề đang là câu đầu của nội dung, nên sửa lại.")
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
    # `thiet_lap` luôn đứng đầu chặng Thí nghiệm: chưa biết dữ liệu và thước đo
    # thì không đọc được bảng kết quả.
    con = sorted((s for s in bo if s["vai"] != "dong_lai"),
                 key=lambda s: (khoa[id(s)], s["vai"] != "thiet_lap"))
    for i in range(1, len(con)):
        # Cơ chế liền nhau là chủ ý (mỗi thành phần một slide, công thức bám ngay
        # sau thành phần của nó), trích đoạn liền nhau cũng vậy (đoạn văn liền
        # nhau trong bài) — tráo là phá đúng thứ tự ấy.
        if con[i]["vai"] == con[i - 1]["vai"] and con[i]["vai"] not in (
                "bang_chung", "co_che", "doan_dich", "huong_nc", "khai_niem"):
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
_UU_TIEN_HINH = {"bang_chung": 0, "co_che": 1, "vi_du": 2, "cong_thuc": 3, "van_de": 4,
                 "huong_nc": 5, "khai_niem": 6, "cach_lam_tn": 6, "y_tuong": 7}


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


# Viết tắt người nghe AI ai cũng biết — đòi giải nghĩa là kêu oan.
_VIET_TAT_PHO_THONG = {"II", "III", "IV", "VI", "VII", "VIII", "IX", "XI", "XII",   # số La Mã ("Lothair II")
                       "LLM", "LLMs", "AI", "NLP", "GPU", "CPU", "API", "RAG", "QA", "ML", "RL",
                       "CNN", "RNN", "LSTM", "MLP", "URL", "PDF", "ID", "SOTA"}
_VIET_TAT = re.compile(r"(?<![\w-])([A-Z][A-Za-z]?[A-Z]{1,4}\d?)(?![\w-])")


def _giai_nghia_viet_tat(doc: dict) -> dict[str, str]:
    """Viết tắt → tên đầy đủ. Tra VĂN BẢN GỐC trước ("Knowledge Discriminator (KD)"),
    rồi mới tới bảng thuật ngữ — và chỉ những mục ghi rõ "viết tắt của".

    Bản đầu lấy cột tiếng Việt của bảng thuật ngữ làm tên đầy đủ, mà cột ấy là
    bản dịch NGHĨA: KD thành "mô hình tích hợp đã chưng cất", khiến subagent đọc
    slide hiểu KD là Knowledge Distillation trong khi bài viết Knowledge Discriminator.
    """
    out = {}
    goc = " ".join(b.get("text") or "" for b in doc["blocks"] if b.get("type") in ("para", "list", "caption"))
    for m in re.finditer(r"((?:[A-Z][\w-]*\s+){1,6}?)\(\s*([A-Z][A-Za-z]{1,6})\s*\)", goc):
        vt, tu = m.group(2), m.group(1).split()
        hoa = [c for c in vt if c.isupper()]
        # cắt từ bên trái tới khi các chữ đầu (viết hoa) ghép đúng viết tắt
        for i in range(len(tu)):
            dau = [w[0] for w in tu[i:] if w[0].isupper()]
            if dau == hoa:
                out.setdefault(vt, " ".join(tu[i:]))
                break
    for g in (doc.get("brief") or {}).get("glossary") or []:
        en, vi = str(g.get("en") or "").strip(), str(g.get("vi") or "").strip()
        m = re.match(r"^viết tắt của\s+(.+)$", vi, flags=re.I)
        if not m:
            continue
        for vt in _VIET_TAT.findall(en):
            out.setdefault(vt, m.group(1))
    return out


# Trường KHÔNG được chèn vào: trích đoạn là nguyên văn bản dịch, còn mã/ảnh là dữ liệu.
_KHONG_CHEN = {"id", "vai", "hinh", "nguon", "trich", "anh", "anh_ver", "canh_bao", "tieu_de",
               "loi_noi", "tiep", "nhan_manh", "minh_hoa", "sua_tay"}


def _chen_ten_day_du(s: dict, vt: str, day_du: str) -> bool:
    """Chèn "VT (tên đầy đủ)" vào lần xuất hiện ĐẦU TIÊN của viết tắt trên mặt slide —
    ưu tiên thân slide, tiêu đề là chỗ cuối cùng (chèn vào tiêu đề làm nó dài ra)."""
    mau = re.compile(rf"(?<![\w-]){re.escape(vt)}(?![\w-])")

    def thu(gt):
        if isinstance(gt, str) and mau.search(gt):
            return mau.sub(f"{vt} ({day_du})", gt, count=1), True
        if isinstance(gt, list):
            for i, x in enumerate(gt):
                moi, ok = thu(x)
                if ok:
                    gt[i] = moi
                    return gt, True
        if isinstance(gt, dict):
            for k in list(gt):
                moi, ok = thu(gt[k])
                if ok:
                    gt[k] = moi
                    return gt, True
        return gt, False

    for k in [k for k in s if k not in _KHONG_CHEN] + ["tieu_de"]:
        moi, ok = thu(s.get(k))
        if ok:
            s[k] = moi
            return True
    return False


def _cum_viet_hoa(chu: str, vt: str) -> str:
    """Cụm từ Latinh viết hoa chữ đầu mà các chữ cái đầu ghép thành `vt`."""
    chu_cai = [c for c in vt if c.isupper()]
    if len(chu_cai) < 2:
        return ""
    mau = r"\b" + r"[\s-]+".join(rf"{c}[a-z]+" for c in chu_cai) + r"\b"
    m = re.search(mau, chu)
    return m.group(0) if m else ""


def _soat_viet_tat(doc: dict, bo: list[dict]) -> None:
    """Viết tắt dùng LẦN ĐẦU mà chưa được giải nghĩa thì gắn cờ vào đúng slide đó.

    Prompt đã cấm, model vẫn làm (đo trên CIRAG: "KD" xuất hiện ở bước cơ chế mà
    chưa slide nào nói nó là Knowledge Discriminator — subagent chỉ đọc slide vấp
    đúng chỗ này). Coi là đã giải nghĩa khi: có slide `khai_niem` cho nó, hoặc
    câu có "KD (" / "(KD)" ở lần đầu, hoặc nó là thước đo đã khai ở `thiet_lap`.
    Tên bài (CIRAG) và từ trong tên bài thì không đòi.
    """
    ten = " ".join([(doc.get("brief") or {}).get("title_vi") or "", doc.get("title") or ""])
    da = set(_VIET_TAT.findall(ten)) | _VIET_TAT_PHO_THONG
    giai = _giai_nghia_viet_tat(doc)
    # KHÔNG tra chữ trong hình: chú giải biểu đồ ghép ra "Kirag Dualrag" cho KD.
    # Bài CIRAG chỉ viết "Knowledge Discriminator" trong Hình 2 (ảnh raster, không
    # có lớp chữ) — không tra được thì để cảnh báo, còn hơn chèn một tên sai.
    da_doc = ""
    for s in bo:
        if s["vai"] == "khai_niem":
            da.update(_VIET_TAT.findall(s.get("thuat_ngu") or ""))
        if s["vai"] in ("huong_nc", "so_sanh"):   # tên phương pháp đã giới thiệu ở đây
            da.update(_VIET_TAT.findall(" ".join(s.get("dai_dien") or []) + " " + " ".join(s.get("cot") or [])))
        if s["vai"] == "thiet_lap":
            da.update(_VIET_TAT.findall(" ".join(d.get("ten", "") for d in s.get("do_do") or [])))
            da.update(_VIET_TAT.findall(" ".join(d.get("ten", "") for d in s.get("du_lieu") or [])))
            da.update(_VIET_TAT.findall(" ".join(s.get("doi_chung") or [])))
        chu = _chu_slide(s) + " " + " ".join(t.get("chu", "") for t in s.get("trich") or [])
        moi = []
        for vt in dict.fromkeys(_VIET_TAT.findall(chu)):
            if vt in da:
                continue
            da.add(vt)
            if re.search(rf"\b{re.escape(vt)}\s*\(|\(\s*{re.escape(vt)}\s*\)", chu):
                continue
            # Bảng thuật ngữ đã có tên đầy đủ thì CHÈN luôn vào lần dùng đầu — rẻ hơn
            # và chắc hơn nhờ model viết lại.
            # Chưa có trong bảng thuật ngữ: tên đầy đủ đã xuất hiện ở slide TRƯỚC
            # dưới dạng cụm viết hoa ghép đúng chữ cái đầu ("Trajectory Distillation"
            # → TD) thì dùng chính cụm đó.
            day_du = giai.get(vt) or _cum_viet_hoa(da_doc, vt)
            if not (day_du and _chen_ten_day_du(s, vt, day_du)):
                moi.append(vt)
        da_doc += " " + chu
        if moi:
            s.setdefault("canh_bao", []).append(
                "Viết tắt dùng lần đầu mà chưa giải nghĩa: " + ", ".join(moi[:5])
                + ". Thêm tên đầy đủ, hoặc một slide khái niệm phía trước.")


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
CHO_CONG_THUC = 160
_CAU = re.compile(r"(?<=[.!?…])(?<!\bal\.)(?<!\bet\.)\s+(?=\S)")


def _tach_trich(bo: list[dict]) -> list[dict]:
    """Trích đoạn quá dài thì tách sang slide kế tiếp ở RANH GIỚI CÂU ("phần 1/2").

    Tách chứ không cắt: cắt là bỏ mất đúng phần ý mà vai này sinh ra để giữ.
    Hình, ý chính và lời nói ở lại phần đầu; cụm tô sáng đi theo phần chứa nó.
    Công thức là một khối không tách, chiếm chỗ bằng ~`CHO_CONG_THUC` ký tự và
    đi cùng câu dẫn vào nó.
    """
    def dai(t):
        return CHO_CONG_THUC if t.get("cong_thuc") else len(t["chu"])

    out = []
    for s in bo:
        tran = TRAN_TRICH_HINH if s.get("hinh") else TRAN_TRICH
        if s["vai"] != "doan_dich" or sum(dai(t) for t in s["trich"]) <= tran:
            out.append(s)
            continue
        don = []   # (mã, chữ, là công thức)
        for t in s["trich"]:
            if t.get("cong_thuc"):
                don.append((t["id"], "", True))
            else:
                don += [(t["id"], c, False) for c in _CAU.split(t["chu"]) if c.strip()]
        phan, cur, n = [], [], 0
        for bid, c, ct in don:
            w = CHO_CONG_THUC if ct else len(c)
            # công thức không mở đầu một phần mới: nó đi theo câu dẫn vào nó
            if cur and not ct and n + w > (tran if not phan else TRAN_TRICH):
                phan.append(cur)
                cur, n = [], 0
            cur.append((bid, c, ct))
            n += w + 1
        if cur:
            phan.append(cur)
        for k, p in enumerate(phan):
            trich = []
            for bid, c, ct in p:
                if ct:
                    trich.append({"id": bid, "cong_thuc": True, "chu": ""})
                elif trich and trich[-1]["id"] == bid and not trich[-1].get("cong_thuc"):
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


def _nen_nhe(doc: dict) -> str:
    """Ngữ cảnh rút gọn cho lượt VIẾT CHỮ của bộ chi tiết: tóm lược, mạch lập luận,
    bảng thuật ngữ — vài trăm token thay cho toàn văn bài (~24k token trên CIRAG).

    Cùng lối với pass đánh dấu câu (`pipeline._insight_context`, giảm ~80% giá).
    Lượt lên khung đã đọc cả bài và chọn sẵn khối nguồn cho từng slide; lượt viết
    chỉ cần đúng các khối ấy (`nguon_chu`), đọc lại cả bài mỗi mẻ là trả tiền thừa.
    """
    from .pipeline import _insight_context

    br = doc.get("brief") or {}
    gl = [f"- {g.get('en', '')} → {'(giữ nguyên)' if g.get('keep_en') else g.get('vi', '')}"
          for g in br.get("glossary") or []]
    return ("# Bài báo: " + (br.get("title_vi") or doc.get("title") or "") + "\n\n"
            + _insight_context(doc)
            + ("## Bảng thuật ngữ (dùng đúng các tên này)\n" + "\n".join(gl) + "\n" if gl else ""))


def _nguon_chu(doc: dict, x: dict) -> list[dict]:
    """Văn bản các khối nguồn của một slide (bản dịch, chưa dịch thì bản gốc)."""
    ids = list(x.get("nguon") or [])
    if x.get("hinh"):
        ids.insert(0, x["hinh"])   # chú thích hình/bảng, hoặc chữ bóc được của công thức
    out, tong = [], 0
    for i in dict.fromkeys(ids):
        chu = _chu_khoi(doc, i)[0][:1500]
        if i == x.get("hinh"):
            trong = _rut_gon(chu_vung_hinh(doc, i), 900)
            if trong:
                chu += "\nChữ và số trong hình/bảng (theo dòng):\n" + trong
        if chu and tong < 4000:
            out.append({"id": i, "chu": chu})
            tong += len(chu)
    return out


async def _goi(doc: dict, task: str, user: str, *, max_tokens: int, tran: float,
               usage, nen: str | None = None, model: str | None = None) -> tuple[dict | None, str]:
    """Một lượt gọi model trả JSON, thử lại MỘT lần. Trả (dữ liệu | None, lý do hỏng).

    Tắt hẳn nghĩ thầm. Đo trên bài World Models với DeepSeek V4 Flash: mức "low"
    vẫn tiêu 12.812 token nghĩ thầm, chạm trần 16.000 rồi trả về CHUỖI RỖNG — người
    dùng chờ 360 giây, trả tiền hai lượt, nhận "không đọc được". Cùng bẫy đã ghi cho
    bài giảng của kho survey. Độ sâu ở đây đến từ khuôn vai + trần chữ, không từ
    token nghĩ thầm. Lượt hỏng vẫn cộng vào `usage` — nó vẫn bị tính tiền.
    """
    from .pipeline import NO_REASONING, cached_prefix

    model = model or doc["model"]
    sysmsg = llm.system_message(cached_prefix(doc) if nen is None else nen, task, model=model)
    loi = ""
    for _ in range(2):
        try:
            raw, u = await asyncio.wait_for(llm.complete(
                [sysmsg, {"role": "user", "content": user}],
                model=model, session_id=doc["id"], max_tokens=max_tokens,
                temperature=0.4, reasoning=NO_REASONING), timeout=tran)
            usage.add(u)
            if not raw.strip():
                loi = "model trả về rỗng (hết trần token trước khi viết xong)"
                continue
            try:
                return llm.extract_json(raw), ""
            except ValueError:
                vot = _vot_slides(raw)
                if vot:
                    return {"slides": vot}, "JSON hỏng giữa chừng, vớt được " + str(len(vot)) + " slide"
                raise
        except asyncio.TimeoutError:
            loi = f"model không trả lời sau {int(tran)} giây"
        except ValueError:
            loi = "model trả về nội dung không đọc được"
    return None, loi


def _vot_slides(raw: str) -> list[dict]:
    """Vớt những slide đã viết TRỌN khỏi một JSON bị cắt hoặc hỏng giữa chừng.

    Pass này trả về một DANH SÁCH, nên một vòng lặp suy biến ở slide thứ 13 (đã
    gặp trên V4 Flash: `"y_chinh":":":":…` tới hết trần token) không được phép
    vứt cả 12 slide viết đúng trước nó — cùng lối với `_vot_marks` của pass đánh
    dấu câu.
    """
    # Vá trước khi quét: một nháy thừa làm lệch trạng thái "đang trong chuỗi" tới
    # hết văn bản, và mọi ranh giới slide phía sau đều mất theo.
    raw = llm._va_nhay(llm._va_escape(raw))
    i = raw.find("[", raw.find('"slides"'))
    if i < 0:
        return []
    out, sau, bat_dau, trong_chuoi, thoat = [], 0, -1, False, False
    for k in range(i + 1, len(raw)):
        c = raw[k]
        if trong_chuoi:
            if thoat:
                thoat = False
            elif c == "\\":
                thoat = True
            elif c == '"':
                trong_chuoi = False
            continue
        if c == '"':
            trong_chuoi = True
        elif c == "{":
            if sau == 0:
                bat_dau = k
            sau += 1
        elif c == "}":
            sau -= 1
            if sau == 0 and bat_dau >= 0:
                try:
                    out.append(llm.extract_json(raw[bat_dau:k + 1]))
                except ValueError:
                    pass
                bat_dau = -1
        elif c == "]" and sau == 0:
            break
    return [x for x in out if isinstance(x, dict)]


def _bao_hong(doc_id: str, usage, loi: str):
    from .pipeline import HetGio

    _cong_chi_phi(doc_id, usage)
    if loi.startswith("model không trả lời"):
        raise HetGio(loi + ", đã thử hai lần")
    raise ValueError(f"Chưa soạn được: {loi}, đã thử hai lần. "
                     "Thử lại, hoặc đổi model ở nút ▾ cạnh nút Dịch.")


async def _tao_mot_luot(doc: dict, so: int, usage, model: str) -> list[dict]:
    """Bộ ngắn (≤ `CHI_TIET_TU` slide): MỘT lượt viết cả bộ."""
    from .pipeline import full_source_text

    data, loi = await _goi(doc, SLIDE_TASK, _user(doc, so), max_tokens=20000,
                           tran=tran_slide(len(full_source_text(doc["blocks"]))), usage=usage, model=model)
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

- Theo đúng SÁU chặng ở trên. Đoạn trích đi cùng chặng của nội dung nó: đoạn
  công trình liên quan nằm trong chặng Hướng tiếp cận dù bài đặt mục ấy ở đâu.
- Phần phương pháp và thí nghiệm: MỖI đoạn quan trọng là một slide `doan_dich`
  (1–3 đoạn liền nhau trong cùng một mục). Chen `co_che` trước nhóm đoạn của mỗi
  thành phần, `cong_thuc` cho công thức chính, `thiet_lap` trước kết quả,
  `bang_chung` cho MỌI bảng/hình kết quả chính.
- BẮT BUỘC một slide `vi_du` đi hết hệ thống bằng một câu hỏi/đầu vào cụ thể
  (dùng lại ví dụ của slide `van_de` nếu được), đặt ngay sau phần phương pháp.
- Không có hai slide nói cùng một ý: trích đoạn đã nói đủ thì bỏ slide cơ chế
  trùng nó, và ngược lại. Mỗi bảng/hình chỉ một slide bằng chứng.
- Mọi slide KHÔNG phải `doan_dich` phải có `nguon`: 1–4 mã khối chứa nội dung
  của nó. Lượt viết chữ CHỈ được đọc các khối này, không đọc lại cả bài.
- Ít nhất MỘT NỬA số slide là `doan_dich`. Không bỏ trống mục nào của thân bài.
  Không dùng thư mục tham khảo.
- Kết bằng `gioi_han` rồi `dong_lai`.
- Mỗi slide `doan_dich` có `muc_do`: 1 = thiếu nó người nghe hiểu sai hoặc thiếu
  ý chính của bài, 2 = nên có, 3 = chi tiết thêm. Bộ dài quá thì công cụ bỏ bớt
  mức 3 rồi mức 2, KHÔNG bao giờ bỏ mức 1 — nên chấm thật, đừng chấm hết là 1.

Chỉ trả về JSON:
{"slides": [{"vai": "huong_nc", "tieu_de": "…", "nguon": ["b14", "b15"]},
            {"vai": "khai_niem", "tieu_de": "…", "nguon": ["b33"]},
            {"vai": "doan_dich", "tieu_de": "…", "doan": ["b31", "b32"], "muc_do": 1, "hinh": ""},
            {"vai": "co_che", "tieu_de": "…", "nguon": ["b40"], "hinh": "b43"}, …]}
"""

VIET_TASK = SLIDE_TASK.replace(
    "## Việc lúc này: viết nội dung cho bộ slide trình bày bài báo trên",
    "## Việc lúc này: viết chữ cho các slide của một bài báo đã lên khung") + """
### Lần này VIẾT CHỮ cho các slide đã lên khung

Lượt này bạn KHÔNG có toàn văn bài: chỉ có bối cảnh ở trên và `nguon_chu` (văn
bản các khối nguồn) của từng slide. Mọi chi tiết và con số chỉ lấy từ đó.

Giữ đúng `vai` và `hinh` đã cho. Tiêu đề chỉ được sửa câu chữ cho đúng văn phong,
không đổi ý. Slide `doan_dich` đã có sẵn phần trích nguyên văn (`trich`, chỗ
`[công thức]` là ảnh công thức). KHÔNG chép lại nó, chỉ viết `diem_chinh`,
`nhan_manh` (chép nguyên văn cụm trong `trich`) và `loi_noi` (20–40 chữ, chỉ nói
điều đoạn trích CHƯA nói: vì sao nó quan trọng, nối với slide trước). Slide khác:
`loi_noi` 40–70 chữ.

Trả về JSON {"slides": [{"id": "t5", …các trường của vai…}, …]}, đủ MỌI slide
được giao, giữ nguyên `id`.
"""

ME_VIET = 10          # số slide mỗi lượt viết chữ (ngữ cảnh rút gọn nên mẻ to hơn vẫn nhẹ)
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


_CONG_TRINH = re.compile(r"công trình liên quan|nghiên cứu liên quan|related work|background", re.I)


def _doi_cong_trinh_lien_quan(doc: dict, khung: list[dict]) -> list[dict]:
    """Trích đoạn thuộc mục công trình liên quan dời lên NGAY SAU phần bài toán.

    Bài báo hay đặt mục này sau phương pháp, và lượt lên khung đi theo thứ tự bài
    nên chép luôn chỗ đặt đó — subagent đọc slide chê "đứt mạch" đúng chỗ này.
    """
    cua = [x for x in khung if x["vai"] == "doan_dich" and x.get("trich")
           and _CONG_TRINH.search(_muc_cua(doc, x["trich"][0]["id"]))]
    if not cua:
        return khung
    con = [x for x in khung if x not in cua]
    moc = max((i for i, x in enumerate(con) if x["vai"] in ("van_de", "yeu_cau", "huong_nc")), default=-1)
    return con[:moc + 1] + cua + con[moc + 1:]


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


async def _tao_chi_tiet(doc: dict, so: int, usage, model: str) -> list[dict]:
    """Bộ chi tiết: lên khung (chỉ mã đoạn) → tách trích đoạn dài → viết chữ theo
    mẻ song song. Mẻ viết hỏng thì slide vẫn còn khung, kèm cờ để viết lại."""

    from .pipeline import full_source_text

    tran = tran_slide(len(full_source_text(doc["blocks"])))
    data, loi = await _goi(doc, KHUNG_TASK, _user(doc, so) + _do_dai_doan(doc, so),
                           max_tokens=12000, tran=tran, usage=usage, model=model)
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
    # Sắp theo SÁU chặng như bộ ngắn — trích đoạn và khái niệm đi theo slide đứng
    # trước, nên thứ tự bài trong mỗi chặng vẫn giữ nguyên.
    khung = _sap_lai(khung)
    khung = _cat_theo_muc_do(_tach_trich(_doi_cong_trinh_lien_quan(doc, khung)), so)
    for i, x in enumerate(khung, 1):
        x["_tam"] = f"t{i}"

    tieu = "\n".join(f"{x['_tam']}. [{x['vai']}] {x['tieu_de']}" for x in khung)
    nen = _nen_nhe(doc)
    gate = asyncio.Semaphore(SONG_SONG_VIET)

    async def viet(me: list[dict], lan: int = 0) -> dict:
        # Giao việc bằng VĂN BẢN, không bằng JSON: đưa đoạn trích vào dưới khoá
        # `trich` thì model chép nguyên khoá ấy ra đầu ra (đã gặp trên V4 Flash, rồi
        # kẹt lặp `":":":…` ở trường kế tiếp tới hết trần token).
        ids = [x["_tam"] for x in me]
        phan = []
        for x in me:
            dau = f"### {x['_tam']} · vai `{x['vai']}` · tiêu đề: {x['tieu_de']}" + (f" · hình {x['hinh']}" if x.get("hinh") else "")
            if x["vai"] == "doan_dich":
                than = ("Đoạn trích đã nằm sẵn trên slide (KHÔNG chép lại):\n"
                        + "\n".join(t["chu"] or "[công thức]" for t in x["trich"]))
            else:
                than = "Văn bản nguồn:\n" + "\n".join(f"[{n['id']}] {n['chu']}" for n in _nguon_chu(doc, x))
            phan.append(dau + "\n" + than)
        user = (_user(doc, len(khung)) + "\n## Khung cả bộ (chỉ để biết slide trước/sau, KHÔNG viết các slide này)\n"
                + tieu + "\n\n## Các slide cần viết chữ lượt này\n\n" + "\n\n".join(phan)
                + f"\n\nViết ĐÚNG {len(ids)} slide có id {', '.join(ids)} rồi dừng. Slide `doan_dich` chỉ "
                "trả `id`, `diem_chinh`, `nhan_manh`, `loi_noi`.")
        async with gate:
            # Trần co theo mẻ (~450 token/slide): vòng lặp suy biến tự dừng sớm
            # thay vì đốt hết 8.000 token mỗi lượt.
            d, loi = await _goi(doc, VIET_TASK, user, max_tokens=450 * len(me) + 800,
                                tran=min(240.0, tran), usage=usage, nen=nen, model=model)
        xong = {str(y.get("id")): y for y in (d or {}).get("slides") or []
                if isinstance(y, dict) and str(y.get("id")) in ids}
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

    # Mẻ đầu chạy MỘT MÌNH rồi mới mở loạt song song — cùng lý do với dịch song
    # song. Mọi mẻ mở đầu bằng cùng một khối (luật + bối cảnh + khung cả bộ, ~9k
    # token); bắn cả loạt lúc cache nguội thì mẻ nào cũng trả giá đầy đủ. Đo trên
    # CIRAG 45 phút: ~75k token vào không trúng cache, gần nửa hoá đơn.
    me = [khung[i:i + ME_VIET] for i in range(0, len(khung), ME_VIET)]
    kq = [await viet(me[0])] if me else []
    kq += await asyncio.gather(*(viet(m) for m in me[1:]))
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


async def tao(doc_id: str, phut: int = 15, model: str | None = None) -> tuple[dict, dict, dict]:
    """Sinh cả bộ slide. Trả (bộ slide, chi phí lượt này, cộng dồn).

    `model` riêng cho slide: mặc định giao diện chọn model RẺ hơn giữa model của
    bài và model mặc định. Bộ chi tiết phần lớn là trích nguyên văn bản dịch nên
    không cần model dịch đắt — đo trên CIRAG: V4 Pro $0,18, V4 Flash rẻ hơn hàng
    chục lần cho cùng khuôn.
    """
    doc = store.load(doc_id)
    phut = phut if phut in SO_SLIDE else 15
    so = SO_SLIDE[phut]
    model = model or doc["model"]
    usage = llm.Usage()
    chi_tiet = so > CHI_TIET_TU
    bo = await (_tao_chi_tiet(doc, so, usage, model) if chi_tiet else _tao_mot_luot(doc, so, usage, model))
    if not bo:
        _cong_chi_phi(doc_id, usage)
        raise ValueError("Model không trả về slide nào dùng được. Thử lại.")
    doc = store.load(doc_id)
    _mot_hinh_mot_cho(bo)
    _soat_ca_bo(bo)
    _soat_viet_tat(doc, bo)
    if chi_tiet:
        _soat_do_phu(doc, bo)
    bo = _danh_ma([_mo_dau(doc), _lo_trinh()] + bo)
    tong = llm.Usage(**doc.get("usage", {}))
    tong.add(usage)
    doc["usage"] = tong.dict()
    doc["slides"] = {"v": 2, "bo": bo, "phut": phut, "tao_luc": time.time(),
                     "chi_phi": usage.dict(), "model": model}
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
    model = (doc.get("slides") or {}).get("model") or doc["model"]
    raw, usage = await llm.complete(
        [llm.system_message(cached_prefix(doc), VIET_LAI_TASK, model=model),
         {"role": "user", "content": user}],
        model=model, session_id=doc_id, max_tokens=3000,
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
_SUA_DUOC = {"tieu_de", "loi_noi", "hinh", "minh_hoa", "du_lieu", "do_do", "thanh_phan", "dai_dien",
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
            for t in s["trich"]:
                kb = by_id.get(t["id"]) if t.get("cong_thuc") else None
                if kb and kb.get("figure"):
                    t["anh"] = kb["figure"]
                    t["anh_ver"] = "_".join(str(round(v)) for v in (kb.get("figure_rect") or []))
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
