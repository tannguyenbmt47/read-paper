/* Loupe — giao diện. */

const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];

/** Lấy câu lỗi từ một response hỏng, KHÔNG bao giờ ném thêm lỗi mới.

    `await apiErr(r)` là cái bẫy: server trả 500 thì FastAPI gửi về một
    trang HTML, `json()` ném `Unexpected token 'I', "Internal S"… is not valid
    JSON`, và chính câu tiếng Anh đó bị in ra màn hình thay cho lỗi thật. Người
    dùng không có cách nào hiểu mình phải làm gì. */
async function apiErr(r, mac = "") {
  try {
    const ct = r.headers.get("content-type") || "";
    if (ct.includes("json")) {
      const d = await r.json();
      const m = d?.detail ?? d?.error ?? d?.message;
      if (typeof m === "string" && m.trim()) return m;
      if (Array.isArray(m) && m.length) return m.map((x) => x?.msg || x).join("; ");
    } else {
      const t = (await r.text()).trim();
      // Trang lỗi HTML thì không có gì đáng đọc — đừng dội nó vào mặt người dùng.
      if (t && !/^\s*</.test(t) && t.length < 300) return t;
    }
  } catch { /* đọc được gì thì đọc, không được thì rơi về câu mặc định */ }
  return mac || `Máy chủ báo lỗi ${r.status}${r.statusText ? " " + r.statusText : ""}.`;
}
/* ============================================ hộp thoại của app =====

   `confirm()` / `prompt()` / `alert()` của hệ điều hành KHOÁ cả tab: không cuộn
   được, không bấm được gì khác, và trên Chromium còn hiện kèm ô "chặn trang này
   hiện thêm hộp thoại" — tick vào là mọi câu hỏi sau đó bị bỏ qua **im lặng**,
   tức `confirm()` trả `false` và người dùng tưởng nút không ăn.

   Mà phần lớn câu hỏi trong app này là câu hỏi TỐN TIỀN ("dựng lại dàn ý?",
   "dịch lại khối này?") nên phải nói rõ mất gì và giá bao nhiêu — hộp thoại
   native hiện chữ một cỡ, không định dạng, nên đoạn giải thích đó trôi hết.

   Ba hàm trả Promise, dùng chung đúng một hộp thoại trong DOM. */

let dlgDong = null;   // hàm đóng của lượt đang mở, để lượt sau không chồng lên

function dlgMo({ title, body = "", ok = "Đồng ý", cancel = "Huỷ", nhap = null, hong = false, nhieuDong = false }) {
  dlgDong?.(null);
  const veil = $("#dlgVeil"), btnOk = $("#dlgOk"), btnNo = $("#dlgCancel");
  // Ô một dòng và ô nhiều dòng là hai phần tử: `<input>` không nhận Enter làm
  // ngắt dòng, nên danh sách cột của bảng đối chiếu buộc phải là `<textarea>`.
  const inp = nhieuDong ? $("#dlgArea") : $("#dlgInput");
  (nhieuDong ? $("#dlgInput") : $("#dlgArea")).classList.add("hidden");
  $("#dlgTitle").textContent = title;
  $("#dlgBody").textContent = body;
  $("#dlgBody").classList.toggle("hidden", !body);
  btnOk.textContent = ok;
  btnOk.classList.toggle("btn-danger", hong);
  btnNo.classList.toggle("hidden", cancel === null);
  if (cancel) btnNo.textContent = cancel;
  inp.classList.toggle("hidden", nhap === null);
  if (nhap !== null) { inp.value = nhap; inp.placeholder = ""; }
  veil.classList.remove("hidden");

  const truoc = document.activeElement;
  (nhap !== null ? inp : btnOk).focus();
  if (nhap !== null) inp.select();

  return new Promise((xong) => {
    const dong = (kq) => {
      dlgDong = null;
      veil.classList.add("hidden");
      document.removeEventListener("keydown", phim, true);
      // Trả tiêu điểm về chỗ cũ, nếu nó còn trong trang.
      if (truoc?.isConnected) truoc.focus();
      xong(kq);
    };
    const nhan = () => dong(nhap !== null ? inp.value : true);
    const phim = (e) => {
      if (e.key === "Escape") { e.preventDefault(); dong(null); }
      // Enter trong ô nhập là "đồng ý" — hộp thoại chỉ có một dòng nên không
      // có form nào để submit.
      /* Trong ô nhiều dòng Enter là ngắt dòng THẬT; Ctrl/⌘+Enter mới là đồng ý.
         Điều kiện bám vào `nhieuDong`, KHÔNG bám vào `document.activeElement`:
         tiêu điểm có thể chưa về ô nhập (đã đo đúng vậy khi hộp thoại còn bị
         một `.hidden` của màn ngoài ăn theo), và lúc ấy Enter đóng mất hộp
         thoại ngay giữa lúc người dùng đang gõ dòng thứ hai. */
      else if (e.key === "Enter" && nhieuDong) {
        if (e.ctrlKey || e.metaKey) { e.preventDefault(); nhan(); }
      }
      // Đang đứng trên một lối của `chonMot` thì để Enter bấm đúng lối ấy.
      else if (e.key === "Enter" && document.activeElement?.classList.contains("dlg-lua")) {
        /* để mặc định */
      }
      else if (e.key === "Enter" && (nhap !== null || document.activeElement !== btnNo)) {
        e.preventDefault(); nhan();
      }
    };
    dlgDong = dong;
    btnOk.onclick = nhan;
    btnNo.onclick = () => dong(null);
    // Bấm ra ngoài là huỷ, nhưng chỉ khi bấm đúng vào lớp phủ: kéo chọn chữ
    // trong hộp rồi thả tay ra ngoài thì không được tính là huỷ.
    veil.onmousedown = (e) => { if (e.target === veil) dong(null); };
    document.addEventListener("keydown", phim, true);
  });
}

/** Hỏi đồng ý / huỷ. Trả `true` khi người dùng đồng ý. */
async function xacNhan(title, body = "", opts = {}) {
  return (await dlgMo({ title, body, ok: "Đồng ý", ...opts })) === true;
}

/** Hỏi một dòng chữ. Trả chuỗi, hoặc `null` khi huỷ. */
async function nhapChu(title, body = "", value = "", nhieuDong = false) {
  const kq = await dlgMo({ title, body, nhap: value ?? "", ok: "Lưu", nhieuDong });
  return kq === null ? null : String(kq);
}

/** Hỏi chọn MỘT trong nhiều lối, mỗi lối một nút kèm dòng giải thích. Trả
    `key` của lối được chọn, hoặc `null` khi huỷ (Esc, bấm ra ngoài, nút Huỷ).

    Dùng cho câu hỏi có hơn hai câu trả lời đúng — "bài này đã có: ghi đè, lưu
    thành phiên bản mới, hay để riêng?". Ép nó vào Đồng ý/Huỷ là bắt người dùng
    trả lời một câu hỏi khác câu họ đang có trong đầu.

    `nut`: [{ key, nhan, giai, kieu }] — `kieu` "chinh" là lối nên chọn, "hong" là
    lối có mất mát. Lối an toàn để ở nút Huỷ, vì Esc rơi về đó. */
function chonMot(title, body, nut, huy = "Huỷ") {
  const act = $(".dlg-act");
  const cu = [...act.children];
  const hop = document.createElement("div");
  hop.className = "dlg-chon";
  hop.innerHTML = nut.map((n, i) => `
    <button type="button" class="dlg-lua${n.kieu === "chinh" ? " chinh" : ""}${n.kieu === "hong" ? " hong" : ""}"
        data-i="${i}"><b>${esc(n.nhan)}</b>${n.giai ? `<span>${esc(n.giai)}</span>` : ""}</button>`).join("");
  return new Promise((xong) => {
    let kq = null;
    hop.onclick = (e) => {
      const b = e.target.closest("[data-i]");
      if (!b) return;
      kq = nut[+b.dataset.i].key;
      $("#dlgCancel").click();             // đóng qua đường huỷ, rồi trả `kq`
    };
    act.before(hop);
    cu.forEach((x) => x.classList.add("hidden"));
    $("#dlgCancel").classList.remove("hidden");
    dlgMo({ title, body, cancel: huy, ok: "" }).then(() => {
      hop.remove();
      cu.forEach((x) => x.classList.remove("hidden"));
      xong(kq);
    });
    $("#dlgOk").classList.add("hidden");     // chỉ còn các lối + nút Huỷ
    $(".dlg-lua.chinh", hop)?.focus();
  });
}

/** Báo một tin, chỉ có nút đóng. */
async function baoTin(title, body = "") {
  await dlgMo({ title, body, ok: "Đã hiểu", cancel: null });
}

/* Thông báo thoáng qua, có thể kèm một nút hoàn lại. Dùng cho việc xoá được
   mà KHÔNG tốn tiền để dựng lại — xoá một mục dàn ý, một slide. Việc xoá tốn
   tiền (xoá bài, bóc lại) thì phải hỏi trước bằng `xacNhan`, vì "hoàn lại" ở
   đó là lời hứa không giữ được. */
function baoNhanh(msg, hoanLai = null) {
  let box = $("#toast");
  if (!box) {
    box = document.createElement("div");
    box.id = "toast";
    box.className = "toast";
    box.setAttribute("role", "status");
    box.setAttribute("aria-live", "polite");
    document.body.append(box);
  }
  clearTimeout(box._hen);
  box.textContent = "";
  box.append(Object.assign(document.createElement("span"), { textContent: msg }));
  if (hoanLai) {
    const b = Object.assign(document.createElement("button"), {
      className: "toast-undo", textContent: "Hoàn lại",
    });
    b.onclick = () => { box.classList.remove("is-on"); hoanLai(); };
    box.append(b);
  }
  box.classList.add("is-on");
  box._hen = setTimeout(() => box.classList.remove("is-on"), hoanLai ? 8000 : 3500);
}

/** Một icon nét vẽ tay trong bộ symbol của `index.html` (xem `.ico`). */
const ico = (ten) => `<svg class="ico" aria-hidden="true"><use href="#i-${ten}"/></svg>`;

const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

/* Parser ghi chỉ số trên/dưới thành `^{N}` và `_{i}` — dạng đó để cho model đọc,
   không phải để người đọc nhìn. Dựng lại thành chỉ số thật khi hiển thị.
   Escape trước rồi mới chèn thẻ, nên nội dung bài không chèn được HTML.
   Ngoặc nhọn còn lại là ngoặc thật của bài (ký hiệu tập hợp), giữ nguyên.

   Dựng cả `**đậm**`, và CHỈ dạng hai dấu sao. Bài báo dùng chữ đậm làm tiêu đề
   chạy đầu đoạn ("**Dataset.** Chúng tôi huấn luyện…") nên để nguyên hai dấu sao
   là vừa mất một tầng cấu trúc vừa lòi ký tự rác ra giữa câu.

   Dấu `*` ĐƠN thì để nguyên. Quét dữ liệu thật: cả hai chỗ dùng nó đều không
   phải chữ nghiêng — một là ký hiệu chú thích bảng, một là phép nhân
   `2 * 10^{−4}`. Dựng chúng thành <em> là hỏng cả hai.

   Luật ở đây phải khớp từng cái với `rich()` bên `server/main.py`, nếu không
   bản xuất ra khác bản đang đọc. */
const sci = (s) => refs(esc(s)
  // LaTeX nội dòng TRƯỚC: `\(a^{(g)}\)` phải đi qua `mathTeX` cả cục, nếu để
  // luật `^{…}` chung xử lý thì mấy macro `\in`, `\tilde` còn nguyên dấu chéo
  // giữa câu tiếng Việt. Đã thấy đúng vậy: `\(Suf(a) \in \{0, 1\}\)`.
  .replace(/\\\((.+?)\\\)/gs, (_, m) => `<span class="imath">${mathTeX(m)}</span>`)
  .replace(/\^\{([^{}]*)\}/g, "<sup>$1</sup>")
  .replace(/_\{([^{}]*)\}/g, "<sub>$1</sub>")
  .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>"));

/* "Figure 2" trong đoạn văn nhưng hình lại nằm cách đó mấy trang — biến mọi
   tham chiếu thành chỗ bấm được để xem ngay hình đó, khỏi mất chỗ đang đọc. */
const REF_RE = /\b(Figures?|Figs?\.?|Tables?|Algorithms?|Hình|Bảng|Thuật toán)\s*(\d{1,2})\b/gi;

/* `[3]`, `[3, 7]` trong thân bài → chỗ rê chuột ra thấy mục tham khảo tương ứng,
   bấm thì mở danh sách và nhảy tới mục đó. Chỉ gắn khi bài CÓ mục `[n]` khớp:
   `[1]` trong một đoạn nói về công thức thì không phải trích dẫn. */
const CITE_RE = /\[(\d{1,3}(?:\s*[,–-]\s*\d{1,3})*)\]/g;

function refs(html) {
  const cite = state.citeIndex;
  if (cite && Object.keys(cite).length) {
    html = html.replace(CITE_RE, (whole, inner) => {
      const so = inner.split(/\s*[,–-]\s*/);
      if (!so.every((x) => cite[x])) return whole;
      const tip = so.map((x) => `[${x}] ${cite[x].text}`).join("\n");
      return `<a class="cite" data-cite="${esc(so[0])}" title="${esc(tip)}">${whole}</a>`;
    });
  }
  const map = state.figIndex;
  if (!map) return html;
  return html.replace(REF_RE, (whole, word, num) => {
    const w = word.toLowerCase();
    const kind = /^(fig|hình)/.test(w) ? "fig"
      : /^(table|bảng)/.test(w) ? "table" : "algorithm";
    const id = map[kind + num];
    return id ? `<a class="figref" data-figref="${esc(id)}">${whole}</a>` : whole;
  });
}

/** Bảng tra "fig2" -> mã khối caption, dựng từ chính caption bóc được. */
/** Chỉ mục tài liệu tham khảo: `{ "1": { id, text }, … }` từ các khối `reference`
    mở đầu bằng `[n]` hoặc `n.`. Khối không có số thì không vào chỉ mục (không có
    gì để `[n]` trỏ tới) nhưng vẫn hiện trong danh sách. */
function buildCiteIndex() {
  const map = {};
  for (const b of state.doc.blocks) {
    if (b.type !== "reference") continue;
    const m = b.text.match(/^\s*\[?(\d{1,3})[\].]\s*(.+)/s);
    if (m) map[m[1]] ??= { id: b.id, text: m[2].replace(/\s+/g, " ").trim() };
  }
  state.citeIndex = map;
}

function buildFigIndex() {
  const map = {};
  for (const b of state.doc.blocks) {
    if (b.type !== "caption") continue;
    const m = b.text.match(/^\s*(figure|fig\.?|table|algorithm|listing)\s*(\d{1,2})/i);
    if (!m) continue;
    const w = m[1].toLowerCase();
    const kind = w.startsWith("fig") ? "fig" : w.startsWith("table") ? "table" : "algorithm";
    map[kind + m[2]] ??= b.id;
  }
  state.figIndex = map;
}

function openFigPeek(blockId) {
  const b = state.doc.blocks.find((x) => x.id === blockId);
  if (!b) return;
  $("#figPeekTitle").textContent = b.text.split(/[:.]/)[0].slice(0, 40);
  $("#figPeekCap").innerHTML = sci(state.doc.translations[b.id] || b.text);
  const img = $("#figPeekImg");
  const body = $(".figpeek-body");
  if (b.figure) {
    img.src = `/api/doc/${state.doc.id}/img/${b.figure}.png`;
    body.classList.remove("hidden");
  } else {
    img.removeAttribute("src");
    body.classList.add("hidden");
  }
  $("#figPeekGo").onclick = () => { closeFigPeek(); jumpToBlock(blockId); };
  $("#figPeek").classList.remove("hidden");
  figReset();          // hình mới thì về vừa khung, đừng giữ mức phóng của hình trước
}

function closeFigPeek() { $("#figPeek").classList.add("hidden"); }

/* ---- kéo–thả và phóng to trong ô xem trước hình ----

   Hình cắt từ PDF dày đặc chữ nhỏ (nhãn trục, chú giải, số trong bảng) mà ô xem
   trước chỉ rộng chừng 560px, nên thu vừa khung là không đọc nổi — mà đọc được
   con số trên biểu đồ mới là lý do người ta bấm vào "Figure 3".

   Đặt vị trí bằng `transform` chứ không bằng thanh cuộn: phóng phải lấy CON TRỎ
   làm tâm (chỗ đang nhìn phải đứng yên dưới chuột), việc đó cần đặt được toạ độ
   chính xác, thanh cuộn thì không. */
const FIG_MIN = 1, FIG_MAX = 8;
const figv = { s: 1, x: 0, y: 0 };

function figApply() {
  const img = $("#figPeekImg");
  const box = $(".figpeek-body").getBoundingClientRect();
  const w = img.offsetWidth * figv.s;
  const h = img.offsetHeight * figv.s;

  // Không cho kéo hình mất hút khỏi khung: luôn còn ít nhất một phần tư hình
  // nằm trong tầm nhìn ở mỗi chiều. Thiếu chốt này thì một cú kéo mạnh là hình
  // biến mất và người dùng tưởng hỏng.
  const keep = 0.25;
  const clamp = (v, span, boxSpan) =>
    Math.min(boxSpan - span * keep, Math.max(-(span - boxSpan * keep), v));
  figv.x = w ? clamp(figv.x, w, box.width) : 0;
  figv.y = h ? clamp(figv.y, h, box.height) : 0;

  img.style.transform = `translate(${figv.x}px, ${figv.y}px) scale(${figv.s})`;
  $("#figPeekZoom").textContent = Math.round(figv.s * 100) + "%";
}

function figReset() { figv.s = 1; figv.x = 0; figv.y = 0; figApply(); }

/** Phóng quanh một điểm trong khung, để chỗ đang nhìn đứng yên dưới con trỏ. */
function figZoom(mul, cx, cy) {
  const box = $(".figpeek-body").getBoundingClientRect();
  const px = cx == null ? box.width / 2 : cx - box.left;
  const py = cy == null ? box.height / 2 : cy - box.top;
  const s2 = Math.min(FIG_MAX, Math.max(FIG_MIN, figv.s * mul));
  if (s2 === figv.s) return;
  // giữ điểm (px,py) bất động: x' = px - (px - x) * s2/s
  figv.x = px - (px - figv.x) * (s2 / figv.s);
  figv.y = py - (py - figv.y) * (s2 / figv.s);
  figv.s = s2;
  figApply();
}

function wireFigPeek() {
  const body = $(".figpeek-body");
  if (!body) return;

  body.addEventListener("wheel", (e) => {
    e.preventDefault();
    figZoom(e.deltaY < 0 ? 1.15 : 1 / 1.15, e.clientX, e.clientY);
  }, { passive: false });

  let drag = null;
  body.addEventListener("pointerdown", (e) => {
    if (e.button !== 0) return;
    drag = { x: e.clientX - figv.x, y: e.clientY - figv.y, id: e.pointerId };
    body.setPointerCapture(e.pointerId);
    body.classList.add("is-drag");
  });
  body.addEventListener("pointermove", (e) => {
    if (!drag || e.pointerId !== drag.id) return;
    figv.x = e.clientX - drag.x;
    figv.y = e.clientY - drag.y;
    figApply();
  });
  const stop = (e) => {
    if (!drag) return;
    body.releasePointerCapture(drag.id);
    drag = null;
    body.classList.remove("is-drag");
  };
  body.addEventListener("pointerup", stop);
  body.addEventListener("pointercancel", stop);
  // Bấm đúp: về vừa khung nếu đang phóng, phóng gấp ba nếu đang vừa khung.
  body.addEventListener("dblclick", (e) => {
    if (figv.s > 1.02) figReset();
    else figZoom(3, e.clientX, e.clientY);
  });

  // Mở rộng cả khung. Bảng nhiều số thì phóng ảnh trong khung nhỏ chỉ đọc được
  // từng ô mà mất cái nhìn toàn bảng — so hàng với cột mới là lý do mở nó ra.
  $("#figPeekBig").onclick = () => {
    const box = $("#figPeek");
    const big = box.classList.toggle("is-big");
    box.style.width = box.style.height = "";   // bỏ cỡ đã kéo tay, để CSS quyết
    $("#figPeekBig").textContent = big ? "⤡" : "⤢";
    $("#figPeekBig").title = big ? "Thu khung về cỡ thường" : "Mở rộng khung";
    figReset();                                 // khung đổi cỡ thì canh lại ảnh
  };

  $("#figPeekIn").onclick = () => figZoom(1.4);
  $("#figPeekOut").onclick = () => figZoom(1 / 1.4);
  $("#figPeekZoom").onclick = figReset;
}

const state = {
  doc: null, chunks: 0, translating: false, stopping: false, bo: null,
  history: [], session: 0, models: [], slideSel: null,
};

/** Giá tiền theo đơn vị người đọc cảm nhận được, không phải 6 chữ số 0. */
function money(v) {
  if (v == null) return "—";
  if (v === 0) return "$0";
  if (v < 0.01) return "$" + v.toFixed(4);
  return "$" + v.toFixed(3);
}

/** Báo chi phí của một lượt gọi vừa xong, kèm tổng đã tiêu cho bài này. */
/* Đồng hồ cho những lượt gọi dài. Một dòng trạng thái đứng yên không nói được
   "đang chạy" khác "đã treo" ở chỗ nào — và người dùng đã ngồi chờ 5 phút trước
   một dòng chữ bất động rồi. */
let _dongHo = null;

function dongHo(nhan) {
  dongHoTat();
  const t0 = Date.now();
  const ve = () => {
    const s = Math.round((Date.now() - t0) / 1000);
    status(`${nhan}… ${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`
      + (s > 45 ? " · bấm ■ Dừng nếu muốn huỷ" : ""));
  };
  ve();
  _dongHo = setInterval(ve, 1000);
}

function dongHoTat() {
  if (_dongHo) { clearInterval(_dongHo); _dongHo = null; }
}

function reportCost(label, run, total) {
  const c = run?.cost;
  if (total) { state.doc.usage = total; renderUsage(); }
  if (c != null) state.session += c;
  const cached = run?.cached_tokens
    ? ` · ${(run.cached_tokens / 1000).toFixed(1)}k token đọc từ cache`
    : "";
  status(`${label} — lượt này ${money(c)}${cached} · phiên này ${money(state.session)}` +
         (state.doc.usage?.cost ? ` · cả bài ${money(state.doc.usage.cost)}` : ""));
}

/* ================================================= sơ đồ Mermaid ===== */

let mermaidReady = false;
function initMermaid() {
  if (mermaidReady || typeof mermaid === "undefined") return;
  // Theme TƯỜNG MINH phải thắng. Trước đây chỗ này chỉ đọc `prefers-color-scheme`,
  // nên chọn "Sáng" trên máy đang dark thì sơ đồ vẫn ra bảng màu tối — chữ đen
  // trên nền đen.
  const chon = document.documentElement.dataset.theme || "";
  const dark = chon === "dark"
    || (!chon && matchMedia("(prefers-color-scheme: dark)").matches);
  mermaid.initialize({
    startOnLoad: false,
    securityLevel: "strict",
    theme: "base",
    themeVariables: dark ? {
      // Bản vẽ: hộp xanh blueprint, nét phấn trắng, một hộp nhấn dạ quang.
      primaryColor: "#1a3b6e", primaryBorderColor: "#d9e6ff",
      primaryTextColor: "#eef4ff", lineColor: "#d9e6ff",
      secondaryColor: "#22477d", tertiaryColor: "#13305c",
      background: "#13305c", mainBkg: "#1a3b6e", nodeTextColor: "#eef4ff",
      fontSize: "15px",
    } : {
      // Sổ tay: hộp giấy, viền và mũi tên mực, hộp phụ màu giấy nhớ. Để màu
      // mặc định của mermaid (tím lavender) thì sơ đồ trông như dán từ chỗ khác.
      primaryColor: "#fffcf3", primaryBorderColor: "#1d1b18",
      primaryTextColor: "#1d1b18", lineColor: "#1d1b18",
      secondaryColor: "#fff4c2", tertiaryColor: "#e2e9fb",
      fontSize: "15px",
    },
    flowchart: { curve: "basis", htmlLabels: false },
    fontFamily: getComputedStyle(document.body).fontFamily,
  });
  mermaidReady = true;
}

let mermaidSeq = 0;
/** Vẽ sơ đồ vào `host`. Cú pháp hỏng thì hiện mã nguồn thay vì vỡ cả trang. */
async function drawDiagram(host, code, caption = "") {
  if (!host || !code || !code.trim()) return;
  initMermaid();
  const box = document.createElement("figure");
  box.className = "diagram";
  host.appendChild(box);
  try {
    if (typeof mermaid === "undefined") throw new Error("mermaid chưa nạp được");
    const { svg } = await mermaid.render("mmd" + ++mermaidSeq, code.trim());
    box.innerHTML = svg + (caption ? `<figcaption>${esc(caption)}</figcaption>` : "");
  } catch (e) {
    box.innerHTML =
      `<pre class="diagram-src">${esc(code.trim())}</pre>` +
      `<figcaption class="muted">Không vẽ được sơ đồ (${esc(e.message || "lỗi cú pháp")}).</figcaption>`;
  }
}

/* ===================================================== khởi động ===== */

init();
async function init() {
  const cfg = await fetch("/api/config").then((r) => r.json());
  $("#keyWarn").classList.toggle("hidden", cfg.has_key);
  // không cài docling thì ẩn hẳn lựa chọn đi cho đỡ rối
  $("#layoutWrap").classList.toggle("hidden", !cfg.layout_model);
  if (!cfg.layout_model) $("#useLayout").checked = false;
  const known = new Set(cfg.models.map((m) => m.id));
  if (!known.has(cfg.model)) cfg.models.unshift({ id: cfg.model, label: cfg.model + " (từ .env)" });
  state.models = cfg.models;
  fillModels($("#modelSelect"), cfg.model);
  // Mục "Tuỳ chọn" gập sẵn, nên dòng tóm tắt của nó phải nói đang chọn gì —
  // gập mà giấu luôn lựa chọn thì người dùng không biết bài sẽ dịch bằng gì.
  const tomTat = () => {
    const tt = [tenModel($("#modelSelect").value)];
    if (cfg.layout_model && $("#useLayout").checked) tt.push("dò bố cục");
    $("#impOptSum").textContent = "· " + tt.join(" · ");
  };
  $("#modelSelect").addEventListener("change", tomTat);
  $("#useLayout").addEventListener("change", tomTat);
  tomTat();
  loadRecent();
  loadDbStats();
  wireStart();
  wireReader();
  wireCrop();
  wireViewMenu();
  wireFind();
  wirePdfPane();
  wireHighlights();
  wireFigPeek();
  wireHashNav();
  // `#survey` do survey.js tự mở lúc nạp; ở đây chỉ lo hash trỏ tới một bài.
  const h = docHash();
  if (h.kind === "doc") openDoc(h.id).catch(() => (location.hash = ""));
}

/* ------------------------------------------------------ chọn model */

/* Nhãn đầy đủ quá dài cho thanh công cụ — lấy phần trước dấu gạch làm nhãn ngắn. */
const shortLabel = (m) => (m.label || m.id).split(" — ")[0];

function fillModels(sel, current, { short = false } = {}) {
  const list = state.models.slice();
  // bài cũ có thể dùng model không còn trong danh sách — vẫn phải hiện đúng
  if (current && !list.some((m) => m.id === current)) list.unshift({ id: current, label: current });
  sel.innerHTML = list.map((m) =>
    `<option value="${esc(m.id)}" title="${esc(m.label || m.id)}"` +
    `${m.id === current ? " selected" : ""}>${esc(short ? shortLabel(m) : m.label)}</option>`
  ).join("");
}

/* Đổi model cho những lượt gọi sau. Bộ nhớ dịch khoá theo (đoạn, model) nên
   phần đã dịch không bị đụng tới — chỉ mẻ chưa dịch mới chạy bằng model mới. */
async function setModel(id) {
  const r = await fetch(`/api/doc/${state.doc.id}/model`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ model: id }),
  });
  if (!r.ok) throw new Error(await apiErr(r, "Không đổi được model"));
  state.doc.model = id;
  $("#revModel").value = id;      // hai ô chọn ở hai màn hình luôn khớp nhau
  $("#docModel").value = id;
}

