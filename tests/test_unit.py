"""Test đơn vị cho phần logic thuần — không cần server, không gọi model.

Ưu tiên những chốt chặn mà hỏng thì im lặng: bộ soát số liệu trên slide, bộ đo
tràn khung, bộ bóc Mermaid, và bộ dựng chỉ số trên/dưới. Đó là chỗ sai không ai
nhìn ra bằng mắt.
"""
from __future__ import annotations

import pytest

from server import pipeline
from server.parser import Block


# --------------------------------------------------------------- chốt số liệu

def _doc(text: str = "Mô hình đạt 42,5 điểm trên tập 2WikiMQA với 1.000 câu hỏi."):
    return {
        "id": "d1",
        "blocks": [{"id": "b1", "type": "para", "text": text, "translate": True}],
        "translations": {"b1": text},
        "brief": {"glossary": []},
        "slides": {},
    }


# ------------------------------------------------------------ slide v2

def _doc_hinh():
    d = _doc()
    d["blocks"] += [
        {"id": "b2", "type": "caption", "text": "Table 2: Main results.", "figure": "b2"},
        {"id": "b3", "type": "equation", "text": "x = y", "figure": "b3"},
    ]
    return d


def test_slide_vai_la_bi_bo():
    from server import slide
    assert slide.chuan_hoa(_doc(), {"vai": "the_pastel", "tieu_de": "x"}) is None


def test_slide_bo_dau_cham_phay_va_viet_hoa():
    """Skill văn phong cấm dấu chấm phẩy — model vẫn hay viết, nên dọn cơ học."""
    from server import slide
    s = slide.chuan_hoa(_doc(), {"vai": "y_tuong", "tieu_de": "ý tưởng gọn.",
                                 "cau": "gom bằng chứng trước; rồi mới trả lời"})
    assert ";" not in s["cau"] and s["cau"] == "Gom bằng chứng trước. Rồi mới trả lời"
    assert s["tieu_de"] == "Ý tưởng gọn"
    # Ký hiệu không bị viết hoa: τ hoa là một ký hiệu khác
    assert slide._sach("τ=0.1") == "τ=0.1" and slide._sach("x_{t} tăng dần") == "x_{t} tăng dần"
    assert slide._sach("multi-hop cần nhiều bước") == "Multi-hop cần nhiều bước"
    assert slide._sach("độ trễ tăng") == "Độ trễ tăng"
    # Dấu tổ hợp lơ lửng bị bỏ, dấu tiếng Việt (kể cả dạng tách NFD) giữ nguyên
    assert slide._sach("632 ± \u0301251") == "632 ± 251"
    assert slide._sach("ca\u0301ch la\u0300m") == "Cách làm"
    # Từ có chữ hoa giữa giữ nguyên
    assert slide._sach("iRAG lặp lại") == "iRAG lặp lại"


def test_slide_hinh_quy_ve_ma_khoi_va_bo_cong_thuc():
    """Model chọn hình theo mã KHỐI. Công thức có ảnh nhưng không phải bằng chứng."""
    from server import slide
    d = _doc_hinh()
    assert slide.chuan_hoa(d, {"vai": "bang_chung", "tieu_de": "t", "hinh": "b2"})["hinh"] == "b2"
    s = slide.chuan_hoa(d, {"vai": "bang_chung", "tieu_de": "t", "hinh": "b3"})
    assert s["hinh"] == "" and any("chọn hình" in c for c in s["canh_bao"])
    s = slide.chuan_hoa(d, {"vai": "bang_chung", "tieu_de": "t", "hinh": "khongco"})
    assert s["hinh"] == "" and any("không có trong bài" in c for c in s["canh_bao"])


def test_slide_so_bia_bi_bat_so_that_khong():
    from server import slide
    s = slide.chuan_hoa(_doc(), {"vai": "so_lieu", "tieu_de": "Đạt 42,5 điểm",
                                 "gia_tri": "42,5", "nhan": "F1", "moc": "so với 39,9",
                                 "nguon": ["b1"]})
    assert s["canh_bao"] == ["Số không có ở đâu trong bài: 39,9."]
    # Nguồn khai bừa thì bị lọc, không làm hỏng phép soát
    s = slide.chuan_hoa(_doc(), {"vai": "so_lieu", "tieu_de": "Đạt 42,5 điểm",
                                 "gia_tri": "42,5", "nguon": ["b1", "b99"]})
    assert s["nguon"] == ["b1"] and not s["canh_bao"]


def test_slide_bo_o_so_bia_tren_slide_bang_chung():
    """Model không thấy ảnh bảng nên hay "đọc" số từ bảng. Ô số là thứ to nhất
    slide — số bịa ở đó thì BỎ ô, không chỉ cảnh báo."""
    from server import slide
    s = slide.chuan_hoa(_doc_hinh(), {"vai": "bang_chung", "tieu_de": "t", "hinh": "b2",
        "so": [{"gia_tri": "42,5", "nhan": "F1", "moc": "trên 2WikiMQA"},
               {"gia_tri": "10.1", "nhan": "F1 giảm", "moc": "61.4 xuống 51.3"}]})
    assert [x["gia_tri"] for x in s["so"]] == ["42,5"]
    assert any("Đã bỏ ô số" in c and "61.4" in c for c in s["canh_bao"])


def test_slide_vi_du_minh_hoa_duoc_giu_co():
    from server import slide
    assert slide.chuan_hoa(_doc(), {"vai": "vi_du", "tieu_de": "t", "minh_hoa": True})["minh_hoa"]
    assert slide.chuan_hoa(_doc(), {"vai": "vi_du", "tieu_de": "t"})["minh_hoa"] is False


