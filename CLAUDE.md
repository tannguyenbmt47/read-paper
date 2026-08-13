# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Chạy và kiểm tra

```bash
./run.sh                 # tạo .venv + cài deps + chạy uvicorn ở 127.0.0.1:8010
PORT=9000 ./run.sh       # đổi cổng
.venv/bin/uvicorn server.main:app --reload --port 8010   # dev, tự nạp lại
```

```bash
.venv/bin/python -m pytest          # 148 test · ~3 phút (phần lớn là import docling)
.venv/bin/python -m pytest tests/test_unit.py -q    # phần logic thuần, ~3 giây
.venv/bin/python -m pytest tests/test_survey.py -q  # kho survey, ~4 giây
node --check web/app.js web/survey.js   # chưa có test cho frontend
```

`tests/test_unit.py` — logic thuần, không cần server: chốt soát số liệu trên
slide, bộ đo tràn khung, bộ bóc Mermaid, chỉ số trên/dưới.

**`tests/conftest.py` là hàng rào giữ bộ test khỏi `data/` thật, và nó phải nằm
ở conftest chứ không phải ở fixture.** `server/db.py` đọc `PAPER_DATA_DIR` **ngay
lúc import**, mà pytest **import mọi file test lúc thu thập** — và
`tests/test_unit.py` import `server.pipeline` ở cấp module. Nên tới lúc fixture
của `test_api.py` đặt biến môi trường thì `db.DATA_DIR` đã chốt vào `data/` thật
từ lâu. Đã hỏng đúng vậy: chạy `pytest` ghi 2 bài rác và 66 kho survey rác lẫn
vào dữ liệu người dùng. Hai hệ quả phải giữ:

- `conftest.py` đặt biến ở **cấp module**, không đặt trong fixture;
- fixture trong từng file test dùng `os.environ.setdefault(...)`, **không gán
  đè** — gán đè thì biến môi trường trỏ một nơi còn dữ liệu nằm một nơi khác, và
  chính phép kiểm bảo vệ cũng nói dối theo.

`test_bo_test_khong_ghi_vao_data_that` canh cho hàng rào này không bị gỡ mất.

`tests/test_survey.py` — kho survey, chạy với `EMBED_BACKEND=off` nên không kéo
về 2,3GB trọng số và không đòi GPU. Đường BM25 phải chạy đúng một mình, vì đó
cũng là đường mà máy không có GPU sẽ chạy. Bộ này canh mấy chỗ đã vỡ thật: chỉ
mục FTS5 external content lệch với bảng `chunk` (hỏng câm, chỉ lộ ra ở một lệnh
xoá rất lâu sau), giao dịch ghi bị bỏ quên làm luồng khác nhận `database is
locked`, và bộ lọc thư mục tham khảo.

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

**Nhưng bộ bóc phải nhận theo TẬP MÃ CỦA MẺ, không theo cú pháp nhãn.** Model
gõ lệch một ký tự là cả giao thức sập, và sập **im lặng**: `<<<b4_g>>` (thiếu một
dấu `>`) hay `### b9_g` (dạng tiêu đề Markdown) đều không khớp mẫu cũ, nên phần
nội dung sau nó bị dồn hết vào ô của nhãn *trước đó*. Đo trên bài thật: một ô
`plain` phình lên **20.052 ký tự** chứa diễn giải của mười mấy khối, kèm 48 nhãn
`###` hiện thành rác ngay trong cột đọc; 16 ô khác dính bản dịch lẫn diễn giải.

Vì `stream_chunk` **biết trước** mẻ này gồm mã nào, `_label_re(ids)` dựng mẫu từ
chính tập mã đó — nhãn chỉ cần *chứa* một mã đã biết, còn bao quanh nó là `<<<>>>`,
`###`, `**…**` hay `[…]` đều nhận. `want_ids` phải truyền vào **cả bốn** chỗ gọi
trong `stream_chunk` và cả `relayout`; thiếu một chỗ là chỗ đó vẫn hỏng như cũ.

Hai ràng buộc ngược lại, cùng quan trọng:

- **Nhãn phải đứng một mình trên dòng** (`^…$` với `re.M`). Nới ra là mọi câu
  nhắc tới mã khối — *"xem thêm phần [b12] ở phụ lục"* — cắt bài làm đôi.
- **Nhãn lọt vào thân chữ thì khối đó vào `dirty`**, không ghi xuống `tm`. Cùng
  lý do với `script_leak()`: rác trong `doc` thì người đọc sửa được, rác trong
  `tm` thì quay lại mãi mãi.

Mẫu dựng từ mã, nên mã dài phải xếp trước (`sorted(key=len, reverse=True)`) —
không thì `b1` khớp trước và `b12` mất phần đuôi.

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

### Phễu lọc sau khi bóc — chỗ tiền rò ra mà không ai thấy

Hai hàm chạy trên danh sách `Block` đã dựng xong nên dùng chung cho cả đường
docling lẫn đường heuristic. Cả hai nhắm vào **cùng một cái giá**: mỗi khối là
MỘT lượt dịch cộng MỘT lượt giải thích, nên một mảnh vụn không gom lại là hai
lượt gọi model trả cho thứ không đọc được.

**`stitch_hyphenated()` — đoạn bị hình chen vào giữa từ.** Ở bài hai cột, hình
và bảng được xếp lên đầu cột nên chúng chen vào **giữa câu**. Đo trên CIRAG: 6
đoạn kết thúc bằng `differ-`, `compo-`, `sen-`, `other-`, `oth-`, `re-`; phần
đuôi nằm sau một hoặc hai caption. Mỗi mảnh được dịch riêng, và model tự ghi vào
cột giải thích rằng *"câu gốc bị cắt ngay sau khi nói Bảng 3, nên chưa cho biết
cụ thể"* — vừa tốn hai lượt vừa cho ra bản dịch không thể đúng được.

