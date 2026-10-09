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
    """Gắn tay một bộ slide v2 vào DB — không gọi model."""
    from server import slide, store
    d = store.load(doc["id"])
    bid = d["blocks"][1]["id"]
    bo = [slide._mo_dau(d), slide._lo_trinh(),
          {"vai": "van_de", "tieu_de": "Truy hồi một lượt bỏ sót bằng chứng bắc cầu",
           "cau": "Câu hỏi nhiều bước cần hai đoạn văn", "vi_du": "Ai là đạo diễn phim X",
           "nguon": [bid], "loi_noi": "nói", "canh_bao": []},
          {"vai": "dong_lai", "tieu_de": "Ba điều mang về", "y": ["Một", "Hai", "Ba"],
           "cau_hoi": "Còn gì chưa rõ", "nguon": [], "loi_noi": "", "canh_bao": []}]
    d["slides"] = {"v": 2, "bo": slide._danh_ma(bo), "phut": 10}
    store.save(d)
    return doc["id"]


@pytest.mark.parametrize("fmt", ["md", "html", "slides", "slides-pdf"])
def test_xuat_du_bon_dang(app_client, with_deck, fmt):
    r = app_client.get(f"/api/doc/{with_deck}/export?fmt={fmt}")
    assert r.status_code == 200, (fmt, r.text[:200])
    assert len(r.content) > 500, fmt


def test_file_slide_xuat_ra_dung_chung_bo_ve_voi_app(app_client, with_deck):
    """File tải về nhúng NGUYÊN VĂN `web/slide-ve.js` + `web/slide.css` — một bộ
    vẽ duy nhất, nên xem trước và file xuất không thể lệch nhau."""
    from server.main import WEB
    html = app_client.get(f"/api/doc/{with_deck}/export?fmt=slides").text
    assert (WEB / "slide-ve.js").read_text(encoding="utf-8").strip()[:400] in html
    assert "SlideVe" in html and "Truy hồi một lượt" in html
    # Font nhúng thẳng, không trỏ về /vendor của app (mở file ngoài app vẫn đúng chữ)
    assert "/vendor/fonts/" not in html


def test_slide_sua_doi_cho_xoa(app_client, with_deck):
    did = with_deck
    bo = app_client.get(f"/api/doc/{did}/slides").json()["bo"]
    assert [s["vai"] for s in bo][:2] == ["mo_dau", "lo_trinh"]
    sid = bo[2]["id"]
    r = app_client.patch(f"/api/doc/{did}/slides/{sid}",
                         json={"tieu_de": "Tiêu đề sửa tay", "nguon": ["khongcothat"]})
    assert r.status_code == 200
    s = next(x for x in r.json()["bo"] if x["id"] == sid)
    assert s["tieu_de"] == "Tiêu đề sửa tay" and s["sua_tay"]
    # `nguon` không sửa tay được — sửa được thì phép soát số liệu thành vô nghĩa
    assert s["nguon"] != ["khongcothat"]
    # Số bịa sửa tay vào vẫn bị soát
    r = app_client.patch(f"/api/doc/{did}/slides/{sid}", json={"cau": "Tăng 987,6 điểm"})
    s = next(x for x in r.json()["bo"] if x["id"] == sid)
    assert any("987" in c for c in s["canh_bao"])
    assert app_client.patch(f"/api/doc/{did}/slides/{sid}",
                            json={"hinh": "khongcothat"}).status_code == 400
    assert app_client.patch(f"/api/doc/{did}/slides/s99", json={}).status_code == 404

    ids = [x["id"] for x in bo]
    moi = ids[:2] + ids[2:][::-1]
    assert [x["id"] for x in app_client.post(f"/api/doc/{did}/slides/thu-tu",
                                             json={"ids": moi}).json()["bo"]] == moi
    assert app_client.post(f"/api/doc/{did}/slides/thu-tu",
                           json={"ids": ids[:2]}).status_code == 400
    assert app_client.post(f"/api/doc/{did}/slides/thu-tu", json={"ids": ids}).status_code == 200

    r = app_client.delete(f"/api/doc/{did}/slides/{ids[-1]}")
    assert r.status_code == 200 and len(r.json()["bo"]) == len(ids) - 1
    assert app_client.delete(f"/api/doc/{did}/slides/{ids[-1]}").status_code == 404


def test_slide_chua_co_tom_luoc_thi_khong_tao(app_client):
    """Sinh slide đi sau `cached_prefix` + mạch lập luận trong brief — chưa có
    brief thì từ chối, không gọi model."""
    r = app_client.post("/api/import", data={
        "text": "Bài nháp\n\nMột đoạn văn đủ dài để thành một khối riêng của bài.",
        "model": "test/model", "force": "1"})
    did = r.json()["id"]
    assert app_client.post(f"/api/doc/{did}/slides/tao", json={"phut": 10}).status_code == 400
    assert app_client.get(f"/api/doc/{did}/export?fmt=slides").status_code == 400


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


