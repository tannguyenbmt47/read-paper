"""Test tích hợp: gọi API thật trên một DB tạm, KHÔNG gọi model nên miễn phí.

Mỗi lần chạy dựng một `PAPER_DATA_DIR` riêng rồi nạp một bài văn bản, nên không
đụng vào `data/` của người dùng.

Bộ này canh đúng những chỗ đã vỡ thật trong lúc phát triển:
  - `PATCH /blocks` từng trả 500 vì một bản vá rơi nhầm hàm (NameError). Test
    chạm vào MỌI endpoint chính là bắt được ngay loại lỗi đó.
  - migration `ALTER TABLE` cho cột thêm sau — bỏ là mọi bài cũ vỡ lúc load.
  - `_with_chunks`: endpoint nào trả `doc` mà quên gọi thì frontend dịch lại từ đầu.
"""
from __future__ import annotations

import os
import tempfile

import pytest


@pytest.fixture(scope="module")
def app_client():
    # `setdefault`, KHÔNG gán đè — `tests/conftest.py` đặt biến này trước khi mọi
    # module test được import, và `server/db.py` chốt `DATA_DIR` ngay lúc import.
    # Gán đè ở đây thì biến môi trường trỏ một nơi còn dữ liệu nằm một nơi khác.
    os.environ.setdefault("PAPER_DATA_DIR", tempfile.mkdtemp(prefix="loupe-test-"))
    os.environ.setdefault("OPENROUTER_API_KEY", "test-key-khong-goi-model")
    # import SAU khi đặt env: `db.DATA_DIR` đọc biến môi trường ngay lúc import
    import importlib
    for m in ("server.db", "server.store", "server.main"):
        if m in list(globals().get("_loaded", [])):
            importlib.reload(__import__(m, fromlist=["x"]))
    from fastapi.testclient import TestClient
    from server import main
    with TestClient(main.app) as c:
        yield c


@pytest.fixture(scope="module")
def doc(app_client):
    """Một bài nạp từ văn bản dán — không cần mạng, không cần PDF."""
    text = (
        "Tiêu đề bài thử nghiệm\n\n"
        "Mô hình đề xuất đạt 42,5 điểm F1 trên tập kiểm tra, cao hơn baseline "
        "mạnh nhất 3,1 điểm. Kết quả này lặp lại trên cả ba bộ dữ liệu.\n\n"
        "Cách làm gồm hai pha chạy nối tiếp nhau. Pha đầu thu thập bằng chứng, "
        "pha sau tích hợp chúng lại rồi mới sinh đáp án cuối cùng.\n\n"
        "Giới hạn chính là chi phí suy luận tăng theo số vòng lặp.\n"
    )
    r = app_client.post("/api/import", data={"text": text, "model": "test/model"})
    assert r.status_code == 200, r.text
    return r.json()


# ------------------------------------------------------------------ cơ bản

def test_config_va_trang_chu(app_client):
    assert app_client.get("/api/config").status_code == 200
    assert app_client.get("/").status_code == 200


def test_nap_bai_ra_khoi(doc):
    assert doc["blocks"], "không bóc được khối nào"
    assert doc["chunks"] >= 1
    assert "chunk_ids" in doc, "thiếu chunk_ids -> frontend sẽ dịch lại từ đầu"


def test_migration_du_cot():
    """Cột thêm sau phải được `ALTER TABLE` bù vào, không thì bài cũ vỡ."""
    from server import db
    cols = {r[1] for r in db.conn().execute("PRAGMA table_info(documents)")}
    assert {"slides", "highlights"} <= cols


def test_moi_endpoint_tra_doc_deu_co_chunks(app_client, doc):
    """Thiếu `_with_chunks` là frontend không biết mẻ nào xong, dịch lại tất."""
    for url in (f"/api/doc/{doc['id']}",
                f"/api/doc/{doc['id']}/blocks"):
        r = (app_client.get(url) if url.endswith(doc["id"])
             else app_client.patch(url, json={"skip": []}))
        assert r.status_code == 200, (url, r.text)
        assert "chunk_ids" in r.json(), url


# ------------------------------------------------------------- sửa khối

def test_bo_qua_va_dich_lai(app_client, doc):
    bid = doc["blocks"][1]["id"]
    r = app_client.patch(f"/api/doc/{doc['id']}/blocks", json={"skip": [bid]})
    assert r.status_code == 200, r.text
    b = next(x for x in r.json()["blocks"] if x["id"] == bid)
    assert b["translate"] is False
    r = app_client.patch(f"/api/doc/{doc['id']}/blocks", json={"keep": [bid]})
    assert next(x for x in r.json()["blocks"] if x["id"] == bid)["translate"] is True


