# Đọc paper song ngữ — ảnh chạy được ngay, không cần cài gì trên máy chủ.
#
# Mặc định KHÔNG kèm docling: nó kéo theo torch và bộ mô hình, đẩy ảnh từ ~400MB
# lên nhiều GB. Không có docling thì `parser.parse_pdf()` vẫn chạy bằng heuristic
# của PyMuPDF — chậm hơn về chất lượng khung hình chứ không hỏng. Cần bố cục
# chính xác thì build lại với `--build-arg WITH_LAYOUT=1`.
#
#   docker compose up -d --build                    # bản gọn, ~400MB
#   WITH_LAYOUT=1 docker compose build              # kèm MinerU (GPU)
#   WITH_LAYOUT=1 TORCH_CPU=1 docker compose build  # kèm MinerU (CPU, gọn hơn ~2,7GB)
#
# Có mô hình rồi thì chọn backend bằng `LAYOUT_BACKEND=mineru|docling`. MinerU
# bắt công thức hiển thị tốt hơn hẳn (xem `server/layout.py`); trọng số của nó
# tải về lần chạy đầu nên hãy mount `~/.cache/huggingface` vào container, không
# thì mỗi lần dựng lại ảnh là tải lại từ đầu.

FROM python:3.12-slim

ARG WITH_LAYOUT=0
ARG TORCH_CPU=0

# fonts-liberation KHÔNG phải để hiển thị: `server/slide_fit.py` dùng nó để đo
# bề rộng chữ bằng metric thật (tương thích Arial) rồi tính xem slide có tràn
# khung không. Thiếu font này thì bộ đo rơi về ước lượng thô và slide bị cắt chữ.
# fonts-dejavu-core lo phần dấu tiếng Việt khi Liberation thiếu glyph.
RUN apt-get update && apt-get install -y --no-install-recommends \
        fonts-liberation fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
# Chọn backend bố cục nào được cài. Cả hai đều kéo theo torch nên đẩy ảnh từ
# ~400MB lên nhiều GB — vì thế mặc định không cài cái nào.
#
#   0        không cài gì (mặc định)
#   1|mineru chỉ MinerU — nhanh hơn docling ~30 lần và bắt công thức tốt hơn,
#            nên đây là lựa chọn đúng cho gần như mọi trường hợp
#   docling  chỉ docling
#   all      cả hai
RUN case "$WITH_LAYOUT" in \
        1|mineru) grep -v '^docling' requirements.txt > /tmp/req.txt ;; \
        docling)  grep -v '^mineru'  requirements.txt > /tmp/req.txt ;; \
        all)      cp requirements.txt /tmp/req.txt ;; \
        *)        grep -v '^docling' requirements.txt | grep -v '^mineru' > /tmp/req.txt ;; \
    esac \
    # torch bản CUDA kéo theo ~2,7GB thư viện nvidia. Đo trên bài 36 trang: GPU
    # 21,2s so với CPU 35,2s — tức trả 2,7GB cho 14 giây mỗi bài. Đáng khi có
    # GPU và đã cấp cho container, còn lại thì `TORCH_CPU=1` cho ảnh gọn hơn hẳn.
    && if [ "$TORCH_CPU" = "1" ]; then \
        pip install --no-cache-dir --index-url https://download.pytorch.org/whl/cpu \
            torch torchvision; \
    fi \
    && pip install --no-cache-dir -r /tmp/req.txt

# MinerU kéo theo `opencv-python`, bản này liên kết với thư viện X của máy để
# bật cửa sổ xem ảnh — thứ một container server không bao giờ có. Thiếu chúng
# thì `import` chết với `ImportError: libxcb.so.1: cannot open shared object
# file`, mà `blocks_from_layout` lại bắt Exception rồi **im lặng** rơi về
# heuristic: người dùng nạp bài thấy "xong" trong 1,3 giây, công thức mất sạch,
# và không có dấu hiệu nào ngoài một dòng log.
#
# Bản `headless` cùng module `cv2`, chỉ bỏ phần giao diện. Đặt ở lớp RUN riêng
# để sửa chỗ này không phải dựng lại toàn bộ lớp pip phía trên.
#
# `six` thì MinerU **dùng mà không khai**. Trên máy dev nó có sẵn vì docling kéo
# `rapidocr` về, nên lỗi chỉ lộ ra trong container gọn — và lộ theo kiểu tệ nhất:
# rơi về heuristic im lặng, đúng như ca opencv ở trên.
RUN if [ "$WITH_LAYOUT" != "0" ]; then \
        pip uninstall -y opencv-python opencv-contrib-python 2>/dev/null || true; \
        pip install --no-cache-dir opencv-python-headless six; \
    fi

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
