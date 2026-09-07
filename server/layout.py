"""Phát hiện bố cục bằng mô hình — tuỳ chọn, có thì dùng, không có thì thôi.

Heuristic suy vùng hình từ vị trí caption có một trần rõ ràng: trang nào xếp
nhiều bảng và hình cạnh nhau thì không còn cách nào suy ra ranh giới đúng. Mô
hình phát hiện bố cục nhìn thẳng vào trang và trả về hộp bao của từng đối tượng,
nên xử lý được đúng những ca đó.

**Hai backend, chọn bằng `LAYOUT_BACKEND`:**

- `docling` — RT-DETR huấn luyện trên DocLayNet. Trả về cả chữ lẫn thứ tự đọc.
- `mineru` — PP-DocLayoutV2 của MinerU. Chỉ trả **khung**, không trả chữ; nhưng
  chữ vốn do PyMuPDF cấp (xem `parser.blocks_from_layout`) nên không thiếu gì.

Vì sao thêm MinerU: **docling bỏ sót công thức hiển thị.** Đo trên bài
arXiv:2602.15922 (36 trang, 6 công thức đánh số) — docling bắt được 8 vùng
`formula` nhưng đường heuristic thì 0, còn PP-DocLayoutV2 bắt đúng từng cái ở
đúng trang (5, 6×2, 22, 23×2) và **phân biệt `display_formula` với
`inline_formula`**, thứ docling gộp làm một. MinerU dẫn đầu OmniDocBench v1.5 về
công thức (CDM 88,46%), trên cả GPT-4o và Gemini-2.5-Pro.

Cài thêm để bật:  pip install docling   ·   pip install "mineru[pipeline]"

Không cài thì mọi thứ vẫn chạy bình thường bằng heuristic — module này chỉ báo
`available() == False` và parser tự quay về đường cũ.
"""

from __future__ import annotations

import os
import threading

_lock = threading.Lock()
_converter = None
_mineru = None
_checked = False
_ok = False

_TAT = ("off", "none", "0", "heuristic")


def backend() -> str:
    """Backend đang chọn: `docling`, `mineru`, hay `off`.

    Không đặt biến thì tự dò: có `mineru` thì dùng nó (bắt công thức tốt hơn),
    không thì `docling`. Đặt tên tường minh vẫn thắng, kể cả khi gói kia có mặt —
    hai lần đo cùng một PDF ra hai kết quả khác nhau là chuyện đủ khó chịu để
    đáng có một biến môi trường chốt lại.
    """
    ten = os.getenv("LAYOUT_BACKEND", "").lower().strip()
    if ten in _TAT:
        return "off"
    if ten in ("docling", "mineru"):
        return ten
    for ung, mod in (("mineru", "mineru"), ("docling", "docling")):
        try:
            __import__(mod)
            return ung
        except Exception:  # noqa: BLE001
            continue
    return "off"


def available() -> bool:
    """Có dùng được backend mô hình không (đã cài, và chưa bị tắt bằng biến môi trường)."""
    global _checked, _ok
    if not _checked:
        _checked = True
        _ok = backend() != "off"
    return _ok


def _get_converter():
    """Dựng converter một lần rồi dùng lại — lần đầu phải tải trọng số về."""
    global _converter
    with _lock:
        if _converter is None:
            from docling.document_converter import DocumentConverter
            _converter = DocumentConverter()
        return _converter


# ------------------------------------------------------------- backend MinerU

# DPI dựng ảnh cho mô hình. PP-DocLayoutV2 tự co ảnh về 800×800 nên dựng to hơn
# không làm nó chính xác thêm, chỉ tốn RAM; 144 là mức đủ để khung nhỏ (chỉ số,
# số công thức) không bị bẹp khi thu nhỏ.
MINERU_DPI = 144

# Số trang chạy một lượt. Ảnh 144dpi cỡ ~1200×1600, mỗi trang vài MB ở dạng
# uint8; 8 trang một lượt là đủ nhanh mà không thổi VRAM của card 8GB.
MINERU_BATCH = 8

