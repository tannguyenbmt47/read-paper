"""Test đơn vị cho phần logic thuần — không cần server, không gọi model.

Ưu tiên những chốt chặn mà hỏng thì im lặng: bộ soát số liệu trên slide, bộ đo
tràn khung, bộ bóc Mermaid, và bộ dựng chỉ số trên/dưới. Đó là chỗ sai không ai
nhìn ra bằng mắt.
"""
from __future__ import annotations

import pytest

from server import pipeline, slide_fit, slide_theme
from server.parser import Block
from server.pptx_out import parse_mermaid, _levels


# --------------------------------------------------------------- chốt số liệu

def _doc(text: str = "Mô hình đạt 42,5 điểm trên tập 2WikiMQA với 1.000 câu hỏi."):
    return {
        "id": "d1",
        "blocks": [{"id": "b1", "type": "para", "text": text, "translate": True}],
        "translations": {"b1": text},
        "brief": {"glossary": []},
        "slides": {},
    }


def _slide(**kw):
    base = dict(id="s1", kind="content", eyebrow="KẾT QUẢ",
                headline="Một câu khẳng định có động từ rõ ràng ở đây",
                bullets=[], cards=[], notes=" ".join(["nói"] * 130),
                source_block_ids=["b1"])
    base.update(kw)
    return base


def test_so_bia_bi_bat():
    """Số không có trong khối nguồn phải bị cảnh báo — chốt chặn quan trọng nhất."""
    d = _doc()
    sl = _slide(bullets=["Đạt 99,9 điểm"])
    pipeline.check_slides(d, [sl])
    assert any("99,9" in w for w in sl["warn"]), sl["warn"]


def test_so_co_that_khong_bi_bat():
    d = _doc()
    sl = _slide(bullets=["Đạt 42,5 điểm trên 2WikiMQA"])
    pipeline.check_slides(d, [sl])
    assert not any("không có trong khối nguồn" in w for w in sl["warn"]), sl["warn"]


@pytest.mark.parametrize("txt", [
    "Mã nguồn ở github.com/52566rz/CIRAG",     # định danh trong URL
    "Chạy trên Qwen2.5-7B-Instruct",           # tên model
    "Đánh giá trên 2WikiMQA",                  # tên tập dữ liệu mở đầu bằng số
])
def test_url_va_dinh_danh_khong_bi_coi_la_so_lieu(txt):
    d = _doc()
    sl = _slide(bullets=[txt])
    pipeline.check_slides(d, [sl])
    assert not any("không có trong khối nguồn" in w for w in sl["warn"]), (txt, sl["warn"])


def test_slide_tieu_de_khong_bi_kiem_so():
    """Năm hội nghị và độ dài buổi nói vốn không có trong bài."""
    d = _doc()
    sl = _slide(kind="title", headline="CIRAG", bullets=["Báo cáo seminar 20 phút"],
                source_block_ids=[])
    pipeline.check_slides(d, [sl])
    assert not any("không có trong khối nguồn" in w for w in sl["warn"]), sl["warn"]


def test_nhan_chu_de_rong_bi_bat():
    d = _doc()
    sl = _slide(headline="Kết quả thực nghiệm:")
    pipeline.check_slides(d, [sl])
    assert any("nhãn chủ đề" in w or "hai chấm" in w for w in sl["warn"]), sl["warn"]


# ------------------------------------------------------------ bố cục & tràn khung

def test_bo_cuc_theo_ti_le_anh(monkeypatch):
    """Ảnh ngang cho tràn khung, ảnh vuông thì hai cột — quyết định từ tỉ lệ thật."""
    monkeypatch.setattr(pipeline, "figure_shape",
                        lambda d, f: ("wide", 3.0) if f == "wide" else ("square", 1.0))
    assert pipeline.slide_layout(_slide(figure="wide"), "d1") == "figwide"
    assert pipeline.slide_layout(
        _slide(figure="sq", bullets=["a"]), "d1") == "figside"


def test_the_khong_bao_gio_vao_cot_hep(monkeypatch):
    """Ba thẻ nhét vào cột 44% thì mỗi thẻ còn ~90px — đã vấp, đừng lặp lại."""
    monkeypatch.setattr(pipeline, "figure_shape", lambda d, f: ("square", 1.0))
    sl = _slide(figure="f1", cards=[{"title": f"Thẻ {i}"} for i in range(3)])
    assert pipeline.slide_layout(sl, "d1") == "figwide"


def test_autofit_co_lai_khi_qua_dai():
    y = "Một ý rất dài dùng để kiểm tra xem bộ đo có phát hiện tràn khung hay không"
    day = _slide(cards=[{"title": f"Thẻ {i}", "bullets": [y] * 4} for i in range(4)],
                 callout={"title": "Chốt lại", "body": "Một câu ngắn"})
    assert slide_fit.fit(day, "cards", 1.0)["over"] > 0      # đúng là quá dài
    assert slide_fit.autofit(day, "cards") < 1.0             # nên phải co lại


def test_autofit_khong_co_duoi_san():
    """Co hết cỡ vẫn không vừa thì dừng ở sàn, không co xuống mức không đọc nổi."""
    qua = _slide(cards=[{"title": f"T{i}", "bullets": ["chữ " * 40] * 6} for i in range(4)])
    assert slide_fit.autofit(qua, "cards") == slide_fit.SCALE_MIN


def test_autofit_khong_co_khi_ngan():
    ngan = _slide(bullets=["Một ý ngắn"])
    assert slide_fit.autofit(ngan, "list") == 1.0


def test_do_chu_dung_font_that():
    """Chuỗi dài hơn thì phải đo ra rộng hơn — nếu không là font không nạp được."""
    assert slide_fit.text_w("mmmmmmmmmm", 20) > slide_fit.text_w("ii", 20)
    assert slide_fit.lines("từ " * 60, 200, 18) > 1


# --------------------------------------------------------------- bóc Mermaid

def test_bocmermaid_khong_sinh_node_rac():
    """Nhãn cạnh `-->|"x"|` từng bị đọc thành node tên `u`, `ch`."""
    _, nodes, edges = parse_mermaid(
        'flowchart LR\n A["Câu hỏi"] --> B["Bằng chứng"]\n'
        ' B -->|"thiếu"| C["Suy luận hỏng"]')
    assert set(nodes) == {"A", "B", "C"}
    assert nodes["C"] == "Suy luận hỏng"
    assert ("B", "C", "thiếu") in edges


def test_bocmermaid_chuoi_nhieu_buoc():
    _, nodes, edges = parse_mermaid('flowchart LR\n A["x"] --> B["y"] --> C["z"]')
    assert len(nodes) == 3 and len(edges) == 2


def test_xep_tang_so_do():
    _, nodes, edges = parse_mermaid(
        'flowchart TD\n A["a"] --> B["b"]\n A --> C["c"]\n B --> D["d"]')
    lv = _levels(nodes, edges)
    assert lv[0] == ["A"] and set(lv[1]) == {"B", "C"}


def test_nhan_dung_khong_bi_bao_sai():
    """`A["nhãn"]` là dạng ĐÚNG mà DIAGRAM_RULES yêu cầu."""
    assert not pipeline._bad_mermaid_labels('flowchart TD\n A["ổn"] --> B["ổn"]')
    assert pipeline._bad_mermaid_labels('flowchart TD\n A["có "trích" bên trong"] --> B["x"]')


# ------------------------------------------------------- chỉ số trên/dưới

def test_chi_so_tren_duoi_dung_lai_duoc():
    from server.pptx_out import _SUP, _SUB
    assert _SUP.search("E = mc^{2}")
    assert _SUB.search("H_{<t}")


def test_icon_la_bao_gio_cung_an_toan():
    assert slide_theme.icon_svg("khong-co-icon-nay") == ""
    assert slide_theme.icon_svg("check").startswith("<svg")


def test_mau_the_luan_phien():
    assert slide_theme.card_tint(0) != slide_theme.card_tint(1)
    assert slide_theme.card_tint(0) == slide_theme.card_tint(4)


# ------------------------------------------------------------ khối và mẻ dịch

def test_khoi_an_khong_vao_me_dich():
    from server.parser import chunk_blocks
    bs = [Block(id=f"b{i}", type="para", text="chữ " * 200) for i in range(6)]
    truoc = sum(len(c) for c in chunk_blocks(bs))
    bs[0].hidden = True
    sau = sum(len(c) for c in chunk_blocks(bs))
    assert sau == truoc - 1


