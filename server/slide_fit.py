"""Đo xem slide có tràn khung 1280×720 không, bằng metric font THẬT.

Vì sao cần: `.card` và `.slide` đều `overflow:hidden`, nên chữ thừa **biến mất
lặng lẽ** — nhìn HTML không thấy, đếm chữ cũng không thấy. Bộ đếm chữ chỉ là ước
lượng thô: 40 chữ ngắn vừa khung, 40 chữ dài thì tràn.

Cách làm: dùng Liberation Sans (tương thích metric với Arial, đúng font mà
`_SLIDES_CSS` chỉ định) để đo bề rộng từng chuỗi, ngắt dòng đúng như trình duyệt
làm, rồi cộng chiều cao. Không phải trình duyệt thật nên sai số vài phần trăm —
đủ để bắt tràn, không đủ để căn pixel.
"""

from __future__ import annotations

import functools
from pathlib import Path

from .slide_theme import GEO

_FONT_DIRS = ("/usr/share/fonts", "/usr/local/share/fonts", str(Path.home() / ".fonts"))
_REG = ("LiberationSans-Regular.ttf", "NotoSans-Regular.ttf", "DejaVuSans.ttf")
_BOLD = ("LiberationSans-Bold.ttf", "NotoSans-Bold.ttf", "DejaVuSans-Bold.ttf")


@functools.lru_cache(maxsize=8)
def _find(names: tuple[str, ...]) -> str | None:
    for root in _FONT_DIRS:
        p = Path(root)
        if not p.is_dir():
            continue
        for name in names:
            hit = next(p.rglob(name), None)
            if hit:
                return str(hit)
    return None


@functools.lru_cache(maxsize=64)
def _font(size: int, bold: bool):
    from PIL import ImageFont
    path = _find(_BOLD if bold else _REG)
    if path is None:
        return None
    return ImageFont.truetype(path, size)


def text_w(s: str, size: float, bold: bool = False) -> float:
    f = _font(int(round(size)), bold)
    if f is None:                       # không có font -> ước lượng thô
        return len(s) * size * 0.52
    return f.getlength(s)


def lines(s: str, width: float, size: float, bold: bool = False) -> int:
    """Số dòng khi ngắt chữ trong bề rộng `width`, giống cách trình duyệt làm."""
    s = (s or "").strip()
    if not s:
        return 0
    n, cur = 1, 0.0
    space = text_w(" ", size, bold)
    for w in s.split():
        ww = text_w(w, size, bold)
        if cur and cur + space + ww > width:
            n += 1
            cur = ww
        else:
            cur += (space if cur else 0) + ww
    return n


def block_h(s: str, width: float, size: float, lh: float = 1.45,
            bold: bool = False) -> float:
    return lines(s, width, size, bold) * size * lh


# Hệ số co hiện hành. Không phải hằng số chỉnh tay — `autofit()` dò ra nó.
_S = 1.0


def _sz(px: float) -> float:
    return px * _S


# --------------------------------------------------------------- từng phần


def _card_h(c: dict, width: float) -> float:
    """Chiều cao một thẻ: đệm + hàng tiêu đề + các ý."""
    pad, gap = GEO["CARD_PAD"], 10
    inner = width - GEO["CARD_PAD"] * 2
    # hàng tiêu đề: chip 44px cạnh tiêu đề, tiêu đề chiếm phần còn lại
    tw = inner - GEO["CHIP"] - 12
    title = (c.get("title") or "")
    meta = (c.get("meta") or "").strip()
    h_title = block_h(title, tw, _sz(GEO["CARD_TITLE"]), 1.25, bold=True)
    if meta:
        h_title += block_h(meta, tw, _sz(GEO["CARD_META"]), 1.3)
    head = max(float(GEO["CHIP"]), h_title)
    items = [b for b in (c.get("bullets") or []) if (b or "").strip()]
    h_items = sum(block_h(b, inner - 16, _sz(GEO["CARD_BODY"]), 1.5) for b in items)
    h_items += max(0, len(items) - 1) * 8
    return pad * 2 + head + (gap + h_items if items else 0)


def _cards_h(cards: list[dict], width: float, cols: int) -> float:
    if not cards:
        return 0.0
    gap = GEO["CARD_GAP"]
    cw = (width - gap * (cols - 1)) / cols
    rows = (len(cards) + cols - 1) // cols
    tallest = [0.0] * rows
    for i, c in enumerate(cards):
        r = i // cols
        tallest[r] = max(tallest[r], _card_h(c, cw))
    return sum(tallest) + gap * (rows - 1)


def _bullets_h(items: list[str], width: float, size: float) -> float:
    if not items:
        return 0.0
    h = sum(block_h(b, width - 20, size, 1.55) for b in items)
    return h + max(0, len(items) - 1) * 14


def _head_h(sl: dict, width: float) -> float:
    h = 0.0
    if (sl.get("eyebrow") or "").strip():
        h += _sz(GEO["EYEBROW"]) * 1.3 + 10
    h += block_h(sl.get("headline") or "", width, _sz(GEO["H2"]), 1.16, bold=True)
    if (sl.get("sub") or "").strip():
        h += 10 + block_h(sl["sub"], width, _sz(GEO["SUB"]), 1.45)
    return h + GEO["HEAD_GAP"]


def _callout_h(sl: dict, width: float) -> float:
    co = sl.get("callout") or {}
    if not (co.get("title") or co.get("body")):
        return 0.0
    inner = width - GEO["CARD_PAD"] * 2 - 38 - 16
    h = block_h(co.get("title") or "", inner, _sz(21), 1.3, bold=True)
    if co.get("body"):
        h += 3 + block_h(co["body"], inner, _sz(17), 1.4)
    return max(60.0, h + 30) + 14