Mốc nhận biết phải là **cả hai** dấu hiệu: gạch nối cuối khối **và** chữ thường
mở đầu khối nối tiếp. Chỉ gạch nối thì `w/o Triple + Sentence-` cũng khớp; chỉ
chữ thường thì mọi đoạn bắt đầu bằng `the` dính vào đoạn trước. Cho nhảy qua tối
đa 4 khối `caption`/`equation`/`figure`/`table`, nhưng **gặp heading thì dừng** —
đoạn cuối mục này không nối vào đầu mục sau. Nối bỏ luôn dấu gạch và không chèn
khoảng trắng: `differ-` + `ent` phải ra `different`. Kết quả: 5/6 nối được, cái
còn lại phần đuôi mở đầu bằng chữ hoa nên cố ý không nối.

**`mark_noise()` — tắt cờ dịch, KHÔNG xoá.** Bắt: mảnh dưới 12 ký tự, khối chỉ
gồm số và dấu (`57.3%`, `(4) ...`), dòng email tác giả, ORCID, và chú thích
chân/cơ quan (`^{1}Our code can be found via github.com/…`). Đo trên ba bài
thật: 8–10 khối mỗi bài.

Tắt cờ chứ không xoá vì ranh giới "rác" không bao giờ chắc chắn — một dòng ngắn
toàn số có thể là kết quả chính của bài. Người đọc vẫn thấy khối đúng chỗ và bật
lại được, cùng lối với `hidden`.

Một bẫy trong chính bộ lọc: **phải bỏ dấu chú thích chân ở đầu dòng trước khi
chấm**. `^{1}Our code…` sau khi gỡ đánh dấu thành `1Our code…`, chữ số dính liền
chữ cái nên `\bOur code\b` không còn khớp và cả dòng lọt lưới.

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

**Span không rơi vào khung nào thì phải NHẶT LẠI, không được vứt.** `assign_spans`
gán mỗi span vào khung nhỏ nhất chứa tâm nó; span ngoài mọi khung trước đây rơi
ra ngoài và không ai nhặt. Docling bỏ sót một vùng chữ — hay gặp ở đoạn vắt qua
ranh giới cột — thì **cả đoạn biến mất khỏi bài mà không có lỗi nào**. Đo trên
bài CIRAG: 20,4% số span rơi ngoài; người đọc thấy một đoạn đứt giữa chừng ở chữ
"Current" rồi nhảy sang ý khác.

**Mép khung cắt ngang dòng đầu là ca riêng, và nó mất NGUYÊN DÒNG.** Khung của
mô hình bám rất sát chữ nên mép trên thường nằm lọt vào giữa dòng đầu tiên. Đo
trên bài GCR: khung abstract bắt đầu ở `y=249,4`, dòng đầu nằm ở `244,5–253,5`
tức tâm ở `249,0` — **cao hơn mép đúng 0,4pt**, thế là cả dòng *"Long-video
question answering requires identifying sparse yet"* rơi ra ngoài, trong khi
docling đã bóc nó hoàn toàn đúng.

Vì thế `assign_spans` có **lượt vét thứ hai**: span nào không khung nào chứa tâm
thì gán theo **diện tích chồng lấn lớn nhất**, đòi chồng ≥⅓ span. Lượt này chạy
sau nên không đổi một phép gán đúng nào ở lượt một, và span thật sự không chạm
khung nào vẫn rơi xuống `recover_uncovered()`. Đo lại: bài GCR **68,1% → 79,2%**
số từ giữ được (674 từ), hai bài khác không đổi.

Span nhặt ở lượt hai phải **sắp lại theo thứ tự PyMuPDF đọc ra** (`_seq`), không
thì dòng đầu của đoạn nằm ở cuối khối — các tầng dưới có sắp lại theo hình học
nên bài vẫn ra đúng, nhưng để hàm trả về thứ tự sai là đặt sẵn bẫy cho lần sau.

`recover_uncovered()` gom phần rơi ngoài thành khối `para` rồi thả vào `items`
trước khi sắp thứ tự đọc, nên nó đi chung đường với mọi khối khác. Hai bộ lọc
giữ cho nó không nhặt nhầm: bỏ span nằm trong vùng hình/bảng (đó là chữ trong
hình, vốn phải biến mất khỏi mạch đọc) và bỏ span khác cỡ thân bài (số trang,
nhãn trục). Đo lại theo tỉ lệ từ giữ được: **82,5% → 89,7%** trên CIRAG.

Bẫy trong chính bản vá: **phải tách cột TRƯỚC khi dựng dòng.** `_rows()` gom theo
baseline trên cả trang, nên ở bài hai cột một dòng trái và một dòng phải cùng độ
cao thành MỘT dòng, ghép theo trục x là chữ hai cột cài răng lược:
*"…static evidence repre- summarized as follows: sentation, failing…"*. Đã ra
đúng vậy ở bản đầu. Và đừng dùng `_page_columns` để đoán số cột ở đây — nó tính
trên khung KHỐI, đưa span rời vào thì luôn trả về một cột.

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

**Bắt buộc có một slide đi hết cơ chế bằng ví dụ chạy tay.** Đây là chỗ mọi bộ
slide về bài phương pháp thường hỏng, và hỏng theo cách người trình bày không
nhận ra: kể được bài toán, kể được kết quả, nhưng phần giữa — cách nó thật sự
chạy — chỉ còn cái tên và một sơ đồ ba hộp. Người nghe gật đầu suốt buổi rồi ra
về không kể lại được cho ai. `OUTLINE_TASK` đòi ít nhất một slide lấy **một đầu
vào cụ thể có thật trong bài**, đi từng bước, và ở mỗi bước nói **vì sao** bước
ấy cần thiết. `pipeline.check_depth()` kiểm ở mức cả bộ (xem mục "Chuẩn độ sâu").

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

### Sửa tay bản dịch, và chốt chặn rò hệ chữ