async function loadDbStats() {
  try {
    // `cache: "no-store"`: trình duyệt cache GET này nên sau khi nạp bài mới,
    // dòng thống kê vẫn là số cũ — đo được "27 bài" khi API đã trả 28.
    const d = await fetch("/api/db/stats", { cache: "no-store" }).then((r) => r.json());
    if (!d.tm_entries && !d.parse_cached) return;
    $("#dbStats").textContent =
      `Kho đã lưu: ${d.documents} bài · ${d.parse_cached} file đã bóc tách sẵn · ` +
      `${d.tm_entries} đoạn trong bộ nhớ dịch (đã dùng lại ${d.tm_hits} lượt) · ` +
      `${(d.db_bytes / 1048576).toFixed(1)} MB`;
  } catch { /* không có thì thôi */ }
}

/** Tên model cho người đọc. Slug của OpenRouter là mã định tuyến, không phải
    nhãn: `~deepseek/deepseek-v4-flash-latest` nói đúng một thứ hữu ích là
    "DeepSeek V4 Flash", phần còn lại là tiền tố hãng, dấu `~` của bản tự chọn
    endpoint, và hậu tố phiên bản.

    KHÔNG dựng bảng tra cả cái tên model: model mới xuất hiện liên tục nên bảng
    sẽ lệch, mà nhãn sai thì khó nhận ra hơn cả slug thô. Chỉ tra **cách viết
    hoa** của những chữ đã biết — danh sách đó nhỏ và ổn định — rồi gọt phần
    chắc chắn là thừa.

    Thử trên slug thật đang có trong `data/`: `Deepseek V4 Flash` sai hoa, và
    `qwen/qwen3-235b-a22b` ra `Qwen Qwen3 …` vì phép bỏ trùng tên hãng đòi có
    dấu gạch ngay sau. Hai ca đó sinh ra đúng hai luật dưới đây. */
const MODEL_HOA = {
  deepseek: "DeepSeek", openai: "OpenAI", gpt: "GPT", qwen: "Qwen",
  anthropic: "Anthropic", google: "Google", meta: "Meta", llama: "Llama",
  gemini: "Gemini", oss: "OSS", vl: "VL", moe: "MoE", glm: "GLM",
  // Mã hãng của OpenRouter có cả dấu gạch, nên tra cả chuỗi hãng nguyên vẹn.
  mistralai: "Mistral", "x-ai": "xAI", "z-ai": "Z.ai", moonshotai: "Moonshot",
  alibaba: "Alibaba", nvidia: "NVIDIA", microsoft: "Microsoft", ai21: "AI21",
};
function tenModel(slug) {
  if (!slug) return "";
  const sach = String(slug).replace(/^[~@]/, "");
  const hang = sach.includes("/") ? sach.split("/")[0] : "";
  const duoi = sach.split("/").pop().replace(/[-:](latest|preview|beta|stable)$/i, "");
  const hoa = (w) => {
    const k = w.toLowerCase();
    if (MODEL_HOA[k]) return MODEL_HOA[k];
    // Tên có số bản dính liền (`qwen3`, `llama4`) thì tra phần CHỮ rồi gắn số
    // lại. Không tách thì `qwen3` rơi vào luật "có chữ số" và ra `QWEN3`.
    const chia = /^([a-z]+)(\d.*)$/.exec(k);
    if (chia && MODEL_HOA[chia[1]]) return MODEL_HOA[chia[1]] + chia[2];
    // Còn lại, có chữ số thì viết hoa cả token: `235b` → `235B`, `v4` → `V4`,
    // `5.6` không đổi. Title Case ở đây cho ra `235b`, trông như lỗi gõ.
    return /\d/.test(w) ? w.toUpperCase() : w[0].toUpperCase() + w.slice(1);
  };
  const ten = duoi.split("-").filter(Boolean).map(hoa).join(" ");
  /* Hãng hay lặp tên mình vào cả tên model (`deepseek/deepseek-v4`,
     `qwen/qwen3-…`). Bỏ TIỀN TỐ HÃNG ở đầu ra, không cắt vào tên model — cắt
     vào tên thì `qwen3` thành `3`.

     So trên TÊN ĐÃ HIỆN, không so trên mã hãng: `mistralai/mistral-large` và
     `meta-llama/llama-4-…` đều lặp tên hãng mà mã lại không trùng tiền tố, nên
     phép so theo mã cho ra `Mistral Mistral Large`. */
  if (!hang) return ten;
  const nhan = hoa(hang);
  const dau = ten.split(" ")[0].toLowerCase();
  const h = hang.toLowerCase();
  // `startsWith` cho ca `qwen` ↔ `qwen3`, `includes` cho ca ngược lại
  // (`mistralai` ↔ `mistral`, `meta-llama` ↔ `llama`). Cần cả hai chiều.
  if (dau && (dau.startsWith(h) || h.includes(dau))) return ten;
  return nhan + " " + ten;
}

/** "3 ngày trước" dễ đọc hơn một mốc ngày tuyệt đối khi nó vừa mới xảy ra, và
    ngược lại khi nó đã lâu — nên đổi cách nói ở mốc một tuần. */
function khiNao(giay) {
  if (!giay) return "";
  const d = new Date(giay * 1000);
  const phut = (Date.now() - d.getTime()) / 60000;
  if (phut < 1) return "vừa xong";
  if (phut < 60) return `${Math.floor(phut)} phút trước`;
  if (phut < 1440) return `${Math.floor(phut / 60)} giờ trước`;
  if (phut < 10080) return `${Math.floor(phut / 1440)} ngày trước`;
  return d.toLocaleDateString("vi-VN", { day: "2-digit", month: "2-digit", year: "numeric" });
}

/** Nguồn bài gọn lại còn tên miền, hoặc tên file. Đường dẫn đầy đủ dài hơn cả
    tiêu đề và không cho biết thêm gì. */
function tenNguon(src) {
  if (!src) return "";
  try { return new URL(src).hostname.replace(/^www\./, ""); }
  catch { return src.split(/[\\/]/).pop().slice(0, 40); }
}

/** Nguồn bài rút gọn cho dòng trích dẫn trên slide (S6): `arXiv:1706.03762`
    thay cho cả URL; URL khác thì bỏ giao thức, `www.` và đuôi `.pdf`. Phải
    khớp từng chữ với `pipeline.nguon_gon()` — file xuất ra dùng bản đó. */