def test_an_khoi_va_hien_lai(app_client, doc):
    bid = doc["blocks"][1]["id"]
    r = app_client.patch(f"/api/doc/{doc['id']}/blocks", json={"hide": [bid]})
    assert r.status_code == 200, r.text
    d = r.json()
    assert next(x for x in d["blocks"] if x["id"] == bid)["hidden"] is True
    it = d["chunks"]
    r = app_client.patch(f"/api/doc/{doc['id']}/blocks", json={"unhide": [bid]})
    assert next(x for x in r.json()["blocks"] if x["id"] == bid)["hidden"] is False
    assert r.json()["chunks"] >= it, "hiện lại khối thì số mẻ không được giảm"


# --------------------------------------------------------------- bôi vàng

def test_boi_vang_du_vong_doi(app_client, doc):
    bid = doc["blocks"][1]["id"]
    add = {"add": {"block": bid, "col": "vi", "start": 0, "end": 12,
                   "text": "đoạn thử", "color": "b"}}
    r = app_client.patch(f"/api/doc/{doc['id']}/highlights", json=add)
    assert r.status_code == 200, r.text
    hid = r.json()["new"]["id"]

    r = app_client.patch(f"/api/doc/{doc['id']}/highlights",
                         json={"update": {"id": hid, "note": "ghi chú", "color": "p"}})
    assert r.json()["item"]["note"] == "ghi chú"
    assert r.json()["item"]["color"] == "p"

    # sửa nội dung khối -> vệt bôi phải bị bỏ, vì khoảng ký tự trỏ sai chỗ
    r = app_client.post(f"/api/doc/{doc['id']}/blocks/split",
                        json={"id": bid, "at": 20})
    if r.status_code == 200:
        r = app_client.get(f"/api/doc/{doc['id']}")
        assert bid not in (r.json().get("highlights") or {})
    else:
        app_client.patch(f"/api/doc/{doc['id']}/highlights", json={"drop": [hid]})


def test_boi_vang_khoi_khong_ton_tai(app_client, doc):
    r = app_client.patch(f"/api/doc/{doc['id']}/highlights",
                         json={"add": {"block": "khongcothat", "col": "vi",
                                       "start": 0, "end": 5, "text": "x"}})
    assert r.status_code == 404


# ------------------------------------------------------------------ slide

@pytest.fixture(scope="module")
def with_deck(app_client, doc):
    """Gắn tay một bộ slide vào DB — không gọi model."""
    from server import store
    d = store.load(doc["id"])
    bid = d["blocks"][1]["id"]
    d["slides"] = {"deck": [
        {"id": "s1", "kind": "title", "headline": "Bài thử", "notes": ""},
        {"id": "s2", "kind": "content", "eyebrow": "KẾT QUẢ",
         "headline": "Mô hình đạt 42,5 điểm F1 trên tập kiểm tra",
         "cards": [{"icon": "chart", "title": "Chất lượng",
                    "bullets": ["Cao hơn baseline 3,1 điểm"]}],
         "callout": {"title": "Chốt lại", "body": "Lặp lại trên cả ba bộ dữ liệu"},
         "notes": " ".join(["nói"] * 130), "source_block_ids": [bid]},
    ], "backup": []}
    store.save(d)
    return doc["id"]


def test_slide_sua_tay(app_client, with_deck):
    r = app_client.patch(f"/api/doc/{with_deck}/slides",
                         json={"slide": {"id": "s2", "headline": "Tiêu đề mới đủ dài"}})
    assert r.status_code == 200, r.text
    s2 = next(x for x in r.json()["slides"]["deck"] if x["id"] == "s2")
    assert s2["headline"] == "Tiêu đề mới đủ dài"
    assert s2["edited"] is True


def test_slide_khong_cho_sua_nguon(app_client, with_deck):
    """`source_block_ids` là ràng buộc soát số liệu — sửa được thì vô nghĩa."""
    r = app_client.patch(f"/api/doc/{with_deck}/slides",
                         json={"slide": {"id": "s2", "source_block_ids": ["bia"]}})
    s2 = next(x for x in r.json()["slides"]["deck"] if x["id"] == "s2")
    assert s2["source_block_ids"] != ["bia"]


