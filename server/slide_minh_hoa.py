"""Người minh họa — vai thứ năm của ban biên tập slide.

## Vì sao cần

Hai lời chê đi liền nhau trên cùng một slide trích đoạn (bài Distilling the
Knowledge, mục 2.1): *"một số trang bị dày thông tin"* và *"câu từ còn gây khó
hiểu nhiều"*. Slide ấy chép nguyên văn bản dịch, gần 800 ký tự, với những câu
giữ nguyên lối nói của bài gốc ("distillation ít chú ý đến việc khớp các logit
âm hơn nhiều so với giá trị trung bình") và thuật ngữ không ai giải nghĩa
("cumbersome model", "zero-mean hóa").

Nguyên văn là cố ý: người dùng từng chê bản tóm tắt "bị mất ý". Nên không bỏ
nguyên văn, mà đổi vai của nó. Mỗi slide dày chữ nhận:

- **"Nói dễ hiểu"** — 2 đến 4 câu kể lại ĐÚNG ý đoạn ấy bằng lời thường, thuật
  ngữ giải nghĩa tại chỗ, không bớt ý, không thêm số. Đây thành chữ chính.
- **Hình vẽ minh họa** (`ve`) — một trong bốn dạng mà `slide-ve.js` tự vẽ theo
  chất sổ tay: dòng chảy, hai phía đối chiếu, trục có các vùng, một ý ở giữa
  với các nhánh.

Nguyên văn lui về cột bên, chữ nhỏ, ghi rõ "Nguyên văn bản dịch". Trích đoạn dài
được tách lại ở `TRAN_TRICH_HINH` cho khỏi dày.

## Vì sao không cào ảnh trên mạng

Người dùng hỏi thẳng. Ba lý do: không biết giấy phép của ảnh mà slide là thứ
đem đi chiếu; ảnh tìm theo từ khoá hay lệch nội dung, và một hình đẹp mà sai ý
tệ hơn không có hình; file tải về phải chiếu được offline. Hình vẽ từ chính chữ
của slide thì đúng ý, của mình, và cùng một bộ vẽ cho cả ba nơi.
"""

from __future__ import annotations

import asyncio

from . import depth, llm, store
from . import slide as sl

KIEU_VE = ("luong", "doi_chieu", "truc", "trung_tam")
DAY_TU = 420            # chữ trên mặt slide (không tính tiêu đề) từ mức này là dày
TRICH_DAY_TU = 150      # trích đoạn nào cũng cần "Nói dễ hiểu" — trừ mẩu vụn
TRAN_SLIDE = 24         # tối đa số slide minh họa mỗi lượt — chặn tiền
ME = 7
# Vai được vẽ hình: vai VĂN XUÔI. Vai đã có cấu trúc riêng (bước đánh số, bảng,
# danh sách tiêu chí, biểu đồ) thì không — đã vấp: slide cơ chế bốn bước nhận
# thêm sơ đồ lặp lại đúng bốn bước ấy, không thêm được gì mà đẩy slide tràn chữ.
VE_VAI = ("doan_dich", "van_de", "y_tuong", "khai_niem", "khoang_trong", "gioi_han",
          "huong_nc", "bang_chung")


def _chu_trich(s: dict) -> str:
    return " ".join(t.get("chu") or "" for t in (s.get("trich") or []) if not t.get("cong_thuc"))


def can_minh_hoa(s: dict) -> bool:
    """Slide dày chữ và chưa có hình thật (hoặc trích đoạn chưa có ô dễ hiểu)."""
    if s.get("vai") not in VE_VAI:
        return False
    if s.get("vai") == "doan_dich":
        return len(_chu_trich(s)) >= TRICH_DAY_TU and not s.get("de_hieu")
    if s.get("hinh") or s.get("ve"):
        return False
    return len(sl._chu_slide(s).replace(s.get("tieu_de") or "", "", 1)) >= DAY_TU