def test_thuat_ngu_gan_lan_dau_xuat_hien():
    d = _doc("CIRAG dùng knowledge triple.")
    d["brief"] = {"glossary": [
        {"en": "knowledge triple", "keep_en": True, "gloss": "bộ ba tri thức"}]}
    deck = [_slide(bullets=["Dùng knowledge triple"]),
            _slide(id="s2", bullets=["knowledge triple lần hai"])]
    pipeline.attach_terms(d, deck)
    assert [t["en"] for t in deck[0]["terms"]] == ["knowledge triple"]
    assert deck[1]["terms"] == []      # lần thứ hai không nhắc lại


# ------------------------------------------------------------ phụ lục

@pytest.mark.parametrize("t", [
    "A Theia Model Architecture", "B Training", "C Additional Ablation Studies",
    "D.1 Baseline Models", "D.3.1 WidowX Arm Experiments", "Appendix A", "Phụ lục B",
])
def test_nhan_ra_tieu_de_phu_luc(t):
    """Phụ lục nằm SAU mục tham khảo — không nhận ra thì cả phụ lục biến mất."""
    from server.parser import _is_appendix_head
    assert _is_appendix_head(t), t


@pytest.mark.parametrize("t", [
    "A. Radford, J. Kim, C. Hallacy. Learning transferable visual models",
    "K. He, X. Zhang, S. Ren, and J. Sun. Deep residual learning",
    "[75] A. Xie, L. Lee, T. Xiao, and C. Finn. Decomposing the task",
    "We train Theia on 8 NVIDIA H100 GPUs.",
    "Backbone. We use the DeiT-Tiny models.",
])
def test_khong_nham_muc_tham_khao_thanh_phu_luc(t):
    """Dương tính giả ở đây kéo cả trăm mục sách báo vào bài — tệ hơn nhiều."""
    from server.parser import _is_appendix_head
    assert not _is_appendix_head(t), t


# ------------------------------------------------------- dàn ý (bước 1)

def _item(**kw):
    base = dict(id="o1", kind="content", section="Kết quả",
                message="Mô hình đề xuất đạt 42,5 điểm trên tập kiểm tra",
                points=["Đạt 42,5 điểm trên tập kiểm tra", "Lặp lại trên ba bộ dữ liệu",
                        "Chi phí suy luận tăng theo số vòng"],
                evidence={"kind": "diagram", "figure": "", "what": "vòng lặp truy hồi"},
                source_block_ids=["b1"])
    base.update(kw)
    return base


def _outline(**kw):
    base = dict(thesis="Một câu chốt", items=[_item()], backup=[],
                sections=[{"name": "A"}, {"name": "B"}, {"name": "Kết quả"}])
    base.update(kw)
    return base


def test_dan_y_bat_so_bia():
    """Bắt số bịa từ lúc còn là dàn ý thì sửa một dòng, để lọt thì phải dựng lại slide."""
    d = _doc()
    ol = _outline(items=[_item(points=["Đạt 99,9 điểm"])])
    pipeline.check_outline(d, ol)
    assert any("99,9" in w for w in ol["items"][0]["warn"]), ol["items"][0]["warn"]


def test_dan_y_khong_bat_so_co_that():
    d = _doc()
    ol = _outline()
    pipeline.check_outline(d, ol)
    assert not any("không có trong khối nguồn" in w for w in ol["items"][0]["warn"])


def test_dan_y_bat_nhan_chu_de_rong():
    d = _doc()
    ol = _outline(items=[_item(message="Kết quả")])
    pipeline.check_outline(d, ol)
    assert any("nhãn chủ đề" in w for w in ol["items"][0]["warn"])


def test_dan_y_bat_muc_rong_y():
    """Bước dựng slide KHÔNG nghĩ hộ nội dung mới — mục rỗng ở đây là slide rỗng."""
    d = _doc()
    ol = _outline(items=[_item(points=[])])
    assert any("không nghĩ hộ" in w or "Không có ý nào" in w
               for w in pipeline.check_outline(d, ol)["items"][0]["warn"])


def test_dan_y_bat_thieu_bang_chung():
    d = _doc()
    ol = _outline(items=[_item(evidence={"kind": "none", "figure": "", "what": ""})])
    assert any("bằng chứng" in w for w in pipeline.check_outline(d, ol)["items"][0]["warn"])


def test_dan_y_bat_anh_khong_co_that():
    d = _doc()
    ol = _outline(items=[_item(evidence={"kind": "figure", "figure": "khongco",
                                         "what": ""})])
    it = pipeline.check_outline(d, ol)["items"][0]
    assert any("Không có ảnh" in w for w in it["warn"])
    assert it["evidence"]["figure"] == ""       # gỡ luôn để bước dựng không gắn nhầm


def test_dan_y_bat_chia_qua_nhieu_phan():
    """3–4 phần: nhiều hơn thì người nghe không giữ nổi bản đồ trong đầu."""
    d = _doc()
    ol = _outline(sections=[{"name": f"P{i}"} for i in range(6)])
    assert pipeline.check_outline(d, ol)["warn"]


def test_dan_y_danh_ma_lien_tuc_va_isalnum():
    """Mã mục đi vào URL nên phải `isalnum()` — cùng hàng rào với doc_id/block_id."""
    ol = pipeline._number_outline(_outline(items=[_item(), _item()],
                                           backup=[_item()]))
    ids = [i["id"] for i in ol["items"]] + [i["id"] for i in ol["backup"]]
    assert ids == ["o1", "o2", "o3"] and all(i.isalnum() for i in ids)


def test_sua_khoi_nguon_thi_danh_dau_ca_dan_y():
    """`mark_stale` bỏ sót dàn ý thì lần dựng sau đẻ lại đúng cái slide đã sai."""
    d = _doc()
    d["slides"] = {"deck": [], "backup": [], "outline": _outline()}
    pipeline.mark_stale(d, ["b1"])
    assert d["slides"]["outline"]["items"][0]["stale"] is True


# ------------------------------------------------- chốt chặn độ sâu

def test_do_sau_bat_cau_dat_ten_thay_vi_giai_thich():
    """Phép thử wakalixes: thay thuật ngữ bằng từ vô nghĩa mà câu vẫn "đúng"
    thì nó chưa giải thích gì."""
    from server import depth
    for nong in (
        "CIRAG dùng cơ chế construction-integration để cải thiện chất lượng truy hồi.",
        "Phương pháp này đóng vai trò quan trọng trong việc nâng cao hiệu quả.",
        "Việc mở rộng dữ liệu góp phần nâng cao chất lượng bộ điều khiển.",
    ):
        assert depth.check_text(nong), nong


def test_do_sau_khong_keu_oan_cau_co_co_che_hoac_so_lieu():
    """Chốt chặn kêu oan vài lần là người dùng thôi đọc nó, lúc đó cảnh báo thật
    cũng trôi theo."""
    from server import depth
    for sau in (
        "CIRAG dựng mạng mệnh đề từ các đoạn lấy về rồi cho chúng kích hoạt lẫn "
        "nhau, nên mệnh đề không được đoạn nào khác đỡ sẽ tắt dần.",
        "Theia đạt 62,3 điểm trên CortexBench, cao hơn baseline mạnh nhất 4,1 điểm.",
        "Bằng cách chưng cất nhiều teacher vào một encoder, mô hình giữ được đặc "
        "trưng không gian mà vẫn chạy nhanh hơn.",
    ):
        assert depth.check_text(sau) == [], sau


def test_do_sau_bat_giai_thich_vong_tron():
    from server import depth
    assert depth.circular("mạng mệnh đề", "Mạng mệnh đề là một mạng gồm các mệnh đề.")
    assert not depth.circular(
        "mạng mệnh đề",
        "Mỗi câu thành một nút; hai nút nối nhau khi cùng nhắc một thực thể, "
        "nên cụm rời rạc sẽ yếu dần qua từng vòng.")


def test_do_sau_bo_qua_cau_ngan():
    """Câu ngắn có thể là một khẳng định gọn — đòi nhân quả ở đó là bắt viết dài
    dòng cho đủ hình thức."""
    from server import depth
    assert depth.check_text("Mô hình dùng ViT-B.") == []


def test_slide_thieu_slide_co_che_thi_bao_ca_bo():
    """Bộ slide kể được bài toán và kết quả nhưng bỏ mất phần giữa — kiểu hỏng
    người trình bày không tự nhận ra."""
    from server import pipeline
    deck = [{"kind": "content", "headline": f"Khẳng định số {i} đạt 9{i} điểm",
             "bullets": ["Kết quả đo trên tập thử nghiệm cho thấy 9%d điểm" % i]}
            for i in range(6)]
    pipeline.check_depth(deck)
    assert any("không có slide nào đi hết cơ chế" in w
               for s in deck for w in s.get("warn", []))