def test_slide_sap_lai_dong_lai_cuoi_va_khong_lap_vai():
    from server import slide
    bo = [{"vai": v} for v in ("dong_lai", "van_de", "co_che", "co_che", "bang_chung",
                               "bang_chung", "gioi_han")]
    ra = [s["vai"] for s in slide._sap_lai(bo)]
    assert ra[-1] == "dong_lai" and ra.count("dong_lai") == 1
    # Hai `co_che` liền nhau được giữ khi chặng "Cách làm" không còn slide nào khác
    # để chen vào: giữ đúng chặng quan trọng hơn, và bộ vẽ tự đổi dáng vai lặp.
    chang = {v: i for i, (_, vs) in enumerate(slide.CHANG) for v in vs}
    assert [chang[v] for v in ra] == sorted(chang[v] for v in ra)
    assert sorted(ra) == sorted(s["vai"] for s in bo)


def test_slide_sap_theo_chang_de_khop_lo_trinh():
    from server import slide
    bo = [{"vai": v} for v in ("van_de", "bang_chung", "gioi_han", "so_lieu", "dong_lai")]
    assert [s["vai"] for s in slide._sap_lai(bo)] == [
        "van_de", "bang_chung", "so_lieu", "gioi_han", "dong_lai"]
    # Hai slide con số liền nhau: tách bằng slide bằng chứng CÙNG chặng, không
    # đẩy qua slide giới hạn
    bo = [{"vai": v} for v in ("bang_chung", "bang_chung", "so_lieu", "so_lieu", "gioi_han")]
    ra = [s["vai"] for s in slide._sap_lai(bo)]
    assert ra[-1] == "gioi_han" and ra.index("gioi_han") > max(i for i, v in enumerate(ra) if v == "so_lieu")


def test_slide_thieu_co_che_thi_bao_ca_bo():
    from server import slide
    bo = [{"vai": "van_de", "canh_bao": []}, {"vai": "bang_chung", "canh_bao": []}]
    slide._soat_ca_bo(bo)
    assert any("cơ chế" in c for c in bo[0]["canh_bao"])
    bo = [{"vai": "van_de", "canh_bao": []}, {"vai": "co_che", "buoc": [{"ten": "a"}], "canh_bao": []}]
    slide._soat_ca_bo(bo)
    assert bo[0]["canh_bao"] == []


def test_slide_cong_thuc_chi_gan_anh_cong_thuc():
    """Ảnh công thức chỉ cho vai `cong_thuc`; hình/bảng cho mọi vai khác."""
    from server import slide
    d = _doc_hinh()
    assert slide.chuan_hoa(d, {"vai": "cong_thuc", "tieu_de": "t", "hinh": "b3"})["hinh"] == "b3"
    assert slide.chuan_hoa(d, {"vai": "cong_thuc", "tieu_de": "t", "hinh": "b2"})["hinh"] == ""
    assert slide.chuan_hoa(d, {"vai": "co_che", "tieu_de": "t", "hinh": "b3"})["hinh"] == ""


def test_slide_bang_doi_chieu_va_bieu_do_duoc_chuan_hoa():
    from server import slide
    s = slide.chuan_hoa(_doc(), {"vai": "so_sanh", "tieu_de": "t", "cot": ["A", "B", "Ours"],
        "hang": [{"tieu_chi": "giữ nhiều nhánh", "o": ["không", "có"]}, {"o": ["x"]}]})
    assert s["cot"] == ["A", "B", "Ours"] and len(s["hang"]) == 1
    assert len(s["hang"][0]["o"]) == 3  # ô thiếu được bù cho đủ số cột
    s = slide.chuan_hoa(_doc(), {"vai": "so_lieu", "tieu_de": "Đạt 42,5 điểm", "gia_tri": "42,5",
        "so_sanh": [{"nhan": "Bài", "gia_tri": "42,5", "cua_bai": True}, {"nhan": "Cũ", "gia_tri": "38,8"}],
        "nguon": ["b1"]})
    assert s["so_sanh"][0]["cua_bai"] and any("38,8" in c for c in s["canh_bao"])


def test_slide_mot_hinh_chi_mot_cho_uu_tien_co_che():
    from server import slide
    bo = [{"vai": "y_tuong", "hinh": "b43"}, {"vai": "co_che", "hinh": "b43"},
          {"vai": "co_che", "hinh": "b43"}, {"vai": "bang_chung", "hinh": "b69"}]
    slide._mot_hinh_mot_cho(bo)
    assert [s["hinh"] for s in bo] == ["", "b43", "", "b69"]


def test_slide_trich_doan_lay_nguyen_van_ban_dich_va_tach_o_ranh_gioi_cau():
    """Trích đoạn: model chỉ chọn MÃ đoạn, chữ là bản dịch thật — không tầng tóm
    tắt nào làm rơi ý. Đoạn dài tách sang slide sau, không cắt bỏ câu nào."""
    from server import slide
    d = _doc()
    cau = [f"Câu thứ {i} nói về cơ chế lọc bằng chứng của hệ thống." for i in range(40)]
    d["blocks"].append({"id": "b9", "type": "para", "text": "x"})
    d["translations"]["b9"] = " ".join(cau)
    s = slide.chuan_hoa(d, {"vai": "doan_dich", "tieu_de": "t", "doan": ["b9", "khongco"],
                            "nhan_manh": ["Câu thứ 3 nói về", "không có trong đoạn"]})
    assert s["trich"][0]["chu"] == d["translations"]["b9"] and s["nguon"] == ["b9"]
    assert s["nhan_manh"] == ["Câu thứ 3 nói về"]
    phan = slide._tach_trich([s])
    assert len(phan) > 1 and all(p["tiep"][1] == len(phan) for p in phan)
    assert " ".join(p["trich"][0]["chu"] for p in phan) == d["translations"]["b9"]
    assert all(len(p["trich"][0]["chu"]) <= slide.TRAN_TRICH for p in phan)
    assert slide.chuan_hoa(d, {"vai": "doan_dich", "tieu_de": "t", "doan": ["khongco"]}) is None