MINH_HOA_TASK = """\
Bạn là người minh họa cho một bộ slide trình bày bài báo khoa học, viết cho nghiên
cứu sinh người Việt CHƯA đọc bài. Mỗi mục dưới đây là một slide dày chữ. Với từng
slide, làm hai việc:

### 1. `de_hieu` — chỉ cho slide có vai "Trích đoạn"
Kể lại ĐÚNG ý của đoạn trích bằng lời thường, 2 đến 4 câu, tối đa 70 chữ.
- Giữ MỌI ý của đoạn, không thêm ý mới, không thêm con số nào.
- Thuật ngữ nào người mới không biết thì giải nghĩa ngay trong câu bằng lời
  thường. Ví dụ "mô hình lớn, cồng kềnh (cumbersome model)" thay cho để trơ tên.
- Câu ngắn, chủ ngữ rõ, nói theo trình tự là gì → làm gì → vì sao.
- Không dấu chấm phẩy, không "tức là", không "nói cách khác".
Slide không phải trích đoạn thì để `de_hieu` rỗng.

### 2. `ve` — một hình vẽ giúp NHÌN ra ý chính của slide
Chọn MỘT dạng hợp với cấu trúc ý, hoặc `null` nếu ý không có cấu trúc để vẽ:
- "luong": một quá trình có thứ tự. `buoc`: 3–5 mục {"nhan": "≤5 chữ", "phu": "≤12 chữ"}.
- "doi_chieu": hai phía đối lập. `trai`/`phai`: {"nhan": "≤5 chữ", "y": ["≤10 chữ", …] 2–3 mục}.
- "truc": một đại lượng thay đổi từ thấp tới cao và điều xảy ra ở từng vùng.
  `ten`: tên đại lượng, `dau`/`cuoi`: nhãn hai đầu (≤3 chữ), `vung`: 2–4 mục
  {"nhan": "≤5 chữ", "mo_ta": "≤12 chữ"} theo thứ tự từ thấp tới cao.
- "trung_tam": một khái niệm và các thành phần/hệ quả của nó. `giua`: ≤5 chữ,
  `nhanh`: 3–5 mục {"nhan": "≤5 chữ", "mo_ta": "≤12 chữ"}.
Chữ trên hình chỉ lấy ý có trong slide. Không thêm con số không có trong slide.

Trả về DUY NHẤT JSON:
{"slides": [{"id": "s13", "de_hieu": "…", "ve": {"kieu": "truc", …} hoặc null}]}
"""


def _cat(x, n: int) -> str:
    return depth.cat_gon(" ".join(str(x or "").split()), n)


def chuan_hoa_ve(v) -> dict | None:
    """Soát hình vẽ: đúng dạng, đủ mục, chữ không quá dài. Sai thì bỏ hẳn —
    hình lệch khuôn vẽ ra méo còn tệ hơn không có hình."""
    if not isinstance(v, dict) or v.get("kieu") not in KIEU_VE:
        return None
    k = v["kieu"]

    def ds(x, lo, hi, f):
        x = [f(i) for i in (x if isinstance(x, list) else []) if isinstance(i, dict)]
        x = [i for i in x if i.get("nhan")]
        return x[:hi] if len(x) >= lo else None

    nm = lambda i: {"nhan": _cat(i.get("nhan"), 36), "mo_ta": _cat(i.get("mo_ta") or i.get("phu"), 90)}
    if k == "luong":
        b = ds(v.get("buoc"), 3, 5, lambda i: {"nhan": _cat(i.get("nhan"), 36), "phu": _cat(i.get("phu"), 90)})
        return {"kieu": k, "buoc": b} if b else None
    if k == "doi_chieu":
        def phia(p):
            if not isinstance(p, dict) or not p.get("nhan"):
                return None
            y = [_cat(t, 80) for t in (p.get("y") or []) if str(t).strip()][:3]
            return {"nhan": _cat(p["nhan"], 36), "y": y} if len(y) >= 1 else None
        t, p = phia(v.get("trai")), phia(v.get("phai"))
        return {"kieu": k, "trai": t, "phai": p} if t and p else None
    if k == "truc":
        z = ds(v.get("vung"), 2, 4, nm)
        return ({"kieu": k, "ten": _cat(v.get("ten"), 40), "dau": _cat(v.get("dau"), 20),
                 "cuoi": _cat(v.get("cuoi"), 20), "vung": z} if z else None)
    z = ds(v.get("nhanh"), 3, 5, nm)
    return {"kieu": k, "giua": _cat(v.get("giua"), 36), "nhanh": z} if z and v.get("giua") else None


def _chu_ve(v: dict) -> str:
    out = []

    def di(x):
        if isinstance(x, dict):
            for val in x.values():
                di(val)
        elif isinstance(x, list):
            for val in x:
                di(val)
        elif isinstance(x, str):
            out.append(x)
    di({k: val for k, val in v.items() if k != "kieu"})
    return " ".join(out)


def _toan_doan(bo: list[dict]) -> dict[str, str]:
    """{mã phần → chữ của CẢ đoạn} cho trích đoạn đã tách thành nhiều phần.

    Đã vấp: phần 2 của một đoạn về nhiệt độ chỉ còn hai câu cuối, người minh họa
    không thấy phần 1 nên vẽ trục ghi cả hai đầu "kém hiệu quả" — bài không nói
    vậy. Phần nào cũng phải được đọc trong ngữ cảnh cả đoạn."""
    out, nhom = {}, []
    for s in bo:
        t = s.get("tiep") if s.get("vai") == "doan_dich" else None
        if not t or t[1] <= 1:
            continue
        if t[0] == 1:
            nhom = []
        nhom.append(s)
        if t[0] == t[1]:
            ca = " ".join(_chu_trich(x) for x in nhom)
            for x in nhom:
                out[x["id"]] = ca
    return out