def test_thu_vien_co_tim_sap_va_ten_model_doc_duoc(app_client, doc):
    """Thư viện phải tìm được, sắp được, và gọi tên model theo lối người đọc.

    `list_docs()` phải trả `cost_usd` / `source` / `created_at`: hộp thoại xoá
    dùng chúng để nói ra CÁI GIÁ, và ô sắp dùng để xếp theo tiền và theo ngày
    nạp. Thiếu một cột là mất im lặng một lựa chọn trên giao diện.
    """
    import re
    from pathlib import Path
    rows = app_client.get("/api/docs").json()
    assert rows, "không có bài nào để kiểm"
    for cot in ("cost_usd", "source", "created_at", "updated_at", "model"):
        assert cot in rows[0], f"/api/docs thiếu cột {cot}"

    app = (Path(__file__).resolve().parents[1] / "web/app.js").read_text()
    # Mỗi khoá sắp trong HTML phải có một hàm so sánh thật trong `RECENT_SORT` —
    # thiếu thì ô chọn có mục đó nhưng chọn vào không đổi gì, và không có lỗi nào.
    html = (Path(__file__).resolve().parents[1] / "web/index.html").read_text()
    o = re.search(r'<select id="recentSort".*?</select>', html, re.S)
    assert o, "không thấy ô sắp thư viện"
    khoa = re.findall(r'value="([^"]+)"', o.group(0))
    assert khoa, "ô sắp không có mục nào"
    bang = re.search(r"const RECENT_SORT = \{(.*?)\n\};", app, re.S)
    assert bang, "không thấy RECENT_SORT"
    for k in khoa:
        assert re.search(rf"(?m)^\s*{re.escape(k)}:", bang.group(1)), f"RECENT_SORT thiếu {k}"

    # Tìm phải đi qua `khongDau` — gõ "truy hoi" phải ra "truy hồi".
    lo = re.search(r"function loRecent\(\) \{(.*?)\n\}", app, re.S)
    assert lo and "khongDau" in lo.group(1), "ô tìm thư viện không bỏ dấu"


def test_nap_trung_bai_thi_hoi_truoc_khi_boc(app_client):
    """Nạp lại đúng file PDF đó phải DỪNG LẠI và hỏi, không lặng lẽ tạo bản thứ hai.

    `parse_cache` làm việc nạp lại gần như miễn phí nên về kỹ thuật không hỏng
    gì — cái hỏng là ở thư viện: một bản TRỐNG nằm cạnh bản đã dịch, và mở nhầm
    bản mới là tưởng mất sạch bản dịch đã trả tiền.

    Và phép dò phải chạy TRƯỚC khi bóc, nên câu trả lời không được kèm `blocks`
    của một bài mới nào cả.
    """
    import fitz
    tai = fitz.open()
    trang = tai.new_page()
    trang.insert_text((72, 100), "Bai thu nghiem nap trung", fontsize=18)
    trang.insert_text((72, 140),
                      "Mo hinh de xuat dat 42.5 diem F1 tren tap kiem tra, cao hon "
                      "baseline manh nhat 3.1 diem tren ca ba bo du lieu.", fontsize=11)
    pdf = tai.tobytes()
    tai.close()

    tep = {"file": ("trung.pdf", pdf, "application/pdf")}
    r1 = app_client.post("/api/import", files=tep, data={"model": "test/model"})
    assert r1.status_code == 200, r1.text
    d1 = r1.json()
    assert "duplicate" not in d1, "lần nạp ĐẦU không được coi là trùng"

    r2 = app_client.post("/api/import", files=tep, data={"model": "test/model"})
    assert r2.status_code == 200, r2.text
    d2 = r2.json()
    dup = d2.get("duplicate")
    assert dup, "nạp lại cùng file mà không báo trùng"
    assert dup["id"] == d1["id"]
    assert "blocks" not in d2, "đã bóc rồi mới hỏi — phải hỏi TRƯỚC khi bóc"
    # Phải nói ra mất gì: số khối, phần đã dịch, số tiền đã tốn.
    for cot in ("title", "blocks", "translated", "translatable", "cost_usd"):
        assert cot in dup, f"câu báo trùng thiếu {cot}"

    # `force=1` là đường thoát, và nó phải tạo một bài THẬT SỰ KHÁC.
    r3 = app_client.post("/api/import", files=tep,
                         data={"model": "test/model", "force": "1"})
    assert r3.status_code == 200, r3.text
    d3 = r3.json()
    assert "duplicate" not in d3 and d3["id"] != d1["id"]

    for did in (d1["id"], d3["id"]):
        app_client.delete(f"/api/doc/{did}")


def test_hai_me_dich_chay_xen_nhau_khong_mat_ban_dich(app_client, monkeypatch):
    """Frontend dịch SONG SONG nhiều mẻ, nên `stream_chunk` phải chịu được hai mẻ
    chạy xen nhau trên cùng một bài mà không mẻ nào ghi đè mẻ kia.

    Bất biến giữ nó: mỗi lần ghi là `load → sửa → save` LIỀN MẠCH, không có
    `await` hay `yield` nào chen giữa. Một tiến trình, một event loop, nên đoạn
    đó là nguyên tử. Ai chèn một `await` vào giữa (gọi DB qua executor, đẩy
    tiến trình lên giữa chừng…) là mẻ xong sau ghi đè mẻ xong trước bằng bản
    `translations` cũ nó đã nạp — mất bản dịch đã trả tiền, và **không có lỗi
    nào**. Test này giả model, ép hai mẻ đan xen từng nhịp, rồi đếm.
    """
    import asyncio
    from server import llm, pipeline, store, db

    # Một bài đủ dài để chia ra ít nhất hai mẻ.
    doan = "\n\n".join(
        f"Paragraph {i} explains step {i} of the method in enough words to count "
        f"as real prose, so the planner treats it as a separate block number {i}."
        for i in range(160))
    r = app_client.post("/api/import", data={"text": "Song song\n\n" + doan,
                                            "model": "test/song-song"})
    assert r.status_code == 200, r.text
    did = r.json()["id"]
    doc = store.load(did)
    doc["brief"] = {"title_vi": "Song song", "glossary": []}
    store.save(doc)
    me = pipeline.plan_chunks(store.load(did))
    assert len(me) >= 2, f"bài thử chỉ ra {len(me)} mẻ — cần ≥2 để thử chạy xen"

    # Mọi mẻ phải tới cửa `load → save` CÙNG MỘT NHỊP. Bản đầu của test này để
    # mỗi mẻ tự stream theo độ dài của nó, nên mẻ ngắn về đích trước và cửa sổ
    # ghi của hai mẻ không bao giờ chồng lên nhau — test xanh kể cả khi đã cố ý
    # chèn `await` vào giữa load và save. Một phép thử không biết đỏ thì không
    # canh được gì.
    con_lai = len(me)
    cung_luc = asyncio.Event()

    async def gia_model(messages, **kw):
        nonlocal con_lai
        import re
        for bid in re.findall(r"<<<(b\d+)>>>", messages[-1]["content"]):
            yield "text", f"<<<{bid}>>>\nBản dịch của {bid}.\n"
        con_lai -= 1
        if con_lai == 0:
            cung_luc.set()
        await cung_luc.wait()          # chờ MỌI mẻ stream xong rồi mới thả ra
        yield "usage", '{"prompt_tokens": 10, "completion_tokens": 5, "cost": 0.0001}'

    monkeypatch.setattr(llm, "stream_text", gia_model)

    async def chay(i):
        async for _ in pipeline.stream_chunk(did, i, mode="vi"):
            pass

    async def ca_hai():
        # `Event` phải sinh ra BÊN TRONG vòng lặp sẽ dùng nó.
        nonlocal cung_luc
        cung_luc = asyncio.Event()
        await asyncio.gather(*(chay(i) for i in range(len(me))))

    asyncio.run(ca_hai())

    tr = store.load(did)["translations"]
    can = [it["id"] for chunk in me for it in chunk]
    thieu = [b for b in can if b not in tr]
    assert not thieu, (f"chạy {len(me)} mẻ xen nhau mất {len(thieu)}/{len(can)} "
                       f"bản dịch — có `await` chen giữa load và save: {thieu[:5]}")
    app_client.delete(f"/api/doc/{did}")


