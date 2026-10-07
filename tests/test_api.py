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
    # Bỏ comment TRƯỚC khi quét: comment không phải markup, mà chính chỗ giải
    # thích luật này lại nhắc tới thẻ `<select>` nên tự làm phép kiểm báo sai.
    html = re.sub(r"<!--.*?-->", "", html, flags=re.S)

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


def test_boc_lai_va_duoc_tieu_de_bi_cut_dau(app_client, doc):
    """Tiêu đề đoán từ khối đầu trang hay mất dòng đầu — bài GCR lưu thành
    "Question Answering", đúng là đuôi của "Ground, Cover, and Refine: … for
    Long-Video Question Answering". Bóc lại vốn sửa được nhưng lại vứt tiêu đề
    mới đi, nên bài mang tên sai vĩnh viễn.

    Chỉ vá ca CỤT ĐUÔI, không đụng tên người dùng tự đặt.
    """
    from server import main
    cur, new = "Question Answering", ("Ground, Cover, and Refine: Evidence-Centric "
                                      "Frame Selection for Long-Video Question Answering")

    def vá(cu, moi):
        cu, moi = cu.strip(), moi.strip()
        return (moi if moi and cu and moi != cu and len(moi) > len(cu)
                and cu.lower() in moi.lower() else cu)

    assert vá(cur, new) == new                       # cụt đuôi → vá
    assert vá("Tên tôi tự đặt", new) == "Tên tôi tự đặt"   # không dính gì → giữ
    assert vá(new, cur) == new                       # bản mới ngắn hơn → giữ
    assert vá(new, new) == new

    # và route thật phải mang cùng luật đó
    src = __import__("pathlib").Path(main.__file__).read_text()
    assert "title_fixed" in src and "cur.lower() in nt.lower()" in src


def test_boc_lai_noi_ra_khi_roi_ve_duong_lui(app_client):
    """Trước đây chỗ này rơi về heuristic **im lặng**: bấm Bóc lại, thấy "xong",
    mà kết quả kém hẳn — công thức không được cắt thành ảnh nên hiện ra bằng chữ
    toán vỡ nát — và không có dấu hiệu nào để đoán ra.

    Đo trên bài SONIC: 8 công thức kèm ảnh tụt còn 3 và không ảnh nào.
    """
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    src = root.joinpath("server/main.py").read_text()
    js = root.joinpath("web/app.js").read_text()

    assert 'stats["layout_used"]' in src, "route không nói ra đã dùng đường nào"
    assert "fallback_why" in src, "route không nói ra vì sao rơi về đường lùi"
    assert "st.layout_used === false" in js, "giao diện không hiện cảnh báo"


def test_ma_nguon_duoc_mount_de_khoi_dung_lai_anh(app_client):
    """Mỗi lần sửa giao diện mà phải dựng lại ảnh Docker thì rất dễ quên, và
    quên là thấy y hệt bản cũ — đã mất cả buổi vì đúng chuyện đó."""
    from pathlib import Path
    yml = Path(__file__).resolve().parents[1].joinpath("docker-compose.yml").read_text()
    assert "./web:/app/web:ro" in yml
    assert "./server:/app/server:ro" in yml
    assert "./data:/data" in yml          # dữ liệu vẫn phải nằm ngoài ảnh


def test_chon_chu_bi_khoa_theo_cot(app_client):
    """Lưới song ngữ xếp theo hàng nên thứ tự DOM là `en, vi, gl, en, vi, gl…`.
    Trình duyệt quét vùng chọn theo thứ tự đó chứ không theo cột nhìn thấy, nên
    kéo xuống vài hàng trong một cột là vơ luôn hai cột kia — copy ra thành ba
    thứ tiếng trộn nhau.

    Không có thuộc tính CSS nào giới hạn vùng chọn theo cột; cách duy nhất là
    `user-select: none` ở hai cột kia, đặt ngay lúc `mousedown` — đặt ở `mouseup`
    thì vùng chọn đã lớn xong rồi.
    """
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    js = root.joinpath("web/app.js").read_text()
    css = root.joinpath("web/style.css").read_text()

    assert "function lockSelectionColumn" in js
    assert 'doc.addEventListener("mousedown", (e) => lockSelectionColumn(e.target));' in js
    for rule in (".doc.sel-en .vi", ".doc.sel-vi .en", ".doc.sel-gl .en"):
        assert rule in css, f"thiếu luật {rule}"
    # cả khối luật phải kết thúc bằng `user-select: none`
    i = css.index(".doc.sel-en")
    assert "user-select: none" in css[i:css.index("}", i) + 1]