def test_slide_bo_chi_tiet_cat_muc_3_truoc_khong_bao_gio_cat_muc_1():
    from server import slide
    khung = [{"vai": "doan_dich", "_md": md, "_nhom": i} for i, md in enumerate([1, 3, 2, 3, 1, 2])]
    khung += [{"vai": "doan_dich", "_md": 3, "_nhom": 1}]          # phần tách của nhóm 1
    ra = slide._cat_theo_muc_do(khung, 4)
    assert [x["_nhom"] for x in ra if x["_md"] == 1] == [0, 4]
    assert not any(x["_nhom"] == 1 for x in ra)                      # bỏ cả nhóm, kể cả phần tách
    assert len(ra) <= round(4 * 1.15) or all(x["_md"] == 1 for x in ra)


def test_json_nhay_doi_thua_duoc_va():
    """V4 Flash viết `"hong": ""Mạng nhỏ…` — một ký tự làm hỏng cả mẻ 8 slide."""
    from server import llm
    assert llm.extract_json('{"a": ""đầu thừa", "b": "cuối."", "c": [""x"", "z"]}') == {
        "a": "đầu thừa", "b": "cuối.", "c": ["x", "z"]}
    assert llm.extract_json('{"a": "", "": "", "l": ["", "y"]}') == {"a": "", "": "", "l": ["", "y"]}


def test_slide_trich_doan_keo_theo_cong_thuc_chen_giua(monkeypatch):
    """Công thức là khối riêng: chỉ chép chữ thì slide dừng ở "giáo viên sinh ra:"
    rồi bỏ trống (6 chỗ trên bộ CIRAG). Giờ công thức chen giữa, và công thức ngay
    sau đoạn kết bằng dấu hai chấm, đi theo trích đoạn — không bị tách khỏi câu dẫn."""
    from server import slide, store
    monkeypatch.setattr(store, "image_path", lambda d, f: "x.png")
    d = _doc()
    d["blocks"] += [{"id": "b5", "type": "para", "text": "a"},
                    {"id": "b6", "type": "equation", "text": "y=f(x)", "figure": "b6"},
                    {"id": "b7", "type": "para", "text": "b"},
                    {"id": "b8", "type": "equation", "text": "z=g(y)", "figure": "b8"},
                    {"id": "b9", "type": "para", "text": "c"}]
    d["translations"].update({"b5": "Mô hình sinh ra:", "b7": "Sau đó bộ truy xuất trả về:", "b9": "Hết."})
    s = slide.chuan_hoa(d, {"vai": "doan_dich", "tieu_de": "t", "doan": ["b5", "b7"]})
    assert [(t["id"], bool(t.get("cong_thuc"))) for t in s["trich"]] == [
        ("b5", False), ("b6", True), ("b7", False), ("b8", True)]
    assert "b9" not in [t["id"] for t in s["trich"]]   # đoạn model không chọn thì không kéo


def test_slide_o_so_khong_co_chu_so_bi_bo_va_cong_trinh_lien_quan_doi_len():
    from server import slide
    s = slide.chuan_hoa(_doc(), {"vai": "bang_chung", "tieu_de": "t",
                                 "so": [{"gia_tri": "Giảm mạnh", "nhan": "F1"}, {"gia_tri": "42,5", "nhan": "F1"},
                                        {"gia_tri": "F1 cao nhất"}, {"gia_tri": "Trung bình hơn 42,5 F1"}]})
    assert [x["gia_tri"] for x in s["so"]] == ["42,5"]
    d = _doc()
    d["blocks"] = [{"id": "h1", "type": "heading", "text": "Method"}, {"id": "p1", "type": "para", "text": "m"},
                   {"id": "h2", "type": "heading", "text": "Related Work"}, {"id": "p2", "type": "para", "text": "r"}]
    khung = [{"vai": "van_de"}, {"vai": "khoang_trong"}, {"vai": "co_che"},
             {"vai": "doan_dich", "trich": [{"id": "p1", "chu": "m"}]},
             {"vai": "doan_dich", "trich": [{"id": "p2", "chu": "r"}]}]
    ra = slide._doi_cong_trinh_lien_quan(d, khung)
    # Chặng 2 (hướng tiếp cận): đoạn công trình liên quan đứng TRƯỚC khoảng trống —
    # khoảng trống là điều rút ra từ các hướng ấy.
    assert [x["vai"] for x in ra][:3] == ["van_de", "doan_dich", "khoang_trong"]
    assert ra[1]["trich"][0]["id"] == "p2"


def test_slide_vot_slide_tron_ven_khoi_json_lap_suy_bien():
    """V4 Flash: `"y_chinh":": "…"` rồi leo thang thành `":":":…` tới hết trần
    token. Các slide viết trọn trước chỗ hỏng phải được giữ lại."""
    from server import llm, slide
    raw = ('{"slides": [{"id": "t1", "loi_noi": "a"},\n {"id": "t2", "y_chinh":": "Chọn một", "loi_noi": "b"},'
           '\n {"id": "t3", "y_chinh":":":":":":":":":')
    assert [x["id"] for x in slide._vot_slides(raw)] == ["t1", "t2"]
    assert llm.extract_json('{"y_chinh":": "Chọn một", "a": ":", "b": ": x"}') == {
        "y_chinh": "Chọn một", "a": ":", "b": ": x"}


def test_gia_ten_tat_lay_muc_cao_hon_cua_model_goc(monkeypatch):
    """`~…-latest` báo giá ra $0,079/triệu token, model gốc $1,28: ước giá từng thấp
    hơn thực chi ~16 lần. Tra cả hai, lấy mức cao hơn."""
    import asyncio
    from server import llm

    async def ds():
        return [{"id": "~deepseek/deepseek-v4-flash-latest", "pricing": {"prompt": "0.000000008", "completion": "0.000000079"}},
                {"id": "deepseek/deepseek-v4-flash", "pricing": {"prompt": "0.000000013", "completion": "0.00000128"}}]
    monkeypatch.setattr(llm, "list_models", ds)
    vao, ra = asyncio.run(llm.gia_model("~deepseek/deepseek-v4-flash-latest"))
    assert abs(ra - 1.28e-6) < 1e-12 and abs(vao - 1.3e-8) < 1e-15
    assert asyncio.run(llm.gia_model("khong/co")) is None


