"""Test cho kho survey — DB tạm, KHÔNG gọi model nên miễn phí, và tắt embedding.

`EMBED_BACKEND=off` để bộ test không kéo về 2,3GB trọng số và không đòi GPU.
Đường BM25 phải chạy đúng một mình — đó cũng chính là đường mà máy không có GPU
sẽ chạy, nên nó đáng được test hơn cả.

Bộ này canh những chỗ đã vỡ thật trong lúc viết:

  - **Chỉ mục FTS5 external content lệch với bảng `chunk`.** Hỏng câm: không lỗi
    lúc ghi, chỉ ném "database disk image is malformed" ở một lệnh xoá rất lâu
    sau. Đã vỡ một lần vì tiêu đề bài truyền vào lúc ghi khác lúc xoá.
  - **Giao dịch ghi bị bỏ quên** làm luồng khác nhận "database is locked" ở một
    chỗ chẳng liên quan. Đã vỡ một lần vì `integrity()` chạy DML ngoài `with`.
  - **Chốt chặn kiểm chứng**: số bịa, mã đoạn bịa, và giấu chỗ chưa tìm ra.
  - **`corpus_digest` phải cố định byte** giữa các câu hỏi, nếu không cache
    prefix trượt và chi phí nhân lên nhiều lần.
"""
from __future__ import annotations

import os
import tempfile

import pytest


@pytest.fixture(scope="module")
def client():
    # `setdefault`, KHÔNG gán đè: `tests/conftest.py` đã đặt biến này trước khi
    # bất cứ module test nào được import, và `server/db.py` đã chốt `DATA_DIR`
    # theo giá trị đó rồi. Gán đè ở đây chỉ tạo ra thư mục tạm thứ hai mà không
    # ai dùng, và làm biến môi trường nói dối về chỗ dữ liệu thật sự nằm.
    os.environ.setdefault("PAPER_DATA_DIR", tempfile.mkdtemp(prefix="loupe-survey-test-"))
    os.environ.setdefault("EMBED_BACKEND", "off")
    os.environ.setdefault("RERANK_BACKEND", "off")
    os.environ.setdefault("OPENROUTER_API_KEY", "test-key-khong-goi-model")
    from fastapi.testclient import TestClient
    from server import main
    with TestClient(main.app) as c:
        yield c


@pytest.fixture(scope="module")
def sdb():
    from server.survey import db
    return db


@pytest.fixture()
def kho(client, sdb):
    """Một kho có sẵn hai bài, đủ để thử tìm liên bài."""
    sid = client.post("/api/survey", json={"name": "thử", "topic": "RAG"}).json()["id"]
    p1 = sdb.add_paper(sid, title="CIRAG: Construction-Integration Retrieval",
                       year=2025, venue="ACL", cites=12, status="indexed", sha256="s1")
    sdb.put_chunks(p1, [
        {"ord": 1, "section": "Abstract", "page": 1, "kind": "abstract",
         "text": "We propose CIRAG, a construction-integration retrieval method for multi-hop QA.",
         "ctx": "abstract introducing CIRAG for multi-hop question answering",
         "vi": "Chúng tôi đề xuất CIRAG, phương pháp truy hồi cho hỏi đáp bắc cầu."},
        {"ord": 2, "section": "Results", "page": 5, "kind": "para",
         "text": "CIRAG reaches 62.3 EM on HotpotQA, above DPR at 58.2 EM.",
         "ctx": "main results of CIRAG versus DPR on HotpotQA",
         "vi": "CIRAG đạt 62,3 EM trên HotpotQA, cao hơn DPR ở mức 58,2 EM."},
    ], title="CIRAG: Construction-Integration Retrieval")
    p2 = sdb.add_paper(sid, title="Theia: Distilling Vision Foundation Models",
                       year=2024, venue="CoRL", cites=40, status="indexed", sha256="s2")
    sdb.put_chunks(p2, [
        {"ord": 1, "section": "Abstract", "page": 1, "kind": "abstract",
         "text": "Theia distills several vision foundation models into one encoder for robot policies.",
         "ctx": "abstract of Theia on vision model distillation for robot learning",
         "vi": "Theia chưng cất nhiều mô hình thị giác nền vào một encoder."},
    ], title="Theia: Distilling Vision Foundation Models")
    return {"sid": sid, "p1": p1, "p2": p2}


# ------------------------------------------------------------- chỉ mục


def test_tim_bang_tieng_viet_ra_bai_tieng_anh(client, kho):
    """Cột `vi` phải được đánh chỉ mục, nếu không hỏi tiếng Việt là trượt sạch."""
    r = client.get(f"/api/survey/{kho['sid']}/search", params={"q": "truy hồi bắc cầu"})
    ids = [h["id"] for h in r.json()["hits"]]
    assert ids and all(i.startswith(kho["p1"]) for i in ids)


def test_tim_khong_dau_van_ra(client, kho):
    """`remove_diacritics 2` — gõ thiếu dấu vẫn phải ra."""
    r = client.get(f"/api/survey/{kho['sid']}/search", params={"q": "truy hoi bac cau"})
    assert r.json()["hits"]


def test_tim_khong_lan_sang_kho_khac(client, sdb, kho):
    other = client.post("/api/survey", json={"name": "kho khác"}).json()["id"]
    assert client.get(f"/api/survey/{other}/search", params={"q": "CIRAG"}).json()["hits"] == []


def test_cau_hoi_co_ky_tu_cu_phap_khong_lam_vo_truy_van(client, kho):
    """Dấu ngoặc và `*` là cú pháp của FTS5 — lọt vào là cả truy vấn ném lỗi."""
    for q in ['CIRAG (multi-hop) "abc" *', "a -b ^c", "((("]:
        assert client.get(f"/api/survey/{kho['sid']}/search",
                          params={"q": q}).status_code == 200


def test_tim_cham_moi_tang_cua_cay(client, sdb, kho):
    """Truy vấn kiểu 'collapsed tree': lá và node tóm lược cùng nằm trong một chỉ mục."""
    sdb.put_summaries(kho["p1"], [{
        "ord": 1, "level": 1, "section": "Abstract",
        "children": [f"{kho['p1']}c1", f"{kho['p1']}c2"],
        "text": "CIRAG is a construction-integration retrieval method reaching 62.3 EM.",
        "ctx": "paper level summary of CIRAG"}])
    hits = client.get(f"/api/survey/{kho['sid']}/search",
                      params={"q": "CIRAG"}).json()["hits"]
    assert {h["level"] for h in hits} >= {0, 1}


# --------------------------------------- bẫy FTS5 external content


def test_ghi_de_doan_thi_chi_muc_khong_con_dau_vet_cu(sdb, kho):
    sdb.put_chunks(kho["p2"], [{"ord": 1, "section": "S", "page": 1, "kind": "para",
                                "text": "hoàn toàn khác về mạng nơ-ron xung",
                                "ctx": "", "vi": ""}], title="Theia")
    assert sdb.bm25(kho["sid"], "distills") == []
    assert sdb.bm25(kho["sid"], "xung")
    assert sdb.integrity() == ""


def test_doi_tieu_de_bai_thi_chi_muc_theo(sdb, kho):
    """Tiêu đề nằm trong chỉ mục — quên đồng bộ là tìm ra tên cũ mãi mãi."""
    sdb.update_paper(kho["p1"], title="Tên Mới Hoàn Toàn")
    assert sdb.bm25(kho["sid"], "Hoàn Toàn")
    assert sdb.integrity() == ""


def test_xoa_bai_khong_lam_hong_chi_muc(client, sdb, kho):
    """Đây là chỗ đã ném 'database disk image is malformed' một lần."""
    client.delete(f"/api/survey/{kho['sid']}/paper/{kho['p1']}")
    assert sdb.integrity() == ""
    assert sdb.bm25(kho["sid"], "HotpotQA") == []


