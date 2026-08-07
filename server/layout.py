"""Phát hiện bố cục bằng mô hình (Docling) — tuỳ chọn, có thì dùng, không có thì thôi.

Heuristic suy vùng hình từ vị trí caption có một trần rõ ràng: trang nào xếp
nhiều bảng và hình cạnh nhau thì không còn cách nào suy ra ranh giới đúng. Mô
hình phát hiện bố cục nhìn thẳng vào trang và trả về hộp bao của từng đối tượng,
nên xử lý được đúng những ca đó.

Cài thêm để bật:  pip install docling

Không cài thì mọi thứ vẫn chạy bình thường bằng heuristic — module này chỉ báo
`available() == False` và parser tự quay về đường cũ.
"""

from __future__ import annotations

import os
import threading

_lock = threading.Lock()
_converter = None
_checked = False
_ok = False


def available() -> bool:
    """Có dùng được backend mô hình không (đã cài, và chưa bị tắt bằng biến môi trường)."""
    global _checked, _ok
    if os.getenv("LAYOUT_BACKEND", "").lower() in ("off", "none", "0", "heuristic"):
        return False
    if not _checked:
        _checked = True
        try:
            import docling  # noqa: F401
            _ok = True
        except Exception:  # noqa: BLE001
            _ok = False
    return _ok


def _get_converter():
    """Dựng converter một lần rồi dùng lại — lần đầu phải tải trọng số về."""
    global _converter
    with _lock:
        if _converter is None:
            from docling.document_converter import DocumentConverter
            _converter = DocumentConverter()
        return _converter


def _to_top_left(bbox, page_height: float) -> list[float]:
    """Đưa hộp bao của Docling về hệ toạ độ của PyMuPDF.

    Docling mặc định lấy gốc ở **góc dưới-trái** (như PDF gốc), PyMuPDF lấy gốc ở
    **góc trên-trái**. Quên đổi thì mọi khung cắt ra đều lật ngược theo chiều dọc.
    """
    try:
        b = bbox.to_top_left_origin(page_height)
        return [float(b.l), float(b.t), float(b.r), float(b.b)]
    except Exception:  # noqa: BLE001
        pass
    l, t, r, b = float(bbox.l), float(bbox.t), float(bbox.r), float(bbox.b)
    origin = str(getattr(bbox, "coord_origin", "")).upper()
    if "BOTTOM" in origin:
        t, b = page_height - t, page_height - b
    if t > b:
        t, b = b, t
    return [l, t, r, b]


def warmup() -> None:
    """Chạy thử một trang trắng để trả trước chi phí nạp model và biên dịch.

    Lần gọi đầu trong mỗi tiến trình mất hơn một phút cho việc này. Trả trước lúc
    server khởi động thì lần nạp bài đầu tiên của người dùng chỉ còn vài giây.
    """
    import tempfile
    try:
        import fitz
        doc = fitz.open()
        page = doc.new_page()
        page.insert_text((72, 100), "warmup")
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tf:
            doc.save(tf.name)
            path = tf.name
        doc.close()
        detect(path)
        os.unlink(path)
    except Exception:  # noqa: BLE001
        pass  # làm nóng hỏng thì thôi, lần nạp thật vẫn chạy được


# Nhãn Docling -> loại khối của tool. Cái gì không có ở đây thì bỏ qua
# (page_header, page_footer… — rác lề trang, trước đây phải đoán bằng heuristic).
LABELS = {
    "text": "para",
    "paragraph": "para",
    "list_item": "list_item",
    "section_header": "heading",
    "title": "title",
    "caption": "caption",
    "formula": "equation",
    "code": "code",
    "footnote": "footnote",
    "reference": "reference",
}


def read(pdf_path: str) -> dict:
    """Đọc cả cấu trúc văn bản lẫn vùng hình/bảng trong MỘT lần convert.

    Trước đây tool gọi Docling rồi chỉ lấy hộp bao của bảng và hình, vứt đi phần
    còn lại — trong khi thứ đắt nhất (chạy mô hình bố cục + mô hình thứ tự đọc)
    đã trả tiền rồi. Ở đây lấy nốt: thứ tự đọc, ranh giới khối, và nhãn từng khối.

    Trả `{"items": [...], "regions": [...]}`, toạ độ đã đổi về hệ PyMuPDF.
    """
    doc = _get_converter().convert(pdf_path).document
    heights = _page_heights(doc)

    items: list[dict] = []
    for it, _level in doc.iterate_items():        # iterate_items = đúng thứ tự đọc
        label = str(getattr(it, "label", "")).lower()
        kind = LABELS.get(label)
        if kind is None:
            continue
        prov = getattr(it, "prov", None) or []
        if not prov:
            continue
        p = prov[0]
        pno = int(getattr(p, "page_no", 1))
        try:
            bbox = _to_top_left(p.bbox, heights.get(pno, 792.0))
        except Exception:  # noqa: BLE001
            continue
        items.append({
            "kind": kind,
            "text": (getattr(it, "text", "") or "").strip(),
            "marker": (getattr(it, "marker", "") or "") if kind == "list_item" else "",
            "level": int(getattr(it, "level", 0) or 0),
            "page": pno - 1,          # Docling đếm từ 1, PyMuPDF từ 0
            "bbox": bbox,
        })
    return {"items": items, "regions": _regions(doc, heights)}


def _page_heights(doc) -> dict[int, float]:
    out: dict[int, float] = {}
    for no, page in (getattr(doc, "pages", {}) or {}).items():
        size = getattr(page, "size", None)
        if size is not None:
            out[int(no)] = float(getattr(size, "height", 0) or 0)
    return out


def detect(pdf_path: str) -> list[dict]:
    """Trả về [{page, bbox:[x0,y0,x1,y1], kind, caption}] cho mọi bảng và hình.

    `bbox` đã ở hệ toạ độ PyMuPDF (point, gốc trên-trái). `caption` là chú thích
    mà chính Docling gắn cho đối tượng — dùng nó để ghép với block caption của
    parser thì chắc hơn là ghép theo khoảng cách.
    """
    doc = _get_converter().convert(pdf_path).document
    return _regions(doc, _page_heights(doc))


def _regions(doc, heights: dict[int, float]) -> list[dict]:
    """Hộp bao của mọi bảng và hình, toạ độ đã đổi về hệ PyMuPDF."""
    out: list[dict] = []
    groups = (
        ("table", getattr(doc, "tables", None) or []),
        ("figure", getattr(doc, "pictures", None) or []),
    )
    for kind, items in groups:
        for it in items:
            prov = getattr(it, "prov", None) or []
            if not prov:
                continue
            p = prov[0]
            pno = int(getattr(p, "page_no", 1))
            h = heights.get(pno, 792.0)
            try:
                bbox = _to_top_left(p.bbox, h)
            except Exception:  # noqa: BLE001
                continue
            caption = ""
            try:
                caption = (it.caption_text(doc) or "").strip()
            except Exception:  # noqa: BLE001
                caption = ""
            out.append({
                # Docling đánh số trang từ 1, PyMuPDF từ 0
                "page": pno - 1,
                "bbox": bbox,
                "kind": kind,
                "caption": caption,
            })
    return out