def test_slide_sau_chang_khai_niem_di_theo_slide_dung_truoc():
    """Bố cục sáu chặng; khái niệm và trích đoạn không thuộc chặng nào nên đứng
    đúng chỗ model đặt (ngay trước slide dùng thuật ngữ), không bị dồn xuống cuối."""
    from server import slide
    assert [t for t, _ in slide.CHANG] == ["Bài toán", "Hướng tiếp cận", "Phương pháp",
                                           "Thí nghiệm", "Kết quả & ablation", "Bổ sung"]
    bo = [{"vai": v} for v in ("dong_lai", "van_de", "huong_nc", "huong_nc", "khoang_trong",
                               "khai_niem", "co_che", "bang_chung", "thiet_lap", "gioi_han")]
    ra = [s["vai"] for s in slide._sap_lai(bo)]
    assert ra == ["van_de", "huong_nc", "huong_nc", "khoang_trong", "khai_niem", "co_che",
                  "thiet_lap", "bang_chung", "gioi_han", "dong_lai"]
    s = slide.chuan_hoa(_doc(), {"vai": "khai_niem", "tieu_de": "t", "thuat_ngu": "KD (Knowledge Discriminator)",
                                 "dinh_nghia": "mô hình lọc triple"})
    assert s["thuat_ngu"] == "KD (Knowledge Discriminator)" and s["dinh_nghia"] == "Mô hình lọc triple"


def test_slide_viet_tat_chua_giai_nghia_bi_gan_co_va_danh_sach_chuoi():
    from server import slide
    d = _doc()
    d["title"] = "CIRAG: a method"
    bo = [{"vai": "co_che", "tieu_de": "KD lọc triple bằng LLM trong CIRAG", "canh_bao": []},
          {"vai": "khai_niem", "tieu_de": "t", "thuat_ngu": "ACMG", "canh_bao": []},
          {"vai": "co_che", "tieu_de": "ACMG chọn mức ngữ cảnh, TD (Trajectory Distillation) chưng cất", "canh_bao": []}]
    slide._soat_viet_tat(d, bo)
    assert bo[0]["canh_bao"] and "KD" in bo[0]["canh_bao"][0] and "LLM" not in bo[0]["canh_bao"][0]
    assert not bo[2]["canh_bao"]
    # tên đầy đủ có trong bảng thuật ngữ hoặc đã xuất hiện trước đó: tự chèn, không cảnh báo
    d["brief"] = {"glossary": [{"en": "KD", "vi": "viết tắt của Knowledge Discriminator"}]}
    bo = [{"vai": "co_che", "tieu_de": "Trajectory Distillation chưng cất", "buoc": [{"ten": "a", "mo_ta": "KD lọc triple"}], "canh_bao": []},
          {"vai": "bang_chung", "tieu_de": "Bỏ TD thì giảm", "ket_luan": "TD cần thiết", "canh_bao": []}]
    slide._soat_viet_tat(d, bo)
    assert bo[0]["buoc"][0]["mo_ta"] == "KD (Knowledge Discriminator) lọc triple" and not bo[0]["canh_bao"]
    assert bo[1]["ket_luan"] == "TD (Trajectory Distillation) cần thiết" and not bo[1]["canh_bao"]
    # model trả chuỗi thay cho danh sách: không được lặp từng ký tự
    s = slide.chuan_hoa(_doc(), {"vai": "huong_nc", "tieu_de": "t", "dai_dien": "IRCoT, FLARE"})
    assert s["dai_dien"] == ["IRCoT", "FLARE"]


def test_slide_thieu_tieu_de_thi_dung_tam_va_vi_du_dai_bo_hinh():
    from server import slide
    s = slide.chuan_hoa(_doc(), {"vai": "khoang_trong", "cach_cu": "Các phương pháp cũ chọn một đường. Câu sau.",
                                 "hong": "x"})
    assert s["tieu_de"] == "Các phương pháp cũ chọn một đường" and any("tiêu đề" in c for c in s["canh_bao"])
    s = slide.chuan_hoa(_doc_hinh(), {"vai": "vi_du", "tieu_de": "t", "hinh": "b2",
                                      "buoc": [{"ten": str(i), "mo_ta": "m"} for i in range(4)]})
    assert s["hinh"] == "" and len(s["buoc"]) == 4


def test_slide_tieu_chi_mong_muon_phai_co_can_cu_trong_bai():
    """Người dùng chê "tự nhiên cần mạng lớn, dẫn chứng đâu": tiêu chí nào cũng kèm
    căn cứ trong bài; mã nguồn bịa bị bỏ, thiếu căn cứ thì cảnh báo."""
    from server import slide
    s = slide.chuan_hoa(_doc(), {"vai": "yeu_cau", "tieu_de": "t", "tieu_chi": [
        {"ten": "Sức biểu diễn", "vi_sao": "cần", "can_cu": "bài nói mạng lớn nén tốt", "nguon": "b1"},
        {"ten": "Dễ tối ưu", "vi_sao": "cần", "nguon": "bia"}]})
    assert s["tieu_chi"][0]["can_cu"] == "Bài nói mạng lớn nén tốt" and s["tieu_chi"][0]["nguon"] == "b1"
    assert s["tieu_chi"][1]["nguon"] == "" and "b1" in s["nguon"]
    assert any("căn cứ" in c for c in s["canh_bao"])


def test_slide_dinh_dang_cu_coi_nhu_chua_co():
    """Bộ slide v1 (deck/outline) không vẽ được bằng bộ vẽ mới — coi như chưa có,
    đừng để giao diện vỡ."""
    from server import slide
    assert slide.lay({"slides": {"deck": [{"id": "s1"}]}})["bo"] == []
    assert slide.lay({"slides": {"v": 2, "bo": [{"id": "s1"}]}})["bo"] == [{"id": "s1"}]


def test_slide_ma_isalnum():
    from server import slide
    assert all(s["id"].isalnum() for s in slide._danh_ma([{}, {}, {}]))