Nút ✎ trên mỗi khối ở màn đọc mở một `<textarea>` chứa **văn bản thô đang lưu**,
không phải `contenteditable` trên nội dung đã hiển thị — cột đó đã đi qua `sci()`
nên có `<sup>`, `<sub>` và thẻ `<a>`; lấy HTML đó làm nội dung lưu thì mỗi lần
sửa lại nhân thêm một lớp thẻ và `^{…}` gốc mất luôn. Cùng lý do với
`paintHighlights` dùng DOM Range chứ không cắt chuỗi HTML.

Bản sửa ghi cả vào `tm`, nên nó theo đoạn văn chứ không theo bài: đoạn y hệt ở
bài khác, hoặc chính bài này sau khi bóc lại, lấy đúng bản người dùng đã sửa.

**`script_leak()` là chốt chặn của pass dịch, và pass dịch trước đây không có
cái nào.** `cjk_leak` chỉ biết CJK/Hangul, mà đã gặp bản dịch chứa `띠ᥕᥕᥲᥕᥱ` thay
cho chữ "bảo toàn" — `ᥕᥲᥱ` là chữ Limbu, ngoài mọi dải nó biết. Liệt kê hệ chữ
**cấm** là trò đuổi bắt không hồi kết; `_OK_SCRIPT` liệt kê hệ chữ **được phép**
(Latinh, dấu tiếng Việt, Hy Lạp, toán, chỉ số trên/dưới) nên mọi thứ lạ đều bị
bắt, kể cả hệ chữ chưa ai gặp.

Chỗ phải chặn cho bằng được là **`tm`**, không phải `doc`: bản dịch rác nằm
trong `doc` thì người đọc thấy và sửa được, nhưng nằm trong `tm` thì nó quay lại
mãi mãi — mọi bài sau có đoạn y hệt đều nhận lại đúng cái rác đó, miễn phí và im
lặng. Quét dữ liệu thật đã tìm ra 4 mục `tm` nhiễm (Cyrillic, Devanagari,
Armenian) và 2 khối trong bài.

### Bóc lại từ PDF mà giữ nguyên bản dịch

`POST /api/doc/{id}/reparse` bóc lại từ file gốc (bỏ qua `parse_cache`) rồi
`pipeline.reparse_merge()` ghép **theo NỘI DUNG, không theo vị trí**: khối mới
trùng văn bản khối cũ thì lấy lại đúng mã cũ, nên bản dịch, ghi chú, vệt bôi và
`source_block_ids` của slide vẫn trỏ đúng chỗ. Ghép theo vị trí thì bản bóc mới
chèn thêm một khối là mọi chỉ số phía sau lệch đi một, bản dịch dán vào nhầm
đoạn — **tệ hơn mất bản dịch**, vì nhìn vẫn có vẻ đúng.

Khối trùng nội dung nhau phải khớp **theo thứ tự xuất hiện** (dict text → *danh
sách* mã). Khớp một-một thì khối thứ hai luôn phải mint mã mới, và mint lại mỗi
lần bóc — đo trên bài thật: 12 khối churn mỗi lượt, và bản dịch của chúng rơi
theo. Sau khi sửa, bóc lại hai lần liên tiếp cho `kept: 282, new: 0, dropped: 0`.

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

## Cơ chế thứ hai: kho survey (`server/survey/`)

Đây là **một công cụ khác**, không phải một tính năng của luồng đọc-hiểu. Luồng
cũ xoay quanh `cached_prefix(doc)` — toàn văn **một** bài nhét vào system prompt.
Kiến trúc đó không dùng lại được cho câu hỏi cần ba mươi bài.

Ranh giới phải giữ: `pipeline.py`, `prompts.py`, `parser.py`, `layout.py` **không
đổi một dòng nào**; `main.py` chỉ thêm hai dòng `include_router`. Kho dùng chung
`data/papers.db` nhưng **không chung bảng nào** với `documents`/`parse_cache`/`tm`.

Ràng buộc chi phối toàn bộ thiết kế: **dịch 50 bài là không khả thi về tiền.**
Nên kho **không dịch bài** — nó bóc, đánh chỉ mục, và rút mỗi bài thành một
*phiếu* (`card`) ~600 token.

```
PDF → đoạn → ngữ cảnh hoá → vector → cây RAPTOR → phiếu → đồ thị thực thể
                                   ↓
hỏi → lập kế hoạch → tìm → đọc → chấm thiếu → tìm tiếp → tổng hợp → kiểm chứng
```

| Bước | Chạy bằng | Giá/bài |
|---|---|---|
| bóc PDF, cắt đoạn, chỉ mục FTS5 | — | **$0** |
| vector hoá | GPU tại máy | **$0** |
| ngữ cảnh hoá · cây · phiếu · đồ thị | model rẻ | ~$0,034 |

### Năm kết quả nghiên cứu, và luật nào sinh ra từ kết quả nào

Đây là phần **không được sửa theo cảm tính** — mỗi hằng số dưới đây có một phép
đo đứng sau.

1. **Vòng lặp tìm-đọc-tìm-tiếp thắng cả bằng chứng hoàn hảo** (80,9% so với
   69,1% khi đưa sẵn gold context; không ngữ cảnh 37,2%). Từ đó ra `MAX_STEPS=5`,
   `PER_STEP=10`, `CARRY=2` trong `agent.py`. Bốn kiểu hỏng đo được, mỗi kiểu một
   chốt chặn:

   | Kiểu hỏng | Thiệt hại | Chốt |
   |---|---|---|
   | thiếu phủ | 77,9% → 49,2% | bảng kiểm tường minh; mục trống **ghi thẳng** vào câu trả lời |
   | hỏng tổng hợp | **87,3% số câu sai** | tổng hợp bằng model **mạnh**, bằng chứng **nhóm theo câu hỏi con** |
   | bám nhầm mồi | −53,9 điểm | mỗi vòng bắt buộc mang **từ khoá mới** |
   | dừng sớm quá tự tin | → 61,5% | cấm dừng ở vòng 1 khi còn mục trống |