def test_hoi_dap_trong_bai_khong_vo_vi_pham_vi_bien(app_client):
    """`Uncaught ReferenceError: answer is not defined` — cả phần Hỏi về bài này
    chết ngay khung hình đầu tiên.

    `paint()` được định nghĩa ngoài khối `try`, còn `answer` khai bằng `let` bên
    TRONG khối đó: hai phạm vi khác nhau. Mỗi lần `requestAnimationFrame(paint)`
    chạy là ném lỗi, và người dùng chỉ thấy "Lỗi: answer is not defined".
    """
    import re
    from pathlib import Path
    js = Path(__file__).resolve().parents[1].joinpath("web/app.js").read_text()
    i = js.index("async function sendQuestion()")
    body = js[i:i + 2600]

    # `answer` phải khai TRƯỚC `paint`, cùng phạm vi hàm
    khai = body.index("let answer")
    dung = body.index("renderMd(answer)")
    assert khai < dung, "`answer` khai sau chỗ dùng"
    # và không được khai lại bên trong khối try
    assert not re.search(r"let\s+buf\s*=\s*\"\"\s*,\s*answer", body), \
        "`answer` lại bị khai trong khối try"


def test_hai_bo_dung_markdown_deu_dung_bang_va_chi_so():
    """Cột trả lời có HAI bộ dựng Markdown khác nhau — `renderMd()` bên
    `app.js` cho khung hỏi-về-bài-này, `svMd()` bên `survey.js` cho hỏi đáp
    trên kho. Bên kho dựng bảng đúng từ đầu, bên đọc thì **sót**: câu trả lời
    so sánh nhiều bài hiện ra nguyên dấu gạch đứng và hàng `|---|---|`, mà
    prompt lại bảo model dùng bảng khi so từ ba nguồn trở lên.

    Và `_SUBSCRIPTISH` khai ở `app.js` nhưng `survey.js` **dùng nhờ**. Xoá bên
    này thì bên kia ném `ReferenceError` lúc dựng — tức mọi câu trả lời trong
    kho thành ô trắng, không lỗi nào hiện lên màn. Đúng loại hỏng chỉ phép kiểm
    cấu trúc mới giữ được.
    """
    from pathlib import Path
    web = Path(__file__).resolve().parents[1] / "web"
    app_js = (web / "app.js").read_text()
    survey_js = (web / "survey.js").read_text()
    css = (web / "style.css").read_text()

    assert "const _SUBSCRIPTISH" in app_js, "app.js mất khai báo _SUBSCRIPTISH"
    assert "_SUBSCRIPTISH" in survey_js, "survey.js thôi dùng chung — kiểm lại"

    # cả hai phải có nhánh bảng, và bảng phải nằm trong khung cuộn ngang riêng
    assert 'class="mdtable"' in app_js, "renderMd() thiếu nhánh bảng"
    assert 'class="sv-tablewrap"' in survey_js, "svMd() thiếu nhánh bảng"
    assert ".mdtable { overflow-x: auto" in css, "bảng thiếu khung cuộn ngang"

    # và cả hai phải dựng dạng lưu ^{…} / _{…}
    for ten, src in (("app.js", app_js), ("survey.js", survey_js)):
        assert "<sup>$1</sup>" in src and "<sub>$1</sub>" in src, \
            f"{ten} không dựng ^{{…}} / _{{…}}"