def test_reindex_dung_lai_duoc_tu_bang_chunk(sdb, kho):
    n = sdb.reindex()
    assert n > 0
    assert sdb.bm25(kho["sid"], "HotpotQA")
    assert sdb.integrity() == ""


def test_integrity_khong_bo_quen_giao_dich(sdb, kho):
    """Chạy nhiều lần rồi ghi tiếp — kẹt khoá là hỏng ở lần ghi, không phải ở đây."""
    for _ in range(3):
        assert sdb.integrity() == ""
    sdb.update_paper(kho["p2"], cites=99)
    assert sdb.load_paper(kho["p2"])["cites"] == 99


# ------------------------------------------------------------- đồ thị


def test_do_thi_gop_thuc_the_trung_giua_cac_bai(sdb, kho):
    for pid in (kho["p1"], kho["p2"]):
        sdb.put_graph(kho["sid"], pid,
                      [{"name": "HotpotQA", "norm": "hotpotqa", "kind": "dataset",
                        "chunks": [f"{pid}c1"]},
                       {"name": "CIRAG", "norm": "cirag", "kind": "method",
                        "chunks": [f"{pid}c1"]}],
                      [{"src": "cirag", "dst": "hotpotqa", "rel": "đánh giá trên",
                        "chunk": f"{pid}c1", "note": ""}])
    ov = sdb.graph_overview(kho["sid"])
    hot = [e for e in ov["entities"] if e["norm"] == "hotpotqa"]
    assert len(hot) == 1 and hot[0]["papers"] == 2   # một node, hai bài


def test_xoa_bai_thi_thuc_the_mo_coi_bien_mat(client, sdb, kho):
    # `norm` phải LUÔN suy ra từ `norm_name()`, không được viết tay: đó là khoá
    # gộp trùng, và `find_entities` cũng tra bằng chính hàm đó.
    name = "ChỉCóỞBàiNày"
    sdb.put_graph(kho["sid"], kho["p2"],
                  [{"name": name, "norm": sdb.norm_name(name), "kind": "concept",
                    "chunks": [f"{kho['p2']}c1"]}], [])
    assert sdb.find_entities(kho["sid"], [name])
    client.delete(f"/api/survey/{kho['sid']}/paper/{kho['p2']}")
    assert sdb.find_entities(kho["sid"], [name]) == []


def test_bo_soat_do_thi_bo_canh_treo_va_ten_rac(kho):
    from server.survey import graph
    got = graph._clean("p1", {
        "entities": [{"name": "our method", "kind": "method", "chunks": []},
                     {"name": "CIRAG", "kind": "method", "chunks": ["p1c1", "khac"]},
                     {"name": "HotpotQA", "kind": "dataset", "chunks": []}],
        "edges": [{"src": "CIRAG", "dst": "HotpotQA", "rel": "đánh giá trên", "chunk": "p1c1"},
                  {"src": "CIRAG", "dst": "KhôngCóTrongDanhSách", "rel": "tốt hơn"},
                  {"src": "CIRAG", "dst": "HotpotQA", "rel": "quan hệ bịa"}],
    })
    assert [e["norm"] for e in got["entities"]] == ["cirag", "hotpotqa"]  # bỏ "our method"
    assert [e["chunks"] for e in got["entities"]][0] == ["p1c1"]         # bỏ mã lạ
    assert len(got["edges"]) == 1                                        # bỏ cạnh treo + rel lạ


# ------------------------------------------------------- chốt chặn


def test_bat_so_bia(client, sdb, kho):
    from server.survey import verify
    ids = [f"{kho['p1']}c2"]
    ok = verify.check_answer(kho["sid"], f"CIRAG đạt 62,3 EM [{ids[0]}].", ids)
    assert not [w for w in ok if w["kind"] == "số_bịa"]
    bad = verify.check_answer(kho["sid"], f"CIRAG đạt 91,7 EM [{ids[0]}].", ids)
    assert [w for w in bad if w["kind"] == "số_bịa"]


def test_bat_ma_doan_bia(client, sdb, kho):
    from server.survey import verify
    w = verify.check_answer(kho["sid"], "Một khẳng định [p999c1].", [f"{kho['p1']}c2"])
    assert [x for x in w if x["kind"] == "cite_lạ"]


def test_bat_so_khong_kem_nguon(client, sdb, kho):
    from server.survey import verify
    w = verify.check_answer(kho["sid"], "Mô hình đạt 62,3 EM.", [f"{kho['p1']}c2"])
    assert [x for x in w if x["kind"] == "số_không_nguồn"]


def test_bo_qua_url_va_dinh_danh_khong_coi_la_so_lieu(client, sdb, kho):
    """`Qwen2.5-7B` và `github.com/52566rz` không phải số liệu của bài."""
    from server.survey import verify
    ids = [f"{kho['p1']}c2"]
    w = verify.check_answer(
        kho["sid"], f"Dùng Qwen2.5-7B, mã ở github.com/52566rz/CIRAG [{ids[0]}].", ids)
    assert not [x for x in w if x["kind"] == "số_bịa"]


def test_bat_viec_giau_cho_chua_tim_ra(client, sdb, kho):
    """Kiểu hỏng tệ nhất: viết trơn tru đè lên chỗ trống."""
    from server.survey import verify
    subs = [{"id": "q1", "ask": "a"}, {"id": "q2", "ask": "b"}]
    giau = verify.check_answer(kho["sid"], "Câu trả lời đầy đủ và trơn tru.",
                               [f"{kho['p1']}c2"], subs, covered=["q1"])
    assert [w for w in giau if w["kind"] == "giấu_thiếu"]
    noi = verify.check_answer(kho["sid"],
                              "Trả lời phần một. Chưa tìm thấy bằng chứng cho phần hai.",
                              [f"{kho['p1']}c2"], subs, covered=["q1"])
    assert not [w for w in noi if w["kind"] == "giấu_thiếu"]


def test_cat_cau_giu_dung_vi_tri_ky_tu(kho):
    """Cảnh báo neo theo chỉ số câu — lệch thì giao diện tô nhầm chỗ."""
    from server.survey import verify
    src = "CIRAG đạt 62,3 EM [p1c2]. Cao hơn DPR ở mức 58,2 [p1c3]."
    got = verify.split_sentences(src)
    assert len(got) == 2
    for s in got:
        assert src[s["start"]:s["end"]] == s["text"]
    assert got[0]["cites"] == ["p1c2"]


# ------------------------------------------------- cache và prefix


def test_corpus_digest_co_dinh_giua_cac_lan_goi(sdb, kho):
    """Đổi byte giữa hai câu hỏi là hỏng prefix cache, chi phí nhân lên nhiều lần."""
    from server.survey import agent
    sdb.update_paper(kho["p1"], card={"tldr_vi": "một câu", "keywords_en": ["rag"]})
    a = agent.digest_of(kho["sid"])
    b = agent.digest_of(kho["sid"])
    assert a == b and a


def test_van_tay_kho_doi_khi_them_bot_bai(client, sdb, kho):
    before = sdb.corpus_fingerprint(kho["sid"])
    sdb.add_paper(kho["sid"], title="bài mới", status="new", sha256="s3")
    assert sdb.corpus_fingerprint(kho["sid"]) != before


def test_rrf_chi_doc_thu_hang(kho):
    """Trộn được BM25 (điểm âm) với cosine ([-1,1]) mà không phải chuẩn hoá gì."""
    from server.survey import search
    got = dict(search.rrf([["a", "b", "c"], ["c", "a", "d"]]))
    assert got["a"] > got["c"] > got["b"]     # a đứng đầu một danh sách, nhì ở kia


def test_tran_da_dang_moi_bai(kho):
    """Thiếu phủ là kiểu hỏng nặng nhất — 10 đoạn cùng một bài đúng là cái bẫy đó."""
    from server.survey import search
    hits = [{"id": f"p1c{i}", "paper_id": "p1", "grade": 3} for i in range(8)]
    hits += [{"id": f"p2c{i}", "paper_id": "p2", "grade": 2} for i in range(4)]
    got = search._diversify(hits, keep=6)
    assert sum(1 for h in got if h["paper_id"] == "p1") == search.PER_PAPER


