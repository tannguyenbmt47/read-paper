# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Chạy và kiểm tra

```bash
./run.sh                 # tạo .venv + cài deps + chạy uvicorn ở 127.0.0.1:8000
PORT=8010 ./run.sh       # đổi cổng
.venv/bin/uvicorn server.main:app --reload --port 8000   # dev, tự nạp lại
```

```bash
.venv/bin/python -m pytest          # 76 test · ~3 phút (phần lớn là import docling)
.venv/bin/python -m pytest tests/test_unit.py -q    # phần logic thuần, ~3 giây
node --check web/app.js             # chưa có test cho frontend
```

`tests/test_unit.py` — logic thuần, không cần server: chốt soát số liệu trên
slide, bộ đo tràn khung, bộ bóc Mermaid, chỉ số trên/dưới.

`tests/test_api.py` — gọi API thật trên `PAPER_DATA_DIR` tạm nên **không đụng
`data/` của người dùng**, và **không gọi model nên miễn phí**. Nó tồn tại vì
những lỗi đã vỡ thật: `PATCH /blocks` từng trả 500 vì một bản vá rơi nhầm hàm
(`NameError`), và cột thêm sau mà quên `ALTER TABLE` thì mọi bài cũ vỡ lúc load.
Chạm vào MỌI endpoint chính là bắt được cả hai loại đó ngay.

Vẫn chưa có linter cấu hình sẵn, và **frontend chưa có test** — phần kéo thả,
contenteditable, toàn màn hình phải kiểm bằng trình duyệt.

Kiểm chứng thật thì phải nạp một PDF qua giao diện — mọi đường dẫn logic đều đi
qua vòng parse → estimate → confirm → translate.

**Bố cục slide thì phải NHÌN, không đo.** `slide_fit.py` chỉ là bản mô phỏng
flexbox viết tay nên luôn thiếu một thứ gì đó (đã vấp: `gap` giữa các con của
`.body`). Chống tràn thật nằm ở vòng autofit chạy trong trình duyệt — đo
`scrollHeight`, giảm `--s`, đo lại, đúng thuật toán `normAutofit fontScale` của
PowerPoint. Muốn soát cả deck thì mở file xuất ra bằng Chromium/Brave qua giao
thức DevTools và hỏi bộ dựng hình, đừng soi từng slide.

`.env` bắt buộc có `OPENROUTER_API_KEY`. `server/main.py` gọi `load_dotenv()`
**trước** khi import các module khác (dòng 11) vì `llm.DEFAULT_MODEL` và
`db.DATA_DIR` đọc biến môi trường ngay lúc import — đừng đảo thứ tự import đó.

Docling (mô hình bố cục) là tuỳ chọn và đã cài trong `.venv` hiện tại. Tắt bằng
`LAYOUT_BACKEND=off`.

## Ngôn ngữ

Toàn bộ docstring, comment, tên nút, thông báo lỗi trả về người dùng đều bằng
tiếng Việt. Code mới viết theo đúng quy ước đó.

## Kiến trúc

Web app một trang, không framework, chạy local. FastAPI phục vụ `web/` tĩnh và
một API JSON + SSE. Trạng thái nằm trong SQLite (`data/papers.db`); ảnh cắt ra và
PDF gốc để nguyên trên đĩa (`data/<id>-img/`, `data/<id>.pdf`).

Luồng chia làm **hai bước tách bạch**, và ranh giới đó có ý nghĩa:

1. **Tiền xử lý** (`parser.py`, `layout.py`) — không gọi model, miễn phí. PDF →
   danh sách `Block`, cắt hình/bảng thành PNG. Người dùng soát ở màn hình
   `#review`, sửa qua `PATCH /api/doc/{id}/blocks` (drop/skip/keep/drop_figure),
   `POST …/blocks/merge`, `POST …/blocks/split`, `POST …/crop/{block_id}`,
   rồi `POST …/confirm`. Mọi thao tác đổi nội dung khối đều gọi `_forget()` để
   xoá bản dịch cũ của khối đó — bản dịch ứng với văn bản cũ mà giữ lại thì hai
   cột lệch nhau không lý do.
   Bước 1 có **một** chỗ tốn tiền: `POST …/relayout` — nhờ `OR_MODEL_FAST` dọn
   lại chữ bóc từ PDF (khoảng trắng dính, gạch nối cuối dòng, mảnh công thức bị
   đảo). Người dùng phải tự bấm, và nút ghi rõ giá.

   **`content_kept()` là chốt chặn của pass này.** Bội ký tự chữ-số trước và sau
   phải bằng nhau, cộng thêm kiểm tra rò chữ Hán. Model chỉ được đổi khoảng
   trắng / dấu ngoặc đánh dấu / thứ tự; thêm hay bớt một chữ là bị chặn và giữ
   nguyên bản gốc. Không có chốt này thì pass tự do sửa nội dung bài báo — đúng
   thứ cả công cụ sinh ra để tránh.

   Hai cái bẫy đã vấp, đừng vấp lại:
   - **Đừng gửi kèm loại khối** (`[para]`) trong dòng có nhãn — model chép lại
     nguyên cái nhãn vào nội dung, bội ký tự lệch, chốt chặn chặn sạch 97/97.
   - **Đừng lọc theo `translate`** khi chọn khối để dọn: công thức luôn có
     `translate=False` mà nó mới là thứ cần dọn nhất.