def test_moi_cho_doc_hash_deu_qua_mot_luat():
    """`location.hash` có ba dạng sống chung: `#survey`, `#doc=<mã>`, `#<mã>`.

    Bộ nghe `hashchange` viết cho nút Back/Forward từng coi MỌI hash khác rỗng
    là mã bài: bấm "Tìm hiểu" thì `svOpen` ghi `#survey`, bộ nghe gọi
    `openDoc("survey")`, nhận 404, rồi đá về màn nhập — công cụ thứ hai của app
    mất hẳn lối vào, không lỗi nào. Lúc khởi động cũng vậy với `#doc=<mã>`.

    Nên mọi chỗ ĐỌC hash phải đi qua `docHash()`; đọc thẳng `location.hash
    .slice(1)` ở đâu là chỗ đó lặp lại đúng lỗi cũ.
    """
    import re
    from pathlib import Path
    app = (Path(__file__).resolve().parents[1] / "web/app.js").read_text()
    than = re.sub(r"/\*.*?\*/", "", app, flags=re.S)
    than = re.sub(r"(?m)//.*$", "", than)
    assert "function docHash()" in than
    ham = re.search(r"function docHash\(\) \{(.*?)\n\}", than, re.S).group(1)
    for dang in ('"survey"', '"doc="'):
        assert dang in ham, f"docHash không xử lý dạng {dang}"
    # Ngoài chính docHash, không ai được đọc thẳng hash rồi coi là mã bài.
    ngoai = than.replace(ham, "")
    assert "location.hash.slice(1)" not in ngoai, "còn chỗ đọc thẳng location.hash"


def test_bam_dich_tren_bai_da_xong_khong_tu_goi_luot_tinh_tien():
    """Bài đã dịch xong mà bấm "Dịch tiếp" — đúng thứ người mới hay bấm, vì nút ấy
    to và vàng nhất màn — thì vòng dịch chạy qua không làm gì. Bản cũ khi ấy vẫn
    tự gọi lượt đánh dấu câu đáng nhớ: TÍNH TIỀN, và thay hết vệt cũ bằng bộ khác,
    vì điều kiện chỉ hỏi "đã dịch hết chưa", không hỏi "lượt này có dịch gì không".

    Phép kiểm cấu trúc, vì lỗi này không hiện ra ở đâu ngoài hoá đơn.
    """
    import re
    from pathlib import Path
    app = (Path(__file__).resolve().parents[1] / "web/app.js").read_text()
    than = re.sub(r"(?m)//.*$", "", app)
    goi = re.search(r"if \(([^{]*?)\)\s*\{\s*await markInsights\(true\)", than, re.S)
    assert goi, "không thấy chỗ tự gọi markInsights(true)"
    assert "can.length" in goi.group(1), (
        "tự đánh dấu phải đòi lượt này có mẻ để dịch (can.length > 0)")
    # Và nhãn nút phải tính theo VIỆC CÒN LẠI, không theo việc đã làm.
    assert "function capNhatNutDich()" in app
    assert '? "Dịch tiếp" : "Dịch"' not in app, "còn chỗ đặt nhãn theo kiểu cũ"


def _bai_thu(app_client, ten):
    r = app_client.post("/api/import", data={
        "text": f"{ten}\n\nĐoạn thân bài đủ dài để thành một khối riêng của {ten}.",
        "model": "test/model"})
    assert r.status_code == 200, r.text
    return r.json()["id"]


