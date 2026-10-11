"""Ban biên tập cho bộ slide — soát và sửa TRƯỚC khi đem đi chiếu.

## Vì sao cần

Bộ chi tiết (30/45 phút) viết chữ theo mẻ, và một mẻ có thể viết lời nói cho
slide mà bỏ sót trường chính của vai. Đã gặp thật: slide "Mong muốn" của bài
Distilling the Knowledge chỉ còn tiêu đề — `tieu_chi` rỗng, không một cảnh báo
nào, và người dùng thấy nó ở đúng giữa màn chiếu. `chuan_hoa` soát từng trường
nhưng không có ai hỏi câu "slide này có NỘI DUNG không".

Và lỗi còn lại lớn nhất mà phép chấm bằng subagent chỉ nhìn ảnh từng bắt
(CLAUDE.md, mục Pass 4): **kết luận mâu thuẫn với chính số liệu** ("luôn tốt
hơn", "vượt mọi baseline"). Model không thấy ảnh bảng; phải có người đọc lại
kết luận cạnh số liệu thật.

## Bốn vai, cùng khuôn với `survey/bientap.py`

| Vai | Chạy bằng | Việc |
|---|---|---|
| Thư ký | máy, $0 | bắt slide trống thân (vai thiếu trường chính) |
| Người xem thử | model của bộ | đọc CẢ BỘ như khán giả: chỉ chữ trên slide, không lời nói, không bài gốc |
| Người kiểm chứng | model của bộ | đặt kết luận của slide bằng chứng cạnh đoạn nguồn và chữ trong bảng |
| Biên tập viên | `slide.viet_lai` | viết lại đúng slide bị chỉ ra, kèm lời chê làm gợi ý |

**Người xem thử không có lời nói.** Khán giả chỉ thấy slide; lời nói là thứ
người trình bày nói thêm. Đưa lời nói vào là người xem thử tự lấp chỗ trống
bằng nó và báo "ổn" — đúng cái bẫy phép chấm ảnh đã tránh được.

**Biên tập viên là `viet_lai` có sẵn**, không phải một bộ viết thứ hai: cùng
prompt, cùng `chuan_hoa`, cùng chốt số liệu. Bộ viết thứ hai là thứ bản slide cũ
đã chết vì nó.
"""

from __future__ import annotations

import asyncio
import re
import time

from . import llm, store
from . import slide as sl

# Vai nào bắt buộc có danh sách nào, tối thiểu mấy mục — thiếu là slide trống
# thân dù có tiêu đề và lời nói.
LOI_VAI = {
    "yeu_cau": ("tieu_chi", 2), "co_che": ("buoc", 2), "vi_du": ("buoc", 2),
    "cach_lam_tn": ("buoc", 2), "gioi_han": ("muc", 1), "so_sanh": ("hang", 2),
    "dong_lai": ("y", 2), "doan_dich": ("trich", 1),
}
# Vai không do model viết, không soát.
_BO_QUA = ("mo_dau", "lo_trinh")
TRAN_SUA = 8            # tối đa số slide viết lại một lượt — chặn tiền
SONG_SONG = 3


def than_rong(s: dict) -> str:
    """Lý do slide trống thân, hoặc rỗng nếu slide có nội dung."""
    if s.get("vai") in _BO_QUA:
        return ""
    loi = LOI_VAI.get(s.get("vai"))
    if loi and len(s.get(loi[0]) or []) < loi[1]:
        return f"thiếu `{loi[0]}` (cần ít nhất {loi[1]} mục) — slide chỉ còn tiêu đề"
    chu = sl._chu_slide(s).replace(s.get("tieu_de") or "", "", 1)
    if s.get("vai") != "doan_dich" and len(chu.split()) < 10 and not s.get("hinh"):
        return "thân slide gần như không có chữ và không có hình"
    return ""


def chu_cho_khan_gia(s: dict, i: int) -> str:
    """Slide dưới mắt khán giả: tiêu đề + chữ trên mặt slide. KHÔNG có lời nói."""
    than = sl._chu_slide(s)
    if s.get("vai") == "doan_dich":
        # `trich` là danh sách đoạn — chuỗi, hoặc object {chu, anh} khi có công thức.
        doan = [x if isinstance(x, str) else " ".join(str(v) for v in x.values() if isinstance(v, str))
                for x in (s.get("trich") or [])]
        than = (s.get("y_chinh") or "") + " | trích: " + " ".join(doan)[:600]
    hinh = " [có hình]" if s.get("hinh") else ""
    ten = (sl.VAI.get(s.get("vai")) or s.get("vai") or "").split(" — ")[0]
    return f"[{s.get('id')}] slide {i} · {ten}{hinh}\n{than}"