@pytest.fixture(scope="module")
def doc_pdf(app_client):
    """Một bài nạp từ PDF dựng tại chỗ — cần cho đường cắt lại ảnh."""
    import io
    fitz = pytest.importorskip("fitz")
    d = fitz.open()
    page = d.new_page()
    page.insert_text((72, 100), "Tieu de bai thu", fontsize=18)
    page.insert_text((72, 140), "Figure 1: so do khoi cua he thong", fontsize=10)
    page.insert_text((72, 200), "Doan van than bai de bo boc co viec ma lam.", fontsize=11)
    page.draw_rect(fitz.Rect(72, 230, 400, 380), color=(0, 0, 0), fill=(0.8, 0.8, 0.9))
    buf = io.BytesIO(d.tobytes())
    d.close()
    r = app_client.post("/api/import",
                        files={"file": ("thu.pdf", buf.getvalue(), "application/pdf")},
                        data={"model": "test/model"})
    assert r.status_code == 200, r.text
    return r.json()


def test_cat_lai_anh_giu_nguyen_khoi_va_ban_dich(app_client, doc_pdf):
    """`POST …/recrop` vẽ lại pixel, KHÔNG dựng lại khối.

    Ảnh cắt trước bản `parser.dpi_for` dùng DPI cứng, nên khung hẹp chỉ ra vài
    trăm pixel — phóng lên là mờ nhoè, mà đọc được con số trên biểu đồ mới đúng
    là lý do người ta phóng. `parse_cache` khoá theo SHA của file PDF nên nạp lại
    cùng file **không** cắt lại; trước bản này không có đường nào chữa ngoài bóc
    lại cả bài, mà bóc lại thì mang theo rủi ro rơi về đường lùi heuristic.

    Đo trên bài CIRAG thật: bề ngang trung bình 514 → 1120px, cả 29 ảnh nét hơn.
    """
    did = doc_pdf["id"]
    blk = doc_pdf["blocks"][0]["id"]

    # dựng một ảnh bằng đường cắt tay, để bài chắc chắn có khung đã lưu
    r = app_client.post(f"/api/doc/{did}/crop/{blk}",
                        json={"page": 0, "rect": [72, 225, 400, 385]})
    assert r.status_code == 200, r.text

    truoc = app_client.get(f"/api/doc/{did}").json()
    r = app_client.post(f"/api/doc/{did}/recrop")
    assert r.status_code == 200, r.text
    st = r.json()["stats"]
    assert st["images"] >= 1
    assert not st["failed"]
    assert st["px_after"] >= st["px_before"]

    # khối không được đổi một chữ, và mọi khối có hình phải có file thật
    sau = r.json()["doc"]
    assert [b["id"] for b in sau["blocks"]] == [b["id"] for b in truoc["blocks"]]
    assert [b["text"] for b in sau["blocks"]] == [b["text"] for b in truoc["blocks"]]
    assert sau["translations"] == truoc["translations"]
    for b in sau["blocks"]:
        if b.get("figure"):
            assert b["figure"] == b["id"], "figure phải trỏ đúng mã khối"
            assert app_client.get(f"/api/doc/{did}/img/{b['figure']}.png").status_code == 200
    # và `_with_chunks` phải chạy, nếu không frontend dịch lại từ đầu
    assert "chunk_ids" in sau


def test_cat_lai_anh_bao_loi_ro_khi_khong_the(app_client, doc):
    """Bài dán từ văn bản thì không có PDF gốc, và phải nói ra chứ không im lặng."""
    r = app_client.post(f"/api/doc/{doc['id']}/recrop")
    assert r.status_code == 400
    assert "PDF" in r.json()["detail"]
    assert app_client.post("/api/doc/khongcobai/recrop").status_code == 404