function nguonGon(src) {
  src = String(src || "").trim();
  const ax = src.match(/(?:arxiv\.org\/(?:abs|pdf)\/|arxiv:\s*)(\d{4}\.\d{4,5})/i);
  if (ax) return "arXiv:" + ax[1];
  return src.replace(/^https?:\/\//i, "").replace(/^www\./i, "")
    .replace(/\.pdf$/i, "").replace(/\/$/, "");
}

const RECENT_SORT = {
  moi: (a, b) => b.updated_at - a.updated_at,
  nap: (a, b) => b.created_at - a.created_at,
  ten: (a, b) => (a.title_vi || a.title).localeCompare(b.title_vi || b.title, "vi"),
  dich: (a, b) => tiLeDich(b) - tiLeDich(a),
  // Việc còn dở xếp trước, và bài chưa dịch gì cũng là việc còn dở — nhưng bài
  // đã xong 100% thì xuống cuối, chứ không lẫn vào giữa.
  chuadich: (a, b) => (tiLeDich(a) >= 1) - (tiLeDich(b) >= 1) || tiLeDich(a) - tiLeDich(b),
  tien: (a, b) => (b.cost_usd || 0) - (a.cost_usd || 0),
};
const tiLeDich = (d) => d.translatable ? d.translated / d.translatable : 0;

let recentDocs = [];

/* Trạng thái thư viện. `loc`: "all" | "none" (chưa xếp) | mã thư mục.
   `chon`: đang ở chế độ chọn nhiều. `moc`: thẻ bấm gần nhất, làm mốc cho
   Shift+bấm chọn cả dải — cách chọn mà ai dùng trình quản lý file cũng đã quen. */
const thuVien = { folders: [], loc: "all", chon: false, daChon: new Set(), moc: null };

/** Lọc và sắp thư viện theo ô tìm và ô sắp.

    Tìm **không dấu** (`khongDau`), cùng lý do với bộ tìm trong bài: gõ "truy
    hoi" phải ra "truy hồi" — không ai gõ dấu khi đang tìm nhanh. Và tìm cả
    `model` lẫn `source`, vì "bài nào tôi nạp từ arxiv" là câu hỏi thật. */
function loRecent() {
  const q = khongDau(($("#recentFind")?.value || "").trim());
  const xep = RECENT_SORT[$("#recentSort")?.value] || RECENT_SORT.moi;
  const loc = thuVien.loc;
  const trongTM = (d) => loc === "all" || (loc === "none" ? !d.folder_id : d.folder_id === loc);
  const ra = recentDocs.filter((d) => trongTM(d) && (!q || khongDau(
    [d.title_vi, d.title, tenModel(d.model), d.source].filter(Boolean).join(" ")
  ).includes(q)));
  return ra.sort(xep);
}

/** Tên hiện ra cho một bài, kèm cờ "chưa có tên đọc được".

    Đếm trong thư viện thật: 11/36 tiêu đề là rác — `(không tiêu đề)`, tiêu đề
    còn dính dấu Markdown (`# Contrastive Pretraining…`), `Tiêu đề thử`. Dấu `#`
    thì gỡ đi; bài không có tên thì hiện NGUỒN thay vào (tên file, tên miền) và
    đánh dấu để giao diện in nghiêng — người dùng thấy ngay bài nào cần đặt tên,
    thay vì năm dòng "(không tiêu đề)" giống hệt nhau. */
function tenBai(d) {
  const t = String(d.title_vi || d.title || "").replace(/^\s*#{1,6}\s+/, "").trim();
  if (t && t !== "(không tiêu đề)") return { ten: t, voDanh: false };
  return { ten: tenNguon(d.source) || "Bài chưa có tên", voDanh: true };
}

/** Thanh tiến độ dịch. Con số "đã dịch 72%" phải đọc mới hiểu, vạch thì liếc. */
function tienDo(d) {
  const pct = Math.round(tiLeDich(d) * 100);
  return `<div class="tien-do" role="progressbar" aria-valuenow="${pct}" aria-valuemin="0"
    aria-valuemax="100" aria-label="Đã dịch ${pct}%"><span class="${pct >= 100 ? "xong" : ""}"
    style="width:${pct}%"></span></div>`;
}

/** Con dấu tiến độ ở lề trái mỗi dòng thư viện: vòng tròn tô dần theo phần đã
    dịch, xong thì thành dấu ✓ xanh. Thanh dài bên dưới tên bài của bản trước
    lặp lại mười lần một vạch y hệt nhau (gần hết bài đều đã xong), chiếm cả một
    dòng mỗi bài mà mắt không học thêm được gì — con dấu nói cùng điều đó trong
    một ô 26px, và bài CHƯA xong thì nổi lên vì nó là vòng hở. */
function dauTienDo(d) {
  const pct = Math.round(tiLeDich(d) * 100);
  const nhan = pct >= 100 ? "Đã dịch xong" : `Đã dịch ${pct}%`;
  return `<span class="dau-td${pct >= 100 ? " xong" : pct === 0 ? " chua" : ""}"
    style="--p:${pct}" role="img" aria-label="${nhan}" title="${nhan}">${
    pct >= 100 ? ico("check") : ""}</span>`;
}

async function loadRecent() {
  [recentDocs, thuVien.folders] = await Promise.all([
    fetch("/api/docs").then((r) => r.json()),
    fetch("/api/folders").then((r) => (r.ok ? r.json() : [])),
  ]);
  // Thư mục đang lọc đã bị xoá (ở tab khác) thì về "Tất cả", đừng để danh
  // sách trống không lý do.
  if (!["all", "none"].includes(thuVien.loc) && !thuVien.folders.some((f) => f.id === thuVien.loc)) {
    thuVien.loc = "all";
  }
  // Bài đã chọn mà không còn nữa thì bỏ khỏi tập chọn.
  const con = new Set(recentDocs.map((d) => d.id));
  thuVien.daChon.forEach((id) => { if (!con.has(id)) thuVien.daChon.delete(id); });
  veFolders();
  $("#recentWrap").classList.toggle("hidden", !recentDocs.length);
  // Có bài thì đổi sang bố cục hai cột kèm thẻ "Đọc tiếp" (xem `#start.has-lib`).
  $("#start").classList.toggle("has-lib", recentDocs.length > 0);
  // Thanh công cụ chỉ đáng hiện khi danh sách đã dài tới mức phải tìm.
  $("#recentTools").classList.toggle("hidden", recentDocs.length < 6);
  veResume();
  veRecent();
}

/** Thẻ "Đọc tiếp": bài có hoạt động gần nhất. Người quay lại cần đúng một thứ
    là đọc tiếp bài dở, nên nó đứng đầu màn và bấm được cả thẻ. */
function veResume() {
  const box = $("#resume");
  const d = [...recentDocs].sort((x, y) => y.updated_at - x.updated_at)[0];
  box.classList.toggle("hidden", !d);
  if (!d) return;
  const { ten, voDanh } = tenBai(d);
  const pct = Math.round(tiLeDich(d) * 100);
  box.innerHTML = `
    <div class="resume-tag">Đọc tiếp</div>
    <b class="resume-t${voDanh ? " vo-danh" : ""}" title="${esc(ten)}">${esc(ten)}</b>
    <div class="resume-meta">${esc([pct >= 100 ? "đã dịch xong" : `đã dịch ${pct}%`,
      "mở " + khiNao(d.updated_at)].join(" · "))}</div>
    <button class="btn btn-primary" type="button">Mở tiếp →</button>
    ${tienDo(d)}`;
  box.onclick = () => openDoc(d.id);
}

/* ------------------------------------------------- thư mục & chọn nhiều */

/** Hàng tai thư mục: Tất cả · Chưa xếp · từng thư mục · + Thư mục. Mỗi tai vừa
    là bộ lọc vừa là chỗ THẢ thẻ bài vào ("Tất cả" thì không — thả vào đó không
    có nghĩa gì). Thư mục đang mở có thêm hai nút đổi tên / xoá ngay cạnh. */
function veFolders() {
  const bar = $("#folderBar");
  const tong = recentDocs.length;
  const chuaXep = recentDocs.filter((d) => !d.folder_id).length;
  const tai = (loc, ten, so, icon = "folder") => `
    <button class="folder${thuVien.loc === loc ? " is-on" : ""}" data-loc="${esc(loc)}" type="button"
        title="${esc(ten)}">${icon ? ico(icon) : ""}<span class="ten">${esc(ten)}</span><span class="so">${so}</span></button>`;
  const dangMo = thuVien.folders.find((f) => f.id === thuVien.loc);
  bar.innerHTML = tai("all", "Tất cả", tong, "")
    + (thuVien.folders.length ? tai("none", "Chưa xếp", chuaXep, "") : "")
    + thuVien.folders.map((f) => tai(f.id, f.name, f.n)).join("")
    + (dangMo ? `<span class="folder-act">
        <button class="icon-btn" data-tm-ren type="button" title="Đổi tên thư mục “${esc(dangMo.name)}”">${ico("pencil")}</button>
        <button class="icon-btn" data-tm-del type="button" title="Xoá thư mục “${esc(dangMo.name)}” (bài bên trong về Chưa xếp)">${ico("trash")}</button>
      </span>` : "")
    + `<button class="folder them" data-tm-new type="button" title="Tạo thư mục mới">${ico("folder-plus")}<span class="ten">Thư mục</span></button>`;

  $$(".folder[data-loc]", bar).forEach((b) => {
    b.onclick = () => {
      thuVien.loc = b.dataset.loc;
      setPref("libfolder", thuVien.loc);
      veFolders();
      veRecent();
    };
    if (b.dataset.loc === "all") return;
    const fid = b.dataset.loc === "none" ? null : b.dataset.loc;
    b.ondragover = (e) => {
      if (!e.dataTransfer.types.includes("application/x-loupe-docs")) return;
      e.preventDefault(); e.dataTransfer.dropEffect = "move"; b.classList.add("tha-duoc");
    };
    b.ondragleave = () => b.classList.remove("tha-duoc");
    b.ondrop = (e) => {
      e.preventDefault(); b.classList.remove("tha-duoc");
      let ids = [];
      try { ids = JSON.parse(e.dataTransfer.getData("application/x-loupe-docs")); } catch { /* bỏ */ }
      if (ids.length) chuyenThuMuc(ids, fid);
    };
  });
  const ren = $("[data-tm-ren]", bar), del = $("[data-tm-del]", bar);
  if (ren) ren.onclick = () => doiTenThuMuc(dangMo);
  if (del) del.onclick = () => xoaThuMuc(dangMo);
  $("[data-tm-new]", bar).onclick = () => taoThuMuc();
}

async function taoThuMuc() {
  const ten = await nhapChu("Thư mục mới", "Đặt tên theo chủ đề hay theo việc đang làm — "
    + "\"Robot học\", \"Đọc cho luận văn\"… Bài vẫn tìm được từ \"Tất cả\".", "");
  if (!ten || !ten.trim()) return null;
  const r = await fetch("/api/folders", {
    method: "POST", headers: { "content-type": "application/json" },
    body: JSON.stringify({ name: ten }),
  });
  if (!r.ok) { await baoTin("Chưa tạo được thư mục", await apiErr(r)); return null; }
  const f = await r.json();
  await loadRecent();
  return f;
}

async function doiTenThuMuc(f) {
  const ten = await nhapChu("Đổi tên thư mục", "", f.name);
  if (!ten || !ten.trim() || ten.trim() === f.name) return;
  const r = await fetch(`/api/folders/${f.id}`, {
    method: "PATCH", headers: { "content-type": "application/json" },
    body: JSON.stringify({ name: ten }),
  });
  if (!r.ok) return baoTin("Chưa đổi được tên", await apiErr(r));
  loadRecent();
}

async function xoaThuMuc(f) {
  // Nói rõ BÀI KHÔNG MẤT: "xoá thư mục" nghe như xoá cả thứ bên trong, mà thứ
  // bên trong là bản dịch đã trả tiền.
  if (!await xacNhan(`Xoá thư mục “${f.name}”?`,
    f.n ? `${f.n} bài bên trong KHÔNG bị xoá — chúng về mục "Chưa xếp".`
        : "Thư mục đang trống.",
    { ok: "Xoá thư mục" })) return;
  const r = await fetch(`/api/folders/${f.id}`, { method: "DELETE" });
  if (!r.ok) return baoTin("Chưa xoá được thư mục", await apiErr(r));
  thuVien.loc = "all";
  setPref("libfolder", "all");
  await loadRecent();
  baoNhanh(`Đã xoá thư mục “${f.name}”` + (f.n ? ` · ${f.n} bài về Chưa xếp` : ""));
}

/** Chuyển bài vào thư mục (`null` = Chưa xếp), kèm nút Hoàn lại — chuyển nhầm
    chỗ là chuyện thường khi kéo-thả, và đưa về đúng chỗ cũ thì miễn phí. */
async function chuyenThuMuc(ids, fid) {
  const cu = new Map(ids.map((id) => [id, recentDocs.find((d) => d.id === id)?.folder_id ?? null]));
  const goi = (ds, f) => fetch("/api/docs/move", {
    method: "POST", headers: { "content-type": "application/json" },
    body: JSON.stringify({ ids: ds, folder_id: f }),
  });
  const r = await goi(ids, fid);
  if (!r.ok) return baoTin("Chưa chuyển được", await apiErr(r));
  const ten = fid ? thuVien.folders.find((f) => f.id === fid)?.name : "Chưa xếp";
  thuVien.daChon.clear();
  await loadRecent();
  baoNhanh(`Đã chuyển ${ids.length} bài vào “${ten}”`, async () => {
    // Hoàn lại theo TỪNG thư mục cũ — các bài có thể đến từ nhiều chỗ khác nhau.
    const nhom = new Map();
    cu.forEach((f, id) => nhom.set(f, [...(nhom.get(f) || []), id]));
    for (const [f, ds] of nhom) await goi(ds, f);
    loadRecent();
  });
}

function batChon(on) {
  thuVien.chon = on;
  if (!on) { thuVien.daChon.clear(); thuVien.moc = null; }
  $("#selBtn").classList.toggle("is-on", on);
  $("#selBtn").textContent = on ? "Đang chọn" : "Chọn";
  veRecent();
}

function chonBai(id, e) {
  if (!thuVien.chon) batChon(true);
  const ds = loRecent().map((d) => d.id);
  if (e?.shiftKey && thuVien.moc && ds.includes(thuVien.moc)) {
    // Shift+bấm: chọn cả dải từ mốc tới đây, theo đúng thứ tự đang HIỆN.
    const [i, j] = [ds.indexOf(thuVien.moc), ds.indexOf(id)].sort((x, y) => x - y);
    ds.slice(i, j + 1).forEach((x) => thuVien.daChon.add(x));
  } else if (thuVien.daChon.has(id)) {
    thuVien.daChon.delete(id);
  } else {
    thuVien.daChon.add(id);
  }
  thuVien.moc = id;
  veRecent();
  // Giữ tiêu điểm trên thẻ vừa chọn — vẽ lại làm mất nó, mà người dùng bàn
  // phím đang đứng đúng ở đó.
  $(`#recentList li[data-open="${CSS.escape(id)}"]`)?.focus({ preventScroll: true });
}

function capNhatSelBar() {
  const n = thuVien.daChon.size;
  $("#selBar").classList.toggle("hidden", !thuVien.chon);
  $("#selCount").textContent = n ? `Đã chọn ${n} bài` : "Chưa chọn bài nào";
  $("#selMove").disabled = $("#selDel").disabled = !n;
  const hien = loRecent().map((d) => d.id);
  const het = hien.length && hien.every((id) => thuVien.daChon.has(id));
  $("#selAll").textContent = het ? "Bỏ chọn hết" : `Chọn hết (${hien.length})`;
}

async function xoaNhieu() {
  const ds = recentDocs.filter((d) => thuVien.daChon.has(d.id));
  if (!ds.length) return;
  const tien = ds.reduce((s, d) => s + (+d.cost_usd || 0), 0);
  const ten = ds.slice(0, 5).map((d) => "· " + tenBai(d).ten).join("\n")
    + (ds.length > 5 ? `\n· …và ${ds.length - 5} bài nữa` : "");
  if (!await xacNhan(`Xoá ${ds.length} bài khỏi máy?`,
    `${ten}\n\n` + (tien ? `Các bài này đã tốn tổng cộng $${tien.toFixed(4).replace(".", ",")} để dịch.\n` : "")
    + "Mất cả bản dịch, ghi chú, vệt bôi và bộ slide. Không hoàn lại được.",
    { ok: `Xoá ${ds.length} bài`, hong: true })) return;
  const r = await fetch("/api/docs/delete", {
    method: "POST", headers: { "content-type": "application/json" },
    body: JSON.stringify({ ids: ds.map((d) => d.id) }),
  });
  if (!r.ok) return baoTin("Chưa xoá được", await apiErr(r));
  const { deleted } = await r.json();
  batChon(false);
  await loadRecent();
  baoNhanh(`Đã xoá ${deleted} bài`);
}

/** Menu "Chuyển vào…": mọi thư mục, "Chưa xếp", và tạo thư mục mới ngay tại chỗ
    — bắt người dùng thoát ra tạo thư mục rồi chọn lại từ đầu là mất cả lựa chọn. */
function veMenuChuyen() {
  const m = $("#selMoveMenu");
  m.innerHTML = thuVien.folders.map((f) =>
      `<a href="#" data-to="${esc(f.id)}">${ico("folder")} ${esc(f.name)}</a>`).join("")
    + (thuVien.folders.length ? `<a href="#" data-to="">Chưa xếp</a><hr>` : "")
    + `<a href="#" data-to-new>${ico("folder-plus")} Thư mục mới…</a>`;
  $$("a", m).forEach((x) => (x.onclick = async (e) => {
    e.preventDefault();
    m.classList.add("hidden");
    const ids = [...thuVien.daChon];
    if (x.hasAttribute("data-to-new")) {
      const f = await taoThuMuc();
      if (f) chuyenThuMuc(ids, f.id);
      return;
    }
    chuyenThuMuc(ids, x.dataset.to || null);
  }));
}

function wireThuVien() {
  thuVien.loc = pref("libfolder", "all");
  $("#selBtn").onclick = () => batChon(!thuVien.chon);
  $("#selDone").onclick = () => batChon(false);
  $("#selDel").onclick = xoaNhieu;
  $("#selAll").onclick = () => {
    const hien = loRecent().map((d) => d.id);
    const het = hien.every((id) => thuVien.daChon.has(id));
    hien.forEach((id) => (het ? thuVien.daChon.delete(id) : thuVien.daChon.add(id)));
    veRecent();
  };
  $("#selMove").onclick = (e) => {
    e.stopPropagation();
    veMenuChuyen();
    $("#selMoveMenu").classList.toggle("hidden");
  };
  $("#selMoveMenu").onclick = (e) => e.stopPropagation();
  document.addEventListener("click", () => $("#selMoveMenu").classList.add("hidden"));
  // Esc thoát chế độ chọn — nhưng nhường cho hộp thoại và ô đổi tên nếu đang mở.
  document.addEventListener("keydown", (e) => {
    if (e.key !== "Escape" || !thuVien.chon || $("#start").classList.contains("hidden")) return;
    if (!$("#dlgVeil").classList.contains("hidden") || e.target.closest?.("input:not(.chon-o)")) return;
    batChon(false);
  });
}

function veRecent() {
  const docs = loRecent();
  // Bản mới nhất của mỗi họ phiên bản — tô nổi để khỏi mở nhầm bản cũ.
  const moiNhat = new Map();
  recentDocs.forEach((d) => {
    if (d.version_of) moiNhat.set(d.version_of, Math.max(moiNhat.get(d.version_of) || 0, d.version));
  });
  $("#recentEmpty").classList.toggle("hidden", !!docs.length || !recentDocs.length);
  $("#recentList").innerHTML = docs
    .map((d) => {
      const pct = Math.round(tiLeDich(d) * 100);
      const nguon = tenNguon(d.source);
      // Ba con số, mỗi con trả lời một câu khác nhau: còn bao nhiêu việc, đọc
      // lần cuối khi nào, và đã bỏ ra bao nhiêu tiền.
      // Đang xem "Tất cả" thì thẻ nói nó nằm ở thư mục nào; đang trong một thư
      // mục thì nói lại là thừa.
      const tm = thuVien.loc === "all" && d.folder_id
        && thuVien.folders.find((f) => f.id === d.folder_id);
      // "đã dịch xong" không nhắc lại — con dấu ở lề đã nói; còn dở thì nói số.
      const meta = [
        `${d.blocks} khối`,
        pct >= 100 ? "" : `dịch ${pct}%`,
        tenModel(d.model),
        d.cost_usd ? `$${(+d.cost_usd).toFixed(2).replace(".", ",")}` : "",
      ].filter(Boolean).join(" · ");
      const { ten, voDanh } = tenBai(d);
      const chon = thuVien.daChon.has(d.id);
      return `<li data-open="${esc(d.id)}" tabindex="0" draggable="true"
          class="${chon ? "da-chon" : ""}"${thuVien.chon ? ` aria-selected="${chon}"` : ""}>
        ${thuVien.chon ? `<input type="checkbox" class="chon-o" data-chon="${esc(d.id)}"
            ${chon ? "checked" : ""} aria-label="Chọn bài này">` : dauTienDo(d)}
        <span class="rt" data-id="${esc(d.id)}">
          <b class="${voDanh ? "vo-danh" : ""}">${esc(ten)}${d.version
            ? `<span class="ver${moiNhat.get(d.version_of) === d.version ? " moi-nhat" : ""}"
                title="Phiên bản ${d.version} của bài này${moiNhat.get(d.version_of) === d.version ? " — bản mới nhất" : ""}">v${d.version}</span>` : ""}</b>
          <span>${tm ? `<span class="the-tm">${ico("folder")}${esc(tm.name)}</span> · ` : ""}${esc(meta)}</span>
        </span>
        <span class="rwhen" title="${esc("Nạp " + khiNao(d.created_at)
          + (nguon ? " từ " + nguon : ""))}">${esc(khiNao(d.updated_at))}${
          nguon ? `<em>${esc(nguon)}</em>` : ""}</span>
        <button class="icon-btn" data-ren="${esc(d.id)}" title="Đổi tên bài">${ico("pencil")}</button>
        <button class="icon-btn" data-del="${esc(d.id)}" title="Xoá">${ico("trash")}</button>
      </li>`;
    })
    .join("");
  $("#recentList").classList.toggle("dang-chon", thuVien.chon);
  capNhatSelBar();
  /* CẢ THẺ mở bài — trừ khi bấm trúng nút sửa/xoá hay ô đang đổi tên. Thiếu
     phép trừ này thì bấm 🗑 là vừa mở bài vừa hỏi xoá.

     Ở chế độ chọn thì bấm thẻ là CHỌN. Ctrl/⌘+bấm ở chế độ thường cũng vào
     chế độ chọn luôn, Shift+bấm chọn cả dải — đúng lối của trình quản lý file. */
  $$("#recentList li[data-open]").forEach((el) => {
    const id = el.dataset.open;
    el.onclick = (e) => {
      if (e.target.closest("button")) return;
      if (e.target.matches(".chon-o")) return chonBai(id, e);   // ô tick tự lo
      if (e.target.closest("input")) return;                    // ô đổi tên
      if (thuVien.chon || e.ctrlKey || e.metaKey || e.shiftKey) {
        e.preventDefault();
        return chonBai(id, e);
      }
      openDoc(id);
    };
    el.onkeydown = (e) => {
      if (e.target !== el) return;
      if (e.key === "Enter" || (e.key === " " && thuVien.chon)) {
        e.preventDefault();
        return thuVien.chon ? chonBai(id, e) : openDoc(id);
      }
    };
    /* Kéo thẻ thả vào tai thư mục để chuyển. Kéo một thẻ ĐÃ CHỌN là kéo cả
       nhóm đang chọn — thả một mình nó thì nhóm còn lại bị bỏ quên. */
    el.ondragstart = (e) => {
      const ids = thuVien.daChon.has(id) ? [...thuVien.daChon] : [id];
      e.dataTransfer.setData("application/x-loupe-docs", JSON.stringify(ids));
      e.dataTransfer.effectAllowed = "move";
      el.classList.add("dang-keo");
    };
    el.ondragend = () => el.classList.remove("dang-keo");
  });
  /* Tiêu đề đoán từ khối đầu trang nên hay sai — dính tên hội nghị, dính số
     trang, hoặc cụt còn vài chữ. Nó hiện ở danh sách này, ở đầu bản xuất ra và
     ở slide tiêu đề, nên sai một chỗ là sai khắp nơi. Đổi tên không đụng nội
     dung: `title` không nằm trong `cached_prefix` nên không có bản dịch nào
     phải bỏ đi. */
  /* Sửa NGAY TRÊN DÒNG, không mở hộp thoại: đổi tên là việc nhẹ và người dùng
     cần thấy tên cũ nằm cạnh các bài khác trong lúc gõ — đó mới là lý do họ
     biết tên này sai. Hộp thoại che mất chính cái ngữ cảnh ấy. */
  $$("#recentList [data-ren]").forEach((el) => (el.onclick = () => {
    const id = el.dataset.ren;
    const nhan = $(`#recentList .rt[data-id="${id}"] b`);
    if (!nhan || nhan.querySelector("input")) return;
    // Tên cũ lấy từ DỮ LIỆU, không từ `textContent`: thẻ còn chứa nhãn "v2",
    // lấy chữ trên màn thì tên lưu lại dính luôn "v2". Bài vô danh thì ô nhập
    // để trống — điền sẵn tên nguồn ("paper.docx") là mời lưu nhầm nó làm tên.
    const d0 = recentDocs.find((x) => x.id === id) || {};
    const { ten: hien, voDanh } = tenBai(d0);
    const cu = voDanh ? "" : hien;
    const inp = Object.assign(document.createElement("input"), {
      className: "input input-inline", value: cu, placeholder: voDanh ? `Đặt tên — đang hiện "${hien}"` : "",
    });
    nhan.textContent = "";
    nhan.append(inp);
    inp.focus();
    inp.select();
    // Chặn click nổi lên `.rt` — không thì bấm vào ô nhập là mở bài.
    inp.onclick = (e) => e.stopPropagation();
    let xong = false;
    // Huỷ hay không đổi gì thì vẽ lại thẻ, không gán chữ trơn — gán chữ là mất
    // nhãn phiên bản và kiểu chữ nghiêng của bài vô danh.
    const thoi = () => { if (!xong) { xong = true; veRecent(); } };
    const luu = async () => {
      if (xong) return;
      xong = true;
      const t = inp.value.trim();
      if (!t || t === cu) { veRecent(); return; }
      nhan.textContent = t;
      const r = await fetch(`/api/doc/${id}/title`, {
        method: "PATCH", headers: { "content-type": "application/json" },
        body: JSON.stringify({ title: t }),
      });
      if (!r.ok) { veRecent(); baoNhanh(await apiErr(r, "Không lưu được tên.")); return; }
      loadRecent();
    };
    inp.onblur = luu;
    inp.onkeydown = (e) => {
      if (e.key === "Enter") { e.preventDefault(); luu(); }
      else if (e.key === "Escape") { e.preventDefault(); thoi(); }
    };
  }));
  $$("#recentList [data-del]").forEach((el) => (el.onclick = async () => {
    const id = el.dataset.del;
    const d = recentDocs.find((x) => x.id === id) || {};
    // Nói ra mất gì: bản dịch là thứ đã trả tiền, "bạn có chắc không" mà không
    // kèm cái giá thì người dùng không có cơ sở nào để chắc.
    const gia = d.cost_usd
      ? `Bài này đã tốn $${(+d.cost_usd).toFixed(4).replace(".", ",")} để dịch.\n\n` : "";
    if (!await xacNhan(
      "Xoá bài này khỏi máy?",
      `${d.title_vi || d.title || ""}\n\n${gia}`
      + "Mất cả bản dịch, ghi chú, vệt bôi và bộ slide. Không hoàn lại được.",
      { ok: "Xoá", hong: true },
    )) return;
    const r = await fetch(`/api/doc/${id}`, { method: "DELETE" });
    if (!r.ok) return baoTin("Không xoá được", await apiErr(r));
    loadRecent();
  }));
}

/* ================================================ màn hình nhập ===== */

function wireStart() {
  /* Thư viện: ô tìm và ô sắp vẽ lại tại chỗ, KHÔNG gọi lại `/api/docs` — dữ
     liệu đã có trong `recentDocs`, gọi lại mỗi lần gõ một ký tự là đổi một
     phép lọc trong bộ nhớ thành một vòng đọc cả bảng `documents`. */
  $("#recentFind").oninput = veRecent;
  $("#recentFind").onkeydown = (e) => {
    if (e.key !== "Escape") return;
    e.target.value = "";
    veRecent();
  };
  $("#recentSort").onchange = () => {
    setPref("recentsort", $("#recentSort").value);
    veRecent();
  };
  $("#recentSort").value = pref("recentsort", "moi");
  wireThuVien();

  $$(".tab").forEach((t) => (t.onclick = () => {
    $$(".tab").forEach((x) => x.classList.toggle("is-on", x === t));
    $$("[data-pane]", $("#start")).forEach((p) =>
      p.classList.toggle("hidden", p.dataset.pane !== t.dataset.tab));
    // Lỗi và nhật ký của lần trước thuộc về NGUỒN trước. Để nguyên thì
    // "Chưa chọn nguồn nào." còn đỏ chót trong khi người dùng đã sang tab khác
    // và đang gõ, rồi tiến trình của lần nạp cũ vẫn chạy trên màn hình.
    startSach();
  }));

  const drop = $("#drop"), input = $("#fileInput");
  drop.onclick = () => input.click();
  input.onchange = () => { if (input.files[0]) $("#fileName").textContent = "Đã chọn: " + input.files[0].name; };
  ["dragenter", "dragover"].forEach((e) => drop.addEventListener(e, (ev) => {
    ev.preventDefault(); drop.classList.add("over");
  }));
  ["dragleave", "drop"].forEach((e) => drop.addEventListener(e, () => drop.classList.remove("over")));
  drop.addEventListener("drop", (ev) => {
    ev.preventDefault();
    if (ev.dataTransfer.files[0]) {
      input.files = ev.dataTransfer.files;
      $("#fileName").textContent = "Đã chọn: " + input.files[0].name;
    }
  });

  $("#importBtn").onclick = doImport;
  $("#urlInput").addEventListener("keydown", (e) => { if (e.key === "Enter") doImport(); });
  // Bắt đầu gõ là lỗi cũ hết nghĩa — xoá luôn, đừng bắt người dùng nhìn một
  // câu đỏ nói về thao tác họ đã bỏ qua từ lâu.
  ["#urlInput", "#textInput"].forEach((sel) =>
    $(sel)?.addEventListener("input", () => $("#startErr").classList.add("hidden")));
}

/** Dọn mọi dấu vết của lần nạp trước trên màn hình nhập. */
function startSach() {
  $("#startErr")?.classList.add("hidden");
  $("#impProg")?.classList.add("hidden");
}

/* Nạp bài đi qua nhiều bước, bước chạy mô hình bố cục lâu nhất. Client mở kênh
   SSE TRƯỚC rồi mới POST, nên biết server đang ở bước nào — thay cho một nút
   đứng im không phân biệt được "đang chạy" với "đã treo". */
function impStart() {
  const box = $("#impProg");
  box.classList.remove("hidden");
  $("#impSteps").innerHTML = "";
  $("#impFill").style.width = "2%";
  $("#impStage").textContent = "Đang bắt đầu…";
  $("#impDetail").textContent = "";
  const t0 = Date.now();
  const tick = setInterval(() => {
    const s = Math.round((Date.now() - t0) / 1000);
    $("#impClock").textContent = `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
  }, 250);

  const job = "j" + Math.random().toString(36).slice(2, 12);
  const es = new EventSource(`/api/import/${job}/progress`);
  let last = null, lastAt = t0;
  es.addEventListener("step", (e) => {
    const { stage, detail, pct } = JSON.parse(e.data);
    if (last) {                       // chốt bước vừa xong, kèm thời gian đã tốn
      const li = $("#impSteps").lastElementChild;
      if (li) {
        li.classList.remove("now");
        li.querySelector(".t").textContent =
          ((Date.now() - lastAt) / 1000).toFixed(1) + "s";
      }
    }
    if (stage !== "Xong" && stage !== "Lỗi") {
      const li = document.createElement("li");
      li.className = "now";
      li.innerHTML = `<b>${esc(stage)}</b><span>${esc(detail || "")}</span><span class="t"></span>`;
      $("#impSteps").appendChild(li);
    }
    $("#impStage").textContent = stage;
    $("#impDetail").textContent = detail || "";
    if (pct != null) $("#impFill").style.width = pct + "%";
    last = stage; lastAt = Date.now();
  });
  es.onerror = () => {};              // server đóng kênh khi xong, không phải lỗi

  return {
    job,
    done(ok) {
      clearInterval(tick);
      es.close();
      $("#impFill").style.width = "100%";
      const li = $("#impSteps").lastElementChild;
      if (li) li.classList.remove("now");
      if (!ok) box.classList.add("hidden");
    },
  };
}

async function doImport() {
  const btn = $("#importBtn"), err = $("#startErr");
  err.classList.add("hidden");
  const fd = new FormData();
  fd.append("model", $("#modelSelect").value);
  fd.append("use_layout", $("#useLayout").checked ? "1" : "0");
  const f = $("#fileInput").files[0];
  const active = $(".tab.is-on").dataset.tab;
  if (active === "file" && f) {
    // Lọc ngay ở đây cho nhanh; server vẫn kiểm lại bằng magic bytes vì đuôi
    // file không đáng tin (`paper.docx` đổi tên là lọt).
    if (!/\.(pdf|txt|md|markdown)$/i.test(f.name)) {
      return showErr(`Chỉ nhận .pdf, .txt và .md — "${f.name}" không thuộc nhóm đó. `
        + "Với file Word, hãy xuất ra PDF rồi tải lên lại.");
    }
    fd.append("file", f);
  } else if (active === "url") {
    const u = $("#urlInput").value.trim();
    if (!u) return showErr("Chưa nhập mã arXiv hay link PDF.");
    // Mã arXiv (YYMM.NNNNN[vN]) hoặc một URL http(s). Sai thì nói luôn ở đây,
    // đừng để server phải đoán rồi trả về một lỗi khó hiểu.
    if (!/^\d{4}\.\d{4,5}(v\d+)?$/i.test(u)
        && !/^arxiv:\s*\d{4}\.\d{4,5}(v\d+)?$/i.test(u)
        && !/^https?:\/\/\S+$/i.test(u)) {
      return showErr("Không nhận ra mã arXiv hay link PDF. "
        + "Ví dụ: 1706.03762 · arXiv:1706.03762v7 · https://…/paper.pdf");
    }
    fd.append("url", u);
  } else if (active === "text") {
    if (!$("#textInput").value.trim()) return showErr("Ô dán văn bản đang trống.");
    fd.append("text", $("#textInput").value);
  } else return showErr("Chưa chọn nguồn nào.");

  btn.disabled = true; btn.textContent = "Đang đọc tài liệu…";
  try {
    // Vòng lặp chạy nhiều nhất hai lượt: lượt đầu có thể trả về "đã có bài
    // này", lượt sau gửi lại cùng form kèm `force=1`. Dùng vòng lặp chứ không
    // gọi đệ quy, để `finally` ở dưới mở khoá nút đúng một lần.
    for (;;) {
      // mở kênh tiến trình TRƯỚC khi POST, không thì mất mấy bước đầu
      const prog = impStart();
      fd.set("job", prog.job);
      await new Promise((r) => setTimeout(r, 120));   // chờ SSE bắt tay xong
      let doc;
      try {
        const r = await fetch("/api/import", { method: "POST", body: fd });
        if (!r.ok) throw new Error(await apiErr(r, r.statusText));
        doc = await r.json();
        prog.done(true);
      } catch (e) { prog.done(false); throw e; }
      if (doc.duplicate) {
        const chon = await hoiTrung(doc.duplicate);   // { che_do, goc } | null
        if (!chon) return;
        fd.delete("force");
        fd.set("che_do", chon.che_do);
        fd.set("goc", chon.goc || "");
        continue;
      }
      sachNguon();
      location.hash = doc.id;
      if (doc.ghi_de) {
        // Ghi đè trả về CHÍNH bài cũ: đã qua bước soát thì vào thẳng màn đọc.
        const st = doc.ghi_de;
        if (doc.prepared) mountDoc(doc); else mountReview(doc);
        // "giữ N đoạn không đổi", không "giữ N đoạn kèm bản dịch": bài chưa dịch
        // thì câu sau nói quá điều đã xảy ra.
        baoNhanh(`Đã ghi đè: ${st.kept} đoạn không đổi được giữ nguyên · ${st.new} đoạn mới`
          + (st.to_translate ? ` · ${st.to_translate} đoạn chờ dịch` : ""));
      } else {
        mountReview(doc);        // bước 1 trước, dịch sau
        if (doc.version) baoNhanh(`Đã lưu thành phiên bản v${doc.version}`);
      }
      return;
    }
  } catch (e) {
    showErr(e.message);
  } finally {
    btn.disabled = false; btn.textContent = "Nạp bài báo";
  }

  function showErr(m) { err.textContent = m; err.classList.remove("hidden"); }

  /* Reset ô nguồn sau khi nạp xong. Không reset thì dòng "Đã chọn: paper.md"
     còn nguyên, người dùng bấm Nạp lần nữa là tạo thêm một bản trùng — đã ra
     5 bản "Attention Is All You Need" trong kho đúng vì vậy. */
  function sachNguon() {
    $("#fileInput").value = "";
    $("#urlInput").value = "";
    // Ô dán cũng phải xoá: còn nguyên văn bản thì bấm Nạp lần nữa là nạp lại
    // đúng bài vừa xong (#32).
    $("#textInput").value = "";
    const fn = $("#fileName");
    if (fn) fn.textContent = "";
  }

  /* Bài trùng. Server dừng TRƯỚC bước tốn thời gian (mô hình bố cục), chưa tạo
     bản ghi nào — việc còn lại là hỏi người dùng muốn gì. Hai ca, hai bộ lựa
     chọn, vì câu hỏi trong đầu người dùng khác nhau:

     - CÙNG FILE: "nạp lại cái này làm gì?" — mở bản cũ, bóc lại vào bản cũ
       bằng bộ bóc mới nhất, hay (hiếm) một bản riêng.
     - CÙNG BÀI, KHÁC FILE (arXiv v2, bản sửa): "bản mới này quan hệ thế nào với
       bản cũ?" — phiên bản kế tiếp, thay hẳn bản cũ, hay thật ra là bài khác.

     Esc / bấm ra ngoài / "Thôi" là KHÔNG LÀM GÌ, kể cả không tự mở bài nào.
     Trả `{ che_do, goc }` để gửi lại, hoặc `null`. */
  async function hoiTrung(d) {
    const pct = d.translatable ? Math.round(d.translated / d.translatable * 100) : 0;
    const gia = d.cost_usd ? `, đã tốn $${(+d.cost_usd).toFixed(4).replace(".", ",")}` : "";
    const vs = d.versions || [];
    const ver = vs.length > 1 ? ` Họ bài này đã có ${vs.length} phiên bản (v${vs.join(", v")}).` : "";
    const than = `${d.title}${d.version ? ` · v${d.version}` : ""}\n\n`
      + `Bản đang có: ${d.blocks} khối, đã dịch ${pct}%${gia}.${ver}`;
    const mo = { key: "mo", nhan: "Mở bản đang có", giai: "Không nạp gì thêm." };
    const chon = d.kind === "cung_file"
      ? await chonMot("Bài này đã có trong thư viện", than + "\n\nĐúng y nội dung này đã được nạp trước đây.", [
        { ...mo, kieu: "chinh" },
        { key: "ghi_de", nhan: "Bóc lại vào bản đang có",
          giai: "Dùng bộ bóc mới nhất cho chính bài cũ. Đoạn nào chữ không đổi giữ nguyên bản dịch, ghi chú, vệt bôi. Miễn phí." },
        { key: "moi", nhan: "Tạo một bản riêng",
          giai: "Bài mới CHƯA DỊCH GÌ, nằm cạnh bản cũ — dễ mở nhầm rồi tưởng mất bản dịch." },
      ], "Thôi")
      : await chonMot("Có vẻ đây là bài đã có", than
          + "\n\nFile khác, nhưng cùng tiêu đề hoặc cùng mã arXiv — có thể là phiên bản mới hay bản đã sửa.", [
        { key: "phien_ban", kieu: "chinh",
          nhan: `Lưu thành phiên bản mới (v${(vs.length ? Math.max(...vs) : 1) + 1})`,
          giai: "Bài mới, đánh dấu là phiên bản kế tiếp, vào cùng thư mục với bản cũ. Bản cũ giữ nguyên." },
        { key: "ghi_de", kieu: "hong", nhan: "Ghi đè bản cũ bằng bản này",
          giai: "Đoạn nào chữ không đổi giữ bản dịch; đoạn mới hay đã sửa phải dịch lại. Nội dung bản cũ không còn." },
        mo,
        { key: "moi", nhan: "Đây là bài khác", giai: "Nạp thành bài riêng, không liên quan tới bài trên." },
      ], "Thôi");
    if (!chon) return null;
    if (chon === "mo") {
      sachNguon();
      await openDoc(d.id);
      return null;
    }
    // Ghi đè là việc có mất mát ở ca khác file — hỏi lại một lần, nói rõ mất gì.
    if (chon === "ghi_de" && d.kind === "cung_bai" && !await xacNhan("Ghi đè bản cũ?",
        `${d.title}\n\nNội dung bản cũ được thay bằng bản này. Bản dịch của các đoạn không đổi `
        + "được giữ lại; đoạn đã sửa hoặc bị bỏ thì mất bản dịch. Không hoàn lại được.\n\n"
        + "Muốn giữ cả hai thì chọn \"Lưu thành phiên bản mới\".",
        { ok: "Ghi đè", hong: true })) return null;
    return { che_do: chon, goc: d.id };
  }
}

async function openDoc(id) {
  const r = await fetch(`/api/doc/${id}`);
  if (!r.ok) throw new Error("not found");
  location.hash = id;
  const doc = await r.json();
  // Bài chưa qua bước 1 thì vào bước 1; đã chốt rồi thì vào thẳng màn hình đọc.
  if (doc.prepared) mountDoc(doc);
  else mountReview(doc);
}

/* ============================ bước 1: kiểm tra ============================ */

function showScreen(id) {
  ["start", "review", "reader", "slides", "survey", "library"].forEach((s) =>
    $("#" + s).classList.toggle("hidden", s !== id));
  syncRail(id);
  // Về màn nhập là làm mới thống kê và danh sách. Trước đây chúng chỉ nạp một
  // lần lúc khởi động, nên nạp xong một bài rồi bấm ← vẫn thấy số cũ — và chỉ
  // đúng sau khi tải lại cả trang.
  if (id === "start") { loadDbStats(); loadRecent(); }
}

/* Nút Back của trình duyệt phải điều hướng thật.

   App lưu mã bài ở `location.hash` (6 chỗ ghi vào đó) nhưng **không ai lắng
   nghe `hashchange`** — nên bấm Back thì URL về "/" mà màn hình vẫn là bài
   đang đọc. Người dùng mất đường về mà không hiểu vì sao.

   Chỉ phản ứng khi hash THẬT SỰ khác bài đang mở: mọi chỗ trong app đều ghi
   `location.hash` khi mở bài, và nếu không so thì chính cú ghi đó lại kích hoạt
   một lượt mở bài nữa. */
/** Đọc `location.hash` theo MỘT luật cho cả app.

    Ba dạng đang sống chung: `#survey` (kho survey), `#doc=<mã>` (survey.js ghi
    khi quay về bài đang đọc) và `#<mã>` (app.js ghi khi mở bài). Bản đầu của
    bộ nghe `hashchange` coi MỌI hash khác rỗng là mã bài, nên bấm "Tìm hiểu"
    thì `svOpen` ghi `#survey`, bộ nghe gọi `openDoc("survey")`, nhận 404, rồi
    đá về màn nhập — công cụ thứ hai của app mất hẳn lối vào mà không lỗi nào. */
function docHash() {
  const h = decodeURIComponent(location.hash.slice(1));
  if (!h) return { kind: "start" };
  if (h === "survey" || h.startsWith("survey=")) return { kind: "survey" };
  if (h === "thu-vien") return { kind: "library" };
  return { kind: "doc", id: h.startsWith("doc=") ? h.slice(4) : h };
}

function wireHashNav() {
  addEventListener("hashchange", () => {
    const h = docHash();
    // Kho survey tự lo màn của nó (`svOpen`); ở đây chỉ mở lại khi nó đang ẩn
    // — tức lúc người dùng bấm Back/Forward về đúng hash ấy.
    if (h.kind === "survey") {
      if ($("#survey")?.classList.contains("hidden") && typeof svOpen === "function") svOpen();
      return;
    }
    if (h.kind === "library") {
      if ($("#library")?.classList.contains("hidden") && typeof tvOpen === "function") tvOpen();
      return;
    }
    const id = h.id;
    if (h.kind === "start") {
      // Về màn nhập, và đóng mọi popup đang mở — trước đây hộp ghi chú tự bật
      // lên che nội dung sau khi điều hướng.
      closeHlPop?.();
      $("#hlBar")?.classList.add("hidden");
      showScreen("start");
      return;
    }
    /* Guard phải hỏi "đang HIỂN THỊ bài đó không", không phải "đã nạp chưa".
       Hỏi sai thì Back về màn nhập xong bấm Forward là kẹt: `state.doc` vẫn là
       bài cũ nên nó return sớm và màn hình không bao giờ quay lại trang đọc.

       Và màn đúng của một bài phụ thuộc `prepared`: bài vừa nạp xong còn ở bước
       soát. Bản đầu của guard này bật thẳng sang `#reader`, nên nạp một bài mới
       là `doImport` gọi `mountReview`, rồi `location.hash = doc.id` kích
       `hashchange`, rồi guard đá ngược về `#reader` — bước soát bị nhảy cóc mà
       không ai bấm gì. Đo trên bài vừa nạp: `prepared: false` mà màn `#reader`
       đang hiện. */
    const man = state.doc?.prepared ? "reader" : "review";
    const dangMo = state.doc && id === state.doc.id;
    if (dangMo && !$(`#${man}`).classList.contains("hidden")) return;
    // Đã nạp rồi thì chỉ cần hiện lại, khỏi gọi lại API.
    if (dangMo) { showScreen(man); return; }
    openDoc(id).catch(() => { location.hash = ""; });
  });
}

/* Thanh bên trái phải luôn chỉ đúng công cụ đang mở, kể cả khi màn hình đổi từ
   chỗ khác (mở bài từ #doc= trên thanh địa chỉ, bấm nút quay lại…). Nên đồng bộ
   ở ĐÂY chứ không ở chỗ bấm nút — chỗ bấm nút chỉ là một trong nhiều đường vào. */
function syncRail(id) {
  const tool = id === "survey" || id === "library" ? id : "doc";
  $$(".rail-item").forEach((b) => b.classList.toggle("is-on", b.dataset.tool === tool));
}

function mountReview(doc) {
  state.doc = doc;
  apCotTheoNguon(doc);
  const lang = $("#revLang");
  lang.classList.toggle("hidden", !state.nguonViet);
  lang.textContent = state.nguonViet
    ? "Bài này đã là tiếng Việt nên sẽ KHÔNG dịch — bước sau chỉ sinh cột Giải "
      + "thích (nói lại bằng lời thường, vai trò của từng đoạn trong lập luận). "
      + "Báo giá dưới đây đã tính theo đó."
    : "";
  // Thẻ "Căn chỉnh" chữa lỗi RIÊNG của chữ bóc từ PDF (khoảng trắng dính, gạch
  // nối đứt, công thức đảo mảnh). Văn bản dán không có mấy lỗi đó — hiện nút
  // tốn tiền ở đây là mời người dùng trả tiền cho việc không cần (#21).
  $(".tidy-card").classList.toggle("hidden", !doc.has_pdf);
  $("#tidyMsg").classList.add("hidden");
  showScreen("review");
  $("#revTitle").textContent = doc.title || doc.source || "";
  fillModels($("#revModel"), doc.model, { short: true });
  renderReview();
  loadEstimate();
}

/* Chế độ cột đang chọn — quyết định phần nào SINH RA, tức phần nào trả tiền.
   Dùng chung cho cả ước tính lẫn lượt dịch, để hai chỗ không lệch nhau. */
function colMode() {
  const vi = $("#colVi")?.checked, gl = $("#colGl")?.checked;
  if (vi && gl) return "both";
  if (gl) return "plain";
  return "vi";
}

async function loadEstimate() {
  const box = $("#revStats");
  box.innerHTML = `<div class="stat"><b>…</b><span>đang ước lượng</span></div>`;
  try {
    // Truyền `mode` theo hai ô tick cột: bật cả hai thì sinh gần gấp đôi chữ,
    // nên ước tính phải theo đúng cái người dùng đang chọn.
    const e = await fetch(`/api/doc/${state.doc.id}/estimate?mode=${colMode()}`)
      .then((r) => r.json());
    const money = e.cost_usd == null
      ? `<div class="stat"><b>—</b><span>không lấy được giá của model</span></div>`
      // DẢI chứ không một con số: ước tính token có sai số thật, và một con số
      // duy nhất tạo ảo giác chính xác mà nó không có. Kèm luôn phạm vi — con số
      // này chỉ tính lượt dịch, không gồm giải thích từng đoạn, slide, hỏi đáp.
      : `<div class="stat warn-stat"><b>$${(e.cost_low ?? e.cost_usd).toFixed(3)}–${(e.cost_high ?? e.cost_usd).toFixed(3)}</b>`
        + `<span>ước tính · ${esc(e.covers || "lượt dịch")}</span></div>`;
    box.innerHTML =
      // Nói rõ phần còn lại đi đâu. Ba con số không giải thích (tổng · sẽ dịch
      // · hiển thị) là thứ đã làm người dùng tưởng mất khối.
      `<div class="stat" title="${esc(Object.entries(e.blocks_by_role || {})
          .map(([k, v]) => `${v} ${k}`).join(" · "))}">`
        + `<b>${e.blocks_to_translate}</b><span>khối sẽ dịch / ${e.blocks_total} khối`
        + `${e.blocks_by_role ? " ⓘ" : ""}</span></div>` +
      `<div class="stat"><b>${e.figures}</b><span>hình &amp; bảng</span></div>` +
      `<div class="stat"><b>${(e.source_chars / 1000).toFixed(1)}k</b><span>ký tự gốc · ${e.chunks} mẻ dịch</span></div>` +
      // Mất chữ IM LẶNG là kiểu hỏng tệ nhất của bước bóc tách: một bảng không
      // viền từng biến mất hoàn toàn mà Bước 1 vẫn báo xong kèm giá dịch. Chỉ
      // kêu khi đáng kể — vài chục ký tự lệch là chuyện thường của bóc PDF.
      (() => {
        // Ngưỡng theo TỈ LỆ, không theo con số tuyệt đối: 7k ký tự trên bài 70k
        // là 10% và đáng biết, 7k trên bài 500k thì không. Dưới 5% thì im —
        // bóc PDF lệch vài phần trăm là chuyện thường, và một cảnh báo nổ trên
        // mọi bài thì người dùng thôi đọc nó.
        const t = e.pdf_chars || 0, u = e.uncovered_chars || 0;
        if (!t || u / t < 0.05) return "";
        const pct = Math.round((u / t) * 100);
        const nang = pct >= 15;
        return `<div class="stat${nang ? " warn-stat" : ""}" title="Chữ có trong PDF nhưng không nằm trong khối nào — thường là bảng không viền, chữ trong hình chưa được cắt, hoặc vùng mô hình bố cục bỏ sót. Bấm Bóc lại từ PDF để thử đường khác.">`
          + `<b>${pct}%</b><span>chữ chưa vào khối nào${nang ? " ⚠" : ""}</span></div>`;
      })() +
      money;
  } catch {
    box.innerHTML = `<div class="stat"><b>—</b><span>không ước lượng được</span></div>`;
  }
}

const KIND_LABEL = {
  para: "đoạn văn", heading: "mục", caption: "chú thích hình/bảng",
  equation: "công thức", reference: "tài liệu tham khảo", meta: "thông tin đầu bài",
};

/** Cho thấy bóc ra được những gì — nhìn một cái là biết bóc tách có đúng không. */
function renderKinds(blocks) {
  const c = {};
  for (const b of blocks) c[b.type] = (c[b.type] || 0) + 1;
  const li = blocks.filter((b) => b.marker).length;
  const rows = Object.entries(KIND_LABEL).map(([k, label]) =>
    `<span class="kind ${c[k] ? "" : "zero"}"><b>${c[k] || 0}</b><span>${label}</span></span>`);
  if (li) rows.push(`<span class="kind"><b>${li}</b><span>mục danh sách</span></span>`);
  $("#revKinds").innerHTML = rows.join("");
}

function renderReview() {
  const blocks = state.doc.blocks;
  renderKinds(blocks);

  // caption chưa cắt được hình cũng hiện ra, để người dùng tự cắt tay
  const figs = blocks.filter((b) => b.figure || (b.type === "caption" && b.figure_page >= 0));
  const hasPdf = blocks.some((b) => b.figure_page >= 0);
  const src = state.doc.layout_model
    ? "Khung hình do mô hình bố cục xác định. "
    : "Khung hình suy từ vị trí chú thích (heuristic). ";
  // Câu báo theo ĐÚNG loại nguồn (#21): bài dán chữ mà bị khuyên "PDF có thể
  // là bản scan" thì người dùng đi tìm một vấn đề không tồn tại.
  $("#figHint").textContent = !figs.length
    ? (state.doc.has_pdf
        ? "Không bóc được hình nào — PDF có thể là bản scan, hoặc bài không có hình."
        : "Bài nhập từ văn bản nên không có hình để cắt.")
    : hasPdf
      ? src + "Hình nào cắt sai thì bấm Chỉnh khung để tự kéo lại, hoặc Bỏ hình."
      : "Bài nhập bằng cách dán văn bản nên không chỉnh khung được.";

  $("#revFigs").innerHTML = figs.map((b) => `
    <div class="figcard ${b.figure_manual ? "manual" : ""}" data-fig="${esc(b.id)}">
      ${b.figure
        ? `<img src="/api/doc/${esc(state.doc.id)}/img/${esc(b.figure)}.png?v=${esc((b.figure_rect || []).join("_"))}"`
          + ` alt="" loading="lazy" decoding="async">`
        : `<div class="cap" style="padding:1.4rem;text-align:center">Chưa cắt được hình cho chú thích này</div>`}
      <div class="cap" title="${esc(b.text)}">${esc(catGon(b.text, 130))}</div>
      <div class="act">
        ${b.figure_page >= 0 ? `<button data-crop="${esc(b.id)}">${ico("scissors")} Chỉnh khung</button>` : ""}
        ${b.figure ? `<button data-dropfig="${esc(b.id)}">Bỏ hình</button>` : ""}
      </div>
    </div>`).join("");

  $$("#revFigs [data-dropfig]").forEach((el) => (el.onclick = async () => {
    el.closest(".figcard").classList.add("dropped");
    el.disabled = true;
    await patchBlocks({ drop_figure: [el.dataset.dropfig] });
  }));
  $$("#revFigs [data-crop]").forEach((el) => (el.onclick = () => openCrop(el.dataset.crop)));

  const suspicious = blocks.filter((b) =>
    b.type === "meta" ||
    (b.type === "para" && b.text.split(/\s+/).length <= 6) ||
    (b.type === "caption" && !b.figure));
  $("#revSus").innerHTML = suspicious.length
    ? suspicious.map(blkRow).join("")
    : `<p class="hint">Không có khối nào đáng ngờ.</p>`;

  const all = blocks.filter((b) => b.type !== "reference");
  // Nói rõ con số này KHÁC tổng ở đầu trang và khác ở chỗ nào — ba con số không
  // chú thích thì người dùng tưởng có khối bị mất.
  const nRef = blocks.length - all.length;
  $("#revAllCount").textContent = nRef
    ? `${all.length} (trong ${blocks.length}, bỏ ${nRef} tài liệu tham khảo)`
    : String(all.length);
  $("#revAll").innerHTML = all.map(blkRow).join("");

  $$('.blk input[type="checkbox"]').forEach((cb) => (cb.onchange = async () => {
    const row = cb.closest(".blk");
    row.classList.toggle("off", !cb.checked);
    await patchBlocks(cb.checked ? { keep: [cb.dataset.id] } : { skip: [cb.dataset.id] });
    loadEstimate();
  }));
  wireBlockEdits();
}

/* Không dùng <label> bọc cả hàng nữa: bấm nút Gộp/Tách/Bỏ bên trong label sẽ
   lật luôn ô tick, vì cả hàng đều là nhãn của ô đó. */
function blkRow(b) {
  const on = b.translate ? "checked" : "";
  return `<div class="blk ${b.translate ? "" : "off"}" data-row="${esc(b.id)}">
    <label class="blk-tick" title="Bỏ tick = giữ khối nhưng không dịch">
      <input type="checkbox" data-id="${esc(b.id)}" ${on}>
    </label>
    <span class="tag">${esc(b.type)}</span>
    <span class="txt">${esc(b.text.slice(0, 220))}</span>
    <span class="blk-act">
      <button data-merge="${esc(b.id)}" title="Gộp với khối ngay sau — dùng khi một đoạn bị cắt làm đôi">${ico("merge")}</button>
      <button data-split="${esc(b.id)}" title="Tách khối này làm hai — dùng khi hai đoạn bị dính">${ico("scissors")}</button>
      <button data-dropblk="${esc(b.id)}" title="Bỏ hẳn khối khỏi bài">${ico("trash")}</button>
    </span>
  </div>`;
}

/* ------------------------------- sửa khối: bỏ hẳn, gộp, tách ------------- */

async function editBlocks(url, payload) {
  const r = await fetch(`/api/doc/${state.doc.id}${url}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!r.ok) throw new Error(await apiErr(r, "không sửa được"));
  state.doc = await r.json();
  renderReview();
  loadEstimate();
}

function wireBlockEdits() {
  $$("#review [data-dropblk]").forEach((el) => (el.onclick = async () => {
    const b = state.doc.blocks.find((x) => x.id === el.dataset.dropblk);
    if (!await xacNhan("Bỏ hẳn khối này khỏi bài?",
      (b?.text || "").slice(0, 300), { ok: "Bỏ khối", hong: true })) return;
    await patchBlocks({ drop: [el.dataset.dropblk] });
    renderReview();
    loadEstimate();
  }));

  $$("#review [data-merge]").forEach((el) => (el.onclick = async () => {
    const id = el.dataset.merge;
    const i = state.doc.blocks.findIndex((x) => x.id === id);
    const nxt = state.doc.blocks[i + 1];
    if (!nxt) return baoTin("Không gộp được", "Đây là khối cuối, không có gì để gộp vào.");
    const warn = nxt.type !== state.doc.blocks[i].type
      ? `\n\nLưu ý: hai khối khác loại (${state.doc.blocks[i].type} + ${nxt.type}).` : "";
    if (!await xacNhan("Gộp khối này với khối ngay sau?",
      `${warn ? warn.trim() + "\n\n" : ""}…${state.doc.blocks[i].text.slice(-90)}`
      + `\n+\n${nxt.text.slice(0, 90)}…`, { ok: "Gộp" })) return;
    try { await editBlocks("/blocks/merge", { ids: [id, nxt.id] }); }
    catch (e) { baoTin("Không gộp được", e.message); }
  }));

  // Khối đáng ngờ nằm ở cả "Khối đáng ngờ" lẫn "Xem toàn bộ" nên data-row trùng
  // nhau — phải bám vào đúng hàng vừa bấm, không tra lại bằng selector.
  $$("#review [data-split]").forEach((el) =>
    (el.onclick = () => openSplit(el.dataset.split, el.closest(".blk"))));
}

/** Mở ô soạn để chọn chỗ cắt. Dùng vị trí con trỏ trong textarea làm điểm tách. */
function openSplit(id, row) {
  const b = state.doc.blocks.find((x) => x.id === id);
  if (!b || !row || $(".splitbox", row)) return;
  row.insertAdjacentHTML("beforeend", `
    <div class="splitbox">
      <p class="hint">Đặt con trỏ vào đúng chỗ muốn cắt (thường là ngay trước chữ đầu của đoạn sau), rồi bấm Tách.</p>
      <textarea class="input" rows="5"></textarea>
      <div class="splitbox-act">
        <button class="btn btn-primary" data-dosplit>Tách ở con trỏ</button>
        <button class="btn" data-cancelsplit>Thôi</button>
      </div>
    </div>`);
  const ta = $("textarea", row);
  ta.value = b.text;              // gán qua value, không nhúng vào HTML
  ta.focus();
  ta.setSelectionRange(0, 0);
  $("[data-cancelsplit]", row).onclick = () => $(".splitbox", row).remove();
  $("[data-dosplit]", row).onclick = async () => {
    const off = ta.selectionStart;
    try { await editBlocks("/blocks/split", { id, offset: off }); }
    catch (e) { baoTin("Không tách được", e.message); }
  };
}

/* ------------------------- cắt hình thủ công ------------------------- */

const crop = { block: null, page: 0, pageW: 0, pageH: 0, scale: 1, auto: null, url: null };

/** Mở hộp chỉnh khung cho một block caption. */
async function openCrop(blockId) {
  const b = state.doc.blocks.find((x) => x.id === blockId);
  if (!b) return;
  crop.block = b;
  crop.auto = b.figure_rect ? [...b.figure_rect] : null;
  crop.page = b.figure_page >= 0 ? b.figure_page : b.page;

  $("#cropCap").textContent = b.text.slice(0, 110);
  $("#cropErr").textContent = "";
  $("#cropModal").classList.remove("hidden");

  const pages = new Set(state.doc.blocks.map((x) => x.page));
  const maxPage = Math.max(...pages, crop.page);
  $("#cropPage").innerHTML = Array.from({ length: maxPage + 1 }, (_, i) =>
    `<option value="${i}"${i === crop.page ? " selected" : ""}>${i + 1}</option>`).join("");

  await loadCropPage(crop.page);
}

async function loadCropPage(pno) {
  const img = $("#cropImg");
  $("#cropErr").textContent = "";
  try {
    const r = await fetch(`/api/doc/${state.doc.id}/page/${pno}.png?dpi=110`);
    if (!r.ok) throw new Error(await apiErr(r, "không tải được trang"));
    crop.pageW = parseFloat(r.headers.get("X-Page-Width")) || 612;
    crop.pageH = parseFloat(r.headers.get("X-Page-Height")) || 792;
    if (crop.url) URL.revokeObjectURL(crop.url);
    crop.url = URL.createObjectURL(await r.blob());
    await new Promise((res, rej) => {
      img.onload = res; img.onerror = rej; img.src = crop.url;
    });
  } catch (e) {
    $("#cropErr").textContent = e.message;
    return;
  }
  crop.page = pno;
  crop.scale = img.clientWidth / crop.pageW;   // px trên màn hình / point của PDF

  // khung ban đầu: khung hiện có nếu cùng trang, không thì lấy giữa trang
  const b = crop.block;
  const same = b.figure_rect && b.figure_page === pno;
  const rect = same ? b.figure_rect
    : [crop.pageW * 0.12, crop.pageH * 0.3, crop.pageW * 0.88, crop.pageH * 0.6];
  setCropBox(rect);
}

/** rect tính bằng point của PDF -> vị trí khung trên màn hình */
function setCropBox([x0, y0, x1, y1]) {
  const img = $("#cropImg"), box = $("#cropBox"), stage = $("#cropStage");
  const s = crop.scale;
  const ox = img.offsetLeft, oy = img.offsetTop;
  box.style.left = ox + x0 * s + "px";
  box.style.top = oy + y0 * s + "px";
  box.style.width = Math.max((x1 - x0) * s, 12) + "px";
  box.style.height = Math.max((y1 - y0) * s, 12) + "px";
  void stage;
}

/** vị trí khung trên màn hình -> rect tính bằng point của PDF */
function getCropRect() {
  const img = $("#cropImg"), box = $("#cropBox");
  const s = crop.scale;
  const x0 = (box.offsetLeft - img.offsetLeft) / s;
  const y0 = (box.offsetTop - img.offsetTop) / s;
  return [
    Math.max(0, x0), Math.max(0, y0),
    Math.min(crop.pageW, x0 + box.offsetWidth / s),
    Math.min(crop.pageH, y0 + box.offsetHeight / s),
  ];
}

function wireCrop() {
  const box = $("#cropBox"), img = $("#cropImg");

  box.addEventListener("pointerdown", (e) => {
    const handle = e.target.dataset?.h || null;   // null = kéo cả khung
    e.preventDefault();
    box.setPointerCapture(e.pointerId);
    const sx = e.clientX, sy = e.clientY;
    const L = box.offsetLeft, T = box.offsetTop, W = box.offsetWidth, H = box.offsetHeight;
    const minX = img.offsetLeft, minY = img.offsetTop;
    const maxX = minX + img.clientWidth, maxY = minY + img.clientHeight;

    const move = (ev) => {
      const dx = ev.clientX - sx, dy = ev.clientY - sy;
      let l = L, t = T, w = W, h = H;
      if (!handle) {
        l = Math.min(Math.max(L + dx, minX), maxX - W);
        t = Math.min(Math.max(T + dy, minY), maxY - H);
      } else {
        if (handle.includes("w")) { l = Math.max(L + dx, minX); w = W - (l - L); }
        if (handle.includes("e")) { w = Math.min(W + dx, maxX - L); }
        if (handle.includes("n")) { t = Math.max(T + dy, minY); h = H - (t - T); }
        if (handle.includes("s")) { h = Math.min(H + dy, maxY - T); }
        if (w < 14) { w = 14; l = L; }
        if (h < 14) { h = 14; t = T; }
      }
      box.style.left = l + "px"; box.style.top = t + "px";
      box.style.width = w + "px"; box.style.height = h + "px";
    };
    const up = () => {
      box.removeEventListener("pointermove", move);
      box.removeEventListener("pointerup", up);
    };
    box.addEventListener("pointermove", move);
    box.addEventListener("pointerup", up);
  });

  $("#cropPage").onchange = (e) => loadCropPage(parseInt(e.target.value, 10));
  $("#cropReset").onclick = () => {
    if (crop.auto && crop.block.figure_page === crop.page) setCropBox(crop.auto);
    else $("#cropErr").textContent = "Khung tự động nằm ở trang khác.";
  };
  $("#cropClose").onclick = closeCrop;
  $("#cropModal").addEventListener("click", (e) => {
    if (e.target.id === "cropModal") closeCrop();
  });
  addEventListener("keydown", (e) => {
    if (e.key === "Escape" && !$("#cropModal").classList.contains("hidden")) closeCrop();
  });

  $("#cropSave").onclick = async () => {
    const btn = $("#cropSave");
    btn.disabled = true; btn.textContent = "Đang cắt…";
    try {
      const r = await fetch(`/api/doc/${state.doc.id}/crop/${crop.block.id}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ page: crop.page, rect: getCropRect() }),
      });
      if (!r.ok) throw new Error(await apiErr(r, "lỗi"));
      const { block } = await r.json();
      Object.assign(crop.block, block);
      closeCrop();
      // Vẽ lại đúng màn đang mở. Gọi cứng `renderReview()` thì sửa khung ở màn
      // đọc xong ảnh vẫn là ảnh cũ cho tới khi tải lại trang.
      if (!$("#reader").classList.contains("hidden")) renderDoc();
      else renderReview();
    } catch (e) {
      $("#cropErr").textContent = e.message;
    } finally {
      btn.disabled = false; btn.textContent = "Lưu khung";
    }
  };
}