def _mo_ta_slide(s: dict, ca_doan: str = "") -> str:
    vai = (sl.VAI.get(s.get("vai")) or s.get("vai") or "").split(" — ")[0]
    if s.get("vai") == "doan_dich":
        than = f"Ý chính: {s.get('y_chinh', '')}\nĐoạn trích của slide này: {_chu_trich(s)}"
        if ca_doan:
            than += (f"\nCẢ ĐOẠN, slide này là phần {s['tiep'][0]}/{s['tiep'][1]} (để hiểu ngữ cảnh; "
                     f"`de_hieu` chỉ kể phần của slide này, hình vẽ phải đúng với cả đoạn): {ca_doan}")
    else:
        than = sl._chu_slide(s)
    return f"### {s['id']} · vai {vai}\nTiêu đề: {s.get('tieu_de', '')}\n{than}"


def tach_lai(bo: list[dict]) -> list[dict]:
    """Trích đoạn dày tách lại ở `TRAN_TRICH_HINH`, mã mới cho phần mới.

    Không đánh lại mã cả bộ (`_danh_ma`): biên bản của các vai trước trỏ tới
    slide theo mã, đánh lại là biên bản chỉ nhầm slide."""
    dung = {s["id"] for s in bo}
    so = max([int(i[1:]) for i in dung if i[1:].isdigit()] + [0])
    out = []
    for s in bo:
        if s.get("vai") != "doan_dich" or len(_chu_trich(s)) <= sl.TRAN_TRICH_HINH or s.get("de_hieu"):
            out.append(s)
            continue
        phan = sl._tach_trich([s], tran_ep=sl.TRAN_TRICH_HINH)
        for k, p in enumerate(phan):
            if k:
                so += 1
                p["id"] = f"s{so}"
            out.append(p)
    return out


async def minh_hoa(doc_id: str, model: str, usage: llm.Usage):
    """Async generator: ("stage", …) rồi ("xong", {"log": […]}). Ghi thẳng vào bộ."""
    from .pipeline import NO_REASONING, so_bia

    doc = store.load(doc_id)
    deck = sl.lay(doc)
    deck["bo"] = tach_lai(deck["bo"])          # tách trước, rồi minh họa từng phần
    doc["slides"] = deck
    store.save(doc)

    chon = [s for s in deck["bo"] if can_minh_hoa(s)][:TRAN_SLIDE]
    log: list[dict] = []
    if not chon:
        yield "xong", {"log": log}
        return
    yield "stage", {"msg": f"người minh họa viết lời dễ hiểu và vẽ hình cho {len(chon)} slide dày chữ",
                    "vai": "minh_hoa"}

    ca = _toan_doan(deck["bo"])

    async def me(lo: list[dict]) -> dict:
        try:
            raw, u = await llm.complete(
                [{"role": "system", "content": MINH_HOA_TASK},
                 {"role": "user", "content": "\n\n".join(_mo_ta_slide(s, ca.get(s["id"], "")) for s in lo)}],
                model=model, session_id=doc_id, max_tokens=350 * len(lo) + 500,
                temperature=0.3, reasoning=NO_REASONING)
            usage.add(u)
            return {str(x.get("id")): x for x in ((llm.extract_json(raw) or {}).get("slides") or [])
                    if isinstance(x, dict)}
        except Exception:                   # noqa: BLE001 — hỏng mẻ nào thì mẻ đó giữ nguyên
            return {}

    kq: dict[str, dict] = {}
    for d in await asyncio.gather(*(me(chon[i:i + ME]) for i in range(0, len(chon), ME))):
        kq.update(d)

    # Ghi liền mạch: load → sửa → save, không await chen giữa.
    doc = store.load(doc_id)
    deck = sl.lay(doc)
    for s in deck["bo"]:
        if s.get("ve") and s.get("vai") not in VE_VAI:
            s.pop("ve")                       # hình vẽ ở vai có cấu trúc riêng từ lượt cũ
        r = kq.get(s["id"])
        if not r:
            continue
        nguon = s.get("nguon") or [t.get("id") for t in s.get("trich") or [] if t.get("id")]
        dh = sl._sach(r.get("de_hieu")) if s.get("vai") == "doan_dich" else ""
        if dh and so_bia(doc, nguon, dh):
            log.append({"vai": "minh_hoa", "o": s["id"], "viec": "lời dễ hiểu bị trả về (thêm số không có trong bài)"})
            dh = ""
        if dh and len(dh.split()) > 95:
            dh = ""
        v = chuan_hoa_ve(r.get("ve"))
        if v and so_bia(doc, nguon, _chu_ve(v)):
            log.append({"vai": "minh_hoa", "o": s["id"], "viec": "hình vẽ bị trả về (thêm số không có trong bài)"})
            v = None
        if dh:
            s["de_hieu"] = dh
        if v and not s.get("hinh"):
            s["ve"] = v
        if dh or v:
            log.append({"vai": "minh_hoa", "o": s["id"],
                        "viec": " và ".join(x for x in ("viết lời dễ hiểu" if dh else "",
                                                       f"vẽ hình {_TEN_KIEU[v['kieu']]}" if v else "") if x),
                        "can": dh[:200]})
    doc["slides"] = deck
    store.save(doc)
    yield "xong", {"log": log}


_TEN_KIEU = {"luong": "dòng chảy", "doi_chieu": "hai phía", "truc": "trục", "trung_tam": "sơ đồ nhánh"}