def test_slide_co_slide_co_che_thi_khong_bao():
    from server import pipeline
    deck = [{"kind": "content", "headline": f"Khẳng định {i}", "bullets": ["x"]}
            for i in range(5)]
    deck.append({"kind": "content", "headline": "Cách CIRAG chạy trên một câu hỏi",
                 "bullets": [
                     "Bước 1: câu hỏi vào bộ tìm, trả về 10 đoạn ứng viên",
                     "Bước 2: mỗi đoạn thành một nút, nên nút rời rạc sẽ yếu dần",
                     "Cuối cùng đầu ra là 3 đoạn còn sáng, vì chúng đỡ lẫn nhau"]})
    pipeline.check_depth(deck)
    assert not any("không có slide nào đi hết cơ chế" in w
                   for s in deck for w in s.get("warn", []))


# --------------------------- nhặt lại chữ mô hình bố cục bỏ sót

def _span(text, x0, y0, size=10.0):
    """Span giả đúng hình dạng PyMuPDF trả về."""
    return {"text": text, "size": size, "bbox": (x0, y0, x0 + len(text) * 5, y0 + 11),
            "origin": (x0, y0 + 9), "flags": 0}


def test_nhat_lai_khong_tron_chu_hai_cot():
    """`_rows()` gom theo baseline trên CẢ TRANG, nên hai cột cùng độ cao thành
    một dòng và chữ cài răng lược vào nhau. Đã ra đúng vậy ở bản đầu:
    "…static evidence repre- summarized as follows: sentation, failing…"
    """
    from server.parser import _loose_paras
    trai = [_span("paradigms typically adopt a static", 50, 100),
            _span("evidence representation, failing to", 50, 112)]
    phai = [_span("summarized as follows:", 320, 100),
            _span("we propose CIRAG which", 320, 112)]
    got = _loose_paras(trai + phai, page_width=612)
    texts = [t for _b, t in got]
    assert len(got) == 2, texts
    assert any("paradigms typically" in t and "summarized" not in t for t in texts)
    assert any("summarized as follows" in t and "paradigms" not in t for t in texts)


def test_nhat_lai_tach_doan_khi_cach_xa():
    """Hai dòng cách nhau hơn hai dòng là hai khối khác — nối lại thì dính."""
    from server.parser import _loose_paras
    spans = [_span("dong dau cua doan mot", 50, 100),
             _span("dong hai cua doan mot", 50, 112),
             _span("doan hai o tan duoi trang", 50, 400)]
    assert len(_loose_paras(spans, page_width=612)) == 2


def test_nhat_lai_bo_chu_trong_hinh_va_chu_khac_co():
    """Chữ trong vùng hình phải biến mất khỏi mạch đọc; chữ khác cỡ thân bài
    (số trang, nhãn trục) cũng vậy."""
    import fitz
    from server.parser import _uncovered

    doc = fitz.open()
    page = doc.new_page(width=612, height=792)
    page.insert_text((50, 100), "cau van xuoi nam ngoai moi khung", fontsize=10)
    page.insert_text((50, 300), "nhan truc trong hinh", fontsize=10)
    page.insert_text((50, 500), "so trang", fontsize=6)
    got = [s["text"] for s in _uncovered(page, boxes=[],
                                         figs=[(40, 280, 400, 320)], body=10.0)]
    doc.close()
    joined = " ".join(got)
    assert "cau van xuoi" in joined
    assert "nhan truc" not in joined      # nằm trong vùng hình
    assert "so trang" not in joined       # cỡ chữ khác thân bài


# ------------------------- bóc lại mà giữ nguyên bản dịch

def test_boc_lai_giu_ban_dich_theo_NOI_DUNG():
    """Ghép theo nội dung, KHÔNG theo vị trí. Bản bóc mới chèn thêm khối thì mọi
    chỉ số phía sau lệch một — dán bản dịch theo vị trí là dán nhầm đoạn, và
    nhìn vẫn có vẻ đúng nên không ai phát hiện."""
    from server import pipeline
    doc = {"blocks": [{"id": "b1", "text": "Đoạn một", "translate": True},
                      {"id": "b2", "text": "Đoạn hai", "translate": True}],
           "translations": {"b1": "dịch một", "b2": "dịch hai"},
           "plain": {}, "notes": {}, "highlights": {"b2": [{"id": "h1"}]},
           "slides": {}}
    # bản bóc mới CHÈN một đoạn vào giữa
    new = [{"id": "x1", "text": "Đoạn một", "translate": True},
           {"id": "x2", "text": "Đoạn mới nhặt về", "translate": True},
           {"id": "x3", "text": "Đoạn hai", "translate": True}]
    st = pipeline.reparse_merge(doc, new)
    ids = [b["id"] for b in doc["blocks"]]
    assert ids[0] == "b1" and ids[2] == "b2"          # mã cũ về đúng chỗ nội dung
    assert ids[1] not in ("b1", "b2")                  # đoạn mới có mã riêng
    assert doc["translations"] == {"b1": "dịch một", "b2": "dịch hai"}
    assert doc["highlights"]["b2"]                     # vệt bôi vẫn bám đúng khối
    assert st == {"blocks": 3, "kept": 2, "new": 1, "dropped": 0, "to_translate": 1}


def test_boc_lai_bo_ban_dich_cua_khoi_khong_con():
    """Khối biến mất thì bản dịch bỏ theo — không mất gì thật, `tm` khoá theo nội
    dung nên đoạn ấy quay lại là lấy lại miễn phí."""
    from server import pipeline
    doc = {"blocks": [{"id": "b1", "text": "còn lại", "translate": True},
                      {"id": "b2", "text": "biến mất", "translate": True}],
           "translations": {"b1": "a", "b2": "b"}, "plain": {}, "notes": {},
           "highlights": {}, "slides": {}}
    st = pipeline.reparse_merge(doc, [{"id": "z", "text": "còn lại", "translate": True}])
    assert "b2" not in doc["translations"] and doc["translations"]["b1"] == "a"
    assert st["dropped"] == 1


def test_boc_lai_khong_dung_lai_ma_cu_cho_hai_khoi():
    """Hai khối mới trùng nội dung nhau thì chỉ khối đầu lấy mã cũ — dùng lại một
    mã cho hai khối là bản dịch hiện ở hai chỗ và mọi thứ trỏ theo mã hoá nhập nhằng."""
    from server import pipeline
    doc = {"blocks": [{"id": "b1", "text": "trùng", "translate": True}],
           "translations": {"b1": "x"}, "plain": {}, "notes": {},
           "highlights": {}, "slides": {}}
    pipeline.reparse_merge(doc, [{"id": "p", "text": "trùng", "translate": True},
                                 {"id": "q", "text": "trùng", "translate": True}])
    ids = [b["id"] for b in doc["blocks"]]
    assert len(set(ids)) == 2 and ids[0] == "b1"


def test_nhat_lai_mot_span_giua_trang_khong_tat_tach_cot():
    """Số trang nằm chính giữa chân trang là chuyện bình thường. Bản đầu dùng
    `any()` nên đúng một cái `26182` tắt phép tách cột cho cả trang, và chữ hai
    cột lại cài răng lược."""
    from server.parser import _loose_paras
    trai = [_span("paradigms typically adopt a static", 50, 100),
            _span("evidence representation, failing to", 50, 112)]
    phai = [_span("summarized as follows:", 320, 100),
            _span("we propose CIRAG which", 320, 112)]
    so_trang = [_span("26182", 285, 700)]
    texts = [t for _b, t in _loose_paras(trai + phai + so_trang, page_width=595)]
    assert any("paradigms typically" in t and "summarized" not in t for t in texts), texts


def test_khung_cat_khong_cat_doi_mot_chu():
    """Khung công thức dựng từ span của riêng nó, nên khi một mảnh dòng văn bên
    cạnh lọt vào thì mép trái rơi vào giữa từ — ảnh hiện "ere at step t…" thay
    vì "where at step t…". Đã ra đúng vậy trên bài CIRAG."""
    import fitz
    from server.parser import _widen_to_glyphs

    doc = fitz.open()
    page = doc.new_page(width=400, height=200)
    page.insert_text((50, 100), "where at step t the teacher", fontsize=10)
    hep = fitz.Rect(66, 88, 200, 104)          # cắt vào giữa chữ "where"
    rong = _widen_to_glyphs(page, hep)
    doc.close()
    assert rong.x0 < hep.x0, "phải nới sang trái cho hết chữ"
    assert rong.y0 == hep.y0 and rong.y1 == hep.y1, "chỉ nới ngang, không nới dọc"


