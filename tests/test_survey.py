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