def test_thu_muc_va_chon_nhieu(app_client):
    """Thư mục và chọn-nhiều của thư viện, đủ vòng CRUD.

    Hai chỗ được canh riêng vì cả hai hỏng CÂM:

    - **Lưu bài không được làm bài rơi khỏi thư mục.** `save_doc` ghi bằng
      INSERT OR REPLACE với danh sách cột cố định; quan hệ bài–thư mục mà nằm
      trên một cột của `documents` thì mỗi lần lưu bản dịch là bị đặt lại.
    - **Xoá thư mục không xoá bài** — bài về "Chưa xếp".
    """
    from server import store
    a, b, c = (_bai_thu(app_client, t) for t in ("Bài A thư mục", "Bài B thư mục", "Bài C thư mục"))

    r = app_client.post("/api/folders", json={"name": "  Robot   học máy "})
    assert r.status_code == 200, r.text
    fid = r.json()["id"]
    assert r.json()["name"] == "Robot học máy", "phải gộp khoảng trắng thừa"
    # trùng tên (không phân biệt hoa thường) thì từ chối
    assert app_client.post("/api/folders", json={"name": "robot HỌC máy"}).status_code == 409
    assert app_client.post("/api/folders", json={"name": "   "}).status_code == 400

    assert app_client.post("/api/docs/move", json={"ids": [a, b], "folder_id": fid}).json()["moved"] == 2
    rows = {d["id"]: d for d in app_client.get("/api/docs").json()}
    assert rows[a]["folder_id"] == fid and rows[b]["folder_id"] == fid and rows[c]["folder_id"] is None
    assert [f["n"] for f in app_client.get("/api/folders").json() if f["id"] == fid] == [2]

    # LƯU lại bài (đúng đường mà mọi lượt dịch đi qua) — vẫn phải còn trong thư mục
    store.save(store.load(a))
    assert {d["id"]: d for d in app_client.get("/api/docs").json()}[a]["folder_id"] == fid, \
        "lưu bài làm bài rơi khỏi thư mục"

    # đổi tên; trùng với chính nó thì được
    assert app_client.patch(f"/api/folders/{fid}", json={"name": "Robot"}).status_code == 200
    assert app_client.patch(f"/api/folders/{fid}", json={"name": "ROBOT"}).status_code == 200

    # đưa một bài về "Chưa xếp"
    app_client.post("/api/docs/move", json={"ids": [b], "folder_id": None})
    assert {d["id"]: d for d in app_client.get("/api/docs").json()}[b]["folder_id"] is None

    # chuyển vào thư mục không tồn tại / mã bẩn
    assert app_client.post("/api/docs/move", json={"ids": [a], "folder_id": "khongco"}).status_code == 404
    assert app_client.post("/api/docs/move", json={"ids": ["../etc"], "folder_id": fid}).status_code == 400

    # xoá THƯ MỤC: bài vẫn còn, về "Chưa xếp"
    r = app_client.delete(f"/api/folders/{fid}")
    assert r.status_code == 200 and r.json()["returned"] == 1
    rows = {d["id"]: d for d in app_client.get("/api/docs").json()}
    assert a in rows and rows[a]["folder_id"] is None, "xoá thư mục đã xoá luôn bài"

    # xoá NHIỀU bài một lượt; mã không còn thì bỏ qua chứ không hỏng cả lượt
    r = app_client.post("/api/docs/delete", json={"ids": [a, b, "khongconnua"]})
    assert r.status_code == 200 and r.json()["deleted"] == 2
    con = {d["id"] for d in app_client.get("/api/docs").json()}
    assert a not in con and b not in con and c in con
    assert app_client.post("/api/docs/delete", json={"ids": []}).status_code == 400
    assert app_client.post("/api/docs/delete", json={"ids": ["a/b"]}).status_code == 400
    app_client.delete(f"/api/doc/{c}")


def test_bai_trung_ghi_de_phien_ban_hoac_bai_rieng(app_client):
    """Bài trùng có ba lối ra, và mỗi lối phải làm đúng việc của nó.

    - Cùng BÀI khác file (v2, bản sửa) — phép dò theo SHA không bắt được, nên dò
      thêm theo tiêu đề / mã arXiv; trước bản này nó lặng lẽ thành bài riêng.
    - **Ghi đè** bóc file mới VÀO bài cũ: cùng mã bài, đoạn không đổi GIỮ bản
      dịch đã trả tiền, đoạn mới chờ dịch, số bài trong thư viện không đổi.
    - **Phiên bản** là bài mới gắn v2 vào họ của bài cũ, và theo bài cũ vào đúng
      thư mục của nó.
    """
    from server import store
    TEN = "Phiên bản thử nghiệm của bài báo về điều khiển robot"
    GIU = "Đoạn này giữ nguyên giữa hai phiên bản, bản dịch của nó phải còn."
    v1 = app_client.post("/api/import", data={
        "text": f"{TEN}\n\n{GIU}\n\nĐoạn chỉ có ở phiên bản một, sẽ bị bỏ ở bản sau.",
        "model": "test/model"}).json()["id"]
    # Có bản dịch cho đoạn giữ nguyên — thứ ghi đè không được làm mất.
    d = store.load(v1)
    bid = next(b["id"] for b in d["blocks"] if b["text"].startswith("Đoạn này giữ nguyên"))
    d["translations"][bid] = "BẢN DỊCH ĐÃ TRẢ TIỀN"
    store.save(d)
    fid = app_client.post("/api/folders", json={"name": "Thư mục của v1"}).json()["id"]
    app_client.post("/api/docs/move", json={"ids": [v1], "folder_id": fid})

    v2_text = f"{TEN}\n\n{GIU}\n\nĐoạn mới chỉ có ở phiên bản hai, chưa dịch."
    # 1) cùng bài, khác nội dung → hỏi, kind = cung_bai, chưa tạo gì
    n0 = len(app_client.get("/api/docs").json())
    r = app_client.post("/api/import", data={"text": v2_text, "model": "test/model"}).json()
    assert r.get("duplicate", {}).get("kind") == "cung_bai", r
    assert r["duplicate"]["id"] == v1
    assert len(app_client.get("/api/docs").json()) == n0, "đã tạo bài trước khi hỏi"

    # 2) ghi đè → cùng mã, giữ bản dịch đoạn không đổi, đoạn mới chờ dịch
    r = app_client.post("/api/import", data={"text": v2_text, "model": "test/model",
                                            "che_do": "ghi_de", "goc": v1})
    assert r.status_code == 200, r.text
    g = r.json()
    assert g["id"] == v1 and "ghi_de" in g, "ghi đè phải trả về CHÍNH bài cũ"
    assert len(app_client.get("/api/docs").json()) == n0, "ghi đè đã đẻ thêm bài"
    d = store.load(v1)
    assert d["translations"].get(bid) == "BẢN DỊCH ĐÃ TRẢ TIỀN", "ghi đè làm mất bản dịch"
    texts = [b["text"] for b in d["blocks"]]
    assert any("phiên bản hai" in t for t in texts)
    assert not any("phiên bản một" in t for t in texts)

    # 3) phiên bản → bài mới v2, bài cũ thành v1, cùng thư mục
    r = app_client.post("/api/import", data={"text": v2_text + "\n\nThêm một đoạn nữa.",
                                            "model": "test/model",
                                            "che_do": "phien_ban", "goc": v1})
    assert r.status_code == 200, r.text
    v2 = r.json()["id"]
    rows = {x["id"]: x for x in app_client.get("/api/docs").json()}
    assert rows[v1]["version"] == 1 and rows[v2]["version"] == 2
    assert rows[v2]["version_of"] == v1
    assert rows[v2]["folder_id"] == fid, "phiên bản mới không theo bài gốc vào thư mục"
    # nạp v3 từ v2 vẫn cùng một họ
    v3 = app_client.post("/api/import", data={"text": v2_text + "\n\nBản ba.", "model": "test/model",
                                             "che_do": "phien_ban", "goc": v2}).json()["id"]
    rows = {x["id"]: x for x in app_client.get("/api/docs").json()}
    assert rows[v3]["version"] == 3 and rows[v3]["version_of"] == v1

    # 4) bài riêng → không phiên bản, không hỏi
    r = app_client.post("/api/import", data={"text": v2_text, "model": "test/model", "che_do": "moi"}).json()
    assert "duplicate" not in r
    rieng = r["id"]
    assert {x["id"]: x for x in app_client.get("/api/docs").json()}[rieng]["version"] is None

    # 5) mã gốc bậy / chế độ lạ
    assert app_client.post("/api/import", data={"text": v2_text, "che_do": "ghi_de",
                                               "goc": "khongco"}).status_code == 404
    assert app_client.post("/api/import", data={"text": v2_text, "che_do": "xoa_het"}).status_code == 400

    app_client.post("/api/docs/delete", json={"ids": [v1, v2, v3, rieng]})
    app_client.delete(f"/api/folders/{fid}")


