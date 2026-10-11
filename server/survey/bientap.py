"""Ban biên tập — nhiều agent soát và sửa bản viết TRƯỚC khi nó tới người đọc.

## Vì sao cần

Bản tổng hợp và bài giảng trước đây ra mắt người đọc kèm một hộp vàng *"9 chỗ
cần soát lại"* đứng ngay đầu trang. Chốt chặn (`synth.check`, `lecture.check`)
bắt đúng, nhưng nó chỉ **báo**, rồi đẩy việc kiểm sang người đọc — đúng người
đang cần công cụ này vì chưa đủ nền để tự kiểm. Người dùng gọi mấy dòng đó là
"sạn cần người đọc check", và đọc một bài mà phải dò sạn thì bài không hấp dẫn
được, dù phần chữ còn lại tốt cỡ nào.

Soát lại dữ liệu thật thì phần lớn sạn KHÔNG phải bịa:

- Bản tổng hợp kho thử: 9/9 cảnh báo "số bịa" là số **có thật** trong bài, chỉ
  bị gắn nhầm đoạn (model trích đoạn tóm tắt `c1` cho một con số nằm ở bảng
  kết quả). Gắn lại đúng đoạn là hết, không cần gọi model.
- Bài giảng SONIC: 109 cảnh báo, gần hết là mã đoạn của BÀI KHÁC lọt vào danh
  sách nguồn. Bỏ mã lạ là hết.
- Mã bài viết thừa một ký tự (`p50d58cb2d3b`) — khớp tiền tố là ra mã thật.

Phần còn lại mới cần đọc hiểu, và đó là việc của các agent.

## Bốn vai

| Vai | Chạy bằng | Làm gì |
|---|---|---|
| Thư ký | máy, $0 | gắn lại trích dẫn sai đoạn, sửa mã bài, bỏ mã đoạn lạ |
| Người đọc thử | model rẻ | CHỈ đọc bản viết, chỉ ra chỗ người mới không hiểu |
| Người kiểm chứng | model của kho | so từng khẳng định còn ngờ với đoạn gốc |
| Biên tập viên | model của kho | viết lại đúng những ô bị chỉ ra |

**Người đọc thử không được thấy bài gốc.** Đây là phép đo đã sửa được bộ slide
(xem CLAUDE.md, mục subagent chỉ nhìn ảnh slide): người đọc thật không có bài
gốc, nên chỉ một người đọc cũng không có mới thấy được chỗ thuật ngữ rơi từ trên
trời xuống, chỗ lập luận nhảy bước. Cho nó đọc kèm nguồn thì nó tự lấp chỗ hổng
bằng nguồn và báo "ổn".

**Biên tập viên và người kiểm chứng chỉ được dùng số có trong bài.** Bản viết lại
nào đưa vào một con số không có trong nội dung đã bóc thì bị trả về bản cũ —
cùng chốt `content_kept` của pass dọn chữ: cho model sửa tự do là đổi một loại
sạn lấy loại sạn khó thấy hơn.

## Cái còn lại không bày ra trước mặt người đọc

Chỗ nào ban biên tập không gỡ được thì vào `bien_tap.con_lai`, hiện gập trong
dòng "Đã qua ban biên tập", không còn là hộp vàng đầu trang. Người cần kiểm kỹ
vẫn mở ra được; người chỉ cần hiểu bài thì không phải đi dò.
"""

from __future__ import annotations

import re
import time

from .. import depth, llm
from . import db as sdb
from . import prompts as sprompts
from . import verify

NO_REASONING = {"enabled": False}

# Khoá không phải văn xuôi: mã, danh sách mã, dữ liệu đã tính sẵn. Biên tập viên
# mà viết lại một ô `cite` là phá trích dẫn. `title`/`name` là nhãn: chạy thật
# thì biên tập viên viết lại cả tên bản tổng hợp thành một câu dài.
_BO_QUA = {"cite", "cite_them", "title", "name", "source", "papers", "paper", "lineage", "paper_names", "warns",
           "kind", "s2_id", "model", "fp", "refs", "created_at", "cost", "bien_tap",
           "n_refs_total", "number_cite"}
TOI_THIEU = 25          # ô ngắn hơn thì là nhãn, không phải câu để soát

# Trần số việc cho mỗi vai — chặn cả chi phí lẫn chuyện model nhặt sạn vụn vặt
# để "đủ chỉ tiêu".
TRAN_DOC_THU = 12
TRAN_KIEM = 14
TRAN_SUA = 10

_CAU = re.compile(r"(?<=[.!?…])\s+(?=[A-ZÀ-Ỹ0-9\"“(])")
_TIMEISH = re.compile(r"\d{1,3}:\d{2}(?::\d{2})?(?:[.,]\d+)?")


# ------------------------------------------------------------- duyệt cây JSON


