"""Tái hiện giới hạn hiện tại trên dữ liệu tạm, không gọi model hoặc sửa data thật."""
import json
import os
from pathlib import Path
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
os.environ["PAPER_DATA_DIR"] = tempfile.mkdtemp(prefix="loupe-audit-")
os.environ["OPENROUTER_API_KEY"] = "audit-no-model"
for key in ("EMBED_BACKEND", "RERANK_BACKEND", "LAYOUT_BACKEND", "META_LOOKUP"):
    os.environ[key] = "off"

from fastapi.testclient import TestClient
from server import main
from server.survey import db, research

results = {}
quote = "The student achieved 74 test errors compared with 146 errors for the baseline."
results["wrong_answer_with_real_quote_accepted"] = bool(research._validated_findings(
    {"findings": [{"answer": "Mô hình đạt 999 lỗi và kém hơn đối chứng.",
                   "chunk_id": "c1", "quote": quote}]},
    {"c1": {"text": quote, "title": "Nguồn kiểm thử", "page": 2}}))

with TestClient(main.app) as client:
    sid = db.create_survey("Kiểm tra giới hạn truy hồi")["id"]
    pid = db.add_paper(sid, title="Distillation", status="indexed", card={})
    db.put_chunks(pid, [{"ord": 0, "section": "ImageNet results", "text":
                        "Knowledge distillation improves ImageNet accuracy by 2 points."}])
    hits = research.suggest(sid, "distillation MNIST results")
    results["unknown_dataset_returns_imagenet_for_mnist"] = bool(hits)
    results["unknown_dataset_hits"] = [h["text"] for h in hits]
    db.update_paper(pid, card={"datasets": ["MNIST", "ImageNet"]})
    db.put_chunks(pid, [{"ord": 0, "section": "Results", "text":
                        "Knowledge distillation improves accuracy by 2 points."}])
    results["dataset_only_in_card_returns_no_source"] = research.suggest(sid, "MNIST results") == []

    # Cùng câu hỏi và nguồn, đổi một bài không liên quan có thể đổi điểm BM25.
    db.put_chunks(pid, [{"ord": 0, "section": "MNIST", "text": quote}])
    before = research.suggest(sid, "MNIST error results")
    other = db.add_paper(sid, title="Unrelated", status="indexed", card={})
    db.put_chunks(other, [{"ord": 0, "text": "Astronomy observes stars and galaxies. " * 100}])
    after = research.suggest(sid, "MNIST error results")
    results["same_source_after_unrelated_addition"] = [h["chunk_id"] for h in before] == [h["chunk_id"] for h in after]
    results["score_changes_after_unrelated_addition"] = [h["score"] for h in before] != [h["score"] for h in after]
    results["scores"] = {"before": [h["score"] for h in before], "after": [h["score"] for h in after]}

    qid = research.save_item(sid, {"kind": "question", "title": "zzznomatch123"})["id"]
    results["cache_get_without_sources"] = client.get(f"/api/survey/{sid}/research/answer/{qid}").json()
    results["data_dir"] = os.environ["PAPER_DATA_DIR"]

print(json.dumps(results, ensure_ascii=False, indent=2))