def test_khung_cat_khong_no_ra_ca_cot():
    """Không có trần thì một span dài chạm mép khung kéo khung ra hết cột, và
    ảnh công thức thành ảnh cả đoạn văn."""
    import fitz
    from server.parser import _widen_to_glyphs

    doc = fitz.open()
    page = doc.new_page(width=800, height=200)
    page.insert_text((10, 100), "x" * 150, fontsize=10)     # dòng rất dài
    hep = fitz.Rect(300, 88, 340, 104)
    rong = _widen_to_glyphs(page, hep)
    doc.close()
    assert rong.width <= hep.width * 2.2, f"nở quá tay: {rong.width:.0f} vs {hep.width:.0f}"


def test_boc_lai_hai_lan_cho_ket_qua_giong_het():
    """Khối trùng nội dung phải khớp theo THỨ TỰ XUẤT HIỆN. Khớp một-một thì
    khối thứ hai luôn phải mint mã mới, và mint lại mỗi lần bóc lại — mã phình
    ra dù nội dung y hệt. Đo trên bài thật: 12 khối churn mỗi lượt."""
    from server import pipeline
    doc = {"blocks": [{"id": "b1", "text": "trùng"}, {"id": "b2", "text": "trùng"},
                      {"id": "b3", "text": "khác"}],
           "translations": {"b1": "x", "b2": "y"}, "plain": {}, "notes": {},
           "highlights": {}, "slides": {}}
    new = [{"id": "p", "text": "trùng"}, {"id": "q", "text": "trùng"},
           {"id": "r", "text": "khác"}]
    pipeline.reparse_merge(doc, [dict(b) for b in new])
    st = pipeline.reparse_merge(doc, [dict(b) for b in new])
    assert st["new"] == 0 and st["dropped"] == 0 and st["kept"] == 3
    assert doc["translations"] == {"b1": "x", "b2": "y"}


# --------------------------- rò hệ chữ trong bản dịch

def test_ro_he_chu_bat_ky_tu_la_ngoai_dai_CJK():
    """`cjk_leak` chỉ biết CJK/Hangul. Đã gặp bản dịch chứa `띠ᥕᥕᥲᥕᥱ` thay cho
    chữ "bảo toàn" — `ᥕᥲᥱ` là chữ Limbu, ngoài mọi dải nó biết. Liệt kê hệ chữ
    CẤM là trò đuổi bắt không hồi kết; liệt kê hệ chữ ĐƯỢC PHÉP thì mọi thứ lạ
    đều bị bắt, kể cả hệ chữ chưa ai gặp."""
    from server.pipeline import script_leak
    src = "naturally preserved in passages"
    assert script_leak("vốn được 띠ᥕᥕᥲᥕᥱ trong đoạn", src)          # Limbu + Hangul
    assert script_leak("vốn được било trong đoạn", src)             # Cyrillic
    assert script_leak("vốn được ահնպ trong đoạn", src)             # Armenian
    assert script_leak("vốn được तथा trong đoạn", src)              # Devanagari


def test_ro_he_chu_khong_keu_oan():
    """Kêu oan là người dùng thôi đọc cảnh báo, lúc đó cảnh báo thật cũng trôi."""
    from server.pipeline import script_leak
    src = "naturally preserved in passages"
    for ok in ("các sắc thái ngôn ngữ vốn được bảo toàn trong đoạn văn",
               "Với D = {dᵢ}ᴺᵢ₌₁ và α ≤ β thì ∑ x → y ∈ ℝ",     # chỉ số + Hy Lạp
               "ế ộ ữ ẩ ọ — “trích dẫn” · 62,3% ± 0,4",           # dấu tiếng Việt
               "dấu mũ TeX ˆa và ligature ﬁ"):
        assert script_leak(ok, src) == set(), ok


def test_ro_he_chu_khong_cam_tuyet_doi():
    """Bài về NLP đa ngữ trích tiếng Trung là chuyện thường, và bản dịch giữ
    nguyên nguyên văn là ĐÚNG — so với bản gốc chứ không cấm thẳng."""
    from server.pipeline import script_leak
    assert script_leak("mô hình 深度学习 giữ nguyên", "the 深度学习 model") == set()
    assert script_leak("mô hình 深度学习 giữ nguyên", "the deep learning model")


# ------------------------------- dựng chữ đậm khi hiển thị

def test_xuat_ban_dung_chu_dam_nhung_giu_nguyen_sao_don():
    """Bài báo dùng chữ đậm làm tiêu đề chạy đầu đoạn — bỏ đi là mất một tầng
    cấu trúc, để nguyên `**` là lòi ký tự rác. Nhưng `*` ĐƠN thì không phải chữ
    nghiêng: quét dữ liệu thật thấy nó là ký hiệu chú thích bảng và phép nhân."""
    import re
    def rich(s):
        out = (s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))
        out = re.sub(r"\^\{([^{}]*)\}", r"<sup>\1</sup>", out)
        out = re.sub(r"_\{([^{}]*)\}", r"<sub>\1</sub>", out)
        return re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", out)

    assert rich("**Dataset.** Chúng tôi huấn luyện") == \
        "<strong>Dataset.</strong> Chúng tôi huấn luyện"
    # phép nhân và ký hiệu chú thích: giữ nguyên
    assert rich("learning rate là 2 * 10^{-4}") == "learning rate là 2 * 10<sup>-4</sup>"
    assert "*" in rich("Dấu * biểu thị uniform frame sampling")
    # không chèn được HTML qua nội dung bài
    assert "&lt;script&gt;" in rich("<script>")


def test_hai_ben_dung_cung_mot_luat_dam():
    """`sci()` bên app.js và `rich()` bên main.py phải khớp, nếu không bản xuất
    ra khác bản đang đọc trên màn hình."""
    import pathlib, re
    js = pathlib.Path("web/app.js").read_text()
    py = pathlib.Path("server/main.py").read_text()
    assert r'.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")' in js
    assert r'r"\*\*([^*]+)\*\*", r"<strong>\1</strong>"' in py


# ------------------- giao thức nhãn: dung sai với biến thể model gõ ra

def test_bat_nhan_du_model_go_lech():
    """Hai kiểu lệch đã gặp thật, mỗi kiểu đủ để phá cả giao thức và dồn 20 nghìn
    ký tự của mười mấy khối vào một ô, im lặng."""
    from server.pipeline import _parse_labeled
    ids = ["b4", "b9", "b11"] + [f"b{n}_g" for n in (4, 9, 11)]
    assert _parse_labeled("<<<b4_g>>\nMột.\n<<<b9_g>>\nHai.", ids) == \
        {"b4_g": "Một.", "b9_g": "Hai."}          # thiếu một dấu >
    assert _parse_labeled("### b4_g\nMột.\n### b9_g\nHai.", ids) == \
        {"b4_g": "Một.", "b9_g": "Hai."}          # dạng tiêu đề Markdown
    assert _parse_labeled("**b4**\nMột.\n[b9]\nHai.", ids) == \
        {"b4": "Một.", "b9": "Hai."}              # in đậm và ngoặc vuông


def test_ma_nhac_giua_cau_khong_bi_cat_thanh_nhan():
    """Nhãn phải đứng MỘT MÌNH trên dòng. Không thế thì mọi câu nhắc tới mã khối
    đều cắt bài làm đôi."""
    from server.pipeline import _parse_labeled
    ids = ["b4", "b9", "b12"]
    body = ("Mô hình b4 tốt hơn b9 trong thí nghiệm này.\n"
            "Xem thêm phần [b12] ở phụ lục để biết chi tiết.")
    assert _parse_labeled("<<<b4>>>\n" + body, ids) == {"b4": body}


def test_khong_co_ids_thi_van_chay_dang_chuan():
    """Chỗ gọi chưa biết trước mã vẫn phải dùng được, rơi về `<<<id>>>` thuần."""
    from server.pipeline import _parse_labeled
    assert _parse_labeled("<<<b4>>>\nMột.") == {"b4": "Một."}


# --------- gán span: mép khung cắt ngang dòng đầu thì không được mất cả dòng


class _TrangGia:
    """Trang giả, chỉ cần đúng thứ `assign_spans` đọc."""

    def __init__(self, spans):
        self._spans = spans

    def get_text(self, _kind):
        return {"blocks": [{"type": 0, "lines": [{"spans": self._spans}]}]}