def test_vach_keo_khung_pdf_dat_be_rong_qua_bien():
    """Bề rộng khung PDF phải đi qua biến `--pdf-w`, **không** đặt inline.

    Ở màn hẹp `.pdfpane` chuyển sang `position: fixed; width: auto` để phủ kín
    màn hình. Luật trong media query thắng được luật `width: var(--pdf-w, 40%)`
    ở khối gốc, nhưng **không** thắng được `style="width:…"` đặt inline — đặt
    inline thì khung PDF tràn màn hình ở mọi máy hẹp.

    Và vách kéo phải nằm TRONG `#pdfPane`: khung này bật/tắt bằng lớp `hidden`,
    để vách ra ngoài thì phải nhớ ẩn cả hai chỗ.
    """
    import re
    from pathlib import Path
    web = Path(__file__).resolve().parents[1] / "web"
    app_js = (web / "app.js").read_text()
    css = (web / "style.css").read_text()
    html = (web / "index.html").read_text()

    assert "--pdf-w" in css and "var(--pdf-w" in css, "CSS không dùng biến --pdf-w"
    assert not re.search(r'#pdfPane"\)\.style\.width', app_js), \
        "đặt width inline: media query màn hẹp không thắng được"
    assert ".pane-grip { display: none; }" in css, "màn hẹp phải ẩn vách kéo"

    pane = re.search(r'<aside id="pdfPane".*?</aside>', html, re.S)
    assert pane, "không thấy #pdfPane"
    assert 'id="pdfGrip"' in pane.group(0), "vách kéo phải nằm trong #pdfPane"
    # kéo được bằng bàn phím, không chỉ bằng chuột
    for thuoc in ('role="separator"', "tabindex=", "aria-label="):
        assert thuoc in pane.group(0), f"vách kéo thiếu {thuoc}"
    assert "ArrowLeft" in app_js and "ArrowRight" in app_js, "vách kéo thiếu đường bàn phím"


def test_dich_lai_mot_khoi_bao_loi_ro(app_client, doc):
    """Đường dịch lại một khối phải nói rõ khi không làm được.

    Tồn tại vì cảnh báo rò hệ chữ của `stream_chunk` đã bảo người dùng *"dịch
    lại khối đó"* mà trước bản này không có đường nào làm việc ấy — chỉ còn cách
    gõ tay cả đoạn hoặc dịch lại cả mẻ. Gặp thật trên bài CIRAG: khối `b41` ở
    **cột giải thích** ra `либо thiếu thông tin để suy luận, либо nhận quá nhiều
    nhiễu`, chữ Cyrillic thay cho "hoặc".

    Không gọi model ở đây (test phải miễn phí), chỉ soát các nhánh chặn.
    """
    did = doc["id"]
    assert app_client.post(f"/api/doc/{did}/retranslate/khongcokhoi").status_code == 404
    assert app_client.post("/api/doc/khongcobai/retranslate/b1").status_code == 404
    r = app_client.post(f"/api/doc/{did}/retranslate/b1", json={"mode": "xyz"})
    assert r.status_code == 400 and "mode" in r.json()["detail"]


def test_tm_drop_bo_duoc_ban_dich_cu():
    """`db.tm_drop` phải bỏ hẳn mục cũ, nếu không lượt dịch lại nhận đúng cái rác.

    Đây là chỗ dễ bỏ sót nhất của đường dịch lại: bộ nhớ dịch trả về trước khi
    model được gọi, nên không bỏ thì người dùng trả tiền cho một lượt trả lại y
    nguyên bản họ vừa bấm để thay.
    """
    from server import db
    src, mo = "Mot doan van de thu bo nho dich.", "test/model"
    db.tm_put([(src, "Bản dịch rác", "")], mo)
    assert db.tm_get([src], mo).get(src, {}).get("vi") == "Bản dịch rác"
    assert db.tm_drop([src], mo) == 1
    assert not db.tm_get([src], mo)
    assert db.tm_drop([src], mo) == 0      # bỏ lần hai không nổ
    assert db.tm_drop([], mo) == 0


def test_chot_chan_ro_he_chu_khong_bat_oan_ky_hieu_toan():
    """`script_leak` phải cho qua ký hiệu toán, và vẫn bắt hệ chữ lạ.

    Bắt oan ở đây tốn tiền thật: bản dịch sạch bị giữ ngoài `tm` nên mọi bài sau
    có đoạn y hệt đều phải dịch lại. Đã bắt oan `⟨⟩` (U+27E8/27E9) — ngoặc nhọn
    toán học dùng cho tích trong và dãy — trên bài SONIC.
    """
    from server.pipeline import script_leak
    assert not script_leak("tích trong ⟨a, b⟩, tổng ⨁, và ⩽ với ⟦x⟧", "nguon")
    assert not script_leak("Đoạn này mô tả ưu, nhược điểm ữ ộ ế của α và β", "nguon")
    # nhưng hệ chữ lạ thì vẫn phải bắt, kể cả hệ chưa ai gặp
    assert script_leak("hệ thống либо thiếu thông tin", "he thong thieu")
    assert script_leak("띠ᥕᥕᥲᥕᥱ", "bao toan")
    # so với bản gốc: bài trích tiếng Trung thật thì không bị bắt
    assert not script_leak("nguyên văn 深度学习 giữ lại", "trich 深度学习 trong bai")