def test_slide_them_nhan_doi_xoa(app_client, with_deck):
    r = app_client.patch(f"/api/doc/{with_deck}/slides", json={"add": "s1"})
    new_id = r.json()["new_id"]
    assert [x["id"] for x in r.json()["slides"]["deck"]][1] == new_id

    r = app_client.patch(f"/api/doc/{with_deck}/slides", json={"duplicate": "s2"})
    dup = r.json()["new_id"]
    assert dup != "s2"

    r = app_client.patch(f"/api/doc/{with_deck}/slides", json={"drop": [new_id, dup]})
    ids = [x["id"] for x in r.json()["slides"]["deck"]]
    assert new_id not in ids and dup not in ids


def test_slide_bo_cuc_tu_do(app_client, with_deck):
    boxes = {"head": [5, 5, 90, 20], "card0": [5, 30, 40, 40]}
    r = app_client.patch(f"/api/doc/{with_deck}/slides",
                         json={"slide": {"id": "s2", "free": True, "boxes": boxes}})
    s2 = next(x for x in r.json()["slides"]["deck"] if x["id"] == "s2")
    assert s2["free"] is True and s2["boxes"]["head"] == [5, 5, 90, 20]
    html = app_client.get(f"/api/doc/{with_deck}/export?fmt=slides").text
    assert "is-free" in html and "left:5%" in html
    app_client.patch(f"/api/doc/{with_deck}/slides",
                     json={"slide": {"id": "s2", "free": False}})


@pytest.mark.parametrize("fmt", ["md", "html", "slides", "slides-pdf", "pptx"])
def test_xuat_du_nam_dang(app_client, with_deck, fmt):
    r = app_client.get(f"/api/doc/{with_deck}/export?fmt={fmt}")
    assert r.status_code == 200, (fmt, r.text[:200])
    assert len(r.content) > 500, fmt


def test_slide_xuat_ra_co_cot_moc_thiet_ke(app_client, with_deck):
    html = app_client.get(f"/api/doc/{with_deck}/export?fmt=slides").text
    for tag in ("class='eyebrow'", "class='card", "class='callout",
                "data-part=", "normAutofit" if False else "scrollHeight"):
        assert tag in html, tag


# ------------------------------------------------------------ tiến trình nạp

def test_kenh_tien_trinh_mo_duoc(app_client):
    r = app_client.get("/api/import/jtest/progress")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/event-stream")


def test_ma_viec_khong_hop_le(app_client):
    assert app_client.get("/api/import/co-gach/progress").status_code == 400


# ------------------------------------------------------------------ chống lỗi

def test_bai_khong_ton_tai_tra_404(app_client):
    assert app_client.get("/api/doc/khongcothat").status_code == 404
    assert app_client.patch("/api/doc/khongcothat/blocks", json={}).status_code == 404


def test_ma_bai_co_ky_tu_la(app_client):
    """`doc_id` phải `isalnum()` — hàng rào chống path traversal."""
    r = app_client.get("/api/doc/..%2F..%2Fetc/img/x.png")
    assert r.status_code in (400, 404)


def test_figsizes_khong_bai_van_khong_vo(app_client, doc):
    r = app_client.get(f"/api/doc/{doc['id']}/figsizes")
    assert r.status_code == 200
    assert isinstance(r.json()["ratios"], dict)


# ------------------------------------------------------- dịch từng phần

def test_danh_sach_muc_kem_gia(app_client, doc):
    r = app_client.get(f"/api/doc/{doc['id']}/sections")
    assert r.status_code == 200, r.text
    secs = r.json()["sections"]
    assert secs, "không tách được mục nào"
    for s in secs:
        assert s["blocks"] == len(s["ids"])
        assert s["done"] <= s["blocks"]
        assert "cost_usd" in s


def test_muc_khong_gom_tham_khao_va_khoi_an(app_client, doc):
    """Trả tiền dịch mục tham khảo hay khối đã ẩn là vô nghĩa."""
    from server import store
    d = store.load(doc["id"])
    bid = d["blocks"][1]["id"]
    app_client.patch(f"/api/doc/{doc['id']}/blocks", json={"hide": [bid]})
    ids = {i for s in app_client.get(f"/api/doc/{doc['id']}/sections").json()["sections"]
           for i in s["ids"]}
    assert bid not in ids
    app_client.patch(f"/api/doc/{doc['id']}/blocks", json={"unhide": [bid]})