def test_span_bi_mep_khung_cat_ngang_van_vao_dung_khoi():
    """Khung của mô hình bám rất sát chữ, nên mép trên hay cắt ngang dòng đầu.

    Đo thật trên bài GCR: khung abstract bắt đầu ở y=249,4 còn dòng đầu nằm ở
    y=244,5–253,5 — tâm ở 249,0, **cao hơn mép khung 0,4pt**. Gán theo tâm thì
    cả dòng "Long-video question answering requires identifying sparse yet"
    rơi ra ngoài và biến mất khỏi bài, dù docling đã bóc nó đúng.
    """
    from server.parser import assign_spans
    dong_dau = {"text": "Long-video question answering", "bbox": (64.0, 244.5, 282.0, 253.5)}
    dong_hai = {"text": "critical evidence from videos", "bbox": (64.0, 254.5, 282.0, 263.4)}
    khung_abstract = (64.0, 249.4, 282.5, 522.5)

    got = assign_spans(_TrangGia([dong_dau, dong_hai]), [khung_abstract])
    assert [s["text"] for s in got[0]] == [dong_dau["text"], dong_hai["text"]]


def test_span_chi_cham_mep_thi_khong_bi_hut_vao():
    """Chồng lấn phải đủ đáng kể. Chạm mép một chút mà đã hút vào thì chữ của
    khối bên cạnh bị kéo sang — dương tính giả tệ hơn âm tính giả ở đây."""
    from server.parser import assign_spans
    # span cao 10pt, chỉ có 1pt nằm trong khung → 10%, dưới ngưỡng 33%
    span = {"text": "của khối khác", "bbox": (64.0, 240.0, 282.0, 250.0)}
    got = assign_spans(_TrangGia([span]), [(64.0, 249.0, 282.5, 522.5)])
    assert got[0] == []


def test_luot_vet_khong_doi_phep_gan_dung_san():
    """Span có khung chứa tâm thì vẫn về đúng khung NHỎ NHẤT như cũ — lượt vét
    chỉ chạy cho span đã trượt hết ở lượt một."""
    from server.parser import assign_spans
    span = {"text": "trong công thức", "bbox": (100.0, 300.0, 200.0, 310.0)}
    to = (50.0, 250.0, 400.0, 400.0)
    nho = (90.0, 295.0, 210.0, 315.0)
    got = assign_spans(_TrangGia([span]), [to, nho])
    assert got[0] == [] and [s["text"] for s in got[1]] == ["trong công thức"]


# ------------- phễu lọc: gom mảnh bị cắt giữa từ, tắt cờ dịch cho khối rác


def _B(bid, kind, text):
    from server.parser import Block
    return Block(bid, kind, text)


def test_noi_lai_doan_bi_hinh_chen_vao_giua_tu():
    """Ở bài hai cột, hình và bảng được xếp lên đầu cột nên chúng chen vào GIỮA
    CÂU. Đo trên bài CIRAG: 6 đoạn kết thúc bằng `differ-`, `compo-`, `sen-`…
    Mỗi mảnh thành một khối riêng, được dịch riêng, và model tự ghi vào phần
    giải thích rằng "câu gốc bị cắt nên chưa cho biết cụ thể" — vừa tốn hai lượt
    gọi vừa cho ra bản dịch không thể đúng.
    """
    from server.parser import stitch_hyphenated
    bs = [_B("b90", "para", "Table 3 compares differ-"),
          _B("b91", "caption", "Figure 5: Effect of Trajectory Distillation"),
          _B("b92", "caption", "Table 3: Ablation Study on Cascaded"),
          _B("b93", "para", "ent evidence granularities and cascade variants.")]
    assert stitch_hyphenated(bs) == 1
    assert bs[0].text.startswith("Table 3 compares different evidence granularities")
    assert len(bs) == 3            # mảnh sau đã dời hết chữ, không còn khối rỗng


def test_khong_noi_khi_doan_sau_mo_dau_bang_chu_hoa():
    """Chữ hoa là câu mới. Chỉ có gạch nối thì `w/o Triple + Sentence-` cũng
    khớp — phải đòi CẢ HAI dấu hiệu."""
    from server.parser import stitch_hyphenated
    bs = [_B("b1", "para", "and Suf(a) = 1 oth-"),
          _B("b2", "equation", "g = min ..."),
          _B("b3", "para", "The final output is the answer.")]
    assert stitch_hyphenated(bs) == 0
    assert bs[0].text.endswith("oth-")


def test_khong_noi_vat_qua_muc_khac():
    """Gặp heading thì dừng — đoạn cuối mục này không phải là đầu mục sau."""
    from server.parser import stitch_hyphenated
    bs = [_B("b1", "para", "expands context from triples to sen-"),
          _B("b2", "heading", "5.3 Kết quả"),
          _B("b3", "para", "tences and passages as needed.")]
    assert stitch_hyphenated(bs) == 0


def test_tat_co_dich_cho_khoi_rac():
    """Mỗi khối là MỘT lượt dịch cộng MỘT lượt giải thích, nên `57.3%` lạc ra từ
    bảng tốn đúng hai lượt gọi model cho thứ không ai đọc."""
    from server.parser import mark_noise
    bs = [_B("b1", "para", "57.3%"),
          _B("b2", "para", "(4) ..."),
          _B("b3", "meta", "^{1}Our code can be found via https://github.com/x/y."),
          _B("b4", "para", "weizl2@mails.neu.edu.cn"),
          _B("b5", "meta", "^{1}School of Computer Science and Engineering, "
                           "Northeastern University, Shenyang 110819, China"),
          _B("b6", "para", "Chúng tôi đề xuất CIRAG, một khung truy hồi kiến tạo "
                           "tích hợp cho hỏi đáp bắc cầu nhiều chặng.")]
    assert mark_noise(bs) == 5
    assert [b.translate for b in bs] == [False] * 5 + [True]


def test_khoi_ngan_toan_so_nhung_la_ket_qua_that_thi_van_giu():
    """Ranh giới "rác" không bao giờ chắc chắn, nên chỉ TẮT CỜ chứ không xoá —
    người đọc bật lại được. Và câu văn có số thì không phải là rác."""
    from server.parser import mark_noise
    bs = [_B("b1", "para", "CIRAG đạt 62,3 EM trên HotpotQA, cao hơn DPR 4,1 điểm.")]
    assert mark_noise(bs) == 0
    assert bs[0].translate is True


def test_noi_hai_doan_lien_nhau_bi_cat_giua_cau():
    """Không có mốc gạch nối thì luật phải chặt hơn hẳn: hai khối LIỀN KỀ, câu
    trước không kết thúc bằng dấu câu, câu sau mở đầu chữ thường."""
    from server.parser import stitch_hyphenated
    bs = [_B("b1", "para", "To address this question, we propose GCR, a training-free"),
          _B("b2", "para", "framework that Grounds, Covers, and Refines evidence.")]
    assert stitch_hyphenated(bs) == 1
    assert bs[0].text == ("To address this question, we propose GCR, a training-free "
                          "framework that Grounds, Covers, and Refines evidence.")


def test_khong_noi_qua_cong_thuc_du_cau_chua_ket_thuc():
    """Mẫu "…sorted as" → công thức → "where T_V is the video duration" đúng là
    một đoạn bị chen, NHƯNG công thức được cắt thành ẢNH và phải nằm giữa hai
    nửa. Nối chữ lại thì ảnh rơi xuống sau cả đoạn — hỏng nặng hơn để nguyên.
    """
    from server.parser import stitch_hyphenated
    bs = [_B("b1", "para", "Let the timestamps in S0 be sorted as"),
          _B("b2", "equation", "t_1 < t_2 < ... < t_B (12)"),
          _B("b3", "para", "where T_V is the video duration.")]
    assert stitch_hyphenated(bs) == 0
    assert [b.id for b in bs] == ["b1", "b2", "b3"]


def test_cau_da_ket_thuc_thi_khong_noi():
    from server.parser import stitch_hyphenated
    bs = [_B("b1", "para", "Kết quả được trình bày ở Bảng 3."),
          _B("b2", "para", "trong đó mỗi hàng là một phương án.")]
    assert stitch_hyphenated(bs) == 0


def test_thu_muc_tham_khao_khong_bi_dich():
    """Đường docling không có bước gắn nhãn `reference` như `parse_pdf`, nên cả
    thư mục rơi vào section cuối với `translate=True`. Đo trên bài GCR: **5.664
    trên 32.701 ký tự — 17% hoá đơn dịch** đổ vào danh sách tài liệu."""
    from server.parser import mark_noise, looks_like_refs
    refs = ("Song, E.; Chai, W.; Ye, T.; Hwang, J.-N. 2026. MovieChat+: "
            "Question-Aware Sparse Memory. IEEE Transactions on PAMI. "
            "Li, X.; Wang, G. 2025. In Proceedings of CVPR. Bai, S. 2024. NeurIPS.")
    assert looks_like_refs(refs)
    bs = [_B("b1", "para", refs)]
    assert mark_noise(bs) == 1 and bs[0].translate is False