def _tex_table_js(ten: str) -> dict:
    """Bóc một bảng macro trong `web/app.js` ra dict, để đối chiếu với bản Python."""
    import re
    from pathlib import Path
    js = (Path(__file__).resolve().parents[1] / "web/app.js").read_text()
    khoi = re.search(rf"const {ten} = \{{(.*?)\n?\}};", js, re.S)
    assert khoi, f"không thấy bảng {ten} trong app.js"
    out = {}
    for m in re.finditer(r'(?:"([^"]+)"|([A-Za-z]+))\s*:\s*"((?:[^"\\]|\\.)*)"',
                         khoi.group(1)):
        # Chỉ giải mã đúng dạng `\uXXXX`: `unicode_escape` trên cả chuỗi sẽ đọc
        # ký tự UTF-8 như latin-1 và biến "α" thành "Î±".
        out[m.group(1) or m.group(2)] = re.sub(
            r"\\u([0-9a-fA-F]{4})", lambda h: chr(int(h.group(1), 16)), m.group(3))
    return out


def test_bang_macro_tex_khop_nhau_giua_app_va_export():
    """`TEX` bên `app.js` và `_TEX` bên `main.py` phải khớp từng khoá.

    Bản đang đọc trên màn hình và file xuất ra là **hai đoạn code dựng cùng một
    nội dung** — cùng họ với cặp `renderSlide()` / `_export_slides_html` và cặp
    `renderMd()` / `svMd()`. Lệch một khoá thì công thức trong file xuất ra khác
    công thức trên màn hình, mà chỉ lộ ra lúc người dùng mở file đã tải về.
    """
    from server import main
    for ten_js, bang_py in (("TEX", main._TEX), ("TEX_ACCENT", main._TEX_ACCENT)):
        bang_js = _tex_table_js(ten_js)
        thieu = set(bang_js) - set(bang_py)
        thua = set(bang_py) - set(bang_js)
        assert not thieu and not thua, (
            f"{ten_js}: app.js có thêm {sorted(thieu)}, main.py có thêm {sorted(thua)}")
        lech = {k: (bang_js[k], bang_py[k]) for k in bang_js if bang_js[k] != bang_py[k]}
        assert not lech, f"{ten_js} lệch giá trị: {lech}"


def test_dung_latex_noi_dong_thanh_ky_hieu_that():
    """`\\(…\\)` phải thành ký hiệu thật, không hiện thô giữa câu tiếng Việt.

    Dạng lưu của công cụ là `^{…}` / `_{…}`, nhưng model vẫn viết LaTeX. Đo trên
    dữ liệu thật: 13 ô, toàn dạng `\\(…\\)` (không có `$…$`, không `\\[`), với
    các macro `\\in \\tau \\tilde \\hat \\cdot \\theta \\star \\rightarrow`.
    Người đọc thấy nguyên `\\(Suf(a) \\in \\{0, 1\\}\\)` giữa câu.
    """
    from server.main import _math_tex

    assert _math_tex(r"Suf(a) \in \{0, 1\}") == "Suf(a) ∈ {0, 1}"
    assert _math_tex(r"g \in G") == "g ∈ G"
    assert _math_tex(r"\pi_T(\cdot \mid x, I)") == "π<sub>T</sub>(· | x, I)"
    assert _math_tex(r"\tilde{T}_t \rightarrow \hat{x}") == "T̃<sub>t</sub> → x̂"
    assert _math_tex(r"\frac{\alpha}{\beta}") == "(α)/(β)"
    assert _math_tex(r"\text{Suf}(a)") == "Suf(a)"

    # Chỉ số LỒNG: `[^{}]*` chỉ khớp lớp trong cùng nên phải lặp từ trong ra
    # ngoài. Làm một lượt thì `_{DOC}` bị ăn trước và ngoặc ngoài còn nguyên.
    assert _math_tex(r"a^{(g_{DOC})}") == "a<sup>(g<sub>DOC</sub>)</sup>"
    assert _math_tex(r"a^{(g^\star)}") == "a<sup>(g<sup>⋆</sup>)</sup>"

    # macro lạ thì để nguyên, không được ăn mất chữ
    assert "\\khongcomacronay" in _math_tex(r"\khongcomacronay x")