def la(d, path: tuple = ()) -> list[tuple[tuple, str]]:
    """Mọi ô văn xuôi trong bản viết, kèm đường dẫn để ghi ngược lại."""
    out: list[tuple[tuple, str]] = []
    if isinstance(d, dict):
        for k, v in d.items():
            if k in _BO_QUA:
                continue
            out += la(v, path + (k,))
    elif isinstance(d, list):
        for i, v in enumerate(d):
            out += la(v, path + (i,))
    elif isinstance(d, str) and len(d.strip()) >= TOI_THIEU:
        out.append((path, d))
    return out


def dat(d, path: tuple, v) -> None:
    for k in path[:-1]:
        d = d[k]
    d[path[-1]] = v


def lay(d, path: tuple):
    for k in path:
        d = d[k]
    return d


def cau(text: str) -> list[str]:
    return [c for c in _CAU.split(text or "") if c.strip()]


def so_trong(text: str) -> list[str]:
    body = verify.tach_don_vi(verify._URLISH.sub(" ", _TIMEISH.sub(" ", text or "")))
    return verify._NUM.findall(body)


# Tên riêng viết hoa (tên tắt, tên hệ thống): ít nhất hai chữ hoa/chữ số.
_TEN = re.compile(r"\b[A-Z][A-Za-z0-9\-]*[A-Z0-9][A-Za-z0-9\-]*\b")


def ten_la(moi: str, nguon: str) -> list[str]:
    """Tên riêng có trong bản viết lại mà nguồn (đoạn đã đưa + bản cũ) không có.

    Cùng họ với chốt số lạ. Chạy thật, biên tập viên đổi "GENMO" thành "GEM" —
    lần này nó ĐÚNG (bài gốc ghi GEM, bản cũ ghi nhầm), nhưng đúng vì nó tình cờ
    có đoạn nguồn trước mặt. Đổi tên mà nguồn không có tên đó là bịa."""
    return sorted({t for t in _TEN.findall(moi or "") if t not in (nguon or "")})


def kho_so(rows) -> set[str]:
    out: set[str] = set()
    for r in rows:
        out |= verify.source_numbers(_TIMEISH.sub(" ", f"{r['text'] or ''} {r.get('vi') or ''}"))
    return out


def so_la(text: str, have: set[str]) -> list[str]:
    """Số trong câu mà nội dung bài không có."""
    return [n for n in so_trong(text) if verify._norm(n) not in have]


# ------------------------------------------------------------- thư ký (máy)


def pid_cua(cid: str, pids) -> str:
    """Mã bài của một mã đoạn. Mã bài là hex nên có thể chứa chữ `c` — không tách
    theo `c` cuối mà so tiền tố với danh sách bài có thật."""
    for p in sorted(pids, key=len, reverse=True):
        if cid.startswith(p) and cid[len(p):len(p) + 1] in ("c", "s"):
            return p
    return ""


def ma_bai_that(x: str, real) -> str:
    """`p50d58cb2d3b` → `p50d58cb2d3`: model viết thừa đuôi. Khớp đúng MỘT bài thì
    nhận, nhiều hơn thì không đoán."""
    if x in real:
        return x
    hit = [p for p in real if x.startswith(p) or p.startswith(x)]
    return hit[0] if len(hit) == 1 and len(x) >= 6 else ""


def tim_doan(survey_id: str, cau_: str, nums: list[str], pids: list[str],
             it_nhat: int = 0) -> tuple[str, int]:
    """Đoạn của đúng bài ấy phủ được NHIỀU con số của câu nhất. Trả (mã, số phủ).

    Đòi phủ ĐỦ thì trượt đúng ca hay gặp nhất: câu tóm tắt ghép số của bảng kết
    quả với một ngưỡng nói ở đoạn khác ("906±21, vượt 838±11, ngưỡng 900") — đo
    trên kho thử, ba số nằm trọn ở một đoạn, số thứ tư ở đoạn khác, và bản đòi
    đủ bỏ qua cả câu. Phủ nhiều hơn đoạn đang trích (`it_nhat`) là đã đỡ hơn;
    phần còn lệch để người kiểm chứng lo.

    Ưu tiên theo BM25 của chính câu, rồi mới quét hết đoạn của bài: bảng kết quả
    hay chứa đủ số mà câu chữ lại khác hẳn câu tóm tắt."""
    want = {verify._norm(n) for n in nums}
    pset = set(pids)
    order = [cid for cid, _ in sdb.bm25(survey_id, cau_, limit=60)]
    rows = sdb.get_chunks(order, survey_id)
    cands = [rows[c] for c in order if c in rows and (not pset or rows[c]["paper_id"] in pset)]
    for p in pids:
        cands += [r for r in sdb.paper_chunks(p, level=0) if r["id"] not in rows]
    best, phu = "", it_nhat
    for r in cands:
        if r.get("level"):
            continue
        if not want:
            return r["id"], 0
        n = len(want & kho_so([r]))
        if n > phu:                     # bằng nhau thì giữ đoạn xếp hạng BM25 cao hơn
            best, phu = r["id"], n
            if n == len(want):
                break
    return best, phu