2. **Dịch** (`pipeline.py`, `prompts.py`, `llm.py`) — tốn tiền. Pass 1 dựng
   brief + glossary một lần cho cả bài; pass 2 dịch từng mẻ qua SSE; pass 2b soát
   lại (tuỳ chọn); pass 3 giải thích từng đoạn khi người đọc bấm; pass 4 dựng bộ
   slide trình bày khi người đọc bấm (xem mục riêng ở dưới).

`server/prompts.py` là nơi quyết định chất lượng dịch. Mọi thứ khác là ống dẫn.

### Bất biến quan trọng nhất: prefix cache

`pipeline.cached_prefix(doc)` ghép luật dịch + brief + glossary + **toàn văn bài**
thành một khối phải **byte-identical giữa mọi request của cùng một bài**, và luôn
đứng trước phần thay đổi. `llm.system_message(prefix, volatile_suffix, model=…)`
đặt điểm cache đúng chỗ đó (model `anthropic/*`, `qwen/*`, `alibaba/*` cần đánh
dấu `cache_control` thủ công; còn lại cache tự động).

Nhét bất cứ thứ gì thay đổi theo request vào `cached_prefix` là hỏng cache và chi
phí nhân lên nhiều lần. Phần thay đổi phải đi vào `volatile_suffix` (các hằng
`*_TASK` / `*_SYSTEM`) hoặc vào message `user`.

`session_id=doc_id` truyền cho mọi lần gọi để OpenRouter giữ sticky routing —
thiếu nó thì request rơi vào provider endpoint khác và cache gần như không hit.

### Giao thức nhãn `<<<id>>>`

Pass dịch trả về văn bản phẳng, mỗi block đánh dấu `<<<b12>>> nội dung`, cột diễn
giải dùng hậu tố `_g`: `<<<b12_g>>> …`. `pipeline._parse_labeled` bóc ra dần
trong lúc stream (phát block khi nhãn kế tiếp xuất hiện) nên chịu được đầu ra bị
cắt cụt. Đổi định dạng nhãn là phải đổi đồng thời `prompts.py`, `_parse_labeled`,
và `split()` trong `stream_chunk`.

Chế độ `mode` (`vi` / `plain` / `both`) do frontend quyết theo hai ô tick cột —
cột nào tắt thì **không sinh ra**, tức không trả tiền. Heading và equation cố ý
không có cột giải thích; `chunkDone()` bên `web/app.js` phải biết điều đó, nếu
không mẻ nào cũng bị coi là chưa xong và dịch lại từ đầu.

### Dịch từng phần

`GET /api/doc/{id}/sections` cắt bài theo tiêu đề mục (`type == "heading"`), trả
`ids` / `blocks` / `done` / `cost_usd` cho từng mục — người dùng tick mục nào thì
`streamChunk` gửi kèm `only=b3,b7,…` và `stream_chunk` lọc `items` theo đó.

Ba chỗ phải khớp nhau, lệch một là hoặc dịch thừa hoặc dịch lại vòng vòng:
- `pickedIds()` trả **`null` khi chọn hết** — không phải danh sách đầy đủ. Có
  `only` là mất luôn ngữ cảnh các khối cùng mẻ, nên đường mặc định phải sạch.
- `runTranslate` bỏ qua hẳn mẻ nào không chứa khối được chọn; mẻ có chứa mà lọc ra
  rỗng thì `stream_chunk` tự trả `{"skipped": true}` chứ không gọi model.
- `chunkDone(i)` chỉ được đòi **phần đã chọn** của mẻ. Đòi cả mẻ thì mẻ nào cũng
  coi là chưa xong, và lần dịch sau chạy lại từ đầu — mất sạch chỗ tiết kiệm.

Mục tham khảo và khối đã ẩn không vào danh sách: trả tiền dịch chúng là vô nghĩa.

### Hai lớp cache trong `db.py` — và bẫy đi kèm

- `parse_cache` khoá theo SHA-256 của **file PDF**. Nạp lại cùng file thì bỏ qua
  hẳn PyMuPDF và mô hình bố cục, chép luôn ảnh từ bài trước (`store.copy_images`).
  → **Sửa `parser.py` xong mà nạp lại cùng PDF sẽ không thấy gì thay đổi.**
- `tm` (bộ nhớ dịch) khoá theo `sha256(text đã chuẩn hoá) | model`. Đoạn đã dịch
  lấy lại miễn phí, kể cả từ bài khác. → **Sửa `prompts.py` xong mà dịch lại cùng
  bài sẽ nhận bản dịch cũ.**
