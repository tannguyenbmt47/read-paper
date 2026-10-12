"""Soát chữ tiếng Việt do model sinh ra: sai ngôn ngữ, lẫn pinyin, rơi âm tiết.

## Vì sao cần

Báo cáo thử 11/10 bắt được ba kiểu hỏng mà mọi chốt chặn trước đó đều bỏ lọt,
vì chúng chỉ soát NỘI DUNG (số bịa, trích dẫn sai, câu rỗng) chứ không ai soát
CHỮ:

- **Cả câu trả lời bằng tiếng Pháp** cho câu hỏi tiếng Việt (DeepSeek V4 Flash,
  Hỏi đáp). Luật "viết tiếng Việt" nằm ở đầu một system prompt dài, còn phần
  user toàn bằng chứng tiếng Anh — model trôi ngôn ngữ mà không máy nào biết.
- **Lẫn pinyin**: "như bài báo miêu shù" (描述 miêu tả), "nguyên shǐ" (原始).
- **Rơi âm tiết**: "dữ li ngẫu nhi" (dữ liệu ngẫu nhiên), "trung reward"
  (trung bình reward), "sản chu" (sản sinh). Đều do model, không do bộ dựng.

## Hai tầng

1. **Máy, $0** (`loi_chu`): tỉ lệ từ mang dấu RIÊNG của tiếng Việt (ă â đ ơ ư và
   khối U+1EA0–1EF9) — đo trên dữ liệu thật: câu trả lời tiếng Pháp 0%, văn
   tiếng Việt xen thuật ngữ Anh 41–55%. Cộng chữ cái pinyin (ā ǐ ǔ…), âm tiết
   mang dấu Việt mà sai cấu trúc tiếng Việt ("shù" — tiếng Việt không có phụ âm
   đầu "sh"), chữ Hán, và từ nối tiếng Anh mở đầu câu ("Concretely,").
2. **Người soát chữ** (`soat_chu`, model rẻ): rơi âm tiết thì máy không bắt
   được mà không có từ điển, nên phải đọc. Model chỉ trả cặp *cụm sai → cụm
   đúng*; cụm sai phải là chuỗi con thật, cụm đúng không được mang số mới và
   phải tự qua được tầng 1. Không cho viết lại cả đoạn — đó là việc của biên
   tập viên, và cho tự do là đổi sạn chữ lấy sạn nội dung.
"""

from __future__ import annotations

import re
from collections import Counter

from . import llm

NO_REASONING = {"enabled": False}

# Dấu RIÊNG của tiếng Việt: à/á/è/é/ê/ô… dùng chung với tiếng Pháp nên không tính.
_VI = set("ăâđơưĩũĂÂĐƠƯĨŨ") | {chr(x) for x in range(0x1EA0, 0x1EFA)}
_PINYIN = set("āēīōūǖǎěǐǒǔǚǘǜĀĒĪŌŪǍĚǏǑǓ")
_HAN = re.compile(r"[\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uac00-\ud7af]")
_TU = re.compile(r"[^\W\d_]+")
_CAU = re.compile(r"(?<=[.!?…])\s+|\n+")

_FR = {"le", "la", "les", "des", "du", "une", "est", "sont", "pas", "que", "qui",
       "avec", "pour", "dans", "sur", "aussi", "mais", "plus", "cette", "ce", "il",
       "elle", "nous", "vous", "leur", "même", "très", "entre", "donc", "ou", "où"}
_EN = {"the", "and", "of", "is", "are", "to", "with", "that", "this", "which",
       "for", "from", "by", "it", "be", "as", "an", "on", "was", "were", "can"}
# Từ nối tiếng Anh mở đầu câu: thuật ngữ thì giữ, nhưng khung câu phải là tiếng
# Việt. "Concretely, encoder của VAE…" đã gặp thật trong bài giảng.
_NOI_EN = re.compile(r"(?:^|(?<=[.!?:]\s))(Concretely|However|Moreover|Furthermore|"
                     r"In other words|For example|For instance|Specifically|Therefore|"
                     r"Thus|Hence|Note that|In short|Overall|Finally|Importantly),",
                     re.M)