def test_cau_van_day_trich_dan_khong_bi_coi_la_thu_muc():
    """Dấu hiệu bắt buộc là NƠI CÔNG BỐ, không phải mật độ năm: đoạn văn dẫn
    "(Lewis et al., 2020; Lin et al., 2024; Ram et al., 2023)" có mật độ năm cao
    hơn cả thư mục thật."""
    from server.parser import looks_like_refs
    assert not looks_like_refs(
        "RAG hoạt động tốt với truy vấn đơn (Lewis et al., 2020; Lin et al., "
        "2024; Ram et al., 2023) nhưng gặp khó khi câu hỏi cần bắc cầu qua "
        "nhiều tài liệu khác nhau trong cùng một kho tài liệu lớn.")


def test_noi_qua_chu_thich_hinh_nhung_khong_qua_cong_thuc():
    """Hình/bảng là phần tử NỔI — trong bản in đoạn văn chảy vòng qua chúng, nên
    nhảy qua để nối là đúng. Công thức thì nằm trong mạch lập luận và được cắt
    thành ảnh phải đứng giữa hai nửa, nên không nhảy."""
    from server.parser import stitch_hyphenated
    noi = [_B("b1", "para", "reformulating selection not as a ranking problem"),
           _B("b2", "caption", "Figure 2: Qualitative examples"),
           _B("b3", "para", "but a fixed-budget joint evidence curation problem.")]
    assert stitch_hyphenated(noi) == 1
    assert noi[0].text.endswith("but a fixed-budget joint evidence curation problem.")
    assert [b.type for b in noi] == ["para", "caption"]

    khong = [_B("c1", "para", "Let the timestamps in S0 be sorted as"),
             _B("c2", "equation", "t_1 < t_2 (12)"),
             _B("c3", "para", "where T_V is the video duration.")]
    assert stitch_hyphenated(khong) == 0


def test_danh_dau_doan_bi_cong_thuc_chen_vao_giua():
    """Mẫu "…sorted as" → công thức → "where T_V is…" là MỘT đoạn trong bản in.
    Không gộp thành một khối (ảnh công thức phải đứng giữa hai nửa), chỉ gắn cờ
    `cont` để tầng hiển thị bỏ khoảng cách."""
    from server.parser import mark_continuations
    bs = [_B("b1", "para", "Let the timestamps in S0 be sorted as"),
          _B("b2", "equation", "t_1 < t_2 (12)"),
          _B("b3", "para", "where T_V is the video duration.")]
    assert mark_continuations(bs) == 1
    assert bs[2].cont is True and bs[0].cont is False
    assert [b.type for b in bs] == ["para", "equation", "para"]   # KHÔNG gộp


def test_cau_truoc_da_ket_thuc_thi_khong_danh_dau_cont():
    from server.parser import mark_continuations
    bs = [_B("b1", "para", "Chúng tôi đánh giá trên ba tập dữ liệu."),
          _B("b2", "equation", "x = y"),
          _B("b3", "para", "trong đó y là số khung hình.")]
    assert mark_continuations(bs) == 0


def test_giai_thich_tung_doan_khong_bat_model_nghi_tham():
    """Đo thật một lượt: **145,7 giây** cho 1.383 token đầu ra với
    `{"effort":"low"}`, và **14,4 giây** sau khi tắt hẳn — cùng chi phí, cùng
    chất lượng ghi chú.

    Cùng cái bẫy đã ghi ở `survey/lecture.py`: độ sâu đến từ CẤU TRÚC bắt buộc
    trong prompt (`gist`/`role`/`link_back`/`unpack`/`analogy`/`caution`/
    `check`), không đến từ token nghĩ thầm — mỗi trường đã hỏi đúng một câu cụ
    thể nên model không cần tự bày dàn ý.
    """
    import inspect
    from server import pipeline
    src = inspect.getsource(pipeline.explain_block)
    assert "reasoning=NO_REASONING" in src, "explain lại bật nghĩ thầm"
    assert "max_tokens=2500" in src, "trần đầu ra lại bị nới quá tay"


# ---------------------------------------------------- backend bố cục MinerU

def test_gop_o_con_cua_cung_mot_hinh():
    """`layout._gop_vung` gộp ô con thành một hình, và KHÔNG gộp qua caption.

    PP-DocLayoutV2 dò từng **ô** chứ không dò cả hình: hình băng ngang đầu bài
    arXiv:2602.15922 ra **28 vùng** riêng. Để nguyên thì `apply_layout` ghép
    caption với ô gần nhất và người đọc nhận đúng một ô con thay cho cả hình —
    đo được ảnh hẹp nhất còn 445px; sau khi gộp là 1062px.
    """
    from server import layout

    # ba ô dính nhau theo hàng ngang -> phải thành một
    r = [{"page": 0, "kind": "figure", "bbox": [10, 100, 110, 200], "caption": ""},
         {"page": 0, "kind": "figure", "bbox": [111, 100, 210, 200], "caption": ""},
         {"page": 0, "kind": "figure", "bbox": [212, 100, 310, 200], "caption": ""}]
    g = layout._gop_vung([dict(x) for x in r], [])
    assert len(g) == 1
    assert g[0]["bbox"] == [10, 100, 310, 200]

    # cách xa nhau -> giữ nguyên hai hình
    xa = [{"page": 0, "kind": "figure", "bbox": [10, 100, 110, 200], "caption": ""},
          {"page": 0, "kind": "figure", "bbox": [10, 600, 110, 700], "caption": ""}]
    assert len(layout._gop_vung([dict(x) for x in xa], [])) == 2

    # gần nhau NHƯNG có caption chen giữa -> là hai hình khác nhau, không gộp
    cap = [{"page": 0, "kind": "caption", "bbox": [10, 201, 310, 209]}]
    assert len(layout._gop_vung([dict(x) for x in r[:1]] +
                                [{"page": 0, "kind": "figure",
                                  "bbox": [10, 215, 110, 300], "caption": ""}], cap)) == 2

    # hai loại khác nhau thì không gộp: bảng nằm sát dưới hình vẫn là hai thứ
    khac = [{"page": 0, "kind": "figure", "bbox": [10, 100, 110, 200], "caption": ""},
            {"page": 0, "kind": "table", "bbox": [10, 205, 110, 300], "caption": ""}]
    assert len(layout._gop_vung([dict(x) for x in khac], [])) == 2


def test_nhan_mineru_bo_cong_thuc_noi_dong():
    """`inline_formula` phải nằm ngoài `MINERU_LABELS`, và đó là chủ ý.

    Nó là vùng con nằm **trong** một dòng chữ. Đưa vào `items` thì `assign_spans`
    gán glyph theo khung nhỏ nhất chứa tâm span, tức mọi ký hiệu toán giữa câu bị
    bốc khỏi đoạn văn và đoạn bị xé vụn — 13 vùng như vậy chỉ riêng trang 6 của
    arXiv:2602.15922. Cùng lý do với `reference` (khung bao của cả danh sách,
    trong khi `reference_content` mới là từng mục).
    """
    from server import layout

    for nhan in ("inline_formula", "reference", "header", "footer", "number",
                 "formula_number"):
        assert nhan not in layout.MINERU_LABELS, f"{nhan} không được thành khối"
        assert nhan in layout.MINERU_NOISE, f"{nhan} phải nằm trong danh sách bỏ có chủ ý"

    # còn công thức HIỂN THỊ thì phải thành khối equation để được cắt thành ảnh
    assert layout.MINERU_LABELS["display_formula"] == "equation"
    assert layout.MINERU_LABELS["algorithm"] == "equation"
    assert layout.MINERU_LABELS["reference_content"] == "reference"
    # hình/bảng đi vào `regions`, không vào `items`
    assert set(layout.MINERU_REGIONS) == {"image", "chart", "table"}
    assert not (set(layout.MINERU_REGIONS) & set(layout.MINERU_LABELS))


def test_chon_backend_bo_cuc_theo_bien_moi_truong(monkeypatch):
    """`LAYOUT_BACKEND` đặt tên tường minh thì thắng, kể cả khi gói kia có mặt.

    Hai lần đo cùng một PDF ra hai kết quả khác nhau vì máy này có mineru còn máy
    kia có docling là chuyện đủ khó chịu để đáng có một biến chốt lại — cùng bài
    học với `LAYOUT_BACKEND=off` của bản Docker gọn.
    """
    from server import layout

    for tat in ("off", "none", "0", "heuristic", "OFF"):
        monkeypatch.setenv("LAYOUT_BACKEND", tat)
        assert layout.backend() == "off"
    for ten in ("mineru", "docling", "MinerU"):
        monkeypatch.setenv("LAYOUT_BACKEND", ten)
        assert layout.backend() == ten.lower()