def test_ngan_sach_chan_truoc_khi_goi(kho):
    from server.survey import agent
    b = agent.Budget(0.01)
    assert b.can("plan")
    b.spent = 0.009
    assert not b.can("answer")       # tổng hợp ước $0.05, vượt trần


# --------------------------------------------------------- cắt đoạn


def test_cat_doan_khong_noi_qua_ranh_gioi_muc(kho):
    from server.survey import ingest
    blocks = [
        {"type": "heading", "text": "Method", "section": "", "page": 1},
        {"type": "para", "text": "a" * 200, "section": "Method", "page": 1},
        {"type": "heading", "text": "Results", "section": "", "page": 2},
        {"type": "para", "text": "b" * 200, "section": "Results", "page": 2},
    ]
    got = ingest.split_blocks(blocks)
    assert {c["section"] for c in got} == {"Method", "Results"}
    assert all("a" not in c["text"] or "b" not in c["text"] for c in got)


def test_cat_doan_giu_nguyen_cong_thuc_va_caption(kho):
    from server.survey import ingest
    got = ingest.split_blocks([
        {"type": "para", "text": "x" * 100, "section": "S", "page": 1},
        {"type": "equation", "text": "E = mc^{2}", "section": "S", "page": 1},
        {"type": "caption", "text": "Figure 1: sơ đồ", "section": "S", "page": 1},
    ])
    kinds = {c["kind"]: c["text"] for c in got}
    assert kinds.get("equation") == "E = mc^{2}"
    assert kinds.get("figcap", "").startswith("Figure 1")


def test_cat_doan_bo_muc_tham_khao(kho):
    from server.survey import ingest
    got = ingest.split_blocks([
        {"type": "para", "text": "nội dung thật", "section": "S", "page": 1},
        {"type": "reference", "text": "[1] Ai đó và cộng sự, 2020.", "section": "R", "page": 9},
    ])
    assert len(got) == 1


_REFS_MOT_DONG = (
    "Videoagent: Long-form video understanding with large language model as agent. "
    "In European Conference on Computer Vision, 58-76. Springer. Wang, Z.; Yu, S.; "
    "Stengel-Eskin, E.; Yoon, J.; Cheng, F.; and Bansal, M. 2025. VideoTree: Adaptive "
    "Tree-based Video Representation for LLM Reasoning on Long Videos. In Proceedings "
    "of the IEEE/CVF Conference on Computer Vision and Pattern Recognition (CVPR), "
    "3272-3283. Weng, Y.; Han, M.; He, H.; and Zhuang, B. 2024. Longvlm: Efficient "
    "long video understanding via large language models. In European Conference on "
    "Computer Vision, 453-470. Springer.")


def test_bo_thu_muc_tham_khao_lot_qua_nhan_sai(kho):
    """Bài nào bộ bóc không nhận ra tiêu đề References thì cả thư mục rơi vào mục
    cuối. Đã đo trên bài thật: một mục thư mục đứng HẠNG NHẤT cho câu hỏi nội dung."""
    from server.survey import ingest
    got = ingest.split_blocks([
        {"type": "para", "text": "Nội dung thật của bài.", "section": "Conclusion", "page": 7},
        {"type": "para", "text": _REFS_MOT_DONG, "section": "Conclusion", "page": 7},
    ])
    assert len(got) == 1 and "Videoagent" not in got[0]["text"]


def test_thu_muc_dai_hon_tran_cung_bi_bo(kho):
    """Khối > MAX_CHARS đi thẳng vào kết quả, vòng qua `_flush` — đã lọt đúng ca này."""
    from server.survey import ingest
    dai = _REFS_MOT_DONG * 4
    assert len(dai) > ingest.MAX_CHARS
    got = ingest.split_blocks([{"type": "para", "text": dai,
                                "section": "Conclusion", "page": 7}])
    assert got == []


def test_van_xuoi_co_trich_dan_trong_cau_khong_bi_vut_oan(kho):
    """Mật độ năm của văn xuôi có trích dẫn CÒN CAO HƠN thư mục thật — nên dấu
    hiệu phải là nơi công bố, không phải là năm."""
    from server.survey import ingest
    for t in (
        "RAG excels in simple queries (Lewis et al., 2020; Lin et al., 2024; Ram et al., "
        "2023) but struggles with multi-hop reasoning (Trivedi et al., 2023; Fan et al., "
        "2024; Mallen et al., 2023), where answering requires composing evidence.",
        "We evaluate on HotpotQA (Yang et al., 2018), 2WikiMQA (Ho et al., 2020) and "
        "MuSiQue (Trivedi et al., 2022), three standard multi-hop benchmarks used widely.",
    ):
        assert not ingest._looks_like_refs(t)


def test_bo_moi_thu_sau_tieu_de_references_tru_phu_luc(kho):
    """Phụ lục đứng SAU thư mục và là nội dung thật — không được bỏ theo."""
    from server.survey import ingest
    got = ingest.split_blocks([
        {"type": "heading", "text": "References", "section": "", "page": 8},
        {"type": "para", "text": "Ai đó và cộng sự. Một bài báo nào đó.", "section": "R", "page": 8},
        {"type": "heading", "text": "Appendix A", "section": "", "page": 9},
        {"type": "para", "text": "Chi tiết thí nghiệm bổ sung.", "section": "Appendix A", "page": 9},
    ])
    assert len(got) == 1 and got[0]["section"] == "Appendix A"


def test_cat_doan_bo_khoi_da_an(kho):
    from server.survey import ingest
    got = ingest.split_blocks([
        {"type": "para", "text": "giữ lại", "section": "S", "page": 1},
        {"type": "para", "text": "đã ẩn", "section": "S", "page": 1, "hidden": True},
    ])
    assert len(got) == 1 and "đã ẩn" not in got[0]["text"]


# ------------------------------------------------------------ API


def test_matrix_khong_goi_model_va_xuat_duoc(client, sdb, kho):
    sdb.update_paper(kho["p1"], card={"tldr_vi": "một câu", "datasets": ["HotpotQA"]})
    j = client.get(f"/api/survey/{kho['sid']}/matrix").json()
    assert j["facets"] and len(j["rows"]) == 2
    row = [r for r in j["rows"] if r["id"] == kho["p1"]][0]
    assert row["cells"]["tldr_vi"] == "một câu"
    assert "HotpotQA" in row["cells"]["datasets"]
    csv = client.get(f"/api/survey/{kho['sid']}/matrix", params={"fmt": "csv"})
    assert csv.status_code == 200 and csv.text.startswith("﻿")   # BOM cho Excel
    assert client.get(f"/api/survey/{kho['sid']}/matrix",
                      params={"fmt": "md"}).text.startswith("| Bài")


def test_mo_dung_doan_duoc_trich_dan(client, kho):
    r = client.get(f"/api/survey/{kho['sid']}/chunk/{kho['p1']}c2")
    assert r.status_code == 200
    assert "62.3" in r.json()["chunk"]["text"]
    assert client.get(f"/api/survey/{kho['sid']}/chunk/khongcothat").status_code == 404


def test_ma_khong_hop_le_bi_chan(client, sdb):
    """Hàng rào chống path traversal — `store` dựng đường dẫn file từ mã."""
    import pytest as _p
    for bad in ("../etc", "a/b", "a-b", ""):
        with _p.raises(ValueError):
            sdb.check_id(bad)
    assert client.get("/api/survey/..%2F..%2Fetc").status_code in (404, 400)


def test_kho_khong_ton_tai_tra_404(client):
    assert client.get("/api/survey/khongcothat").status_code == 404
    assert client.patch("/api/survey/khongcothat", json={"name": "x"}).status_code == 404