def phu_so(survey_id: str, cau_: str, pids: list[str]) -> list[str]:
    """Tối đa hai đoạn mà gộp lại phủ được nhiều số của câu nhất — đoạn chứa
    nhiều nhất, rồi đoạn chứa phần còn thiếu. Người kiểm chứng phải được thấy cả
    hai, không thì nó kết luận "bỏ" cho một câu đúng chỉ vì số nằm ở đoạn khác."""
    nums = so_trong(cau_)
    a, _ = tim_doan(survey_id, cau_, nums, pids)
    if not a:
        return []
    ra = sdb.get_chunks([a], survey_id).get(a)
    con = [n for n in nums if ra and verify._norm(n) not in kho_so([ra])]
    b, _ = tim_doan(survey_id, cau_, con, pids) if con else ("", 0)
    return [x for x in (a, b) if x]


def ten_ngan(title: str) -> str:
    """Tên bài để gọi trong câu văn: phần trước dấu hai chấm, bỏ lối viết hoa
    toàn bộ của PDF, cắt ở ranh giới từ. "SONIC: Supersizing…" → "SONIC"."""
    t = (title or "").split(":")[0].strip() or "(bài không tên)"
    if t == t.upper() and re.search(r"[A-Z]{4}", t) and " " in t:
        t = " ".join(w if len(w) <= 4 else w.capitalize() for w in t.lower().split())
        t = t[0].upper() + t[1:]
    return depth.cat_gon(t, 40)


_MA = re.compile(r"\bp[0-9a-f]{8,}[0-9a-z]*\b")
_NHAN_P = re.compile(r"\bP(\d{1,3})\b")


def doi_ma_trong_chu(d, papers: list[dict], nhan_p: bool) -> list[dict]:
    """Mã nội bộ lọt vào VĂN XUÔI → tên bài người đọc hiểu được. $0.

    Chụp màn bản tổng hợp thật thì thấy "P5 là bài kinh điển…", "(P1, P5)",
    "Bài thứ nhất (p50d58cb2d3b)". Nhãn `P1` là thứ ta đặt cho model đọc
    (`prompts.paper_labels`), `p50d…` là mã DB — người đọc không biết cả hai là
    bài nào. Cả chốt cũ lẫn người đọc thử đều bỏ qua: chốt cũ chỉ soát trường
    `papers`, còn người đọc thử tưởng đó là ký hiệu của bài.

    Mã ĐOẠN thì bỏ hẳn (kèm ngoặc bao nó) — chỗ để bấm kiểm là trường `cite`."""
    log: list[dict] = []
    real = {p["id"]: ten_ngan(p.get("title") or "") for p in papers}
    nhan = {v: k for k, v in sprompts.paper_labels(papers).items()} if nhan_p else {}

    def doi(m):
        x = m.group(0)
        pid = pid_cua(x, real) or ma_bai_that(x, set(real))
        if not pid:
            return x
        duoi = x[len(pid):]
        if re.fullmatch(r"[cs]\d+", duoi):
            return "\x00"                       # mã đoạn: đánh dấu để gỡ cả ngoặc
        return real[pid]

    for path, t in la(d):
        moi = _MA.sub(doi, t)
        moi = re.sub(r"\s*[\[(]\s*\x00(?:\s*[,;]\s*\x00)*\s*[\])]", "", moi).replace("\x00", "")
        if nhan:
            moi = _NHAN_P.sub(lambda m: real.get(nhan.get(m.group(0), ""), m.group(0)), moi)
        if moi != t:
            dat(d, path, moi)
            log.append({"vai": "thu_ky", "viec": "đổi mã nội bộ trong câu thành tên bài",
                        "truoc": depth.cat_gon(t, 100), "sau": depth.cat_gon(moi, 100)})
    return log


