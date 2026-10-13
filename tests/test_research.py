"""Sổ nghiên cứu: lưu bền, kiểm nguồn và không gọi model ngoài."""

import pytest


@pytest.fixture()
def research_case():
    from fastapi.testclient import TestClient
    from server import main
    from server.survey import db as sdb

    with TestClient(main.app) as client:
        sid = sdb.create_survey("Kho kiểm chứng thực nghiệm")["id"]
        pid = sdb.add_paper(sid, title="Paper Alpha", status="carded", card={
            "datasets": ["HotpotQA"], "metrics": ["EM"], "baselines": ["DPR"],
            "gap": "Mô hình cũ gặp khó khi tổng hợp nhiều đoạn.",
            "limitations": "Chỉ đánh giá trên một tập dữ liệu.",
            "results": [
                {"claim": "Alpha reaches 62.3 EM on HotpotQA versus DPR", "number": "62.3",
                 "chunk": "", "dataset": "HotpotQA", "metric": "EM", "baseline": "DPR",
                 "conditions": "dev split"},
                {"claim": "Alpha reaches 90.0 EM", "number": "90.0", "chunk": ""},
            ], "code_url": "https://github.com/example/alpha",
        })
        sdb.put_chunks(pid, [
            {"ord": 0, "page": 2, "section": "Results", "text":
             "Alpha reaches 62.3 EM on HotpotQA versus DPR. We use GPU A100 and learning rate 1e-4."},
        ])
        ch = sdb.paper_chunks(pid)[0]["id"]
        card = sdb.load_paper(pid)["card"]
        card["results"][0]["chunk"] = ch
        card["results"][1]["chunk"] = ch
        card["gap_chunk"] = ch
        card["limitations_chunks"] = [ch]
        sdb.update_paper(pid, card=card)
        pid2 = sdb.add_paper(sid, title="Paper Beta", status="carded", card={
            "datasets": ["HotpotQA"], "metrics": ["EM"],
            "results": [{"claim": "Beta reaches 60.0 EM on HotpotQA", "number": "60.0",
                         "chunk": "", "dataset": "HotpotQA", "metric": "EM",
                         "conditions": "test split"}],
        })
        sdb.put_chunks(pid2, [{"ord": 0, "page": 4, "text":
                               "Beta reaches 60.0 EM on HotpotQA with a held-out test split."}])
        ch2 = sdb.paper_chunks(pid2)[0]["id"]
        card2 = sdb.load_paper(pid2)["card"]
        card2["results"][0]["chunk"] = ch2
        sdb.update_paper(pid2, card=card2)
        yield client, sdb, sid, pid, pid2, ch, ch2


def test_research_tu_dong_tach_bay_muc_va_khong_so_sanh_khac_dieu_kien(research_case):
    client, _sdb, sid, pid, pid2, ch, ch2 = research_case
    result = client.get(f"/api/survey/{sid}/research")
    assert result.status_code == 200
    data = result.json()
    assert data["cost_usd"] == 0
    assert len(data["experiments"]) == 3
    matched = next(x for x in data["experiments"] if x["value"] == "62.3")
    assert matched["number_in_source"] and not matched["comparable"]
    assert matched["dataset"] == "HotpotQA" and matched["metric"] == "EM"
    invented = next(x for x in data["experiments"] if x["value"] == "90.0")
    assert not invented["number_in_source"]
    assert data["gaps"][0]["chunk_id"] == ch
    assert len(data["relations"]) == 1
    assert set(data["relations"][0]["sources"]) == {ch, ch2}
    audit = next(x for x in data["reproducibility"] if x["paper_id"] == pid)
    assert next(x for x in audit["checks"] if x["key"] == "hardware")["chunk_id"] == ch
    assert next(x for x in audit["checks"] if x["key"] == "uncertainty")["status"] == "not_found"