2. **RRF (k=60)** trộn theo **thứ hạng**, nên cắm thêm/rút bớt bộ tìm không phải
   chỉnh hệ số. Đây cũng là lý do `EMBED_BACKEND=off` chạy được mà không rẽ nhánh
   code. Đo thật trên máy này: cosine của BGE-M3 nằm trong dải hẹp 0,31–0,57 —
   cộng điểm thẳng với BM25 (điểm âm) là vô nghĩa.
3. **Contextual Retrieval** — chèn một câu ngữ cảnh do model sinh vào trước mỗi
   đoạn rồi mới đánh chỉ mục: giảm ~67% tỉ lệ tìm trượt. Đoạn *"we reach 62.3
   EM"* tự nó không tìm ra được bằng bất cứ truy vấn tự nhiên nào.
4. **query2doc** — sinh một đoạn văn giả **tiếng Anh** rồi ghép vào truy vấn. Có
   lợi cho cả BM25 lẫn dense (khác HyDE vốn chỉ giúp dense). Đây cũng là cách câu
   hỏi tiếng Việt bắt được bài tiếng Anh.
5. **RAPTOR (ICLR 2024)** — truy vấn kiểu **"collapsed tree"**: đổ hết mọi node
   của mọi tầng vào **cùng một chỉ mục** rồi tìm một lần, ăn đứt cách đi lần từ
   gốc xuống lá. Vì thế **không có bảng riêng cho node cây**: tầng tóm lược ghi
   thẳng vào `chunk` với `level > 0`.
6. **GraphRAG** — bản đầy đủ đắt tới mức không dùng được (LazyGraphRAG đưa chi
   phí nạp về **0,1%**), nên ở đây **không dựng bản tóm lược cộng đồng**. Và đồ
   thị dùng để **mở rộng sau khi đã tìm**, không phải một đường tìm song song:
   hỏi tra cứu chi tiết thì vector thường **hơn** đồ thị (F1 64,8 vs 63,0), hỏi
   bắc cầu thì đồ thị hơn (70,3 vs 67,0).

### Bất biến quan trọng nhất: `corpus_digest`

`prompts.corpus_digest(papers)` ghép phiếu của cả kho thành một khối phải
**byte-identical giữa mọi câu hỏi** của cùng một dự án, và luôn đứng trước phần
thay đổi. Đây là `cached_prefix` của cơ chế survey, và bẫy y hệt: nhét câu hỏi,
thời gian hay số vòng vào đó là hỏng cache và chi phí nhân lên nhiều lần. Bài xếp
theo `id` chứ không theo `updated_at` — xếp theo thời gian thì mở lại một bài
cũng đảo thứ tự và cache trượt sạch. `session_id=survey_id` cho mọi lần gọi.

### Bẫy FTS5 external content — đã làm hỏng DB một lần

`chunk_fts` là bảng `content='chunk'`: FTS5 không giữ nội dung mà đọc ngược về
bảng `chunk` qua rowid. Đổi lấy dung lượng bằng một nghĩa vụ — **lệnh xoá phải
mang đúng từng byte nội dung cũ**. Sai một ký tự thì lệnh chạy trót lọt, không
báo gì, rồi rất lâu sau ném `database disk image is malformed` ở một chỗ chẳng
liên quan.

Đã vỡ đúng vậy vì `title` lúc đầu được truyền vào như tham số rời lấy từ bảng
`paper`. Cách sửa **không phải** là cẩn thận hơn, mà là làm cho việc lệch trở nên
bất khả: `title` được chép hẳn vào bảng `chunk`, nên nội dung chỉ mục là **hàm
thuần của một dòng `chunk`**, và mọi lần ghi đều đi qua `_fts_write()`. Phần
thưởng kèm theo: `reindex()` (`'rebuild'`) chạy được, vì mọi cột của `chunk_fts`
đều là cột có thật trong `chunk`. Đổi `paper.title` thì `update_paper` tự đồng bộ
xuống `chunk.title` rồi reindex.

Bẫy anh em: **DML ngoài `with conn():` là giao dịch ghi không bao giờ đóng.** Kết
nối là thread-local, nên luồng khác nhận `database is locked` ở một chỗ hoàn toàn
khác. Đã vấp với `integrity()`.

### Bộ lọc thư mục tham khảo — hai tầng, và vì sao cần cả hai

`parse_pdf` gắn nhãn `reference` khi tìm được tiêu đề mục tham khảo, nhưng có bài
nó không tìm ra — lúc đó cả thư mục rơi vào mục cuối cùng. Đo trên bài thật: một
mục thư mục đứng **hạng nhất** cho câu hỏi về nội dung. Thư mục dày đặc từ khoá
chủ đề mà không mang nội dung nào, nên nó là thứ gây nhiễu tệ nhất trong chỉ mục.

- `_is_ref_block()` chấm **từng khối**, chạy **trước khi gộp**. Gộp rồi mới chấm
  thì đoạn văn thật nằm cạnh thư mục bị vứt theo — đã mất luôn câu kết luận.
- `_looks_like_refs()` chấm **cụm đã gộp**, cho trường hợp nhiều mục ngắn.
- Và phải chạy **cả ở nhánh khối quá dài** (`len > MAX_CHARS`): khối đó đi thẳng
  vào kết quả, vòng qua `_flush`. Thư mục của bài hai cột hay bị nối thành một
  dòng dài duy nhất — đã lọt đúng ca này.

Dấu hiệu bắt buộc là **nơi công bố** (`In Proceedings`, `arXiv:`, `Springer`),
không phải mật độ năm hay `et al.`: đoạn văn *"RAG tốt với truy vấn đơn (Lewis et
al., 2020; Lin et al., 2024; Ram et al., 2023) nhưng…"* có mật độ năm **cao hơn**
cả thư mục thật.