def thu_ky_tong_hop(survey_id: str, d: dict) -> list[dict]:
    """Sửa máy cho bản tổng hợp: mã bài, mã đoạn, đoạn sai số. $0."""
    log: list[dict] = []
    papers = {p["id"]: p for p in sdb.list_papers(survey_id)}
    real = set(papers)

    def sua_ds(items):
        for it in items:
            if isinstance(it.get("papers"), list):
                moi = []
                for x in it["papers"]:
                    y = ma_bai_that(str(x), real)
                    if y != x:
                        log.append({"vai": "thu_ky", "viec": "sửa mã bài",
                                    "truoc": str(x), "sau": y or "(bỏ)"})
                    if y and y not in moi:
                        moi.append(y)
                it["papers"] = moi
            if it.get("paper"):
                y = ma_bai_that(str(it["paper"]), real)
                if y != it["paper"]:
                    log.append({"vai": "thu_ky", "viec": "sửa mã bài",
                                "truoc": it["paper"], "sau": y or "(bỏ)"})
                    it["paper"] = y

    pb = d.get("problem") or {}
    sides = [sd for t in (d.get("tensions") or []) for sd in (t.get("sides") or [])]
    for items in (d.get("approaches") or [], d.get("novelty") or [], d.get("read_order") or [],
                  pb.get("framings") or [], sides):
        sua_ds(items)

    # (ô có `cite`, câu khẳng định, bài liên quan)
    viec = []
    for a in d.get("approaches") or []:
        for e in a.get("evidence") or []:
            viec.append((e, "claim", a.get("papers") or []))
    for n in d.get("novelty") or []:
        viec.append((n, "new", [n.get("paper")] if n.get("paper") else []))
    for sd in sides:
        viec.append((sd, "claim", sd.get("papers") or []))

    for it, khoa, pids in viec:
        cid = str(it.get("cite") or "")
        claim = f"{it.get(khoa, '')} {it.get('assembled', '')}".strip()
        if not cid or not claim:
            continue
        nums = so_trong(claim)
        pids = [p for p in pids if p in real] or ([pid_cua(cid, real)] if pid_cua(cid, real) else [])
        them = [str(x) for x in (it.get("cite_them") or [])]
        cu = list(sdb.get_chunks([cid, *them], survey_id).values())
        if cu and not so_la(claim, kho_so(cu)):
            continue                                # đoạn chính + đoạn phụ đã đủ
        want = {verify._norm(n) for n in nums}
        dang = len(want & kho_so(cu)) if cu else 0
        # Đoạn chứa nhiều số nhất, rồi đoạn chứa phần còn thiếu: câu tóm tắt hay
        # ghép số của bảng với một nhận xét ở đoạn khác ("49% so với 31%, tức hơn
        # 2 lần") — đòi một đoạn đủ hết thì câu đúng vẫn bị báo sạn.
        moi = phu_so(survey_id, claim, pids)
        phu = len(want & kho_so(list(sdb.get_chunks(moi, survey_id).values()))) if moi else 0
        if moi and phu > dang and [cid, *them] != moi:
            it["cite"] = moi[0]
            if len(moi) > 1:
                it["cite_them"] = moi[1:]
            else:
                it.pop("cite_them", None)
            log.append({"vai": "thu_ky", "viec": "gắn lại trích dẫn đúng đoạn chứa số",
                        "truoc": ", ".join([cid, *them]), "sau": ", ".join(moi),
                        "cau": depth.cat_gon(claim, 120)})
    log += doi_ma_trong_chu(d, list(papers.values()), nhan_p=True)
    return log


def thu_ky_bai_giang(paper_id: str, sections: dict, ids: set[str]) -> list[dict]:
    """Bài giảng: bỏ mã đoạn không thuộc bài này khỏi danh sách nguồn. $0.

    Bỏ chứ không thay: danh sách `source` chỉ là chỗ để bấm kiểm lại, thiếu một
    mã thì mất một lối kiểm, còn mã lạ thì dẫn người đọc tới bài khác."""
    log: list[dict] = []
    for name, data in sections.items():
        if not isinstance(data, dict) or not isinstance(data.get("source"), list):
            continue
        lo = [c for c in data["source"] if not (isinstance(c, str) and c in ids)]
        if lo:
            data["source"] = [c for c in data["source"] if isinstance(c, str) and c in ids]
            log.append({"vai": "thu_ky", "viec": f"bỏ {len(lo)} mã đoạn không thuộc bài",
                        "truoc": ", ".join(map(str, lo[:3])), "sau": name})
    sid = sdb.load_paper(paper_id, full=False)["survey_id"]
    log += doi_ma_trong_chu(sections, sdb.list_papers(sid), nhan_p=False)
    return log


# ------------------------------------------------------------- các agent

DOC_THU_TASK = """\
Bạn là một nghiên cứu sinh năm nhất, thông minh nhưng CHƯA đọc bài báo gốc và
chưa làm trong nhánh này. Bạn chỉ có bản viết dưới đây. Nhiệm vụ: chỉ ra những
chỗ bạn đọc tới đó thì KHÔNG hiểu được, hoặc thấy khẳng định thiếu căn cứ.

Chỉ báo các loại sau:
- "thuật_ngữ": một thuật ngữ, tên viết tắt, ký hiệu được dùng mà chưa nói nó là
  gì ở chỗ đó hoặc trước đó.
- "nhảy_bước": kết luận xuất hiện mà thiếu mắt xích nói vì sao dẫn tới nó.
- "mơ_hồ": câu đọc xong không biết thêm điều gì cụ thể (cải thiện thế nào, bao
  nhiêu, so với cái gì).
- "thiếu_căn_cứ": một khẳng định về kết quả hay so sánh mà không nói dựa vào đâu.

KHÔNG báo lỗi chính tả, văn phong, độ dài. Chỉ báo chỗ thật sự cản việc hiểu.
Tối đa {tran} mục, chỗ cản nhiều nhất trước. Không có gì thì trả danh sách rỗng.

Trả về DUY NHẤT một object JSON:
{{"van_de": [{{"o": "L3", "cau": "trích NGUYÊN VĂN cụm gây vướng (≤20 chữ)",
  "loai": "thuật_ngữ|nhảy_bước|mơ_hồ|thiếu_căn_cứ",
  "can": "người đọc cần được nói thêm điều gì (≤25 chữ)"}}]}}
"""