def test_research_luu_hoi_va_phan_loai_bang_chung_persist(research_case):
    client, _sdb, sid, *_rest, ch, _ch2 = research_case
    url = f"/api/survey/{sid}/research/items"
    q = client.post(url, json={"kind": "question", "title": "Kết quả EM có cải thiện không?"})
    assert q.status_code == 200
    qid = q.json()["id"]
    ev = client.post(url, json={"kind": "evidence", "title": "Kết quả thử nghiệm",
                                "stance": "support", "chunk_id": ch, "question_id": qid})
    assert ev.status_code == 200
    assert ev.json()["paper_id"] == _rest[0]
    evidence_id = ev.json()["id"]
    # Chọn lại cùng đoạn sẽ cập nhật phân loại, không tạo hai bản ghi trùng.
    same = client.post(url, json={"kind": "evidence", "title": "Kiểm tra lại",
                                   "stance": "unclear", "chunk_id": ch, "question_id": qid})
    assert same.status_code == 200 and same.json()["id"] == evidence_id
    data = client.get(f"/api/survey/{sid}/research").json()
    assert len(data["evidence"]) == 1
    assert any(e["question_id"] == qid and e["source"]["page"] == 2
               for e in data["evidence"])
    assert client.patch(f"{url}/{evidence_id}", json={"stance": "oppose"}).json()["stance"] == "oppose"
    assert client.delete(f"{url}/{qid}").status_code == 200
    assert client.get(f"/api/survey/{sid}/research").json()["evidence"][0]["question_id"] == ""
    # Người dùng có thể gắn bằng chứng mồ côi sang câu hỏi mới.
    new_q = client.post(url, json={"kind": "question", "title": "Câu hỏi thay thế"}).json()
    assert client.patch(f"{url}/{evidence_id}", json={"question_id": new_q["id"]}).status_code == 200


def test_goi_y_giu_nhieu_doan_goc_cung_bai_va_khong_lay_tom_luoc(research_case):
    client, sdb, sid, pid, pid2, *_ = research_case
    sdb.put_chunks(pid, [
        {"ord": 0, "page": 1, "text": "HotpotQA experiments improve exact match for Alpha."},
        {"ord": 1, "page": 2, "text": "HotpotQA experiments show lower exact match on noisy questions."},
        {"ord": 2, "page": 3, "text": "HotpotQA experiments have limited evidence for long questions."},
        {"ord": 3, "page": 4, "text": "HotpotQA experiments need additional validation."},
    ])
    sdb.put_summaries(pid, [{"ord": 0, "level": 1, "text":
                            "HotpotQA experiments HotpotQA experiments HotpotQA experiments"}])
    result = client.get(f"/api/survey/{sid}/research/suggest", params={"q": "HotpotQA experiments"})
    assert result.status_code == 200
    hits = result.json()["hits"]
    assert len([h for h in hits if h["paper_id"] == pid]) == 3
    assert len({h["chunk_id"] for h in hits}) == len(hits)
    assert any(h["paper_id"] == pid2 for h in hits)
    assert all("s0" not in h["chunk_id"] for h in hits)
    assert not any(h["stance"] != "unclassified" for h in hits)


def test_goi_y_trich_doan_dung_vi_tri_tu_khoa(research_case):
    client, sdb, sid, pid, *_ = research_case
    sdb.put_chunks(pid, [{"ord": 0, "page": 6,
                          "text": "Phần mô tả mở đầu. " * 80 +
                                  "We measured HotpotQA exact match on the held-out set. " +
                                  "Phần ghi chú cuối. " * 30}])
    hits = client.get(f"/api/survey/{sid}/research/suggest", params={"q": "HotpotQA"}).json()["hits"]
    match = next(h for h in hits if h["paper_id"] == pid)
    assert "HotpotQA exact match" in match["text"]
    assert match["text"].startswith("…")
    assert match["text"].endswith("…")
    assert len(match["text"]) <= 482