def test_ghi_nhieu_vet_boi_mot_luot(app_client, doc):
    """`add_many` ghi cả loạt vệt bôi trong một lượt, và bỏ qua thứ không hợp lệ.

    Pass đánh dấu câu đáng nhớ trả về tới 18 vệt. Gọi đường `add` một-vệt-một-
    request là 18 lượt `store.save(doc)` ghi lại nguyên cả tài liệu.
    """
    did = doc["id"]
    bid = doc["blocks"][0]["id"]
    r = app_client.patch(f"/api/doc/{did}/highlights", json={"add_many": [
        {"block": bid, "col": "vi", "color": "v", "start": 0, "end": 5,
         "text": "abcde", "note": "Luận điểm chính — vì sao"},
        {"block": bid, "col": "vi", "color": "g", "start": 6, "end": 9, "text": "fgh"},
        {"block": "khongcokhoi", "col": "vi", "start": 0, "end": 3},   # khối lạ
        {"block": bid, "col": "vi", "start": 9, "end": 9},             # khoảng rỗng
        {"block": bid, "col": "vi", "color": "khongcomau", "start": 20, "end": 25},
    ]})
    assert r.status_code == 200, r.text
    them = r.json()["added"]
    assert len(them) == 3, "phải bỏ khối lạ và khoảng rỗng, giữ 3 vệt"
    assert [h["id"] for h in them] == ["h1", "h2", "h3"], "mã vệt phải không trùng"
    assert them[0]["color"] == "v" and them[0]["note"].startswith("Luận điểm")
    assert them[2]["color"] == "y", "màu lạ phải rơi về mặc định"

    # ghi tiếp lượt hai: mã mới không được đụng mã cũ
    r2 = app_client.patch(f"/api/doc/{did}/highlights", json={"add_many": [
        {"block": bid, "col": "en", "start": 30, "end": 40, "text": "x"}]})
    assert r2.status_code == 200
    tat_ca = [h["id"] for lst in r2.json()["highlights"].values() for h in lst]
    assert len(tat_ca) == len(set(tat_ca)) == 4


def test_danh_dau_cau_dang_nho_bao_loi_ro(app_client, doc):
    """Bài chưa dịch thì nói thẳng, chứ đừng gọi model rồi trả về rỗng."""
    r = app_client.post(f"/api/doc/{doc['id']}/insights")
    assert r.status_code == 400
    assert "dịch" in r.json()["detail"]
    assert app_client.post("/api/doc/khongcobai/insights").status_code == 404