### Chuẩn độ sâu (`server/depth.py`) — dùng chung cho tổng hợp, hỏi đáp và slide

Ba chốt chặn cũ (`content_kept`, `check_answer`, `check_slides`) đều chặn model
**bịa**. Không cái nào chặn được kiểu hỏng phổ biến nhất và khó thấy nhất: câu
đúng sự thật, có trích dẫn đàng hoàng, mà **không mang thông tin nào**.

> "CIRAG dùng cơ chế construction-integration để cải thiện chất lượng truy hồi."

Không sai, và cũng vô dụng. Feynman gọi đúng chỗ này khi phê một cuốn sách giáo
khoa viết *"năng lượng làm cho nó chuyển động"*: thay "năng lượng" bằng một từ vô
nghĩa thì câu vẫn "đúng" y như cũ. **Phép thử wakalixes** ấy giờ nằm trong prompt
của cả ba pass, và ba phép kiểm cơ học đi kèm:

| Phép kiểm | Bắt gì |
|---|---|
| `find_filler` | cụm rỗng: "đóng vai trò quan trọng", "góp phần nâng cao"… |
| `vague_claim` | "cải thiện / nâng cao / tối ưu" mà không nói **bằng cách nào** |
| `missing_mechanism` | câu đủ dài, không có quan hệ nhân quả, cũng không có số |
| `circular` | lời giải thích chỉ lặp lại chính khái niệm đang giải thích |

Hai chỗ dễ làm hỏng bộ này:

- **Ngưỡng phải nới tay.** `missing_mechanism` bỏ qua câu ngắn và câu có số —
  đòi nhân quả ở một khẳng định gọn là bắt viết dài dòng cho đủ hình thức. Đã
  hiệu chỉnh trên 3 câu nông + 4 câu sâu: bắt 3/3, kêu oan 0/4. Sửa ngưỡng thì
  chạy lại `tests/test_unit.py::test_do_sau_*`.
- **`DEPTH_RULES` là một nguồn duy nhất** nhúng vào `SYNTH_SYSTEM`,
  `ANSWER_SYSTEM`, `OUTLINE_TASK`, `SLIDES_TASK`. Sửa luật ở một chỗ, đừng chép
  ra bốn chỗ rồi để chúng trôi mỗi nơi một kiểu.

Ở mức **cả bộ slide**, `check_depth()` thêm một phép kiểm mà từng slide không
thấy được: deck về bài phương pháp mà **không có slide nào đi hết cơ chế** thì
người nghe nắm được bài toán và kết quả nhưng không kể lại được cách nó chạy.
`_walks_mechanism()` đòi **cả** dấu hiệu trình tự (bước / trước hết / đầu vào)
**lẫn** quan hệ nhân quả — đòi một từ khoá thì model học được cách rắc vào cho qua.

### Nhãn bài ngắn `P1`, `P2` — và vì sao không dùng mã thật

Mã bài (`p50d58cb2d3`, 11 ký tự) chỉ khác mã đoạn (`p50d58cb2d3c14`) ở phần đuôi.
Model lẫn hai thứ liên tục rồi viết ra mã 12 ký tự không tồn tại — đo trên bài
thật: **6 trong 9 cảnh báo** của một bản tổng hợp là "mã bài không có trong kho",
và vì không mã nào khớp nên cả phần hướng tiếp cận thành vô dụng.

`prompts.paper_labels()` đổi sang `P1`, `P2`… trong `corpus_digest`, `synth._clean`
đổi ngược về mã thật. Nhãn xếp theo `id` chứ không theo thứ tự truyền vào, vì
`corpus_digest` phải byte-identical thì cache mới hit.

### Bóc số: hai bên cố ý KHÔNG đối xứng

`verify.source_numbers()` bóc số ở phía **nguồn** rộng tay hơn `_NUM` (cho phép
đuôi chữ: `100M`, `7B`, `62.3%`, và cả `1,000` ↔ `1000`).

Phía câu trả lời phải chặt, vì ở đó đang hỏi "cái này có phải số liệu bịa không"
và `Qwen2.5-7B` không được tính là số liệu. Phía nguồn chỉ là đống cỏ để tìm kim:
bóc rộng hơn chỉ làm giảm báo động giả, **không thể** làm lọt số bịa. Đã báo oan
đúng vì chỗ này — bài ghi `100M frames`, câu trả lời viết "100 triệu khung hình".

### Chốt chặn: `verify.check_answer()`

Bản sao của `content_kept()` / `check_slides()` cho pass này, cùng triết lý
**cảnh báo chứ không chặn**. Phép kiểm đáng giá nhất vẫn là ràng buộc số liệu:
mọi con số phải có mặt nguyên văn trong đoạn đã trích (dùng lại `pipeline._NUM`,
`_URLISH`, `_norm_num`). Thêm ba thứ riêng của cơ chế này:

- mã đoạn phải **nằm trong tập đã lấy về ở lượt này** — trích một mã có thật
  trong kho nhưng không thuộc lượt này cũng là bịa, model không hề đọc nó;
- **giấu chỗ chưa tìm ra** bị bắt: còn mục chưa có bằng chứng mà câu trả lời
  không nói ra thì gắn cờ. Đây là kiểu hỏng tệ nhất của công cụ này;
- `check_entailment()` bắt *trích dẫn hình thức*: mã nguồn có thật, số liệu có
  thật, nhưng đoạn ấy không nói điều câu đó khẳng định.

Cảnh báo neo theo **chỉ số câu** (`split_sentences` giữ `start`/`end`), để giao
diện tô đúng câu chứ không tô cả bài.

### Chuyển bài sang kho khác — và vì sao đồ thị là chỗ dễ quên

Nạp nhầm kho là chuyện thường, mà cách chữa hiển nhiên — bỏ đi rồi nạp lại — ném
mất đúng phần đắt: phiếu, câu ngữ cảnh của từng đoạn, cây tóm lược, vector, bài
giảng. Bơm lại một bài tốn ~$0,034 và vài phút; `db.move_paper()` giữ nguyên tất
cả và **$0**.