# --------------------------------------------------------------- bóc Mermaid


def test_nhan_dung_khong_bi_bao_sai():
    """`A["nhãn"]` là dạng ĐÚNG mà DIAGRAM_RULES yêu cầu."""
    assert not pipeline._bad_mermaid_labels('flowchart TD\n A["ổn"] --> B["ổn"]')
    assert pipeline._bad_mermaid_labels('flowchart TD\n A["có "trích" bên trong"] --> B["x"]')


# ------------------------------------------------------- chỉ số trên/dưới


# ------------------------------------------------------------ khối và mẻ dịch

def test_khoi_an_khong_vao_me_dich():
    from server.parser import chunk_blocks
    bs = [Block(id=f"b{i}", type="para", text="chữ " * 200) for i in range(6)]
    truoc = sum(len(c) for c in chunk_blocks(bs))
    bs[0].hidden = True
    sau = sum(len(c) for c in chunk_blocks(bs))
    assert sau == truoc - 1


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


def test_chu_thich_chan_khong_bi_dan_vao_doan_van():
    """`_CONT` nói "chỉ số không bao giờ mở đầu một đoạn văn" — đúng với toán,
    SAI với chú thích chân trang, vốn mở đầu đúng bằng một chỉ số trên.

    Đo trên bài CIRAG: `^{*} Corresponding author..` và `^{1}Our code can be
    found via github.com/…` bị dán vào cuối đoạn mở bài. Vào rồi thì
    `mark_noise` không bắt được nữa (không còn là khối riêng), nên người dùng
    trả tiền dịch chúng và đoạn mở bài kết thúc bằng một địa chỉ GitHub.
    """
    from server.parser import _la_chu_thich_chan

    # chú thích chân trang — KHÔNG được nối vào khối trước
    for t in ("^{*} Corresponding author..",
              "^{1}Our code can be found via https://github.com/ 52566rz/CIRAG.",
              "^{2} Equal contribution.",
              "^{1School of Computer Science and Engineering, Northeastern University}a@b.cn"):
        assert _la_chu_thich_chan(t), f"bỏ sót chú thích: {t[:40]}"

    # chỉ số toán thật — PHẢI nối, nếu không câu bị chẻ đôi giữa mệnh đề
    for t in ("_{i=1}, the objective function is",
              "_{k=1}.",
              "^{N} denotes the set",
              "^{2} + b^{2}",
              "_{t} is the hidden state"):
        assert not _la_chu_thich_chan(t), f"bắt oan chỉ số toán: {t[:40]}"


def test_noi_lai_trich_dan_bi_cat_qua_ranh_gioi_cot():
    """Trích dẫn bị cắt đôi ở ranh giới cột thì khối sau mở đầu bằng NĂM, không
    phải chữ thường — nên `_CONT_LOWER` không bắt được.

    Đo trên CIRAG: đoạn mở bài kết thúc ở `…Iterative RAG (iRAG) (Trivedi et
    al.,` (6 ngoặc mở, 5 đóng), nhảy qua chú thích Hình 1, rồi sang `2023; Asai
    et al., 2024) is introduced by…`. Người đọc nhận hai mẩu: một mẩu cụt giữa
    trích dẫn, một mẩu mở đầu bằng con số không rõ của ai.
    """
    from server.parser import Block, _stitch_runon, _ngoac_ho

    assert _ngoac_ho("Iterative RAG (iRAG) (Trivedi et al.,")
    assert not _ngoac_ho("as single-step retrieval often fails (Shao et al., 2023).")

    bs = [
        Block(id="b1", type="para",
              text="Iterative RAG (iRAG) (Trivedi et al.,"),
        Block(id="b2", type="caption", text="Figure 1: Challenges."),
        Block(id="b3", type="para",
              text="2023; Asai et al., 2024) is introduced by retrieving in steps."),
    ]
    assert _stitch_runon(bs) == 1
    assert "(Trivedi et al., 2023; Asai et al., 2024) is introduced" in bs[0].text

    # Nhưng một đoạn mới thật sự mở đầu bằng số thì KHÔNG được nối — khối trước
    # phải còn ngoặc hở mới tính.
    bs2 = [
        Block(id="b1", type="para", text="Chúng tôi đo trên ba bộ dữ liệu"),
        Block(id="b2", type="para", text="2024, nhóm khác công bố kết quả tương tự."),
    ]
    assert _stitch_runon(bs2) == 0


def test_mang_set_trang_dau_khong_lam_trang_hai_cot_thanh_mot_cot():
    """`_page_columns` phải cân theo CHIỀU CAO, không theo số khối.

    Trang đầu một bài hai cột luôn có măng-sét trải hết bề ngang — tên bài, mấy
    dòng tác giả, dòng cơ quan — mà trang đầu lại là trang có ít vùng nhất. Đo
    trên bài CIRAG: 4 khối cắt ngang trên 13 vùng = 31%, vượt ngưỡng 25%, nên
    trang bị chấm MỘT cột.

    Chấm một cột thì sắp thuần theo `y`, mà sắp thuần theo `y` trên trang hai
    cột là cài răng lược hai cột vào nhau: `1 Introduction` và đoạn mở bài hiện
    ra SAU chú thích Hình 1 và sau chính đoạn nối tiếp của nó. Không chỗ nào
    báo lỗi.
    """
    from server.parser import _page_columns

    PW = 595.0
    # Măng-sét: 4 dòng trải hết bề ngang, mỗi dòng cao ~15–25pt.
    mang_set = [(71, 75, 524, 100), (175, 128, 422, 143),
                (157, 143, 439, 158), (81, 157, 514, 172)]
    # Thân bài: hai cột chữ cao, không cắt qua trục giữa.
    trai = [(71, 200 + i * 60, 288, 255 + i * 60) for i in range(5)]
    phai = [(307, 200 + i * 60, 524, 255 + i * 60) for i in range(5)]
    assert _page_columns(mang_set + trai + phai, PW) == 2, \
        "măng-sét ngắn ở đầu trang không được làm trang hai cột thành một cột"

    # Trang MỘT cột thật: mọi khối đều trải hết bề ngang.
    mot_cot = [(71, 100 + i * 50, 524, 145 + i * 50) for i in range(10)]
    assert _page_columns(mot_cot, PW) == 1

    # Trang hai cột bị một ĐOẠN VĂN trải hết bề ngang chiếm phần lớn chiều cao
    # vẫn phải là một cột — đoạn đó mới là thứ quyết định mạch đọc.
    doan_rong = [(71, 100, 524, 400)]
    assert _page_columns(doan_rong + trai + phai[:2], PW) == 1