XEM_THU_TASK = """\
Bạn là khán giả của một buổi trình bày bài báo khoa học: nghiên cứu sinh, thông
minh, CHƯA đọc bài. Bạn chỉ thấy chữ trên từng slide theo thứ tự, không nghe lời
người nói. Chỉ ra những slide khiến bạn không theo được:

- "rong": slide gần như không nói gì (chỉ có tiêu đề, hoặc vài chữ không mang tin).
- "thuat_ngu": thuật ngữ, viết tắt, ký hiệu xuất hiện mà chưa slide nào giải nghĩa.
- "nhay_buoc": slide này kết luận điều mà các slide trước chưa dẫn tới.
- "mo_ho": đọc xong không biết thêm điều gì cụ thể.
- "mau_thuan": kết luận trên slide không khớp với số trên chính slide đó, hoặc
  với slide khác.

Không báo lỗi văn phong hay độ dài. Tối đa 10 mục, chỗ cản nhiều nhất trước.
Trả về DUY NHẤT JSON:
{"van_de": [{"slide": "s4", "loai": "rong|thuat_ngu|nhay_buoc|mo_ho|mau_thuan",
             "can": "khán giả cần được nói thêm điều gì, ≤30 chữ"}]}
"""

KIEM_TASK = """\
Bạn là người kiểm chứng của ban biên tập. Mỗi mục là KẾT LUẬN trên một slide bằng
chứng, kèm số liệu trên slide và các đoạn NGUYÊN VĂN của bài (cả chữ trong bảng
nếu có). Chỉ dựa vào các đoạn đã cho, với từng kết luận:

- "dung": các đoạn đỡ được đúng mức kết luận nói.
- "sua": kết luận nói quá, hoặc ngược với số ("luôn tốt hơn" trong khi có cột
  thua, "vượt mọi baseline" trong khi chỉ hơn một số). Viết lại cho ĐÚNG mức bằng
  chứng, giữ độ dài, không thêm con số nào không có trong các đoạn.
- "bo": không đoạn nào đỡ được.

Trả về DUY NHẤT JSON:
{"ket_qua": [{"slide": "s23", "ket_luan": "dung|sua|bo", "cau_moi": "…", "vi_sao": "≤25 chữ"}]}
"""

# Khoá loại vướng là ASCII cho model viết khỏi sai dấu; giao diện hiện bản có dấu.
_TEN_VUONG = {"rong": "slide gần như trống", "thuat_ngu": "thuật ngữ chưa giải nghĩa",
              "nhay_buoc": "nhảy bước", "mo_ho": "mơ hồ", "mau_thuan": "mâu thuẫn với số liệu"}

# Vai có ô kết luận mà người kiểm chứng phải soát.
_O_KET_LUAN = {"bang_chung": "ket_luan", "so_sanh": "ket_luan", "so_lieu": "y_nghia"}


async def _goi(task: str, user: str, model: str, did: str, max_tokens: int,
               usage: llm.Usage) -> dict:
    """Một lượt JSON. Hỏng thì {} — ban biên tập là lớp phụ, hỏng một vai thì
    bộ slide vẫn như cũ chứ không mất."""
    from .pipeline import NO_REASONING
    try:
        raw, u = await llm.complete(
            [{"role": "system", "content": task}, {"role": "user", "content": user}],
            model=model, session_id=did, max_tokens=max_tokens, temperature=0.2,
            reasoning=NO_REASONING)
        usage.add(u)
        return llm.extract_json(raw) or {}
    except Exception:                       # noqa: BLE001
        return {}


def _nguon_chu(doc: dict, s: dict) -> str:
    """Đoạn nguồn của slide + chữ trong bảng/hình gắn kèm — thứ để đối chiếu."""
    from .pipeline import chu_vung_hinh
    tr = doc.get("translations") or {}
    text = {b["id"]: b.get("text") or "" for b in doc.get("blocks") or []}
    out = [f"<<{b}>> {text.get(b, '')[:900]}" + (f"\n(bản dịch) {tr[b][:600]}" if tr.get(b) else "")
           for b in (s.get("nguon") or [])[:4] if b in text]
    if s.get("hinh"):
        bang = chu_vung_hinh(doc, s["hinh"])
        if bang:
            out.append(f"<<chữ trong hình/bảng {s['hinh']}>> {bang[:1500]}")
    return "\n".join(out)