def test_ma_arxiv_nhan_ra_phien_ban():
    """`2604.00965v1.pdf` và `arXiv:2604.00965v2` là cùng một bài; số khác thì không."""
    from server.main import _ARXIV_ID
    g = lambda s: (m.group(1) if (m := _ARXIV_ID.search(s)) else None)
    assert g("2604.00965v1.pdf") == g("arXiv:2604.00965v2") == "2604.00965"
    assert g("arXiv:1706.03762") == "1706.03762"
    assert g("paper.pdf") is None
    assert g("12604.00965") is None, "không được bắt dính vào một dãy số dài hơn"


def test_khong_con_hop_thoai_native(app_client):
    """`confirm()` / `prompt()` / `alert()` của hệ KHOÁ cả tab, và Chromium còn
    cho người dùng tick "chặn trang này hiện thêm hộp thoại" — tick vào là mọi
    câu hỏi sau đó bị bỏ qua **im lặng**, tức `confirm()` trả `false` và người
    dùng tưởng nút không ăn.

    Mà phần lớn câu hỏi ở đây là câu hỏi TỐN TIỀN, nên phải nói rõ mất gì và giá
    bao nhiêu — hộp thoại native hiện chữ một cỡ nên đoạn giải thích đó trôi hết.
    Thay bằng `xacNhan` / `nhapChu` / `baoTin`.

    Phép kiểm cấu trúc vì đây là loại lỗi không hiện ra lúc chạy: hộp thoại
    native vẫn "hoạt động", chỉ là hoạt động sai chỗ.
    """
    import re
    from pathlib import Path
    web = Path(__file__).resolve().parents[1] / "web"
    xau = []
    for ten in ("app.js", "survey.js"):
        src = (web / ten).read_text()
        # Bỏ comment TRƯỚC khi quét — chính chỗ giải thích luật này nhắc tới
        # `confirm()`, nên không bỏ thì phép kiểm tự báo sai.
        src = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
        src = re.sub(r"(?m)//.*$", "", src)
        for m in re.finditer(r"(?<![.\w])(confirm|alert|prompt)\s*\(", src):
            xau.append(f"{ten}: {m.group(1)}() ở offset {m.start()}")
    assert not xau, "còn hộp thoại native: " + "; ".join(xau)

    # Và ba hàm thay thế phải có thật, cùng hộp thoại trong DOM.
    app = (web / "app.js").read_text()
    for ham in ("function xacNhan", "function nhapChu", "function baoTin"):
        assert ham in app, f"thiếu {ham}"
    html = (web / "index.html").read_text()
    for el in ("dlgVeil", "dlgTitle", "dlgBody", "dlgInput", "dlgArea", "dlgOk", "dlgCancel"):
        assert f'id="{el}"' in html, f"thiếu #{el} trong index.html"


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


def test_ma_nguon_chi_mount_o_ban_phat_trien(app_client):
    """Mount mã nguồn tiện cho người PHÁT TRIỂN (sửa giao diện khỏi dựng lại ảnh —
    quên dựng là thấy y hệt bản cũ, đã mất cả buổi vì đúng chuyện đó), nhưng là
    bẫy với người chỉ CHẠY app: mã trên đĩa che mã trong ảnh. Nên mount nằm ở
    docker-compose.dev.yml, file compose chính tuyệt đối không mount mã nguồn."""
    from pathlib import Path
    goc = Path(__file__).resolve().parents[1]
    yml = goc.joinpath("docker-compose.yml").read_text()
    dev = goc.joinpath("docker-compose.dev.yml").read_text()
    assert "/app/web" not in yml and "/app/server" not in yml
    assert "./data:/data" in yml          # dữ liệu vẫn phải nằm ngoài ảnh
    assert "./web:/app/web:ro" in dev and "./server:/app/server:ro" in dev


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