function closeCrop() {
  $("#cropModal").classList.add("hidden");
  if (crop.url) { URL.revokeObjectURL(crop.url); crop.url = null; }
}

async function patchBlocks(payload) {
  const r = await fetch(`/api/doc/${state.doc.id}/blocks`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (r.ok) state.doc = await r.json();
}

/* ================================================= màn hình đọc ===== */

function mountDoc(doc) {
  state.doc = doc;
  state.chunks = doc.chunks || 0;
  // Sau `state.chunks`: `applyCols` đặt lại nhãn nút Dịch, mà nhãn đó tính
  // theo số mẻ của bài ĐANG mở.
  apCotTheoNguon(doc);
  applyCols();
  state.history = [];
  /* Mọi thứ thuộc về BÀI TRƯỚC phải dọn ngay ở đây, vì màn `#reader` không bị
     dựng lại — nó chỉ được nạp nội dung khác.

     `state.pick` là chỗ tệ nhất: nó giữ mã khối của bài trước, nên `pickedIds()`
     trả về một tập mã KHÔNG TỒN TẠI trong bài mới, và `runTranslate` bỏ qua
     sạch mọi mẻ. Bấm Dịch không ra gì và không có lỗi nào.

     `#statusLine` thì nhìn thấy được mà vẫn dễ bỏ qua: dòng cuối cùng của bài
     trước hay là dòng báo giá, nên mở bài mới ra là thấy ngay "…$0,0266" của
     một bài khác — người dùng có lý do tưởng vừa bị tính tiền. */
  state.pick = null;
  state.sections = null;
  state.showHidden = false;
  state.stopping = false;
  $("#statusLine").classList.add("hidden");
  $("#statusLine").textContent = "";
  $("#progress").classList.add("hidden");
  $("#findBar").classList.add("hidden");
  $("#hiddenBar").classList.add("hidden");
  $("#pickMenu").classList.add("hidden");
  $("#chat").classList.add("hidden");
  dongHoTat();
  // Khung chat phải dọn theo. `state.history` rỗng nên MODEL không nhầm, nhưng
  // DOM vẫn đầy hỏi-đáp về bài trước — màn hình và model bất đồng, kiểu tệ nhất:
  // người đọc thấy văn bản đúng ngữ pháp nói về bài khác mà không dấu hiệu gì.
  const cl = $("#chatLog"); if (cl) cl.innerHTML = "";
  const ci = $("#chatInput"); if (ci) ci.value = "";
  state.session = 0;
  showScreen("reader");
  const tenVi = doc.brief?.title_vi || doc.title || "(không tiêu đề)";
  const tenEn = doc.title && doc.brief?.title_vi ? doc.title : doc.source || "";
  $("#docTitleVi").textContent = tenVi;
  $("#docTitleEn").textContent = tenEn;
  // Tiêu đề bài báo thường dài hơn chỗ có trên thanh, nên nó bị cắt bằng dấu ba
  // chấm. Không có `title` thì phần bị cắt không có đường nào đọc được — mà đó
  // hay là phần phân biệt bài này với bài kia ("… for Multi-hop QA").
  $(".topbar-title").title = tenEn ? `${tenVi}\n${tenEn}` : tenVi;
  // Nhãn ĐẦY ĐỦ: từ khi ô chọn nằm trong panel tuỳ chọn thay vì trên thanh, nó
  // rộng cả panel, nên nhãn rút gọn chỉ còn làm mất phần giá và độ dài ngữ cảnh
  // — vốn là hai thứ duy nhất để chọn giữa hai model.
  fillModels($("#docModel"), doc.model);
  // bài dán bằng văn bản thì không có PDF gốc để đối chiếu
  $("#pdfBtn").classList.toggle("hidden", !doc.has_pdf);
  $("#pdfPane").classList.add("hidden");
  closeFigPeek();
  Object.assign(pdfv, { page: -1, pages: 0 });
  buildFigIndex();          // phải dựng trước renderDoc, vì sci() tra bảng này
  buildCiteIndex();
  renderDoc();
  renderSide();
  renderUsage();
  const done = Object.keys(doc.translations || {}).length;
  capNhatNutDich();
  state.slideSel = null;
  syncSlidesBtn();
  restorePos();
}

function wireReader() {
  const home = () => { showScreen("start"); location.hash = ""; loadRecent(); };
  $("#backBtn").onclick = home;
  $("#revBack").onclick = home;
  // Căn chỉnh bằng model — chỗ duy nhất ở bước 1 tốn tiền, nên nói rõ ra
  $("#tidyBtn").onclick = async () => {
    const btn = $("#tidyBtn"), msg = $("#tidyMsg");
    btn.disabled = true;
    btn.textContent = "Đang căn chỉnh…";
    msg.classList.remove("hidden");
    msg.textContent = "Đang nhờ model dọn lại chữ bóc từ PDF…";
    try {
      const r = await fetch(`/api/doc/${state.doc.id}/relayout`, { method: "POST" });
      if (!r.ok) throw new Error(await apiErr(r, "không căn chỉnh được"));
      const { stats, run, doc } = await r.json();
      state.doc = doc;
      renderReview();
      loadEstimate();
      msg.innerHTML =
        `Đã soát ${stats.checked} khối, sửa <b>${stats.changed}</b>` +
        (stats.rejected
          ? `, <b>chặn ${stats.rejected}</b> đề xuất làm sai lệch nội dung (giữ bản gốc)`
          : "") +
        ` · ${esc(stats.model || "")} · ${money(run?.cost)}`;
    } catch (e) {
      msg.textContent = "Lỗi: " + e.message;
    } finally {
      btn.disabled = false;
      btn.textContent = "✨ Căn chỉnh";
    }
  };

  $("#revGo").onclick = async () => {
    await fetch(`/api/doc/${state.doc.id}/confirm`, { method: "POST" });
    const doc = await fetch(`/api/doc/${state.doc.id}`).then((r) => r.json());
    mountDoc(doc);
  };
  $("#translateBtn").onclick = () => (state.translating ? requestStop() : runTranslate());

  // đổi model ở bước 1 -> phải tính lại giá, vì giá là của model cụ thể
  $("#revModel").onchange = async (e) => {
    const prev = state.doc.model;
    try {
      await setModel(e.target.value);
      loadEstimate();
    } catch (err) {
      e.target.value = prev;
      $("#revStats").innerHTML = `<div class="stat"><b>—</b><span>${esc(err.message)}</span></div>`;
    }
  };
  $("#docModel").onchange = async (e) => {
    const prev = state.doc.model;
    try {
      await setModel(e.target.value);
      status(`Từ giờ dịch bằng ${e.target.selectedOptions[0].textContent}.` +
             " Phần đã dịch giữ nguyên, chỉ phần chưa dịch mới dùng model mới.");
    } catch (err) {
      e.target.value = prev;
      status("Lỗi: " + err.message);
    }
  };

  ["#colEn", "#colVi", "#colGl"].forEach((id) => ($(id).onchange = applyCols));
  applyCols();

  // Dưới 1000px sidebar bị đẩy hẳn ra ngoài màn hình — không có nút này thì
  // tóm lược, mạch lập luận, thuật ngữ và mục lục không còn đường nào mở ra.
  $("#sideToggle").onclick = () => toggleSide(!$("#side").classList.contains("open"));
  $("#sideClose").onclick = () => toggleSide(false);
  // màn rộng mặc định mở, màn hẹp mặc định đóng; sau đó theo lựa chọn đã lưu
  toggleSide(pref("side", narrow() ? "0" : "1") === "1");
  // Bề rộng vùng đọc đổi vì cột trái, khung PDF, hay cửa sổ — đo một chỗ là đủ
  // cả ba, thay vì nhớ gọi lại ở từng nút.
  new ResizeObserver(() => capNhatCotHep()).observe($("#doc"));

  $("#figPeekClose").onclick = closeFigPeek;
  addEventListener("keydown", (e) => { if (e.key === "Escape") closeFigPeek(); });

  $$(".side-tab").forEach((t) => (t.onclick = () => {
    $$(".side-tab").forEach((x) => x.classList.toggle("is-on", x === t));
    $$(".side-pane").forEach((p) => p.classList.toggle("hidden", p.dataset.pane !== t.dataset.side));
  }));

  $("#termSearch").oninput = (e) => {
    const q = e.target.value.toLowerCase();
    $$("#termsBox .term").forEach((el) =>
      el.classList.toggle("hidden", q && !el.textContent.toLowerCase().includes(q)));
  };

  $("#exportBtn").onclick = (e) => { e.stopPropagation(); $("#exportMenu").classList.toggle("hidden"); };
  document.addEventListener("click", () => $("#exportMenu").classList.add("hidden"));
  $$("#exportMenu a").forEach((a) => (a.onclick = (e) => {
    e.preventDefault();
    const url = `/api/doc/${state.doc.id}/export?mode=${a.dataset.mode}&fmt=${a.dataset.fmt}`;
    window.open(url, "_blank");
    if (a.dataset.fmt === "pdf") {
      status("Trang in đã mở ở tab mới — chọn “Lưu thành PDF” trong hộp in." +
             " Sơ đồ cần vài giây để vẽ xong trước khi hộp in hiện ra.");
    }
  }));

  // Bảng thuật ngữ bị đóng băng trong brief. Sửa luật dịch xong mà không dựng
  // lại brief thì bài đang đọc vẫn dùng bảng cũ.
  /* Bóc lại từ PDF. Miễn phí, và phần đã dịch giữ nguyên — nên nút này không
     cần cảnh báo giá, chỉ cần nói rõ nó sẽ đổi gì. */
  $("#reparseBtn").onclick = async () => {
    const btn = $("#reparseBtn");
    if (!await xacNhan("Bóc lại bài từ file PDF gốc bằng bộ bóc mới nhất?",
      "Miễn phí, không gọi model. Bản dịch, ghi chú và vệt bôi vàng giữ nguyên "
      + "— khối được ghép lại theo nội dung.\n\nPhần chữ mới nhặt về sẽ chưa có bản "
      + "dịch; bấm Dịch tiếp là xong, và đoạn nào từng dịch rồi thì lấy lại miễn phí.",
      { ok: "Bóc lại" })) return;
    btn.disabled = true;
    const old = btn.textContent;
    btn.textContent = "Đang bóc lại…";
    try {
      const r = await fetch(`/api/doc/${state.doc.id}/reparse`, { method: "POST" });
      if (!r.ok) throw new Error(await apiErr(r, "không bóc lại được"));
      const res = await r.json();
      mountDoc(res.doc);
      const st = res.stats;
      status(`Bóc lại xong: ${st.blocks} khối · giữ ${st.kept} bản dịch cũ · `
        + `${st.new} khối mới` + (st.to_translate ? ` · ${st.to_translate} khối chờ dịch` : "")
        + (st.dropped ? ` · bỏ ${st.dropped} khối không còn` : "")
        + (st.title_fixed ? ` · đã vá tiêu đề bị cụt` : ""));
      // Rơi về đường lùi phải NÓI RA. Trước đây nó im lặng, nên bạn bấm Bóc lại
      // thấy "xong" mà kết quả kém hẳn — công thức mất ảnh, hiện ra bằng chữ
      // toán vỡ — và không có cách nào đoán ra vì sao.
      if (st.layout_used === false) {
        await baoTin("Bóc lại bằng ĐƯỜNG LÙI, không dùng mô hình bố cục",
          "Lý do: " + (st.fallback_why || "không rõ") + ".\n\n"
          + "Kết quả kém hơn rõ rệt: công thức không được cắt thành ảnh nên hiện "
          + "ra bằng chữ toán vỡ nát, và nhiều đoạn bị cắt vụn hơn.\n\n"
          + "Cách chữa: chạy bằng ./run.sh trên máy (đã có sẵn mô hình), hoặc dựng "
          + "lại ảnh Docker với WITH_LAYOUT=1 và LAYOUT_BACKEND=docling.");
      }
    } catch (e) {
      status("Lỗi: " + e.message);
    } finally {
      btn.disabled = false;
      btn.textContent = old;
    }
  };

  /* Cắt lại ảnh. Khác `reparse` ở chỗ nó KHÔNG dựng lại danh sách khối, nên
     không mang theo rủi ro rơi về đường lùi heuristic. Miễn phí, và không mất
     gì — nên chỉ cần một câu xác nhận nhẹ. */
  $("#recropBtn").onclick = async () => {
    const btn = $("#recropBtn");
    if (!await xacNhan("Vẽ lại mọi ảnh đã cắt, từ file PDF gốc?",
      "Miễn phí, không gọi model. Khối, bản dịch, ghi chú và vệt bôi không bị "
      + "chạm tới — chỉ pixel của ảnh được vẽ lại, theo đúng khung đã lưu.",
      { ok: "Cắt lại" })) return;
    btn.disabled = true;
    const old = btn.textContent;
    btn.textContent = "Đang cắt lại…";
    try {
      const r = await fetch(`/api/doc/${state.doc.id}/recrop`, { method: "POST" });
      if (!r.ok) throw new Error(await apiErr(r, "không cắt lại được"));
      const res = await r.json();
      mountDoc(res.doc);
      const st = res.stats;
      // Ảnh cũ còn trong cache của trình duyệt dưới đúng URL cũ, nên phải đổi
      // URL mới thấy bản mới — cùng cái bẫy đã ghi cho CSS/JS ở `_asset_tag`.
      const v = Date.now();
      $$("img[src*='/img/']").forEach((im) => {
        im.src = im.src.split("?")[0] + "?v=" + v;
      });
      status(`Cắt lại xong: ${st.images} ảnh · bề ngang ${st.px_before} → ${st.px_after}px`
        + (st.sharper ? ` · ${st.sharper} ảnh nét hơn` : "")
        + (st.restored ? ` · ${st.restored} ảnh trước đây bị mất file` : "")
        + (st.failed.length ? ` · ${st.failed.length} khung không cắt được` : ""));
    } catch (e) {
      status("Lỗi: " + e.message);
    } finally {
      btn.disabled = false;
      btn.textContent = old;
    }
  };

  $("#insightBtn").onclick = markInsights;
  const lv = $("#insightLevel");
  if (lv) lv.value = pref("insightlevel", "vua");
  const au = $("#insightAuto");
  if (au) {
    au.checked = pref("insightauto", "1") === "1";
    au.onchange = () => setPref("insightauto", au.checked ? "1" : "0");
  }

  $("#rebriefBtn").onclick = async () => {
    const btn = $("#rebriefBtn");
    if (!await xacNhan("Đọc lại toàn bài để chốt lại bảng thuật ngữ?",
      "TỐN TIỀN: một lượt gọi model đọc cả bài.\n\nPhần đã dịch giữ nguyên — muốn "
      + "dịch lại theo bảng mới thì bấm Dịch tiếp sau khi xoá bộ nhớ dịch.",
      { ok: "Dựng lại" })) return;
    btn.disabled = true;
    const old = btn.textContent;
    btn.textContent = "Đang đọc toàn bài…";
    try {
      const r = await fetch(`/api/doc/${state.doc.id}/brief`,
                           { method: "POST", signal: state.bo?.signal });
      if (!r.ok) throw new Error(await apiErr(r, "không dựng được"));
      const res = await r.json();
      state.doc.brief = res.brief;
      $("#docTitleVi").textContent = res.brief.title_vi || state.doc.title;
      renderSide();
      reportCost("Đã chốt lại bảng thuật ngữ", res.run, res.total);
    } catch (e) {
      status("Lỗi: " + e.message);
    } finally {
      btn.disabled = false;
      btn.textContent = old;
    }
  };

  $("#hiddenToggle").onclick = () => {
    state.showHidden = !state.showHidden;
    renderDoc();
  };

  $("#pickBtn").onclick = (e) => {
    e.stopPropagation();
    const pop = $("#pickMenu");
    pop.classList.toggle("hidden");
    if (!pop.classList.contains("hidden")) loadSections();
  };
  $("#pickMenu").onclick = (e) => e.stopPropagation();
  document.addEventListener("click", () => $("#pickMenu").classList.add("hidden"));
  $("#pickAll").onclick = () => { setPickAll(true); };
  $("#pickNone").onclick = () => { setPickAll(false); };

  $("#askBtn").onclick = () => $("#chat").classList.toggle("hidden");
  $("#chatClose").onclick = () => $("#chat").classList.add("hidden");
  $("#chatForm").onsubmit = (e) => { e.preventDefault(); sendQuestion(); };
  $("#chatInput").addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); sendQuestion(); }
  });
}