def test_dau_hai_cham_van_la_doan_bi_cong_thuc_chen():
    """"…defined as:" → công thức → "where…" phải được nhận là MỘT đoạn.

    `_SENT_END` coi ":" là kết câu — đúng cho `_stitch_runon`, vốn thật sự **nối
    chữ** lại — nhưng `mark_continuations` chỉ gắn cờ hiển thị, và dấu hai chấm
    ngay trước một công thức chính là dấu dẫn vào nó. Đo trên arXiv:2602.15922:
    cả 3 ứng viên của bài đều bị loại đúng vì lý do này.
    """
    from server.parser import Block, mark_continuations

    def blk(i, kind, text):
        return Block(i, kind, text, "", 0, 0, kind == "para")

    bs = [blk("b1", "para", "Our model denoises the latents, defined as:"),
          blk("b2", "equation", "z = t z_1 + (1 - t) z_0"),
          blk("b3", "para", "where z_0 is Gaussian noise and z_1 is the clean latent.")]
    assert mark_continuations(bs) == 1
    assert bs[2].cont is True

    # câu đã kết thúc hẳn bằng dấu chấm thì KHÔNG phải đoạn bị chen
    bs2 = [blk("b1", "para", "We train with flow matching."),
           blk("b2", "equation", "L = E[...]"),
           blk("b3", "para", "where w is a weight function.")]
    assert mark_continuations(bs2) == 0

    # không có công thức chen vào thì cũng không
    bs3 = [blk("b1", "para", "Our model denoises the latents, defined as:"),
           blk("b2", "para", "where z_0 is Gaussian noise.")]
    assert mark_continuations(bs3) == 0


# ------------------------------------------- đánh dấu câu đáng nhớ (pass 5)

def test_va_escape_cuu_duoc_json_co_latex():
    r"""`extract_json` phải sống được với `\(`, `\tilde`, `\{` trong chuỗi.

    Model trích **nguyên văn** một câu có LaTeX thì phải tự escape thành `\\(`,
    và nhiều model không làm. `json.loads` ném `Invalid \escape` rồi hỏng cả lượt
    gọi **đã trả tiền**. Đã gặp thật ở pass đánh dấu, trên bài dịch từ trước khi
    `TRANSLATE_TASK` cấm LaTeX.

    Nó hỏng **theo bài**: bài nào model tình cờ không trích câu có dấu chéo thì
    chạy trót lọt — nên rất dễ tưởng đã ổn, và đó là lý do phải có test.
    """
    from server.llm import extract_json

    d = extract_json(r'{"marks":[{"quote":"ta có \(Suf(a) \in \{0,1\}\)","why":"x"}]}')
    assert d["marks"][0]["quote"] == r"ta có \(Suf(a) \in \{0,1\}\)"

    # escape HỢP LỆ không được đụng tới
    d2 = extract_json('{"a":"dòng1\\ndòng2","b":"nháy \\" bên trong","c":"\\u00e9"}')
    assert d2["a"] == "dòng1\ndòng2"
    assert d2["b"] == 'nháy " bên trong'
    assert d2["c"] == "é"

    # và vẫn bóc được khi model bọc trong ``` hoặc thêm lời dẫn
    assert extract_json('Đây nhé:\n```json\n{"x":1}\n```')["x"] == 1


def test_loai_cau_dang_nho_khop_bang_mau_ve_boi():
    """Năm loại phải ánh xạ đúng năm màu đã có, mỗi màu một loại.

    Màu không chỉ để đẹp: nó là thứ trả lời "vì sao câu này đáng nhớ" ngay khi
    liếc qua. Hai loại trùng màu là mất luôn thông tin đó, mà không có gì báo.
    """
    from server import prompts
    from server.main import HL_COLORS

    mau = [v[0] for v in prompts.INSIGHT_KINDS.values()]
    assert len(mau) == len(set(mau)), "hai loại dùng chung một màu"
    assert set(mau) <= set(HL_COLORS), f"màu lạ: {set(mau) - set(HL_COLORS)}"
    assert set(prompts.INSIGHT_MIX) == set(prompts.INSIGHT_KINDS)
    assert abs(sum(prompts.INSIGHT_MIX.values()) - 1) < 1e-9, "tỉ lệ loại phải cộng bằng 1"


def test_ngan_sach_vet_boi_co_theo_do_dai_bai():
    """Số vệt phải co theo độ dài bài, không phải một con số cứng.

    Bản đầu đặt trần cứng 18 vệt. Trên bài 149 đoạn đã dịch, con số đó ra **một
    vệt mỗi 11 đoạn** — và người dùng nói ngay là quá ít. Đọc lại đúng nghiên cứu
    đã trích: mức được đo là **hiệu quả** là *một hai câu mỗi đoạn*, còn cái thất
    bại là *bôi vàng cả trang*. Bản đầu lẫn hai chuyện đó và siết nhầm.

    Đo trên 6 bài trong `data/` (61–149 đoạn): thưa 6–14 · vừa 15–37 · dày 30–74.
    """
    from server import prompts

    n = 149                                     # bài CIRAG
    b = {lv: prompts.insight_budget(n, lv)["total"] for lv in prompts.INSIGHT_LEVELS}
    assert b["thua"] < b["vua"] < b["day"], "ba mức phải tăng dần"
    assert b["vua"] == 37 and b["day"] == 74

    # bài ngắn vẫn phải có gì đó, bài dài không được phình vô hạn
    assert prompts.insight_budget(3, "thua")["total"] == prompts.INSIGHT_MIN
    assert prompts.insight_budget(100000, "day")["total"] == prompts.INSIGHT_CAP

    # hạn mức từng loại cộng lại phải đủ để đạt trần, và loại nào cũng có suất
    for lv in prompts.INSIGHT_LEVELS:
        bd = prompts.insight_budget(n, lv)
        assert sum(bd["per_kind"].values()) >= bd["total"]
        assert all(v >= 1 for v in bd["per_kind"].values())

    # mọi chỗ trống trong prompt phải được điền
    import re
    assert not re.findall(r"\{[A-Z_]+\}", prompts.insight_task(prompts.insight_budget(n)))


def test_tran_vet_boi_cho_tung_khoi():
    """Một khối không được bôi gần hết, nhưng câu ĐẦU TIÊN thì luôn được nhận.

    Ngân sách tổng không chặn được chuyện dồn hết vào một đoạn. Đo trên
    arXiv:2602.15922 ở mức vừa: khối tóm tắt nhận **4 vệt phủ 93% khối** — cả
    đoạn bị bôi, tức đúng cái thất bại nghiên cứu mô tả, chỉ ở mức đoạn.

    Nhưng trần này chỉ áp **từ vệt thứ hai**: khối 400 ký tự có một câu 250 ký tự
    thì tự nó đã 62%, áp cho cả vệt đầu là bỏ mất 15 trên 40 câu (đã đo). Sau khi
    sửa, khối tóm tắt còn 2 vệt phủ 46%.
    """
    from server import prompts

    assert prompts.INSIGHT_PER_BLOCK >= 1
    assert 0 < prompts.INSIGHT_BLOCK_FRAC < 1

    # mô phỏng đúng luật trong `mark_insights`
    def nhan(da, quote_len, block_len):
        if len(da) >= prompts.INSIGHT_PER_BLOCK:
            return False
        phu = sum(da) + quote_len
        return not (da and phu > block_len * prompts.INSIGHT_BLOCK_FRAC)

    assert nhan([], 250, 400), "câu đầu tiên phải được nhận dù dài"
    assert not nhan([250], 200, 400), "vệt thứ hai làm tô quá trần thì phải bỏ"
    assert nhan([100], 100, 1000), "khối dài thì vệt thứ hai vẫn được"
    assert not nhan([100] * prompts.INSIGHT_PER_BLOCK, 10, 100000), "quá số vệt thì bỏ"