def test_giao_dien_khong_pha_luat_chu_tieng_viet():
    """Không luật nào của giao diện app được viết hoa toàn bộ, siết chữ âm, hay
    đặt font mono cho văn xuôi tiếng Việt.

    Dấu tiếng Việt chồng tầng (ế, ộ, ữ) bị cắt ngọn — đúng cái bẫy đã ghi cho
    slide và cho `.sv-note`. Bản đầu của test này chỉ soát skin "Báo"; từ khi
    chất liệu của skin ấy thành giao diện mặc định, nó soát CẢ HAI file. Lúc
    chuyển sang, còn 13 nhãn viết hoa trong phần app ("THƯ VIỆN", "BÀI TOÁN",
    "TÀI LIỆU"…) — nhãn mục giờ lấy chất từ font, không từ chữ hoa.

    Slide (`slide.css`) cũng soát chung: một bộ vẽ cho cả app lẫn file xuất,
    và chữ trên slide là chữ to nhất app — cắt ngọn dấu ở đó là lộ nhất.
    """
    import re
    from pathlib import Path
    web = Path(__file__).resolve().parents[1] / "web"
    for ten in ("style.css", "survey.css", "slide.css"):
        css = re.sub(r"/\*.*?\*/", "", (web / ten).read_text(), flags=re.S)
        for sel, than in re.findall(r"([^{}]+)\{([^{}]*)\}", css):
            sel = " ".join(sel.split())
            if sel.startswith("@"):
                continue
            t = than.replace(" ", "")
            assert "text-transform:uppercase" not in t, f"{ten} · {sel}: viết hoa cắt ngọn dấu"
            assert not re.search(r"letter-spacing:-", t), f"{ten} · {sel}: siết chữ âm"
            # Ký hiệu lẻ trong `<code>` thì mono là đúng — chỉ văn xuôi mới cấm.
            if ((".sv-note" in sel or "modelnote" in sel)
                    and not sel.rstrip().endswith("code")):
                assert "var(--mono)" not in t, f"{ten} · {sel}: mono cho văn xuôi"

def test_do_chu_bi_bo_roi_khong_bao_dong_sai(app_client, doc_pdf):
    """Phép đo "chữ chưa vào khối nào" phải im trên bài bóc tốt.

    Nó canh kiểu hỏng tệ nhất của bước bóc tách — **mất chữ im lặng**: một bảng
    không viền từng biến mất hoàn toàn mà Bước 1 vẫn báo xong kèm giá dịch.

    Nhưng bản đầu đo sai tới 20 lần vì hai chỗ, và cả hai đều làm nó **kêu oan
    trên mọi bài** — mà một chốt chặn kêu oan thì người dùng thôi đọc nó:

    - đối chiếu bằng `full_source_text()`, vốn **cố ý loại khối `reference`**,
      nên mỗi mục tham khảo thành một khoản mất. Đo trên SONIC: riêng từ `arxiv`
      bị tính mất 86 lần.
    - nhận tiêu đề chạy đầu trang bằng dải lề 5,5%, bỏ sót header của SONIC nên
      từng từ trong tên bài bị tính mất 35–104 lần.

    Sau khi sửa: 0–4% trên sáu bài thật, không bài nào vượt ngưỡng 5%.
    """
    r = app_client.get(f"/api/doc/{doc_pdf['id']}/estimate?mode=both")
    assert r.status_code == 200, r.text
    e = r.json()
    assert "uncovered_chars" in e and "pdf_chars" in e
    assert e["pdf_chars"] > 0, "bài có PDF gốc thì phải đo được"
    assert e["uncovered_chars"] <= e["pdf_chars"] * 0.15, \
        f"kêu oan: {e['uncovered_chars']}/{e['pdf_chars']}"

    # và phải nói rõ các con số khối là gì — ba con số không chú thích từng làm
    # người dùng tưởng mất khối (161 tổng / 94 dịch / 117 hiển thị)
    assert isinstance(e.get("blocks_by_role"), dict) and e["blocks_by_role"]
    assert sum(e["blocks_by_role"].values()) == e["blocks_total"]


def test_do_bo_roi_doi_chieu_voi_MOI_khoi():
    """Phép đo không được dùng `full_source_text()` để đối chiếu.

    Hàm đó cố ý loại khối `reference` vì thư mục không cần vào ngữ cảnh model.
    Dùng nó làm mốc đối chiếu thì mỗi mục tham khảo thành một khoản "mất" — đó
    chính là lỗi đã làm phép đo phóng đại 20 lần.
    """
    import inspect
    from server import pipeline

    # Bỏ docstring và comment trước khi kiểm: chính docstring của hàm có nhắc
    # `full_source_text()` để giải thích vì sao KHÔNG dùng nó, nên tìm thô là
    # phép kiểm tự báo sai.
    import re
    src = inspect.getsource(pipeline.chu_bi_bo_roi)
    ma = re.sub(r'"""[\s\S]*?"""', "", src)
    ma = "\n".join(re.sub(r"#.*$", "", ln) for ln in ma.splitlines())

    assert "full_source_text" not in ma, \
        "đối chiếu bằng full_source_text() là bỏ sót toàn bộ thư mục tham khảo"
    assert 'b["text"] for b in doc["blocks"]' in ma, "phải đối chiếu với MỌI khối"
    # và nhận tiêu đề chạy bằng độ lặp, không chỉ bằng dải lề
    assert "lap[" in ma, "phải nhận tiêu đề chạy bằng độ lặp"