KIEM_CHUNG_TASK = """\
Bạn là người kiểm chứng của một ban biên tập. Mỗi mục dưới đây là một câu trong
bản viết về bài báo, kèm các đoạn NGUYÊN VĂN của bài (có mã đoạn). Với từng mục,
chỉ dựa vào các đoạn đã cho, kết luận:

- "dung": các đoạn nói đúng điều câu khẳng định, kể cả từng con số. Ghi vào
  "nguon" mọi mã đoạn cần để mỗi con số đều có chỗ dựa (số rải ở hai đoạn thì
  ghi cả hai, đoạn chứa nhiều số nhất trước).
- "sua": câu sai một phần (số lệch, gán nhầm bài, nói quá). Viết lại câu cho
  ĐÚNG với đoạn, giữ giọng văn và độ dài, ghi các mã đoạn vào "nguon". Con số
  nào không có trong đoạn thì bỏ con số đó chứ không đoán.
- "bo": không đoạn nào đỡ được câu này.

Không dùng hiểu biết riêng về các hệ thống này. Không có trong đoạn là không có.

Trả về DUY NHẤT một object JSON:
{"ket_qua": [{"id": "K1", "ket_luan": "dung|sua|bo", "cau_moi": "…", "nguon": ["mã đoạn"]}]}
"""

BIEN_TAP_TASK = """\
Bạn là biên tập viên. Mỗi ô dưới đây là một đoạn trong bản viết giúp người mới
hiểu một bài báo, kèm những chỗ người đọc thử đã vướng và vài đoạn nguyên văn
của bài để dựa vào. Viết lại TỪNG ô sao cho người đọc hết vướng:

- Thuật ngữ chưa giải nghĩa: nói nó là gì và nó làm gì, bằng một câu riêng đặt
  ngay trước chỗ nó được dùng.
- Nhảy bước: thêm đúng mắt xích còn thiếu (vì sao A dẫn tới B).
- Mơ hồ: thay bằng điều cụ thể từ các đoạn đã cho (cơ chế, so với cái gì, bao nhiêu).
- Thiếu căn cứ: nói rõ bài dựa vào thí nghiệm hay quan sát nào.

Luật cứng:
- Giữ nguyên ý và mọi con số đang đúng. Chỉ được thêm con số có trong các đoạn đã cho.
- Không thêm khẳng định mà các đoạn đã cho không nói.
- Độ dài mới không quá 1,6 lần bản cũ. Không mở đầu bằng lời dẫn kiểu "Nói cách khác".
- Không dấu chấm phẩy. Không dùng "tức là", "nghĩa là" để nối phần giải thích.
- Ô nào thấy không cần sửa thì bỏ qua, đừng trả về.

Trả về DUY NHẤT một object JSON: {"sua": {"L3": "đoạn viết lại", "L7": "…"}}
"""


async def _goi(task: str, user: str, model: str, sid: str, max_tokens: int,
               usage: llm.Usage) -> dict:
    """Một lượt gọi trả JSON. Hỏng thì trả {} — ban biên tập là lớp phụ, hỏng
    một vai thì bản viết vẫn ra như cũ chứ không mất cả bài."""
    try:
        raw, u = await llm.complete(
            [{"role": "system", "content": task}, {"role": "user", "content": user}],
            model=model, session_id=sid, max_tokens=max_tokens,
            temperature=0.2, reasoning=NO_REASONING)
        usage.add(u)
        return llm.extract_json(raw) or {}
    except Exception:                       # noqa: BLE001
        return {}


def _nhan(path: tuple, nhan_muc) -> str:
    return nhan_muc(path) if nhan_muc else ".".join(map(str, path))


async def doc_thu(o: list[tuple[tuple, str]], model: str, sid: str, usage, nhan_muc=None):
    ban = "\n\n".join(f"[L{i}] ({_nhan(p, nhan_muc)}) {t}" for i, (p, t) in enumerate(o, 1))
    d = await _goi(DOC_THU_TASK.format(tran=TRAN_DOC_THU), ban, model, sid, 2500, usage)
    out = []
    for v in (d.get("van_de") or [])[:TRAN_DOC_THU]:
        if not isinstance(v, dict):
            continue
        m = re.fullmatch(r"L(\d+)", str(v.get("o") or "").strip())
        if m and 1 <= int(m.group(1)) <= len(o):
            out.append({"i": int(m.group(1)) - 1, "cau": str(v.get("cau") or ""),
                        "loai": str(v.get("loai") or ""), "can": str(v.get("can") or "")})
    return out


async def _doan_lien_quan(survey_id: str, q: str, pids: set[str], k: int = 4) -> list[dict]:
    from . import search                    # muộn: search kéo embed về
    try:
        hits = await search.plain(survey_id, q[:400], limit=k * 4)
    except Exception:                       # noqa: BLE001
        hits = [sdb.get_chunks([c], survey_id).get(c) for c, _ in sdb.bm25(survey_id, q, limit=k * 4)]
        hits = [h for h in hits if h]
    hits = [h for h in hits if not h.get("level") and (not pids or h["paper_id"] in pids)]
    return hits[:k]