Đoạn, vector và chỉ mục toàn văn **tự theo** vì chúng khoá theo `paper_id`, không
theo kho — `chunk` không có cột `survey_id` nên `chunk_fts` không phải đụng tới.

**Đồ thị thực thể thì không.** `entity.id` là sha của *(survey_id, tên đã chuẩn
hoá)*, nên cùng một thực thể ở hai kho là hai mã khác nhau. Bỏ qua chỗ này thì
bài sang kho mới mà thực thể của nó còn nằm ở kho cũ: đồ thị kho mới thiếu bài
đó, kho cũ đầy node mồ côi trỏ tới một bài không còn ở đấy. `move_paper` khoá
lại `entity` / `mention` / `edge`, rồi đếm lại `papers` cho **cả hai** kho theo
đúng luật của `put_graph` — hai chỗ lệch luật thì xoá nhầm thực thể đang còn
cạnh, và cạnh đó biến mất theo.

Ba chi tiết:

- **Trùng `sha256` ở kho đích thì từ chối** (409). Hai bản cùng một bài trong một
  kho làm mọi câu trả lời trích dẫn hai lần cùng một đoạn mà không ai hiểu vì
  sao. Đã bắt được một ca thật lúc thử.
- **Cạnh không khoá lại được thì bỏ.** `edge` lưu *mã* chứ không lưu tên, nên
  đầu mút thiếu dòng `entity` là không suy ra được tên để dựng mã ở kho đích.
  `graph.py:161` đã lọc sẵn nên chuyện này không xảy ra với dữ liệu thật; nếu có
  thì cạnh ấy vốn đã chết (`graph_overview` lọc theo `entity.papers`), chở sang
  kho mới với một mã thuộc kho cũ chỉ tệ thêm.
- **`synth_stale` của cả hai kho tự bật**, vì nó so với `corpus_fingerprint` mà
  vân tay tính từ danh sách bài. Không phải làm gì thêm.

Kiểm bằng vòng tròn trên dữ liệu thật: chuyển đi rồi chuyển về phải cho **trạng
thái khớp từng con số** — số thực thể, số cạnh, số đoạn, và không mention mồ côi
nào, không cạnh treo nào.

Và `svProg(msg, free = true)` cho mọi việc không gọi model: gắn tên model vào một
dòng ghi "miễn phí" thì tự mâu thuẫn, người dùng có lý do tưởng vừa bị tính tiền.

### Bài giảng (`survey/lecture.py`, `survey/refs.py`) — giảng MỘT bài cho hiểu

Tab *Bài giảng* đứng giữa hai tab kia và lấp đúng chỗ trống giữa chúng: *Tổng
hợp* nói về cả kho mà không đi sâu bài nào, *Hỏi đáp* đi sâu được nhưng **đòi
người ta biết trước phải hỏi gì** — mà lúc mới mở một bài lạ ra thì đó chính là
thứ chưa có.

**Câu trích dẫn của chính tác giả là thứ thay thế việc đọc ba mươi bài tham
khảo.** Đây là toàn bộ thiết kế của phần đối chiếu. Cách hiển nhiên — tải về mọi
bài được dẫn rồi bắt model đọc — tốn gấp vài chục lần và **vẫn tệ hơn**, vì đọc
cả bài được dẫn thì model phải tự đoán bài chính đã lấy ý nào từ đó. Trong khi
tác giả đã viết sẵn câu trả lời ngay tại chỗ trích dẫn.

Semantic Scholar Graph API phát không thứ đó, **không cần key**:

| Trường | Được gì | Thay cho |
|---|---|---|
| `contexts` | nguyên văn câu chứa chỗ trích dẫn | đọc cả bài được dẫn |
| `isInfluential` | bài này có thật sự dựa vào bài kia không | đếm số lần dẫn |
| `tldr` | tóm tắt một câu, model SciTLDR dựng sẵn | một lượt gọi model mỗi bài |

Đo trên bài LAPA: 63 tham khảo, **58 có câu trích dẫn**, lấy về bằng **hai**
request HTTP, **$0**. Ba chỗ phải cẩn thận:

- **`intents` không đáng tin.** Nó cần S2 có toàn văn bài dẫn, mà bản tiền ấn
  arXiv thì thường không — đo trên LAPA: 0/63. Xếp hạng phải dựa vào `contexts`,
  `intents` chỉ là gia vị.
- **Khớp theo tiêu đề là khớp mờ**, nên `resolve()` tự soát lại độ trùng, và
  `usable_title()` chặn hẳn tiêu đề quá ngắn (đã gặp một bài bóc hỏng còn mỗi
  "Question Answering" — khớp trúng hàng nghìn bài). Dựng bài giảng đối chiếu
  với NHẦM bài còn tệ hơn hẳn không có phần đối chiếu, vì nhìn vẫn có vẻ đúng.
- **Ba trường hợp "không có hồ sơ" phải nói khác nhau**, vì cách xử lý khác
  nhau: tiêu đề hỏng (sửa tiêu đề), không khớp được (chịu), và **khớp được mã
  nhưng S2 chưa bóc xong tham khảo** — hay gặp với bản tiền ấn vừa đăng, và
  người dùng chỉ cần chờ. Trường `why` mang lý do lên giao diện.
- **Câu trích dẫn do máy bóc nên có thể lệch** (đã gặp: một câu nói về GENMO bị
  gán cho AMO). Prompt phải nói rõ điều đó và cấm khẳng định quá thứ câu ấy chứa.

