# Chạy Loupe bằng Docker

Docker đóng gói sẵn Python và mọi thư viện, nên máy chỉ cần cài Docker. Dữ liệu
(bài báo, bản dịch, slide) nằm ở thư mục `data/` ngay cạnh mã nguồn, nên nâng
cấp hay dựng lại ảnh đều không mất bài.

## Cần gì

- Docker có Compose v2. Gõ thử `docker compose version`.
- Một API key của OpenRouter: <https://openrouter.ai/keys>.
- Khoảng 400 MB đĩa cho ảnh gọn, hoặc vài GB nếu kèm mô hình bố cục (xem mục 3).

## 1. Chạy nhanh

```bash
git clone https://github.com/tannguyenbmt47/read-paper.git loupe
cd loupe

cp .env.example .env
# Mở .env, điền OPENROUTER_API_KEY=...
echo "DOCKER_UID=$(id -u)" >> .env
echo "DOCKER_GID=$(id -g)" >> .env

docker compose up -d --build
```

Lần đầu mất khoảng 2 phút để dựng ảnh. Xong thì mở <http://localhost:8010>.

Hai dòng `DOCKER_UID` / `DOCKER_GID` cho container chạy bằng đúng tài khoản của
bạn. Thiếu chúng thì container không ghi được vào `data/`, và app báo
`unable to open database file` ngay khi nạp bài đầu tiên.

Đổi cổng: đặt `PORT=9000` trong `.env` rồi `docker compose up -d`.

## 2. Kiểm tra đã chạy đúng

```bash
docker compose ps        # cột STATUS phải là "healthy"
curl -s -o /dev/null -w "%{http_code}\n" http://localhost:8010/api/config   # 200
docker compose logs -f app
```

## 3. Hai loại ảnh

| | Ảnh gọn (mặc định) | Ảnh kèm MinerU |
|---|---|---|
| Dung lượng | ~350 MB | vài GB (có torch) |
| Bóc PDF | heuristic của PyMuPDF | mô hình bố cục PP-DocLayoutV2 |
| Công thức hiển thị cắt thành ảnh | gần như không | đủ (đo: 7/7 trên bài 36 trang) |
| Thời gian bóc một bài | 1–3 giây | 6–35 giây |

Ảnh gọn đủ cho phần lớn bài. Bài nhiều công thức hoặc bảng phức tạp thì nên dùng
MinerU. Cách bật: sửa trong `.env`

```bash
WITH_LAYOUT=mineru
LAYOUT_BACKEND=mineru
TORCH_CPU=1            # máy không có GPU NVIDIA: torch bản CPU, nhẹ hơn ~2,7 GB
```

rồi dựng lại:

```bash
docker compose up -d --build
```

Trọng số mô hình (~200 MB) tải về ở lần nạp bài đầu tiên và lưu ở `data/models`,
nên các lần dựng lại ảnh sau không phải tải lại.

### Dùng GPU NVIDIA

Cần cài `nvidia-container-toolkit` trên máy chủ, rồi trong `.env`:

```bash
TORCH_CPU=0
DOCKER_RUNTIME=nvidia
MINERU_DEVICE=cuda
```

GPU chỉ làm bước bóc PDF nhanh hơn (đo: 21 giây so với 35 giây trên CPU). Không
có GPU vẫn chạy bình thường.

## 4. Cấu hình

Mọi cấu hình nằm trong `.env`. Các khoá hay dùng:

| Khoá | Ý nghĩa |
|---|---|
| `OPENROUTER_API_KEY` | **Bắt buộc** |
| `OR_MODEL` | Model dịch mặc định (`~deepseek/deepseek-v4-flash-latest`) |
| `OR_MODEL_FAST` | Model rẻ cho các việc nhẹ |
| `PORT` | Cổng trên máy chủ (mặc định 8010) |
| `WITH_LAYOUT`, `LAYOUT_BACKEND`, `TORCH_CPU` | Mô hình bố cục, xem mục 3 |
| `DOCKER_RUNTIME`, `MINERU_DEVICE` | GPU, xem mục 3 |
| `HF_CACHE` | Nơi lưu trọng số mô hình. Phải là đường dẫn tuyệt đối |
| `EMBED_BACKEND` | `off` để tắt tìm kiếm ngữ nghĩa của kho survey |

Sửa `.env` xong chỉ cần `docker compose up -d`. Riêng `WITH_LAYOUT` và `TORCH_CPU`
quyết định lúc dựng ảnh, nên phải thêm `--build`.