def test_luong_doc_hieu_khong_bi_dung_toi(client):
    """Cơ chế mới phải không đụng gì vào luồng cũ."""
    assert client.get("/api/docs").status_code == 200
    assert client.get("/api/config").status_code == 200
    assert client.get("/api/db/stats").status_code == 200


# ------------------------------------------------------- bản tổng hợp


def _synth_kho(sdb, kho):
    """Kho có phiếu + đồ thị, đủ để dựng và soát bản tổng hợp."""
    sdb.update_paper(kho["p1"], card={"tldr_vi": "CIRAG dùng construction-integration",
                                      "novelty": "pha tích hợp"})
    sdb.update_paper(kho["p2"], card={"tldr_vi": "Theia chưng cất mô hình thị giác"})
    for pid in (kho["p1"], kho["p2"]):
        sdb.put_graph(kho["sid"], pid,
                      [{"name": "CIRAG", "norm": "cirag", "kind": "method", "chunks": []},
                       {"name": "DPR", "norm": "dpr", "kind": "method", "chunks": []}],
                      [{"src": "cirag", "dst": "dpr", "rel": "tốt hơn",
                        "chunk": f"{pid}c1", "note": "hơn 4.1 điểm"},
                       {"src": "cirag", "dst": "dpr", "rel": "dùng",
                        "chunk": f"{pid}c1", "note": "làm bộ tìm"}])


def test_lineage_tinh_tu_do_thi_khong_hoi_model(sdb, kho):
    """Quan hệ kế thừa phải suy từ cạnh đã bóc, và mỗi cái mang mã đoạn kiểm được."""
    from server.survey import synth
    _synth_kho(sdb, kho)
    lin = synth.lineage(kho["sid"])
    assert lin and all(g["cite"] for g in lin)
    # "dùng" nói về công cụ, không phải kế thừa — không được lọt vào
    assert {g["rel"] for g in lin} == {"tốt hơn"}


def test_nhan_bai_ngan_va_co_dinh(sdb, kho):
    """`P1`,`P2`… thay cho mã dài, và phải cố định giữa các lần gọi.

    Mã bài (`p50d58cb2d3`) chỉ khác mã đoạn (`p50d58cb2d3c14`) ở phần đuôi, nên
    model liên tục lẫn hai thứ rồi viết ra mã 12 ký tự không tồn tại — đo trên
    bài thật: 6/9 cảnh báo của một bản tổng hợp là "mã bài không có trong kho".
    """
    from server.survey import prompts
    papers = sdb.list_papers(kho["sid"])
    lab = prompts.paper_labels(papers)
    assert sorted(lab.values()) == ["P1", "P2"]
    assert lab == prompts.paper_labels(list(reversed(papers)))   # không đổi theo thứ tự
    sdb.update_paper(kho["p1"], card={"tldr_vi": "x"})
    d = prompts.corpus_digest(sdb.list_papers(kho["sid"]))
    assert f"[{lab[kho['p1']]}]" in d and f"[{kho['p1']}]" not in d


def test_tong_hop_doi_nhan_ngan_ve_ma_that(sdb, kho):
    from server.survey import prompts, synth
    _synth_kho(sdb, kho)
    lab = prompts.paper_labels(sdb.list_papers(kho["sid"]))
    short = lab[kho["p1"]]
    out = synth._clean(kho["sid"], {
        "approaches": [{"name": "H", "papers": [short], "evidence": []}],
        "novelty": [{"paper": short, "new": "n"}],
        "read_order": [{"paper": short, "why": "w"}],
    }, [])
    assert out["approaches"][0]["papers"] == [kho["p1"]]
    assert out["novelty"][0]["paper"] == kho["p1"]
    # đổi rồi thì chốt chặn không được kêu "bài lạ" nữa
    assert not [w for w in synth.check(kho["sid"], out) if w["kind"] == "bài_lạ"]


def test_so_co_duoi_chu_trong_nguon_khong_bi_bao_oan(sdb, kho):
    """Bài ghi `100M`, câu trả lời viết "100 triệu" — đó KHÔNG phải số bịa.

    `_NUM` cố ý từ chối số dính chữ để `Qwen2.5-7B` không bị coi là số liệu.
    Nhưng luật đó chỉ đúng ở phía CÂU TRẢ LỜI; ở phía nguồn thì nó tạo báo động
    giả. Đã báo oan đúng ca này trên bài thật.
    """
    from server.survey import verify
    have = verify.source_numbers("we scale to 100M frames and a 7B model, 62.3% success")
    for n in ("100", "7", "62.3"):
        assert verify._norm(n) in have, n
    # `1,000` trong bài và `1000` trong câu trả lời là một số
    assert verify._norm("1000") in verify.source_numbers("about 1,000 episodes")


def test_bat_so_bia_van_con_chan_duoc(sdb, kho):
    """Nới bên nguồn không được làm lọt số bịa."""
    from server.survey import verify
    cid = f"{kho['p1']}c2"
    w = verify.check_answer(kho["sid"], f"Đạt 91,7 EM [{cid}].", [cid])
    assert [x for x in w if x["kind"] == "số_bịa"]


def test_tong_hop_bat_ma_doan_va_ma_bai_bia(sdb, kho):
    from server.survey import synth
    _synth_kho(sdb, kho)
    d = {"approaches": [{"name": "H1", "papers": ["pKHONGCO"],
                         "evidence": [{"claim": "một khẳng định", "cite": "pXcY"}]}],
         "novelty": [], "tensions": [], "read_order": []}
    kinds = {w["kind"] for w in synth.check(kho["sid"], d)}
    assert "cite_lạ" in kinds and "bài_lạ" in kinds


def test_tong_hop_bat_so_bia(sdb, kho):
    from server.survey import synth
    _synth_kho(sdb, kho)
    cid = f"{kho['p1']}c2"          # đoạn có "62.3 EM ... 58.2 EM"
    ok = {"approaches": [{"name": "H", "papers": [kho["p1"]],
                          "evidence": [{"claim": "đạt 62,3 EM", "cite": cid}]}],
          "novelty": [], "tensions": [], "read_order": []}
    bad = {"approaches": [{"name": "H", "papers": [kho["p1"]],
                           "evidence": [{"claim": "đạt 91,7 EM", "cite": cid}]}],
           "novelty": [], "tensions": [], "read_order": []}
    assert not [w for w in synth.check(kho["sid"], ok) if w["kind"] == "số_bịa"]
    assert [w for w in synth.check(kho["sid"], bad) if w["kind"] == "số_bịa"]


def test_tong_hop_bat_bo_sot_bai_va_khong_gom_duoc(sdb, kho):
    """Hai kiểu hỏng mà nhìn bằng mắt không thấy: bỏ quên bài, và mỗi bài một hướng."""
    from server.survey import synth
    _synth_kho(sdb, kho)
    d = {"approaches": [{"name": "H1", "papers": [kho["p1"]], "evidence": []}],
         "novelty": [], "tensions": [], "read_order": []}
    assert [w for w in synth.check(kho["sid"], d) if w["kind"] == "bỏ_sót_bài"]

    d3 = {"approaches": [{"name": f"H{i}", "papers": [kho["p1"]], "evidence": []}
                         for i in range(3)],
          "novelty": [], "tensions": [], "read_order": []}
    assert [w for w in synth.check(kho["sid"], d3) if w["kind"] == "chưa_gom_được"]


def test_tong_hop_danh_dau_cu_khi_kho_doi(client, sdb, kho):
    """Thêm bài thì bản cũ bị gắn cờ, KHÔNG bị xoá — công đọc nằm trong đó."""
    from server.survey import synth
    _synth_kho(sdb, kho)
    sdb.save_synth(kho["sid"], {"title": "x", "approaches": []})
    assert sdb.load_survey(kho["sid"])["synth_stale"] is False
    sdb.add_paper(kho["sid"], title="bài mới", status="new", sha256="sZ")
    s = sdb.load_survey(kho["sid"])
    assert s["synth_stale"] is True and s["synth"]["title"] == "x"