Tám mục, và **thứ tự là kết luận từ nghiên cứu, không phải khẩu vị**: `prereq`
(kiến thức bài giả định bạn đã có) đứng **đầu** vì thứ chặn người đọc là nền
không được nói ra chứ không phải câu dài — đó là *curse of knowledge*, tác giả
bỏ qua phần "ai trong nhánh này cũng biết". Rồi `problem` → `why_hard` →
`mechanism` (chạy tay một ví dụ, mỗi bước nói **vì sao** bước ấy cần) →
`compare` → `evidence` → `limits` → `check`. Mục `check` là câu hỏi **vì sao /
điều gì xảy ra nếu**, không phải câu tra cứu: chính lúc người đọc tự dựng lại
lời giải thích mới là lúc họ học được. Paper Plain (TOCHI 2023) đo được rằng
tóm lược tại chỗ + bộ câu hỏi dẫn đường làm người không chuyên đọc dễ hơn hẳn
**mà không giảm mức hiểu**.

Bốn chỗ đã vấp thật khi chạy trên bài thật, đừng vấp lại:

- **Tắt hẳn nghĩ thầm** (`NO_REASONING`). Để `{"effort":"low"}` thì hai mẻ trên
  bốn chạy 76 giây rồi trả về **chuỗi rỗng** — token bị phần nghĩ thầm ăn sạch
  trước khi tới phần viết. Độ sâu đến từ `SECTIONS` + `DEPTH_RULES` + vòng viết
  lại, không đến từ token nghĩ thầm; ở đây nghĩ thầm còn *tranh chỗ* với phần
  cần dài.
- **Mẻ nhỏ, `mechanism` đi một mình.** Vừa cho mục dài nhất trọn cả trần đầu ra,
  vừa để mọi mẻ về đích trong 300s timeout của `llm`. Chia nhỏ gần như miễn phí
  vì prefix vẫn ấm: đo thật **93.952/117.704 token đọc vào lấy từ cache**.
- **Mẻ hỏng phải thử lại, và mục thiếu phải vào `warns`.** Bỏ qua là người dùng
  trả tiền các mẻ khác rồi nhận bài giảng thiếu ba mục, mà lý do chỉ thoáng qua
  một dòng tiến trình đã trôi mất từ hôm trước.
- **In đậm chỉ có tác dụng khi nó ngắn.** Model hay viết `do` và `point` thành cả
  đoạn 700 ký tự; in đậm hoặc nhuộm màu tiêu đề nguyên đoạn thì mắt không còn
  chỗ bám. `LEAD_MAX = 90` bên server và `SV_LEAD_MAX` bên `survey.js` là **một
  quyết định ở hai chỗ** — đổi một bên phải đổi bên kia.

Chi phí đo thật trên LAPA (101 đoạn): **175 giây, $0,0099** cho 5.750 từ.

Vòng **đào sâu** là chỗ trả lời cho yêu cầu "tự đào sâu": `depth.check_text()`
chấm xong thì mục nào bị bắt được viết lại **kèm đúng câu bị chê**, một lượt.
Chê chung chung ("viết sâu hơn") thì model viết *dài* hơn chứ không *sâu* hơn.
Số bịa và mã đoạn sai **không** vào lời chê (`_shallow` lọc ra): viết lại không
sửa được kiểu hỏng đó, nhồi vào chỉ làm loãng đúng chỗ cần chê. Chạy thật trên
LAPA: `limits` bị chấm, viết lại, rồi hết cảnh báo.

**Ràng buộc số liệu chỉ áp cho mục KHẲNG ĐỊNH VỀ BÀI** (`CLAIM_SECTIONS`).
`mechanism` và `problem` cố ý kể một *tình huống ví dụ* — "giả sử video dài 10
phút, N = 600 khung hình" không phải kết quả của ai cả. Không phân biệt hai thứ
đó thì chốt chặn **kêu oan 32 lần cho một bài** (đã đo), và lúc ấy người dùng
thôi đọc cảnh báo — cảnh báo thật ở `evidence` trôi theo. Cùng bài học với mấy
hằng ngân sách của slide.

Kèm theo: `_TIMEISH` phải cắt mốc thời gian **trước** khi bóc số, vì `_NUM` biến
`[00:12:30-00:12:35]` thành sáu số rời không con nào là số liệu. Và giao diện
**gom cảnh báo trùng loại trong cùng một mục** thành một dòng gập được, để lần
sau có kêu nhiều thì cũng không nuốt mất phần còn lại.

Ba chỗ khác dễ vấp:

- **`_texts()` phải bóc chữ theo hình dạng riêng của từng mục** — `mechanism`
  giấu chữ trong `steps[].why`, `check` trong `items[].a`. Soát trên
  `json.dumps` thì tên khoá và dấu ngoặc lọt vào phép đếm và mọi phép kiểm lệch.
  Mục `check` cố ý **không** soát độ sâu: câu hỏi tự kiểm vốn ngắn.
- **Hai cột `refs` và `lecture` phải nằm NGOÀI `list_papers()`** (xem `_HEAVY`).
  Không phải vì nặng, mà vì `corpus_digest` dựng từ danh sách đó và phải
  byte-identical giữa mọi câu hỏi — cột đổi theo từng lần dựng bài giảng mà lọt
  vào là hỏng cache của cả kho.
- **Ô chọn bài giãn theo option dài nhất**, mà option ở đây là *tiêu đề bài* —
  dài nhất trong cả app. Cộng `min-width: auto` mặc định của flex item là thanh
  công cụ rộng 450px trong khung 369px. Cần `min-width: 0`.

Và một bẫy của chính việc soát bằng trình duyệt: **`Page.navigate` tới URL chỉ
khác phần `#hash` thì KHÔNG tải lại trang**, nên CSS/JS cũ còn nguyên và mọi
phép đo nói dối. Phải `Page.reload(ignoreCache=True)`.

**Font mono không dựng nổi dấu tiếng Việt chồng tầng** — đã nhìn thấy "số" ra
"sô´", "chuỗi" ra "chuôĩ", "biểu" ra "biêủ". Nên `.sv-note` (văn xuôi giải thích
ký hiệu) dùng font thường; chỉ ký hiệu lẻ trong `<code>` mới để mono. Cùng họ
với cái bẫy `line-height` ở slide.

