"""Hệ thiết kế của bộ slide — dùng chung cho bản xuất HTML và bản xem trước.

Mọi con số ở đây tính trên khung **1280×720 px**. Bản xem trước bên `style.css`
quy sang `cqw` (1280px = 100cqw, nên 1px = 0,078125cqw), bản `.pptx` quy sang
point (1px = 0,75pt). Ba nơi, một bộ số.

Bố cục lấy theo bộ slide mẫu người dùng đưa: nhãn phần in hoa màu nhấn ở trên,
tiêu đề đậm lớn, rồi nội dung gói trong **thẻ nền pastel** thay vì gạch đầu dòng
trần. Màu thẻ luân phiên theo thứ tự — công cụ tự chọn, không hỏi model, để cả
bộ nhìn nhất quán.
"""

from __future__ import annotations

# Bốn màu thẻ luân phiên. Nền rất nhạt để chữ đen vẫn đủ tương phản khi chiếu.
CARD_TINTS = ("#e9eefc", "#ddf3f5", "#e4f5ea", "#fdefe2")
CHIP_COLORS = ("#2563eb", "#0d9488", "#16a34a", "#ea580c")

# Icon vẽ bằng path SVG 24×24, nét trắng trên chip màu đặc. Giữ bộ nhỏ và đặt
# tên theo *ý nghĩa* chứ không theo hình, để model chọn đúng ngữ cảnh.
ICONS = {
    "target": "M12 2a10 10 0 1 0 0 20 10 10 0 0 0 0-20zm0 5a5 5 0 1 0 0 10 5 5 0 0 0 0-10zm0 3.5a1.5 1.5 0 1 1 0 3 1.5 1.5 0 0 1 0-3z",
    "check": "M9 16.2 4.8 12l-1.4 1.4L9 19 21 7l-1.4-1.4z",
    "warn": "M12 2 1 21h22zm0 6 7.5 13h-15zm-1 4v4h2v-4zm0 5v2h2v-2z",
    "data": "M12 2c-4.4 0-8 1.3-8 3v14c0 1.7 3.6 3 8 3s8-1.3 8-3V5c0-1.7-3.6-3-8-3zm0 2c3.9 0 6 1 6 1s-2.1 1-6 1-6-1-6-1 2.1-1 6-1zm6 15s-2.1 1-6 1-6-1-6-1v-2.3c1.6.8 3.8 1.3 6 1.3s4.4-.5 6-1.3zm0-5s-2.1 1-6 1-6-1-6-1V9.7c1.6.8 3.8 1.3 6 1.3s4.4-.5 6-1.3z",
    "chart": "M4 20h16v2H2V2h2zm3-2V9h3v9zm5 0V4h3v14zm5 0v-6h3v6z",
    "eye": "M12 5C6 5 2 12 2 12s4 7 10 7 10-7 10-7-4-7-10-7zm0 12c-4 0-7-4-7.7-5C5 11 8 7 12 7s7 4 7.7 5C19 13 16 17 12 17zm0-8a3 3 0 1 0 0 6 3 3 0 0 0 0-6z",
    "bolt": "M13 2 4 14h6l-1 8 9-12h-6z",
    "gear": "M19.4 13a7.8 7.8 0 0 0 0-2l2-1.6-2-3.4-2.4 1a7.6 7.6 0 0 0-1.7-1L15 3H9l-.3 2.9a7.6 7.6 0 0 0-1.7 1l-2.4-1-2 3.4L4.6 11a7.8 7.8 0 0 0 0 2l-2 1.6 2 3.4 2.4-1a7.6 7.6 0 0 0 1.7 1L9 21h6l.3-2.9a7.6 7.6 0 0 0 1.7-1l2.4 1 2-3.4zM12 15.5a3.5 3.5 0 1 1 0-7 3.5 3.5 0 0 1 0 7z",
    "layers": "m12 2 10 5.5-10 5.5L2 7.5zm0 12.3 8.1-4.4 1.9 1.1-10 5.5-10-5.5 1.9-1.1zm0 4.4 8.1-4.4 1.9 1.1-10 5.6-10-5.6 1.9-1.1z",
    "link": "M10.6 13.4a1 1 0 0 1 0-1.4l1.4-1.4a1 1 0 0 1 1.4 1.4l-1.4 1.4a1 1 0 0 1-1.4 0zM7.8 16.2a4 4 0 0 1 0-5.7l2.8-2.8 1.4 1.4-2.8 2.8a2 2 0 0 0 2.9 2.9l2.8-2.8 1.4 1.4-2.8 2.8a4 4 0 0 1-5.7 0zm8.4-8.4a4 4 0 0 1 0 5.7l-2.8 2.8-1.4-1.4 2.8-2.8a2 2 0 0 0-2.9-2.9L9.1 9.9 7.7 8.5l2.8-2.8a4 4 0 0 1 5.7 0z",
    "doc": "M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8zm-1 7V3.5L18.5 9zM8 13h8v2H8zm0 4h8v2H8z",
    "search": "M15.5 14h-.8l-.3-.3a6.5 6.5 0 1 0-.7.7l.3.3v.8l5 5 1.5-1.5zm-6 0a4.5 4.5 0 1 1 0-9 4.5 4.5 0 0 1 0 9z",
}