# Nhãn PP-DocLayoutV2 -> loại khối của tool. Cái gì không có ở đây thì bỏ qua.
#
# `inline_formula` CỐ Ý không có mặt, và đây là chỗ dễ làm hỏng nhất: nó là vùng
# con nằm **trong** một dòng chữ. Đưa nó vào `items` thì `assign_spans` gán glyph
# vào khung nhỏ nhất chứa tâm span, tức mọi ký hiệu toán giữa câu bị bốc khỏi
# đoạn văn và đoạn bị xé vụn. Đo trên bài arXiv:2602.15922: 13 vùng
# `inline_formula` chỉ riêng trang 6.
#
# `reference` cũng bỏ: nó là khung BAO của cả danh sách, còn `reference_content`
# mới là từng mục. Giữ cả hai thì mỗi mục bị đếm hai lần.
MINERU_LABELS = {
    "text": "para",
    "abstract": "para",
    "content": "para",
    "vertical_text": "para",
    "paragraph_title": "heading",
    "doc_title": "title",
    "figure_title": "caption",
    "display_formula": "equation",
    "algorithm": "equation",
    "reference_content": "reference",
    "footnote": "footnote",
}

# Vùng hình/bảng — đi vào `regions`, không vào `items`.
MINERU_REGIONS = {"image": "figure", "chart": "figure", "table": "table"}

# Rác lề trang. Liệt kê tường minh để `_mineru_read` biết mình đang **cố ý** bỏ,
# chứ không phải quên: nhãn lạ sẽ lọt vào `_mineru_bo_qua` và hiện ra lúc soát.
MINERU_NOISE = {"header", "footer", "number", "aside_text", "seal",
                "header_image", "footer_image", "vision_footnote",
                "formula_number", "reference", "inline_formula"}


# Khoảng hở tối đa (point) để coi hai vùng là ô con của CÙNG một hình.
# Đo trên arXiv:2602.15922: ô con của hình băng ngang trang 1 cách nhau 0,5pt,
# hai ô cạnh nhau ở trang 11 cách 6pt, còn hai hình khác nhau ở trang 15 cách
# 371pt. 12pt nằm gọn giữa hai nhóm đó.
MINERU_MERGE_GAP = 12.0


def _gop_vung(regions: list[dict], caps: list[dict]) -> list[dict]:
    """Gộp ô con của cùng một hình thành một vùng.

    PP-DocLayoutV2 dò từng **ô** chứ không dò cả hình: hình băng ngang đầu bài
    arXiv:2602.15922 ra **28 vùng** riêng. Để nguyên thì `apply_layout` ghép
    caption với ô gần nhất, và người đọc nhận đúng một ô con thay cho cả hình —
    đo được ảnh hẹp nhất còn 445px.

    Hai điều kiện, cần cả hai:
    - khoảng hở ≤ `MINERU_MERGE_GAP` theo **cả hai** trục;
    - **không có caption nào nằm chen giữa** — caption chen giữa nghĩa là hai
      hình khác nhau, gộp lại là mất một hình và cắt sai cả hai.
    """
    out: list[dict] = []
    for pno in sorted({r["page"] for r in regions}):
        cua_trang = [r for r in regions if r["page"] == pno]
        cap_y = [((c["bbox"][1] + c["bbox"][3]) / 2, c["bbox"][0], c["bbox"][2])
                 for c in caps if c["page"] == pno]
        doi = True
        while doi:
            doi = False
            for i in range(len(cua_trang)):
                for j in range(i + 1, len(cua_trang)):
                    a, b = cua_trang[i], cua_trang[j]
                    if a["kind"] != b["kind"]:
                        continue
                    ax, ay = a["bbox"], b["bbox"]
                    gx = max(0.0, max(ax[0], ay[0]) - min(ax[2], ay[2]))
                    gy = max(0.0, max(ax[1], ay[1]) - min(ax[3], ay[3]))
                    if gx > MINERU_MERGE_GAP or gy > MINERU_MERGE_GAP:
                        continue
                    lo, hi = min(ax[3], ay[3]), max(ax[1], ay[1])
                    trai, phai = min(ax[0], ay[0]), max(ax[2], ay[2])
                    if any(lo < cy < hi and cx1 > trai and cx0 < phai
                           for cy, cx0, cx1 in cap_y):
                        continue
                    a["bbox"] = [min(ax[0], ay[0]), min(ax[1], ay[1]),
                                 max(ax[2], ay[2]), max(ax[3], ay[3])]
                    cua_trang.pop(j)
                    doi = True
                    break
                if doi:
                    break
        out.extend(cua_trang)
    return out


def _mineru_device() -> str:
    """`cuda` nếu có GPU, ngược lại `cpu`.

    Đo trên bài 36 trang: GPU 21,2s so với CPU 35,2s, đỉnh 669 MB VRAM. Chênh
    lệch khiêm tốn nên đừng đòi GPU cho bằng được — nhưng có thì dùng.
    """
    if os.getenv("MINERU_DEVICE"):
        return os.environ["MINERU_DEVICE"]
    try:
        import torch
        return "cuda" if torch.cuda.is_available() else "cpu"
    except Exception:  # noqa: BLE001
        return "cpu"