- `doc["brief"]` **đóng băng bảng thuật ngữ** đã chốt. Sửa luật thuật ngữ trong
  `prompts.py` không áp cho bài đã có brief — phải dựng lại brief (nút *Dựng lại
  tóm lược & bảng thuật ngữ* ở cột trái, hoặc `POST /api/doc/{id}/brief` lần nữa,
  endpoint này ghi đè). Đây là chỗ thứ ba dễ tưởng "sửa prompt xong mà không thấy
  gì đổi", sau hai bảng cache dưới đây.

Muốn thấy hiệu quả của thay đổi thì phải xoá cache liên quan:

```bash
.venv/bin/python -c "
import sqlite3; c = sqlite3.connect('data/papers.db')
c.execute('DELETE FROM tm'); c.execute('DELETE FROM parse_cache'); c.commit()"
```

### Chỉ số trên/dưới và dấu phụ

PDF không lưu "đây là chỉ số dưới" — nó chỉ vẽ chữ nhỏ hơn, đặt thấp hơn.
`_line_text()` suy ra mức từ **cỡ chữ + độ lệch baseline** (và bit 0 của
`span["flags"]` cho chỉ số trên) rồi ghi lại thành `^{…}` / `_{…}`. Nối span
thẳng tuột thì `D = {dᵢ}ᴺᵢ₌₁` thành `D = {di}N i=1` — mất sạch cấu trúc, model
dịch sai theo.

TeX cũng đặt dấu mũ bằng glyph rời đứng **trước** chữ (`ˆ` + `a`). `_join_accents()`
ghép chúng lại và **phải chạy trước NFKC**: NFKC biến phần lớn dấu rời thành
*dấu cách + dấu tổ hợp*, tức tự chèn thêm khoảng trắng rồi mới ghép.

Sửa hai chỗ này không tự động áp cho bài đã nạp — `parse_cache` khoá theo SHA của
file PDF (xem phần bẫy cache ở trên), phải xoá cache rồi nạp lại bài.

### Danh sách

`_list_items()` tách một block thành từng mục, mỗi mục là một `Block` riêng mang
`marker`. Tách chứ không giữ chung một khối, vì cột song ngữ căn theo khối — gộp
lại thì ba ý song song thành một đoạn chạy dài, và tầng dịch phải tự đoán chỗ
ngắt mục.

Ngưỡng nhận dạng cố ý chặt, vì dương tính giả tệ hơn âm tính giả:
- Gạch ngang **không** được nhận làm dấu đầu mục — `−E` trong công thức và từ bị
  ngắt gạch nối cuối dòng đều mở đầu bằng gạch.
- Đánh số phải **chạy liên tiếp** và các mục **thẳng hàng mép trái** (lệch ≤2.5pt),
  nếu không thì `(i) cách này, (ii) cách kia` nằm trong câu sẽ bị cắt thành mục.
- Phải có ≥2 mục.

`_to_blocks` không nối đoạn qua ranh giới mục (điều kiện `not marker and not
blocks[-1].marker`), nếu không luật nối-đoạn-bị-cắt sẽ dính các mục lại ngay.

Baseline nhảy vì chỉ số cũng khiến **PyMuPDF cắt block giữa câu** — `D = {d_{i}}^{N}`
kết thúc một block, `_{i=1}, the objective…` mở đầu block sau. `_stitch()` nối lại,
mốc nhận biết là block sau mở đầu bằng `_{` hoặc `^{`: chỉ số không bao giờ mở đầu
một đoạn văn. Nối **không chèn khoảng trắng**.

**`^{…}` / `_{…}` là dạng lưu và dạng gửi cho model, không phải dạng để nhìn.**
Mọi chỗ hiển thị phải đi qua bộ dựng: `sci()` bên `web/app.js` và `rich()` bên
`_export_html`. Cả hai escape trước rồi mới chèn `<sup>`/`<sub>`.

Cẩn thận: `rich()` **chỉ** dùng cho thân bài. Mã Mermaid, thuộc tính `alt` và thẻ
`<title>` phải giữ `esc()` thuần — chèn thẻ vào đó là hỏng sơ đồ và hỏng HTML.

### Hai đường bóc tách — mô hình là đường chính

Có **docling** thì `layout.read()` chạy MỘT lần convert và trả cả cấu trúc văn
bản lẫn vùng hình. `parser.blocks_from_layout()` dựng `Block` từ đó. Phân công:

> **Mô hình quyết định khối nào ở đâu và là loại gì. PyMuPDF cấp glyph.**

Vì thế nhãn (mục / danh sách / công thức / chú thích / footnote / header-footer)
là do mô hình, không còn đoán bằng regex và cỡ chữ. Không có docling thì rơi về
`parse_pdf()` — toàn bộ heuristic cũ vẫn nguyên vẹn làm đường lùi.

Ba chỗ phải tự lo, vì mô hình không giải quyết được:

- **`assign_spans()`** — mỗi span thuộc đúng một khối, chọn khung **nhỏ nhất**
  chứa tâm span. Khung của mô hình chồng nhau được; dùng phép giao (`clip=`) thì
  vùng công thức nuốt luôn chữ của đoạn bên cạnh.