# Âm tiết tiếng Việt: phụ âm đầu và vần cuối hữu hạn. Từ mang dấu Việt mà sai cấu
# trúc này thì không phải tiếng Việt (pinyin viết bằng dấu Việt: "shù").
_DAU = re.compile(r"^(ngh|ng|nh|ch|gh|gi|kh|ph|qu|th|tr|b|c|d|đ|g|h|k|l|m|n|p|r|s|t|v|x)?",
                  re.I)
_CUOI_OK = re.compile(r"(?:[aăâeêioôơuưy]|ch|ng|nh|c|m|n|p|t)$", re.I)


def _bo_dau(s: str) -> str:
    import unicodedata
    s = s.replace("đ", "d").replace("Đ", "D")
    return "".join(ch for ch in unicodedata.normalize("NFD", s)
                   if unicodedata.category(ch) != "Mn")


def co_viet(tu: str) -> bool:
    return any(ch in _VI for ch in tu)


def ti_le_viet(text: str) -> tuple[float, int]:
    """(tỉ lệ từ mang dấu riêng tiếng Việt, số từ ≥2 chữ cái)."""
    tu = [w for w in _TU.findall(text or "") if len(w) >= 2]
    return sum(co_viet(w) for w in tu) / max(1, len(tu)), len(tu)


def sai_ngon_ngu(text: str) -> str:
    """'fr' / 'en' / 'khac' nếu CẢ VĂN BẢN không phải tiếng Việt, '' nếu ổn.

    Ngưỡng 0,15 nằm xa cả hai phía: tiếng Pháp đo được 0%, tiếng Việt thật
    ≥41%. Văn bản quá ngắn (<30 từ) thì không kết luận."""
    tl, n = ti_le_viet(text)
    if n < 30 or tl >= 0.15:
        return ""
    return _doan_ngu(text)


def _doan_ngu(text: str) -> str:
    tu = [w.lower() for w in _TU.findall(text)]
    fr = sum(w in _FR for w in tu) + text.count("’") + text.count("'")
    en = sum(w in _EN for w in tu)
    if fr > en:
        return "fr"
    return "en" if en else "khac"


def am_tiet_la(tu: str) -> bool:
    """Từ mang dấu Việt nhưng không thể là một âm tiết tiếng Việt.

    Chỉ xét từ VIẾT THƯỜNG: tên riêng Tây Ban Nha trong thư mục tham khảo
    ("Gutiérrez", "Pérez") là 12/12 ca bắt oan trên 1.823 bản dịch thật, còn
    pinyin lọt vào văn xuôi thì luôn viết thường ("miêu shù")."""
    if not tu or not tu[0].islower():
        return False
    if not co_viet(tu) and not any(ch in "àáèéìíòóùúýêôÀÁÈÉÌÍÒÓÙÚÝÊÔ" for ch in tu):
        return False
    tho = _bo_dau(tu).lower()
    if re.match(r"^(sh|zh|f|j|w|z)", tho):
        return True
    van = tho[len(_DAU.match(tho).group(0)):]
    return bool(van) and not _CUOI_OK.search(van)


_TEN_LOI = {"tieng_phap": "câu tiếng Pháp", "tieng_anh": "câu tiếng Anh",
            "ngon_ngu_khac": "câu không phải tiếng Việt", "pinyin": "lẫn pinyin",
            "chu_han": "lẫn chữ Hán", "am_tiet_la": "chữ không phải tiếng Việt",
            "noi_tieng_anh": "từ nối tiếng Anh"}


def ten_loi(k: str) -> str:
    return _TEN_LOI.get(k, k)