def test_tong_hop_xuat_markdown(sdb, kho):
    from server.survey import synth
    _synth_kho(sdb, kho)
    md = synth.as_markdown({
        "title": "Chủ đề", "scope": "2 bài",
        "problem": {"statement": "S", "why_hard": "W", "framings": []},
        "approaches": [{"name": "H", "idea": "I", "mechanism": "M", "bet": "B",
                        "papers": [kho["p1"]], "evidence": [{"claim": "C", "cite": "x"}]}],
        "paper_names": {kho["p1"]: "CIRAG"},
    })
    assert "# Chủ đề" in md and "Đặt cược vào." in md and "CIRAG" in md


def test_api_tong_hop_khong_goi_model(client, sdb, kho):
    _synth_kho(sdb, kho)
    r = client.get(f"/api/survey/{kho['sid']}/synthesis")
    assert r.status_code == 200 and r.json()["synth"] is None
    sdb.save_synth(kho["sid"], {"title": "T", "approaches": [], "paper_names": {}})
    assert client.get(f"/api/survey/{kho['sid']}/synthesis").json()["synth"]["title"] == "T"
    assert client.get(f"/api/survey/{kho['sid']}/synthesis",
                      params={"fmt": "md"}).text.startswith("# T")


def test_danh_sach_kho_khong_keo_theo_ban_tong_hop(client, sdb, kho):
    """Danh sách kho được gọi mỗi lần mở màn hình — không nhét vài nghìn chữ vào."""
    sdb.save_synth(kho["sid"], {"title": "T" * 5000, "approaches": []})
    row = [s for s in client.get("/api/surveys").json()["surveys"] if s["id"] == kho["sid"]][0]
    assert "synth" not in row and row["has_synth"] is True


def test_header_http_phai_thuan_ascii(kho):
    """Header HTTP mã hoá bằng latin-1 — một chữ có dấu là `UnicodeEncodeError`.

    Đã vấp: User-Agent viết tiếng Việt làm cả ba nguồn tìm bài cùng chết, mà
    `find()` bắt hết lỗi nên màn hình chỉ hiện "không tìm thấy bài nào".
    """
    from server.survey import sources
    for name, val in sources.UA.items():
        val.encode("latin-1")          # ném UnicodeEncodeError nếu có dấu
        assert name.isascii()


def test_loi_nguon_phai_tra_ve_chu_khong_nuot(kho, monkeypatch):
    """Nguồn hỏng thì `find()` phải BÁO, kể cả khi không còn kết quả nào.

    Bản đầu gắn lỗi vào từng dòng kết quả — cả ba nguồn hỏng thì không có dòng
    nào để gắn, và nguyên nhân biến mất.
    """
    import asyncio
    from server.survey import sources

    async def no(*a, **k):
        raise RuntimeError("mạng hỏng")

    monkeypatch.setattr(sources, "arxiv", no)
    monkeypatch.setattr(sources, "openalex", no)
    monkeypatch.setattr(sources, "crossref", no)
    rows, errs = asyncio.get_event_loop_policy().new_event_loop().run_until_complete(
        sources.find("bất kỳ", limit=5))
    assert rows == [] and len(errs) == 3
    assert all("RuntimeError" in e for e in errs)


def test_bo_test_khong_ghi_vao_data_that(client, sdb):
    """Hàng rào: bộ test phải chạy trên thư mục tạm, không đụng `data/` thật.

    `server/db.py` đọc `PAPER_DATA_DIR` **lúc import**, mà pytest import mọi file
    test lúc thu thập — nên fixture đặt biến môi trường là đã muộn. Đã hỏng thật:
    bộ test ghi 2 bài rác và 66 kho rác vào cơ sở dữ liệu của người dùng. Chốt
    chặn nằm ở `tests/conftest.py`; test này canh cho nó không bị gỡ mất.
    """
    from pathlib import Path
    from server import db as maindb
    real = Path(__file__).resolve().parent.parent / "data"
    assert maindb.DATA_DIR.resolve() != real.resolve()
    assert str(maindb.DB_PATH).startswith(os.environ["PAPER_DATA_DIR"])


def test_bang_cua_kho_khong_dung_bang_cua_luong_cu(client, sdb):
    have = {r[0] for r in sdb.conn().execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"documents", "parse_cache", "tm"} <= have      # bảng cũ còn nguyên
    assert {"survey", "paper", "chunk", "emb", "entity", "edge", "run"} <= have


# ------------------------------------------------- bài giảng và hồ sơ đối chiếu
#
# Không test nào ở đây gọi model hay đi ra mạng: `refs.py` được nạp bằng dữ liệu
# giả đúng hình dạng Semantic Scholar trả về, còn `lecture.check` vốn là hàm
# thuần. Phần đi mạng thật đã đo tay trên bài LAPA (63 tham khảo, 58 có câu
# trích dẫn) — không đưa vào bộ test vì nó phụ thuộc mạng và phụ thuộc S2.


def test_bo_soat_bai_giang_bat_so_bia(client, sdb, kho):
    """Chốt chặn đáng giá nhất: số trên bài giảng phải có thật trong bài.

    Bằng mắt thì không ai bắt được, mà một con số bịa là gán kết quả giả cho tác
    giả thật.
    """
    from server.survey import lecture
    ids = {c["id"] for c in sdb.paper_chunks(kho["p1"], level=0)}
    that = {"evidence": {"items": [{"claim": "CIRAG hơn DPR", "number": "62.3",
                                    "setting": "HotpotQA"}], "source": []}}
    bia = {"evidence": {"items": [{"claim": "CIRAG hơn DPR", "number": "71.9",
                                   "setting": "HotpotQA"}], "source": []}}
    assert not [w for w in lecture.check(that, ids, kho["p1"])
                if w["kind"] == "số_không_có_trong_bài"]
    assert [w for w in lecture.check(bia, ids, kho["p1"])
            if w["kind"] == "số_không_có_trong_bài"]


def test_bo_soat_bai_giang_bat_ma_doan_khong_co(client, sdb, kho):
    from server.survey import lecture
    ids = {c["id"] for c in sdb.paper_chunks(kho["p1"], level=0)}
    d = {"problem": {"body": "Một câu.", "source": ["pKHONGCOc99"]}}
    assert [w for w in lecture.check(d, ids, kho["p1"]) if w["kind"] == "mã_đoạn_không_có"]


def test_bo_soat_bai_giang_bat_cau_nong(client, sdb, kho):
    """Kiểu hỏng khó thấy nhất: câu đúng sự thật mà không mang tin nào."""
    from server.survey import lecture
    nong = {"mechanism": {"steps": [
        {"do": "Chạy bộ mã hoá", "why": "Bước này đóng vai trò quan trọng, "
                                        "góp phần nâng cao chất lượng của hệ thống."}]}}
    sau = {"mechanism": {"steps": [
        {"do": "Chạy bộ mã hoá trên hai khung hình liền nhau",
         "why": "Vì một khung hình đơn lẻ không phân biệt được tay đang nâng cốc "
                "với tay đang hạ cốc, nên nhãn hành động sinh ra sẽ bị đảo chiều."}]}}
    assert [w for w in lecture.check(nong, set()) if w["kind"] in
            ("câu_độn", "nói_chung_chung", "thiếu_cơ_chế")]
    assert not [w for w in lecture.check(sau, set()) if w["kind"] in
                ("câu_độn", "nói_chung_chung", "thiếu_cơ_chế")]


def test_vong_dao_sau_chi_lay_canh_bao_ve_do_sau(client, sdb, kho):
    """Số bịa và mã đoạn sai KHÔNG được vào lời chê: viết lại không sửa nổi kiểu
    hỏng đó, mà nhồi vào chỉ làm loãng đúng chỗ cần chê."""
    from server.survey import lecture
    warns = [{"section": "evidence", "kind": "số_không_có_trong_bài", "msg": "x"},
             {"section": "mechanism", "kind": "thiếu_cơ_chế", "msg": "y", "text": "z"}]
    got = lecture._shallow(warns)
    assert "evidence" not in got
    assert "mechanism" in got