def test_nhan_dong_kieu_the_khong_lot_vao_noi_dung():
    """Model đôi khi "đóng" khối như đóng thẻ HTML. Bộ dò nhãn mở không nhận
    dạng này nên trước đây nó lọt nguyên vào cuối ô — và cả vào `tm`, tức quay
    lại mãi ở mọi bài có đoạn y hệt. Quét `data/` thật: 21 ô, 21 mục `tm`.

    Dạng đo được trên dữ liệu là `</<b370_g>>>` — model mở lại ngoặc nhọn ngay
    sau dấu `/`. Mẫu đầu tiên chỉ nhận `</b370_g>>>` và bỏ lọt cả 21 ô.
    """
    from server.pipeline import _parse_labeled

    t = ("<<<b370>>>\nBản dịch.\n"
         "<<<b370_g>>>\nRAG trả lời câu hỏi.\n</<b370_g>>>\n"
         "<<<b371>>>\nĐoạn sau.\n</b371>>>\n"
         "<<<b372>>>\nĐoạn nữa.\n[/b372]\n")
    r = _parse_labeled(t, ["b370", "b370_g", "b371", "b372"])
    assert r == {"b370": "Bản dịch.", "b370_g": "RAG trả lời câu hỏi.",
                 "b371": "Đoạn sau.", "b372": "Đoạn nữa."}, r

    # Nhắc tới mã khối GIỮA câu thì giữ nguyên — nhãn đóng phải đứng một mình.
    r = _parse_labeled("<<<b5>>>\nXem </b4> ở phụ lục.\n", ["b4", "b5"])
    assert r["b5"] == "Xem </b4> ở phụ lục."


def test_pdf_tieng_viet_khong_bi_coi_la_scan():
    """HỒI QUY #30: bai_tieng_viet.pdf có lớp chữ đọc ra bình thường mà nhận 422
    "PDF có thể là bản scan ảnh — cần OCR".

    Gốc: PyMuPDF gom dòng "Tóm tắt" (16pt) cùng 11 dòng thân bài (10pt) vào MỘT
    khối; khối mang cỡ lớn nhất 16 — bằng tên bài, nằm ngay dưới — nên bộ gộp
    tiêu đề nhiều dòng nuốt cả bài vào tiêu đề và còn 0 khối. Kéo theo #13:
    "Tóm tắt", "1. Giới thiệu" không thành tiêu đề mục, và vì bài tiếng Việt
    không có mốc "Abstract", luật "đầu trang 1 là tác giả" nuốt luôn mục 1.
    """
    from pathlib import Path
    from server.parser import parse_pdf
    data = (Path(__file__).parent / "fixtures" / "bai_tieng_viet.pdf").read_bytes()
    title, blocks, _ = parse_pdf(data)
    assert title == "Tinh chỉnh LLM cho tiếng Việt với LoRA", title
    kieu = [(b.type, b.text[:14]) for b in blocks]
    assert kieu[0] == ("heading", "Tóm tắt"), kieu
    assert ("heading", "1. Giới thiệu") in kieu, kieu
    assert sum(b.type == "para" for b in blocks) == 2, kieu
    assert all(b.translate for b in blocks if b.type == "para"), "mục 1 bị xếp vào meta"


def test_pdf_co_lop_chu_khong_bao_gio_ra_khong_khoi(monkeypatch):
    """Lưới an toàn: bộ bóc có hỏng kiểu gì thì PDF có lớp chữ vẫn ra khối.

    0 khối = 422 "bản scan ảnh — cần OCR", một lời khuyên SAI làm người dùng đi
    tìm OCR cho một file vốn đọc được. Rơi về bóc như văn bản thường thì mất cấu
    trúc nhưng không mất bài.
    """
    from pathlib import Path
    from server import parser as P
    monkeypatch.setattr(P, "_to_blocks", lambda *a, **k: [])
    data = (Path(__file__).parent / "fixtures" / "bai_tieng_viet.pdf").read_bytes()
    _, blocks, _ = P.parse_pdf(data)
    assert blocks and any("tiếng Việt" in b.text for b in blocks)


def test_gach_noi_cuoi_dong_giu_tu_ghep_that():
    """#18: `history-` / `conditionally` bị nối thành `historyconditionally`.

    Mảnh vụn của từ bị cắt (`differ-ent`, `trans-lation`) vẫn phải nối liền;
    từ ghép thật thì giữ gạch; nửa trái hay ghép liền (`along-side`) thì nối.
    """
    from server import parser as P
    tu = {"history", "differ", "ent", "translation", "along", "side", "ground",
          "truth", "pre", "built"}
    tok = P._VOCAB.set(tu)
    try:
        assert P.clean_text("and history-\nconditionally integrates") == \
            "and history-conditionally integrates"
        assert P.clean_text("compares differ-\nent methods") == "compares different methods"
        assert P.clean_text("trans-\nlation") == "translation"
        assert P.clean_text("along-\nside it") == "alongside it"
        assert P.clean_text("ground-\ntruth") == "ground-truth"
        assert P.clean_text("pre-\nserving") == "preserving"
    finally:
        P._VOCAB.reset(tok)
    # không có vốn từ (văn bản dán) thì giữ đúng hành vi cũ: nối
    assert P.clean_text("history-\nconditionally") == "historyconditionally"


