"""Đẩy MỌI test sang một thư mục dữ liệu tạm — chạy trước khi import `server`.

Đây không phải tiện nghi, nó là hàng rào. `server/db.py` đọc `PAPER_DATA_DIR`
**ngay lúc import** (`DATA_DIR = Path(os.getenv(...))`), nên biến môi trường đặt
muộn hơn thời điểm đó thì vô tác dụng — module đã chốt đường dẫn rồi.

Và pytest **import mọi file test lúc thu thập**, trước khi chạy fixture đầu tiên.
`tests/test_unit.py` import `server.pipeline` ở cấp module, mà nó kéo theo
`server.store` → `server.db`. Nên tới lúc fixture của `test_api.py` hay
`test_survey.py` đặt biến môi trường thì đã muộn: `db.DATA_DIR` đang trỏ vào
`data/` thật của người dùng.

Đã hỏng đúng như vậy: bộ test ghi 2 bài rác vào `data/papers.db` và 66 kho survey
rác, lẫn vào dữ liệu thật. Không mất gì, nhưng đó là thứ không được phép xảy ra —
người ta chạy `pytest` với niềm tin rằng nó không đụng vào việc của mình.

`conftest.py` được pytest nạp **trước** mọi module test, nên đặt biến ở đây là
chỗ sớm nhất còn kịp. Fixture trong từng file test vẫn giữ nguyên phần đặt biến
của chúng: chạy một file lẻ vẫn phải an toàn, không phụ thuộc file này.
"""

from __future__ import annotations

import os
import tempfile

# Đặt NGAY lúc nạp module này, không đặt trong fixture — fixture chạy sau khi các
# file test đã được import, tức là sau khi `server.db` đã chốt DATA_DIR.
_TMP = tempfile.mkdtemp(prefix="loupe-tests-")
os.environ["PAPER_DATA_DIR"] = _TMP
os.environ.setdefault("OPENROUTER_API_KEY", "test-key-khong-goi-model")

# Bộ test không được kéo về 2,3GB trọng số, cũng không được đòi có GPU. Đường
# BM25 phải chạy đúng một mình — đó cũng là đường mà máy không có GPU sẽ chạy.
os.environ.setdefault("EMBED_BACKEND", "off")
os.environ.setdefault("RERANK_BACKEND", "off")

# Mô hình bố cục nạp mất hàng chục giây và không test nào cần tới nó.
os.environ.setdefault("LAYOUT_BACKEND", "off")


def pytest_report_header(config):
    return f"dữ liệu test: {_TMP} (data/ thật không bị đụng tới)"