- **`_trim_overlaps()`** — khung công thức hay cao quá tay vì dấu `{ }` nhiều
  tầng, trùm luôn dòng đầu đoạn dưới. Cắt ngang ở đỉnh khung dưới: tâm dấu ngoặc
  vẫn ở trong (ngoặc không mất), tâm dòng văn thì ra ngoài.
- **Thứ tự đọc lấy theo hình học, KHÔNG theo thứ tự mô hình trả về.** Docling
  đôi khi dồn cả cụm công thức xuống sau đoạn văn nằm dưới chúng. Ranh giới khối
  của nó thì đáng tin, nên sắp lại theo (trang, cột, y) là chắc hơn.

`_rows()` gom span theo **baseline của span cỡ thường** rồi mới gắn span nhỏ vào
dòng gần nhất. Gom theo tâm dọc là sai: chỉ số dưới có tâm thấp hơn nên tách
thành dòng riêng, nối lại thì mọi chỉ số bị dồn xuống cuối công thức.

### Bóc hình: hai tầng

Heuristic (`parser.py`) theo PDFFigures 2.0: **phân loại chữ trước** (`_mark_body_text`
dùng cụm đồ hoạ, cỡ chữ, khoảng cách từ, căn lề), rồi mới nới vùng từ caption ra
tới khối chữ-thân-bài gần nhất và co lại quanh cụm đồ hoạ. Khối nào bị xếp là
"chữ trong hình" và nằm trong vùng cắt thì biến mất khỏi mạch đọc.

Docling (`layout.py`) chỉ thay **khung cắt hình**, không đụng vào cấu trúc
đoạn/mục. `apply_layout` ghép vùng với caption theo nhãn (`caption_key`:
`"Table 1"` → `"table1"`) trước, còn lại mới ghép theo khoảng cách dọc cùng trang.
Docling lấy gốc toạ độ ở góc dưới-trái, PyMuPDF ở góc trên-trái — `_to_top_left`
lo việc đổi; quên là mọi khung cắt lật ngược.

### Pass 4 chia làm hai bước — và ranh giới đó là chỗ chất lượng đến từ

Bản đầu gọi model **một lượt** cho cả bộ slide. Nó phải cùng lúc quyết kể chuyện
gì, chia mấy phần, mỗi slide nói gì — **và** chọn icon, dựng thẻ, vẽ Mermaid,
khớp JSON, canh ngân sách chữ. Phần lớn chú ý rơi vào khuôn dạng, nên nội dung ra
nhạt: khẳng định chung chung, thẻ độn cho đủ, sơ đồ ba hộp.

Tách ra, đúng như ranh giới tiền-xử-lý / dịch ở bước 1 — **model đề xuất, người
dùng quyết, rồi mới tới bước tốn tiền**:

1. **`make_outline()` — soạn nội dung.** Chỉ nghĩ về mạch trình bày: `thesis`,
   `sections` (3–4), và mỗi mục có `message` (một câu khẳng định), `evidence`
   (hình nào / sơ đồ gì / số liệu nào), `points` (nội dung viết sẵn thành câu),
   `source_block_ids`. Không icon, không thẻ, không Mermaid, không ngân sách chữ.
   Người dùng soát và sửa ở tab **① Dàn ý** của màn `#slides`, lưu qua
   `PATCH …/outline` (miễn phí).
2. **`render_deck()` — dựng slide từ dàn ý ĐÃ DUYỆT**, theo mẻ `RENDER_BATCH = 4`,
   đẩy tiến trình qua SSE `GET …/slides/build`.

`RENDER_BATCH` nhỏ chính là chỗ "chi tiết" đến từ: dựng cả hai mươi slide trong
một lượt thì mỗi slide được chia chưa tới một nghìn token đầu ra và model tự cắt
cho vừa. Bốn mục một lượt thì mỗi slide rộng gấp năm, mà prefix vẫn ấm nên input
gần như không tốn thêm (đo thật: 71k token đọc từ cache cho 5 mẻ).

Mỗi mẻ **lưu ngay vào DB**, nên mất kết nối giữa chừng thì phần đã dựng vẫn còn.
Slide người dùng đã sửa tay (`edited`) thì **chép lại, không gọi model** — dựng
đè lên là xoá công sức của họ mà không báo.

`check_outline()` là bản sao của `check_slides()` cho bước 1, và bắt cùng loại
lỗi: số bịa, nhãn chủ đề rỗng, mục không có bằng chứng, ảnh không có thật. Bắt ở
đây rẻ hơn hẳn — sửa một dòng, thay vì dựng lại cả slide.

`mark_stale()` phải quét **cả dàn ý**, không chỉ deck: bỏ sót thì lần dựng sau đẻ
lại đúng cái slide đã sai.

Mục lục thì **tính từ dàn ý, không hỏi model** (`agenda_from_sections()`) — cùng
lối với `section_icons()`. Hỏi nó thì nó rơi về nhãn rỗng (`Thực nghiệm`,
`Kết luận`) và mục lục lệch với các vách ngăn phía sau.

### Bằng chứng và thẻ tranh nhau chiều cao — đây là chỗ vỡ bố cục hay gặp nhất