def _doan_text(rows: list[dict]) -> str:
    return "\n".join(f"<<{r['id']}>> {depth.cat_gon(r['text'], 900)}" for r in rows)


# ------------------------------------------------------------- điều phối


async def soat(survey_id: str, d: dict, *, kieu: str, paper_id: str = "",
               ids: set[str] | None = None, strong: str, fast: str,
               nhan_muc=None, kiem_so=None, soat_sau=None):
    """Async generator: ("stage", {...}) rồi ("xong", {"nhat_ky", "usage"}).

    `d` bị sửa TẠI CHỖ. `kieu` là "tong_hop" hoặc "bai_giang".

    `kiem_so(path)` nói ô nào phải đối chiếu số với bài. Bài giảng cố ý kể một
    TÌNH HUỐNG VÍ DỤ ở mục cơ chế ("giả sử video dài 10 phút, N = 600") — soát số
    ở đó là kêu oan 32 lần một bài (đã đo, xem `lecture.CLAIM_SECTIONS`), và tệ
    hơn nữa ở đây: người kiểm chứng sẽ XOÁ câu ví dụ vì không đoạn nào đỡ nó.
    `soat_sau(path)` tương tự cho phép chấm độ sâu (câu hỏi tự kiểm vốn ngắn)."""
    kiem_so = kiem_so or (lambda p: True)
    soat_sau = soat_sau or (lambda p: True)
    usage = llm.Usage()
    log: list[dict] = []
    pids = {paper_id} if paper_id else {p["id"] for p in sdb.list_papers(survey_id)}
    rows_all = [r for p in pids for r in sdb.paper_chunks(p, level=0)]
    have = kho_so(rows_all)

    # ---- 1. thư ký: miễn phí
    yield "stage", {"msg": "thư ký soát trích dẫn và mã bài (miễn phí)", "vai": "thu_ky"}
    if kieu == "tong_hop":
        log += thu_ky_tong_hop(survey_id, d)
        khung = d
    else:
        log += thu_ky_bai_giang(paper_id, d, ids or set())
        khung = d

    o = la(khung)
    if not o:
        yield "xong", {"nhat_ky": log, "usage": usage}
        return

    # ---- 2. người đọc thử: chỉ thấy bản viết
    yield "stage", {"msg": "người đọc thử đọc bản viết (không có bài gốc)", "vai": "doc_thu"}
    vuong = await doc_thu(o, fast, survey_id, usage, nhan_muc)
    for v in vuong:
        log.append({"vai": "doc_thu", "viec": v["loai"].replace("_", " "),
                    "cau": depth.cat_gon(v["cau"], 100), "can": v["can"]})

    # ---- 3. người kiểm chứng: câu có số lạ + khẳng định người đọc thấy thiếu căn cứ
    can_kiem: list[dict] = []
    da_co = set()
    for i, (p, t) in enumerate(o):
        if not kiem_so(p):
            continue
        for c in cau(t):
            if so_la(c, have) and (i, c) not in da_co:
                da_co.add((i, c))
                can_kiem.append({"i": i, "cau": c})
    # Ô mang trích dẫn (`cite` nằm cạnh) mà số trong câu vẫn lệch với đoạn trích
    # sau khi thư ký đã gắn lại: số có trong kho nhưng rải ở nhiều đoạn, hoặc
    # câu nói quá điều đoạn nói. Người kiểm chứng được đổi cả câu lẫn trích dẫn.
    for i, (p, t) in enumerate(o):
        cha = lay(khung, p[:-1]) if len(p) > 1 else None
        if not isinstance(cha, dict) or not cha.get("cite") or (i, t) in da_co:
            continue
        ma = [str(cha["cite"]), *map(str, cha.get("cite_them") or [])]
        rows_ = list(sdb.get_chunks(ma, survey_id).values())
        if not rows_ or so_la(t, kho_so(rows_)):
            da_co.add((i, t))
            can_kiem.append({"i": i, "cau": t, "cite": True})
    for v in vuong:
        if v["loai"] == "thiếu_căn_cứ":
            t = o[v["i"]][1]
            c = next((x for x in cau(t) if v["cau"][:20] and v["cau"][:20] in x), "")
            if c and (v["i"], c) not in da_co:
                da_co.add((v["i"], c))
                can_kiem.append({"i": v["i"], "cau": c})
    can_kiem = can_kiem[:TRAN_KIEM]

    if can_kiem:
        yield "stage", {"msg": f"người kiểm chứng đối chiếu {len(can_kiem)} câu với bài gốc",
                        "vai": "kiem_chung"}
        khoi = []
        for j, k in enumerate(can_kiem, 1):
            doan = await _doan_lien_quan(survey_id, k["cau"], pids)
            # Đoạn phủ nhiều số nhất của câu luôn có mặt — tìm theo chữ hay bỏ
            # sót bảng kết quả, mà bảng mới là chỗ chứa số.
            if so_trong(k["cau"]):
                for tot in phu_so(survey_id, k["cau"], list(pids)):
                    if tot not in {r["id"] for r in doan}:
                        r = sdb.get_chunks([tot], survey_id).get(tot)
                        if r:
                            doan = [r] + doan[:4]
            k["doan"] = {r["id"] for r in doan}
            k["so"] = kho_so(doan)
            k["chu"] = " ".join(f"{r['text']} {r.get('vi') or ''}" for r in doan)
            khoi.append(f"### K{j}\nCâu: {k['cau']}\nĐoạn của bài:\n{_doan_text(doan)}")
        kq = await _goi(KIEM_CHUNG_TASK, "\n\n".join(khoi), strong, survey_id,
                        300 + 220 * len(can_kiem), usage)
        for r in kq.get("ket_qua") or []:
            if not isinstance(r, dict):
                continue
            m = re.fullmatch(r"K(\d+)", str(r.get("id") or "").strip())
            if not m or not 1 <= int(m.group(1)) <= len(can_kiem):
                continue
            k = can_kiem[int(m.group(1)) - 1]
            p, t = o[k["i"]]
            kl = str(r.get("ket_luan") or "")
            ng = r.get("nguon") or []
            ng = [ng] if isinstance(ng, str) else ng
            # Mã đoạn khai ra phải là một trong các đoạn đã đưa — mã khác là bịa.
            nguon = [str(x).strip() for x in ng if str(x).strip() in k["doan"]][:3]
            cha = lay(khung, p[:-1]) if k.get("cite") else None
            if kl in ("dung", "sua") and nguon and isinstance(cha, dict):
                cu = [cha.get("cite"), *(cha.get("cite_them") or [])]
                if cu != nguon:
                    log.append({"vai": "kiem_chung", "viec": "đổi trích dẫn sang đoạn nói đúng điều này",
                                "truoc": ", ".join(map(str, filter(None, cu))),
                                "sau": ", ".join(nguon), "cau": depth.cat_gon(t, 120)})
                    cha["cite"] = nguon[0]
                    if len(nguon) > 1:
                        cha["cite_them"] = nguon[1:]
                    else:
                        cha.pop("cite_them", None)
            if kl == "sua":
                moi = str(r.get("cau_moi") or "").strip()
                # Câu sửa mang số không có trong CÁC ĐOẠN ĐÃ ĐƯA thì không nhận —
                # đổi sạn này lấy sạn khác. Đối chiếu với cả kho thì quá lỏng.
                if moi and not so_la(moi, k["so"]) and k["cau"] in t \
                        and not ten_la(moi, k["cau"] + " " + k["chu"]):
                    t = t.replace(k["cau"], moi, 1)
                    log.append({"vai": "kiem_chung", "viec": "sửa câu cho khớp bài gốc",
                                "truoc": depth.cat_gon(k["cau"], 140),
                                "sau": depth.cat_gon(moi, 140)})
            elif kl == "bo" and k["cau"] in t and len(cau(t)) > 1:
                t = " ".join(x for x in cau(t) if x != k["cau"])
                log.append({"vai": "kiem_chung", "viec": "bỏ câu không đoạn nào đỡ được",
                            "truoc": depth.cat_gon(k["cau"], 140), "sau": ""})
            if t != o[k["i"]][1]:
                dat(khung, p, t)
                o[k["i"]] = (p, t)

    # ---- 4. biên tập viên: ô có chỗ vướng hoặc bị chấm nông
    viec: dict[int, list[str]] = {}
    for v in vuong:
        if v["loai"] != "thiếu_căn_cứ":
            viec.setdefault(v["i"], []).append(f"{v['loai'].replace('_', ' ')} ở “{v['cau']}”: {v['can']}")
    for i, (p, t) in enumerate(o):
        if not soat_sau(p):
            continue
        for w in depth.check_text(t):
            viec.setdefault(i, []).append(f"{tenloai(w['kind'])}: “{depth.cat_gon(w.get('text') or t, 90)}”")
    chon = sorted(viec, key=lambda i: -len(viec[i]))[:TRAN_SUA]

    if chon:
        yield "stage", {"msg": f"biên tập viên viết lại {len(chon)} đoạn cho dễ hiểu",
                        "vai": "bien_tap"}
        doan_cua: dict[int, list[dict]] = {}
        for i in chon:
            doan_cua[i] = await _doan_lien_quan(survey_id, o[i][1], pids, k=3)
        ly_do: dict[int, str] = {}
        for vong in range(2):
            # Vòng hai chỉ gửi lại những ô bị trả về, KÈM lý do. Chạy thật vòng
            # đầu bị trả 6/9 lần (đánh rơi số, thêm số), nghĩa là đúng những chỗ
            # người đọc thử vướng nhất vẫn nằm nguyên.
            lam = chon if vong == 0 else [i for i in chon if i in ly_do]
            if not lam:
                break
            khoi = []
            for i in lam:
                p, t = o[i]
                giu = sorted(set(so_trong(t)))
                khoi.append(
                    f"### L{i + 1} ({_nhan(p, nhan_muc)})\n{t}\n"
                    + (f"Phải giữ đủ các số: {', '.join(giu)}\n" if giu else "")
                    + (f"Lần trước bị trả về vì: {ly_do[i]}\n" if i in ly_do else "")
                    + "Chỗ vướng:\n" + "\n".join(f"- {x}" for x in viec[i])
                    + f"\nĐoạn của bài để dựa vào:\n{_doan_text(doan_cua[i])}")
            ly_do = {}
            kq = await _goi(BIEN_TAP_TASK, "\n\n".join(khoi), strong, survey_id,
                            400 + 700 * len(lam), usage)
            for key, moi in (kq.get("sua") or {}).items():
                m = re.fullmatch(r"L(\d+)", str(key).strip())
                if not m or (int(m.group(1)) - 1) not in lam or not isinstance(moi, str):
                    continue
                i = int(m.group(1)) - 1
                p, t = o[i]
                moi = moi.strip()
                if not moi or moi == t:
                    continue
                # Ba chốt, cùng tinh thần `content_kept`. Số MỚI chỉ được lấy từ
                # chính các đoạn đã đưa — đối chiếu với cả kho thì quá lỏng: chạy
                # thật đã lọt "cỡ mẫu nhỏ (10-20…)" vì số nhỏ kho nào cũng có.
                duoc = kho_so(doan_cua[i]) | {verify._norm(n) for n in so_trong(t)}
                la_ = so_la(moi, duoc)
                ten = ten_la(moi, t + " " + " ".join(f"{r['text']} {r.get('vi') or ''}"
                                                    for r in doan_cua[i]))
                mat = sorted({verify._norm(n) for n in so_trong(t)}
                             - {verify._norm(n) for n in so_trong(moi)})
                if la_:
                    ly_do[i] = f"thêm số không có trong các đoạn đã cho ({', '.join(la_[:4])})"
                elif ten:
                    ly_do[i] = f"đưa vào tên không có trong các đoạn đã cho ({', '.join(ten[:4])})"
                elif len(moi) > 1.8 * len(t) + 80:
                    ly_do[i] = f"dài {len(moi)} ký tự, quá 1,6 lần bản cũ ({len(t)})"
                elif mat:
                    ly_do[i] = f"đánh rơi số {', '.join(mat[:4])}"
                if i in ly_do:
                    if vong == 1:
                        log.append({"vai": "bien_tap", "viec": f"bản viết lại bị trả về: {ly_do[i]}",
                                    "truoc": depth.cat_gon(t, 80), "sau": ""})
                    continue
                dat(khung, p, moi)
                o[i] = (p, moi)
                log.append({"vai": "bien_tap", "viec": "viết lại cho dễ hiểu",
                            "o": _nhan(p, nhan_muc), "truoc": depth.cat_gon(t, 160),
                            "sau": depth.cat_gon(moi, 160)})

    # ---- 5. thư ký soát lần cuối: biên tập viên được thêm số lấy từ đoạn nguồn
    # của nó, mà ô ấy có thể đang trích một đoạn khác. Chạy thật đã ra đúng vậy:
    # câu bằng chứng thêm "49%, 31%, 33%" mà vẫn trích đoạn tóm tắt. Miễn phí.
    if kieu == "tong_hop":
        log += thu_ky_tong_hop(survey_id, d)

    yield "xong", {"nhat_ky": log, "usage": usage}


