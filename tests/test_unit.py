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