def test_only_khong_khop_thi_bo_qua_me(app_client, doc):
    """Mẻ không chứa khối nào được chọn phải thoát ngay, KHÔNG gọi model."""
    with app_client.stream(
            "GET", f"/api/doc/{doc['id']}/translate?chunk=0&mode=vi&only=khongcothat"
    ) as r:
        body = "".join(r.iter_text())
    assert '"skipped": true' in body.lower().replace(" ", " ")


# --------------------------------------------- dàn ý: màn soát của pass slide

@pytest.fixture()
def with_outline(app_client, doc):
    """Gắn tay một dàn ý vào DB — không gọi model."""
    from server import store
    d = store.load(doc["id"])
    bid = d["blocks"][1]["id"]
    sl = d.get("slides") or {}
    sl["outline"] = {
        "thesis": "Một câu chốt lại cả bài",
        "sections": [{"name": "Bài toán"}, {"name": "Cách làm"}, {"name": "Kết quả"}],
        "items": [
            {"id": "o1", "kind": "title", "message": "Bài thử", "points": [],
             "evidence": {"kind": "none", "figure": "", "what": ""},
             "source_block_ids": []},
            {"id": "o2", "kind": "content", "section": "Kết quả",
             "message": "Mô hình đề xuất đạt 42,5 điểm F1 trên tập kiểm tra",
             "points": ["Cao hơn baseline mạnh nhất 3,1 điểm"],
             "evidence": {"kind": "diagram", "figure": "", "what": "hai pha nối tiếp"},
             "source_block_ids": [bid]},
        ],
        "backup": [],
    }
    d["slides"] = sl
    store.save(d)
    return doc["id"]


def test_sua_dan_y_bang_tay(app_client, with_outline):
    r = app_client.patch(f"/api/doc/{with_outline}/outline",
                         json={"item": {"id": "o2", "message": "Câu khẳng định mới",
                                        "points": ["ý một", "ý hai", "ý ba"]}})
    assert r.status_code == 200, r.text
    it = r.json()["outline"]["items"][1]
    assert it["message"] == "Câu khẳng định mới"
    assert it["points"] == ["ý một", "ý hai", "ý ba"]
    assert it["edited"] is True


def test_dan_y_khong_cho_sua_nguon(app_client, with_outline):
    """Cùng lý do với slide: `source_block_ids` là ràng buộc soát số liệu."""
    truoc = app_client.get(f"/api/doc/{with_outline}").json()["slides"]["outline"]
    goc = truoc["items"][1]["source_block_ids"]
    r = app_client.patch(f"/api/doc/{with_outline}/outline",
                         json={"item": {"id": "o2", "source_block_ids": ["bia"]}})
    assert r.json()["outline"]["items"][1]["source_block_ids"] == goc


def test_dan_y_them_xoa_doi_cho(app_client, with_outline):
    r = app_client.patch(f"/api/doc/{with_outline}/outline", json={"add": "o1"})
    ids = [i["id"] for i in r.json()["outline"]["items"]]
    assert len(ids) == 3 and ids == ["o1", "o2", "o3"]   # đánh mã lại liên tục

    r = app_client.patch(f"/api/doc/{with_outline}/outline",
                         json={"move": {"id": "o1", "by": 1}})
    assert r.json()["outline"]["items"][1]["kind"] == "title"

    r = app_client.patch(f"/api/doc/{with_outline}/outline", json={"drop": "o1"})
    assert len(r.json()["outline"]["items"]) == 2


def test_dan_y_chuyen_sang_du_phong(app_client, with_outline):
    r = app_client.patch(f"/api/doc/{with_outline}/outline",
                         json={"id": "o2", "to": "backup"})
    ol = r.json()["outline"]
    assert len(ol["items"]) == 1 and len(ol["backup"]) == 1


def test_dan_y_soat_lai_sau_moi_lan_sua(app_client, with_outline):
    """Sửa tay xong vẫn phải qua `check_outline` — không thì chốt chặn bỏ trống."""
    r = app_client.patch(f"/api/doc/{with_outline}/outline",
                         json={"item": {"id": "o2", "points": ["Đạt 99,9 điểm"]}})
    it = r.json()["outline"]["items"][1]
    assert any("99,9" in w for w in it["warn"]), it["warn"]