def loi_chu(text: str) -> list[dict]:
    """Lỗi chữ máy bắt được, mỗi lỗi kèm đoạn chữ để người soát biết chỗ."""
    out: list[dict] = []
    if not text:
        return out
    for cau in _CAU.split(text):
        cau = cau.strip()
        tu = [w for w in _TU.findall(cau) if len(w) >= 2]
        if len(tu) >= 8 and not any(co_viet(w) for w in tu):
            ngu = _doan_ngu(cau)
            thuong = [w.lower() for w in tu]
            # Đủ từ chức năng mới kết luận: một dòng toàn tên riêng / thuật ngữ
            # ("VAE, MDN-RNN, CMA-ES, Dreamer…") không phải câu tiếng nước ngoài.
            n_cn = sum(w in _FR or w in _EN for w in thuong)
            if n_cn >= 2:
                out.append({"loai": {"fr": "tieng_phap", "en": "tieng_anh"}.get(ngu, "ngon_ngu_khac"),
                            "doan": cau[:160]})
    for m in _NOI_EN.finditer(text):
        out.append({"loai": "noi_tieng_anh", "doan": m.group(0)})
    for w in set(_TU.findall(text)):
        if any(ch in _PINYIN for ch in w):
            out.append({"loai": "pinyin", "doan": w})
        elif am_tiet_la(w):
            out.append({"loai": "am_tiet_la", "doan": w})
    for m in _HAN.finditer(text):
        out.append({"loai": "chu_han", "doan": text[max(0, m.start() - 10):m.end() + 10]})
        break
    return out


# ------------------------------------------------------------ người soát chữ

SOAT_CHU_TASK = """\
Bạn là người soát chữ tiếng Việt. Bản viết dưới đây do máy sinh ra và đôi khi
hỏng CHỮ theo đúng mấy kiểu sau — chỉ sửa đúng các kiểu này:

1. Rơi âm tiết: một từ ghép mất nửa ("dữ li ngẫu nhi" → "dữ liệu ngẫu nhiên",
   "trung reward" → "trung bình reward", "chư giải" → "chưa giải được",
   "huấn luyện trực trên" → "huấn luyện trực tiếp trên").
2. Lẫn pinyin hoặc âm Hán viết sai ("miêu shù" → "miêu tả", "nguyên shǐ" →
   "nguyên thủy", "Giao mian" → "Giao diện").
3. Câu hoặc cụm tiếng Pháp, tiếng Anh, tiếng Trung chen vào giữa văn tiếng Việt
   → dịch sang tiếng Việt, giữ nguyên thuật ngữ chuyên ngành. Từ nối tiếng Anh
   ("Concretely," "However,") → "Cụ thể," "Tuy nhiên,".

KHÔNG sửa:
- Thuật ngữ tiếng Anh chuyên ngành cố ý để nguyên (reward, encoder, VAE, rollout,
  latent, policy…) — đó là quy ước, không phải lỗi.
- Văn phong, cách dùng từ, độ dài, nội dung, con số.

"sai" phải chép NGUYÊN VĂN từ bản viết, đủ dài (3–15 chữ) để chỉ xuất hiện một
lần trong ô đó. Chỉ báo chỗ CHẮC CHẮN hỏng. Không có gì thì trả danh sách rỗng.

Trả về DUY NHẤT một object JSON:
{"sua": [{"o": "L3", "sai": "cụm hỏng, chép nguyên văn", "dung": "cụm đã sửa"}]}
"""

_SO = re.compile(r"\d+(?:[.,]\d+)?")
_GIU = re.compile(r"\[[^\]\s]{2,40}\]|`[^`]+`|[_^]\{[^}]*\}")
ME_KY_TU = 7000