def test_danh_dau_lai_thay_cho_cu_nhung_giu_vet_nguoi_dung(app_client, doc):
    """Bấm nút đánh dấu lần hai phải THAY chỗ cũ, không cộng dồn.

    Đã thấy thật khi soát bằng trình duyệt: chạy hai lượt thì đúng câu đầu bài
    hiện ra hai lần. Nhưng chỉ được dọn vệt do MÁY đặt — vệt người dùng tự tô là
    công sức của họ, cùng lý do `mark_stale` chỉ gắn cờ chứ không xoá slide đã
    sửa tay.
    """
    did = doc["id"]
    bid = doc["blocks"][0]["id"]
    # Fixture `doc` dùng chung cả module nên đã có vệt của test khác — so theo
    # MỐC chứ không so số tuyệt đối.
    def dem():
        h = app_client.get(f"/api/doc/{did}").json().get("highlights") or {}
        v = [x for lst in h.values() for x in lst]
        return sum(1 for x in v if x.get("auto")), sum(1 for x in v if not x.get("auto"))

    _, tay_truoc = dem()
    r = app_client.patch(f"/api/doc/{did}/highlights", json={"add": {
        "block": bid, "col": "vi", "start": 0, "end": 4, "text": "tay"}})
    assert r.status_code == 200
    tay = r.json()["new"]["id"]

    # lượt máy đánh dấu thứ nhất
    app_client.patch(f"/api/doc/{did}/highlights", json={"replace_auto": True, "add_many": [
        {"block": bid, "col": "vi", "start": 10, "end": 15, "auto": True},
        {"block": bid, "col": "vi", "start": 20, "end": 25, "auto": True}]})
    # lượt thứ hai
    r2 = app_client.patch(f"/api/doc/{did}/highlights", json={"replace_auto": True, "add_many": [
        {"block": bid, "col": "vi", "start": 30, "end": 35, "auto": True}]})
    assert r2.status_code == 200

    tat_ca = [h for lst in r2.json()["highlights"].values() for h in lst]
    may, tay_sau = dem()
    assert may == 1, f"vệt máy bị cộng dồn: {may}"
    assert tay_sau == tay_truoc + 1, "vệt người dùng tự tô bị đụng tới"
    assert any(h["id"] == tay and not h.get("auto") for h in tat_ca), "mất vệt vừa tô tay"


def test_vot_lai_marks_khi_dau_ra_bi_cat_cut():
    """JSON cắt cụt thì vớt những mục đã trọn vẹn, đừng vứt cả lượt gọi.

    Pass đánh dấu trả về một DANH SÁCH, nên mất phần đuôi chỉ là ít vệt hơn —
    khác hẳn pass trả về một object phải nguyên vẹn. Đã cắt cụt thật: bài 162
    đoạn ở mức vừa (40 vệt) vượt trần 6.000 token sau 94 giây, và toàn bộ lượt
    gọi đã trả tiền mất trắng.
    """
    from server.pipeline import _vot_marks

    cut = ('{"marks":[{"block":"b1","kind":"claim","quote":"câu một","why":"vì"},'
           '{"block":"b2","kind":"term","quote":"câu hai","why":"vì"},'
           '{"block":"b3","kind":"limit","quote":"câu ba bị cắt')
    m = _vot_marks(cut)
    assert [x["quote"] for x in m] == ["câu một", "câu hai"]

    # mục thiếu `quote` hoặc `block` thì không vớt — vớt vào cũng bị chốt chặn loại
    assert _vot_marks('{"marks":[{"kind":"claim","why":"vì"}]}') == []
    assert _vot_marks("hoàn toàn không phải JSON") == []

    # và câu trích có LaTeX vẫn vớt được (cùng lý do với `llm._va_escape`)
    m2 = _vot_marks(r'{"marks":[{"block":"b1","kind":"term","quote":"\(x\)","why":"v"}]}')
    assert m2 and m2[0]["quote"] == r"\(x\)"


@pytest.mark.parametrize("ten,noi_dung,ma,phan", [
    ("paper.docx", b"PK\x03\x04" + b"x" * 200, 400, "Office"),
    ("empty.txt", b"", 400, "rỗng"),
    ("fake.pdf", b"day khong phai pdf", 400, "đuôi .pdf"),
])
def test_nap_bai_tu_choi_nguon_hong_bang_4xx_co_noi_dung(app_client, ten, noi_dung, ma, phan):
    """Nguồn hỏng phải ra **4xx kèm JSON đọc được**, không phải 500 kèm trang HTML.

    Frontend gọi `res.json()` lên một trang 500 thì ném `Unexpected token 'I',
    "Internal S"… is not valid JSON`, và chính câu tiếng Anh đó bị in ra màn hình
    thay cho lỗi thật — người dùng không có cách nào hiểu phải làm gì.
    """
    r = app_client.post("/api/import",
                        files={"file": (ten, noi_dung, "application/octet-stream")},
                        data={"model": "test/model"})
    assert r.status_code == ma, r.text
    assert r.headers["content-type"].startswith("application/json")
    assert phan in r.json()["detail"]