/* ========================= bước 3: bộ slide trình bày ==================== */

/* Slide dựng từ bản dịch đã soát, nên phải dịch xong mới vào được — dựng từ bản
   dịch dở thì model tự viết lấy phần còn thiếu, mà đó đúng là thứ công cụ này
   sinh ra để tránh. */
/** Còn bao nhiêu khối cần dịch mà chưa dịch. 0 = xong cả bài. */
/** Bài này vốn đã là tiếng Việt?

    Bên test nạp một PDF tiếng Việt và app vẫn cho dịch VI→VI rồi tính tiền
    (#13). Đo bằng tỉ lệ chữ cái MANG DẤU TIẾNG VIỆT trong các đoạn văn: văn
    xuôi tiếng Việt khoảng 20–30%, bài tiếng Anh gần 0 (dấu chỉ xuất hiện ở tên
    riêng lác đác). Ngưỡng 8% là rộng tay về cả hai phía; dưới 200 chữ cái thì
    không đủ căn cứ, coi như không phải. */
function laBaiTiengViet(doc) {
  const chu = (doc?.blocks || []).filter((b) => b.type === "para").map((b) => b.text).join(" ");
  const cai = chu.match(/\p{L}/gu)?.length || 0;
  if (cai < 200) return false;
  const viet = chu.match(/[àáảãạăằắẳẵặâầấẩẫậđèéẻẽẹêềếểễệìíỉĩịòóỏõọôồốổỗộơờớởỡợùúủũụưừứửữựỳýỷỹỵ]/giu)?.length || 0;
  return viet / cai > 0.08;
}

/** Bài tiếng Việt thì KHÔNG sinh cột dịch — chỉ cột Giải thích. Đặt ô cột
    theo từng bài (ô cột không lưu vào localStorage, nên đổi ở đây không lan
    sang bài khác), và mở bài tiếng Anh thì trả cột Việt về như cũ. */
function apCotTheoNguon(doc) {
  state.nguonViet = laBaiTiengViet(doc);
  $("#colVi").checked = !state.nguonViet;
  $("#colVi").closest("label").title = state.nguonViet
    ? "Bài này đã là tiếng Việt nên không dịch — chỉ sinh cột Giải thích."
    : "Bản dịch tiếng Việt.";
}

function untranslatedCount() {
  const d = state.doc;
  if (!d?.blocks?.length) return 0;
  if (state.nguonViet) return 0;     // bài tiếng Việt: không có gì để dịch
  return d.blocks.filter((b) => b.translate && !b.hidden && !d.translations?.[b.id]).length;
}
/* Vệt bôi neo theo khoảng ký tự trong **văn bản hiển thị** của một ô (`.en`,
   `.vi`, `.gl`), không phải trong chuỗi HTML: `sci()` chèn `<sup>`, `<sub>` và
   thẻ `<a>` cho tham chiếu hình, nên mọi vị trí tính trên HTML đều lệch so với
   chỗ người đọc thật sự bôi. Bọc lại bằng DOM Range vì lý do tương tự — cắt
   chuỗi HTML sẽ phá các thẻ đó. */

const HL_COLS = { en: "en", vi: "vi", gl: "gl" };

/** Vị trí ký tự của (node, offset) tính trong textContent của `root`. */
function offsetIn(root, node, off) {
  const w = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
  let n = 0, t;
  while ((t = w.nextNode())) {
    if (t === node) return n + off;
    n += t.nodeValue.length;
  }
  return n;
}

/** Bọc [start,end) trong `root` bằng một thẻ, cắt text node ở hai mép. */
function wrapRange(root, start, end, make) {
  const w = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
  const parts = [];
  let n = 0, t;
  while ((t = w.nextNode())) {
    const a = n, b = n + t.nodeValue.length;
    n = b;
    if (b <= start || a >= end) continue;
    parts.push([t, Math.max(0, start - a), Math.min(t.nodeValue.length, end - a)]);
  }
  for (const [node, s, e] of parts) {
    let target = node;
    if (e < target.nodeValue.length) target.splitText(e);
    if (s > 0) target = target.splitText(s);
    const el = make();
    target.parentNode.insertBefore(el, target);
    el.appendChild(target);
  }
  return parts.length > 0;
}

/** Vẽ lại mọi vệt bôi của một cặp hàng. */
function paintHighlights(pairEl) {
  const bid = pairEl.dataset.id;
  const list = (state.doc.highlights || {})[bid] || [];
  if (!list.length) return;
  for (const col of Object.keys(HL_COLS)) {
    const cell = $("." + col, pairEl);
    if (!cell) continue;
    // vẽ từ cuối về đầu để việc cắt text node không làm lệch vệt phía trước
    list.filter((h) => h.col === col)
      .sort((a, b) => b.start - a.start)
      .forEach((h) => wrapRange(cell, h.start, h.end, () => {
        const m = document.createElement("mark");
        m.className = "hl" + (h.note ? " has-note" : "");
        m.dataset.hl = h.id;
        m.dataset.c = h.color || "y";
        return m;
      }));
  }
}

function repaintHighlights(root = document) {
  $$("#doc .pair", root).forEach(paintHighlights);
}

/* ---- bắt vùng chọn ---- */

let hlPending = null;

function onDocSelect() {
  const sel = document.getSelection();
  const bar = $("#hlBar");
  if (!sel || sel.isCollapsed || sel.rangeCount === 0) return bar.classList.add("hidden");
  const r = sel.getRangeAt(0);
  // vùng chọn phải nằm gọn trong MỘT ô của MỘT hàng, không thì không neo được
  const cell = r.startContainer.parentElement?.closest(".en,.vi,.gl");
  if (!cell || !cell.contains(r.endContainer)) return bar.classList.add("hidden");
  const pair = cell.closest(".pair");
  const col = ["en", "vi", "gl"].find((c) => cell.classList.contains(c));
  if (!pair || !col) return bar.classList.add("hidden");

  const start = offsetIn(cell, r.startContainer, r.startOffset);
  const end = offsetIn(cell, r.endContainer, r.endOffset);
  const text = sel.toString().trim();
  if (end <= start || !text) return bar.classList.add("hidden");

  hlPending = { block: pair.dataset.id, col, start, end, text };
  const box = r.getBoundingClientRect();
  bar.style.left = `${box.left + box.width / 2 + scrollX}px`;
  bar.style.top = `${box.top + scrollY - 6}px`;
  bar.classList.remove("hidden");
}

async function makeHighlight(color) {
  if (!hlPending) return;
  hlPending.color = color || pref("hlcolor", "y");
  $("#hlBar").classList.add("hidden");
  try {
    const r = await fetch(`/api/doc/${state.doc.id}/highlights`, {
      method: "PATCH", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ add: hlPending }),
    });
    if (!r.ok) throw new Error(await apiErr(r, "không bôi được"));
    const { highlights, new: item } = await r.json();
    state.doc.highlights = highlights;
    document.getSelection().removeAllRanges();
    renderDoc();
    openHlPop(item.id);
  } catch (e) {
    status("Lỗi: " + e.message);
  } finally { hlPending = null; }
}

/** Đánh dấu tự động những câu đáng nhớ trong bản dịch.

    Model chỉ trả về `quote` — chuỗi **thô như đang lưu**. Việc neo phải làm ở
    đây, vì vệt bôi neo theo khoảng ký tự trong **chữ đã dựng** của một ô, mà
    `sci()` biến `^{N}` thành `<sup>N</sup>`: đo trên một câu thật, 71 ký tự lưu
    còn 54 ký tự hiển thị. Cách dò không cần bản Python của `sci()`: chạy chính
    `sci()` lên `quote` rồi lấy `textContent` — phép biến đổi là cục bộ nên kết
    quả là một chuỗi con của ô đã dựng. */
function insightOffsets(cell, quote) {
  const tmp = document.createElement("div");
  tmp.innerHTML = sci(quote);
  const can = tmp.textContent;
  const start = cell.textContent.indexOf(can);
  return start < 0 ? null : { start, end: start + can.length, text: can };
}

async function markInsights(tuDong = false) {
  const btn = $("#insightBtn");
  // Ước lượng số vệt ngay trong câu hỏi, để người dùng biết mình sắp nhận gì —
  // "bạn có chắc không" mà không kèm con số thì họ không có cơ sở nào để chắc.
  const moi = { thua: 10, vua: 4, day: 2 }[$("#insightLevel")?.value || "vua"];
  const nPara = (state.doc.blocks || []).filter(
    (b) => (b.type === "para" || b.type === "caption")
        && (state.doc.translations || {})[b.id]).length;
  const uoc = Math.max(6, Math.min(90, Math.floor(nPara / moi)));
  // Chạy tự động thì KHÔNG hỏi lại: người dùng đã đồng ý một lần bằng ô tick,
  // hỏi thêm ngay sau khi dịch xong là bắt họ bấm hai lần cho cùng một quyết định.
  if (!tuDong && !await xacNhan("Đánh dấu những câu đáng nhớ trong bản dịch?",
    `Bài này có ${nPara} đoạn đã dịch, mật độ đang chọn cho ra khoảng ${uoc} câu.\n\n`
    + `TỐN TIỀN: một lượt gọi model — đo trên bài 149 đoạn: `
    + `$0,02 ở mức thưa, $0,08 ở mức vừa, khoảng gấp đôi thế ở mức dày.\n\n`
    + `Mỗi câu kèm một dòng nói vì sao chỗ đó đáng nhớ, và màu cho biết loại — `
    + `tím là luận điểm, xanh dương là cơ chế, xanh lá là số liệu, hồng là giới `
    + `hạn, vàng là khái niệm.\n\nVệt bôi bạn tự tô từ trước vẫn giữ nguyên.`,
    { ok: "Đánh dấu" })) return;
  btn.disabled = true;
  const old = btn.textContent;
  btn.textContent = "Đang đọc lại bài…";
  try {
    const muc = $("#insightLevel")?.value || "vua";
    setPref("insightlevel", muc);
    const r = await fetch(
      `/api/doc/${state.doc.id}/insights?level=${encodeURIComponent(muc)}`,
      { method: "POST" });
    if (!r.ok) throw new Error(await apiErr(r, "không đánh dấu được"));
    const res = await r.json();

    // Neo từng câu vào đúng ô của nó. Ô chưa dựng (khối đang bị ẩn, hoặc cột
    // tiếng Việt đang tắt) thì bỏ qua — không có chữ thì không neo được.
    const add = [];
    const hut = [];
    for (const m of res.marks) {
      const cell = $(`#p-${CSS.escape(m.block)} [data-vi]`);
      const pos = cell && insightOffsets(cell, m.quote);
      if (!pos) { hut.push(m.block); continue; }
      add.push({ block: m.block, col: "vi", color: m.color, auto: true,
                 start: pos.start, end: pos.end, text: pos.text,
                 note: `${m.label} — ${m.why}` });
    }
    if (add.length) {
      const w = await fetch(`/api/doc/${state.doc.id}/highlights`, {
        method: "PATCH", headers: { "Content-Type": "application/json" },
        // `replace_auto`: bấm lần hai thì thay chỗ cũ chứ không cộng dồn.
        // Vệt người dùng tự tô không mang cờ `auto` nên không bị đụng.
        body: JSON.stringify({ add_many: add, replace_auto: true }),
      });
      if (!w.ok) throw new Error(await apiErr(w, "không lưu được vệt bôi"));
      state.doc.highlights = (await w.json()).highlights;
      renderDoc();
    }
    reportCost(`Đã đánh dấu ${add.length} câu`, res.run, res.usage);
    // Câu model bịa hoặc chép sai một ký tự thì bị chốt chặn loại — nói ra chứ
    // đừng im lặng, nếu không người dùng trả tiền mà không biết đã mất gì.
    // Nói ĐÚNG lý do: phần lớn câu bị bỏ là do trần "đừng tô quá nửa khối", chứ
    // không phải model chép sai. Gộp hết thành "không khớp bản dịch" là đổ lỗi
    // nhầm chỗ và làm người dùng tưởng bản dịch có vấn đề.
    const bo = (res.skipped || []).length + hut.length;
    if (bo) {
      const dayKhoi = (res.skipped || []).filter((x) => /khối/.test(x)).length;
      const lech = bo - dayKhoi;
      status(`Đã đánh dấu ${add.length} câu · bỏ ${bo}`
        + (dayKhoi ? ` (${dayKhoi} vì khối đó đã đủ vệt)` : "")
        + (lech ? `${dayKhoi ? " và" : " ("}${lech} vì không khớp bản dịch)` : ""));
    }
  } catch (e) {
    status("Lỗi: " + e.message);
  } finally {
    btn.disabled = false;
    btn.textContent = old;
  }
}

/* ---- hộp ghi chú ---- */

function findHl(id) {
  for (const [bid, lst] of Object.entries(state.doc.highlights || {})) {
    const h = lst.find((x) => x.id === id);
    if (h) return { bid, h };
  }
  return null;
}

function openHlPop(id, anchorEl) {
  const hit = findHl(id);
  if (!hit) return;
  state.hlOpen = id;
  const pop = $("#hlPop");
  $("#hlQuote").textContent = hit.h.text || "";
  $("#hlNote").value = hit.h.note || "";
  $("#hlMsg").textContent = "";
  const cur = hit.h.color || "y";
  $$("#hlRecolor .hl-sw").forEach((b) => b.classList.toggle("is-on", b.dataset.c === cur));
  $$("#doc mark.hl").forEach((m) => m.classList.toggle("is-on", m.dataset.hl === id));
  const el = anchorEl || $(`#doc mark.hl[data-hl="${CSS.escape(id)}"]`);
  const box = el ? el.getBoundingClientRect() : { left: innerWidth / 2, bottom: 120 };
  pop.classList.remove("hidden");
  const w = pop.offsetWidth;
  pop.style.left = `${Math.max(8, Math.min(box.left + scrollX, innerWidth - w - 16))}px`;
  pop.style.top = `${box.bottom + scrollY + 8}px`;
}

function closeHlPop() {
  state.hlOpen = null;
  $("#hlPop").classList.add("hidden");
  $$("#doc mark.hl").forEach((m) => m.classList.remove("is-on"));
}

async function saveHlNote() {
  if (!state.hlOpen) return;
  const hit = findHl(state.hlOpen);
  const note = $("#hlNote").value.trim();
  if (!hit || hit.h.note === note) return;
  try {
    const r = await fetch(`/api/doc/${state.doc.id}/highlights`, {
      method: "PATCH", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ update: { id: state.hlOpen, note } }),
    });
    if (!r.ok) throw new Error(await apiErr(r, "không lưu được"));
    state.doc.highlights = (await r.json()).highlights;
    $("#hlMsg").textContent = "Đã lưu.";
    $$(`#doc mark.hl[data-hl="${CSS.escape(state.hlOpen)}"]`)
      .forEach((m) => m.classList.toggle("has-note", !!note));
  } catch (e) { $("#hlMsg").textContent = "Lỗi: " + e.message; }
}

/* Chọn chữ chỉ trong MỘT cột.

   Lưới song ngữ xếp theo hàng, nên thứ tự DOM là `en, vi, gl, en, vi, gl…`.
   Trình duyệt chọn theo thứ tự DOM chứ không theo cột nhìn thấy: kéo từ hàng 1
   xuống hàng 3 trong cột dịch là vơ luôn bản gốc và cột giải thích của những
   hàng ở giữa. Copy ra thì ba thứ tiếng trộn vào nhau.

   Không có thuộc tính CSS nào giới hạn vùng chọn theo cột. Nhưng chữ nằm trong
   phần tử `user-select: none` thì **bị bỏ qua khi quét vùng chọn** — nên chỉ
   cần, ngay lúc bắt đầu kéo, tắt chọn ở hai cột kia. Bỏ cờ khi bắt đầu lượt kéo
   sau, chứ không bỏ lúc thả chuột: người dùng hay thả rồi kéo tiếp cho dài thêm,
   và bỏ sớm thì lần nới đó lại dính cột khác. */
const SEL_COLS = ["en", "vi", "gl"];

function lockSelectionColumn(target) {
  const doc = $("#doc");
  const cell = target?.closest?.(".en, .vi, .gl");
  SEL_COLS.forEach((c) => doc.classList.remove("sel-" + c));
  if (cell) {
    const col = SEL_COLS.find((c) => cell.classList.contains(c));
    if (col) doc.classList.add("sel-" + col);
  }
}

function wireHighlights() {
  const doc = $("#doc");
  // `mousedown` chứ không phải `mouseup`: phải khoá TRƯỚC khi vùng chọn lớn lên.
  doc.addEventListener("mousedown", (e) => lockSelectionColumn(e.target));
  doc.addEventListener("mouseup", () => setTimeout(onDocSelect, 0));
  doc.addEventListener("keyup", (e) => {
    if (e.shiftKey) setTimeout(onDocSelect, 0);
  });
  // màu vừa chọn được nhớ lại, vì người ta thường bôi nhiều đoạn cùng loại
  $$("#hlPick .hl-sw").forEach((b) => (b.onclick = (e) => {
    e.stopPropagation();
    setPref("hlcolor", b.dataset.c);
    makeHighlight(b.dataset.c);
  }));
  $$("#hlRecolor .hl-sw").forEach((b) => (b.onclick = async (e) => {
    e.stopPropagation();
    if (!state.hlOpen) return;
    try {
      const r = await fetch(`/api/doc/${state.doc.id}/highlights`, {
        method: "PATCH", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ update: { id: state.hlOpen, color: b.dataset.c } }),
      });
      if (!r.ok) throw new Error(await apiErr(r, "không đổi màu được"));
      state.doc.highlights = (await r.json()).highlights;
      setPref("hlcolor", b.dataset.c);
      $$(`#doc mark.hl[data-hl="${CSS.escape(state.hlOpen)}"]`)
        .forEach((m) => (m.dataset.c = b.dataset.c));
      $$("#hlRecolor .hl-sw").forEach((x) => x.classList.toggle("is-on", x === b));
    } catch (err) { $("#hlMsg").textContent = "Lỗi: " + err.message; }
  }));

  // rê chuột vào vệt bôi -> hiện ghi chú; bấm vào -> mở để sửa
  let hoverT = null;
  doc.addEventListener("mouseover", (e) => {
    const m = e.target.closest("mark.hl");
    if (!m || state.hlOpen) return;
    clearTimeout(hoverT);
    hoverT = setTimeout(() => openHlPop(m.dataset.hl, m), 220);
  });
  doc.addEventListener("mouseout", (e) => {
    if (e.target.closest("mark.hl")) clearTimeout(hoverT);
  });
  doc.addEventListener("click", (e) => {
    const m = e.target.closest("mark.hl");
    if (m) { e.stopPropagation(); openHlPop(m.dataset.hl, m); }
  });

  $("#hlNote").addEventListener("blur", saveHlNote);
  $("#hlClose").onclick = () => { saveHlNote(); closeHlPop(); };
  $("#hlPop").addEventListener("click", (e) => e.stopPropagation());
  document.addEventListener("click", () => {
    $("#hlBar").classList.add("hidden");
    if (state.hlOpen && !$("#hlNote").matches(":focus")) { saveHlNote(); closeHlPop(); }
  });
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && state.hlOpen) { saveHlNote(); closeHlPop(); }
  });

  $("#hlDel").onclick = async () => {
    if (!state.hlOpen) return;
    const id = state.hlOpen;
    closeHlPop();
    try {
      const r = await fetch(`/api/doc/${state.doc.id}/highlights`, {
        method: "PATCH", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ drop: [id] }),
      });
      if (!r.ok) throw new Error(await apiErr(r, "không xoá được"));
      state.doc.highlights = (await r.json()).highlights;
      renderDoc();
    } catch (e) { status("Lỗi: " + e.message); }
  };

  $("#hlAsk").onclick = async () => {
    if (!state.hlOpen) return;
    const btn = $("#hlAsk"), old = btn.textContent;
    btn.disabled = true; btn.textContent = "Đang đọc…";
    $("#hlMsg").textContent = "Đang nhờ model giải thích đoạn này…";
    try {
      const r = await fetch(
        `/api/doc/${state.doc.id}/highlights/${state.hlOpen}/explain`, { method: "POST" });
      if (!r.ok) throw new Error(await apiErr(r, "không giải thích được"));
      const res = await r.json();
      const hit = findHl(state.hlOpen);
      if (hit) hit.h.note = res.note;
      $("#hlNote").value = res.note;
      $$(`#doc mark.hl[data-hl="${CSS.escape(state.hlOpen)}"]`)
        .forEach((m) => m.classList.add("has-note"));
      reportCost("Đã giải thích đoạn bôi", res.run, res.total);
      $("#hlMsg").textContent = "Sửa thoải mái — rời ô là tự lưu.";
    } catch (e) {
      $("#hlMsg").textContent = "Lỗi: " + e.message;
    } finally { btn.disabled = false; btn.textContent = old; }
  };
}



/* ------------------------------------------- tuỳ chỉnh hiển thị & chủ đề */

/* Lưu ở localStorage chứ không ở server: đây là sở thích của máy đang ngồi,
   không phải thuộc tính của bài báo. */
/* Giữ tiền tố cũ dù công cụ đã đổi tên: đổi là mọi sở thích người dùng đã lưu
   (cỡ chữ, bề rộng cột, sáng/tối, chỗ đọc dở) biến mất im lặng. */
const PREF = "docdoc:";
const pref = (k, dflt) => localStorage.getItem(PREF + k) ?? dflt;
const setPref = (k, v) => localStorage.setItem(PREF + k, v);

function applyReaderPrefs() {
  const font = pref("font", "16");
  document.documentElement.style.setProperty("--reader-font", font / 16 + "rem");
  const inner = $("#doc .doc-inner");
  if (inner) {
    const w = pref("width", "");
    // mặc định: một cột thì thắt lại cho dễ đọc, nhiều cột thì dùng hết bề ngang
    inner.style.maxWidth = w ? w + "%" : ($("#doc").classList.contains("cols-1") ? "46rem" : "");
  }
}

