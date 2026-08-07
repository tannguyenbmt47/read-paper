# Đọc Thấu

> *Dịch máy cho bạn chữ. Công cụ này cho bạn hiểu.*

Phiên bản 1.4.0 · chạy local · [DOCKER.md](DOCKER.md) để chạy bằng Docker

| | |
|---|---|
| **Tiền xử lý** | PDF → khối văn bản, cắt hình/bảng thành PNG. Soát tay trước khi tốn tiền. |
| **Dịch** | Chốt thuật ngữ trước, giữ liên từ lý lẽ, không đổi độ mạnh khẳng định. |
| **Giải thích** | Từng đoạn đang làm gì trong lập luận — vai trò, nối với đoạn trước, chỗ dễ hiểu nhầm. |
| **Bôi vàng** | 5 màu, ghi chú sửa được như comment, nhờ model giải thích đúng đoạn đã bôi. |
| **Hỏi đáp** | Toàn văn bài nằm sẵn trong ngữ cảnh. |
| **Slide** | Soạn nội dung → bạn soát → dựng slide khẳng-định-và-bằng-chứng, sửa thẳng trên slide, trình chiếu, xuất PDF/HTML/PPTX. |

### Có gì mới ở 1.4.0

- **Làm slide tách làm hai bước.** Trước đây một lượt gọi model phải vừa nghĩ nội
  dung vừa lo bố cục, icon, sơ đồ, ngân sách chữ — và phần lớn chú ý rơi vào khuôn
  dạng, nên nội dung ra nhạt. Nay: **① Soạn nội dung** dựng dàn ý (mỗi mục một câu
  khẳng định + bằng chứng + các ý), bạn soát và sửa thẳng vào ý; **② Dựng slide**
  mới dựng thành slide, theo từng mẻ 4 mục nên mỗi slide được viết chi tiết hơn
  hẳn. Đo trên một bài thật: 129 chữ/slide (trước 90), số slide có cảnh báo giảm
  từ 7/16 xuống 0/16.
- **Sửa 4 lỗi bố cục chỉ thấy được khi soát bằng trình duyệt** — `.vis` không bị
  chặn chiều cao nên ảnh hiện cỡ gốc và sơ đồ mermaid phình tới 4000px (10/20
  slide tràn khung); sơ đồ vẽ ở cỡ tự nhiên nên bé bằng con tem; ô công thức
  không co theo autofit; thanh công cụ dính trên đầu che mất tiêu đề slide.
- **Bằng chứng và thẻ không còn tranh chỗ** — slide có hình/sơ đồ thì tối đa 2
  thẻ, slide 3–4 thẻ thì không gắn sơ đồ trang trí, và sơ đồ cạnh thẻ phải nằm
  ngang. `check_slides` cảnh báo cả ba trường hợp.
- **Bỏ một hàm `slideLayout()` chết** — có hai bản trong `app.js`, bản dưới đè bản
  trên; bản đang chạy lại thiếu một luật so với server nên bản xem trước nói dối.

### Có gì mới ở 1.3.0

- **Chọn phần để dịch** — nút ☑ liệt kê từng mục của bài kèm số khối đã dịch và
  giá riêng; tick mục nào thì chỉ dịch mục đó. Mẻ không chứa khối được chọn thì
  không gửi request nào.
- **Nhận ra phần phụ lục** — phụ lục nằm sau mục tham khảo nên trước đây bị nuốt
  hết vào "reference": hiện tiêu đề mà không có nội dung.

### Có gì mới ở 1.2.0

- **Bố cục tự do cho từng slide** — bật cho riêng một slide thì mọi phần chuyển
  sang khung tuyệt đối, **chụp từ chính vị trí đang hiển thị**: kéo tiếp chứ
  không phải bày lại. Kéo để di chuyển, 8 nút vuông để co giãn, mũi tên nhích
  0,5% (Shift = 2%), hút mép và đường gióng khi trùng. Bấm đúp mới vào sửa chữ —
  như Google Slides. Các slide khác giữ nguyên bố cục tự sắp.