def test_dung_tom_luoc_treo_thi_het_gio_chu_khong_treo_vo_han(app_client, monkeypatch):
    """#2: model nhận request rồi im — lượt dựng tóm lược treo hơn 5 phút, nút
    Dừng kẹt ở "Đang dừng…". Giờ có trần riêng (`tran_brief`), gọi lại đúng MỘT
    lần, hỏng cả hai thì trả 504 kèm câu nói rõ là quá giờ."""
    import asyncio
    from server import llm, pipeline

    goi = []

    async def model_treo(*a, **k):
        goi.append(1)
        await asyncio.sleep(30)

    monkeypatch.setattr(llm, "complete", model_treo)
    monkeypatch.setattr(pipeline, "tran_brief", lambda n: 0.05)
    r = app_client.post("/api/import", data={"text": "Bài treo\n\nMột đoạn văn đủ dài "
                                            "để thành một khối riêng trong bài thử.",
                                            "model": "test/treo"})
    assert r.status_code == 200, r.text
    r = app_client.post(f"/api/doc/{r.json()['id']}/brief")
    assert r.status_code == 504
    assert "Quá giờ" in r.json()["detail"]
    assert len(goi) == 2


def test_trang_gioi_thieu_va_huong_dan(app_client):
    """`docs/` là trang giới thiệu + hướng dẫn: app phục vụ ở `/gioi-thieu/`, nút
    "Hướng dẫn" ở màn đầu trỏ vào đó. Ảnh chụp và neo mục lục dễ trôi khi sửa
    sau này — ảnh hỏng hay mục lục trỏ vào mục không có thì không lỗi nào báo."""
    import re
    from pathlib import Path
    goc = Path(__file__).resolve().parent.parent / "docs"
    for ten in ("index.html", "huong-dan.html"):
        r = app_client.get(f"/gioi-thieu/{ten}")
        assert r.status_code == 200, ten
        html = r.text
        for anh in re.findall(r'src="(anh/[^"]+)"', html):
            assert (goc / anh).is_file(), f"{ten} trỏ tới ảnh không có: {anh}"
        ids = set(re.findall(r'\sid="([^"]+)"', html))
        for neo in re.findall(r'href="#([^"]+)"', html):
            assert neo in ids, f"{ten} có neo #{neo} không trỏ vào đâu"
        for trang, neo in re.findall(r'href="(huong-dan\.html|index\.html)#([^"]+)"', html):
            assert f'id="{neo}"' in (goc / trang).read_text(encoding="utf-8"), f"{trang}#{neo}"
    assert app_client.get("/gioi-thieu/").status_code == 200
    assert 'href="/gioi-thieu/huong-dan.html"' in app_client.get("/").text


def test_boc_lai_doi_ten_anh_theo_ma_khoi(app_client):
    """S16: bóc lại giữ MÃ KHỐI cũ (ghép theo nội dung) nhưng ảnh mang tên của bản
    bóc MỚI. Trên CIRAG: khối b94 (Table 5) trỏ ảnh b103, còn ảnh tên b94 là của
    khối khác — slide xin "hình b94" theo mã khối nhận nhầm bảng ablation."""
    from server import main, store
    from server.parser import Block
    r = app_client.post("/api/import", data={"text": "Bài có hình\n\nĐoạn mở đầu đủ dài để thành khối.\n\nFigure 1: Hình một.\n\nTable 1: Bảng một.",
                                            "model": "test/anh"})
    doc = store.load(r.json()["id"])
    cap = [b for b in doc["blocks"] if b["type"] == "caption"]
    assert len(cap) == 2
    # bản bóc MỚI: cùng nội dung, nhưng mã khối và tên ảnh đã trôi (b7, b9)
    moi = []
    for i, b in enumerate(doc["blocks"]):
        nb = Block(**{**b, "id": f"b{50 + i}"})
        if b["type"] == "caption":
            nb.figure = f"b{90 + i}"
        moi.append(nb)
    imgs = {nb.figure: f"PNG-{nb.text}".encode() for nb in moi if nb.figure}
    main._ghep_ban_boc(doc, moi, imgs, "", "", b"")
    doc = store.load(doc["id"])
    for b in doc["blocks"]:
        if b["type"] == "caption":
            assert b["figure"] == b["id"], "ảnh phải mang đúng mã khối"
            assert store.image_path(doc["id"], b["id"]).read_bytes() == f"PNG-{b['text']}".encode()


def test_thu_vien_thong_tin_nhan_va_bibtex(app_client):
    """Thư viện kiểu Zotero: sửa tay thông tin, gắn nhãn, xuất BibTeX — và lượt
    tự lấy thông tin sau đó KHÔNG ghi đè trường đã sửa tay."""
    from server import db
    r = app_client.post("/api/import", data={"text": "Attention Is All You Need\n\nThe dominant sequence "
                                            "transduction models are based on recurrent networks.",
                                            "model": "test/tv"})
    did = r.json()["id"]
    r = app_client.patch(f"/api/doc/{did}/meta", json={"authors": "Ashish Vaswani; Noam Shazeer",
                                                       "year": "2017", "venue": "NeurIPS"})
    assert r.status_code == 200, r.text
    assert r.json()["data"]["authors"] == ["Ashish Vaswani", "Noam Shazeer"]
    assert set(r.json()["sua_tay"]) == {"authors", "year", "venue"}
    # lượt tự lấy (giả) không được đè tác giả đã sửa tay, nhưng bổ sung trường còn trống
    db.set_meta(did, {"authors": ["Sai Tên"], "doi": "10.5555/3295222"}, "crossref")
    m = db.get_meta(did)["data"]
    assert m["authors"] == ["Ashish Vaswani", "Noam Shazeer"] and m["doi"] == "10.5555/3295222"
    assert app_client.patch(f"/api/doc/{did}/meta", json={"year": "năm"}).status_code == 400

    r = app_client.put(f"/api/doc/{did}/tags", json={"tags": ["transformer", " Transformer ", "đọc sau"]})
    assert r.json()["tags"] == ["transformer", "đọc sau"]      # trùng không phân biệt hoa thường
    assert {"tag": "đọc sau", "count": 1} in app_client.get("/api/tags").json()
    row = next(x for x in app_client.get("/api/docs").json() if x["id"] == did)
    assert row["meta"]["year"] == 2017 and row["tags"] == ["transformer", "đọc sau"]
    assert "abstract" not in row["meta"]                       # danh sách không chở abstract

    bib = app_client.get(f"/api/docs/bibtex?ids={did}").text
    assert "@article{vaswani2017" in bib and "author = {Ashish Vaswani and Noam Shazeer}" in bib
    assert "year = {2017}" in bib and "journal = {NeurIPS}" in bib

    app_client.post("/api/docs/delete", json={"ids": [did]})
    assert db.get_meta(did)["data"] == {} and "đọc sau" not in [t["tag"] for t in db.list_tags()]