def test_boc_chu_theo_dung_hinh_dang_tung_muc(client):
    """`mechanism` giấu chữ trong `steps[].why`. Soát trên `json.dumps` thì tên
    khoá và dấu ngoặc lọt vào phép đếm, và mọi phép kiểm độ sâu đều lệch."""
    from server.survey import lecture
    got = dict(lecture._texts("mechanism", {"steps": [
        {"do": "Làm A", "why": "Vì B"}, {"do": "Làm C", "why": "Vì D"}]}))
    assert "Vì B" in got["bước 1"] and "Làm A" in got["bước 1"]
    assert "Vì D" in got["bước 2"]
    # câu hỏi tự kiểm cố ý ngắn — soát độ sâu ở đó là kêu oan
    assert lecture._texts("check", {"items": [{"q": "Vì sao?", "a": "Vì thế."}]}) == []


def test_xep_hang_bai_dan_uu_tien_cho_dan_nhieu_lan(client):
    """Số câu trích dẫn nặng hơn số trích dẫn toàn cầu: một bài nền tảng được
    dẫn qua loa không giúp gì cho việc hiểu bài chính."""
    from server.survey import refs
    kinh_dien_dan_qua_loa = {"citedPaper": {"citationCount": 90000}, "contexts": []}
    it_tieng_nhung_dan_ky = {"citedPaper": {"citationCount": 30},
                             "contexts": ["a" * 60, "b" * 60, "c" * 60],
                             "isInfluential": True}
    assert refs._score(it_tieng_nhung_dan_ky, False) > refs._score(kinh_dien_dan_qua_loa, False)


def test_khop_nham_bai_thi_tha_khong_co_ho_so(client):
    """Khớp theo tiêu đề là khớp mờ. Dựng bài giảng đối chiếu với NHẦM bài còn
    tệ hơn hẳn không có phần đối chiếu, vì nhìn vẫn có vẻ đúng."""
    from server.survey import refs
    a = refs._norm_title("Latent Action Pretraining from Videos")
    assert refs._overlap(a, refs._norm_title("Latent Action Pretraining From Videos")) >= 0.6
    assert refs._overlap(a, refs._norm_title("Attention Is All You Need")) < 0.6


def test_mã_bai_phai_isalnum_o_route_bai_giang(client, kho):
    """Cùng hàng rào chống path traversal như mọi chỗ khác."""
    r = client.get(f"/api/survey/{kho['sid']}/paper/..%2F..%2Fetc/lecture")
    assert r.status_code >= 400


def test_danh_sach_bai_khong_keo_theo_bai_giang(client, sdb, kho):
    """Hai cột nặng phải nằm ngoài danh sách bài: `corpus_digest` dựng từ danh
    sách này và phải byte-identical giữa mọi câu hỏi — cột đổi theo từng lần dựng
    bài giảng mà lọt vào là hỏng cache của cả kho."""
    sdb.update_paper(kho["p1"], lecture='{"sections": {"problem": {"body": "x"}}}')
    nhe = sdb.list_papers(kho["sid"])
    assert all("lecture" not in p and "refs" not in p for p in nhe)
    assert "lecture" in sdb.load_paper(kho["p1"])

    from server.survey import prompts
    truoc = prompts.corpus_digest(sdb.list_papers(kho["sid"]))
    sdb.update_paper(kho["p1"], lecture='{"sections": {"problem": {"body": "ĐÃ ĐỔI"}}}')
    assert prompts.corpus_digest(sdb.list_papers(kho["sid"])) == truoc


def test_khong_soat_so_o_muc_von_la_vi_du_gia_dinh(client, sdb, kho):
    """`mechanism` cố ý kể một TÌNH HUỐNG VÍ DỤ — "giả sử video dài 10 phút,
    N = 600 khung hình" không phải kết quả của ai cả.

    Đo trên bài thật: không phân biệt thì một bài sinh 32 cảnh báo, và lúc ấy
    người dùng thôi đọc cảnh báo — cảnh báo THẬT ở `evidence` trôi theo.
    """
    from server.survey import lecture
    ids = {c["id"] for c in sdb.paper_chunks(kho["p1"], level=0)}
    vi_du = {"mechanism": {"steps": [
        {"do": "Giả sử video dài 600 giây, lấy khung 750 đến 755",
         "why": "Vì cửa sổ phải phủ hết sự kiện nên hai đầu mốc mới cần lấy dư."}]}}
    assert not [w for w in lecture.check(vi_du, ids, kho["p1"])
                if w["kind"] == "số_không_có_trong_bài"]
    # nhưng ở mục KHẲNG ĐỊNH về bài thì vẫn phải bắt
    khang_dinh = {"evidence": {"items": [{"claim": "x", "number": "750", "setting": "y"}]}}
    assert [w for w in lecture.check(khang_dinh, ids, kho["p1"])
            if w["kind"] == "số_không_có_trong_bài"]


def test_moc_thoi_gian_khong_bi_boc_thanh_so_lieu(client):
    """`[00:12:30-00:12:35]` từng bị bóc thành SÁU số rời — "00", "12", "30",
    "00", "12", "35" — mà không con nào là số liệu của bài."""
    from server.survey import lecture
    got = lecture._numbers("evidence", {"items": [
        {"claim": "sự kiện [00:12:30-00:12:35]", "number": "62.3", "setting": ""}]})
    assert got == ["62.3"]


def test_tieu_de_qua_ngan_thi_khong_tra_semantic_scholar(client):
    """Đã gặp một bài trong kho bóc hỏng tiêu đề, chỉ còn "Question Answering".
    Tiêu đề như vậy khớp trúng hàng nghìn bài — thà không tra còn hơn dựng cả
    phần đối chiếu với NHẦM bài."""
    from server.survey import refs
    assert not refs.usable_title("Question Answering")
    assert not refs.usable_title("GCR")
    assert refs.usable_title("LATENT ACTION PRETRAINING FROM VIDEOS")


# ------------------------------------------------------- chuyển bài sang kho khác


def test_chuyen_bai_thi_doan_va_chi_muc_di_theo(client, sdb, kho):
    """Nạp nhầm kho là chuyện thường. Chữa bằng cách xoá đi nạp lại thì ném mất
    phiếu, câu ngữ cảnh, cây tóm lược và bài giảng — nên phải chuyển được."""
    import asyncio
    from server.survey import search
    sid2 = client.post("/api/survey", json={"name": "kho hai"}).json()["id"]

    r = client.post(f"/api/survey/{kho['sid']}/paper/{kho['p1']}/move",
                    json={"to": sid2})
    assert r.status_code == 200 and r.json()["moved"]

    assert sdb.load_paper(kho["p1"])["survey_id"] == sid2
    assert len(sdb.paper_chunks(kho["p1"], level=0)) == 2   # đoạn không mất
    # và tìm được ở kho mới, không còn ở kho cũ
    assert asyncio.run(search.plain(sid2, "CIRAG", 5))
    assert not asyncio.run(search.plain(kho["sid"], "CIRAG", 5))