Ba lỗi đã vấp thật khi soát bằng trình duyệt, và **bộ đo Python không thấy cái
nào**:

- **`.vis` không có ràng buộc chiều cao** ở luồng thường (chỉ có CSS cho bố cục
  tự do). `flex:1` của `figure` và `max-height:100%` của svg đều đo theo một cha
  cao tự do, tức không đo gì cả: ảnh hiện ở cỡ gốc, sơ đồ mermaid phình tới
  4000px. **10/20 slide tràn khung.** Một dòng CSS sửa cả mười.
- **Nhưng `min-height:0` là lỗi ngược lại**: thẻ ăn hết chiều cao, sơ đồ co còn
  ~140px — không tràn nên bộ đo im lặng, mà nhìn thì nó bé bằng con tem. Bằng
  chứng giữ tối thiểu **38%**; nhồi thêm chữ thì slide tràn và `check_slides`
  kêu, đúng thứ cần xảy ra.
- **Ba thẻ cộng một sơ đồ là quá tải.** Slide có `figure`/`diagram` thì tối đa
  **2 thẻ** và bỏ `callout`; slide cần 3–4 thẻ thì **đừng gắn sơ đồ** — thẻ có
  nền màu, chip icon, tiêu đề đậm tự nó đã là cấu trúc để mắt bám vào. Vì thế
  `check_slides` coi **thẻ cũng là "thứ để nhìn"**; đòi thêm hình ở slide bốn thẻ
  là đẩy model gắn sơ đồ trang trí.

Thêm một luật hình học: **slide có thẻ thì sơ đồ phải `flowchart LR`.** Chỗ còn
lại cho nó là dải ngang thấp; `flowchart TD` xếp node thành cột dọc nên bị bóp
còn một vệt hẹp. `check_slides` cảnh báo đúng trường hợp này.

Và svg của mermaid phải để `width:100%;height:100%` chứ **không** `auto`: mermaid
sinh sơ đồ chừng vài trăm pixel nên `auto` vẽ ở cỡ tự nhiên, thành vệt bé tí giữa
khung dù còn thừa chỗ. Svg có `viewBox` nên 100% hai chiều là tự co giãn vừa
khung mà vẫn giữ tỉ lệ.

### Pass 4: làm slide

`pipeline.make_slides()` chạy liền cả hai bước (đường tắt, không có chỗ soát).
Nó dựng bộ slide từ bài **đã dịch xong** (nút bị khoá tới
lúc đó — dựng từ bản dịch dở thì model tự viết lấy phần thiếu, đúng thứ cần
tránh). Đi sau `cached_prefix(doc)` giống pass giải thích nên gần như chỉ trả
tiền đầu ra; `minutes` là thứ thay đổi theo request nên nằm ở message `user`.
Kết quả lưu ở cột `slides`, xem/sửa ở màn `#slides`, xuất qua
`?fmt=slides` (tải file) và `?fmt=slides-pdf` (mở hộp in).

**Luật slide là khẳng-định-và-bằng-chứng, và đó là quyết định có bằng chứng.**
Garner & Alley 2013 giữ nguyên kịch bản nói 1.000 từ, chỉ đổi thiết kế slide:
tiêu đề là **một câu khẳng định** + thân là **hình** (21,2 chữ/slide) so với nhãn
chủ đề + gạch đầu dòng (41,5 chữ/slide) cho d = 0,81 về hiểu bài và **d = 0,89
khi kiểm tra lại sau 10 ngày**. Hai hệ quả cho code:

- Lợi ích nằm ở hiểu cơ chế và nhớ lâu, **không** ở nhớ số liệu rời.
- Người xem chấm slide ít chữ là "ít chữ quá" *trong khi học được nhiều hơn* —
  nên bản ít chữ phải là **mặc định**, thêm chữ là việc người dùng tự làm. Đừng
  nới ngân sách chỉ vì thấy slide trông trống.

Quy tắc 6×6 / 7×7 không có nguồn nghiên cứu nào, đừng đưa vào prompt: nó ép *cắt
cho ngắn* chứ không phải *sửa cho rõ*.

**Nhưng con số 21 chữ đó là của tiếng ANH và không có yêu cầu chú giải hình.**
Áp thẳng vào đây là sai hai lần, và đã sai thật một lần rồi:

1. Cùng nội dung, **tiếng Việt dài hơn tiếng Anh 10–25%**. Trần ≤70 ký tự cho
   `headline` ép model lược hư từ ("của", "trong", "so với", "khi") — ra thứ
   tiếng Việt kiểu tít báo, sai ngữ pháp: *"CIRAG thay chốt sớm bằng tích hợp
   bằng chứng"*, *"Ngữ cảnh theo tầng hướng tới cân bằng đủ thông tin và nhiễu"*.
   Vì thế `SLIDES_TASK` có hẳn một mục **văn phong học thuật tiếng Việt** với
   bảng sửa mẫu, và mốc đã quy đổi: `headline` 8–18 chữ / ≤85 ký tự, slide ≤35
   chữ. Thuật ngữ vẫn giữ tiếng Anh — cái phải chuẩn là **khung câu** quanh nó.