def _stats_h(sl: dict, width: float) -> float:
    """Khối số liệu lớn. Trước đây KHÔNG được tính -> slide đè lên chân trang."""
    st = [x for x in (sl.get("stats") or []) if (x or {}).get("value")][:2]
    if not st:
        return 0.0
    col = (width - 56) / max(len(st), 1)
    h = max(block_h(x.get("value") or "", col, _sz(GEO["STAT"]), 1.1, bold=True)
            + 4 + block_h(x.get("label") or "", col, _sz(GEO["STAT_LABEL"]), 1.4)
            for x in st)
    return h + 18


def _fignote_h(sl: dict, width: float) -> float:
    """Chú thích nghiêng dưới hình — cũng bị bỏ sót."""
    n = (sl.get("figure_note") or "").strip()
    if not n or not sl.get("figure"):
        return 0.0
    return block_h(n, width, _sz(GEO["FNOTE"]), 1.4) + 10


def _terms_h(sl: dict, width: float) -> float:
    tm = sl.get("terms") or []
    if not tm:
        return 0.0
    h = 10
    for t in tm:
        h += block_h(f"{t['en']} — {t['gloss']}", width, _sz(GEO["TERM"]), 1.4)
    return h


# ------------------------------------------------------------------- kiểm


def fit(sl: dict, lay: str, scale: float = 1.0) -> dict:
    """Trả về {'need': px cần, 'have': px có, 'over': px thừa} ở hệ số co `scale`.

    `over > 0` nghĩa là chữ bị cắt mất khi hiện ra.
    """
    global _S
    _S = scale
    W, H = GEO["W"], GEO["H"]
    pad_x, pad_t, pad_b = GEO["PAD_X"], GEO["PAD_TOP"], GEO["PAD_BOT"]
    body_w = W - pad_x * 2
    have = H - pad_t - pad_b

    if lay in ("title", "section", "closing", "agenda"):
        return {"need": 0.0, "have": have, "over": 0.0}

    cards = [c for c in (sl.get("cards") or []) if (c or {}).get("title")]
    bl = [b for b in (sl.get("bullets") or []) if (b or "").strip()]

    need = _head_h(sl, body_w)
    tail = (_callout_h(sl, body_w) + _terms_h(sl, body_w)
            + _stats_h(sl, body_w) + _fignote_h(sl, body_w))
    # `.body` có `gap:18px` GIỮA các con. Bỏ sót nó thì mỗi khối thêm vào lại
    # dôi ra 18px, và slide tràn đúng bằng số đó — đã vấp: thêm ô chờ ảnh vào
    # là hộp chốt bị đẩy rơi khỏi khung.
    kids = sum(1 for x in (
        cards or bl, sl.get("stats"), sl.get("callout"), sl.get("terms"),
    ) if x)
    tail += max(0, kids - 1) * 18

    if lay in ("figside", "split"):
        # Cột chữ chỉ chiếm 44% bề ngang. Thẻ xếp trong đó phải là MỘT cột —
        # hai cột trong 44% thì mỗi thẻ chỉ còn ~250px, chữ vỡ vụn rồi tràn.
        col = (body_w - 36) * 0.44
        left = _cards_h(cards, col, 1) if cards else _bullets_h(bl, col, _sz(20))
        # cột phải là hình/sơ đồ, tự co nên không tính vào chiều cao bắt buộc
        need += left + tail
    elif lay == "figwide":
        cols = min(len(cards), 4) if cards else 1
        top = _cards_h(cards, body_w, cols) if cards else _bullets_h(bl, body_w, _sz(20))
        need += top + 200 + tail      # hình dưới 200px thì bảng số liệu không đọc nổi
    elif lay == "figfull":
        need += 200 + tail
    elif lay == "cards":
        cols = min(len(cards), 4) if cards else 1
        need += _cards_h(cards, body_w, cols) + tail
    else:
        need += _bullets_h(bl, body_w, _sz(GEO["BULLET"])) + tail

    return {"need": round(need), "have": round(have), "over": round(need - have)}


# Dải hệ số cho phép co. Dưới 0,78 thì chữ thân thẻ xuống dưới 14px — người ngồi
# xa không đọc nổi, thà báo tràn để người dùng bớt nội dung còn hơn co tiếp.
SCALE_MIN, SCALE_STEP = 0.78, 0.02


def autofit(sl: dict, lay: str) -> float:
    """Hệ số co LỚN NHẤT mà slide vẫn vừa khung. 1.0 nghĩa là không phải co.

    Đây là chỗ thay cho việc chỉnh hằng số bằng tay: đo, co, đo lại, cho tới khi
    vừa. Không vừa được kể cả ở mức nhỏ nhất thì trả về `SCALE_MIN` và để
    `check_slides` báo động — lúc đó vấn đề là nội dung quá nhiều, không phải cỡ chữ.
    """
    scale = 1.0
    while scale > SCALE_MIN:
        if fit(sl, lay, scale)["over"] <= 0:
            return round(scale, 3)
        scale -= SCALE_STEP
    return SCALE_MIN


# Ảnh dưới mức này thì nhét vào cũng không nhìn ra gì — thà không hiện ô chờ còn
# hơn hiện một dải cao 60px rồi mời người dùng bỏ ảnh vào.
MIN_ART_H = 150


def room_for_art(sl: dict, lay: str, scale: float = 1.0) -> int:
    """Chiều cao còn thừa trên slide, đủ để đặt một tấm ảnh hay không (px).

    Trừ thêm một `gap` nữa vì ô chờ ảnh là MỘT CON MỚI của `.body` — nó tự kéo
    theo một khoảng cách với khối đứng trước.
    """
    if lay not in ("cards", "list"):
        return 0
    f = fit(sl, lay, scale)
    return max(0, f["have"] - f["need"] - 18)