function applyTheme(t) {
  // Skin "Báo" cũ đã thành giao diện mặc định (Sổ tay), nên ai còn lưu lựa
  // chọn đó thì nhận Sổ tay — để nguyên giá trị lạ thì không nút nào trong
  // menu sáng lên, trông như chưa chọn gì.
  if (t === "bao") { t = "light"; setPref("theme", t); }
  if (t === "auto") delete document.documentElement.dataset.theme;
  else document.documentElement.dataset.theme = t;
  $$("#themeSeg .seg-btn").forEach((b) => b.classList.toggle("is-on", b.dataset.theme === t));
  // Sơ đồ đã vẽ giữ nguyên màu của theme cũ — phải vẽ lại chứ không thì
  // chữ đen nằm trên nền đen.
  if (mermaidReady) {
    mermaidReady = false;
    $$(".mmd-slot").forEach((s) => { s.innerHTML = ""; delete s.dataset.done; });
    hydrateDiagrams(document);
  }
}

function wireViewMenu() {
  $("#viewBtn").onclick = (e) => { e.stopPropagation(); $("#viewMenu").classList.toggle("hidden"); };
  $("#viewMenu").onclick = (e) => e.stopPropagation();
  document.addEventListener("click", () => $("#viewMenu").classList.add("hidden"));

  const fs = $("#fontSize"), cw = $("#colWidth");
  fs.value = pref("font", "16");
  cw.value = pref("width", "100");
  // Hiện GIÁ TRỊ cạnh thanh trượt: kéo mà không biết đang ở đâu thì không có
  // cách nào quay lại đúng mức cũ (#33).
  const hienGiaTri = () => {
    $("#fontVal").textContent = `${fs.value}px`;
    $("#widthVal").textContent = `${cw.value}%`;
  };
  fs.oninput = () => { setPref("font", fs.value); applyReaderPrefs(); hienGiaTri(); };
  cw.oninput = () => { setPref("width", cw.value); applyReaderPrefs(); hienGiaTri(); };
  hienGiaTri();

  $$("#themeSeg .seg-btn").forEach((b) => (b.onclick = () => {
    setPref("theme", b.dataset.theme);
    applyTheme(b.dataset.theme);
  }));
  applyTheme(pref("theme", "auto"));
}

/* --------------------------------------- khung PDF gốc để đối chiếu đọc */

/* Dựng bằng ảnh render từng trang qua endpoint sẵn có của công cụ cắt hình —
   không cần nhúng thư viện đọc PDF nào. */
const pdfv = { page: -1, pages: 0, zoom: 100 };

async function togglePdf(open) {
  $("#pdfPane").classList.toggle("hidden", !open);
  if (!open) return;
  // Ghim lại bề rộng đã lưu ĐÚNG LÚC MỞ. Lúc nối dây thì khung còn ẩn, nên
  // `pdfClamp` đo trên một hàng chưa có nó và trần tính ra rộng hơn thật — đo
  // được 720px lọt qua trong khi chỗ còn chỉ đủ 698, và cột văn bản bị bóp
  // xuống dưới mức tối thiểu.
  const luu = pref("pdfw", "");
  if (luu) pdfSetWidth(+luu, false);
  if (!pdfv.pages) {
    try {
      pdfv.pages = (await fetch(`/api/doc/${state.doc.id}/pdfinfo`).then((r) => r.json())).pages || 0;
    } catch { pdfv.pages = 0; }
  }
  if (pdfv.page < 0) pdfGo(topBlockPage() ?? 0);
  else pdfGo(pdfv.page);
}

/** Trang PDF của đoạn đang ở đầu khung đọc. */
function topBlockPage() {
  const id = topBlockId();
  return id ? state.doc.blocks.find((b) => b.id === id)?.page : undefined;
}

function pdfGo(pno) {
  if (!pdfv.pages) return;
  pdfv.page = Math.max(0, Math.min(pno, pdfv.pages - 1));
  $("#pdfImg").src = `/api/doc/${state.doc.id}/page/${pdfv.page}.png?dpi=170`;
  $("#pdfPageLbl").textContent = `${pdfv.page + 1}/${pdfv.pages}`;
}

function pdfZoom(step) {
  pdfv.zoom = Math.max(50, Math.min(pdfv.zoom + step, 400));
  $("#pdfStage").style.setProperty("--pdf-zoom", pdfv.zoom + "%");
}

/* Bề rộng khung PDF: chỗ ít nhất phải chừa lại cho cột văn bản, và khoảng
   rộng cho phép của chính khung PDF. Phóng ảnh trong khung KHÔNG thay được việc
   nới khung — bài hai cột chụp cả trang thì phóng lên chỉ thấy một mảnh, mà đối
   chiếu với bản gốc mới là lý do người ta mở khung này. Cùng bài học với nút ⤢
   của ô xem trước hình. */
const PDF_MIN = 300;
const DOC_MIN = 360;

/** Ghim bề rộng vào khoảng dùng được, theo chỗ CÒN LẠI ở hàng hiện tại.

    Phải trừ cả cột trái, không chỉ lấy bề ngang màn hình: đo trên khung 1440px
    có cột trái đang mở, tính theo màn hình thì trần là 1080px, kéo tới đó là cột
    văn bản còn **51px** — chữ rơi xuống mỗi dòng một từ. Cột trái đóng lại được
    nên số này đổi theo lúc đo, vì thế đo mỗi lần chứ không nhớ sẵn. */
function pdfClamp(px) {
  const hang = $("#pdfPane").parentElement;
  const trai = $("#side");
  const rong = (hang ? hang.clientWidth : 0) || window.innerWidth;
  const chiem = trai && trai.offsetParent !== null ? trai.getBoundingClientRect().width : 0;
  const tran = Math.max(PDF_MIN, rong - chiem - DOC_MIN);
  return Math.round(Math.max(PDF_MIN, Math.min(px, tran)));
}

function pdfSetWidth(px, luu = true) {
  const w = pdfClamp(px);
  $("#pdfPane").style.setProperty("--pdf-w", w + "px");
  if (luu) setPref("pdfw", w);
  return w;
}

/** Trả lại bề rộng mặc định 40% — bỏ hẳn sở thích đã lưu, không ghi đè bằng số. */
function pdfResetWidth() {
  $("#pdfPane").style.removeProperty("--pdf-w");
  localStorage.removeItem(PREF + "pdfw");
}

function wirePdfPane() {
  $("#pdfBtn").onclick = () => togglePdf($("#pdfPane").classList.contains("hidden"));
  $("#pdfClose").onclick = () => togglePdf(false);
  $("#pdfPrev").onclick = () => pdfGo(pdfv.page - 1);
  $("#pdfNext").onclick = () => pdfGo(pdfv.page + 1);
  $("#pdfZoomIn").onclick = () => pdfZoom(25);
  $("#pdfZoomOut").onclick = () => pdfZoom(-25);

  const luu = pref("pdfw", "");
  if (luu) pdfSetWidth(+luu, false);   // ghim lại: màn hình lần này có thể hẹp hơn

  /* Kéo bằng Pointer Events chứ không phải mouse: `setPointerCapture` giữ được
     sự kiện cả khi con trỏ chạy ra ngoài cửa sổ, nên kéo mạnh một cái không làm
     vách tuột mất giữa chừng. Và nó nhận cả bút lẫn cảm ứng, miễn phí. */
  const grip = $("#pdfGrip");
  let x0 = 0, w0 = 0;
  grip.addEventListener("pointerdown", (e) => {
    if (e.button) return;
    x0 = e.clientX;
    w0 = $("#pdfPane").getBoundingClientRect().width;
    grip.setPointerCapture(e.pointerId);
    grip.classList.add("is-drag");
    document.body.classList.add("is-resizing");
    e.preventDefault();
  });
  grip.addEventListener("pointermove", (e) => {
    if (!grip.classList.contains("is-drag")) return;
    pdfSetWidth(w0 - (e.clientX - x0), false);   // kéo sang trái là nới rộng ra
  });
  const xong = (e) => {
    if (!grip.classList.contains("is-drag")) return;
    grip.classList.remove("is-drag");
    document.body.classList.remove("is-resizing");
    try { grip.releasePointerCapture(e.pointerId); } catch { /* đã nhả rồi */ }
    setPref("pdfw", Math.round($("#pdfPane").getBoundingClientRect().width));
  };
  grip.addEventListener("pointerup", xong);
  grip.addEventListener("pointercancel", xong);
  grip.ondblclick = pdfResetWidth;

  // Bàn phím: vách là `role="separator"` có `tabindex`, nên phải kéo được bằng
  // mũi tên — chuột không phải đường duy nhất vào một điều khiển.
  grip.addEventListener("keydown", (e) => {
    const buoc = e.shiftKey ? 64 : 16;
    if (e.key === "ArrowLeft") pdfSetWidth($("#pdfPane").getBoundingClientRect().width + buoc);
    else if (e.key === "ArrowRight") pdfSetWidth($("#pdfPane").getBoundingClientRect().width - buoc);
    else if (e.key === "Home" || e.key === "Escape") pdfResetWidth();
    else return;
    e.preventDefault();
  });

  // Thu cửa sổ nhỏ lại thì bề rộng đã lưu có thể vượt chỗ còn. Ghim lại từ
  // GIÁ TRỊ ĐÃ LƯU chứ không từ bề rộng hiện tại, và không ghi đè sở thích:
  // đo lại từ hiện tại thì mỗi lần thu nhỏ là mất một ít, nới cửa sổ ra khung
  // không bao giờ to lại như cũ.
  window.addEventListener("resize", () => {
    const luu = pref("pdfw", "");
    if (luu) pdfSetWidth(+luu, false);
  });
}

/** Lật khung PDF theo chỗ đang đọc, nếu người dùng để chế độ bám theo. */
function pdfFollow() {
  if ($("#pdfPane").classList.contains("hidden") || !$("#pdfFollow").checked) return;
  const p = topBlockPage();
  if (p != null && p !== pdfv.page) pdfGo(p);
}

/* ------------------------------------------------- nhớ chỗ đang đọc dở */

/** Khối đang nằm trên cùng khung đọc — mốc chung cho cả nhớ vị trí lẫn lật PDF. */
function topBlockId() {
  const doc = $("#doc");
  if (!doc) return "";
  const top = doc.getBoundingClientRect().top;
  return $$("#doc .pair").find((p) => p.getBoundingClientRect().bottom > top + 8)?.dataset.id || "";
}

let posTimer = 0, scrollQueued = false;
function onDocScroll() {
  // lật PDF theo khung hình (mượt), còn ghi vị trí thì để nguội rồi hãy ghi
  if (!scrollQueued) {
    scrollQueued = true;
    requestAnimationFrame(() => { scrollQueued = false; pdfFollow(); });
  }
  clearTimeout(posTimer);
  posTimer = setTimeout(() => {
    const id = topBlockId();
    if (id) setPref("pos:" + state.doc.id, id);
  }, 400);
}

function restorePos() {
  /* Về ĐẦU trước đã. Thay `innerHTML` không đặt lại `scrollTop` — trình duyệt
     chỉ kẹp nó vào chiều cao nội dung mới — nên bài chưa đọc dở lần nào mở ra
     ở đúng độ cao mình đang đọc bài trước (đo được 4731px), tức giữa chừng một
     bài hoàn toàn khác. */
  $("#doc").scrollTop = 0;
  const id = pref("pos:" + state.doc.id, "");
  if (!id) return;
  const el = $(`#p-${CSS.escape(id)}`);
  if (!el) return;
  // scroll-behavior:smooth làm cú nhảy đầu tiên chạy rất lâu — tắt trong lúc khôi phục
  const doc = $("#doc");
  doc.style.scrollBehavior = "auto";
  el.scrollIntoView({ block: "start" });
  doc.style.scrollBehavior = "";
  status("Đã về chỗ đọc dở lần trước. Cuộn lên đầu nếu muốn đọc lại từ đầu.");
}

const narrow = () => matchMedia("(max-width: 1000px)").matches;

/** Mở/đóng cột trái. Dùng ở mọi khổ màn hình — màn rộng cũng cần tắt nó đi để
    lấy chỗ cho ba cột đọc. Màn hẹp thì kèm lớp phủ, bấm ra ngoài là đóng. */
function toggleSide(open) {
  $("#side").classList.toggle("open", open);
  setPref("side", open ? "1" : "0");
  $(".side-veil")?.remove();
  if (!open || !narrow()) return;
  const veil = document.createElement("div");
  veil.className = "side-veil";
  veil.onclick = () => toggleSide(false);
  $("#reader").appendChild(veil);
}

/** Bật/tắt cột hiển thị. Số cột quyết định luôn bố cục lưới. */
function applyCols() {
  const on = { en: $("#colEn").checked, vi: $("#colVi").checked, gl: $("#colGl").checked };
  if (!on.en && !on.vi && !on.gl) {      // tắt hết thì không còn gì để đọc
    $("#colEn").checked = on.en = true;
  }
  const shown = Object.entries(on).filter(([, v]) => v);
  $("#doc").className = "doc cols-" + shown.length +
    shown.map(([k]) => " show-" + k).join("");
  applyReaderPrefs();      // bề rộng mặc định phụ thuộc số cột đang bật
  capNhatNutDich();        // bật thêm cột Giải thích là có thêm việc để làm
  capNhatCotHep();
}

/** Bề rộng tối thiểu của một cột đọc (~45 ký tự một dòng). Dưới mức này chữ vụn
    (#29: cột trái mở trên màn 1366px cho ~200px mỗi cột). */
const COT_TOI_THIEU = 320;

/** Tạm ẩn cột gốc khi ba cột không đủ chỗ — KHÔNG gỡ ô tick của người dùng,
    chỉ gắn lớp `hep` lên `#doc`. Đóng cột trái hay nới cửa sổ là gốc tự về.
    Chỉ xét khi bật cả gốc lẫn ít nhất một cột khác: gốc là cột duy nhất mà
    thiếu nó người đọc vẫn đọc được bài. */
function capNhatCotHep() {
  const d = $("#doc");
  if (!d) return;
  const n = ["en", "vi", "gl"].filter((k) => d.classList.contains("show-" + k)).length;
  const inner = d.querySelector(".doc-inner");
  const rong = (inner || d).clientWidth;
  const hep = !narrow() && n >= 2 && d.classList.contains("show-en") &&
    rong > 0 && rong / n < COT_TOI_THIEU;
  d.classList.toggle("hep", hep);
}

/* --------------------------------------------------- tìm trong bài */

const find = { hits: [], i: -1 };

/** Bọc mọi lần khớp trong <mark>, giữ nguyên chữ hoa thường của bản gốc. */
/** Bỏ dấu để so khớp, nhưng GIỮ NGUYÊN số ký tự.

    Người Việt gõ không dấu là thói quen phổ biến, mà "giam nhieu" trước đây
    không tìm ra "giảm nhiễu". Dùng NFD rồi bỏ dải dấu tổ hợp U+0300–U+036F,
    cộng `đ`/`Đ` (chữ này không tách được bằng NFD).

    **Số ký tự phải không đổi**, vì vị trí tìm được trên chuỗi đã bỏ dấu được
    dùng để cắt chuỗi GỐC — lệch một ký tự là vệt tô lệch khỏi từ. Vì thế
    `normalize("NFD")` xong phải bỏ **đúng** các dấu tổ hợp chứ không gộp gì
    thêm, và `đ → d` là phép thay một-đổi-một. */
function khongDau(t) {
  return t.normalize("NFD").replace(/[\u0300-\u036f]/g, "")
    .replace(/đ/g, "d").replace(/Đ/g, "D").toLowerCase();
}

function markUp(text, q) {
  // So trên bản bỏ dấu của CẢ HAI phía: gõ có dấu vẫn trúng, gõ không dấu cũng
  // trúng. Độ dài giữ nguyên nên `slice` trên chuỗi gốc vẫn đúng chỗ.
  const low = khongDau(text), needle = khongDau(q);
  let out = "", i = 0;
  for (;;) {
    const j = low.indexOf(needle, i);
    if (j < 0) return out + esc(text.slice(i));
    out += esc(text.slice(i, j)) + `<mark class="hit">${esc(text.slice(j, j + needle.length))}</mark>`;
    i = j + needle.length;
  }
}

function clearFind() {
  // khôi phục nguyên HTML cũ: ô bản dịch có thể chứa <span class="pending">,
  // gán lại textContent sẽ nuốt mất nó
  $$("#doc [data-orig]").forEach((el) => {
    el.innerHTML = el.dataset.orig;
    delete el.dataset.orig;
  });
  find.hits = [];
  find.i = -1;
}

function runFind(q) {
  clearFind();
  q = q.trim();
  if (q.length < 2) {          // 1 ký tự thì khớp khắp nơi, vô dụng
    $("#findCount").textContent = q ? "gõ thêm…" : "";
    return;
  }
  const needle = khongDau(q);
  $$("#doc .en, #doc .vi, #doc .gl").forEach((cell) => {
    const text = cell.textContent;
    if (!khongDau(text).includes(needle)) return;
    cell.dataset.orig = cell.innerHTML;
    cell.innerHTML = markUp(text, q);
  });
  find.hits = $$("#doc mark.hit");
  $("#findCount").textContent = find.hits.length ? `1/${find.hits.length}` : "không thấy";
  if (find.hits.length) gotoHit(0);
}

function gotoHit(n) {
  if (!find.hits.length) return;
  find.hits[find.i]?.classList.remove("cur");
  find.i = (n + find.hits.length) % find.hits.length;
  const h = find.hits[find.i];
  h.classList.add("cur");
  h.scrollIntoView({ block: "center" });
  $("#findCount").textContent = `${find.i + 1}/${find.hits.length}`;
}

function toggleFind(open) {
  $("#findBar").classList.toggle("hidden", !open);
  if (open) { $("#findInput").focus(); $("#findInput").select(); }
  else { clearFind(); $("#findInput").value = ""; $("#findCount").textContent = ""; }
}

function wireFind() {
  $("#findBtn").onclick = () => toggleFind($("#findBar").classList.contains("hidden"));
  $("#findClose").onclick = () => toggleFind(false);
  $("#findNext").onclick = () => gotoHit(find.i + 1);
  $("#findPrev").onclick = () => gotoHit(find.i - 1);

  let t = 0;
  $("#findInput").oninput = (e) => {
    clearTimeout(t);
    t = setTimeout(() => runFind(e.target.value), 180);
  };
  $("#findInput").onkeydown = (e) => {
    if (e.key === "Enter") { e.preventDefault(); gotoHit(find.i + (e.shiftKey ? -1 : 1)); }
    if (e.key === "Escape") toggleFind(false);
  };
  addEventListener("keydown", (e) => {
    if ((e.ctrlKey || e.metaKey) && e.key === "f" && !$("#reader").classList.contains("hidden")) {
      e.preventDefault();
      toggleFind(true);
    }
  });
}

/* ------------------------------------------------------- vẽ nội dung */


/* Ẩn khối rác còn sót ngay trong lúc đọc. Ẩn chứ KHÔNG xoá: bản dịch đã trả
   tiền rồi, và người ta hay đổi ý. Khối ẩn cũng bị loại khỏi mẻ dịch nên phần
   chưa dịch thì không tốn thêm. */
async function setBlockHidden(id, on) {
  const b = state.doc.blocks.find((x) => x.id === id);
  if (!b) return;
  b.hidden = on;                       // đổi ngay cho mượt, hỏng thì trả lại
  renderDoc();
  try {
    const r = await fetch(`/api/doc/${state.doc.id}/blocks`, {
      method: "PATCH", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(on ? { hide: [id] } : { unhide: [id] }),
    });
    if (!r.ok) throw new Error(await apiErr(r, "không lưu được"));
    const doc = await r.json();
    state.doc.blocks = doc.blocks;
    state.doc.chunk_ids = doc.chunk_ids;
    state.chunks = doc.chunks || 0;    // ẩn bớt thì số mẻ dịch cũng đổi
    renderDoc();
  } catch (e) {
    b.hidden = !on; renderDoc();
    status("Lỗi: " + e.message);
  }
}

/** Thanh nhắc "đang ẩn N khối" — không có nó thì ẩn xong là quên mất. */
function syncHiddenBar(n) {
  const bar = $("#hiddenBar");
  bar.classList.toggle("hidden", n === 0);
  if (!n) { state.showHidden = false; return; }
  $("#hiddenCount").textContent = n;
  $("#hiddenToggle").textContent = state.showHidden ? "Ẩn lại" : "Xem lại";
}

function renderDoc() {
  const { blocks, translations, notes } = state.doc;
  const host = $("#doc");
  const nHidden = blocks.filter((b) => b.hidden).length;
  const thamKhao = blocks.filter((b) => b.type === "reference" && !b.hidden);
  // Nhãn gốc nói luôn ngôn ngữ: bài tiếng Việt thì "gốc" cũng là tiếng Việt
  const langGoc = state.nguonViet ? "VI" : "EN";
  host.innerHTML = `<div class="doc-inner">
    <div class="colbar" aria-hidden="true">
      <span class="cb-en"><b>Bản gốc</b><i class="cb-tag">${langGoc}</i></span>
      <span class="cb-vi"><b>Bản dịch</b><i class="cb-tag">VI</i>
        <em class="cb-hint cb-hep">· gốc tạm ẩn vì cột hẹp</em></span>
      <span class="cb-gl"><b>Giải thích</b></span>
    </div>${blocks
    .filter((b) => b.type !== "reference")
    .filter((b) => !b.hidden || state.showHidden)
    .map((b, i, arr) => pairHTML(b, translations[b.id], notes[b.id],
      // công thức nằm giữa hai nửa của một đoạn: siết luôn khoảng cách của nó
      b.type === "equation" && arr[i + 1]?.cont))
    .join("")}${thamKhao.length ? `
    <details class="refs" id="refList">
      <summary>Tài liệu tham khảo <span class="muted">· ${thamKhao.length} mục, không dịch</span></summary>
      <ol class="refs-list">${thamKhao.map((b) => `
        <li id="ref-${esc(b.id)}" data-ref-id="${esc(b.id)}">${esc(b.text.replace(/^\s*\[?\d{1,3}[\].]\s*/, ""))}</li>`).join("")}
      </ol>
    </details>` : ""}</div>`;
  syncHiddenBar(nHidden);
  wirePairs();
  repaintHighlights();
  hydrateDiagrams(host);
  applyReaderPrefs();
  host.onscroll = onDocScroll;
}

/** Mã khối nội bộ (`b8`) mà model nhắc tới trong cột giải thích → tên đọc được.

    Model thấy bài dưới dạng `<<<b8>>> …` nên hay viết *"củng cố luận điểm ở
    đoạn b8"* — người đọc không có cách nào biết b8 là gì. Prompt giờ cấm (xem
    `_PLAIN_BODY`), nhưng ô đã sinh ra rồi thì chữa ở đây, miễn phí: thay bằng
    mấy chữ đầu của chính đoạn đó, bấm vào thì nhảy tới.

    Thay trên chuỗi THÔ bằng ký tự giữ chỗ rồi mới `sci()` — thay trên HTML đã
    dựng thì có nguy cơ đụng vào thuộc tính của thẻ `sci()` sinh ra. */
const MA_KHOI_RE = /(?:(?:đoạn|khối|block|Đoạn|Khối|Block)\s+)?\(?\b(b\d{1,4})(?:_g)?\b\)?/g;

function dauDoan(id) {
  const b = state.doc?.blocks?.find((x) => x.id === id);
  if (!b) return "";
  const t = String(state.doc.translations?.[id] || b.text || "")
    .replace(/[\^_]\{([^}]*)\}/g, "$1").replace(/\s+/g, " ").trim();
  return catGon(t, 34);
}

function sciGoiKhoi(t) {
  const ids = [];
  const tho = String(t || "").replace(MA_KHOI_RE, (whole, id) => {
    if (!dauDoan(id)) return whole;          // không phải mã khối có thật: để nguyên
    ids.push(id);
    return `\u0001${ids.length - 1}\u0001`;
  });
  return sci(tho).replace(/\u0001(\d+)\u0001/g, (_, i) => {
    const id = ids[+i];
    return `<a class="ref-khoi" data-goto="${esc(id)}" title="Nhảy tới đoạn này">đoạn “${esc(dauDoan(id))}”</a>`;
  });
}