2. **Hình cắt từ bài là hình tiếng Anh.** Trục, nhãn, chú giải nằm trong PNG,
   không sửa được. Slide chỉ có một câu tiếng Việt + một biểu đồ tiếng Anh là
   slide **trống** với người nghe Việt Nam, dù về hình thức nó đúng khẳng-định-
   và-bằng-chứng. Nên có trường **`figure_note`**: 1–3 câu dịch nhãn trục và chỉ
   rõ nhìn vào đâu. Bắt buộc khi có `figure`; `check_slides` cảnh báo nếu thiếu.

`figure_note` **đếm riêng, không cộng vào `MAX_WORDS`** — nó là chú thích của
hình, không phải chữ tranh chỗ với thông điệp (nghiên cứu gốc cũng không tính
chú thích hình). Cộng gộp thì hai giới hạn tự mâu thuẫn và mọi slide có hình đều
kêu oan.

Nguyên tắc chung cho mấy hằng ngân sách: **prompt đặt mục tiêu, hằng số trong
`pipeline.py` đặt chỗ thật sự vỡ bố cục.** Để hai con số bằng nhau thì slide nào
sát mức cũng kêu; chốt chặn kêu oan vài lần là người dùng thôi đọc nó, lúc đó
cảnh báo thật cũng trôi theo. Hiện tại: mục tiêu ≤35 chữ / ≤85 ký tự / chú giải
≤35 chữ, còn cảnh báo ở 55 / 105 / 42.

Một ngoại lệ cố ý: luật "đừng lặp lời nói thành chữ" đảo chiều với người nghe
không phải bản ngữ của ngôn ngữ thuật ngữ. Nên **thuật ngữ `keep_en` vẫn giữ trên
slide**, chỉ bỏ các câu tường thuật.

### Mục lục và vách ngăn: hình mới là thứ dẫn đường, không phải chữ