def test_khung_hinh_mo_hinh_noi_ra_tron_nhan_va_khong_nuot_dong_van():
    """#11: khung Figure 2 (arXiv 1706.03762) cắt ngang tiêu đề hình con nên
    ảnh mất chữ "Scal"; còn Table 2 thì phần lề +5pt kéo vào một vệt dòng văn
    phía trên. Nới theo dòng chữ bị cắt ngang, cắt khỏi dòng chỉ chạm nhờ lề."""
    import fitz
    from server import parser as P
    d = fitz.open()
    pg = d.new_page(width=612, height=792)
    # dòng văn thân bài rộng, ngay trên vùng hình
    pg.insert_text((72, 96), "This is a long body text line that spans most of the "
                   "page width above the figure region here.", fontsize=10)
    # nhãn hình con thò ra ngoài mép trái của khung mô hình
    pg.insert_text((140, 120), "Scaled Dot-Product Attention", fontsize=10)
    pg.draw_rect(fitz.Rect(170, 130, 400, 250), color=(0, 0, 0))
    goc = fitz.Rect(165, 101, 405, 255)        # mô hình: bám vùng đậm, sót chữ
    r = P._chinh_khung_hinh(pg, goc, 10.0)
    nhan = [fitz.Rect(l["bbox"]) for b in pg.get_text("dict")["blocks"]
            for l in b.get("lines", []) if "Scaled" in "".join(s["text"] for s in l["spans"])][0]
    van = [fitz.Rect(l["bbox"]) for b in pg.get_text("dict")["blocks"]
           for l in b.get("lines", []) if "body text" in "".join(s["text"] for s in l["spans"])][0]
    assert r.x0 <= nhan.x0, "nhãn hình con phải nằm trọn trong ảnh"
    assert r.y0 >= van.y1, "dòng văn phía trên không được lọt vào ảnh"
    assert r.y1 >= 255                                     # vẫn giữ lề phía dưới


def _pdf_hai_cot_co_bang() -> bytes:
    """PDF hai cột, tiêu đề mục 12pt trên thân 10pt, bảng không viền ở mục 3 —
    dựng lại đúng điều kiện của #6 (bản gốc của người test là ReportLab)."""
    import fitz
    d = fitz.open()
    pg = d.new_page(width=612, height=792)
    pg.insert_text((150, 60), "Efficient Sparse Adapters for Language Models", fontsize=16)
    para = ("We study parameter efficient fine tuning and show that sparse adapters "
            "reduce memory while keeping accuracy on standard benchmarks. ") * 2

    def cot(x, y, phan):
        for kieu, t in phan:
            if kieu == "h":
                pg.insert_text((x, y), t, fontsize=12)
                y += 20
            elif kieu == "p":
                con = pg.insert_textbox(fitz.Rect(x, y - 9, x + 250, y + 140), t, fontsize=10)
                y += (140 - con) + 18
            else:
                for hang in t:
                    for i, o in enumerate(hang):
                        pg.insert_text((x + i * 80, y), o, fontsize=10)
                    y += 14
    cot(54, 100, [("h", "1 Introduction"), ("p", para), ("h", "2 Related Work"), ("p", para),
                  ("h", "3 Results"),
                  ("t", [["Model", "GLUE", "Mem"], ["Base", "84.1", "16GB"], ["Ours", "86.2", "10GB"]])])
    cot(318, 100, [("p", para), ("h", "4 Analysis"), ("p", para), ("h", "5 Conclusion"), ("p", para)])
    return d.tobytes()


def test_pdf_hai_cot_giu_bang_khong_vien_va_tieu_de_muc():
    """#6: bảng không viền mất sạch số liệu, không báo gì; và tiêu đề mục 12pt
    dính vào cuối đoạn 10pt phía trên (ngưỡng lệch cỡ cũ là 1,3 lần)."""
    from server import parser as P
    _, bl, _ = P.parse_pdf(_pdf_hai_cot_co_bang())
    bang = [b for b in bl if b.type == "table"]
    assert len(bang) == 1 and "| Base | 84.1 | 16GB |" in bang[0].text
    assert not bang[0].translate
    tieu_de = [b.text for b in bl if b.type == "heading"]
    assert tieu_de == ["1 Introduction", "2 Related Work", "3 Results",
                       "4 Analysis", "5 Conclusion"]


def test_doan_van_khong_bi_nhan_nham_la_bang():
    """Dương tính giả của bộ dò bảng là xé một đoạn văn thành lưới."""
    from server import parser as P
    def dong(x, y, chu):
        return {"bbox": (x, y, x + 6 * len(chu), y + 10), "spans": [{"text": chu, "size": 10}]}
    van = [dong(54, 100 + 12 * i, "We study parameter efficient fine tuning and show")
           for i in range(6)]
    assert P._bang_khong_vien(van) is None
    # danh sách hai cột không có số liệu nào: cũng không phải bảng số
    ds = [dong(54 + 80 * k, 100 + 12 * i, w) for i, w2 in enumerate(
          [("alpha", "beta"), ("gamma", "delta"), ("eps", "zeta")]) for k, w in enumerate(w2)]
    assert P._bang_khong_vien(ds) is None


def test_manh_so_vun_bi_an_va_khong_chan_viec_noi_cau():
    """World Models: chú thích chân trang + năm khối `10^{3}`… chen giữa một câu
    bị cắt đôi. Năm mảnh số hiện thành năm dòng trơ trọi, và vì chúng mà phần
    đuôi "can learn a highly compact policy…" không được nối — thành một khối
    riêng, bị dịch và giải thích riêng, tốn tiền cho nửa câu."""
    from server.parser import Block, an_manh_so, stitch_hyphenated
    bl = [Block("b18", "para", "Training the agent through its world model, we show that it",
                "Intro", 0, 1, True),
          Block("b19", "meta", "^{1}Typical model-free RL models have in the order of to",
                "Intro", 0, 1, False)]
    bl += [Block(f"b{20 + k}", "meta", f"10^{{{e}}}", "Intro", 0, 1, False)
           for k, e in enumerate((3, 6, 7, 9, 8))]
    bl.append(Block("b25", "para", "can learn a highly compact policy to perform its task.",
                    "Intro", 0, 1, True))
    assert an_manh_so(bl) == 5
    stitch_hyphenated(bl)
    con = [b for b in bl if b.text and not b.hidden]
    assert [b.id for b in con] == ["b18", "b19"]
    assert con[0].text.endswith("we show that it can learn a highly compact policy to perform its task.")
    # kết quả ngắn có thập phân / phần trăm thì KHÔNG ẩn — có thể là số chính của bài
    kq = [Block("x1", "para", "57.3%", "", 0, 0, True), Block("x2", "para", "(4)", "", 0, 0, True)]
    an_manh_so(kq)
    assert not kq[0].hidden and kq[1].hidden