Ảnh gọn không có torch, nên tìm kiếm ngữ nghĩa của kho survey tự lùi về tìm theo
từ khoá (BM25). App vẫn chạy và màn survey nói rõ lý do.

## 5. Dữ liệu, sao lưu, nâng cấp

Toàn bộ dữ liệu nằm trong `data/`:

- `papers.db`: cơ sở dữ liệu SQLite (bài, bản dịch, ghi chú, slide, thư viện)
- `<mã bài>.pdf`: PDF gốc
- `<mã bài>-img/`: ảnh hình, bảng, công thức đã cắt
- `models/`: trọng số mô hình (nếu dùng MinerU)

**Sao lưu:** dừng app rồi chép cả thư mục.

```bash
docker compose stop
tar czf loupe-data-$(date +%F).tar.gz data
docker compose start
```

**Nâng cấp lên bản mới:**

```bash
git pull
docker compose up -d --build
```

Dữ liệu cũ giữ nguyên; app tự thêm cột mới vào cơ sở dữ liệu khi khởi động.

## 6. Đem sang máy khác

**Cách 1: dựng lại trên máy mới (khuyên dùng).** Chép mã nguồn, `.env` và thư mục
`data/` sang, rồi `docker compose up -d --build`.

**Cách 2: chép nguyên ảnh đã dựng** (máy đích không có mạng, hoặc không muốn dựng
lại):

```bash
# máy nguồn
docker save loupe | gzip > loupe-image.tar.gz

# máy đích (cần docker-compose.yml, .env và data/ bên cạnh)
gunzip -c loupe-image.tar.gz | docker load
docker compose up -d
```

## 7. Phát triển

Bình thường ảnh chạy đúng bản mã đã đóng gói, và sửa mã thì phải dựng lại ảnh.
Khi đang sửa mã, mount thẳng mã nguồn vào container cho nhanh:

```bash
cp docker-compose.dev.yml docker-compose.override.yml
docker compose up -d
```

Compose tự nạp `docker-compose.override.yml`. Từ đó:

- sửa `web/` → F5 trên trình duyệt
- sửa `server/` → `docker compose restart`

Muốn quay về chạy bản đóng gói thì xoá `docker-compose.override.yml`.

## 8. Sửa lỗi thường gặp

**`unable to open database file`.** Thiếu `DOCKER_UID` / `DOCKER_GID` trong
`.env`, xem mục 1.

**Nạp bài xong nhưng không thấy bài nào, hoặc `data/` trống.** Thư mục mount phải
nằm ở chỗ Docker nhìn thấy được. Các thư mục tạm cô lập (`/tmp/...` ở một số môi
trường) mount lên là rỗng mà không báo lỗi. Hãy để `data/` cạnh
`docker-compose.yml`.

**Docker cài qua snap.** Có hai bẫy:

- Dấu `~` trong đường dẫn volume không nở ra thư mục home của bạn. Container nhận
  một thư mục rỗng trong khi `docker compose config` trông vẫn đúng. Luôn dùng
  đường dẫn tuyệt đối cho `HF_CACHE`.
- Cờ `--gpus` bị từ chối. Dùng `DOCKER_RUNTIME=nvidia` như ở mục 3.

**Đã bật MinerU mà công thức vẫn không thành ảnh.** Xem `docker compose logs app`.
App ghi rõ lý do khi rơi về heuristic. Kiểm tra cả hai khoá: `WITH_LAYOUT=mineru`
(và đã dựng lại với `--build`) và `LAYOUT_BACKEND=mineru`. Lưu ý app nhớ kết quả
bóc theo nội dung file PDF, nên nạp lại đúng file cũ sẽ nhận bản bóc cũ. Hãy dùng
nút **Bóc lại từ PDF (miễn phí)** trong màn soát.

**Cổng 8010 đang bận.** Đặt `PORT=` khác trong `.env`.

**Sửa mã mà giao diện không đổi.** Ảnh chạy bản đã đóng gói: dựng lại bằng
`docker compose up -d --build`, hoặc dùng chế độ phát triển ở mục 7.

## Lệnh thường dùng

```bash
docker compose up -d --build   # dựng (nếu cần) và chạy
docker compose stop            # dừng
docker compose restart         # khởi động lại
docker compose logs -f app     # xem log
docker compose down            # gỡ container (dữ liệu trong data/ vẫn còn)
```