# Khung, khoảng cách, cỡ chữ — px trên 1280×720
GEO = {
    "W": 1280, "H": 720,
    "PAD_X": 60, "PAD_TOP": 44, "PAD_BOT": 72,   # 72 = lề + chỗ cho chân slide
    "EYEBROW": 15, "H1": 48, "H2": 42, "SUB": 20,
    "CARD_TITLE": 21, "CARD_META": 14, "CARD_BODY": 18,
    "BULLET": 21, "FNOTE": 17, "STAT": 40, "STAT_LABEL": 16,
    "TERM": 15, "FOOT": 13,
    "CARD_RADIUS": 12, "CHIP": 38, "CHIP_RADIUS": 9,
    "CARD_PAD": 18, "CARD_GAP": 16, "HEAD_GAP": 18,
}


def icon_svg(name: str, size: int = 22, color: str = "#fff") -> str:
    """Một icon thành thẻ <svg> nội tuyến. Tên lạ thì trả về rỗng, không vỡ."""
    d = ICONS.get((name or "").strip().lower())
    if not d:
        return ""
    return (f"<svg viewBox='0 0 24 24' width='{size}' height='{size}' "
            f"fill='{color}' aria-hidden='true'><path d='{d}'/></svg>")


def card_tint(i: int) -> str:
    return CARD_TINTS[i % len(CARD_TINTS)]


def chip_color(i: int) -> str:
    return CHIP_COLORS[i % len(CHIP_COLORS)]


# --------------------------------------------------------- prompt vẽ minh hoạ

# Công cụ KHÔNG tự gọi model sinh ảnh (xem `upload_slide_image` bên main.py).
# Nó chỉ dựng sẵn prompt để người dùng mang sang công cụ vẽ mình tin dùng, rồi
# thả ảnh về. Prompt viết bằng tiếng Anh vì model vẽ hiểu tiếng Anh tốt hơn hẳn,
# và ảnh không có chữ nên ngôn ngữ prompt không lộ ra sản phẩm.
_ART_STYLE = (
    "Clean minimal flat vector editorial illustration. "
    "Strict palette: deep navy #16264a, blue #2563eb, teal #0d9488, on PURE WHITE "
    "background (#ffffff exactly — no cream, no off-white). Generous white space, "
    "thick even strokes, geometric shapes, no gradients, no shadows, no 3D. "
    "ABSOLUTELY NO TEXT, no letters, no numbers, no labels anywhere in the image. "
    "Centered composition, square 1:1."
)


def art_prompt(sl: dict) -> str:
    """Prompt vẽ ảnh minh hoạ cho một slide, dựng từ chính nội dung slide."""
    bits = [(sl.get("headline") or "").strip()]
    for c in (sl.get("cards") or [])[:3]:
        if (t := ((c or {}).get("title") or "").strip()):
            bits.append(t)
    for b in (sl.get("bullets") or [])[:2]:
        if (b or "").strip():
            bits.append(b.strip())
    idea = " · ".join(x for x in bits if x)[:420]
    return (
        "Conceptual illustration for a scientific presentation slide.\n"
        f"The slide argues: {idea}\n"
        "Depict the IDEA abstractly with simple symbols (documents, nodes, links, "
        "arrows, layers, filters). Do NOT draw a chart, a graph, real data or a "
        "screenshot — this is a decorative concept image, not evidence.\n"
        + _ART_STYLE
    )