_TEN_LOAI = {"thiếu_cơ_chế": "nói kết quả mà thiếu cơ chế", "câu_độn": "cụm rỗng",
             "nói_chung_chung": "nói chung chung, không nói bằng cách nào",
             "vòng_tròn": "giải thích vòng tròn"}


def tenloai(k: str) -> str:
    return _TEN_LOAI.get(k, (k or "").replace("_", " "))


def tom_tat(log: list[dict], con_lai: list[dict], usage: llm.Usage, truoc: int) -> dict:
    """Biên bản lưu kèm bản viết, để giao diện nói được ban biên tập đã làm gì."""
    dem = {}
    for x in log:
        if x["vai"] in ("thu_ky", "kiem_chung", "bien_tap") and "trả về" not in x["viec"]:
            dem[x["vai"]] = dem.get(x["vai"], 0) + 1
    return {"sua": sum(dem.values()), "theo_vai": dem, "nhat_ky": log[:120],
            "con_lai": con_lai, "sac_truoc": truoc, "chi_phi": round(usage.cost, 5),
            "luc": time.time()}


def nghiem_trong(warns: list[dict]) -> list[dict]:
    """Sạn còn lại sau ban biên tập. Cảnh báo độ sâu đã qua tay biên tập viên
    thì không bày lại cho người đọc — đã sửa được thì hết, không sửa được thì
    đó là giới hạn của chính bài, không phải việc người đọc phải dò."""
    nang = {"số_bịa", "số_không_có_trong_bài", "cite_lạ", "bài_lạ",
            "mã_đoạn_không_có", "thiếu_mục", "bỏ_sót_bài", "chưa_gom_được"}
    return [w for w in warns if w.get("kind") in nang]