def test_chuoi_rac_o_tab_arxiv_khong_thanh_500(app_client):
    """Gõ chữ bừa vào ô link phải ra 400 kèm ví dụ hợp lệ, không phải 500."""
    r = app_client.post("/api/import",
                        data={"url": "hello world not a link", "model": "test/model"})
    assert r.status_code == 400
    assert "1706.03762" in r.json()["detail"]


def test_client_doc_loi_an_toan_tu_response_hong():
    """`apiErr` phải tồn tại và KHÔNG chỗ nào còn bóc `.detail` thẳng tay.

    `(await r.json()).detail` là cái bẫy: 500 trả về HTML, `json()` ném, và câu
    ném đó thành thông báo cho người dùng. Đếm được 29 chỗ như vậy trước bản này.
    """
    from pathlib import Path
    web = Path(__file__).resolve().parents[1] / "web"
    app_js = (web / "app.js").read_text()
    assert "async function apiErr(" in app_js
    for ten in ("app.js", "survey.js"):
        src = (web / ten).read_text()
        assert "json()).detail" not in src, f"{ten} còn bóc .detail thẳng tay"


def test_moi_khoi_theme_khai_du_nam_mau_boi():
    """Năm màu bôi vàng phải khai ở **mọi** khối theme, kể cả skin mới.

    Đây là cái bẫy đã vấp thật: `--c-y…--c-v` khai ở nhiều chỗ, sửa mỗi `:root`
    thì bật theme sáng tường minh là màu cũ quay lại — đo trong trình duyệt vẫn
    ra `#fff3a3` sau khi đã "sửa". Thêm skin "Báo" làm số khối từ bốn lên năm,
    nên phép kiểm này phải đếm theo thực tế chứ không chốt con số.
    """
    import re
    from pathlib import Path
    css = (Path(__file__).resolve().parents[1] / "web/style.css").read_text()

    # mỗi khối khai theme = một chỗ đặt `--bg`
    khoi = re.findall(r"(:root(?:\[data-theme=\"[a-z]+\"\])?)\s*\{([^}]*--bg:[^}]*)\}", css)
    assert len(khoi) >= 4, f"chỉ thấy {len(khoi)} khối theme — phép kiểm đã hỏng"

    for ten, than in khoi:
        thieu = [c for c in ("--c-y", "--c-g", "--c-b", "--c-p", "--c-v")
                 if f"{c}:" not in than.replace(" ", "")]
        assert not thieu, f"{ten} thiếu màu bôi: {thieu}"
        # và phải có đủ cặp màu đậm đi kèm (dùng cho viền/chữ trên vệt)
        thieu2 = [c for c in ("--c-yl", "--c-gl", "--c-bl", "--c-pl", "--c-vl")
                  if f"{c}:" not in than.replace(" ", "")]
        assert not thieu2, f"{ten} thiếu màu đậm: {thieu2}"


def test_skin_bao_khong_pha_luat_chu_tieng_viet():
    """Skin "Báo" không được viết hoa toàn bộ, siết chữ, hay hạ `line-height`.

    Dấu tiếng Việt chồng tầng (ế, ộ, ữ) bị cắt ngọn — đúng cái bẫy đã ghi cho
    slide và cho `.sv-note`. Chất "tít báo" phải lấy từ độ đậm và nét kẻ, không
    lấy từ chữ hoa.
    """
    import re
    from pathlib import Path
    css = (Path(__file__).resolve().parents[1] / "web/style.css").read_text()
    # chỉ xét những luật thuộc skin này
    luat = re.findall(r':root\[data-theme="bao"\][^{]*\{([^}]*)\}', css)
    assert luat, "không thấy luật nào của skin Báo"
    than = " ".join(luat).replace(" ", "")

    assert "text-transform:uppercase" not in than, "viết hoa toàn bộ cắt ngọn dấu"
    assert not re.search(r"letter-spacing:-", than), "siết chữ âm cắt ngọn dấu"
    for lh in re.findall(r"line-height:([\d.]+)", than):
        assert float(lh) >= 1.28, f"line-height {lh} dưới 1.28"
    # và không dùng font mono cho văn xuôi
    assert "font-family:var(--mono)" not in than, "mono không dựng nổi dấu chồng tầng"