def test_tao_slide_mot_luot_goi_model_va_tu_dung_mo_dau(app_client, monkeypatch):
    """Sinh slide là MỘT lượt gọi model; slide mở đầu và lộ trình dựng tại chỗ.
    JSON hỏng thì thử lại đúng một lần."""
    import json
    from server import llm, store

    r = app_client.post("/api/import", data={
        "text": "Bài slide giả\n\nMô hình đạt 42,5 điểm F1, cao hơn baseline 3,1 điểm.",
        "model": "test/slide", "force": "1"})
    did = r.json()["id"]
    d = store.load(did)
    d["brief"] = {"title_vi": "Bài slide giả", "glossary": [],
                  "argument_chain": [{"role": "problem", "step": "x"}]}
    store.save(d)
    bid = d["blocks"][-1]["id"]
    goi = []

    async def gia(messages, **kw):
        goi.append(kw.get("session_id"))
        if len(goi) == 1:
            return "{hỏng", llm.Usage()
        return json.dumps({"slides": [
            {"vai": "dong_lai", "tieu_de": "Đúc kết", "y": ["a", "b", "c"], "cau_hoi": "?"},
            {"vai": "so_lieu", "tieu_de": "Đạt 42,5 điểm F1", "gia_tri": "42,5",
             "nhan": "F1", "moc": "cao hơn baseline 3,1 điểm", "nguon": [bid]},
            {"vai": "vai_la", "tieu_de": "bỏ"},
        ]}), llm.Usage()

    monkeypatch.setattr(llm, "complete", gia)
    r = app_client.post(f"/api/doc/{did}/slides/tao", json={"phut": 10})
    assert r.status_code == 200, r.text
    bo = r.json()["slides"]["bo"]
    assert [s["vai"] for s in bo] == ["mo_dau", "lo_trinh", "so_lieu", "dong_lai"]
    assert bo[0]["tieu_de"] == "Bài slide giả"
    # 42,5 và 3,1 có trong bài nên không bị bắt; bộ thiếu slide cơ chế thì bị báo
    assert not any("Số không có" in c for c in bo[2]["canh_bao"])
    assert any("cơ chế" in c for c in bo[2]["canh_bao"])
    assert goi == [did, did]  # session_id cho sticky routing, đúng hai lần


def test_ban_xuat_dung_latex_nhu_man_hinh_va_khong_vach_mau_doc(app_client):
    """Bản xuất từng thiếu bước `\\(…\\)` mà `sci()` của app có — màn hình dựng
    đúng còn file tải về hiện nguyên `\\in`. Và chất liệu sổ tay không được có
    vạch màu kẻ dọc cạnh ô chữ (bản cũ có ở bốn chỗ)."""
    import re
    from server import store
    r = app_client.post("/api/import", data={
        "text": "Bài xuất\n\nMột đoạn văn đủ dài để thành một khối riêng của bài thử.",
        "model": "test/xuat", "force": "1"})
    did = r.json()["id"]
    d = store.load(did)
    bid = d["blocks"][-1]["id"]
    d["translations"][bid] = r"Tập \(Suf(a) \in \{0, 1\}\) với x^{2}."
    store.save(d)
    html = app_client.get(f"/api/doc/{did}/export?fmt=html&mode=vi").text
    assert "\\in" not in html.split("<main>")[1] and "∈" in html and "<sup>2</sup>" in html
    css = html.split("<style>")[1].split("</style>")[0]
    assert not re.search(r"border-left:\s*[23]px solid", css), "vạch màu dọc cạnh ô chữ"
    assert "text-transform:uppercase" not in css.replace(" ", "")
    assert "data:font/woff2" in css  # font nhúng: mở file ngoài app vẫn đúng chữ


def test_slide_model_tra_rong_thi_bao_ro_va_van_ghi_chi_phi(app_client, monkeypatch):
    """DeepSeek V4 Flash tiêu hết trần token vào nghĩ thầm rồi trả CHUỖI RỖNG.
    Hai lượt hỏng vẫn bị tính tiền — phải cộng vào chi phí của bài, và lời báo
    phải nói đúng lý do chứ không phải "không đọc được"."""
    from server import llm, store
    r = app_client.post("/api/import", data={
        "text": "Bài rỗng\n\nMột đoạn văn đủ dài để thành một khối riêng của bài thử.",
        "model": "test/rong", "force": "1"})
    did = r.json()["id"]
    d = store.load(did)
    d["brief"] = {"title_vi": "Bài rỗng", "glossary": []}
    store.save(d)
    goi = []

    async def rong(messages, **kw):
        goi.append(kw.get("reasoning"))
        return "", llm.Usage(prompt_tokens=100, completion_tokens=16000, cost=0.008)

    monkeypatch.setattr(llm, "complete", rong)
    r = app_client.post(f"/api/doc/{did}/slides/tao", json={"phut": 10})
    assert r.status_code == 502 and "rỗng" in r.json()["detail"]
    assert len(goi) == 2 and all(x == {"enabled": False} for x in goi)
    assert abs(store.load(did)["usage"]["cost"] - 0.016) < 1e-9