def test_research_khong_nhan_doan_tu_kho_khac(research_case):
    client, sdb, sid, _pid, _pid2, ch, _ch2 = research_case
    other = sdb.create_survey("Kho khác")["id"]
    url = f"/api/survey/{other}/research/items"
    assert client.post(url, json={"kind": "claim", "title": "Alpha 62.3", "chunk_id": ch}).status_code == 400
    assert client.get(f"/api/survey/{other}/research/suggest", params={"q": "HotpotQA"}).json()["hits"] == []
    assert client.post(url, json={"kind": "evidence", "title": "A", "stance": "support",
                                  "question_id": "something"}).status_code == 400
    assert client.post(url, json={"kind": "evidence", "title": "Chưa chọn câu hỏi",
                                  "stance": "support"}).status_code == 400
    assert client.get(f"/api/survey/{other}/research").json()["experiments"] == []


def test_research_soat_so_va_bai_bi_xoa(research_case):
    client, sdb, sid, pid, _pid2, ch, _ch2 = research_case
    url = f"/api/survey/{sid}/research/items"
    correct = client.post(url, json={"kind": "claim", "title": "Alpha đạt 62.3 EM", "chunk_id": ch})
    assert correct.status_code == 200
    wrong = client.post(url, json={"kind": "claim", "title": "Alpha đạt 75.5 EM", "chunk_id": ch})
    assert wrong.status_code == 200
    substring = client.post(url, json={"kind": "claim", "title": "Alpha đạt 2.3 EM", "chunk_id": ch})
    assert substring.status_code == 200
    claims = client.get(f"/api/survey/{sid}/research").json()["claims"]
    assert next(x for x in claims if x["id"] == correct.json()["id"])["check"] == "text_check_needed"
    assert next(x for x in claims if x["id"] == wrong.json()["id"])["check"] == "number_missing"
    assert next(x for x in claims if x["id"] == substring.json()["id"])["check"] == "number_missing"
    sdb.drop_paper(pid)
    claims = client.get(f"/api/survey/{sid}/research").json()["claims"]
    assert all(x["check"] == "no_source" for x in claims)
    # Nguồn bị xoá thì nhận định vẫn sửa được; trạng thái không thành giá trị lỗi.
    assert client.patch(f"{url}/{correct.json()['id']}",
                        json={"body": "Cần tìm nguồn khác"}).status_code == 200
    assert sdb.integrity() == ""


def test_research_ghi_chu_sua_trang_thai_va_xoa_kho(research_case):
    client, sdb, sid, *_ = research_case
    url = f"/api/survey/{sid}/research/items"
    n = client.post(url, json={"kind": "issue", "title": "Cần kiểm tra giao thức",
                               "body": "Đọc mục Experimental Setup"})
    assert n.status_code == 200
    item = n.json()
    assert client.patch(f"{url}/{item['id']}", json={"kind": "note"}).json()["kind"] == "note"
    assert client.patch(f"{url}/{item['id']}", json={"kind": "issue"}).json()["kind"] == "issue"
    assert client.patch(f"{url}/{item['id']}", json={"status": "resolved"}).json()["status"] == "resolved"
    assert any(x["id"] == item["id"] for x in client.get(f"/api/survey/{sid}/research").json()["items"])
    assert client.delete(f"/api/survey/{sid}").status_code == 200
    assert sdb.conn().execute("SELECT COUNT(*) FROM research_item WHERE survey_id = ?", (sid,)).fetchone()[0] == 0


def test_goi_y_khong_lay_doan_chi_khop_tieu_de_hoac_ngu_canh(research_case):
    client, sdb, sid, pid, *_ = research_case
    sdb.put_chunks(pid, [
        {"ord": 0, "text": "exp(z_i/T) / sum(exp(z_j/T))", "ctx": "Knowledge distillation"},
        {"ord": 1, "text": "Knowledge distillation improves student accuracy on this dataset."},
        {"ord": 2, "text": "Unrelated evaluation protocol.", "vi": "Knowledge distillation"},
    ], title="Knowledge distillation")
    hits = client.get(f"/api/survey/{sid}/research/suggest",
                      params={"q": "knowledge distillation"}).json()["hits"]
    assert [h["chunk_id"] for h in hits] == [pid + "c1"]