def test_chuyen_bai_khong_de_lai_thuc_the_mo_coi(client, sdb, kho):
    """`entity.id` là sha của (survey_id, tên chuẩn hoá) — cùng một thực thể ở
    hai kho là hai mã khác nhau. Bỏ qua chỗ này thì bài sang kho mới mà thực thể
    của nó vẫn nằm ở kho cũ: đồ thị kho mới thiếu bài, kho cũ đầy node mồ côi."""
    sid2 = client.post("/api/survey", json={"name": "kho ba"}).json()["id"]
    # Khai cả hai đầu mút, đúng như `graph.py` làm — nó lọc bỏ cạnh nào có đầu
    # mút chưa khai (graph.py:161), nên trạng thái khác không xảy ra thật.
    sdb.put_graph(kho["sid"], kho["p1"],
                  [{"name": "CIRAG", "norm": "cirag", "kind": "method",
                    "chunks": [kho["p1"] + "c1"]},
                   {"name": "HotpotQA", "norm": "hotpotqa", "kind": "dataset",
                    "chunks": [kho["p1"] + "c2"]}],
                  [{"src": "cirag", "dst": "hotpotqa", "rel": "đánh giá trên",
                    "chunk": kho["p1"] + "c2"}])
    sdb.move_paper(kho["p1"], sid2)

    c = sdb.conn()
    assert c.execute(
        "SELECT COUNT(*) FROM mention m LEFT JOIN entity e ON e.id = m.entity_id"
        " WHERE e.id IS NULL").fetchone()[0] == 0
    assert c.execute(
        "SELECT COUNT(*) FROM edge g LEFT JOIN entity a ON a.id = g.src"
        " LEFT JOIN entity b ON b.id = g.dst"
        " WHERE a.id IS NULL OR b.id IS NULL").fetchone()[0] == 0
    # thực thể đã sang kho mới, và kho cũ không giữ lại bản mồ côi
    assert c.execute("SELECT COUNT(*) FROM entity WHERE survey_id = ?",
                     (sid2,)).fetchone()[0] > 0
    assert c.execute("SELECT COUNT(*) FROM edge WHERE survey_id = ? AND paper_id = ?",
                     (sid2, kho["p1"])).fetchone()[0] == 1


def test_chuyen_vao_kho_da_co_dung_bai_do_thi_tu_choi(client, sdb, kho):
    """Hai bản cùng một bài trong một kho làm mọi câu trả lời trích dẫn hai lần
    cùng một đoạn, mà người dùng không hiểu vì sao."""
    sid2 = client.post("/api/survey", json={"name": "kho bốn"}).json()["id"]
    sdb.add_paper(sid2, title="CIRAG bản sao", status="indexed", sha256="s1")
    r = client.post(f"/api/survey/{kho['sid']}/paper/{kho['p1']}/move",
                    json={"to": sid2})
    assert r.status_code == 409
    assert sdb.load_paper(kho["p1"])["survey_id"] == kho["sid"]   # không đụng gì


def test_chuyen_bai_khong_thuoc_kho_thi_tu_choi(client, sdb, kho):
    """Cùng hàng rào như mọi route khác: bài phải thuộc kho đang thao tác."""
    sid2 = client.post("/api/survey", json={"name": "kho năm"}).json()["id"]
    assert client.post(f"/api/survey/{sid2}/paper/{kho['p2']}/move",
                       json={"to": sid2}).status_code == 404


# ------------------------------------------------------------- đủ bộ CRUD
#
# Mỗi thực thể người dùng tạo ra phải sửa được và xoá được từ giao diện, không
# chỉ tạo rồi đọc. Chỗ đau nhất đã gặp thật: tiêu đề bóc hỏng thành "Question
# Answering" — nó nằm trong chỉ mục toàn văn, trong phiếu gửi cho model, và là
# thứ dùng để tra Semantic Scholar, mà không có cách nào sửa.


def test_sua_tieu_de_bai_thi_chi_muc_va_doan_theo(client, sdb, kho):
    r = client.patch(f"/api/survey/{kho['sid']}/paper/{kho['p1']}",
                     json={"title": "CIRAG đã sửa tên", "year": 2026, "venue": "NeurIPS"})
    assert r.status_code == 200
    p = sdb.load_paper(kho["p1"])
    assert p["title"] == "CIRAG đã sửa tên" and p["year"] == 2026

    # `chunk.title` là bản chép dùng cho chỉ mục — lệch là chỉ mục hỏng câm
    c = sdb.conn()
    assert {r["title"] for r in c.execute(
        "SELECT title FROM chunk WHERE paper_id = ?", (kho["p1"],))} == {"CIRAG đã sửa tên"}
    assert sdb.integrity() == ""


def test_khong_cho_sua_thu_do_pass_co_chot_chan_sinh_ra(client, sdb, kho):
    """`card` và `status` là kết quả của pass có chốt chặn. Sửa tay được thì
    chốt chặn thành vô nghĩa — cùng lý do `PATCH …/slides` cấm sửa
    `source_block_ids`."""
    truoc = sdb.load_paper(kho["p1"])
    client.patch(f"/api/survey/{kho['sid']}/paper/{kho['p1']}",
                 json={"title": "vẫn đổi được", "card": {"task": "bịa"},
                       "status": "carded"})
    sau = sdb.load_paper(kho["p1"])
    assert sau["title"] == "vẫn đổi được"
    assert sau["card"] == truoc["card"] and sau["status"] == truoc["status"]


def test_tieu_de_rong_bi_tu_choi(client, kho):
    assert client.patch(f"/api/survey/{kho['sid']}/paper/{kho['p1']}",
                        json={"title": "   "}).status_code == 400


def test_xoa_luot_hoi_thi_cache_tro_toi_no_cung_di(client, sdb, kho):
    """Bỏ sót `qcache` thì hỏi lại đúng câu đó trúng cache, tra ra một `run_id`
    không còn tồn tại, và người dùng nhận màn hình trống không hiểu vì sao."""
    rid = sdb.save_run(kho["sid"], "câu hỏi thử", "trả lời", [], [], [], {}, 0.01)
    sdb.qcache_put(sdb.qcache_key(kho["sid"], "câu hỏi thử"), rid)
    c = sdb.conn()
    assert c.execute("SELECT COUNT(*) FROM qcache WHERE run_id = ?", (rid,)).fetchone()[0] == 1

    assert client.delete(f"/api/survey/{kho['sid']}/run/{rid}").status_code == 200
    assert sdb.load_run(rid) is None
    assert c.execute("SELECT COUNT(*) FROM qcache WHERE run_id = ?", (rid,)).fetchone()[0] == 0


def test_xoa_luot_hoi_cua_kho_khac_thi_tu_choi(client, sdb, kho):
    sid2 = client.post("/api/survey", json={"name": "kho sáu"}).json()["id"]
    rid = sdb.save_run(sid2, "của kho khác", "x", [], [], [], {}, 0.0)
    assert client.delete(f"/api/survey/{kho['sid']}/run/{rid}").status_code == 404
    assert sdb.load_run(rid) is not None


def test_xoa_ban_tong_hop_va_bai_giang(client, sdb, kho):
    sdb.save_synth(kho["sid"], {"scope": "thử"})
    assert sdb.load_survey(kho["sid"])["synth"]
    assert client.delete(f"/api/survey/{kho['sid']}/synthesis").status_code == 200
    assert not sdb.load_survey(kho["sid"])["synth"]

    sdb.update_paper(kho["p1"], lecture='{"sections": {}}', lecture_fp="x")
    assert client.delete(
        f"/api/survey/{kho['sid']}/paper/{kho['p1']}/lecture").status_code == 200
    assert not sdb.load_paper(kho["p1"]).get("lecture")
    # hồ sơ đối chiếu giữ lại: nó miễn phí nhưng đi ra mạng ngoài
    sdb.update_paper(kho["p1"], refs='{"refs": []}')
    client.delete(f"/api/survey/{kho['sid']}/paper/{kho['p1']}/lecture")
    assert sdb.load_paper(kho["p1"]).get("refs")


def test_sua_cot_bang_so_sanh(client, sdb, kho):
    """Cột dựng thẳng từ phiếu nên thêm/bớt cột KHÔNG gọi model."""
    r = client.patch(f"/api/survey/{kho['sid']}",
                     json={"facets": [{"key": "idea", "label": "Ý tưởng"}]})
    assert r.status_code == 200
    assert sdb.load_survey(kho["sid"])["facets"] == [{"key": "idea", "label": "Ý tưởng"}]
    m = client.get(f"/api/survey/{kho['sid']}/matrix").json()
    assert [f["label"] for f in m["facets"]] == ["Ý tưởng"]