def _get_mineru():
    """Nạp PP-DocLayoutV2 một lần rồi dùng lại — lần đầu phải tải trọng số về."""
    global _mineru
    with _lock:
        if _mineru is None:
            from mineru.backend.pipeline.model_init import AtomModelSingleton
            from mineru.backend.pipeline.model_list import AtomicModel
            from mineru.utils.enum_class import ModelPath
            from mineru.utils.models_download_utils import (
                auto_download_and_get_model_root_path)
            w = os.path.join(
                auto_download_and_get_model_root_path(ModelPath.pp_doclayout_v2),
                ModelPath.pp_doclayout_v2)
            _mineru = AtomModelSingleton().get_atom_model(
                atom_model_name=AtomicModel.Layout,
                pp_doclayout_v2_weights=str(w),
                device=_mineru_device())
        return _mineru


def _mineru_read(pdf_path: str) -> dict:
    """Dò bố cục bằng PP-DocLayoutV2, trả đúng hợp đồng của `read()`.

    Khác `docling`: mô hình này **không trả chữ**, chỉ trả khung. Không sao —
    `parser.blocks_from_layout` lấy chữ từ PyMuPDF theo khung (`assign_spans`) và
    chỉ dùng `item["text"]` làm đường lùi. Phân công vốn đã là *"mô hình quyết
    định khối nào ở đâu, PyMuPDF cấp glyph"*.

    Toạ độ mô hình trả về tính bằng **pixel của ảnh đã dựng**, nên phải quy về
    point (`72 / MINERU_DPI`) — quên là mọi khung lệch theo đúng tỉ lệ đó.
    """
    import fitz
    import numpy as np

    model = _get_mineru()
    scale = 72.0 / MINERU_DPI
    items: list[dict] = []
    regions: list[dict] = []
    bo_qua: set[str] = set()

    with fitz.open(pdf_path) as doc:
        anh, so_trang = [], []
        for pno in range(len(doc)):
            pm = doc[pno].get_pixmap(dpi=MINERU_DPI)
            a = np.frombuffer(pm.samples, dtype=np.uint8)
            anh.append(a.reshape(pm.height, pm.width, pm.n)[:, :, :3])
            so_trang.append(pno)

        ket = model.batch_predict(anh, batch_size=MINERU_BATCH)

        for pno, boxes in zip(so_trang, ket):
            # Số công thức — "(3)" ở lề phải — gộp vào chính công thức của dòng
            # đó thay vì bỏ đi. Bỏ hẳn thì glyph "(3)" không thuộc khung nào,
            # `recover_uncovered` nhặt lên và đẻ ra một khối văn bản chỉ có "(3)".
            so_ct = [b for b in boxes if b.get("label") == "formula_number"]
            for b in boxes:
                nhan = str(b.get("label") or "")
                x0, y0, x1, y1 = (float(v) * scale for v in b["bbox"])
                if nhan in MINERU_REGIONS:
                    regions.append({"page": pno, "bbox": [x0, y0, x1, y1],
                                    "kind": MINERU_REGIONS[nhan], "caption": ""})
                    continue
                kind = MINERU_LABELS.get(nhan)
                if kind is None:
                    if nhan not in MINERU_NOISE:
                        bo_qua.add(nhan)
                    continue
                if kind == "equation":
                    for s in so_ct:
                        sx0, sy0, sx1, sy1 = (float(v) * scale for v in s["bbox"])
                        if sy0 < y1 and sy1 > y0:        # cùng dải ngang
                            x0, y0 = min(x0, sx0), min(y0, sy0)
                            x1, y1 = max(x1, sx1), max(y1, sy1)
                items.append({
                    "kind": kind, "text": "", "marker": "", "level": 0,
                    "page": pno, "bbox": [x0, y0, x1, y1],
                })

    if bo_qua:
        print(f"[layout] MinerU trả nhãn chưa ánh xạ, đã bỏ: {sorted(bo_qua)}")
    caps = [it for it in items if it["kind"] == "caption"]
    return {"items": items, "regions": _gop_vung(regions, caps)}



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
    if backend() == "mineru":
        return _mineru_read(pdf_path)
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
    parser thì chắc hơn là ghép theo khoảng cách. MinerU không gắn caption cho
    vùng nên trả rỗng, và `apply_layout` tự rơi về ghép theo khoảng cách dọc.
    """
    if backend() == "mineru":
        return _mineru_read(pdf_path)["regions"]
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
