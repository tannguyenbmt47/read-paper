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