def test_chon_model_thi_luu_that(client, sdb, kho):
    """Route từng giữ một BẢN CHÉP RIÊNG của danh sách trường sửa được, và bản
    chép đó thiếu `model`/`fast_model` — nên chọn model xong thì lựa chọn bị vứt
    **lặng lẽ**: không lỗi, không cảnh báo, `svLoad()` đọc lại giá trị cũ và ô
    chọn nhảy về "Theo .env". Nhìn ra ngoài y hệt như dropdown tự đóng.
    """
    r = client.patch(f"/api/survey/{kho['sid']}",
                     json={"model": "anthropic/claude-opus-4.1",
                           "fast_model": "deepseek/deepseek-v4-pro"})
    assert r.status_code == 200
    assert r.json()["model"] == "anthropic/claude-opus-4.1"

    d = client.get(f"/api/survey/{kho['sid']}").json()
    assert d["models"]["strong"] == "anthropic/claude-opus-4.1"
    assert d["models"]["fast"] == "deepseek/deepseek-v4-pro"
    assert d["models"]["strong_src"] == "kho"

    # trả về mặc định cũng phải ăn
    client.patch(f"/api/survey/{kho['sid']}", json={"model": "", "fast_model": ""})
    assert sdb.load_survey(kho["sid"])["model"] == ""


def test_route_khong_giu_ban_chep_rieng_cua_danh_sach_truong(client):
    """Hai danh sách thì sớm muộn cũng lệch; một danh sách thì không lệch được.

    Đây chính là cách lỗi trên lọt qua: `update_survey` cho phép `model`, route
    thì không, và không chỗ nào báo gì cả.
    """
    from pathlib import Path
    import re
    src = Path(__file__).resolve().parents[1].joinpath("server/survey_api.py").read_text()
    assert "sdb.SURVEY_FIELDS" in src

    # bộ lọc của route phải trỏ tới danh sách chung, không phải một tuple gõ tay
    loc = re.search(r"if k in ([^\n]+)", src)
    assert loc and "SURVEY_FIELDS" in loc.group(1), \
        f"route lại chép tay danh sách trường: {loc.group(1) if loc else '?'}"

    # và danh sách chung phải phủ đúng những cột kho mà người dùng sửa được
    from server.survey import db as sdb
    assert set(sdb.SURVEY_FIELDS) >= {"model", "fast_model", "name", "budget_usd"}


def test_moi_route_tren_bai_deu_kiem_bai_thuoc_kho(client, sdb, kho):
    """`DELETE …/paper/{pid}` từng xoá được bài của kho KHÁC rồi trả `stats` của
    kho hiện tại — nhìn vào không thấy gì lạ, hỏng câm. `enrich` còn nặng hơn:
    nó ghi `entity`/`edge` theo `sid` lấy từ URL, tức nhét đồ thị của một bài vào
    kho không chứa nó."""
    sid2 = client.post("/api/survey", json={"name": "kho bảy"}).json()["id"]
    pid = kho["p1"]                       # bài của kho `kho["sid"]`
    for method, path in (("delete", f"/api/survey/{sid2}/paper/{pid}"),
                         ("get", f"/api/survey/{sid2}/paper/{pid}"),
                         ("post", f"/api/survey/{sid2}/paper/{pid}/recard"),
                         ("post", f"/api/survey/{sid2}/paper/{pid}/enrich")):
        r = getattr(client, method)(path)
        assert r.status_code == 404, f"{method.upper()} {path} → {r.status_code}"
    assert sdb.load_paper(pid)["survey_id"] == kho["sid"]   # không bị đụng


def test_xoa_kho_thi_qcache_di_theo(client, sdb, kho):
    rid = sdb.save_run(kho["sid"], "câu hỏi", "trả lời", [], [], [], {}, 0.0)
    sdb.qcache_put(sdb.qcache_key(kho["sid"], "câu hỏi"), rid)
    c = sdb.conn()
    assert c.execute("SELECT COUNT(*) FROM qcache WHERE run_id = ?", (rid,)).fetchone()[0] == 1
    sdb.delete_survey(kho["sid"])
    assert c.execute("SELECT COUNT(*) FROM qcache WHERE run_id = ?", (rid,)).fetchone()[0] == 0


def test_danh_dau_cache_khong_bi_tien_to_bi_danh_lam_truot():
    """App đặt tên model dạng `~anthropic/claude-…`. `startswith("anthropic/")`
    thuần thì không khớp — mất `cache_control` mà không lỗi, không cảnh báo, chỉ
    thấy hoá đơn cao gấp mấy lần."""
    from server.llm import _needs_explicit_cache as f
    assert f("anthropic/claude-sonnet-4.5") and f("~anthropic/claude-opus-4.1")
    assert f("qwen/qwen3") and f("~alibaba/x")
    assert not f("openai/gpt-5.6") and not f("~deepseek/deepseek-v4-flash-latest")


def test_falsify_phai_den_duoc_mat_nguoi_doc(client, sdb, kho):
    """`falsify` — "quan sát nào sẽ chứng minh hướng này sai" — là trường Feynman
    nhất của bản tổng hợp. Model vẫn sinh, `check` vẫn đòi, mà nó chưa bao giờ
    được vẽ ra: trả tiền token đầu ra rồi giấu đi, và cảnh báo "thiếu phản chứng"
    trỏ tới một trường người dùng không nhìn thấy để mà sửa."""
    from pathlib import Path
    from server.survey import synth
    root = Path(__file__).resolve().parents[1]
    assert "a.falsify" in root.joinpath("web/survey.js").read_text()

    md = synth.as_markdown({"approaches": [
        {"name": "A", "idea": "y", "mechanism": "m", "bet": "b",
         "falsify": "nếu đo trên video dài hơn 30 phút"}]})
    assert "nếu đo trên video dài hơn 30 phút" in md


def test_gom_canh_bao_bai_giang_cung_muc_cung_loai(client):
    """Một bài thật có 106 cảnh báo `mã_đoạn_không_có`, riêng một mục 71 mã KHÁC
    nhau — gom theo thông điệp không ăn. Ba cảnh báo thật nằm lẫn trong đó."""
    from server.survey import lecture
    nhieu = [{"section": "check", "kind": "mã_đoạn_không_có", "msg": f"mã {i}"}
             for i in range(71)]
    that = [{"section": "limits", "kind": "thiếu_cơ_chế", "msg": "câu này nông"}]
    got = lecture._gom(nhieu + that)
    assert len(got) == 2, got
    assert got[0]["n"] == 71 and "và 67 chỗ nữa" in got[0]["msg"]
    assert got[1]["kind"] == "thiếu_cơ_chế"      # cảnh báo thật không bị nuốt


def test_di_het_co_che_khong_dem_hu_tu():
    """`CAUSAL` cố ý rộng cho `missing_mechanism`, nhưng đếm nó để kết luận "đã
    đi hết cơ chế" thì sai: `khi`, `nếu`, `nên`, `trong khi` có mặt trong gần như
    mọi câu tiếng Việt. Đo trên deck thật: slide ablation KHÔNG có cơ chế nào lại
    đạt, còn hai slide mô tả cơ chế thì trượt."""
    from server import pipeline
    hu_tu = {"headline": "ICI tích luỹ bằng chứng nhiều bước mà vẫn ổn định",
             "bullets": ["Khi số bước tăng thì kết quả không đổi",
                         "Trong khi đó chi phí tăng, nên cần cân nhắc, sau đó dừng"]}
    that = {"headline": "ICI dựng core triple set",
            "bullets": ["Bước 1: nhận truy vấn, trả về tập triple ứng viên",
                        "Bước 2: lọc bằng cách so ràng buộc toàn cục, "
                        "nhờ đó bỏ được triple mâu thuẫn, dẫn đến tập nhất quán"]}
    assert not pipeline._walks_mechanism(hu_tu)
    assert pipeline._walks_mechanism(that)