async def soat_chu(o: list[tuple], model: str, sid: str, usage: llm.Usage) -> tuple[list, list]:
    """`o` là danh sách (khoá, chữ). Trả (danh sách (khoá, chữ mới) đã đổi, nhật ký).

    Chia mẻ theo số ký tự để mỗi lượt gọi gọn; mẻ hỏng thì bỏ qua mẻ đó —
    soát chữ là lớp phụ, hỏng thì bản viết vẫn ra như cũ."""
    import asyncio
    o = [(k, t) for k, t in o if isinstance(t, str) and t.strip()]
    if not o:
        return [], []
    me, cur, n = [], [], 0
    for i, (_, t) in enumerate(o):
        if cur and n + len(t) > ME_KY_TU:
            me.append(cur)
            cur, n = [], 0
        cur.append(i)
        n += len(t)
    if cur:
        me.append(cur)

    async def mot(idx: list[int]) -> list[dict]:
        goi_y = []
        dong = []
        for i in idx:
            dong.append(f"[L{i + 1}] {o[i][1]}")
            for l in loi_chu(o[i][1]):
                goi_y.append(f"- L{i + 1}: {ten_loi(l['loai'])} “{l['doan'][:80]}”")
        user = "\n\n".join(dong)
        if goi_y:
            user += "\n\nMáy đã bắt chắc chắn mấy chỗ sau, phải sửa:\n" + "\n".join(goi_y[:30])
        try:
            raw, u = await llm.complete(
                [{"role": "system", "content": SOAT_CHU_TASK}, {"role": "user", "content": user}],
                model=model, session_id=sid, max_tokens=500 + 80 * len(idx) + sum(len(g) for g in goi_y) // 2,
                temperature=0.1, reasoning=NO_REASONING)
            usage.add(u)
            d = llm.extract_json(raw) or {}
        except Exception:                   # noqa: BLE001
            return []
        sua = d.get("sua") if isinstance(d, dict) else []
        return [x for x in sua if isinstance(x, dict)] if isinstance(sua, list) else []

    kq = await asyncio.gather(*(mot(m) for m in me))
    moi = {i: t for i, (_, t) in enumerate(o)}
    log = []
    for x in (y for ds in kq for y in ds):
        m = re.fullmatch(r"L(\d+)", str(x.get("o") or "").strip())
        sai = str(x.get("sai") or "")
        dung = str(x.get("dung") or "").strip()
        if not m or not sai.strip() or not dung or sai == dung:
            continue
        i = int(m.group(1)) - 1
        if i not in moi or moi[i].count(sai) != 1:
            continue
        # Ba chốt: không mang số mới, không dài ra quá đà, và tự qua tầng máy
        # (người soát chữ là model rẻ gốc Trung — chính nó cũng có thể lẫn pinyin).
        if Counter(_SO.findall(dung)) != Counter(_SO.findall(sai)):
            continue
        if len(dung) > 2.5 * len(sai) + 40:
            continue
        # Mã trích dẫn, công thức, mã inline phải nguyên vẹn: người soát chữ mà
        # nuốt mất một `[p3c17]` là câu mất nguồn.
        if sorted(_GIU.findall(dung)) != sorted(_GIU.findall(sai)):
            continue
        if any(l["loai"] in ("pinyin", "chu_han", "am_tiet_la") for l in loi_chu(dung)):
            continue
        moi[i] = moi[i].replace(sai, dung, 1)
        log.append({"vai": "soat_chu", "viec": "sửa chữ hỏng", "truoc": sai[:120], "sau": dung[:120]})
    doi = [(o[i][0], t) for i, t in moi.items() if t != o[i][1]]
    return doi, log


NHAC_NGON_NGU = ("\n\nNHẮC LẠI: viết TOÀN BỘ bằng tiếng Việt, kể cả khi bằng chứng ở trên "
                 "là tiếng Anh. Thuật ngữ chuyên ngành giữ nguyên tiếng Anh, nhưng khung "
                 "câu phải là tiếng Việt. Không viết tiếng Pháp, tiếng Trung hay pinyin.")