function pairHTML(b, vi, note, inFlow = false) {
  // `cont` = nửa sau của một đoạn bị công thức chen vào giữa. Ba khối vẫn là ba
  // khối (ảnh công thức phải đứng giữa hai nửa, và mỗi khối vẫn là một đơn vị
  // dịch/bôi/ghi chú), chỉ bỏ khoảng cách và vạch ngăn để đọc ra liền một đoạn
  // như trang in.
  const cls = (b.type === "heading" ? "h" + Math.min(b.level || 1, 3) : b.type)
    + (b.marker ? " li" : "")
    + (b.cont ? " is-cont" : "") + (inFlow ? " in-flow" : "")
    + (b.type === "equation" && b.figure ? " eq-img" : "");
  // Bảng và khối mã của nguồn Markdown giữ nguyên xuống dòng, nên `sci()` —
  // vốn dựng một đoạn văn — sẽ dồn chúng thành một dòng dài. Dùng lại
  // `renderMd` cho bảng (nó đã có nhánh bảng và có test) và `<pre>` cho mã.
  const bodyHTML = (blk) =>
    blk.type === "table" ? renderMd(blk.text)
    : blk.type === "code" ? `<pre class="codeblk"><code>${esc(
        blk.text.replace(/^\s*(```|~~~).*\n?/, "").replace(/\n?\s*(```|~~~)\s*$/, ""))}</code></pre>`
    : sci(blk.text);

  // dấu đầu mục treo ngoài lề, lặp ở cả cột gốc lẫn cột dịch vì hai cột là hai
  // bản của cùng một mục
  const mk = b.marker ? `<span class="li-mk">${esc(b.marker)}</span>` : "";
  // Với tiêu đề mục, hiện luôn bản gốc mờ thay vì chữ "chưa dịch" — đỡ vỡ mục lục
  const viHTML = !b.translate ? ""
    : vi ? sci(vi)
    : b.type === "heading" ? `<span class="pending">${sci(b.text)}</span>`
    : `<span class="pending">chưa dịch</span>`;
  // Chỉ để biểu tượng: nhãn "Giải thích" đủ rộng để đè lên chữ của đoạn.
  // Nút ẩn có ở mọi loại khối: rác còn sót hay là nhãn trục lạc ra từ hình,
  // dòng chân trang — chúng không phải `para` nên trước đây không có nút nào.
  const tools = `<div class="tools">
       ${b.type === "para" || b.type === "caption" ? `
         <button data-act="explain" title="Giải thích — đoạn này đang làm gì trong lập luận của bài?">${ico("bulb")}</button>
         <button data-act="copy" title="Chép bản dịch">${ico("copy")}</button>
         <button data-act="edit" title="Sửa tay bản dịch. Miễn phí, và bản sửa được ghi vào bộ nhớ dịch nên đoạn y hệt ở bài khác cũng dùng bản của bạn.">${ico("pencil")}</button>
         <button data-act="redo" title="Dịch lại đoạn này. Tốn một lượt gọi model: rẻ nếu bài vừa dịch xong (toàn văn còn trong cache), tới khoảng $0,03 nếu đã lâu vì phải đọc lại cả bài. Bản cũ bị bỏ khỏi bộ nhớ dịch nên không quay lại, và lượt mới chạy ở nhiệt độ cao hơn để không ra đúng kết quả cũ.">${ico("redo")}</button>` : ""}
       ${b.hidden
         ? `<button data-act="unhide" title="Đưa khối này trở lại mạch đọc">${ico("undo")}</button>`
         : `<button data-act="hide" title="Ẩn khối này khỏi mạch đọc (giữ nguyên bản dịch, hiện lại được)">${ico("hide")}</button>`}
     </div>`;
  // Hình/bảng cắt từ PDF hiện trên caption. Công thức cũng là ảnh cắt từ PDF —
  // toán hai chiều dựng lại bằng chữ thì mất hình dạng, ảnh thì đúng bản in.
  // Khung cắt sai thì phải sửa được NGAY TẠI CHỖ ĐANG ĐỌC. Trước đây nút ✂ chỉ
  // có ở màn soát, nên gặp một công thức bị cắt cụt giữa lúc đọc thì phải quay
  // ra, tìm lại đúng khối, sửa, rồi vào đọc lại từ đầu — đủ phiền để người ta
  // bỏ qua và đọc tiếp với cái ảnh hỏng.
  const fig = b.figure
    ? `<figure class="${b.type === "equation" ? "eqfig" : "figure"}">
         <img src="/api/doc/${esc(state.doc.id)}/img/${esc(b.figure)}.png"
              alt="${esc(b.text.slice(0, 90))}" loading="lazy">
         ${b.figure_page >= 0
           ? `<button class="fig-crop" data-crop="${esc(b.id)}"
                title="Khung cắt sai? Kéo lại khung trên trang PDF gốc.">${ico("scissors")}</button>` : ""}
       </figure>`
    : "";
  const gl = state.doc.plain?.[b.id] || "";
  return `<div class="pair ${cls}${b.hidden ? " is-hidden" : ""}" id="p-${esc(b.id)}" data-id="${esc(b.id)}" data-section="${esc(b.section || "")}">
    ${fig}${tools}
    <div class="en">${mk}${bodyHTML(b)}</div>
    <div class="vi" data-vi>${mk}${viHTML}</div>
    <div class="gl" data-gl>${sciGoiKhoi(gl)}</div>
    ${note ? noteHTML(note) : ""}
  </div>`;
}

/* Sửa tay một ô — bản dịch hoặc phần diễn giải.

   Dùng `<textarea>` chứa **văn bản thô đang lưu**, không dùng `contenteditable`
   trên nội dung đã hiển thị: cột đó đã đi qua `sci()` nên có `<sup>`, `<sub>` và
   thẻ `<a>` cho tham chiếu hình. Lấy HTML đó làm nội dung lưu thì mỗi lần sửa
   lại nhân thêm một lớp thẻ, và `^{…}` gốc mất luôn. */
function editCell(pair, which) {
  const id = pair.dataset.id;
  const cell = $(which === "vi" ? "[data-vi]" : "[data-gl]", pair);
  if (!cell || cell.querySelector("textarea")) return;

  const raw = (which === "vi" ? state.doc.translations : state.doc.plain)?.[id] || "";
  const before = cell.innerHTML;
  cell.innerHTML = `<textarea class="cell-edit" rows="3"></textarea>
    <div class="cell-edit-bar">
      <button data-e="save" class="btn btn-sm">Lưu</button>
      <button data-e="cancel" class="btn btn-sm">Huỷ</button>
      <span class="hint">Ctrl+Enter lưu · Esc huỷ · giữ nguyên <code>^{…}</code> và <code>_{…}</code></span>
    </div>`;
  const ta = $("textarea", cell);
  ta.value = raw;
  ta.style.height = "auto";
  ta.style.height = Math.max(70, ta.scrollHeight) + "px";
  ta.focus();
  ta.setSelectionRange(raw.length, raw.length);

  const cancel = () => { delete cell.dataset.orig; cell.innerHTML = before; };
  const save = async () => {
    const val = ta.value;
    if (val === raw) return cancel();
    try {
      const r = await fetch(`/api/doc/${state.doc.id}/translation`, {
        method: "PATCH", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ block_id: id, [which]: val }),
      });
      if (!r.ok) throw new Error(await apiErr(r, "không lưu được"));
      const res = await r.json();
      if (which === "vi") state.doc.translations[id] = res.vi;
      else state.doc.plain[id] = res.plain;
      // Thanh tìm kiếm cất bản HTML gốc vào `dataset.orig` rồi khôi phục khi
      // đóng — bản chép đó nằm trên chính `cell` nên `innerHTML` không cuốn đi,
      // và nó sẽ ghi đè bản vừa sửa. Xoá đi thì đóng tìm kiếm không còn lật
      // ngược công sửa của người dùng.
      delete cell.dataset.orig;
      const mk = pair.querySelector(".li-mk");
      cell.innerHTML = (mk ? mk.outerHTML : "")
        + sci(which === "vi" ? res.vi : res.plain);
      cell.classList.add("was-edited");
      status("Đã lưu bản sửa — bộ nhớ dịch cũng được cập nhật");
    } catch (e) {
      status("Lỗi: " + e.message);
      cancel();
    }
  };
  // Gắn vào chính hai NÚT vừa dựng, không gắn vào `cell`.
  //
  // `cell` sống sót qua cả phiên đọc (mọi đường thoát chỉ thay `innerHTML` của
  // nó), nên gắn vào `cell` là mỗi lần mở sửa lại chồng thêm một handler. Lần
  // sửa thứ hai chạy CẢ HAI: handler cũ còn giữ textarea cũ và văn bản gốc cũ,
  // nên nó `PATCH` đè bản cũ lên bản vừa gõ — mất chữ, và hai request đua nhau.
  // `e.stopPropagation()` không cứu được vì hai handler nằm trên cùng phần tử.
  // Hai nút này chết theo `cell.innerHTML` nên không tích lại được.
  $('[data-e="save"]', cell).onclick = (e) => { e.stopPropagation(); save(); };
  $('[data-e="cancel"]', cell).onclick = (e) => { e.stopPropagation(); cancel(); };
  ta.addEventListener("keydown", (e) => {
    if (e.key === "Escape") { e.stopPropagation(); cancel(); }
    if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) { e.preventDefault(); save(); }
  });
}

function noteHTML(n) {
  const row = (label, val, cls = "") =>
    val ? `<dt>${label}</dt><dd class="${cls}">${sci(val)}</dd>` : "";
  const diagram = n.diagram?.trim()
    ? `<div class="mmd-slot" data-mmd="${esc(n.diagram)}" data-cap="${esc(n.diagram_caption || "")}"></div>`
    : "";
  return `<div class="note">
    <h4>Giải thích lập luận <button class="icon-btn" data-act="closenote" title="Thu gọn — ghi chú vẫn giữ, mở lại không tốn tiền">✕</button></h4>
    ${diagram}
    <dl>
      ${row("Ý chính", n.gist, "gist")}
      ${row("Vai trò trong bài", n.role)}
      ${row("Nối với đoạn trước", n.link_back)}
      ${row("Giải thích chi tiết", n.unpack)}
      ${row("Hình dung", n.analogy)}
      ${row("Cần lưu ý", n.caution, "caution")}
      ${row("Tự kiểm tra", n.check, "check-q")}
    </dl>
  </div>`;
}

/** Tìm mọi chỗ đánh dấu sơ đồ trong `root` và vẽ chúng ra. */
function hydrateDiagrams(root) {
  $$(".mmd-slot", root).forEach((slot) => {
    if (slot.dataset.done) return;
    slot.dataset.done = "1";
    drawDiagram(slot, slot.dataset.mmd, slot.dataset.cap);
  });
}

function wirePairs() {
  $$("#doc .pair").forEach((el) => {
    el.addEventListener("click", (e) => {
      // Thẻ giải thích của chú thích hình được thu gọn sẵn (xem `.pair.caption
      // .gl` trong style.css): bấm vào là mở hết / thu lại.
      const gl = e.target.closest(".pair.caption .gl");
      if (gl && !e.target.closest("a, button, textarea")) {
        gl.classList.toggle("mo-rong");
        return;
      }
      const goto = e.target.closest("a.ref-khoi[data-goto]");
      if (goto) { e.preventDefault(); jumpToBlock(goto.dataset.goto); return; }
      const cite = e.target.closest("[data-cite]");
      if (cite) {
        e.stopPropagation();
        const m = state.citeIndex?.[cite.dataset.cite];
        const li = m && $(`#ref-${CSS.escape(m.id)}`);
        if (li) {
          $("#refList").open = true;
          li.scrollIntoView({ block: "center" });
          li.classList.add("moi-nhay");
          setTimeout(() => li.classList.remove("moi-nhay"), 1600);
        }
        return;
      }
      const ref = e.target.closest("[data-figref]");
      if (ref) { e.stopPropagation(); return openFigPeek(ref.dataset.figref); }
      const cut = e.target.closest("[data-crop]");
      if (cut) { e.stopPropagation(); return openCrop(cut.dataset.crop); }
      const act = e.target.closest("[data-act]")?.dataset.act;
      if (act === "hide") return setBlockHidden(el.dataset.id, true);
      if (act === "unhide") return setBlockHidden(el.dataset.id, false);
      if (act === "explain") return explainBlock(el.dataset.id);
      if (act === "edit") return editCell(el, "vi");
      if (act === "redo") return redoBlock(el.dataset.id);
      if (act === "copy") {
        navigator.clipboard.writeText($("[data-vi]", el).textContent.trim());
        // Ghi lên NÚT, không lên `e.target`: target có thể là icon bên trong.
        const nut = e.target.closest("[data-act]");
        nut.innerHTML = ico("check");
        setTimeout(() => (nut.innerHTML = ico("copy")), 900);
        return;
      }
      // Thu gọn chứ không xoá: ghi chú đã nằm trong DB rồi, xoá đi chỉ khiến
      // lần sau bấm 💡 phải gọi model và trả tiền lại cho đúng nội dung đó.
      if (act === "closenote") { $(".note", el)?.classList.add("collapsed"); return; }
      $$("#doc .pair.is-on").forEach((x) => x.classList.remove("is-on"));
      el.classList.add("is-on");
      // bấm vào đoạn nào thì khung PDF mở đúng trang của đoạn đó
      const p = state.doc.blocks.find((b) => b.id === el.dataset.id)?.page;
      if (p != null && !$("#pdfPane").classList.contains("hidden")) pdfGo(p);
    });
  });
}

/* ------------------------------------------------------- vẽ sidebar */

function renderSide() {
  const b = state.doc.brief;
  $("#rebriefBtn").classList.toggle("hidden", !b);   // chưa có brief thì chưa có gì để dựng lại
  if (b) {
    const line = (k, v) => v ? `<p class="brief-line"><b>${k}</b>${sci(v)}</p>` : "";
    $("#briefBox").innerHTML =
      (b.one_line ? `<p class="pull">${sci(b.one_line)}</p>` : "") +
      line("Bài toán", b.problem) + line("Khoảng trống", b.gap) + line("Ý tưởng", b.idea) +
      line("Cách làm", b.method) + line("Bằng chứng", b.evidence) + line("Giới hạn", b.limits) +
      (b.reader_warnings?.length
        ? `<p class="brief-line"><b>Dễ hiểu nhầm</b></p><ul class="warns">${
            b.reader_warnings.map((w) => `<li>${esc(w)}</li>`).join("")}</ul>`
        : "");

    const dia = (code, cap) => code?.trim()
      ? `<div class="mmd-slot" data-mmd="${esc(code)}" data-cap="${esc(cap)}"></div>` : "";
    $("#diagramBox").innerHTML =
      dia(b.argument_diagram, "Mạch lập luận của bài") +
      dia(b.method_diagram, "Cơ chế bài đề xuất");
    hydrateDiagrams($("#diagramBox"));

    $("#chainBox").innerHTML = b.argument_chain?.length
      ? `<ol class="chain">${b.argument_chain.map((s) => `<li>
            <span class="role" data-r="${esc(s.role || "")}">${esc(s.role || "bước")}</span>
            <div>${sci(s.step || "")}</div>
            ${(s.sections || []).map((x) => `<span class="go" data-sec="${esc(x)}">→ ${esc(x)}</span>`).join("")}
          </li>`).join("")}</ol>`
      : `<p class="muted">Chưa có.</p>`;
    $$("#chainBox .go").forEach((el) => (el.onclick = () => jumpToSection(el.dataset.sec)));

    $("#termsBox").innerHTML = b.glossary?.length
      // Thuật ngữ giữ tiếng Anh vẫn hiện nghĩa tiếng Việt để tra — chỉ là nghĩa
      // đó không được dùng thay cho thuật ngữ trong bản dịch.
      ? b.glossary.map((g) => `<div class="term">
          <b>${esc(g.en)}</b> ${g.keep_en
            ? `<span class="keep">giữ nguyên</span>${
                g.vi ? ` <span class="vi">≈ ${esc(g.vi)}</span>` : ""}`
            : `→ <span class="vi">${esc(g.vi || "")}</span>`}
          ${g.gloss ? `<span class="gloss">${sci(g.gloss)}</span>` : ""}
        </div>`).join("")
      : `<p class="muted">Chưa có.</p>`;
  }

  const heads = state.doc.blocks.filter((x) => x.type === "heading");
  // Không dùng href="#p-…": location.hash là chỗ lưu id bài, ghi đè vào đó thì
  // reload xong sẽ đi mở một bài tên "p-b12" không tồn tại rồi văng về màn nhập.
  $("#outlineBox").innerHTML = heads.length
    ? `<nav class="outline">${heads.map((h) => {
        const vi = state.doc.translations[h.id];
        return `<a role="button" tabindex="0" data-go="${esc(h.id)}" data-lvl="${h.level || 1}">${sci(vi || h.text)}</a>`;
      }).join("")}</nav>`
    : `<p class="muted">Không nhận ra mục nào.</p>`;
  $$("#outlineBox [data-go]").forEach((el) => (el.onclick = () => jumpToBlock(el.dataset.go)));
}

/** Cuộn tới một khối và làm nổi nó lên. */
function jumpToBlock(id) {
  const el = $(`#p-${CSS.escape(id)}`);
  if (!el) return;
  el.scrollIntoView({ block: "center" });
  $$("#doc .pair.is-on").forEach((x) => x.classList.remove("is-on"));
  el.classList.add("is-on");
  toggleSide(false);          // ở chế độ hẹp, nhảy xong thì trả màn hình lại cho bài
}

function jumpToSection(name) {
  const el = $$("#doc .pair").find((p) => p.dataset.section === name || $(".en", p)?.textContent.trim() === name);
  if (el) jumpToBlock(el.dataset.id);
}

/** Dòng cuối cột trái: **bài này đã tốn bao nhiêu**.

    Bản cũ ghi `23.4k vào · 5.1k ra · 12.0k đọc từ cache · $0.0266` — bốn con
    số, ba trong đó là token. Token là đơn vị tính tiền của nhà cung cấp, không
    phải thứ người đọc quyết định được gì dựa vào: không ai nhìn "5.1k ra" rồi
    đổi cách dùng công cụ. Cái họ thật sự muốn biết là **đã bỏ ra bao nhiêu**,
    và nó bị đẩy xuống cuối dòng, sau ba con số không ai đọc.

    Nên tiền lên trước, phần token xuống `title` — vẫn tra được khi cần soát
    cache, mà không tranh chỗ với con số duy nhất có nghĩa.

    Tỉ lệ cache đi kèm vì đó là con số DUY NHẤT trong đám token mà người dùng
    tác động được: bấm 💡 ngay sau khi dịch thì prefix còn ấm, bấm hôm sau thì
    đọc lại cả bài ở giá đầy đủ (đã đo: `cached_tokens = 0` trên 23.836 token). */
function renderUsage() {
  const u = state.doc.usage || {};
  const k = (n) => `${((n || 0) / 1000).toFixed(1)}k`.replace(".", ",");
  const vao = u.prompt_tokens || 0;
  const tiLe = vao ? Math.round((u.cached_tokens || 0) / vao * 100) : 0;
  const box = $("#usageBox");
  box.textContent = u.cost
    ? `Bài này đã tốn $${u.cost.toFixed(4).replace(".", ",")}`
    : "Chưa tốn gì cho bài này";
  if (tiLe >= 5) box.textContent += ` · ${tiLe}% đọc lại từ cache`;
  box.title = `${k(vao)} token đọc vào · ${k(u.completion_tokens)} token viết ra`
    + (u.cached_tokens ? ` · ${k(u.cached_tokens)} trong số đọc vào lấy từ cache` : "")
    + "\n\nPhần đọc vào gần như là toàn văn bài, lặp lại ở mọi lượt gọi. Nó rẻ "
    + "khi còn trong cache của model, nên dịch xong rồi bấm giải thích hay dựng "
    + "slide ngay thì rẻ hơn hẳn bấm vào hôm sau.";
}

/* --------------------------------------- chọn dịch từng phần --------- */

/* Prefix bài (luật dịch + toàn văn + glossary) nằm sẵn trong cache của model,
   nên chi phí một lượt dịch chủ yếu là token ĐẦU RA. Bỏ bớt mục không định đọc
   — phụ lục, tham khảo mở rộng — là tiết kiệm thật chứ không phải mẹo vặt. */

async function loadSections() {
  const box = $("#pickList");
  box.innerHTML = `<p class="hint">Đang tính…</p>`;
  try {
    const r = await fetch(`/api/doc/${state.doc.id}/sections`);
    if (!r.ok) throw new Error("không lấy được danh sách mục");
    const { sections } = await r.json();
    state.sections = sections;
    state.pick ??= new Set(sections.map((s) => s.first));   // mặc định chọn hết
    box.innerHTML = sections.map((s) => {
      const left = s.blocks - s.done;
      const money = s.cost_usd == null ? "" :
        `~$${(s.cost_usd * (left / Math.max(s.blocks, 1))).toFixed(3)}`;
      return `<label class="pick-row${left === 0 ? " done" : ""}" data-sec="${esc(s.first)}">
        <input type="checkbox" ${state.pick.has(s.first) ? "checked" : ""}>
        <span class="nm" title="${esc(s.name)}">${esc(s.name)}</span>
        <span class="ct">${left}/${s.blocks} khối ${money}</span>
      </label>`;
    }).join("");
    $$("#pickList input").forEach((i) => (i.onchange = () => {
      const key = i.closest(".pick-row").dataset.sec;
      i.checked ? state.pick.add(key) : state.pick.delete(key);
      sumPick();
    }));
    sumPick();
  } catch (e) {
    box.innerHTML = `<p class="err">${esc(e.message)}</p>`;
  }
}

function setPickAll(on) {
  state.pick = new Set(on ? (state.sections || []).map((s) => s.first) : []);
  $$("#pickList input").forEach((i) => (i.checked = on));
  sumPick();
}

function sumPick() {
  const secs = (state.sections || []).filter((s) => state.pick?.has(s.first));
  const left = secs.reduce((n, s) => n + (s.blocks - s.done), 0);
  const cost = secs.reduce((n, s) => n + (s.cost_usd || 0)
    * ((s.blocks - s.done) / Math.max(s.blocks, 1)), 0);
  $("#pickSum").textContent = left
    ? `${secs.length} mục · ${left} khối chưa dịch · ~${money(cost)}`
    : "Không còn khối nào chưa dịch trong phần đã chọn";
}

/** Mã khối được phép dịch lần này. `null` = dịch tất, như cũ. */
function pickedIds() {
  if (!state.pick || !state.sections) return null;
  if (state.pick.size === state.sections.length) return null;
  const out = new Set();
  for (const s of state.sections) {
    if (state.pick.has(s.first)) s.ids.forEach((i) => out.add(i));
  }
  return out;
}

/* --------------------------------------------------------- dịch bài */

/* Dừng ở ranh giới mẻ chứ không cắt ngang: mẻ đang chạy đã sinh token và đã bị
   tính tiền rồi, bỏ giữa chừng là mất trắng phần đó mà vẫn phải trả. */
function requestStop() {
  if (state.stopping) return;
  state.stopping = true;
  const btn = $("#translateBtn");
  btn.textContent = "Đang dừng…";
  btn.disabled = true;
  // HUỶ THẬT, không chỉ gắn cờ. Trước đây Dừng chỉ hẹn "sẽ dừng khi xong mẻ
  // đang chạy" — mà mẻ đang chạy là một request có thể treo tới 300 giây, nên
  // nút kẹt ở "Đang dừng…" và cách duy nhất còn lại là tải lại trang.
  //
  // Lượt dựng tóm lược chưa sinh ra gì nên huỷ là không mất tiền; mẻ dịch đang
  // chạy thì phần đã stream về vẫn được giữ, vì `streamChunk` lưu từng khối
  // ngay khi nhận.
  try { state.bo?.abort(); } catch { /* đã huỷ rồi thì thôi */ }
  status("Đang huỷ lượt gọi hiện tại… Phần đã dịch xong vẫn được giữ.");
}

/* Số mẻ dịch chạy cùng lúc. Ba, không hơn: mỗi mẻ giữ một `EventSource`, mà
   trình duyệt chỉ mở 6 kết nối HTTP/1.1 tới một máy chủ — để dành chỗ cho các
   request khác của chính trang này (ảnh, giải thích đoạn, hỏi đáp). Vượt trần
   đó thì kết nối thứ bảy XẾP HÀNG im lặng, và mọi thứ trên trang trông như treo. */
const SONG_SONG = 3;

async function runTranslate() {
  if (state.translating) return;
  // Dịch xong sẽ ghi đè textContent của ô — bỏ đánh dấu tìm kiếm trước, không
  // thì bản gốc lưu trong data-orig bị lệch với nội dung thật.
  clearFind();
  state.translating = true;
  state.stopping = false;
  const btn = $("#translateBtn");
  btn.textContent = "⏸ Dừng";
  if (pickedIds()) {
    const n = (state.sections || []).filter((x) => state.pick.has(x.first)).length;
    status(`Chỉ dịch ${n} mục đã chọn — các mục khác giữ nguyên.`);
  }
  $("#docModel").disabled = true;   // đổi model giữa chừng thì mỗi mẻ một giọng
  $("#progress").classList.remove("hidden");
  const refine = $("#refineChk").checked ? 1 : 0;
  let reusedTotal = 0;
  const only = pickedIds();          // null = dịch cả bài như cũ
  // chỉ sinh đúng cột đang bật — tắt cột nào là không trả tiền cho cột đó
  const wantVi = $("#colVi").checked, wantGl = $("#colGl").checked;
  const mode = wantVi && wantGl ? "both" : wantGl ? "plain" : "vi";
  if (!wantVi && !wantGl) {
    status("Bật ít nhất một trong hai cột Việt hoặc Giải thích thì mới có gì để sinh.");
    state.translating = false; btn.disabled = false; $("#docModel").disabled = false;
    capNhatNutDich();
    $("#progress").classList.add("hidden");
    return;
  }

  state.bo = new AbortController();
  let thuLaiTomLuoc = "";        // lý do hỏng ở bước tóm lược, nếu có
  try {
    if (!state.doc.brief) {
      // Đồng hồ chạy ngay: một thanh trạng thái đứng im không phân biệt được
      // "đang chạy" với "đã treo", và đó đúng là thứ người dùng gặp.
      dongHo("Đang đọc toàn bài để dựng tóm lược và chốt bảng thuật ngữ");
      const r = await fetch(`/api/doc/${state.doc.id}/brief`,
                           { method: "POST", signal: state.bo?.signal });
      if (!r.ok) {
        const e = new Error(await apiErr(r, "Không dựng được tóm lược"));
        e.laTomLuoc = true;           // hỏng ở bước đầu: mời thử lại ngay (#2)
        throw e;
      }
      const res = await r.json();
      state.doc.brief = res.brief;
      $("#docTitleVi").textContent = state.doc.brief.title_vi || state.doc.title;
      renderSide();
      dongHoTat();
      reportCost("Đọc toàn bài xong", res.run, res.total);
    }

    /* Dịch SONG SONG, nhưng mẻ đầu chạy MỘT MÌNH.

       Song song được vì không mẻ nào cần kết quả của mẻ trước: tóm lược và bảng
       thuật ngữ đã chốt sẵn trong prefix, và phía server mỗi lần ghi là
       `load → sửa → save` liền mạch, không `await` chen giữa — nên hai mẻ về
       đích cùng lúc cũng không đè nhau (`test_hai_me_dich_chay_xen_nhau_…`).

       Mẻ đầu chạy một mình vì CACHE. Mọi mẻ mở đầu bằng cùng một prefix — luật
       dịch + tóm lược + bảng thuật ngữ + TOÀN VĂN bài, ~24k token trên CIRAG.
       Bắn cả loạt lúc cache còn nguội thì mẻ nào cũng trả giá đầy đủ cho chừng
       ấy token đọc vào. Chờ mẻ đầu xong là prefix đã nằm trong cache, các mẻ
       sau đọc lại với giá rẻ — đúng như lúc chạy tuần tự, chỉ nhanh hơn. */
    const can = [];
    let xong = 0;
    for (let i = 0; i < state.chunks; i++) {
      // mẻ nào không chứa khối nào trong phần đã chọn thì bỏ hẳn, khỏi gọi model
      const ngoai = only && !(state.doc.chunk_ids?.[i] || []).some((id) => only.has(id));
      if (chunkDone(i) || ngoai) xong++;
      else can.push(i);
    }
    bar(xong / state.chunks);
    const soat = refine ? " (có soát lại)" : "";

    const chay1 = async (i) => {
      const d = await streamChunk(i, refine, mode, only);
      if (d?.reused) reusedTotal += d.reused;
      xong++;
      const tag = d?.reused
        ? ` (${d.reused} đoạn lấy lại từ bộ nhớ dịch, ${d.generated || 0} đoạn dịch mới)`
        : "";
      reportCost(`Xong phần ${i + 1} · ${xong}/${state.chunks}${tag}`, d?.run, d?.usage);
      bar(xong / state.chunks);
    };

    const hang = [...can];
    if (hang.length) {
      const dau = hang.shift();
      status(`Đang dịch phần ${dau + 1}/${state.chunks}${soat} — mẻ đầu chạy một mình `
        + "để nạp bài vào cache, các mẻ sau sẽ chạy song song…");
      await chay1(dau);
    }

    /* Một mẻ hỏng (mạng, model trả lỗi) thì THÔI NHẬN mẻ mới nhưng để các mẻ
       đang chạy về đích: chúng đã sinh token, tức đã bị tính tiền rồi, huỷ
       ngang là mất trắng phần đó. Bấm Dừng thì khác — người dùng muốn dừng
       NGAY, nên `AbortError` lan ra và huỷ cả loạt. */
    let loi = null;
    const tho = async () => {
      while (hang.length && !state.stopping && !loi) {
        const i = hang.shift();
        status(`Đang dịch song song${soat}: ${xong}/${state.chunks} phần xong · `
          + `${Math.min(SONG_SONG, hang.length + 1)} mẻ cùng lúc…`);
        try { await chay1(i); }
        catch (e) { if (e.name === "AbortError") throw e; loi ||= e; }
      }
    };
    await Promise.all(Array.from({ length: Math.min(SONG_SONG, hang.length) }, tho));
    if (loi) throw loi;
    const stoppedAt = state.stopping && hang.length ? xong : -1;
    const spent = ` — phiên này tốn ${money(state.session)}` +
      (reusedTotal ? `, ${reusedTotal} đoạn lấy lại miễn phí từ bộ nhớ dịch` : "") +
      (state.doc.usage?.cost ? `, cả bài ${money(state.doc.usage.cost)}` : "");
    status(stoppedAt >= 0
      ? `Đã dừng — xong ${stoppedAt}/${state.chunks} phần${spent}.` +
        " Bấm Dịch tiếp để chạy nốt — phần đã xong không dịch lại."
      : `Dịch xong${spent}. Bấm 💡 trên từng đoạn để xem nó đang làm gì trong lập luận.`);

    // Đánh dấu câu đáng nhớ ngay khi dịch xong CẢ bài — đây là thứ được yêu cầu
    // từ đầu ("khi dịch nên có cơ chế bôi câu cần nhớ"), và làm nút bấm tay
    // thôi thì người dùng dịch xong một bài rồi không thấy vệt nào.
    //
    // Ba điều kiện, cần cả ba: không dừng giữa chừng, MỌI đoạn đã có bản dịch
    // (dịch một mục thì chưa đủ ngữ cảnh để chọn câu cho cả bài), và cột tiếng
    // Việt đang bật — vệt neo vào ô đó, tắt cột thì không có chỗ để neo.
    // Và điều kiện thứ tư: lượt này THẬT SỰ có mẻ để dịch (`can.length`). Thiếu
    // nó thì bấm "Dịch tiếp" trên bài đã xong — đúng thứ người mới hay bấm, vì
    // nút ấy to và vàng nhất màn — là vòng dịch chạy qua không làm gì rồi tự
    // gọi lại lượt đánh dấu, TÍNH TIỀN, và thay hết vệt cũ bằng một bộ khác.
    if (can.length > 0 && stoppedAt < 0 && $("#insightAuto")?.checked && wantVi && allTranslated()) {
      await markInsights(true);
    }
  } catch (e) {
    status(e.name === "AbortError"
      ? "Đã dừng theo yêu cầu. Phần đã dịch xong vẫn giữ nguyên — bấm Dịch tiếp để chạy nốt."
      : "Lỗi: " + e.message);
    if (e.laTomLuoc) thuLaiTomLuoc = e.message;
  } finally {
    dongHoTat();
    state.bo = null;
    state.translating = false;
    state.stopping = false;
    btn.disabled = false;
    $("#docModel").disabled = false;
    capNhatNutDich();
    $("#progress").classList.add("hidden");
    renderSide();
    renderUsage();
    syncSlidesBtn();      // dịch xong cả bài thì mở khoá nút dựng slide
  }

  // Bước tóm lược hỏng thì chưa mẻ nào chạy: hỏi thử lại NGAY, kèm lý do —
  // một dòng trạng thái đỏ dễ trôi mất, và người dùng không biết nên chờ hay
  // bấm gì (#2). Hỏi sau `finally` để nút Dịch đã mở khoá.
  if (thuLaiTomLuoc && await xacNhan("Chưa dựng được tóm lược",
      thuLaiTomLuoc,
      { ok: "Thử lại", cancel: "Để sau" })) {
    runTranslate();
  }

  function bar(f) { $("#progressBar").style.width = Math.round(f * 100) + "%"; }
}