- **Bộ test** — 76 test ở 1.4.0, `pytest`. `tests/test_unit.py` cho logic thuần (~3 giây),
  `tests/test_api.py` gọi API thật trên DB tạm, không gọi model nên miễn phí.
- **Chống tràn khung viết lại** — thay bản mô phỏng flexbox bằng vòng autofit
  chạy trong trình duyệt (đo `scrollHeight`, giảm cỡ chữ, đo lại), đúng thuật
  toán `normAutofit fontScale` của PowerPoint. Kèm sàn `max()` cho chữ nhỏ.
- **Đánh số phần** trên nhãn đầu mục, khớp số ở mục lục.
- **Ô chờ ảnh chỉ hiện khi còn chỗ thật** — slide đã kín thì nói thẳng là hết chỗ.

### Có gì mới ở 1.1.0

- **Tiến trình nạp bài** — thanh chạy, bước đang làm và đồng hồ đếm giây. Bước
  chạy mô hình bố cục mất hàng chục giây; trước đây nút đứng im nên không phân
  biệt được đang chạy với đã treo.
- **Ẩn khối rác ngay khi đọc** — nhãn trục lạc ra từ hình, dòng chân trang…
  Nút ⊘ trên mọi khối. Ẩn chứ không xoá: bản dịch giữ nguyên, hiện lại được, và
  khối ẩn bị loại khỏi mẻ dịch nên phần chưa dịch không tốn thêm.
- **Màn slide tải lười** — trước đây mở màn slide là tải cả 22 ảnh (589 KB) về
  chỉ để đọc kích thước mà chọn bố cục. Nay server tính tỉ lệ bằng PIL (chỉ đọc
  header) và trả trong một request 293 byte; ảnh chỉ tải khi thật sự hiện ra.
  Đo bằng trình duyệt: **22 request ảnh → 1**.
- **Vào màn slide không còn bị chặn** — trước đây bài thiếu dù một khối là nút
  bị khoá và bấm vào không báo gì. Nay vào lúc nào cũng được; cảnh báo chuyển
  sang đúng chỗ tốn tiền là lúc bấm *Dựng slide*.

