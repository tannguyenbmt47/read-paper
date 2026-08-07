# Chạy bằng Docker

## Ba bước

```bash
cp .env.example .env
# mở .env, điền OPENROUTER_API_KEY lấy từ https://openrouter.ai/keys
echo "DOCKER_UID=$(id -u)" >> .env
echo "DOCKER_GID=$(id -g)" >> .env

docker compose up -d --build
```

Mở http://127.0.0.1:8000

Đổi cổng: `PORT=8010 docker compose up -d`

## Hai chỗ dễ vấp, đã gặp thật

**1. `unable to open database file`** — container chạy bằng uid riêng, không ghi
nổi vào `./data` của máy chủ. Đó là lý do có `DOCKER_UID` / `DOCKER_GID` trong
`.env`: chúng bắt container chạy bằng đúng uid của bạn. Thiếu hai dòng đó là
SQLite chết ngay lúc mở bài đầu tiên.

**2. Mount phải nằm ở chỗ Docker daemon nhìn thấy được.** Thư mục tạm của một số
môi trường (`/tmp/...` bị cô lập) không mount được — volume im lặng thành rỗng và
app tưởng chưa có bài nào. Cứ để `./data` cạnh `docker-compose.yml` là chắc.

## Mô hình bố cục (docling)

Ảnh mặc định **không** kèm docling: nó kéo theo torch và bộ mô hình, đẩy ảnh từ
**302MB lên nhiều GB**. Không có nó thì `parser.parse_pdf()` vẫn chạy bằng
heuristic của PyMuPDF — khung cắt hình kém chính xác hơn ở trang nhiều bảng, chứ
không hỏng.

Cần độ chính xác đó thì:

```bash
WITH_LAYOUT=1 docker compose build
# rồi bỏ LAYOUT_BACKEND=off trong docker-compose.yml
```

## Dữ liệu

Tất cả nằm ở `./data` (SQLite `papers.db`, PDF gốc, ảnh cắt ra). Nâng cấp ảnh
không mất bài đã đọc. Sao lưu = copy thư mục đó.

## Vì sao ảnh cần font

`fonts-liberation` **không phải để hiển thị** — server không vẽ gì cả.
`server/slide_fit.py` dùng nó để đo bề rộng chữ bằng metric thật (Liberation Sans
tương thích metric với Arial, đúng font mà `_SLIDES_CSS` chỉ định), rồi tính xem
slide có tràn khung 1280×720 không và tự co cỡ chữ cho vừa. Gỡ font đi thì bộ đo
rơi về ước lượng thô và slide bị cắt mất chữ ở đáy.

## Kiểm nhanh sau khi dựng

```bash
docker compose ps                          # phải thấy "healthy"
curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:8000/api/config   # 200
docker compose logs -f app                 # xem lỗi nếu có
```
