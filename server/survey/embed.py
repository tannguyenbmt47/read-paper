"""Vector hoá bằng model chạy ngay trên máy — không key, không tiền, không giới hạn.

## Vì sao không dùng API embedding

OpenRouter **không phục vụ embedding**, nên đi đường API là phải thêm một key thứ
hai (OpenAI/Voyage/Jina) và trả tiền theo từng chữ nạp vào. Với kho 50 bài, nạp
lại mỗi lần đổi cách cắt đoạn là trả tiền lại từ đầu. Model chạy máy thì tải một
lần rồi miễn phí vĩnh viễn — và máy này có sẵn CUDA cùng `torch`/`transformers`
(docling đã kéo về).

## Vì sao là BGE-M3

Kho là bài báo **tiếng Anh**, câu hỏi là **tiếng Việt**. Đó là bài toán tìm kiếm
xuyên ngữ, và BGE-M3 được huấn luyện đúng cho việc đó (100+ ngôn ngữ, cùng một
không gian vector), cửa sổ 8192 token nên nuốt trọn cả đoạn dài lẫn node tóm lược
của cây mà không phải cắt. Các model embedding tiếng Việt mạnh nhất hiện nay
(`AITeamVN/Vietnamese_Embedding`…) đều là bản tinh chỉnh từ chính BGE-M3.

Đặt `SURVEY_EMBED_MODEL` để đổi sang model khác; đặt `EMBED_BACKEND=off` để tắt
hẳn, lúc đó bộ tìm rơi về BM25 đơn thuần và **vẫn chạy** — chỉ kém hơn.

## Vì sao không cần cơ sở dữ liệu vector

50 bài × ~250 node × 1024 chiều × 4 byte ≈ 50MB. Nhân ma trận toàn bộ bằng numpy
mất vài mili giây. Thêm FAISS/Chroma vào một công cụ chạy local chỉ để tránh một
phép nhân ma trận là đổi một phụ thuộc nặng lấy thứ không đo được.
"""

from __future__ import annotations

import asyncio
import os
import threading

import numpy as np

MODEL_NAME = os.getenv("SURVEY_EMBED_MODEL") or "BAAI/bge-m3"
BACKEND = (os.getenv("EMBED_BACKEND") or "auto").lower()   # auto | off
MAX_LEN = int(os.getenv("SURVEY_EMBED_MAXLEN") or 1024)
BATCH = int(os.getenv("SURVEY_EMBED_BATCH") or 8)

_lock = threading.Lock()
_state: dict = {"tried": False, "model": None, "tok": None, "dim": 0, "err": "", "dev": ""}


def enabled() -> bool:
    return BACKEND != "off"


def status() -> dict:
    """Trạng thái để giao diện nói thật với người dùng thay vì im lặng chạy kém."""
    return {"backend": BACKEND, "model": MODEL_NAME, "ready": _state["model"] is not None,
            "tried": _state["tried"], "dim": _state["dim"],
            "device": _state["dev"], "err": _state["err"]}


def _load() -> bool:
    """Nạp model một lần, có khoá. Hỏng thì ghi lý do và **không** ném lỗi.

    Không tải được model (mạng hỏng, hết đĩa, máy yếu) không được phép làm chết
    cả tính năng — bộ tìm còn BM25 để chạy tiếp. Nhưng lý do phải hiện ra, vì
    chạy kém âm thầm là kiểu hỏng khó phát hiện nhất.
    """
    if not enabled():
        return False
    if _state["model"] is not None:
        return True
    with _lock:
        if _state["model"] is not None:
            return True
        if _state["tried"] and _state["err"]:
            return False
        _state["tried"] = True
        try:
            import torch
            from transformers import AutoModel, AutoTokenizer

            dev = "cuda" if torch.cuda.is_available() else "cpu"
            tok = AutoTokenizer.from_pretrained(MODEL_NAME)
            model = AutoModel.from_pretrained(
                MODEL_NAME, dtype=torch.float16 if dev == "cuda" else torch.float32)
            model.to(dev).eval()
            _state.update(model=model, tok=tok, dev=dev,
                          dim=int(model.config.hidden_size), err="")
            return True
        except Exception as e:                            # noqa: BLE001
            _state["err"] = f"{type(e).__name__}: {e}"[:300]
            return False


def _encode_sync(texts: list[str]) -> np.ndarray:
    import torch

    out = []
    tok, model, dev = _state["tok"], _state["model"], _state["dev"]
    with torch.inference_mode():
        for i in range(0, len(texts), BATCH):
            batch = [t[:8000] or " " for t in texts[i:i + BATCH]]
            enc = tok(batch, padding=True, truncation=True, max_length=MAX_LEN,
                      return_tensors="pt").to(dev)
            # BGE-M3 lấy vector dense ở token CLS, rồi chuẩn hoá L2. Lấy trung
            # bình theo token là cách của model khác, dùng ở đây sẽ tụt chất lượng.
            hidden = model(**enc).last_hidden_state[:, 0]
            hidden = torch.nn.functional.normalize(hidden, p=2, dim=-1)
            out.append(hidden.float().cpu().numpy())
    return np.concatenate(out) if out else np.zeros((0, _state["dim"]), dtype=np.float32)


async def encode(texts: list[str]) -> np.ndarray | None:
    """Vector hoá một danh sách. Trả `None` nếu backend không dùng được.

    Chạy trong executor: nhân ma trận trên GPU vẫn chặn luồng Python, mà server
    còn phải phục vụ SSE cho các tab khác trong lúc đang nạp bài.
    """
    if not texts or not _load():
        return None
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, _encode_sync, texts)