def test_doi_nhan_khong_mat_nhan_xet_va_khong_cat_mat_dieu_kien(research_case):
    client, _sdb, sid, _pid, _pid2, ch, _ch2 = research_case
    url = f"/api/survey/{sid}/research/items"
    qid = client.post(url, json={"kind": "question", "title": "Độ chính xác cải thiện?"}).json()["id"]
    excerpt = "Measured improvement. " * 18 + "However, this result applies only to the test split."
    ev = client.post(url, json={"kind": "evidence", "title": excerpt,
                               "body": "Chỉ áp dụng cho tập kiểm tra.", "stance": "support",
                               "chunk_id": ch, "question_id": qid}).json()
    assert ev["title"] == excerpt
    changed = client.post(url, json={"kind": "evidence", "stance": "unclear",
                                    "chunk_id": ch, "question_id": qid}).json()
    assert changed["id"] == ev["id"]
    assert changed["body"] == ev["body"]
    assert changed["title"] == excerpt
    assert changed["stance"] == "unclear"
    assert len(client.get(f"/api/survey/{sid}/research").json()["evidence"]) == 1


def test_tu_tim_bang_thuat_ngu_cua_dung_kho(research_case):
    from server import db
    from server.survey import research
    client, sdb, sid, pid, *_ = research_case
    doc_id = sdb.new_id('d')
    db.save_doc({'id': doc_id, 'blocks': [], 'brief': {'glossary': [
        {'vi': 'chưng cất tri thức', 'en': 'knowledge distillation'}]}})
    sdb.update_paper(pid, loupe_doc_id=doc_id)
    sdb.put_chunks(pid, [{'ord': 0, 'text': 'Knowledge distillation transfers information from teacher to student.'}])
    result = client.get(f'/api/survey/{sid}/research/suggest',
                        params={'q': 'Chưng cất tri thức là gì?'}).json()
    assert result['query'] == 'knowledge distillation'
    assert result['hits'][0]['chunk_id'] == pid + 'c0'
    assert result['cost_usd'] == 0
    other = sdb.create_survey('Kho không có glossary')['id']
    assert 'distillation' not in research.search_query(other, 'chưng cất tri thức')
    assert research.suggest(sid, 'the and which') == []


def test_goi_y_cau_hoi_chi_dung_du_lieu_co_trong_nguon(research_case):
    client, sdb, sid, pid, *_ = research_case
    card = sdb.load_paper(pid)['card']
    card['datasets'].append('DatasetNotMentioned')
    sdb.update_paper(pid, card=card)
    data = client.get(f'/api/survey/{sid}/research').json()
    assert len(data['question_starters']) == 1
    q = data['question_starters'][0]
    assert 'HotpotQA' in q['title']
    assert q['chunk_id'] in sdb.get_chunks([q['chunk_id']], sid)
    assert 'DatasetNotMentioned' not in q['title']