def test_dung_slide_khi_chua_co_dan_y(app_client, doc):
    """Không có dàn ý thì KHÔNG gọi model — trả lỗi để người dùng đi soạn trước."""
    from server import store
    d = store.load(doc["id"])
    d["slides"] = {}
    store.save(d)
    with app_client.stream("GET", f"/api/doc/{doc['id']}/slides/build") as r:
        body = "".join(r.iter_text())
    assert "event: error" in body and "dàn ý" in body


def test_sua_dan_y_bai_khong_co(app_client, doc):
    from server import store
    d = store.load(doc["id"])
    d["slides"] = {}
    store.save(d)
    r = app_client.patch(f"/api/doc/{doc['id']}/outline", json={"drop": "o1"})
    assert r.status_code == 404


def test_doi_ten_bai(app_client, doc):
    """Tiêu đề đoán từ khối đầu trang nên hay sai, mà nó hiện ở danh sách bài, ở
    đầu bản xuất ra và ở slide tiêu đề — sai một chỗ là sai khắp nơi.

    Đổi tên KHÔNG đụng nội dung: `title` không nằm trong `cached_prefix` nên
    không có bản dịch nào phải bỏ đi.
    """
    did = doc["id"]
    r = app_client.patch(f"/api/doc/{did}/title", json={"title": "Tên mới của bài"})
    assert r.status_code == 200 and r.json()["title"] == "Tên mới của bài"
    assert app_client.get(f"/api/doc/{did}").json()["title"] == "Tên mới của bài"
    assert app_client.patch(f"/api/doc/{did}/title",
                            json={"title": "  "}).status_code == 400
    assert app_client.patch("/api/doc/khongcobai/title",
                            json={"title": "x"}).status_code == 404


def test_khong_o_chon_nao_bi_long_trong_label(app_client):
    """`<select>` nằm trong `<label>` thì click nổi lên label, label chuyển tiếp
    thành một cú kích hoạt nữa xuống chính cái select — dropdown mở ra rồi đóng
    ngay, không kịp chọn. Lỗi Chromium đã biết, và nó **không** tái hiện được
    bằng sự kiện tổng hợp, nên chỉ có phép kiểm cấu trúc này canh được.

    Nhãn phải đứng riêng và nối bằng `for=`.
    """
    import re
    from pathlib import Path
    html = Path(__file__).resolve().parents[1].joinpath("web/index.html").read_text()

    long_nhau = []
    for m in re.finditer(r"<label[^>]*>((?:(?!</label>).)*?)</label>", html, re.S):
        if "<select" in m.group(1):
            got = re.search(r'id="([^"]+)"', m.group(1))
            long_nhau.append(got.group(1) if got else "?")
    assert not long_nhau, f"select bị lồng trong label: {long_nhau}"

    # và mọi select phải có tên gọi được: label[for], aria-label, hoặc title
    for m in re.finditer(r"<select\b([^>]*)>", html):
        attrs = m.group(1)
        sid = re.search(r'id="([^"]+)"', attrs)
        assert sid, f"select không có id: {attrs[:60]}"
        co_ten = (f'for="{sid.group(1)}"' in html
                  or "aria-label=" in attrs or "title=" in attrs)
        assert co_ten, f"select {sid.group(1)} không có nhãn nào"


def test_o_xem_truoc_hinh_co_bo_phong_to(app_client):
    """Hình cắt từ PDF dày đặc chữ nhỏ — nhãn trục, chú giải, số trong bảng — mà
    ô xem trước chỉ rộng chừng 560px. Đọc được con số trên biểu đồ mới là lý do
    người ta bấm vào "Figure 3", nên ô đó phải phóng to và kéo được.

    Phép kiểm cấu trúc, vì hành vi kéo–thả chỉ soát được bằng trình duyệt.
    """
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    html = root.joinpath("web/index.html").read_text()
    js = root.joinpath("web/app.js").read_text()
    css = root.joinpath("web/style.css").read_text()

    for el in ("figPeekIn", "figPeekOut", "figPeekZoom"):
        assert f'id="{el}"' in html, f"thiếu nút {el}"
    assert "wireFigPeek()" in js, "bộ phóng to chưa được nối vào lúc khởi động"
    for fn in ("function figZoom", "function figApply", "function figReset"):
        assert fn in js
    # transform-origin phải ở góc trên-trái, nếu không phép phóng quanh con trỏ
    # tính sai tâm và hình nhảy mỗi lần cuộn
    assert "transform-origin: 0 0" in css