def test_nhan_ngoai_me_la_cho_model_chep_lai_prefix():
    """Model viết xong ô của nó rồi chép tiếp toàn văn bài trong prefix
    (`<<<b26>>> [loại: para | mục: …]` …). Nhãn ngoài mẻ không được dồn vào ô
    trước — đo trên `data/`: một ô giải thích phình tới 66.205 ký tự."""
    from server.pipeline import _parse_labeled
    ra = ("<<<b25_g>>>\nGiải thích thật của đoạn này.\n\n"
          "<<<b26>>> [loại: para | mục: 2.1]\nScaling Up Motion Tracking. In Fig. 2…\n"
          "<<<b27>>> [loại: para | mục: 2.1]\nMore source text…\n")
    out = _parse_labeled(ra, ["b25", "b25_g"])
    assert out == {"b25_g": "Giải thích thật của đoạn này."}
    # câu NHẮC tới mã khối giữa dòng thì không bị cắt
    ra2 = "<<<b25_g>>>\nĐoạn này nối với <<<b8>>> ở trên.\n"
    assert "nối với" in _parse_labeled(ra2, ["b25_g"])["b25_g"]


def test_so_bia_do_tren_toan_bai():
    """S21: "900" có ở khối khác trong bài thì không phải số bịa."""
    from server.pipeline import so_bia
    doc = {"id": "x", "blocks": [{"id": "b1", "text": "Agent đạt 906 ± 21 điểm."},
                                 {"id": "b2", "text": "Ngưỡng giải được là 900 điểm."}],
           "translations": {}}
    assert so_bia(doc, ["b1"], "Đạt 906 điểm, vượt ngưỡng 900") == []
    assert so_bia(doc, ["b1"], "Đạt 9999 điểm") == ["9999"]


def test_nguon_gon_cho_trich_dan():
    from server.pipeline import nguon_gon
    assert nguon_gon("https://arxiv.org/abs/1706.03762v7") == "arXiv:1706.03762"
    assert nguon_gon("https://aclanthology.org/2026.acl-long.1203.pdf") == "aclanthology.org/2026.acl-long.1203"


def test_hai_ban_nguon_gon_khop_nhau():
    """`nguonGon()` (app.js) và `nguon_gon()` (pipeline.py) dựng cùng một dòng
    trích dẫn ở hai nơi — lệch là file xuất ra khác bản xem trước."""
    import re
    from pathlib import Path
    js = (Path(__file__).resolve().parent.parent / "web" / "app.js").read_text(encoding="utf-8")
    assert "function nguonGon(" in js
    for tok in ("arXiv:", "\\.pdf$", "^www\\.", "^https?:"):
        assert tok in js.split("function nguonGon(")[1][:600], tok


def test_ghep_chu_thich_voi_vung_theo_cot_va_tong_diem():
    """S16: chú thích ↔ vùng hình ghép theo VỊ TRÍ, không theo thứ tự.

    Ca 1 (CIRAG): Figure 6 ở cột trái, Table 4 ở cột phải, cùng độ cao — bản
    cũ chỉ so chiều dọc nên tráo nhau. Ca 2 (Theia): hai bảng chồng nhau, chú
    thích Table 2 dài ba dòng — dòng đầu gần bảng 1 hơn, trọn khối thì gần bảng 2.
    """
    import fitz
    from server import parser as P
    from server.parser import Block
    d = fitz.open()
    pg = d.new_page(width=612, height=792)
    pg.insert_text((71, 210), "Figure 6: Latency vs. F1 on 2WikiMQA.", fontsize=9)
    pg.insert_text((306, 210), "Table 4: Additional results on single-hop QA.", fontsize=9)
    pg.insert_text((108, 410), "Table 1: Mean CortexBench score across tasks.", fontsize=9)
    pg.insert_text((108, 470), "Table 2: Real robot behavioral cloning results measured", fontsize=9)
    pg.insert_text((108, 481), "by success rate across four tasks and two settings with", fontsize=9)
    pg.insert_text((108, 492), "the same evaluation protocol for every baseline.", fontsize=9)
    vung = [{"page": 0, "kind": "table", "bbox": [327, 70, 501, 194]},
            {"page": 0, "kind": "figure", "bbox": [84, 70, 272, 196]},
            {"page": 0, "kind": "table", "bbox": [108, 420, 502, 448]},
            {"page": 0, "kind": "table", "bbox": [108, 500, 502, 570]}]
    caps = [Block("f6", "caption", "Figure 6: Latency vs. F1 on 2WikiMQA.", page=0),
            Block("t4", "caption", "Table 4: Additional results on single-hop QA.", page=0),
            Block("t1", "caption", "Table 1: Mean CortexBench score across tasks.", page=0),
            Block("t2", "caption", "Table 2: Real robot behavioral cloning results measured", page=0)]
    ghep = {b.id: r["bbox"] for b, r in P._ghep_theo_vi_tri(d, caps, vung)}
    assert ghep["f6"] == [84, 70, 272, 196]      # hình cột trái
    assert ghep["t4"] == [327, 70, 501, 194]     # bảng cột phải
    assert ghep["t1"] == [108, 420, 502, 448]
    assert ghep["t2"] == [108, 500, 502, 570]