def test_tra_loi_co_nguon_cache_va_chan_trich_dan_bia(research_case, monkeypatch):
    from server import llm
    import json
    client, sdb, sid, pid, _pid2, ch, _ch2 = research_case
    qid = client.post(f'/api/survey/{sid}/research/items', json={
        'kind': 'question', 'title': 'Kết quả trên HotpotQA là bao nhiêu?'}).json()['id']
    calls = []
    async def price(model):
        return (0.0000001, 0.0000002)
    async def complete(messages, **kwargs):
        calls.append(kwargs)
        return json.dumps({'findings': [
            {'answer': 'Alpha đạt 62.3 EM trên HotpotQA.', 'chunk_id': ch,
             'quote': 'Alpha reaches 62.3 EM on HotpotQA versus DPR.'},
            {'answer': 'Alpha đạt 99.9.', 'chunk_id': ch, 'quote': 'Invented source text that must not pass validation.'},
            {'answer': 'Nguồn ngoài kho.', 'chunk_id': 'foreign', 'quote': 'Alpha reaches 62.3 EM on HotpotQA versus DPR.'}
        ], 'missing': ['Chưa có kết quả cùng điều kiện cho tất cả các bài.']}), llm.Usage(cost=0.001)
    monkeypatch.setattr(llm, 'gia_model', price)
    monkeypatch.setattr(llm, 'complete', complete)
    url = f'/api/survey/{sid}/research/answer/{qid}'
    assert client.get(url).json() is None
    assert calls == []
    result = client.post(url).json()
    assert len(result['findings']) == 1
    assert result['findings'][0]['chunk_id'] == ch
    assert result['cost_usd'] == 0.001 and not result['cached']
    cached = client.post(url).json()
    assert cached['cached'] and cached['cost_usd'] == 0
    assert len(calls) == 1 and calls[0]['session_id'] == sid
    assert client.get(url).json()['cached']
    assert len(calls) == 1
    sdb.put_chunks(pid, [{'ord': 0, 'text': 'New HotpotQA results do not contain the old score.'}])
    assert client.get(url).json() is None
    assert len(calls) == 1
    changed = client.post(url).json()
    assert len(calls) == 2
    assert changed['findings'] == [] and changed['missing']


def test_khong_goi_model_khi_gia_vuot_tran_hoac_khong_co_nguon(research_case, monkeypatch):
    from server import llm
    client, _sdb, sid, *_ = research_case
    async def price(model):
        return (1, 1)
    async def forbidden(*args, **kwargs):
        pytest.fail('Không được gọi model khi chưa đủ điều kiện')
    monkeypatch.setattr(llm, 'gia_model', price)
    monkeypatch.setattr(llm, 'complete', forbidden)
    url = f'/api/survey/{sid}/research'
    qid = client.post(url+'/items', json={'kind': 'question', 'title': 'HotpotQA'}).json()['id']
    assert client.post(url+'/answer/'+qid).status_code == 400
    qid = client.post(url+'/items', json={'kind': 'question', 'title': 'zzznomatch123'}).json()['id']
    result = client.post(url+'/answer/'+qid).json()
    assert result['cost_usd'] == 0 and result['findings'] == []


def test_cau_hoi_ket_qua_uu_tien_doan_thuc_nghiem(research_case):
    client, sdb, sid, pid, *_ = research_case
    sdb.put_chunks(pid, [
        {'ord': 0, 'text': 'HotpotQA HotpotQA HotpotQA is a dataset for question answering.'},
        {'ord': 1, 'text': 'HotpotQA results achieve 62.3 EM under the test settings.'},
        {'ord': 2, 'text': 'Results on another benchmark achieve 99.9 accuracy.'},
    ])
    hits = client.get(f'/api/survey/{sid}/research/suggest',
                      params={'q': 'Kết quả trên HotpotQA?'}).json()['hits']
    assert hits[0]['chunk_id'] == pid+'c1'
    assert not any(h['chunk_id'] == pid+'c2' for h in hits)


def test_uu_tien_muc_dung_du_lieu_va_trich_dung_so_lieu(research_case):
    client, sdb, sid, pid, *_ = research_case
    sdb.put_chunks(pid, [
        {'ord': 0, 'section': 'Conclusion', 'text': 'HotpotQA results improve accuracy.'},
        {'ord': 1, 'section': 'Experiments on HotpotQA', 'text':
         'Background description. ' * 35 + 'The baseline achieved 146 test errors. Our method achieved 74 test errors.'},
    ])
    result = client.get(f'/api/survey/{sid}/research/suggest', params={'q': 'HotpotQA results'})
    hit = result.json()['hits'][0]
    assert hit['chunk_id'] == pid + 'c1'
    assert '146 test errors' in hit['text'] and '74 test errors' in hit['text']