### Đủ bộ CRUD — mỗi thứ người dùng tạo ra phải sửa và xoá được

Phần lớn màn hình ban đầu chỉ có **tạo** và **đọc**. Kiểu thiếu này không lộ ra
lúc thử, vì lúc thử thì cái gì cũng vừa mới tạo và vừa đúng; nó lộ ra lúc dùng
thật, khi một thứ vào sai và **không có đường nào sửa ngoài xoá đi làm lại** —
mà làm lại thì tốn tiền.

Chỗ đau nhất đã gặp: một bài bóc hỏng tiêu đề, còn mỗi *"Question Answering"*.
Tiêu đề không chỉ là nhãn — nó nằm trong **chỉ mục toàn văn** (`chunk.title`),
trong **phiếu toàn kho** gửi cho model, và là thứ dùng để **tra Semantic
Scholar** cho phần đối chiếu. Sai tiêu đề là hỏng cả ba, và trước bản này không
sửa được ở đâu cả.

Ranh giới phải giữ: **cho sửa thứ người nhập, không cho sửa thứ pass sinh ra.**
`PATCH …/paper/{pid}` nhận `title` / `year` / `venue` / `authors` / `url`, và
lặng lẽ bỏ qua `card` / `status` / `lecture`. Sửa tay được mấy cột đó thì
`check_answer`, ràng buộc số liệu và `check_depth` thành vô nghĩa — cùng lý do
`PATCH …/slides` cấm sửa `source_block_ids`.

Ba chỗ dễ quên khi thêm đường xoá:

- **Xoá phải kéo theo thứ trỏ tới nó.** `drop_run` phải xoá cả `qcache`: bỏ sót
  thì hỏi lại đúng câu đó trúng cache, tra ra một `run_id` không còn tồn tại, và
  người dùng nhận màn hình trống mà không hiểu vì sao.
- **Đổi tiêu đề phải đồng bộ xuống `chunk.title` rồi reindex** — `update_paper`
  đã lo, đừng ghi thẳng vào bảng (xem bẫy external content).
- **Hỏi trước khi xoá thứ dựng lại tốn tiền, và nói rõ bao nhiêu.** "Bạn có chắc
  không" mà không kèm giá thì người dùng không có cơ sở nào để chắc.

Đổi tên bài ở luồng đọc-hiểu thì an toàn tuyệt đối: `title` **không** nằm trong
`cached_prefix`, nên không bản dịch nào phải bỏ đi.

Và một bẫy của chính việc soát: **uvicorn không bind được cổng thì im lặng bỏ
qua**, tiến trình cũ vẫn phục vụ mã cũ và mọi phép thử nói dối — đã mất công
truy một lỗi 405 "Method Not Allowed" của route vừa thêm, hoá ra server đang
chạy là bản khởi động từ nửa tiếng trước. Cùng họ với container Docker giữ cổng
8010. Soát bằng `ps` trước khi tin kết quả.

### Một danh sách trường, không hai bản chép

`PATCH /api/survey/{sid}` từng giữ **bản chép riêng** của danh sách trường sửa
được, và bản chép đó thiếu `model` / `fast_model`. Hậu quả: chọn model xong thì
lựa chọn bị vứt **lặng lẽ** — không lỗi, không cảnh báo, `update_survey` không
bao giờ thấy nó — rồi `svLoad()` đọc lại giá trị cũ và ô chọn nhảy về "Theo
.env". Người dùng thấy đúng một thứ: **bấm vào rồi mà không chọn được gì**, y
hệt triệu chứng dropdown tự đóng, nên rất dễ đi truy nhầm hướng.

Nên `db.SURVEY_FIELDS` là **nguồn duy nhất**, route lọc theo đúng nó.
`test_route_khong_giu_ban_chep_rieng_cua_danh_sach_truong` canh chỗ này.

Ngược lại, `PAPER_USER_FIELDS` ở `survey_api.py` **cố ý hẹp hơn**
`db.update_paper`: hàm đó còn nhận `card` / `status` / `lecture` cho các pass
nội bộ ghi vào, còn route thì không được cho sửa tay — sửa được thì ràng buộc số
liệu và `check_depth` thành vô nghĩa. Hai danh sách khác nhau vì phục vụ hai
việc khác nhau, không phải một bản chép bị lệch; đặt tên cho nó để lần sau không
ai "sửa" bằng cách nới ra.

**Bài học chẩn đoán:** triệu chứng người dùng kể ("mở ra là đóng lại") và
nguyên nhân thật (giá trị không lưu được) có thể trông giống hệt nhau. Hỏi thêm
một câu — *"nó có hiện cái vừa chọn không"* — rẻ hơn nhiều so với soát cả cây
sự kiện DOM.

### `<select>` không được lồng trong `<label>`

Click vào một ô chọn nằm trong `<label>` thì cú click nổi lên label, label
chuyển tiếp thành **một cú kích hoạt nữa** xuống chính cái select — dropdown mở
ra rồi đóng lại ngay, không kịp chọn. Lỗi Chromium đã biết từ lâu, và nó ảnh
hưởng **cả tám** ô chọn của app trước bản này.

Cái bẫy nằm ở chỗ **soát bằng máy không bắt được**: dispatch `mousedown`/`click`
tổng hợp lên chính cái select thì cú click không đi qua label, MutationObserver
không thấy gì, focus vẫn giữ — mọi phép đo báo "bình thường". Chỉ bấm bằng
chuột thật mới lộ.

Nên: nhãn đứng riêng, nối bằng `for=`, khối bọc là `<div>` (`.fld` thay chỗ
`<label>` trong `.sv-optbox`). Vừa hết lỗi vừa đúng chuẩn trợ năng.
`test_khong_o_chon_nao_bi_long_trong_label` canh cấu trúc này, vì đây đúng là
loại lỗi mà chỉ phép kiểm cấu trúc mới giữ được.

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
