# Đọc paper song ngữ — ảnh chạy được ngay, không cần cài gì trên máy chủ.
#
# Mặc định KHÔNG kèm docling: nó kéo theo torch và bộ mô hình, đẩy ảnh từ ~400MB
# lên nhiều GB. Không có docling thì `parser.parse_pdf()` vẫn chạy bằng heuristic
# của PyMuPDF — chậm hơn về chất lượng khung hình chứ không hỏng. Cần bố cục
# chính xác thì build lại với `--build-arg WITH_LAYOUT=1`.
#
#   docker compose up -d --build          # bản gọn
#   WITH_LAYOUT=1 docker compose build     # bản có mô hình bố cục

FROM python:3.12-slim

ARG WITH_LAYOUT=0

# fonts-liberation KHÔNG phải để hiển thị: `server/slide_fit.py` dùng nó để đo
# bề rộng chữ bằng metric thật (tương thích Arial) rồi tính xem slide có tràn
# khung không. Thiếu font này thì bộ đo rơi về ước lượng thô và slide bị cắt chữ.
# fonts-dejavu-core lo phần dấu tiếng Việt khi Liberation thiếu glyph.
RUN apt-get update && apt-get install -y --no-install-recommends \
        fonts-liberation fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
# Bỏ docling khỏi danh sách trừ khi được yêu cầu rõ ràng
RUN if [ "$WITH_LAYOUT" = "1" ]; then \
        cp requirements.txt /tmp/req.txt; \
    else \
        grep -v '^docling' requirements.txt > /tmp/req.txt; \
    fi \
    && pip install --no-cache-dir -r /tmp/req.txt

COPY server/ ./server/
COPY web/ ./web/

# Dữ liệu (SQLite, PDF gốc, ảnh cắt ra) nằm ở volume để nâng cấp ảnh không mất bài
ENV PAPER_DATA_DIR=/data \
    PYTHONUNBUFFERED=1 \
    PORT=8010
RUN mkdir -p /data

EXPOSE 8010

# Chạy bằng user thường: container phục vụ file người dùng tải lên, không có lý
# do gì để nó chạy bằng root. `docker-compose.yml` ghi đè uid này thành uid của
# người dùng trên máy chủ, nếu không thì không ghi nổi vào volume ./data.
RUN useradd -m -u 10001 app && chmod 777 /data && chown -R app:app /app
USER app

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request,sys; \
sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8010/api/config',timeout=4).status==200 else 1)"

CMD ["sh", "-c", "uvicorn server.main:app --host 0.0.0.0 --port ${PORT:-8010}"]