Công cụ dịch bài báo khoa học Anh → Việt **giữ nguyên mạch lập luận**, chạy local,
gọi model qua [OpenRouter](https://openrouter.ai).

Khác biệt so với các công cụ dịch PDF sẵn có (Immersive Translate, PDFMathTranslate/BabelDOC):
chúng giữ **layout**, công cụ này giữ **lý lẽ** — và giải thích được từng đoạn đang
làm gì trong lập luận của bài.

```
./run.sh          # lần đầu sẽ tự tạo .env, điền key rồi chạy lại
# mở http://localhost:8000  (đổi cổng: PORT=8010 ./run.sh)
```

---

## Vấn đề nó giải quyết

AI dịch paper thường mất logic vì bốn lý do có thể chỉ đích danh:

| Lý do | Biểu hiện | Cách xử lý ở đây |
|---|---|---|
| Dịch từng đoạn rời rạc | Mất từ nối lập luận; `while`/`since` dịch sai nghĩa | Nạp **toàn văn** vào ngữ cảnh, bảng ánh xạ liên từ bắt buộc |
| Không chốt thuật ngữ | Một thuật ngữ dịch 3 kiểu trong cùng bài | Pass riêng **chốt glossary trước khi dịch** |
| Đổi độ mạnh khẳng định | `may` → "sẽ", `suggests` → "chứng minh" | Bảng hedging cấm nâng/hạ cấp |
| Không đối chiếu được | Đọc bản dịch không dò lại được bản gốc | Song ngữ căn theo đoạn + lớp giải thích |

## Cách nó chạy — hai bước tách bạch

```
╔═ BƯỚC 1 · TIỀN XỬ LÝ ═══════════ không gọi model, không tốn tiền ═╗
║  PDF / arXiv / văn bản dán                                        ║
║    ├─ phân loại chữ: chữ của bài  vs  chữ nằm trong hình          ║
║    ├─ tách khối: đoạn, mục, caption, công thức, tài liệu tham khảo║
║    ├─ cắt hình & bảng ra ảnh                                      ║
║    └─ loại header/footer kỷ yếu, footnote, nhãn trong sơ đồ       ║
║                          ↓                                        ║
║  Màn hình kiểm tra: xem hình cắt đúng chưa, loại khối rác,        ║
║  và xem trước sẽ tốn bao nhiêu tiền                               ║
╚═══════════════════════════════════════════════════════════════════╝
                           ↓  bạn bấm xác nhận
╔═ BƯỚC 2 · DỊCH ═══════════════════════════════════════════════════╗
║  Pass 1  đọc toàn bài → tóm lược + mạch lập luận                  ║
║          + BẢNG THUẬT NGỮ CHỐT + sơ đồ                            ║
║  Pass 2  dịch từng mẻ, mang theo toàn văn + tóm lược + glossary   ║
║  Pass 2b (tuỳ chọn "Dịch kỹ") soát lại, đối chiếu với bản gốc     ║
║  Pass 3  giải thích từng đoạn — chạy khi bạn bấm 💡, kèm sơ đồ    ║
╚═══════════════════════════════════════════════════════════════════╝
```

Tách hai bước vì chúng hỏng theo hai kiểu khác nhau và sửa theo hai cách khác
nhau. Bóc tách sai thì dịch có giỏi mấy cũng vô nghĩa — mà bóc tách lại **miễn
phí**, nên phải soát nó trước khi tiêu tiền vào bước 2.

### Bóc hình và bảng: hai tầng

**Tầng 1 — mô hình bố cục (khuyến nghị).** Cài thêm `docling` thì tool dùng mô
hình phát hiện bố cục để lấy hộp bao của từng bảng/hình. Đây là cách duy nhất xử
lý đúng trang có nhiều bảng và hình xếp sát nhau — heuristic suy vùng từ vị trí
chú thích không tách nổi những trang như vậy.

```bash
.venv/bin/pip install docling      # ~5GB, tự dùng GPU nếu có
```

Đo trên paper ACL 2023 (9 bảng/hình, có trang chứa 3 đối tượng):

| | Hình bóc đúng | Cặp khung trùng nhau |
|---|---|---|
| Heuristic | 8/9 | 1 |
| Mô hình bố cục | **9/9** | **0** |

Lần chạy đầu sau khi cài mất vài phút (tải trọng số + biên dịch). Server tự làm
nóng lúc khởi động, nên các lần nạp bài sau chỉ tốn ~5 giây. Tắt bằng ô *Mô hình
bố cục* trên màn hình nhập, hoặc `LAYOUT_BACKEND=off` trong `.env`.

**Tầng 2 — heuristic, dùng khi không cài docling: theo PDFFigures 2.0**

Cách làm trực giác — "cắt vùng phía trên caption cho tới đoạn văn gần nhất" — hỏng
liên tục, vì chữ *bên trong* hình cũng là khối văn bản hợp lệ với PyMuPDF: nhãn
sơ đồ, ô bảng, thậm chí cả câu văn hoàn chỉnh nằm trong khung minh hoạ.

Thuật toán của [PDFFigures 2.0](https://ai2-website.s3.amazonaws.com/publications/pdf2.0.pdf)
(Allen AI, chạy cho Semantic Scholar, 94% precision / 90% recall) đảo ngược thứ tự:
**phân loại chữ trước, suy vùng hình sau**. Phần lớn chữ trong một bài là chữ thân
bài và trình bày nhất quán, nên cái gì lệch chuẩn thì là chữ trong hình:

| Dấu hiệu | Kết luận |
|---|---|
| Đè lên cụm đồ hoạ | chữ trong hình |
| Cỡ chữ nhỏ hơn chuẩn của bài | chữ trong hình |
| Nhiều khoảng cách giữa các từ rộng bất thường | thân bảng |
| Nhiều dòng, rộng đúng bề ngang cột | chữ của bài |
| Cỡ chữ lớn hơn chuẩn + canh lề hoặc canh giữa | tiêu đề bài / tên mục |
| Canh đúng mép cột | chữ của bài |

Rồi mới: nới từ caption ra tới khối **chữ của bài** gần nhất, và co lại quanh cụm
đồ hoạ lớn nhất trong vùng đó. Bước co này là thứ ngăn ảnh nuốt cả tiêu đề và tên
tác giả — chúng là chữ của bài nhưng nằm cách hình một quãng trắng.

**Về chi phí.** Phần system prompt (luật dịch + toàn văn bài + glossary) là
byte-identical ở mọi request của cùng một bài và luôn nằm trước điểm cache —
đo thực tế: **99% system prompt nằm trong vùng cache**, dùng chung cho cả 4 tác
vụ (dịch, soát, giải thích, hỏi đáp). Với model `anthropic/*` điểm cache được
đánh dấu thủ công; OpenAI/Gemini/DeepSeek cache tự động. `session_id` giữ mọi
request về cùng một provider endpoint, nếu không thì cache gần như không hit.

## Tính năng

- **Màn hình kiểm tra trước khi dịch** — xem hình cắt ra có đúng không, loại khối
  rác, và biết trước chi phí (lấy giá thật của model từ OpenRouter).
- **Tự kéo khung cắt hình** — hình nào cắt sai thì bấm ✂, trang PDF hiện lên với
  khung hiện tại, kéo tám nút ở viền để chỉnh rồi lưu. Cắt lại ở DPI cao hơn nên
  nét hơn bản tự động. Caption nào máy không cắt được cũng cắt tay được.
- **Song ngữ căn theo đoạn** — các cột nằm cùng một hàng lưới, cuộn là tự khớp,
  không cần đồng bộ scroll.
- **Hình và bảng gốc** cắt thẳng từ PDF, hiện ngay trên caption của nó.
- **Sơ đồ Mermaid** — mạch lập luận toàn bài, cơ chế bài đề xuất, và sơ đồ riêng
  cho từng đoạn khi bấm Giải thích.
- **Bảng thuật ngữ** chốt trước khi dịch, tra được, có giải thích nghĩa.
- **Giải thích lập luận** cho từng đoạn: ý chính · vai trò trong bài · nối với
  đoạn trước ra sao · giải thích chi tiết · hình dung · điều tác giả *không*
  khẳng định · câu hỏi tự kiểm tra.
- **Hỏi đáp** về bài, toàn văn đã nằm sẵn trong ngữ cảnh.
- **Ba cột bật/tắt độc lập** — Gốc · Việt · Giải thích. Cột nào tắt thì **không
  sinh ra**, tức không trả tiền cho nó. Tắt cột Việt thì chỉ sinh phần giải thích.
- **Cột giải thích cho người chưa có nền** — nói lại đoạn bằng lời thường, định
  nghĩa khái niệm mới ngay tại chỗ, giải thích cơ chế và vai trò trong lập luận.
  Cấm ẩn dụ; muốn làm rõ thì dùng ví dụ cụ thể lấy từ chính bài.
- **Báo giá từng lượt** — mỗi mẻ dịch, mỗi lần giải thích đều hiện chi phí lượt
  đó, cộng dồn phiên này và cộng dồn cả bài.
- **Xuất PDF, HTML hoặc Markdown**, song ngữ hoặc chỉ tiếng Việt. Ảnh nhúng
  thẳng vào file nên mở ở đâu cũng còn hình; bản HTML là một file duy nhất, đọc
  offline, sơ đồ vẫn vẽ được. PDF đi qua hộp in của trình duyệt — cách duy nhất
  giữ được cả sơ đồ lẫn lưới hai cột.
- **Dừng dịch giữa chừng** — bấm Dừng thì chạy nốt mẻ đang dở (mẻ đó đã trả tiền
  rồi) rồi ngừng. Dịch dở bỏ đó, mở lại dịch tiếp, không dịch lại phần đã xong.
- **Làm slide qua hai bước** — bấm *Soạn nội dung* thì model dựng **dàn ý**: buổi
  nói kể chuyện gì, chia mấy phần, mỗi slide chứng minh điều gì và lấy bằng chứng
  nào. Bạn soát và sửa thẳng vào ý — đổi câu khẳng định, thêm bớt ý, chọn hình
  khác, đổi thứ tự — rồi mới bấm *Dựng slide*. Bước soạn rẻ hơn hẳn bước dựng,
  nên sửa ở đó không tốn gì. Slide bạn đã sửa tay thì dựng lại không đè lên.
- **Chọn phần để dịch** — nút ☑ liệt kê từng mục của bài (kèm số khối đã dịch và
  giá riêng của mục đó); tick mục nào thì chỉ dịch mục đó. Bài dài thường chỉ cần
  Phương pháp và Kết quả, còn phụ lục thì để đó — không trả tiền cho phần không đọc.
- **Sửa khối ở bước 1** — bỏ hẳn khối rác, gộp đoạn bị cắt đôi ở chỗ nhảy cột,
  tách hai đoạn bị dính làm một.
- **Xem PDF gốc song song** — mở khung bên phải, tự lật trang theo đoạn đang đọc,
  bấm vào đoạn nào là nhảy tới trang của đoạn đó.
- **Tìm trong bài** (Ctrl+F) trên cả ba cột, nhớ chỗ đang đọc dở, chỉnh cỡ chữ
  và bề rộng cột, đổi nền sáng/tối.
- **Căn chỉnh lại bằng model rẻ** — PDF không lưu khoảng trắng cũng không lưu
  cấu trúc, nên chữ bóc ra hay dính nhau (`=∅or`), đứt gạch nối, công thức đảo
  mảnh. Một model rẻ dọn lại (~$0.001/bài). Nó **chỉ** được sửa khoảng trắng và
  thứ tự: bội ký tự chữ-số trước/sau phải khớp nhau, lệch là chặn và giữ bản gốc.
- **Giữ danh sách** — chỗ bài liệt kê gạch đầu dòng thì mỗi mục là một khối
  riêng, dịch riêng, hiện đúng dạng danh sách; không bị nén thành một đoạn chạy dài.
- **Giữ chỉ số trên/dưới của công thức** — `D = {dᵢ}ᴺᵢ₌₁` ghi thành
  `D = {d_{i}}^{N}_{i=1}` chứ không bẹp thành `D = {di}N i=1`, và `ˆa` ghép lại
  thành `â`. Cấu trúc này là thứ model cần để dịch và giải thích cho đúng.

## Cài đặt

Cần Python 3.10+.

```bash
./run.sh
```

Script tự tạo `.venv`, cài phụ thuộc, và tạo `.env` từ mẫu. Mở `.env` điền key
lấy tại <https://openrouter.ai/keys>, rồi chạy lại.

Cấu hình trong `.env`:

| Biến | Ý nghĩa |
|---|---|
| `OPENROUTER_API_KEY` | Bắt buộc |
| `OR_MODEL` | Model dịch. Mặc định `~deepseek/deepseek-v4-flash-latest` |
| `OR_MODEL_FAST` | Model cho việc nhẹ (chưa dùng tới, để dành) |
| `OPENROUTER_BASE_URL` | Đổi khi đi qua proxy nội bộ |
| `PAPER_DATA_DIR` | Nơi lưu bài đã đọc. Mặc định `./data` |

Model đổi được ngay trên giao diện: lúc nạp bài, ở màn hình kiểm tra (bước 1 —
đổi thì giá ước tính tính lại theo model mới), và trên thanh công cụ lúc đang
đọc. Đổi giữa chừng không dịch lại phần đã xong; chỉ mẻ chưa dịch mới chạy bằng
model mới.

| Model | Giá vào/ra mỗi triệu token | Ghi chú |
|---|---|---|
| `~deepseek/deepseek-v4-flash-latest` | $0.09 / $0.18 | Mặc định. 1M context. Cả abstract + intro tốn ~$0.005. Chậm hơn ở lượt đọc toàn bài (~60–75s). |
| `openai/gpt-5.6-luna` | $0.10 / $0.60 | Rẻ gần bằng DeepSeek, 1M context |
| `deepseek/deepseek-v4-pro` | $0.43 / $0.87 | Khá hơn, vẫn rẻ |
| `openai/gpt-5.6-terra` | $1 / $6 | 1M context. Bản `-pro` cùng giá, suy luận sâu hơn |
| `anthropic/claude-sonnet-4.5` | $3 / $15 | Tiếng Việt mượt, đắt hơn DeepSeek ~33 lần |
| `openai/gpt-5.6-sol` | $5 / $30 | Bậc cao nhất của dòng 5.6 |

Dấu `~` là một phần của tên model — OpenRouter dùng nó cho alias tự cập nhật.

## Giới hạn đã biết

- **PDF scan ảnh không đọc được** — cần OCR trước (`ocrmypdf`).
- Bố cục lạ (3 cột, tạp chí, poster) tách khối kém hơn bố cục 1–2 cột chuẩn.
- Heuristic được chỉnh theo bài hội nghị khoa học máy tính (ACL, NeurIPS…), giống
  phạm vi mà PDFFigures 2.0 nhắm tới. Ngành khác có thể lệch.
- Công thức giữ dạng text, **không render LaTeX**. Chỉ số trên/dưới có được giữ
  (`x^{2}`, `d_{i}`) nhưng phân số, tổng, tích phân nhiều tầng thì đọc trong
  khung PDF gốc vẫn hơn.
- **Model suy luận cần ghìm lại.** DeepSeek V4 và dòng GPT-5.x có thể tiêu sạch
  ngân sách token vào phần nghĩ thầm rồi trả về rỗng hoặc JSON dở dang. Tool tắt
  suy luận ở các lượt dịch và ghìm ở mức thấp cho lượt đọc-toàn-bài.
- **Model gốc Trung Quốc có thể trả về tiếng Trung** dù prompt viết bằng tiếng
  Việt. Tool có luật ngôn ngữ tường minh, và tự phát hiện chữ Hán lọt ra rồi gọi
  lại một lần — nhưng chỉ so với bản gốc, nên trích dẫn tiếng Trung thật vẫn giữ.
- Tài liệu tham khảo cố ý **không** dịch.

Đó là lý do có bước 1: khi heuristic sai, bạn thấy ngay và sửa được, thay vì phát
hiện sau khi đã trả tiền dịch cả bài.

### Đã kiểm chứng trên

| Paper | Hình & bảng bóc đúng | Đoạn rác còn sót |
|---|---|---|
| Attention Is All You Need (NeurIPS 2017, 2 cột) | 6/6 | 0 |
| Precise Zero-Shot Dense Retrieval (ACL 2023, 2 cột) | 8/9, 1 cặp khung chồng nhau | 0 |

Hai bảng xếp sát nhau vẫn có thể dính chung một khung. Khi đó bước 1 hiện ra hai
ảnh giống hệt nhau — tách bằng nút ✂. Tool cố ý **không** tự bỏ bớt một cái: bỏ
đi thì bạn mất hẳn một bảng mà không biết là đã mất.

## Bố cục mã nguồn

```
server/
  parser.py    PDF/text → khối có cấu trúc; cắt hình & bảng thành ảnh
  prompts.py   toàn bộ prompt — phần quyết định chất lượng, sửa ở đây trước
  llm.py       bọc OpenRouter: streaming, điểm cache, sticky session
  pipeline.py  điều phối các pass, ghép ngữ cảnh dùng chung
  layout.py    mô hình phát hiện bố cục (Docling), tuỳ chọn
  db.py        SQLite: tài liệu, cache kết quả parse, bộ nhớ dịch
  store.py     mặt tiền của db.py; ảnh và PDF gốc để trên đĩa
  main.py      HTTP API + SSE + xuất PDF/HTML/Markdown
web/           giao diện, không framework
```

Muốn chỉnh chất lượng dịch thì sửa `server/prompts.py` — mọi thứ còn lại chỉ là
ống dẫn.