Penn State ([bộ hướng dẫn A-E gốc](https://cpb-us-e1.wpmucdn.com/sites.psu.edu/dist/7/13153/files/2008/10/Assertion-Evidence-Slides-Instruction_Set.pdf))
coi "mapping slide" là một bước chính thức, và quy định của nó khác hẳn cái mục
lục gạch đầu dòng thông thường:

> Với mỗi phần, kèm **một hình đại diện cho phần đó**. Nên dùng chính **hình đầu
> tiên của mỗi phần** — người nghe thấy hình lặp lại sẽ nhận ra đang sang phần mới.

`pipeline.section_icons()` làm đúng việc đó và **tính từ deck, không hỏi model**:
mỗi slide `section` nhận hình của slide có `figure` đầu tiên đứng sau nó. Hình ấy
hiện ở cả mục lục lẫn vách ngăn. Model chỉ cần xếp sao cho ngay sau `section` là
một slide có hình.

Hai con số từ cùng nguồn: **buổi 10–15 phút thì đúng 3 phần** (20 phút trở lên
mới 4–5), và **call-out tối đa 1–2 mỗi slide** — "ba cái trở lên làm slide rối và
kém hiệu quả".

Chỗ này có một mâu thuẫn cố ý, đừng "sửa" nhầm: Alley viết **"gạch đầu dòng không
có chỗ trong kiểu trình bày này"**, nhưng người dùng của công cụ này muốn slide
có thêm chữ. Cách hoà giải là dùng đúng cơ chế của Alley: trên slide có hình,
`bullets` phải là **call-out chú vào từng phần của hình** (tối đa 2), không phải
danh sách ý rời. `check_slides` cảnh báo khi quá 2.

### Bố cục slide: bảy kiểu, suy ra từ nội dung

Deck chuyên nghiệp dùng **3–5 kiểu bố cục**. Bản đầu tiên của tính năng này chỉ
có **một** — tiêu đề trên, mọi thứ dồn vào một cột giữa — và hai mươi slide giống
hệt nhau chính là thứ làm nó trông rẻ tiền, chứ không phải màu sắc.

`pipeline.slide_layout()` chọn bố cục **từ nội dung**, không hỏi model: model
không biết trước slide rốt cuộc có bao nhiêu chữ nên khai bố cục sai.

| Bố cục | Khi nào | Vì sao |
|---|---|---|
| `title` | `kind == "title"` | vạch nhấn mảnh — chỗ **duy nhất** màu nhấn xuất hiện ngoài `statement` |
| `agenda` | `kind == "agenda"` | mỗi phần một dòng: số · hình đầu của phần · tên phần |
| `section` | `kind == "section"` | tên phần cỡ lớn + đúng hình đã thấy ở mục lục |
| `statement` | takeaway/thanks không có gì để nhìn | một câu lớn giữa slide, nhiều khoảng trắng |
| `full` | có `figure` | hình trong bài là biểu đồ/bảng **nằm ngang**; nhét vào nửa slide thì chữ trong hình không đọc được |
| `split` | có `diagram`/`equation` + gạch đầu dòng | sơ đồ do ta dựng, hẹp hơn, bám sát chữ bên cạnh |
| `list` | không có gì để nhìn | chỉ chữ |

Chính cặp `full`/`split` tạo ra sự đa dạng: bài nào cũng có cả hình cắt lẫn sơ đồ.

**Ba thứ bị chỉ đích danh là "dấu hiệu slide do AI làm": nền màu kem, hoa văn
serif nghiêng, thanh màu kẻ dọc cạnh ô chữ.** Gạch chân màu dưới tiêu đề cũng
vậy — dùng khoảng trắng thay thế. `_SLIDES_CSS` cố ý không có thứ nào; nền trắng
thật, một màu nhấn duy nhất dùng đúng hai chỗ. Đừng thêm lại.

**`check_slides()` là chốt chặn của pass này** — bản sao của `content_kept()` cho
slide. Khác một điểm: **cảnh báo chứ không chặn**, vì người dùng có màn hình để
tự sửa, và cắt mất một slide còn tệ hơn hiện nó kèm cờ đỏ. Phép kiểm đáng giá
nhất là ràng buộc số liệu: mọi con số trên slide phải có mặt nguyên văn trong các
khối khai ở `source_block_ids` — một con số bịa trên slide là gán kết quả giả cho
tác giả thật, và bằng mắt thì không ai bắt được. Vì thế `PATCH …/slides` **không
cho sửa `source_block_ids`**; sửa được thì chốt chặn thành vô nghĩa.

`mark_stale()` là cặp song sinh của `_forget()`: khối nguồn bị sửa thì slide dựa
trên nó bị gắn cờ `stale` chứ **không xoá** — công sức sửa tay của người dùng nằm
trong đó.

Năm chỗ dễ vấp:
- Cột `slides` là cột thêm sau. `CREATE TABLE IF NOT EXISTS` không đụng vào bảng
  đã có, nên `db._migrate()` phải `ALTER TABLE` — bỏ là mọi bài cũ vỡ lúc load.
- Nhãn `A["nhãn"]` là dạng **đúng** mà `DIAGRAM_RULES` yêu cầu. Bộ soát chỉ được
  bắt nháy **lồng bên trong** nhãn (`_bad_mermaid_labels` đếm số nháy trong một
  nhãn, phải là 0 hoặc 2), chứ tìm dấu nháy là báo sai sạch.
- Ràng buộc số liệu phải **bỏ qua URL và định danh** (`_URLISH`, và `_NUM` chặn
  chữ ở cả hai đầu): `github.com/52566rz`, `2WikiMQA`, `Qwen2.5-7B` không phải số
  liệu của bài. Slide `title`/`thanks` thì **không kiểm số** — số trên đó là năm
  hội nghị và độ dài buổi nói, vốn không có trong bài.
- Cỡ chữ trên slide quy theo bề ngang khung (`cqw` bên `style.css`, px trên khung
  1280×720 bên `_SLIDES_CSS`) — **24px là sàn tuyệt đối**, `figure_note` cũng
  phải ở mức đó chứ không nhỏ hơn. `line-height` không dưới 1.28 vì dấu tiếng
  Việt chồng tầng (ế, ộ, ữ) bị cắt ngọn; cũng vì thế không viết hoa toàn bộ,
  không siết `letter-spacing`.
- Bản xem trước trong app và file xuất ra là **hai đoạn code khác nhau dựng cùng
  một markup** (`renderSlide()` bên `app.js`, `_export_slides_html` bên
  `main.py`). Sửa một bên phải sửa bên kia, không thì xem trước nói dối. Từ khi
  có `.pptx` thì thành **ba** chỗ — `pptx_out._render()` là chỗ thứ ba.

### Xuất `.pptx`

`server/pptx_out.py`, qua `?fmt=pptx`. Khổ 13,333×7,5 inch = đúng khung 1280×720
của bản HTML; **1px = 0,75pt**, mọi con số quy từ `_SLIDES_CSS` bằng hằng đó.
Dùng bố cục trống (`slide_layouts[6]`) và tự đặt từng khung chữ, vì python-pptx
**không có autofit thật** (`fit_text()` cần đo font ngoài thư viện và hay tràn).

Sơ đồ Mermaid **vẽ lại bằng shape gốc PowerPoint**, không nhúng ảnh: `DIAGRAM_RULES`
đã giới hạn ở `flowchart TD|LR`, ≤9 node, nhãn ≤8 chữ nên `parse_mermaid()` bóc
được, rồi `_draw_diagram()` xếp theo tầng và nối bằng connector. Người dùng kéo
và sửa chữ được — đó mới là lý do họ cần `.pptx`.

Bẫy đã vấp: `_MMD_ELABEL` phải bóc nhãn cạnh (`-->|"ghi chú"|`) ra **trước** khi
quét khai node, không thì `|"thiếu"|` bị đọc thành node tên `u` và `ch`. Và
`Pt()` trả về Length tính bằng EMU sẵn — nhân thêm 12700 là văng ValueError.

Công thức dựng bằng `baseline` ở mức run (`_rich_runs`), không cần OMML — vì
`^{…}` / `_{…}` vốn đã là dạng lưu.

### Bôi vàng và ghi chú

Người đọc bôi một đoạn trong màn `#reader`, vệt bôi lưu ở cột `highlights`, rê
chuột hoặc bấm vào là hiện ghi chú sửa được — như comment. `POST
…/highlights/{id}/explain` nhờ model giải thích đúng đoạn đó (đi sau
`cached_prefix` nên rẻ, ~$0,003 một lần).

**Neo theo khoảng ký tự trong VĂN BẢN HIỂN THỊ của một ô, không phải trong chuỗi
HTML.** `sci()` chèn `<sup>`, `<sub>` và thẻ `<a>` cho tham chiếu hình, nên mọi
vị trí tính trên HTML đều lệch so với chỗ người đọc thật sự bôi. Vì cùng lý do
đó, `paintHighlights()` bọc bằng **DOM Range** (cắt text node ở hai mép) chứ
không cắt chuỗi HTML — cắt chuỗi sẽ phá chính mấy thẻ kia.

Ba chỗ dễ vấp:
- **Vẽ từ cuối về đầu** trong `paintHighlights`: cắt text node làm lệch mọi vị
  trí phía sau, nên phải xử lý vệt có `start` lớn trước.
- Khối bị sửa chữ thì `_forget()` **xoá luôn** vệt bôi của khối đó. Khoảng ký tự
  cũ trỏ vào chỗ khác; giữ lại còn tệ hơn mất, vì người đọc thấy vàng ở một đoạn
  chẳng liên quan gì tới ghi chú của chính mình.
- Một vệt bôi trải nhiều dòng thì `getBoundingClientRect()` trả khung bao gồm cả
  khoảng trống cuối dòng. Nhắm chuột vào đó có thể trúng ô cha chứ không trúng
  `<mark>` — dùng `getClientRects()[0]` khi cần toạ độ thật (đã vấp lúc viết test).

Năm màu (`y g b p v`) đặt trên chính thẻ `<mark data-c>` để đổi màu không phải vẽ
lại DOM. Màu vừa dùng nhớ ở `localStorage` vì người ta hay bôi liền mấy đoạn cùng
loại.

### Model suy luận

`pipeline.NO_REASONING` cho các lượt dịch, `LOW_REASONING` cho brief, explain và
slide.
Để mặc định thì DeepSeek V4 / GPT-5.x tiêu sạch `max_tokens` vào phần nghĩ thầm
rồi trả về rỗng hoặc JSON dở dang. `cjk_leak()` bắt trường hợp model gốc Trung
Quốc trả về chữ Hán (so với bản gốc, không cấm tuyệt đối) và gọi lại một lần.

### Các quy ước nhỏ dễ vấp

- `store.py` chỉ là mặt tiền mỏng của `db.py`, giữ tên hàm cũ thời còn lưu JSON.
  `store.migrate_json()` chạy lúc khởi động, đổi tên file cũ thành `.migrated`.
- `doc_id` và `block_id` phải `isalnum()` — `store` dùng chúng để dựng đường dẫn
  file, kiểm tra này là hàng rào chống path traversal, đừng bỏ.
- `doc["chunks"]` và `doc["chunk_ids"]` **không** lưu trong DB; server tính lại
  bằng `_with_chunks()` ở mỗi lần trả `doc`. Frontend dựa vào `chunk_ids`
  để biết mẻ nào đã xong nên đừng dịch lại. Endpoint nào trả `doc` cũng phải đi
  qua `_with_chunks`, thiếu là frontend dịch lại từ đầu.
- Export có năm dạng qua `?fmt=md|html|pdf|slides|slides-pdf`. Ảnh luôn nhúng base64 — bản cũ ghi
  đường dẫn `/api/doc/…` nên mở file ngoài app là hỏng hết hình. `fmt=pdf` chỉ
  là bản HTML tự mở hộp in: đó là đường duy nhất giữ được cả sơ đồ Mermaid (cần
  JS) lẫn lưới hai cột (cần CSS grid), thư viện PDF thuần Python không làm được.
  Mermaid nặng 3.5MB nên chỉ nhúng khi bài thật sự có sơ đồ.
- Sở thích hiển thị (cỡ chữ, bề rộng, sáng/tối, chỗ đang đọc dở) nằm ở
  `localStorage` với tiền tố `docdoc:` (tiền tố giữ tên cũ có chủ đích: đổi là
  mọi sở thích đã lưu biến mất im lặng) — thuộc về máy đang ngồi, không phải
  thuộc tính của bài, nên cố ý không lưu vào DB.
- Đổi chủ đề sáng/tối phải vẽ lại sơ đồ (`applyTheme` xoá `data-done` rồi
  `hydrateDiagrams`), vì mermaid nướng màu vào SVG lúc render.
- Sửa block (`PATCH …/blocks`) phân biệt `drop` (bỏ hẳn khối) với `skip`/`keep`
  (giữ khối, bật/tắt cờ `translate`), và `drop_figure` (bỏ ảnh, giữ caption).
- Mermaid nạp từ `web/vendor/mermaid.min.js`, không lấy từ CDN.