/** Mọi đoạn của bài đã có bản dịch chưa (không tính khối đã ẩn).

    Khác `chunkDone`: hàm kia hỏi về MỘT mẻ và tôn trọng phần đang chọn, còn ở
    đây phải là cả bài — chọn câu đáng nhớ cho một bài mới dịch nửa chừng thì
    ngân sách tính trên số đoạn sai và model không thấy được mạch lập luận. */
/** Đặt nhãn cho nút Dịch theo việc CÒN LẠI, không theo việc đã làm.

    Bản cũ chỉ hỏi "đã dịch đoạn nào chưa", nên bài dịch xong 100% vẫn treo nút
    vàng to nhất màn hình ghi "Dịch tiếp". Người mới hỏi "dịch tiếp cái gì?" — và
    bấm thử. Giờ hết việc thì nút nói thẳng "Đã dịch xong" và lùi về dáng nút
    thường; còn việc (kể cả khi vừa bật thêm cột Giải thích) thì nó vàng lại.
    Tính theo đúng `chunkDone`, tức theo các cột đang bật và phần đang chọn. */
function capNhatNutDich() {
  const btn = $("#translateBtn");
  if (!btn || !state.doc || state.translating) return;
  const coBan = Object.keys(state.doc.translations || {}).length > 0;
  let conViec = false;
  for (let i = 0; i < state.chunks; i++) if (!chunkDone(i)) { conViec = true; break; }
  btn.textContent = !coBan ? "Dịch" : conViec ? "Dịch tiếp" : "✓ Đã dịch xong";
  btn.classList.toggle("da-xong", coBan && !conViec);
  btn.title = coBan && !conViec
    ? "Mọi đoạn đã có bản dịch cho các cột đang bật. Bật thêm cột Giải thích, hoặc chọn mục khác ở nút ▾, thì nút này sáng lại."
    : "";
}

function allTranslated() {
  if (state.nguonViet) return true;
  const tr = state.doc.translations || {};
  const b = (state.doc.blocks || []).filter(
    (x) => (x.type === "para" || x.type === "caption") && !x.hidden && x.translate);
  return b.length > 0 && b.every((x) => (tr[x.id] || "").trim());
}

/* Một mẻ đã dịch xong chưa — dùng đúng kế hoạch chia mẻ do server trả về,
   nhờ vậy bấm "Dịch tiếp" không dịch lại (và không trả tiền lại) phần đã xong. */
function chunkDone(i) {
  const ids = state.doc.chunk_ids?.[i];
  if (!ids?.length) return false;
  const tr = state.doc.translations, pl = state.doc.plain || {};
  const wantVi = $("#colVi").checked, wantGl = $("#colGl").checked;
  const type = Object.fromEntries(state.doc.blocks.map((b) => [b.id, b.type]));
  // heading và công thức cố ý không có cột giải thích — đừng đòi chúng,
  // nếu không mẻ nào cũng bị coi là chưa xong và dịch lại từ đầu
  const needsGl = (id) => ["para", "caption"].includes(type[id]);
  // Khi dịch từng phần, mẻ chỉ cần xong PHẦN ĐÃ CHỌN của nó — không thì mẻ nào
  // cũng bị coi là dở dang và vòng dịch chạy lại vô ích.
  const only = pickedIds();
  const need = only ? ids.filter((id) => only.has(id)) : ids;
  if (!need.length) return true;
  return need.every((id) =>
    (!wantVi || tr[id]) && (!wantGl || !needsGl(id) || pl[id]));
}

function streamChunk(i, refine, mode, only) {
  return new Promise((resolve, reject) => {
    const q = only && only.size ? `&only=${[...only].join(",")}` : "";
    const es = new EventSource(
      `/api/doc/${state.doc.id}/translate?chunk=${i}&refine=${refine}&mode=${mode}${q}`);
    // `EventSource` không nhận `AbortSignal`, nên nối tay vào cùng một nút Dừng:
    // đóng kênh là server thấy client ngắt và dừng stream. Khối đã nhận vẫn còn
    // vì mỗi khối được lưu ngay lúc tới.
    const huy = () => { try { es.close(); } catch {} ; reject(new DOMException("Đã dừng", "AbortError")); };
    state.bo?.signal.addEventListener("abort", huy, { once: true });
    const xong = () => state.bo?.signal.removeEventListener("abort", huy);
    es.addEventListener("block", (e) => {
      const { id, vi, plain } = JSON.parse(e.data);
      if (plain !== undefined) {
        (state.doc.plain ||= {})[id] = plain;
        const g = $(`#p-${CSS.escape(id)} [data-gl]`);
        if (g) g.innerHTML = sciGoiKhoi(plain);
        return;
      }
      state.doc.translations[id] = vi;
      const cell = $(`#p-${CSS.escape(id)} [data-vi]`);
      if (cell) { cell.innerHTML = sci(vi); cell.classList.remove("pending"); }
    });
    es.addEventListener("status", (e) => status(JSON.parse(e.data).msg));
    /* Model trả về ký tự thuộc hệ chữ lạ. Bản dịch vẫn hiện ra (người đọc cần
       thấy để sửa), nhưng nó KHÔNG được ghi vào bộ nhớ dịch — nằm trong đó thì
       nó quay lại mãi mãi. Đánh dấu khối để mắt tìm ra ngay. */
    es.addEventListener("warn", (e) => {
      const d = JSON.parse(e.data);
      status(d.msg);
      (d.blocks || []).forEach((id) => {
        const el = $(`#p-${CSS.escape(id)}`);
        if (el) el.classList.add("bad-script");
      });
    });
    es.addEventListener("done", (e) => {
      const d = JSON.parse(e.data);
      if (d.usage) { state.doc.usage = d.usage; renderUsage(); }
      xong(); es.close(); resolve(d);
    });
    es.addEventListener("error", (e) => {
      es.close();
      let msg = "mất kết nối tới server";
      try { msg = JSON.parse(e.data).message; } catch {}
      reject(new Error(msg));
    });
  });
}

function status(msg) {
  const el = $("#statusLine");
  el.textContent = msg;
  el.classList.remove("hidden");
}

/* --------------------------------------------------- giải thích đoạn */

async function explainBlock(id) {
  const pair = $(`#p-${CSS.escape(id)}`);
  const shown = $(".note", pair);
  if (shown) return shown.classList.remove("collapsed");   // đang thu gọn -> mở ra
  // đã giải thích ở phiên trước và còn trong DB -> dựng lại, không gọi model
  if (state.doc.notes[id]) {
    pair.insertAdjacentHTML("beforeend", noteHTML(state.doc.notes[id]));
    hydrateDiagrams(pair);
    return;
  }
  pair.insertAdjacentHTML("beforeend",
    `<div class="note" data-loading><h4>Giải thích lập luận</h4><p class="muted"><span class="spin">◐</span> đang phân tích…</p></div>`);
  try {
    const r = await fetch(`/api/doc/${state.doc.id}/explain/${id}`, { method: "POST" });
    if (!r.ok) throw new Error(await apiErr(r, "lỗi"));
    const { note, run, total } = await r.json();
    state.doc.notes[id] = note;
    $(".note[data-loading]", pair).outerHTML = noteHTML(note);
    hydrateDiagrams(pair);
    reportCost("Giải thích xong", run, total);
  } catch (e) {
    $(".note[data-loading]", pair).innerHTML =
      `<h4>Giải thích lập luận</h4><p class="err">${esc(e.message)}</p>`;
  }
}

/** Dịch lại một khối. Tốn một lượt gọi model nhỏ, nên phải hỏi và nói rõ giá.

    Có nút này vì cảnh báo rò hệ chữ đã bảo người dùng "dịch lại khối đó" mà
    không có đường nào làm việc ấy. Gặp thật trên bài CIRAG: một đoạn ra
    `либо thiếu thông tin để suy luận, либо nhận quá nhiều nhiễu` — chữ Cyrillic
    thay cho "hoặc" — và cách duy nhất là gõ tay lại cả đoạn. */
async function redoBlock(id) {
  const pair = $(`#p-${CSS.escape(id)}`);
  const cell = $("[data-vi]", pair);
  if (!cell) return;
  if (!await xacNhan("Dịch lại đoạn này?",
    "TỐN TIỀN: một lượt gọi model. Rẻ nếu bài vừa dịch xong — toàn văn còn trong "
    + "cache; nhưng nếu đã lâu thì phải đọc lại cả bài, đo thật khoảng $0,03.\n\n"
    + "Bản dịch cũ của đoạn bị bỏ khỏi bộ nhớ dịch nên không quay lại nữa, và "
    + "lượt mới chạy ở nhiệt độ cao hơn để không ra đúng kết quả cũ.\n\n"
    + "Vệt bôi vàng trên đoạn này sẽ bị xoá — khoảng ký tự cũ không còn khớp "
    + "bản dịch mới.", { ok: "Dịch lại" })) return;
  const cu = cell.innerHTML;
  cell.innerHTML = `<span class="pending"><span class="spin">◐</span> đang dịch lại…</span>`;
  try {
    // `colMode()` quyết cột nào đang bật, nên không sinh ra cột người dùng đã
    // tắt — tức không trả tiền cho nó. Cùng luật với `streamChunk`.
    const r = await fetch(`/api/doc/${state.doc.id}/retranslate/${id}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ mode: colMode() }),
    });
    if (!r.ok) throw new Error(await apiErr(r, "lỗi"));
    const res = await r.json();
    if (res.vi) state.doc.translations[id] = res.vi;
    if (res.plain) (state.doc.plain ||= {})[id] = res.plain;
    delete (state.doc.highlights || {})[id];
    cell.innerHTML = res.vi ? sci(res.vi) : `<span class="pending">chưa dịch</span>`;
    const gl = $("[data-gl]", pair);
    if (gl && res.plain) gl.innerHTML = renderMd(res.plain);
    paintHighlights(pair);
    reportCost("Dịch lại xong", res.run, res.usage);
    if (res.warn) status("⚠ " + res.warn);
  } catch (e) {
    cell.innerHTML = cu;
    status("Lỗi: " + e.message);
  }
}

/* ------------------------------------------------------------ hỏi đáp */

/* Model trả lời bằng Markdown và LaTeX. Gán thẳng vào textContent thì người đọc
   thấy nguyên dấu sao, dấu gạch đầu dòng và \[ \]. Đây là bộ dựng tối giản, chỉ
   nhận đúng những thứ model hay dùng.

   An toàn: escape TOÀN BỘ trước, rồi mới chèn thẻ. Nhờ vậy dù model trả về thẻ
   HTML hay <script> thì chúng cũng chỉ là chữ, không chạy được. */

/** Dọn ký hiệu LaTeX thuần trình bày. Cố ý không dựng công thức — cả tool này
    vốn giữ công thức ở dạng chữ, không render LaTeX. */
/* Macro TeX → ký tự thật. Dạng lưu của công cụ này là `^{…}` / `_{…}`, nhưng
   model vẫn hay viết LaTeX kèm `\(…\)`; đo trên dữ liệu thật: 13 ô, toàn dạng
   `\(…\)` (không có `$…$`, không `\[`), với các macro `\in \tau \tilde \hat
   \cdot \theta \star \rightarrow \xi \pi \mid`. Không dựng thì người đọc
   thấy nguyên `\(Suf(a) \in \{0, 1\}\)` giữa câu tiếng Việt.

   Bảng này phải KHỚP với `_TEX` bên `server/main.py` — bản đang đọc trên màn
   hình và file xuất ra là hai đoạn code dựng cùng một nội dung.
   `test_bang_macro_tex_khop_nhau_giua_app_va_export` canh chỗ đó. */
const TEX = {
  alpha: "α", beta: "β", gamma: "γ", delta: "δ", epsilon: "ε", varepsilon: "ε",
  zeta: "ζ", eta: "η", theta: "θ", vartheta: "ϑ", iota: "ι", kappa: "κ",
  lambda: "λ", mu: "μ", nu: "ν", xi: "ξ", pi: "π", rho: "ρ", sigma: "σ",
  tau: "τ", upsilon: "υ", phi: "φ", varphi: "φ", chi: "χ", psi: "ψ", omega: "ω",
  Gamma: "Γ", Delta: "Δ", Theta: "Θ", Lambda: "Λ", Xi: "Ξ", Pi: "Π",
  Sigma: "Σ", Phi: "Φ", Psi: "Ψ", Omega: "Ω",
  in: "∈", notin: "∉", ni: "∋", subset: "⊂", subseteq: "⊆", supset: "⊃",
  supseteq: "⊇", cup: "∪", cap: "∩", emptyset: "∅", setminus: "∖",
  leq: "≤", le: "≤", geq: "≥", ge: "≥", neq: "≠", ne: "≠", approx: "≈",
  sim: "∼", simeq: "≃", equiv: "≡", propto: "∝", ll: "≪", gg: "≫",
  to: "→", rightarrow: "→", Rightarrow: "⇒", leftarrow: "←", Leftarrow: "⇐",
  leftrightarrow: "↔", mapsto: "↦", implies: "⇒", iff: "⇔",
  times: "×", div: "÷", cdot: "·", cdots: "⋯", ldots: "…", dots: "…",
  pm: "±", mp: "∓", ast: "∗", star: "⋆", circ: "∘", bullet: "∙",
  sum: "∑", prod: "∏", int: "∫", partial: "∂", nabla: "∇", infty: "∞",
  forall: "∀", exists: "∃", neg: "¬", lnot: "¬", land: "∧", lor: "∨",
  wedge: "∧", vee: "∨", oplus: "⊕", otimes: "⊗", perp: "⊥", angle: "∠",
  sqrt: "√", top: "⊤", bot: "⊥",
  mid: "|", parallel: "∥", langle: "⟨", rangle: "⟩", lVert: "‖", rVert: "‖",
  quad: " ", qquad: "  ", ",": " ", ";": " ", ":": " ", "!": "",
};

/* Dấu phụ đặt bằng ký tự tổ hợp, đứng SAU chữ — cùng cơ chế `_join_accents()`
   bên `parser.py` dùng để gộp dấu mũ rời của TeX. */
const TEX_ACCENT = { hat: "\u0302", tilde: "\u0303", bar: "\u0304", overline: "\u0304",
                     dot: "\u0307", ddot: "\u0308", vec: "\u20D7", check: "\u030C" };

/** LaTeX nội dòng → chữ thường + `<sup>`/`<sub>`. Nhận chuỗi ĐÃ escape. */
function mathTeX(s) {
  let out = s
    .replace(/\\(?:text|mathrm|mathit|mathbf|mathcal|mathbb|operatorname)\s*\{([^{}]*)\}/g, "$1")
    .replace(/\\frac\s*\{([^{}]*)\}\s*\{([^{}]*)\}/g, "($1)/($2)")
    // dấu phụ trước khi đổi macro, vì `\hat{x}` cần biết cả phần trong ngoặc
    .replace(/\\([A-Za-z]+)\s*\{([^{}]*)\}/g,
             (m, ten, arg) => (TEX_ACCENT[ten] ? arg + TEX_ACCENT[ten] : m))
    .replace(/\\([{}|])/g, "$1")
    .replace(/\\([A-Za-z]+)/g, (m, ten) => (ten in TEX ? TEX[ten] : m))
    .replace(/\\([,;:!])/g, (m, k) => TEX[k] ?? " ")
    .replace(/\\\s/g, " ");
  // Chỉ số dạng ngoặc, LẶP từ trong ra ngoài: `[^{}]*` chỉ khớp được lớp trong
  // cùng, nên `a^{(g_{DOC})}` mà làm một lượt thì `_{DOC}` bị ăn trước và ngoặc
  // ngoài không còn khớp — để lại `a^{(g<sub>DOC</sub>)}` nguyên dấu ngoặc.
  for (let i = 0; i < 4; i++) {
    const truoc = out;
    out = out
      .replace(/\^\{([^{}]*)\}/g, "<sup>$1</sup>")
      .replace(/_\{([^{}]*)\}/g, "<sub>$1</sub>");
    if (out === truoc) break;
  }
  out = out
    .replace(/\^([A-Za-z0-9])/g, "<sup>$1</sup>")
    .replace(/_([A-Za-z0-9])/g, "<sub>$1</sub>")
    // Vét cuối cho chỉ số LỒNG: `a^{(g^\star)}` thì luật ngoặc ăn cả cục nên
    // dấu `^` bên trong còn nguyên giữa câu. Chỉ chạy trong công thức, nên nhận
    // cả ký hiệu vừa đổi ra (`^⋆`) chứ không riêng chữ và số.
    .replace(/\^([^\s{}<])/g, "<sup>$1</sup>")
    .replace(/_([^\s{}<])/g, "<sub>$1</sub>");
  return out.trim();
}

function tidyMath(s) {
  return mathTeX(s);
}

/* Chỉ số dưới KHÔNG ngoặc, chỉ ở cột trả lời. Model viết `x_t`, `z_t`, `X_t+H`
   chứ không viết dạng lưu `x_{t}`, nên `_{…}` ở trên không bắt được và người đọc
   thấy nguyên dấu gạch dưới giữa câu.

   Luật cố ý HẸP, vì `snake_case` trông y hệt: gốc phải là **một chữ cái duy
   nhất**, chỉ số dài 1–2 ký tự. Nhờ đó `paper_id`, `chunk_id`, `t_max`,
   `source_block_ids` không bị chạm — nới ra là mọi tên biến trong câu trả lời
   hoá thành công thức. */
/** Nhãn đọc được cho khoá cảnh báo của các chốt chặn bên server (#26).
    Khoá như `thiếu_cơ_chế` là tên NỘI BỘ — viết vậy cho dễ grep, không phải
    để người dùng đọc. Khoá lạ chưa có trong bảng thì vẫn hiện, chỉ đổi `_`
    thành dấu cách, để cảnh báo mới không bị nuốt mất. `survey.js` dùng nhờ. */
const TEN_CANH_BAO = {
  "vòng_tròn": "Giải thích vòng tròn",
  "thiếu_phản_chứng": "Thiếu phản chứng",
  "thiếu_mục": "Thiếu mục",
  "thiếu_cơ_chế": "Thiếu cơ chế",
  "số_không_có_trong_bài": "Số không có trong bài",
  "số_bịa": "Số không có nguồn",
  "rò_hệ_chữ": "Lẫn chữ lạ",
  "nói_chung_chung": "Nói chung chung",
  "năm_lệch": "Năm lệch",
  "mã_đoạn_không_có": "Trích đoạn không có thật",
  "không_được_đỡ": "Nguồn không đỡ câu này",
  "giấu_thiếu": "Giấu chỗ chưa tìm ra",
  "cite_lạ": "Trích dẫn lạ",
  "chữ_hán": "Lẫn chữ Hán",
  "chưa_gom_được": "Chưa gom được bằng chứng",
  "câu_độn": "Câu độn",
  "bỏ_sót_bài": "Bỏ sót bài",
  "bài_lạ": "Bài không có trong kho",
};
/** Cắt chữ ở ranh giới từ, kèm "…" khi có cắt — bản client của
    `depth.cat_gon`. `slice(0, n)` trơn cắt giữa chữ mà không báo là đã cắt,
    nên người đọc tưởng chính văn bản bị hỏng (#11, #26). `survey.js` dùng nhờ. */
function catGon(s, n) {
  s = String(s || "").replace(/\s+/g, " ").trim();
  if (s.length <= n) return s;
  const cut = s.slice(0, n), sp = cut.lastIndexOf(" ");
  return (sp > n * 0.6 ? cut.slice(0, sp) : cut).replace(/[\s,.;:]+$/, "") + "…";
}
const tenCanhBao = (k) => TEN_CANH_BAO[k] || String(k || "").replace(/_/g, " ");

const _SUBSCRIPTISH = /(?<![\w`>])([A-Za-z])_([A-Za-z0-9](?:\+[A-Za-z0-9]{1,2})?)(?![\w{])/g;

function mdInline(s) {
  return s
    .replace(/\^\{([^{}]*)\}/g, "<sup>$1</sup>")
    .replace(/_\{([^{}]*)\}/g, "<sub>$1</sub>")
    .replace(_SUBSCRIPTISH, "$1<sub>$2</sub>")
    .replace(/`([^`]+)`/g, "<code>$1</code>")
    .replace(/\*\*([^*]+)\*\*/g, "<b>$1</b>")
    .replace(/(?<![*\w])\*([^*\n]+)\*(?!\w)/g, "<i>$1</i>")
    .replace(/\\\((.+?)\\\)/g, (_, m) => `<span class="imath">${mathTeX(m)}</span>`)
    .replace(/\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g,
             '<a href="$2" target="_blank" rel="noopener">$1</a>');
}

function renderMd(src) {
  const out = [];
  let list = null, quote = false, fence = null, math = null, table = false;
  const shut = () => {
    if (list) { out.push(`</${list}>`); list = null; }
    if (quote) { out.push("</blockquote>"); quote = false; }
    if (table) { out.push("</tbody></table></div>"); table = false; }
  };

  for (const raw of esc(src).split("\n")) {
    const line = raw.trimEnd();

    if (fence !== null) {                       // đang trong khối mã
      if (line.trim().startsWith(fence)) { out.push("</code></pre>"); fence = null; }
      else out.push(line);
      continue;
    }
    if (math !== null) {                        // đang trong công thức khối
      if (line.trim() === math) { out.push("</div>"); math = null; }
      else out.push(tidyMath(line));
      continue;
    }

    const f = line.match(/^\s*(```|~~~)/);
    if (f) { shut(); fence = f[1]; out.push("<pre><code>"); continue; }
    if (/^\s*(\\\[|\$\$)\s*$/.test(line)) {
      shut();
      math = line.trim() === "$$" ? "$$" : "\\]";
      out.push('<div class="math">');
      continue;
    }
    if (!line.trim()) { shut(); continue; }
    if (/^\s*(-{3,}|\*{3,})\s*$/.test(line)) { shut(); out.push("<hr>"); continue; }

    const h = line.match(/^\s*(#{1,6})\s+(.*)$/);
    if (h) { shut(); out.push(`<b class="mdh">${mdInline(h[2])}</b>`); continue; }

    const q = line.match(/^\s*&gt;\s?(.*)$/);   // '>' đã bị escape thành &gt;
    if (q) {
      if (list) { out.push(`</${list}>`); list = null; }
      if (!quote) { out.push("<blockquote>"); quote = true; }
      out.push(mdInline(q[1]) + "<br>");
      continue;
    }
    if (quote) { out.push("</blockquote>"); quote = false; }

    // Bảng Markdown. Thiếu nhánh này thì câu trả lời so sánh nhiều bài hiện ra
    // nguyên dấu gạch đứng và hàng `|---|---|` — mà `prompts.py` lại BẢO model
    // dùng bảng khi so từ ba bài trở lên, nên đây là đường hay đi. `svMd` bên
    // kho survey đã dựng bảng đúng từ đầu; chỗ này bị sót.
    if (/^\s*\|.*\|\s*$/.test(line)) {
      if (/^[\s|:\-]+$/.test(line)) continue;        // hàng kẻ phân cách
      const cells = line.trim().slice(1, -1).split("|").map((c) => mdInline(c.trim()));
      if (!table) {
        if (list) { out.push(`</${list}>`); list = null; }
        out.push('<div class="mdtable"><table><thead><tr>'
          + cells.map((c) => `<th>${c}</th>`).join("") + "</tr></thead><tbody>");
        table = true;
      } else {
        out.push("<tr>" + cells.map((c) => `<td>${c}</td>`).join("") + "</tr>");
      }
      continue;
    }
    if (table) { out.push("</tbody></table></div>"); table = false; }

    const li = line.match(/^\s*([-*+]|\d+[.)])\s+(.*)$/);
    if (li) {
      const kind = /^\d/.test(li[1]) ? "ol" : "ul";
      if (list && list !== kind) { out.push(`</${list}>`); list = null; }
      if (!list) { out.push(`<${kind}>`); list = kind; }
      out.push(`<li>${mdInline(li[2])}</li>`);
      continue;
    }
    if (list) { out.push(`</${list}>`); list = null; }
    out.push(`<p>${mdInline(line)}</p>`);
  }
  if (fence !== null) out.push("</code></pre>");
  if (math !== null) out.push("</div>");
  shut();
  return out.join("");
}

async function sendQuestion() {
  const input = $("#chatInput");
  const q = input.value.trim();
  if (!q) return;
  input.value = "";
  const log = $("#chatLog");
  log.insertAdjacentHTML("beforeend", `<div class="msg user">${esc(q)}</div>`);
  const bot = document.createElement("div");
  bot.className = "msg bot streaming";
  log.appendChild(bot);
  log.scrollTop = log.scrollHeight;

  // Dựng lại Markdown mỗi khung hình chứ không mỗi token — câu trả lời dài thì
  // dựng theo từng token vừa tốn vừa giật.
  //
  // `answer` phải khai Ở ĐÂY, cùng phạm vi với `paint`. Bản trước khai nó bằng
  // `let` bên TRONG khối `try`, mà `paint` được định nghĩa bên ngoài — hai phạm
  // vi khác nhau, nên mỗi khung hình `paint` chạy là ném
  // `ReferenceError: answer is not defined`, và cả phần hỏi đáp trong bài chết.
  let painting = false;
  let answer = "";
  const paint = () => {
    painting = false;
    bot.innerHTML = renderMd(answer);
    log.scrollTop = log.scrollHeight;
  };

  try {
    const r = await fetch(`/api/doc/${state.doc.id}/ask`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ question: q, history: state.history }),
    });
    const reader = r.body.getReader();
    const dec = new TextDecoder();
    let buf = "";
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      buf += dec.decode(value, { stream: true });
      const frames = buf.split("\n\n");
      buf = frames.pop();
      for (const f of frames) {
        const ev = /^event: (.+)$/m.exec(f)?.[1];
        const data = /^data: (.*)$/m.exec(f)?.[1];
        if (!data) continue;
        if (ev === "delta") answer += JSON.parse(data).t;
        else if (ev === "usage") { const u = JSON.parse(data); reportCost("Trả lời xong", u.run, u.total); }
        else if (ev === "error") answer += "\n\n**[lỗi]** " + JSON.parse(data).message;
        // Rò hệ chữ: pass dịch đã soát từ lâu, pass hỏi đáp thì chưa — và nó rò
        // thật, một câu trả lời đúng nội dung có chữ `तथा` (Hindi) nằm giữa câu
        // tiếng Việt. Gắn cờ lên chính bong bóng đó để mắt tìm ra ngay.
        else if (ev === "warn") {
          const w = JSON.parse(data);
          bot.classList.add("bad-script");
          bot.dataset.warn = w.msg || "";
          status("⚠ " + (w.msg || "Câu trả lời lẫn ký tự lạ."));
        }
        else continue;
        if (!painting) { painting = true; requestAnimationFrame(paint); }
      }
    }
    paint();
    bot.classList.remove("streaming");
    state.history.push({ role: "user", content: q }, { role: "assistant", content: answer });
    renderUsage();
  } catch (e) {
    bot.classList.remove("streaming");
    bot.textContent = "Lỗi: " + e.message;
  }
}