# BGE-M3 không đòi tiền tố cho câu truy vấn (khác dòng E5 vốn bắt buộc "query:"
# và "passage:"). Giữ hai hàm này làm chỗ móc: đổi sang model dòng E5 thì chỉ sửa
# ở đây, không phải lần theo mọi chỗ gọi.
def as_query(text: str) -> str:
    return text


def as_passage(section: str, ctx: str, text: str) -> str:
    """Vector hoá **cả câu ngữ cảnh lẫn nội dung**, giống hệt bên chỉ mục BM25.

    Đây là phần dense của *Contextual Retrieval*: đoạn "we reach 62.3 EM" không
    có tên phương pháp nào để mà khớp ngữ nghĩa; câu ngữ cảnh đứng trước mới kéo
    được nó về đúng vùng không gian.
    """
    head = " — ".join(p for p in (section or "", ctx or "") if p)
    return f"{head}\n{text}" if head else text


def pack(vecs: np.ndarray) -> list[bytes]:
    v = np.ascontiguousarray(vecs.astype(np.float32))
    return [v[i].tobytes() for i in range(v.shape[0])]


def unpack(blobs: list[bytes], dim: int) -> np.ndarray:
    if not blobs:
        return np.zeros((0, dim), dtype=np.float32)
    return np.frombuffer(b"".join(blobs), dtype=np.float32).reshape(len(blobs), dim)


def top_k(query_vec: np.ndarray, ids: list[str], mat: np.ndarray,
          k: int = 30) -> list[tuple[str, float]]:
    """Cosine similarity trên vector đã chuẩn hoá = tích vô hướng."""
    if not ids or mat.size == 0:
        return []
    scores = mat @ query_vec.astype(np.float32)
    k = min(k, len(ids))
    idx = np.argpartition(-scores, k - 1)[:k]
    idx = idx[np.argsort(-scores[idx])]
    return [(ids[i], float(scores[i])) for i in idx]


# ------------------------------------------------------- cross-encoder rerank
#
# Bi-encoder (phần trên) mã hoá câu hỏi và đoạn **riêng rẽ** rồi so vector, nên
# nhanh và quét được cả kho — nhưng nó không bao giờ nhìn thấy hai thứ cạnh nhau.
# Cross-encoder đọc `(câu hỏi, đoạn)` như MỘT chuỗi và chấm trực tiếp, nên bắt
# được quan hệ mà cosine bỏ sót (phủ định, sai hướng so sánh, đúng chủ đề nhưng
# trả lời câu khác). Đổi lại nó phải chạy một lượt cho mỗi cặp, nên chỉ dùng ở
# bước lọc cuối trên vài chục ứng viên.
#
# Đây là bước có mức cải thiện lớn nhất trong cả chuỗi tìm kiếm theo các phép đo
# công bố, nên đáng cái giá tải model thứ hai.

RERANK_MODEL = os.getenv("SURVEY_RERANK_MODEL") or "BAAI/bge-reranker-v2-m3"
RERANK_BACKEND = (os.getenv("RERANK_BACKEND") or "auto").lower()   # auto | off

_rr: dict = {"tried": False, "model": None, "tok": None, "err": "", "dev": ""}


def rerank_ready() -> bool:
    return _rr["model"] is not None


def rerank_status() -> dict:
    return {"backend": RERANK_BACKEND, "model": RERANK_MODEL,
            "ready": rerank_ready(), "tried": _rr["tried"], "err": _rr["err"]}


def _load_rr() -> bool:
    if RERANK_BACKEND == "off":
        return False
    if _rr["model"] is not None:
        return True
    with _lock:
        if _rr["model"] is not None:
            return True
        if _rr["tried"] and _rr["err"]:
            return False
        _rr["tried"] = True
        try:
            import torch
            from transformers import AutoModelForSequenceClassification, AutoTokenizer

            dev = "cuda" if torch.cuda.is_available() else "cpu"
            tok = AutoTokenizer.from_pretrained(RERANK_MODEL)
            model = AutoModelForSequenceClassification.from_pretrained(
                RERANK_MODEL, dtype=torch.float16 if dev == "cuda" else torch.float32)
            model.to(dev).eval()
            _rr.update(model=model, tok=tok, dev=dev, err="")
            return True
        except Exception as e:                            # noqa: BLE001
            _rr["err"] = f"{type(e).__name__}: {e}"[:300]
            return False


def _rerank_sync(query: str, docs: list[str]) -> list[float]:
    import torch

    tok, model, dev = _rr["tok"], _rr["model"], _rr["dev"]
    scores: list[float] = []
    with torch.inference_mode():
        for i in range(0, len(docs), BATCH):
            pairs = [[query, d[:4000] or " "] for d in docs[i:i + BATCH]]
            enc = tok(pairs, padding=True, truncation=True, max_length=MAX_LEN,
                      return_tensors="pt").to(dev)
            logits = model(**enc).logits.view(-1).float()
            scores.extend(logits.cpu().tolist())
    return scores


async def cross_score(query: str, docs: list[str]) -> list[float] | None:
    """Chấm lại `(câu hỏi, đoạn)` bằng cross-encoder. `None` nếu không dùng được."""
    if not docs or not _load_rr():
        return None
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, _rerank_sync, query, docs)