async def soat(doc_id: str):
    """Async generator: ("stage", {...}) rồi ("xong", {"bien_tap", "usage"}).

    Ghi thẳng vào `doc["slides"]` (kể cả `bien_tap`). Mỗi lần ghi là
    load → sửa → save liền mạch, không `await` chen giữa — `viet_lai` chạy song
    song cũng tự ghi theo cùng luật đó (xem bất biến của `stream_chunk`)."""
    from .pipeline import so_bia

    doc = store.load(doc_id)
    deck = sl.lay(doc)
    bo = deck["bo"]
    model = deck.get("model") or doc["model"]
    usage = llm.Usage()            # người xem thử + kiểm chứng — ta tự cộng vào bài
    viet = llm.Usage()             # `viet_lai` — nó đã tự cộng vào bài, chỉ để báo
    log: list[dict] = []
    viec: dict[str, list[str]] = {}
    ngoai_tran: list[str] = []

    # ---- 1. thư ký: slide trống thân
    yield "stage", {"msg": "thư ký soát slide trống thân (miễn phí)", "vai": "thu_ky"}
    for s in bo:
        ly_do = than_rong(s)
        if ly_do:
            viec.setdefault(s["id"], []).append(f"Slide đang trống: {ly_do}. Viết đủ nội dung của vai.")
            log.append({"vai": "thu_ky", "viec": "slide trống thân", "o": s["id"],
                        "truoc": s.get("tieu_de", ""), "sau": ly_do})

    # ---- 2. người xem thử: cả bộ, như khán giả
    yield "stage", {"msg": "người xem thử đọc cả bộ như khán giả (không lời nói, không bài gốc)",
                    "vai": "doc_thu"}
    chu = "\n\n".join(chu_cho_khan_gia(s, i) for i, s in enumerate(bo, 1)
                      if s.get("vai") not in _BO_QUA)
    ids = {s["id"] for s in bo}
    kq = await _goi(XEM_THU_TASK, chu, model, doc_id, 2000, usage)
    mau_thuan: set[str] = set()
    for v in (kq.get("van_de") or [])[:10]:
        if not isinstance(v, dict) or str(v.get("slide")) not in ids:
            continue
        sid, loai, can = str(v["slide"]), str(v.get("loai") or ""), str(v.get("can") or "")
        log.append({"vai": "doc_thu", "viec": _TEN_VUONG.get(loai, loai.replace("_", " ")),
                    "o": sid, "can": can})
        if loai == "mau_thuan":
            mau_thuan.add(sid)
        else:
            viec.setdefault(sid, []).append(f"Khán giả vướng ({_TEN_VUONG.get(loai, loai)}): {can}")

    # ---- 3. người kiểm chứng: kết luận đặt cạnh số liệu thật
    can_kiem = [s for s in bo if _O_KET_LUAN.get(s.get("vai")) and s.get(_O_KET_LUAN[s["vai"]])
                and (s.get("nguon") or s.get("hinh"))]
    can_kiem.sort(key=lambda s: s["id"] not in mau_thuan)
    can_kiem = can_kiem[:8]
    if can_kiem:
        yield "stage", {"msg": f"người kiểm chứng đặt {len(can_kiem)} kết luận cạnh số liệu trong bài",
                        "vai": "kiem_chung"}
        khoi = [f"### {s['id']}\nKết luận: {s[_O_KET_LUAN[s['vai']]]}\n"
                f"Số trên slide: {' · '.join(x.get('gia_tri', '') + ' ' + x.get('nhan', '') for x in s.get('so') or [])}\n"
                f"Đoạn của bài:\n{_nguon_chu(doc, s)}" for s in can_kiem]
        kq = await _goi(KIEM_TASK, "\n\n".join(khoi), model, doc_id, 300 + 200 * len(can_kiem), usage)
        sua: dict[str, tuple[str, str]] = {}
        for r in kq.get("ket_qua") or []:
            if not isinstance(r, dict) or str(r.get("slide")) not in {s["id"] for s in can_kiem}:
                continue
            sid = str(r["slide"])
            if r.get("ket_luan") == "sua" and str(r.get("cau_moi") or "").strip():
                # Chỉ GHI NHẬN ở đây; biên bản ghi "đã sửa" lúc bản sửa thật sự được
                # nhận — bản đầu ghi trước khi soát số, nên biên bản kể một lần sửa
                # mà chốt số bịa đã trả về.
                sua[sid] = (str(r["cau_moi"]).strip(), str(r.get("vi_sao") or ""))
            elif r.get("ket_luan") == "bo":
                viec.setdefault(sid, []).append(
                    "Kết luận trên slide không có đoạn nào của bài đỡ được. Viết lại kết luận "
                    "đúng điều bài nói, đúng mức bằng chứng.")
        # Ghi liền mạch: load → sửa → save, không await chen giữa.
        doc = store.load(doc_id)
        deck = sl.lay(doc)
        for s in deck["bo"]:
            moi, vi_sao = sua.get(s["id"], ("", ""))
            o = _O_KET_LUAN.get(s.get("vai"))
            if not (moi and o):
                continue
            # Câu sửa mang số không có trong bài thì không nhận — đổi sạn này lấy sạn khác.
            if so_bia(doc, s.get("nguon") or [], moi):
                log.append({"vai": "kiem_chung", "o": s["id"],
                            "viec": "bản sửa kết luận bị trả về (mang số không có trong bài)"})
                continue
            log.append({"vai": "kiem_chung", "viec": "sửa kết luận cho khớp số liệu", "o": s["id"],
                        "truoc": s[o], "sau": sl._sach(moi), "can": vi_sao})
            s[o] = sl._sach(moi)
        doc["slides"] = deck
        store.save(doc)

    # ---- 4. biên tập viên: viết lại đúng slide bị chỉ, kèm lời chê làm gợi ý
    # Slide trống đứng đầu hàng (thư ký ghi trước), rồi tới chỗ khán giả vướng.
    # Phần vượt trần không bị lờ đi: nó vào `con_lai` để người dùng biết còn gì.
    chon = list(viec)[:TRAN_SUA]
    ngoai_tran = [sid for sid in viec if sid not in chon]
    if chon:
        yield "stage", {"msg": f"biên tập viên viết lại {len(chon)} slide", "vai": "bien_tap"}
        sem = asyncio.Semaphore(SONG_SONG)

        async def mot(sid: str):
            async with sem:
                goi_y = " ".join(viec[sid])[:480]
                try:
                    _s, u, _t = await sl.viet_lai(doc_id, sid, goi_y)
                    viet.add(llm.Usage(**u))
                    return sid, None
                except Exception as e:      # noqa: BLE001
                    return sid, str(e)

        for sid, loi in await asyncio.gather(*(mot(x) for x in chon)):
            log.append({"vai": "bien_tap", "o": sid,
                        "viec": "viết lại slide" if not loi else f"không viết lại được: {loi[:80]}",
                        "can": " ".join(viec[sid])[:200]})

    # ---- 5. soát lần cuối: slide nào vẫn trống thì gắn cảnh báo, đừng im lặng
    doc = store.load(doc_id)
    deck = sl.lay(doc)
    con_lai = []
    for s in deck["bo"]:
        ly_do = than_rong(s)
        if ly_do:
            canh = f"Slide còn trống sau khi ban biên tập viết lại: {ly_do}."
            if canh not in (s.get("canh_bao") or []):
                s.setdefault("canh_bao", []).append(canh)
            con_lai.append({"kind": "slide_trống", "msg": f"{s['id']} · {s.get('tieu_de', '')}: {ly_do}"})
        elif s["id"] in ngoai_tran:
            con_lai.append({"kind": "chưa_viết_lại",
                            "msg": f"{s['id']} · {s.get('tieu_de', '')}: chưa kịp viết lại "
                                   f"(mỗi lượt soát viết lại tối đa {TRAN_SUA} slide) — "
                                   + " ".join(viec[s["id"]])[:160]})
    dem: dict[str, int] = {}
    for x in log:
        if x["vai"] in ("kiem_chung", "bien_tap") and not re.search(r"không viết lại|trả về", x["viec"]):
            dem[x["vai"]] = dem.get(x["vai"], 0) + 1
    # Thư ký chỉ PHÁT HIỆN slide trống; slide được sửa thật là việc của hai vai sau.
    ca = llm.Usage(**usage.dict())
    ca.add(viet)
    bt = {"sua": sum(dem.values()), "theo_vai": dem, "nhat_ky": log[:120], "con_lai": con_lai,
          "chi_phi": round(ca.cost, 5), "luc": time.time()}
    deck["bien_tap"] = bt
    doc["slides"] = deck
    tong = llm.Usage(**doc.get("usage", {}))
    tong.add(usage)                 # `viet_lai` đã tự cộng phần của nó
    doc["usage"] = tong.dict()
    store.save(doc)
    yield "xong", {"bien_tap": bt, "usage": ca, "tong": tong}