def test_noi_het_cau_va_khop_nguyen_van():
    """Model chỉ chép mấy từ đầu câu; server nối tới hết câu và tha lệch KIỂU CHỮ.

    Đo trên arXiv:2602.15922: trong 10 câu bị loại, 4 câu khai mã khối **không
    tồn tại** và 2 câu khai nhầm khối — chỉ 3 câu là chép lệch thật. Nên mã khối
    do model khai chỉ là gợi ý, còn câu trích mới là thứ phải có thật.
    """
    from server.pipeline import noi_het_cau, tim_nguyen_van, _do_khoi

    t = "Câu một ở đây. Chúng tôi giới thiệu DreamZero, một mô hình mới. Câu ba."
    assert noi_het_cau("Chúng tôi giới thiệu", t) == \
        "Chúng tôi giới thiệu DreamZero, một mô hình mới."
    assert noi_het_cau("Không có", t) == ""
    # số thập phân và ngoặc đóng không được cắt câu sớm
    assert noi_het_cau("Đạt", "Đạt 62,3% EM (xem Bảng 1). Sau.") == "Đạt 62,3% EM (xem Bảng 1)."

    # tha lệch kiểu chữ, nhưng TRẢ VỀ chuỗi gốc để client còn neo được
    goc = "Ví dụ — nếu có “áo sơ mi” thì  mô hình  gấp được."
    assert tim_nguyen_van("Ví dụ - nếu", goc) == "Ví dụ — nếu"
    assert tim_nguyen_van('có "áo sơ mi" thì', goc) == "có “áo sơ mi” thì"
    assert tim_nguyen_van("thì mô hình gấp", goc) == "thì  mô hình  gấp"
    assert tim_nguyen_van("nếu SỐ liệu khác hẳn", goc) == "", "lệch CHỮ thì vẫn phải loại"

    # dò lại khối khi model khai sai mã
    by = {"b1": "Câu một.", "b2": "Chúng tôi định nghĩa độ chi tiết.", "b3": "Câu một."}
    assert _do_khoi("Chúng tôi định nghĩa", by) == "b2"
    assert _do_khoi("Câu một", by) == "", "nằm ở hai khối thì bỏ, không đoán"
    assert _do_khoi("không ở đâu", by) == ""


# ------------------------------------------- nhận nguồn: chặn trước khi bóc

def test_nhan_dinh_dang_that_tu_noi_dung_khong_tu_duoi_file():
    """Đuôi file không đáng tin — phải đọc chữ ký đầu file.

    `paper.docx` từng lọt qua và được đọc như văn bản: ra **một khối 9.800 ký tự**
    `PK…[Content_Types].xml…`, có báo giá dịch $0,009–0,021, và được lưu vĩnh viễn
    vào kho thành "(không tiêu đề)". Chặn phải xảy ra **trước** khi bóc và trước
    khi tạo bất cứ bản ghi nào.
    """
    from server.parser import sniff, kiem_nguon, NguonHong

    assert sniff(b"%PDF-1.7 ...") == "pdf"
    assert sniff(b"PK\x03\x04" + b"x" * 50) == "zip"
    assert sniff("# Tiêu đề\n\nĐoạn văn tiếng Việt.".encode()) == "text"
    assert sniff(b"   \n ") == "rong"
    assert sniff(bytes(range(256))) == "nhiphan"

    for data, name in ((b"PK\x03\x04" + b"x" * 50, "paper.docx"), (b"", "empty.txt")):
        with pytest.raises(NguonHong):
            kiem_nguon(data, name)

    # Mang tên `.pdf` mà ruột là text: người dùng đang tin mình vừa tải PDF lên,
    # nên bóc im lặng như văn bản rồi báo "không có chữ nào" là trả lời lạc đề.
    with pytest.raises(NguonHong, match="đuôi .pdf"):
        kiem_nguon(b"day khong phai pdf", "fake.pdf")

    # còn .txt/.md thật thì phải đi qua
    assert kiem_nguon(b"# Bai\n\nNoi dung", "paper.md") == "text"


def test_boc_markdown_that_su_chu_khong_doan_bang_heuristic():
    """`.md` phải ra HEADING / EQUATION / TABLE / CODE / REF, và tiêu đề sạch `#`.

    Màn hình ghi là nhận `.md`, nhưng đường cũ cắt theo dòng trống rồi đoán bằng
    heuristic của văn bản thường. Hậu quả đo được: tên bài giữ nguyên dấu `#` ở
    cả header lẫn danh sách kho, `## Abstract` bị xếp vào "khối đáng ngờ" và tự
    bỏ tick, bảng và khối mã bị dồn thành một dòng, `$$…$$` không ai nhận ra.
    """
    from server.parser import parse_text

    md = (
        "# Sparse Mixture Routing\n\n"
        "## Abstract\n\n"
        "Một đoạn văn có $\\alpha = 1$ ở giữa câu.\n\n"
        "$$\n\\sum_{i=1}^{E} g_i = 1\n$$\n\n"
        "## 2. Phương pháp\n\n"
        "| Model | Acc |\n|---|---|\n| Dense | 84.1 |\n\n"
        "```python\ndef route(x):\n    return x\n```\n\n"
        "## References\n\n"
        "[1] Vaswani et al. Attention is all you need. NeurIPS 2017.\n"
    )
    title, blocks, _ = parse_text(md, name="paper.md")

    assert title == "Sparse Mixture Routing", "dấu # phải bị bỏ khỏi tên bài"
    loai = {b.type for b in blocks}
    assert {"heading", "para", "equation", "table", "code", "reference"} <= loai

    # heading phải còn cờ dịch — trước đây nó bị xếp là rác rồi tự bỏ tick
    abst = next(b for b in blocks if b.text == "Abstract")
    assert abst.type == "heading" and abst.translate

    # bảng và mã giữ NGUYÊN xuống dòng (dồn một dòng là mất cấu trúc) và không dịch
    bang = next(b for b in blocks if b.type == "table")
    assert "\n" in bang.text and not bang.translate
    code = next(b for b in blocks if b.type == "code")
    assert "def route" in code.text and not code.translate

    # `$…$` và `$$…$$` quy về `\(…\)` — dạng mà `mathTeX` đã dựng được và có test
    assert any("\\(" in b.text for b in blocks if b.type == "para")
    assert next(b for b in blocks if b.type == "equation").text.startswith("\\(")

    # văn bản THƯỜNG vẫn đi đường cũ, không bị Markdown hoá
    t2, b2, _ = parse_text("Tiêu đề bài\n\nMột đoạn văn bình thường.\n")
    assert t2 == "Tiêu đề bài" and all(b.type != "code" for b in b2)


def test_gop_tieu_de_nhieu_dong_nhung_khong_nuot_ten_tac_gia():
    """Tiêu đề bài báo hay nằm trên nhiều dòng cùng cỡ chữ — phải gộp lại.

    Lấy đúng một khối thì mất phần còn lại, và phần mất **không im lặng**: nó
    thành khối `meta` rác ngay đầu bài. Đo trên arXiv:2604.00965v1, tiêu đề ba
    dòng small-caps: đường heuristic chỉ lấy được
    "UNDERSTANDING TRANSFORMERS AND ATTENTION" — một phần ba.

    Chạy lại trên 10 PDF trong `data/`: sửa được ba bài bị cụt (trong đó có bài
    chỉ còn "Question Answering" mà CLAUDE.md ghi là ca đau nhất) và **không
    bài nào bị nối thừa**.

    Điều kiện khe dọc là thứ giữ nó không nuốt tên tác giả — tên tác giả cũng
    thường to hơn thân bài.
    """
    from server.parser import gop_tieu_de, TIEU_DE_DONG_TOI_DA

    def dong(i, text, size, y0, h=14):
        return {"key": i, "text": text, "size": size, "y0": y0, "y1": y0 + h}

    # ba dòng tiêu đề cùng cỡ, sát nhau -> gộp
    phan = [dong(0, "UNDERSTANDING TRANSFORMERS AND ATTENTION", 17.2, 98),
            dong(1, "MECHANISMS: AN INTRODUCTION FOR APPLIED", 17.2, 118),
            dong(2, "MATHEMATICIANS", 17.2, 138),
            dong(3, "A PREPRINT", 10.0, 179),
            dong(4, "Michel Fabrice Serret", 10.0, 208)]
    t, dung = gop_tieu_de(phan, 0)
    assert t.endswith("MATHEMATICIANS") and t.startswith("UNDERSTANDING")
    assert dung == {0, 1, 2}, "phải dừng trước 'A PREPRINT' vì cỡ chữ khác"

    # cùng cỡ nhưng CÁCH XA -> không nối: tên tác giả cũng to hơn thân bài
    xa = [dong(0, "Tiêu đề bài", 17.0, 98),
          dong(1, "Nguyễn Văn A", 17.0, 260)]
    t2, d2 = gop_tieu_de(xa, 0)
    assert t2 == "Tiêu đề bài" and d2 == {0}

    # không nối quá trần, kể cả khi mọi dòng đều hợp lệ
    nhieu = [dong(i, f"dòng {i}", 17.0, 100 + i * 18) for i in range(8)]
    t3, d3 = gop_tieu_de(nhieu, 0)
    assert len(d3) == TIEU_DE_DONG_TOI_DA
