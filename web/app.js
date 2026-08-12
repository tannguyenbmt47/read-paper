/* Loupe — giao diện. */

const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];
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
  .replace(/\^\{([^{}]*)\}/g, "<sup>$1</sup>")
  .replace(/_\{([^{}]*)\}/g, "<sub>$1</sub>")
  .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>"));

/* "Figure 2" trong đoạn văn nhưng hình lại nằm cách đó mấy trang — biến mọi
   tham chiếu thành chỗ bấm được để xem ngay hình đó, khỏi mất chỗ đang đọc. */
const REF_RE = /\b(Figures?|Figs?\.?|Tables?|Algorithms?|Hình|Bảng|Thuật toán)\s*(\d{1,2})\b/gi;

function refs(html) {
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
}

function closeFigPeek() { $("#figPeek").classList.add("hidden"); }

const state = {
  doc: null, chunks: 0, translating: false, stopping: false,
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
  const dark = matchMedia("(prefers-color-scheme: dark)").matches;
  // Trên màn slide, sơ đồ phải theo bảng màu của deck — màu mặc định của
  // mermaid là tím lavender, lạc hẳn khỏi navy/xanh của phần còn lại.
  const onSlides = !$("#slides")?.classList.contains("hidden");
  mermaid.initialize({
    startOnLoad: false,
    securityLevel: "strict",
    theme: onSlides ? "base" : (dark ? "dark" : "neutral"),
    themeVariables: onSlides ? {
      primaryColor: "#e9eefc", primaryBorderColor: "#2563eb",
      primaryTextColor: "#0f172a", lineColor: "#64748b",
      secondaryColor: "#ddf3f5", tertiaryColor: "#e4f5ea",
      fontFamily: "Helvetica Neue,Arial,sans-serif", fontSize: "15px",
    } : undefined,
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
  loadRecent();
  loadDbStats();
  wireStart();
  wireReader();
  wireCrop();
  wireViewMenu();
  wireFind();
  wirePdfPane();
  wireHighlights();
  wireSlides();
  wirePresent();
  const id = location.hash.slice(1);
  if (id) openDoc(id).catch(() => (location.hash = ""));
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
  if (!r.ok) throw new Error((await r.json()).detail || "Không đổi được model");
  state.doc.model = id;
  $("#revModel").value = id;      // hai ô chọn ở hai màn hình luôn khớp nhau
  $("#docModel").value = id;
}

async function loadDbStats() {
  try {
    const d = await fetch("/api/db/stats").then((r) => r.json());
    if (!d.tm_entries && !d.parse_cached) return;
    $("#dbStats").textContent =
      `Kho đã lưu: ${d.documents} bài · ${d.parse_cached} file đã bóc tách sẵn · ` +
      `${d.tm_entries} đoạn trong bộ nhớ dịch (đã dùng lại ${d.tm_hits} lượt) · ` +
      `${(d.db_bytes / 1048576).toFixed(1)} MB`;
  } catch { /* không có thì thôi */ }
}

async function loadRecent() {
  const docs = await fetch("/api/docs").then((r) => r.json());
  $("#recentWrap").classList.toggle("hidden", !docs.length);
  $("#recentList").innerHTML = docs
    .map((d) => {
      const pct = d.translatable ? Math.round((d.translated / d.translatable) * 100) : 0;
      return `<li>
        <span class="rt" data-id="${esc(d.id)}">
          <b>${esc(d.title_vi || d.title)}</b>
          <span>${d.blocks} khối · đã dịch ${pct}% · ${esc(d.model || "")}</span>
        </span>
        <button class="icon-btn" data-ren="${esc(d.id)}" title="Đổi tên bài">✎</button>
        <button class="icon-btn" data-del="${esc(d.id)}" title="Xoá">🗑</button>
      </li>`;
    })
    .join("");
  $$("#recentList .rt").forEach((el) => (el.onclick = () => openDoc(el.dataset.id)));
  /* Tiêu đề đoán từ khối đầu trang nên hay sai — dính tên hội nghị, dính số
     trang, hoặc cụt còn vài chữ. Nó hiện ở danh sách này, ở đầu bản xuất ra và
     ở slide tiêu đề, nên sai một chỗ là sai khắp nơi. Đổi tên không đụng nội
     dung: `title` không nằm trong `cached_prefix` nên không có bản dịch nào
     phải bỏ đi. */
  $$("#recentList [data-ren]").forEach((el) => (el.onclick = async () => {
    const id = el.dataset.ren;
    const cur = docs.find((d) => d.id === id) || {};
    const title = prompt("Tên bài:", cur.title_vi || cur.title || "");
    if (!title || !title.trim()) return;
    await fetch(`/api/doc/${id}/title`, {
      method: "PATCH", headers: { "content-type": "application/json" },
      body: JSON.stringify({ title: title.trim() }),
    });
    loadRecent();
  }));
  $$("#recentList [data-del]").forEach((el) => (el.onclick = async () => {
    if (!confirm("Xoá bài này khỏi máy?")) return;
    await fetch(`/api/doc/${el.dataset.del}`, { method: "DELETE" });
    loadRecent();
  }));
}

/* ================================================ màn hình nhập ===== */

function wireStart() {
  $$(".tab").forEach((t) => (t.onclick = () => {
    $$(".tab").forEach((x) => x.classList.toggle("is-on", x === t));
    $$("[data-pane]", $("#start")).forEach((p) =>
      p.classList.toggle("hidden", p.dataset.pane !== t.dataset.tab));
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
  if (active === "file" && f) fd.append("file", f);
  else if (active === "url") fd.append("url", $("#urlInput").value);
  else if (active === "text") fd.append("text", $("#textInput").value);
  else return showErr("Chưa chọn nguồn nào.");

  btn.disabled = true; btn.textContent = "Đang đọc tài liệu…";
  // mở kênh tiến trình TRƯỚC khi POST, không thì mất mấy bước đầu
  const prog = impStart();
  fd.append("job", prog.job);
  await new Promise((r) => setTimeout(r, 120));   // chờ SSE bắt tay xong
  try {
    const r = await fetch("/api/import", { method: "POST", body: fd });
    if (!r.ok) throw new Error((await r.json()).detail || r.statusText);
    const doc = await r.json();
    prog.done(true);
    location.hash = doc.id;
    mountReview(doc);          // bước 1 trước, dịch sau
  } catch (e) {
    prog.done(false);
    showErr(e.message);
  } finally {
    btn.disabled = false; btn.textContent = "Nạp bài báo";
  }
  function showErr(m) { err.textContent = m; err.classList.remove("hidden"); }
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
  ["start", "review", "reader", "slides", "survey"].forEach((s) =>
    $("#" + s).classList.toggle("hidden", s !== id));
  syncRail(id);
}

/* Thanh bên trái phải luôn chỉ đúng công cụ đang mở, kể cả khi màn hình đổi từ
   chỗ khác (mở bài từ #doc= trên thanh địa chỉ, bấm nút quay lại…). Nên đồng bộ
   ở ĐÂY chứ không ở chỗ bấm nút — chỗ bấm nút chỉ là một trong nhiều đường vào. */
function syncRail(id) {
  const tool = id === "survey" ? "survey" : "doc";
  $$(".rail-item").forEach((b) => b.classList.toggle("is-on", b.dataset.tool === tool));
}

function mountReview(doc) {
  state.doc = doc;
  showScreen("review");
  $("#revTitle").textContent = doc.title || doc.source || "";
  fillModels($("#revModel"), doc.model, { short: true });
  renderReview();
  loadEstimate();
}

async function loadEstimate() {
  const box = $("#revStats");
  box.innerHTML = `<div class="stat"><b>…</b><span>đang ước lượng</span></div>`;
  try {
    const e = await fetch(`/api/doc/${state.doc.id}/estimate`).then((r) => r.json());
    const money = e.cost_usd == null
      ? `<div class="stat"><b>—</b><span>không lấy được giá của model</span></div>`
      : `<div class="stat warn-stat"><b>$${e.cost_usd.toFixed(3)}</b><span>ước tính cho bước 2</span></div>`;
    box.innerHTML =
      `<div class="stat"><b>${e.blocks_to_translate}</b><span>khối sẽ dịch / ${e.blocks_total} khối</span></div>` +
      `<div class="stat"><b>${e.figures}</b><span>hình &amp; bảng</span></div>` +
      `<div class="stat"><b>${(e.source_chars / 1000).toFixed(1)}k</b><span>ký tự gốc · ${e.chunks} mẻ dịch</span></div>` +
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
  $("#figHint").textContent = !figs.length
    ? "Không bóc được hình nào — PDF có thể là bản scan, hoặc bài không có hình."
    : hasPdf
      ? src + "Hình nào cắt sai thì bấm Chỉnh khung để tự kéo lại, hoặc Bỏ hình."
      : "Bài nhập bằng cách dán văn bản nên không chỉnh khung được.";

  $("#revFigs").innerHTML = figs.map((b) => `
    <div class="figcard ${b.figure_manual ? "manual" : ""}" data-fig="${esc(b.id)}">
      ${b.figure
        ? `<img src="/api/doc/${esc(state.doc.id)}/img/${esc(b.figure)}.png?v=${esc((b.figure_rect || []).join("_"))}"`
          + ` alt="" loading="lazy" decoding="async">`
        : `<div class="cap" style="padding:1.4rem;text-align:center">Chưa cắt được hình cho chú thích này</div>`}
      <div class="cap">${esc(b.text.slice(0, 130))}</div>
      <div class="act">
        ${b.figure_page >= 0 ? `<button data-crop="${esc(b.id)}">✂ Chỉnh khung</button>` : ""}
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
  $("#revAllCount").textContent = all.length;
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
      <button data-merge="${esc(b.id)}" title="Gộp với khối ngay sau — dùng khi một đoạn bị cắt làm đôi">⇓</button>
      <button data-split="${esc(b.id)}" title="Tách khối này làm hai — dùng khi hai đoạn bị dính">✂</button>
      <button data-dropblk="${esc(b.id)}" title="Bỏ hẳn khối khỏi bài">🗑</button>
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
  if (!r.ok) throw new Error((await r.json()).detail || "không sửa được");
  state.doc = await r.json();
  renderReview();
  loadEstimate();
}

function wireBlockEdits() {
  $$("#review [data-dropblk]").forEach((el) => (el.onclick = async () => {
    const b = state.doc.blocks.find((x) => x.id === el.dataset.dropblk);
    if (!confirm(`Bỏ hẳn khối này khỏi bài?\n\n${(b?.text || "").slice(0, 200)}`)) return;
    await patchBlocks({ drop: [el.dataset.dropblk] });
    renderReview();
    loadEstimate();
  }));

  $$("#review [data-merge]").forEach((el) => (el.onclick = async () => {
    const id = el.dataset.merge;
    const i = state.doc.blocks.findIndex((x) => x.id === id);
    const nxt = state.doc.blocks[i + 1];
    if (!nxt) return alert("Đây là khối cuối, không có gì để gộp vào.");
    const warn = nxt.type !== state.doc.blocks[i].type
      ? `\n\nLưu ý: hai khối khác loại (${state.doc.blocks[i].type} + ${nxt.type}).` : "";
    if (!confirm(`Gộp khối này với khối ngay sau?${warn}\n\n…${
      state.doc.blocks[i].text.slice(-90)}\n+\n${nxt.text.slice(0, 90)}…`)) return;
    try { await editBlocks("/blocks/merge", { ids: [id, nxt.id] }); }
    catch (e) { alert(e.message); }
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
    catch (e) { alert(e.message); }
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
    if (!r.ok) throw new Error((await r.json()).detail || "không tải được trang");
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
      if (!r.ok) throw new Error((await r.json()).detail || "lỗi");
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
  state.history = [];
  state.session = 0;
  showScreen("reader");
  $("#docTitleVi").textContent = doc.brief?.title_vi || doc.title || "(không tiêu đề)";
  $("#docTitleEn").textContent = doc.title && doc.brief?.title_vi ? doc.title : doc.source || "";
  fillModels($("#docModel"), doc.model, { short: true });
  // bài dán bằng văn bản thì không có PDF gốc để đối chiếu
  $("#pdfBtn").classList.toggle("hidden", !doc.has_pdf);
  $("#pdfPane").classList.add("hidden");
  closeFigPeek();
  Object.assign(pdfv, { page: -1, pages: 0 });
  buildFigIndex();          // phải dựng trước renderDoc, vì sci() tra bảng này
  renderDoc();
  renderSide();
  renderUsage();
  const done = Object.keys(doc.translations || {}).length;
  $("#translateBtn").textContent = done ? "Dịch tiếp" : "Dịch";
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
      if (!r.ok) throw new Error((await r.json()).detail || "không căn chỉnh được");
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
    if (!confirm("Bóc lại bài từ file PDF gốc bằng bộ bóc mới nhất?\n\n"
      + "Miễn phí, không gọi model. Bản dịch, ghi chú và vệt bôi vàng giữ nguyên "
      + "— khối được ghép lại theo nội dung. Phần chữ mới nhặt về sẽ chưa có bản "
      + "dịch; bấm Dịch tiếp là xong, và đoạn nào từng dịch rồi thì lấy lại miễn phí.")) return;
    btn.disabled = true;
    const old = btn.textContent;
    btn.textContent = "Đang bóc lại…";
    try {
      const r = await fetch(`/api/doc/${state.doc.id}/reparse`, { method: "POST" });
      if (!r.ok) throw new Error((await r.json()).detail || "không bóc lại được");
      const res = await r.json();
      mountDoc(res.doc);
      const st = res.stats;
      status(`Bóc lại xong: ${st.blocks} khối · giữ ${st.kept} bản dịch cũ · `
        + `${st.new} khối mới` + (st.to_translate ? ` · ${st.to_translate} khối chờ dịch` : "")
        + (st.dropped ? ` · bỏ ${st.dropped} khối không còn` : ""));
    } catch (e) {
      status("Lỗi: " + e.message);
    } finally {
      btn.disabled = false;
      btn.textContent = old;
    }
  };

  $("#rebriefBtn").onclick = async () => {
    const btn = $("#rebriefBtn");
    if (!confirm("Đọc lại toàn bài để chốt lại bảng thuật ngữ?\n\n" +
                 "Tốn một lượt gọi model. Phần đã dịch giữ nguyên — muốn dịch lại " +
                 "theo bảng mới thì bấm Dịch tiếp sau khi xoá bộ nhớ dịch.")) return;
    btn.disabled = true;
    const old = btn.textContent;
    btn.textContent = "Đang đọc toàn bài…";
    try {
      const r = await fetch(`/api/doc/${state.doc.id}/brief`, { method: "POST" });
      if (!r.ok) throw new Error((await r.json()).detail || "không dựng được");
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
function untranslatedCount() {
  const d = state.doc;
  if (!d?.blocks?.length) return 0;
  return d.blocks.filter((b) => b.translate && !b.hidden && !d.translations?.[b.id]).length;
}

function syncSlidesBtn() {
  const btn = $("#slidesBtn");
  const n = untranslatedCount();
  // KHÔNG khoá nút nữa. Khoá cứng thì bấm vào chẳng có gì xảy ra và người dùng
  // không hiểu vì sao — đã vấp thật với một bài thiếu đúng 2 khối
  // "Acknowledgments". Cho vào màn slide thoải mái, chỉ cảnh báo ở NÚT DỰNG,
  // vì chỗ tốn tiền và chỗ model dễ bịa là lúc dựng chứ không phải lúc xem.
  btn.disabled = false;
  btn.title = n === 0
    ? "Dựng bộ slide để trình bày lại bài này"
    : `Bộ slide — còn ${n} khối chưa dịch, dựng lúc này thì phần đó model tự viết lấy`;
  btn.classList.toggle("warn-dot", n > 0);
}

function openSlides() {
  showScreen("slides");
  $("#slDocTitle").textContent = state.doc.brief?.title_vi || state.doc.title || "";
  renderStrip();
  // Chưa có slide thì mở thẳng bước 1 — đó là việc phải làm trước, và mở vào
  // một dải slide trống chỉ khiến người dùng không biết bắt đầu từ đâu.
  const has = deckOf("deck").length || deckOf("backup").length;
  slTab(has ? pref("slTab", "deck") : "outline");
  const nMiss = untranslatedCount();
  slStatus(nMiss
    ? `Còn ${nMiss} khối chưa dịch. Dựng slide lúc này thì phần đó model tự viết `
      + `lấy — nên dịch nốt trước, hoặc ẩn mấy khối không cần.`
    : "");
  // đo tỉ lệ ảnh rồi vẽ lại: `slideLayout` cần biết ảnh ngang hay vuông mới
  // chọn được bố cục, mà trước khi đo xong thì nó tạm đoán là ảnh ngang
  measureFigures().then(() => { renderStrip(); if (state.slideSel) selectSlide(state.slideSel); });
}

/** Cắt ở ranh giới từ — cắt giữa chữ ra “trả lời câu hỏ”, trông như lỗi. */
function clip(s, n) {
  s = String(s ?? "").trim();
  if (s.length <= n) return s;
  return (s.slice(0, n).replace(/\s+\S*$/, "") || s.slice(0, n))
    .replace(/[\s,;:.]+$/, "") + "…";
}

/** Hình dùng được, dựng từ chính blocks — cùng nguồn với `_figure_catalog` server. */
function figChoices() {
  return state.doc.blocks
    .filter((b) => b.figure && b.type !== "equation")
    .map((b) => ({
      id: b.figure,
      label: `tr.${b.page ?? "?"} · ${(state.doc.translations?.[b.id] || b.text || "").slice(0, 60)}`,
    }));
}

const deckOf = (key) => (state.doc.slides?.[key] || []);
const findSlide = (sid) => {
  for (const key of ["deck", "backup"]) {
    const i = deckOf(key).findIndex((s) => s.id === sid);
    if (i >= 0) return { key, i, sl: state.doc.slides[key][i] };
  }
  return null;
};

const LAY_LABEL = {
  title: "tiêu đề", agenda: "mục lục", section: "vách ngăn", closing: "kết",
  cards: "thẻ", split: "hai cột", figside: "chữ + ảnh",
  figwide: "ảnh ngang", figfull: "ảnh lớn", list: "danh sách",
};

function renderStrip() {
  const sl = state.doc.slides || {};
  const has = (sl.deck?.length || 0) + (sl.backup?.length || 0) > 0;
  $("#slEmpty").classList.toggle("hidden", has);
  $("#slGenBtn").textContent = has ? "Dựng lại từ dàn ý · tốn tiền" : "Dựng slide · tốn tiền";
  $("#slOutlineBtn").textContent = outlineOf() ? "Soạn lại nội dung · tốn ít"
                                              : "Soạn nội dung · tốn ít";

  const item = (s, n) => {
    const lay = slideLayout(s);
    const nav = lay === "agenda" || lay === "section";
    const tags = [];
    if (s.warn?.length) tags.push(`<span class="sl-tag bad">${s.warn.length} cảnh báo</span>`);
    if (s.stale) tags.push(`<span class="sl-tag old">đoạn nguồn đã đổi</span>`);
    if (s.edited) tags.push(`<span class="sl-tag">đã sửa tay</span>`);
    tags.push(`<span class="sl-tag">${LAY_LABEL[lay]}</span>`);
    return `<li class="sl-item${nav ? " is-nav" : ""}${s.id === state.slideSel ? " is-on" : ""}"
              data-sid="${esc(s.id)}">
      <span class="n">${esc(n)}</span>
      <span class="h">${sci(s.headline || "(chưa có tiêu đề)")}
        ${tags.length ? `<span class="tags">${tags.join("")}</span>` : ""}</span>
    </li>`;
  };

  // Vách ngăn chia dải bên trái thành từng phần, đúng như nó chia buổi nói —
  // nhìn dải là thấy ngay bộ xương của deck.
  const rows = [];
  let part = 0;
  deckOf("deck").forEach((s, i) => {
    if (slideLayout(s) === "section") {
      part += 1;
      rows.push(`<li class="sl-sep">Phần ${part}</li>`);
    }
    rows.push(item(s, i + 1));
  });
  $("#slList").innerHTML = rows.join("");
  const bk = deckOf("backup");
  $("#slBackupWrap").classList.toggle("hidden", !bk.length);
  $("#slBackupCount").textContent = bk.length ? `(${bk.length})` : "";
  $("#slBackupList").innerHTML = bk.map((s, i) => item(s, "D" + (i + 1))).join("");

  $$("#slStrip .sl-item").forEach((el) => {
    el.onclick = () => selectSlide(el.dataset.sid);
    el.draggable = true;
    el.ondragstart = (e) => {
      state.dragSid = el.dataset.sid;
      e.dataTransfer.effectAllowed = "move";
      el.classList.add("is-drag");
    };
    el.ondragend = () => {
      el.classList.remove("is-drag");
      $$("#slStrip .sl-item").forEach((x) => x.classList.remove("drop-before"));
    };
    el.ondragover = (e) => {
      if (!state.dragSid || state.dragSid === el.dataset.sid) return;
      e.preventDefault();
      el.classList.add("drop-before");
    };
    el.ondragleave = () => el.classList.remove("drop-before");
    el.ondrop = async (e) => {
      e.preventDefault();
      el.classList.remove("drop-before");
      const from = state.dragSid, to = el.dataset.sid;
      state.dragSid = null;
      if (!from || from === to) return;
      // dựng lại thứ tự cho cả hai ngăn rồi gửi lên — server là nơi chốt
      const order = {
        deck: deckOf("deck").map((x) => x.id),
        backup: deckOf("backup").map((x) => x.id),
      };
      for (const k of ["deck", "backup"]) order[k] = order[k].filter((i) => i !== from);
      for (const k of ["deck", "backup"]) {
        const at = order[k].indexOf(to);
        if (at >= 0) { order[k].splice(at, 0, from); break; }
      }
      try {
        await patchSlides({ order }, "Đã đổi thứ tự.");
        selectSlide(state.slideSel);
      } catch (err) { slStatus("Lỗi: " + err.message); }
    };
  });

  if (has && !findSlide(state.slideSel)) selectSlide(deckOf("deck")[0]?.id || bk[0]?.id);
  else if (!has) { $("#slStage").innerHTML = ""; $("#slEdit").classList.add("hidden"); }
}

function selectSlide(sid) {
  state.slideSel = sid;
  const hit = findSlide(sid);
  $$("#slStrip .sl-item").forEach((el) =>
    el.classList.toggle("is-on", el.dataset.sid === sid));
  if (!hit) return;
  renderSlide(hit.sl);
  fillEditor(hit);
}

/* Bản sao của `pipeline.slide_layout()` bên server. Bố cục suy ra TỪ NỘI DUNG
   chứ không hỏi model — model không biết trước slide rốt cuộc có bao nhiêu chữ.
   Sửa luật ở một bên phải sửa bên kia, không thì xem trước nói dối. */
const CARD_TINTS = ["#e9eefc", "#ddf3f5", "#e4f5ea", "#fdefe2"];
const CHIP_COLORS = ["#2563eb", "#0d9488", "#16a34a", "#ea580c"];
/* Bộ icon 24×24 — bản sao của `slide_theme.ICONS` bên server. Sửa một bên phải
   sửa bên kia, không thì xem trước khác file xuất ra. */
const ICONS = {
  target: "M12 2a10 10 0 1 0 0 20 10 10 0 0 0 0-20zm0 5a5 5 0 1 0 0 10 5 5 0 0 0 0-10zm0 3.5a1.5 1.5 0 1 1 0 3 1.5 1.5 0 0 1 0-3z",
  check: "M9 16.2 4.8 12l-1.4 1.4L9 19 21 7l-1.4-1.4z",
  warn: "M12 2 1 21h22zm0 6 7.5 13h-15zm-1 4v4h2v-4zm0 5v2h2v-2z",
  data: "M12 2c-4.4 0-8 1.3-8 3v14c0 1.7 3.6 3 8 3s8-1.3 8-3V5c0-1.7-3.6-3-8-3zm0 2c3.9 0 6 1 6 1s-2.1 1-6 1-6-1-6-1 2.1-1 6-1zm6 15s-2.1 1-6 1-6-1-6-1v-2.3c1.6.8 3.8 1.3 6 1.3s4.4-.5 6-1.3zm0-5s-2.1 1-6 1-6-1-6-1V9.7c1.6.8 3.8 1.3 6 1.3s4.4-.5 6-1.3z",
  chart: "M4 20h16v2H2V2h2zm3-2V9h3v9zm5 0V4h3v14zm5 0v-6h3v6z",
  eye: "M12 5C6 5 2 12 2 12s4 7 10 7 10-7 10-7-4-7-10-7zm0 12c-4 0-7-4-7.7-5C5 11 8 7 12 7s7 4 7.7 5C19 13 16 17 12 17zm0-8a3 3 0 1 0 0 6 3 3 0 0 0 0-6z",
  bolt: "M13 2 4 14h6l-1 8 9-12h-6z",
  gear: "M19.4 13a7.8 7.8 0 0 0 0-2l2-1.6-2-3.4-2.4 1a7.6 7.6 0 0 0-1.7-1L15 3H9l-.3 2.9a7.6 7.6 0 0 0-1.7 1l-2.4-1-2 3.4L4.6 11a7.8 7.8 0 0 0 0 2l-2 1.6 2 3.4 2.4-1a7.6 7.6 0 0 0 1.7 1L9 21h6l.3-2.9a7.6 7.6 0 0 0 1.7-1l2.4 1 2-3.4zM12 15.5a3.5 3.5 0 1 1 0-7 3.5 3.5 0 0 1 0 7z",
  layers: "m12 2 10 5.5-10 5.5L2 7.5zm0 12.3 8.1-4.4 1.9 1.1-10 5.5-10-5.5 1.9-1.1zm0 4.4 8.1-4.4 1.9 1.1-10 5.6-10-5.6 1.9-1.1z",
  link: "M10.6 13.4a1 1 0 0 1 0-1.4l1.4-1.4a1 1 0 0 1 1.4 1.4l-1.4 1.4a1 1 0 0 1-1.4 0zM7.8 16.2a4 4 0 0 1 0-5.7l2.8-2.8 1.4 1.4-2.8 2.8a2 2 0 0 0 2.9 2.9l2.8-2.8 1.4 1.4-2.8 2.8a4 4 0 0 1-5.7 0zm8.4-8.4a4 4 0 0 1 0 5.7l-2.8 2.8-1.4-1.4 2.8-2.8a2 2 0 0 0-2.9-2.9L9.1 9.9 7.7 8.5l2.8-2.8a4 4 0 0 1 5.7 0z",
  doc: "M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8zm-1 7V3.5L18.5 9zM8 13h8v2H8zm0 4h8v2H8z",
  search: "M15.5 14h-.8l-.3-.3a6.5 6.5 0 1 0-.7.7l.3.3v.8l5 5 1.5-1.5zm-6 0a4.5 4.5 0 1 1 0-9 4.5 4.5 0 0 1 0 9z",
};
const iconSvg = (n, sz = 22) => {
  const d = ICONS[String(n || "").trim().toLowerCase()];
  return d ? `<svg viewBox="0 0 24 24" width="${sz}" height="${sz}" fill="#fff"><path d="${d}"/></svg>` : "";
};
const tint = (i) => CARD_TINTS[i % CARD_TINTS.length];
const chipCol = (i) => CHIP_COLORS[i % CHIP_COLORS.length];

const img = (fig) =>
  `<img src="/api/doc/${state.doc.id}/img/${esc(fig)}.png" alt=""`
  + ` loading="lazy" decoding="async">`;

/* Bản sao của `pipeline.slide_layout()`. Bố cục suy ra TỪ NỘI DUNG, và với slide
   có hình thì còn theo TỈ LỆ ẢNH THẬT: ảnh ngang cho tràn khung, ảnh vuông/dọc
   thì xếp hai cột. Tỉ lệ đo bằng `state.figAR` (nạp dần khi ảnh hiện ra). */
function slideLayout(s) {
  const kind = s.kind || "content";
  if (kind === "title" || kind === "agenda" || kind === "section") return kind;
  if (kind === "closing" || kind === "thanks") return "closing";
  const drawn = !!(s.diagram?.trim() || s.equation?.trim());
  const cards = (s.cards || []).filter((c) => c && c.title);
  const text = !!(cards.length || (s.bullets || []).some((b) => (b || "").trim()));
  // THẺ KHÔNG BAO GIỜ vào cột hẹp — giống hệt `slide_layout` bên server. Thiếu
  // luật này thì slide có thẻ + sơ đồ hiện ra hai kiểu khác nhau ở hai nơi.
  if (cards.length && (s.figure || drawn)) return "figwide";
  if (s.figure) {
    const ar = state.figAR?.[s.figure];
    if (ar && ar >= 1.9) return "figwide";
    if (!ar) return "figwide";          // chưa đo được thì đoán ngang, như server
    return text ? "figside" : "figfull";
  }
  if (drawn) return text ? "split" : "figfull";
  if (cards.length) return "cards";
  return "list";
}

/** Tỉ lệ mọi ảnh của bài, để `slideLayout` chọn đúng bố cục.
 *
 * Lấy từ server bằng MỘT request thay vì tải cả hai chục ảnh về chỉ để đọc
 * `naturalWidth` — PIL ở server chỉ cần đọc header là ra kích thước.
 */
async function measureFigures() {
  if (state.figAR) return;
  try {
    const r = await fetch(`/api/doc/${state.doc.id}/figsizes`);
    state.figAR = r.ok ? (await r.json()).ratios || {} : {};
  } catch {
    state.figAR = {};
  }
}

/* Sửa thẳng trên slide: mỗi ô chữ mang `data-edit` là đường dẫn tới trường
   tương ứng trong object slide, ví dụ `cards.0.bullets.1`. Đọc ngược DOM về
   object bằng `getPath`/`setPath` nên không cần map tay từng trường. */
const ED_PH = {
  eyebrow: "TÊN PHẦN", headline: "Tiêu đề — một câu khẳng định",
  sub: "Dòng phụ (bỏ trống được)", figure_note: "Chú giải hình: trục là gì, nhìn vào đâu",
  "callout.title": "Chốt lại", "callout.body": "Một câu",
};
const ed = (path) => {
  const ph = ED_PH[path] || ED_PH[path.replace(/\d+/g, "N")] || "…";
  return ` contenteditable="plaintext-only" spellcheck="false"`
    + ` data-edit="${path}" data-ph="${esc(ph)}"`;
};

function setPath(obj, path, val) {
  const ks = path.split(".");
  let o = obj;
  for (let i = 0; i < ks.length - 1; i++) {
    const k = ks[i], nx = ks[i + 1];
    if (o[k] == null) o[k] = /^\d+$/.test(nx) ? [] : {};
    o = o[k];
  }
  o[ks[ks.length - 1]] = val;
}

/** Đọc mọi ô đang sửa trên slide về lại object, rồi lưu. */
async function commitSlide() {
  const hit = findSlide(state.slideSel);
  if (!hit) return;
  const patch = { id: state.slideSel };
  // chép các trường gốc để không mất phần không hiện trên slide
  for (const k of ["headline", "sub", "eyebrow", "bullets", "cards", "callout",
                   "stats", "figure_note"]) {
    if (hit.sl[k] !== undefined) patch[k] = JSON.parse(JSON.stringify(hit.sl[k]));
  }
  $$("#slStage [data-edit]").forEach((el) =>
    setPath(patch, el.dataset.edit, el.innerText.replace(/\s+\n/g, "\n").trim()));
  // bỏ mục rỗng do người dùng xoá hết chữ
  if (Array.isArray(patch.bullets)) patch.bullets = patch.bullets.filter(Boolean);
  (patch.cards || []).forEach((c) => {
    if (Array.isArray(c.bullets)) c.bullets = c.bullets.filter(Boolean);
  });
  if (patch.cards) patch.cards = patch.cards.filter((c) => (c.title || "").trim());
  try {
    await patchSlides({ slide: patch }, "Đã lưu.");
    selectSlide(state.slideSel);
  } catch (e) { slStatus("Lỗi: " + e.message); }
}

function wireInlineEdit() {
  const host = $("#slStage");
  if (!host || host.dataset.wired) return;
  host.dataset.wired = "1";
  host.addEventListener("focusout", (e) => {
    if (!e.target.dataset?.edit) return;
    // đợi xem tiêu điểm có sang ô sửa khác trên cùng slide không, tránh lưu liên tục
    setTimeout(() => {
      if (!host.contains(document.activeElement)) commitSlide();
    }, 60);
  });
  host.addEventListener("keydown", (e) => {
    if (!e.target.dataset?.edit) return;
    if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); e.target.blur(); }
    if (e.key === "Escape") { e.preventDefault(); selectSlide(state.slideSel); }
  });
}

/* Tự co cho vừa khung — đúng thuật toán `normAutofit fontScale` của PowerPoint:
   ĐO bằng chính bộ dựng hình, giảm cỡ chữ, đo lại. Hệ số server gửi sang chỉ là
   điểm khởi đầu; `slide_fit.py` là bản mô phỏng flexbox viết tay nên luôn thiếu
   một thứ gì đó. Trình duyệt nói tràn là tràn — chỗ này không đoán. */
function autofitSlide(el) {
  if (!el) return;
  const LO = 0.7, STEP = 0.03;
  let s = parseFloat(el.style.getPropertyValue("--s")) || 1, n = 0;
  while (el.scrollHeight > el.clientHeight + 1 && s > LO && n++ < 30) {
    s = Math.max(LO, s - STEP);
    el.style.setProperty("--s", s.toFixed(3));
  }
}

/** Vẽ đúng cấu trúc mà `_export_slides_html` sinh ra — xem trước phải khớp file. */
function renderSlide(s) {
  const lay = slideLayout(s);
  const foot = clip(state.doc.brief?.title_vi || state.doc.title, 70);
  const cards = (s.cards || []).filter((c) => c && c.title);
  const bl = (s.bullets || []).filter((b) => (b || "").trim());

  const chip = (name, i, sz = 22) => {
    const svg = iconSvg(name, sz);
    return svg ? `<span class="chip" style="background:${chipCol(i)}">${svg}</span>` : "";
  };
  const cardsHtml = () => {
    if (!cards.length) return "";
    const inner = cards.map((c, i) => {
      const items = (c.bullets || []).map((x, j) =>
        `<li${ed(`cards.${i}.bullets.${j}`)}>${sci(x)}</li>`).join("");
      const meta = `<span class="card-m"${ed(`cards.${i}.meta`)}>${esc(c.meta || "")}</span>`;
      return `<div class="card part" data-part="card${i}" style="background:${tint(i)}">
        <div class="card-h">${chip(c.icon, i)}
        <div><span class="card-t"${ed(`cards.${i}.title`)}>${sci(c.title)}</span>${meta}</div></div>
        ${items ? `<ul>${items}</ul>` : ""}</div>`;
    }).join("");
    return `<div class="cards n${Math.min(cards.length, 4)}">${inner}</div>`;
  };
  const plainHtml = () => bl.length
    ? `<div class="plain part" data-part="bullets"><ul>${bl.map((b, i) =>
        `<li${ed(`bullets.${i}`)}>${sci(b)}</li>`).join("")}</ul></div>` : "";
  // Chỉ hiện ô chờ khi slide còn chỗ thật (server đo bằng `room_for_art`).
  // Slide kín thẻ thì chỗ trống chỉ vài chục pixel — hiện ô ở đó là mời người
  // dùng bỏ ảnh vào một khe không nhìn ra gì.
  const placeholderHtml = () => {
    const room = +(s.art_room || 0);
    if (room < 150) return "";
    return `<div class="artslot" style="min-height:${Math.min(room, 300)}px">
      <span>Chỗ dành cho ảnh minh hoạ</span>
      <em>Prompt có sẵn ở ô sửa bên dưới — tự tạo rồi tải lên</em></div>`;
  };

  const visualHtml = () => `<div class="vis part" data-part="visual">${visInner()}</div>`;

  const visInner = () => {
    const out = [];
    if (s.figure) {
      // ảnh AI vẽ phải nói rõ là minh hoạ, không để nhầm với hình của tác giả
      let note = (s.figure_note || "").trim();
      if (s.illus) {
        note = "Hình minh hoạ khái niệm, không phải hình trong bài báo."
          + (note ? " " + note : "");
      }
      const cap = `<figcaption${ed("figure_note")}>${sci(s.figure_note || "")}</figcaption>`;
      out.push(`<figure><div class="frame">${img(s.figure)}</div>${cap}</figure>`);
    }
    // sơ đồ phải đi qua .mmd-slot + hydrateDiagrams, không gọi mermaid.render thẳng
    if (s.diagram?.trim()) {
      out.push(`<div class="mmd-slot" data-mmd="${esc(s.diagram)}" data-cap=""></div>`);
    }
    if (s.equation?.trim()) out.push(`<div class="eq">${sci(s.equation)}</div>`);
    return out.join("");
  };
  const statsHtml = () => {
    const st = (s.stats || []).filter((x) => x && x.value).slice(0, 2);
    if (!st.length) return "";
    return `<div class="stats part" data-part="stats">${st.map((x, i) =>
      `<div><div class="stat-v"${ed(`stats.${i}.value`)}>${sci(x.value)}</div>
       <div class="stat-l"${ed(`stats.${i}.label`)}>${sci(x.label)}</div></div>`).join("")}</div>`;
  };
  const calloutHtml = () => {
    const co = s.callout || {};
    if (!(co.title || co.body)) return "";
    return `<div class="callout part" data-part="callout">${chip(co.icon || "check", 0, 19)}
      <div><b${ed("callout.title")}>${sci(co.title)}</b>
      <span${ed("callout.body")}>${sci(co.body || "")}</span></div></div>`;
  };
  const termsHtml = () => {
    const tm = s.terms || [];
    if (!tm.length) return "";
    return `<div class="terms part" data-part="terms">${tm.map((t) =>
      `<div><b>${esc(t.en)}</b> — ${sci(t.gloss)}</div>`).join("")}</div>`;
  };
  const header = () => {
    return `<div class="head part" data-part="head">
      <p class="eyebrow"${ed("eyebrow")}>${esc(s.eyebrow || "")}</p>
      <h2${ed("headline")}>${sci(s.headline || "")}</h2>
      <p class="sub"${ed("sub")}>${sci(s.sub || "")}</p></div>`;
  };

  let body;
  if (lay === "title") {
    const venue = clip(state.doc.brief?.venue_guess, 80);
    const src = clip(state.doc.source, 90);
    body = `<div class="deco"></div>
      <div class="part" data-part="head">
      <p class="eyebrow">${esc(s.eyebrow || "BÁO CÁO SEMINAR")}</p>
      <h1>${sci(s.headline || state.doc.brief?.title_vi || state.doc.title)}</h1>
      <p class="sub">${sci(s.sub || state.doc.title || "")}</p>
      <p class="who">${esc(venue)}<br><span class="dim">${esc(src)}</span></p></div>
      ${s.figure ? `<div class="art part" data-part="visual">${img(s.figure)}</div>` : ""}`;
  } else if (lay === "section") {
    const secs = deckOf("deck").filter((x) => (x.kind || "") === "section");
    const at = secs.findIndex((x) => x.id === s.id);
    body = `<div class="deco"></div>
      <div class="part" data-part="head">
      <p class="eyebrow">PHẦN ${at + 1} / ${secs.length}</p>
      <h2>${sci(s.headline || "")}</h2>
      ${s.sub?.trim() ? `<p class="sub">${sci(s.sub)}</p>` : ""}</div>
      ${s.figure ? `<div class="art part" data-part="visual">${img(s.figure)}</div>` : ""}`;
  } else if (lay === "agenda") {
    const rows = cards.map((c, i) => {
      const d = (c.bullets || []).find((x) => (x || "").trim()) || "";
      return `<div class="ag-row part" data-part="ag${i}" style="background:${tint(i)}">
        <span class="ag-n" style="background:${chipCol(i)}">${i + 1}</span>
        <div><div class="ag-t">${sci(c.title)}</div>
        ${d ? `<div class="ag-d">${sci(d)}</div>` : ""}</div></div>`;
    }).join("");
    body = header() + `<div class="ag">${rows}</div>`;
  } else if (lay === "closing") {
    body = `<div class="part" data-part="head"><h2>${sci(s.headline || "")}</h2>`
      + (s.sub?.trim() ? `<p class="sub">${sci(s.sub)}</p>` : "") + `</div>` + plainHtml();
  } else if (lay === "figwide") {
    body = header() + `<div class="body">${cardsHtml() || plainHtml()}${visualHtml()}
      ${statsHtml()}${calloutHtml()}${termsHtml()}</div>`;
  } else if (lay === "figside" || lay === "split") {
    body = header() + `<div class="body"><div class="two">
      <div>${cardsHtml() || plainHtml()}${statsHtml()}</div>
      <div>${visualHtml()}</div></div>${calloutHtml()}${termsHtml()}</div>`;
  } else if (lay === "figfull") {
    body = header() + `<div class="body">${visualHtml()}${calloutHtml()}${termsHtml()}</div>`;
  } else if (lay === "cards") {
    body = header() + `<div class="body">${cardsHtml()}${statsHtml()}`
      + `${placeholderHtml()}${calloutHtml()}${termsHtml()}</div>`;
  } else {
    body = header() + `<div class="body">${plainHtml()}${statsHtml()}`
      + `${calloutHtml()}${termsHtml()}</div>`;
  }

  const n = deckOf("deck").findIndex((x) => x.id === s.id);
  const label = n >= 0 ? String(n + 1)
    : "D" + (deckOf("backup").findIndex((x) => x.id === s.id) + 1);
  const foothtml = lay === "title" ? ""
    : `<div class="foot">${esc(foot)} · ${esc(label)}</div>`;
  // `--s` do server đo bằng metric font thật rồi gắn vào `s.fit.scale` —
  // xem trước phải co đúng như file xuất ra, không đoán lại ở client
  const sc = s.fit?.scale;
  const st = sc && sc < 1 ? ` style="--s:${sc}"` : "";
  const free = s.free && s.boxes ? " is-free" : "";
  $("#slStage").innerHTML =
    `<div class="sl-slide L-${lay}${free}"${st}>${body}${foothtml}</div>`;
  const el = $(".sl-slide", $("#slStage"));
  if (free) {
    // Bấm để chọn và kéo, BẤM ĐÚP mới vào sửa chữ — đúng cách Google Slides làm.
    // Để nguyên contenteditable thì mousedown nào cũng rơi vào ô chữ và không
    // bao giờ kéo được khung.
    $$("[contenteditable]", el).forEach((x) => x.setAttribute("contenteditable", "false"));
    // đặt từng phần vào đúng khung % đã lưu
    for (const [k, b] of Object.entries(s.boxes || {})) {
      const p = $(`.part[data-part="${CSS.escape(k)}"]`, el);
      if (p && Array.isArray(b) && b.length === 4) {
        Object.assign(p.style,
          { left: b[0] + "%", top: b[1] + "%", width: b[2] + "%", height: b[3] + "%" });
      }
    }
  }
  hydrateDiagrams($("#slStage"));
  if (!free) autofitSlide(el);      // bố cục tự do thì người dùng tự chịu khung
  wireFreeLayout();
  $("#slFree").textContent = s.free ? "↺ Bố cục tự sắp" : "⤢ Bố cục tự do";
  wireInlineEdit();

  const box = $("#slWarn");
  const rows = [...(s.warn || [])];
  if (s.stale) rows.unshift("Đoạn nguồn của slide này đã bị sửa sau khi dựng — soát lại rồi lưu.");
  box.classList.toggle("hidden", !rows.length);
  box.innerHTML = rows.length
    ? `<b>Bộ soát nói:</b><ul>${rows.map((w) => `<li>${esc(w)}</li>`).join("")}</ul>` : "";
}

function fillEditor({ key, sl }) {
  $("#slEdit").classList.remove("hidden");
  $("#slHead").value = sl.headline || "";
  $("#slBullets").value = (sl.bullets || []).join("\n");
  $("#slFigNote").value = sl.figure_note || "";
  // prompt vẽ minh hoạ chỉ có nghĩa khi slide chưa có gì để nhìn
  const wrap = $("#slArtWrap");
  wrap.classList.toggle("hidden", !sl.art_prompt);
  $("#slArtPrompt").value = sl.art_prompt || "";
  const room = +(sl.art_room || 0);
  $("#slArtMsg").textContent = sl.illus
    ? "Đang dùng ảnh bạn tải lên."
    : (sl.art_prompt && room < 150
        ? `Slide này đã kín — chỉ còn ${room}px, không đủ chỗ cho ảnh. `
          + "Bớt một thẻ hoặc bỏ hộp chốt nếu vẫn muốn thêm."
        : "");
  $("#slNotes").value = sl.notes || "";
  const opts = [`<option value="">— không có hình —</option>`].concat(
    figChoices().map((f) =>
      `<option value="${esc(f.id)}"${f.id === sl.figure ? " selected" : ""}>${esc(f.label)}</option>`));
  $("#slFig").innerHTML = opts.join("");
  $("#slMove").textContent = key === "deck" ? "Chuyển sang dự phòng" : "Đưa vào bộ chính";
  countEditor();
}

/* Ngân sách hiện ngay lúc gõ. Con số đã quy đổi cho tiếng Việt: cùng nội dung,
   tiếng Việt dài hơn tiếng Anh 10–25%, nên áp thẳng mốc của tiếng Anh sẽ ép câu
   cụt hư từ — ra thứ tiếng Việt kiểu tít báo không ai nói ra miệng. */
function countEditor() {
  const head = $("#slHead").value.trim();
  const bl = $("#slBullets").value.split("\n").filter((x) => x.trim());
  const fn = $("#slFigNote").value.trim();
  const wc = (s) => s.split(/\s+/).filter(Boolean).length;
  // chú giải hình đếm riêng — nó là chú thích của hình, không tranh chỗ với
  // thông điệp, nên không cộng vào ngân sách chữ của slide
  const words = wc(head) + bl.reduce((n, b) => n + wc(b), 0);
  const h = $("#slHeadCount");
  h.textContent = `${head.length}/85 ký tự · tiêu đề + gạch đầu dòng ${words} chữ `
    + `(nhắm ≤35, trần 55)` + (fn ? ` · chú giải hình ${wc(fn)} chữ (nhắm ≤35)` : "")
    + (bl.length > 4 ? ` · ${bl.length} gạch đầu dòng, tối đa 4` : "");
  h.classList.toggle("over",
    head.length > 105 || words > 55 || bl.length > 4 || wc(fn) > 42);

  const nw = $("#slNotes").value.split(/\s+/).filter(Boolean).length;
  const n = $("#slNotesCount");
  n.textContent = `${nw} chữ (nhắm 120–160 — khoảng một phút nói)`;
  n.classList.toggle("over", nw > 0 && (nw < 80 || nw > 200));
}

async function patchSlides(body, label) {
  const r = await fetch(`/api/doc/${state.doc.id}/slides`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!r.ok) throw new Error((await r.json()).detail || "không lưu được");
  state.doc.slides = (await r.json()).slides;
  renderStrip();
  if (label) slStatus(label);
}

function slStatus(msg) {
  const el = $("#slStatus");
  el.textContent = msg;
  el.classList.toggle("hidden", !msg);
}


/* ==================== bước 1 của pass slide: dàn ý ===================== */

/* Model đề xuất nội dung, người dùng quyết — đúng vai trò của màn `#review` ở
   bước 1 của cả công cụ. Gộp soạn nội dung với dựng slide làm một lượt thì model
   phải vừa nghĩ nội dung vừa lo khuôn dạng, và phần lớn chú ý của nó rơi vào
   khuôn dạng: câu khẳng định chung chung, thẻ độn cho đủ, sơ đồ ba hộp. */

const OL_KINDS = {
  title: "tiêu đề", agenda: "mục lục", section: "vách ngăn",
  content: "nội dung", closing: "kết",
};
const OL_EV = {
  figure: "hình trong bài", diagram: "sơ đồ tự vẽ", stats: "số liệu lớn",
  equation: "công thức", none: "không có",
};

const outlineOf = () => state.doc.slides?.outline || null;

/** Đổi giữa hai bước. Dàn ý là mặc định khi chưa có slide nào. */
function slTab(which) {
  const olMode = which === "outline";
  $("#slOutlinePane").classList.toggle("hidden", !olMode);
  $(".slides-body").classList.toggle("hidden", olMode);
  $("#slTabOutline").classList.toggle("is-on", olMode);
  $("#slTabDeck").classList.toggle("is-on", !olMode);
  setPref("slTab", which);
  if (olMode) renderOutline();
}

function renderOutline() {
  const ol = outlineOf();
  $("#slOlEmpty").classList.toggle("hidden", !!ol);
  $("#slOlTop").classList.toggle("hidden", !ol);
  if (!ol) {
    $("#slOlList").innerHTML = "";
    $("#slOlBackWrap").classList.add("hidden");
    return;
  }

  $("#slThesis").value = ol.thesis || "";
  $("#slOlSecs").innerHTML = (ol.sections || []).map((s, i) => `
    <span class="ol-sec-chip"><b>${i + 1}</b> ${esc(s.name || "")}</span>`).join("");
  const w = $("#slOlWarn");
  w.textContent = (ol.warn || []).join(" ");
  w.classList.toggle("hidden", !(ol.warn || []).length);

  const figs = figChoices();
  const opts = (map, cur) => Object.entries(map).map(([k, v]) =>
    `<option value="${k}"${k === cur ? " selected" : ""}>${esc(v)}</option>`).join("");

  const item = (it, n) => {
    const ev = it.evidence || {};
    const tags = [];
    if (it.warn?.length) tags.push(`<span class="sl-tag bad">${it.warn.length} cảnh báo</span>`);
    if (it.stale) tags.push(`<span class="sl-tag old">đoạn nguồn đã đổi</span>`);
    if (it.edited) tags.push(`<span class="sl-tag">đã sửa tay</span>`);
    // Tiêu đề, mục lục, vách ngăn và slide kết không lấy nội dung từ bài: chúng
    // không có ý, không có bằng chứng riêng. Hiện mấy ô đó ra là mời người dùng
    // điền vào chỗ rồi sẽ bị bỏ qua.
    const isSec = it.kind === "section" || it.kind === "agenda"
                  || it.kind === "title" || it.kind === "closing";
    // Chọn phần bằng danh sách chứ không gõ tay: gõ lệch một chữ là mục đó rơi
    // ra ngoài mục lục, và cảnh báo đó do chính ô nhập đẻ ra.
    const secNames = (ol.sections || []).map((s) => s.name || "");
    const cur = it.section || "";
    const secOpts = [""].concat(secNames, secNames.includes(cur) || !cur ? [] : [cur])
      .map((nm) => `<option value="${esc(nm)}"${nm === cur ? " selected" : ""}
        >${esc(nm || "— thuộc phần nào —")}</option>`).join("");
    return `<li class="ol-item${it.warn?.length ? " has-warn" : ""}" data-oid="${esc(it.id)}">
      <div class="ol-row">
        <span class="n">${esc(n)}</span>
        <select class="input ol-f ol-kind" title="Loại mục">${opts(OL_KINDS, it.kind)}</select>
        <select class="input ol-f ol-sec" title="Thuộc phần nào trong mục lục"
                ${isSec ? "disabled" : ""}>${secOpts}</select>
        <span class="tags">${tags.join("")}</span>
        <span class="spacer"></span>
        <button type="button" class="icon-btn" data-act="up" title="Lên trên">↑</button>
        <button type="button" class="icon-btn" data-act="down" title="Xuống dưới">↓</button>
        <button type="button" class="icon-btn" data-act="add" title="Thêm mục trắng ngay sau">＋</button>
        <button type="button" class="icon-btn" data-act="move" title="Chuyển giữa bộ chính và dự phòng">⇄</button>
        <button type="button" class="icon-btn" data-act="drop" title="Xoá mục này">✕</button>
      </div>
      <input class="input ol-msg" value="${esc(it.message || "")}"
             placeholder="Câu khẳng định — điều slide này chứng minh, không phải nhãn chủ đề">
      ${isSec ? "" : `<textarea class="input ol-pts" rows="4"
             placeholder="Mỗi dòng một ý sẽ hiện trên slide. 3–5 ý, mỗi ý một thông tin cụ thể."
             >${esc((it.points || []).join("\n"))}</textarea>`}
      ${isSec ? "" : `<div class="ol-ev">
        <select class="input ol-f ol-evk" title="Bằng chứng loại gì">${opts(OL_EV, ev.kind || "none")}</select>
        <select class="input ol-f ol-evf" title="Hình trong bài"
                ${ev.kind === "figure" ? "" : "disabled"}>
          <option value="">— chọn hình —</option>
          ${figs.map((f) => `<option value="${esc(f.id)}"${f.id === ev.figure ? " selected" : ""}
            >${esc(f.label)}</option>`).join("")}
        </select>
        <input class="input ol-evw" value="${esc(ev.what || "")}"
               placeholder="Bằng chứng đó là gì — với sơ đồ thì tả cơ chế cần vẽ">
      </div>`}
      ${it.warn?.length ? `<ul class="ol-warns">${
        it.warn.map((x) => `<li>${esc(x)}</li>`).join("")}</ul>` : ""}
    </li>`;
  };

  const rows = [];
  let part = 0;
  (ol.items || []).forEach((it, i) => {
    if (it.kind === "section") rows.push(`<li class="sl-sep">Phần ${++part}</li>`);
    rows.push(item(it, i + 1));
  });
  $("#slOlList").innerHTML = rows.join("");
  const bk = ol.backup || [];
  $("#slOlBackWrap").classList.toggle("hidden", !bk.length);
  $("#slOlBackCount").textContent = bk.length ? `(${bk.length})` : "";
  $("#slOlBackList").innerHTML = bk.map((it, i) => item(it, "D" + (i + 1))).join("");
  wireOutlineRows();
}

async function patchOutline(body, label) {
  const r = await fetch(`/api/doc/${state.doc.id}/outline`,
                        { method: "PATCH", headers: { "Content-Type": "application/json" },
                          body: JSON.stringify(body) });
  if (!r.ok) { slStatus("Lỗi: " + ((await r.json()).detail || "không lưu được")); return; }
  (state.doc.slides ||= {}).outline = (await r.json()).outline;
  renderOutline();
  if (label) slStatus(label);
}

/** Gửi cả mục lên server — sửa ô nào cũng gói chung, đỡ phải theo dõi từng ô. */
function saveOlItem(li) {
  const q = (s) => li.querySelector(s);
  const pts = q(".ol-pts");
  const evk = q(".ol-evk");       // mục tiêu đề/vách ngăn không có hàng bằng chứng
  patchOutline({
    item: {
      id: li.dataset.oid,
      kind: q(".ol-kind").value,
      section: q(".ol-sec").value,
      message: q(".ol-msg").value,
      points: pts ? pts.value.split("\n").map((s) => s.trim()).filter(Boolean) : [],
      evidence: evk ? {
        kind: evk.value,
        // hình chỉ có nghĩa khi bằng chứng là hình — giữ lại thì lần dựng sau
        // gắn nhầm một cái ảnh mà người dùng vừa bỏ đi
        figure: evk.value === "figure" ? q(".ol-evf").value : "",
        what: q(".ol-evw").value,
      } : { kind: "none", figure: "", what: "" },
    },
  }, "Đã lưu mục.");
}

function wireOutlineRows() {
  $$("#slOutlinePane .ol-item").forEach((li) => {
    li.querySelectorAll("input, textarea, select").forEach((el) => {
      el.onchange = () => saveOlItem(li);
    });
    li.querySelectorAll("[data-act]").forEach((b) => {
      b.onclick = () => {
        const oid = li.dataset.oid;
        if (b.dataset.act === "drop") {
          if (!confirm("Xoá mục này khỏi dàn ý?")) return;
          patchOutline({ drop: oid }, "Đã xoá mục.");
        } else if (b.dataset.act === "add") {
          patchOutline({ add: oid }, "Đã thêm mục trắng.");
        } else if (b.dataset.act === "move") {
          const inBack = !!li.closest("#slOlBackList");
          patchOutline({ id: oid, to: inBack ? "items" : "backup" },
                       inBack ? "Đã đưa lên bộ chính." : "Đã chuyển sang dự phòng.");
        } else {
          patchOutline({ move: { id: oid, by: b.dataset.act === "up" ? -1 : 1 } });
        }
      };
    });
  });
}

function wireOutline() {
  $("#slTabOutline").onclick = () => slTab("outline");
  $("#slTabDeck").onclick = () => slTab("deck");
  $("#slThesis").onchange = () => patchOutline({ thesis: $("#slThesis").value });

  $("#slOutlineBtn").onclick = async () => {
    const btn = $("#slOutlineBtn"), old = btn.textContent;
    const nMiss = untranslatedCount();
    if (nMiss && !confirm(`Bài còn ${nMiss} khối chưa dịch.\n\n`
        + "Nội dung soạn từ bản dịch đã soát; phần chưa dịch thì model tự đọc lấy "
        + "từ bản gốc.\n\nVẫn soạn?")) return;
    if (outlineOf() && !confirm("Soạn lại dàn ý?\n\nMọi sửa tay trên dàn ý hiện tại"
        + " sẽ mất. Slide đã dựng thì vẫn còn.")) return;
    btn.disabled = true;
    btn.textContent = "Đang soạn…";
    slStatus("Đang đọc lại bài và soạn nội dung buổi nói…");
    try {
      const r = await fetch(`/api/doc/${state.doc.id}/outline`, { method: "POST" });
      if (!r.ok) throw new Error((await r.json()).detail || "không soạn được");
      const res = await r.json();
      (state.doc.slides ||= {}).outline = res.outline;
      slTab("outline");
      const n = (res.outline.items || []).length;
      const bad = (res.outline.items || []).filter((i) => i.warn?.length).length;
      reportCost(`Đã soạn ${n} mục` + (bad ? ` · ${bad} mục có cảnh báo` : ""),
                 res.run, res.total);
      slStatus("Soát và sửa dàn ý, xong thì bấm Dựng slide.");
    } catch (e) {
      slStatus("Lỗi: " + e.message);
    } finally {
      btn.disabled = false;
      btn.textContent = old;
    }
  };
}

/** Dựng slide từ dàn ý đã duyệt — từng mẻ, báo tiến trình. */
function buildDeck() {
  return new Promise((resolve, reject) => {
    const es = new EventSource(`/api/doc/${state.doc.id}/slides/build`);
    // cộng dồn chi phí các mẻ để báo một lần ở cuối, thay vì mỗi mẻ một dòng
    const run = { cost: 0, cached_tokens: 0 };
    es.addEventListener("start", (e) => {
      slStatus(`Đang dựng 0/${JSON.parse(e.data).total} slide…`);
    });
    es.addEventListener("batch", (e) => {
      const d = JSON.parse(e.data);
      slStatus(`Đang dựng ${d.done}/${d.total} slide…`);
      if (d.sum) { state.doc.usage = d.sum; renderUsage(); }
      if (d.run) {
        run.cost += d.run.cost || 0;
        run.cached_tokens += d.run.cached_tokens || 0;
      }
    });
    es.addEventListener("done", (e) => {
      es.close();
      resolve({ ...JSON.parse(e.data), run });
    });
    es.addEventListener("error", (e) => {
      es.close();
      let msg = "mất kết nối tới server";
      try { msg = JSON.parse(e.data).error; } catch {}
      reject(new Error(msg));
    });
  });
}


/* ==================== bôi vàng & ghi chú (như comment) ================== */

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
    if (!r.ok) throw new Error((await r.json()).detail || "không bôi được");
    const { highlights, new: item } = await r.json();
    state.doc.highlights = highlights;
    document.getSelection().removeAllRanges();
    renderDoc();
    openHlPop(item.id);
  } catch (e) {
    status("Lỗi: " + e.message);
  } finally { hlPending = null; }
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
    if (!r.ok) throw new Error((await r.json()).detail || "không lưu được");
    state.doc.highlights = (await r.json()).highlights;
    $("#hlMsg").textContent = "Đã lưu.";
    $$(`#doc mark.hl[data-hl="${CSS.escape(state.hlOpen)}"]`)
      .forEach((m) => m.classList.toggle("has-note", !!note));
  } catch (e) { $("#hlMsg").textContent = "Lỗi: " + e.message; }
}

function wireHighlights() {
  const doc = $("#doc");
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
      if (!r.ok) throw new Error((await r.json()).detail || "không đổi màu được");
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
      if (!r.ok) throw new Error((await r.json()).detail || "không xoá được");
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
      if (!r.ok) throw new Error((await r.json()).detail || "không giải thích được");
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


/* ==================== bố cục tự do: kéo và co khung ==================== */

/* Mặc định slide tự sắp bằng flexbox — đó là thứ giữ cả bộ nhất quán và là chỗ
   autofit bám vào. Bật "tự do" cho RIÊNG một slide thì mọi phần chuyển sang toạ
   độ tuyệt đối, và toạ độ đó **chụp từ chính vị trí đang hiển thị**: bạn kéo
   tiếp từ cái đang thấy, không phải bày lại từ canvas trắng.
   Lưu theo % khung slide nên đổi cỡ màn hình hay in ra PDF vẫn đúng tỉ lệ. */

const SNAP = 0.4;              // hút mép khi lệch dưới 0.4% bề ngang slide

/** Chụp vị trí hiện tại của mọi phần thành khung %, để bắt đầu kéo. */
function captureBoxes(slideEl) {
  const r0 = slideEl.getBoundingClientRect();
  const out = {};
  $$(".part", slideEl).forEach((el) => {
    const r = el.getBoundingClientRect();
    out[el.dataset.part] = [
      +(((r.left - r0.left) / r0.width) * 100).toFixed(3),
      +(((r.top - r0.top) / r0.height) * 100).toFixed(3),
      +((r.width / r0.width) * 100).toFixed(3),
      +((r.height / r0.height) * 100).toFixed(3),
    ];
  });
  return out;
}

async function toggleFree(on) {
  const hit = findSlide(state.slideSel);
  if (!hit) return;
  const slideEl = $(".sl-slide", $("#slStage"));
  const patch = { id: state.slideSel, free: on };
  if (on) patch.boxes = captureBoxes(slideEl);   // chụp trước khi đổi sang tuyệt đối
  try {
    await patchSlides({ slide: patch }, on
      ? "Bố cục tự do — kéo để di chuyển, kéo nút vuông để co giãn."
      : "Đã trả về bố cục tự sắp.");
    selectSlide(state.slideSel);
  } catch (e) { slStatus("Lỗi: " + e.message); }
}

/** Gắn nút co giãn vào phần đang chọn. */
function addGrips(el) {
  $$(".grip", el).forEach((g) => g.remove());
  ["nw", "n", "ne", "e", "se", "s", "sw", "w"].forEach((d) => {
    const g = document.createElement("i");
    g.className = "grip " + d;
    g.dataset.dir = d;
    el.appendChild(g);
  });
}

function clearGuides() { $$(".sl-guide", $("#slStage")).forEach((g) => g.remove()); }

function showGuide(slideEl, axis, pct) {
  const g = document.createElement("div");
  g.className = "sl-guide " + axis;
  g.style[axis === "v" ? "left" : "top"] = pct + "%";
  slideEl.appendChild(g);
}

/** Hút mép đang kéo về mép của các phần khác, và về giữa slide. */
function snap(v, edges) {
  for (const e of edges) if (Math.abs(v - e) < SNAP) return e;
  return v;
}

function wireFreeLayout() {
  const stage = $("#slStage");
  if (stage.dataset.free) return;
  stage.dataset.free = "1";

  let drag = null;

  stage.addEventListener("mousedown", (e) => {
    const slideEl = $(".sl-slide", stage);
    if (!slideEl?.classList.contains("is-free")) return;
    if (e.target.isContentEditable) return;   // đang trong ô chữ đã mở thì đừng kéo
    const grip = e.target.closest(".grip");
    const part = grip ? grip.parentElement : e.target.closest(".part");
    if (!part) return;
    e.preventDefault();
    const r0 = slideEl.getBoundingClientRect();
    const r = part.getBoundingClientRect();
    $$(".part", slideEl).forEach((p) => p.classList.toggle("sel", p === part));
    addGrips(part);
    // mép của các phần KHÁC, để hút vào
    const ex = [], ey = [50];
    $$(".part", slideEl).forEach((p) => {
      if (p === part) return;
      const b = p.getBoundingClientRect();
      ex.push(((b.left - r0.left) / r0.width) * 100,
              ((b.right - r0.left) / r0.width) * 100);
      ey.push(((b.top - r0.top) / r0.height) * 100,
              ((b.bottom - r0.top) / r0.height) * 100);
    });
    drag = {
      part, slideEl, r0, dir: grip?.dataset.dir || null,
      mx: e.clientX, my: e.clientY,
      x: ((r.left - r0.left) / r0.width) * 100,
      y: ((r.top - r0.top) / r0.height) * 100,
      w: (r.width / r0.width) * 100,
      h: (r.height / r0.height) * 100,
      ex, ey,
    };
  });

  // bấm đúp vào một phần -> mở ô chữ gần nhất để sửa, xong thì đóng lại
  stage.addEventListener("dblclick", (e) => {
    const slideEl = $(".sl-slide", stage);
    if (!slideEl?.classList.contains("is-free")) return;
    const t = e.target.closest("[data-edit]");
    if (!t) return;
    t.setAttribute("contenteditable", "plaintext-only");
    t.focus();
    const done = () => {
      t.setAttribute("contenteditable", "false");
      t.removeEventListener("blur", done);
    };
    t.addEventListener("blur", done);
  });

  addEventListener("mousemove", (e) => {
    if (!drag) return;
    const dx = ((e.clientX - drag.mx) / drag.r0.width) * 100;
    const dy = ((e.clientY - drag.my) / drag.r0.height) * 100;
    let { x, y, w, h } = drag;
    if (!drag.dir) { x += dx; y += dy; }
    else {
      if (drag.dir.includes("w")) { x += dx; w -= dx; }
      if (drag.dir.includes("e")) { w += dx; }
      if (drag.dir.includes("n")) { y += dy; h -= dy; }
      if (drag.dir.includes("s")) { h += dy; }
    }
    w = Math.max(4, w); h = Math.max(3, h);
    clearGuides();
    const sx = snap(x, drag.ex), sy = snap(y, drag.ey);
    if (sx !== x) { x = sx; showGuide(drag.slideEl, "v", x); }
    if (sy !== y) { y = sy; showGuide(drag.slideEl, "h", y); }
    const r = snap(x + w, drag.ex);
    if (r !== x + w) { w = r - x; showGuide(drag.slideEl, "v", r); }
    Object.assign(drag.part.style, {
      left: x.toFixed(3) + "%", top: y.toFixed(3) + "%",
      width: w.toFixed(3) + "%", height: h.toFixed(3) + "%",
    });
    drag.cur = [x, y, w, h];
  });

  addEventListener("mouseup", async () => {
    if (!drag) return;
    const { part, slideEl, cur } = drag;
    drag = null;
    clearGuides();
    if (!cur) return;
    const hit = findSlide(state.slideSel);
    if (!hit) return;
    const boxes = { ...(hit.sl.boxes || {}), ...captureBoxes(slideEl) };
    boxes[part.dataset.part] = cur.map((v) => +v.toFixed(3));
    try {
      await patchSlides({ slide: { id: state.slideSel, free: true, boxes } }, "");
    } catch (e) { slStatus("Lỗi: " + e.message); }
  });

  // phím mũi tên nhích 0.5% (giữ Shift = 2%)
  addEventListener("keydown", async (e) => {
    if ($("#slides").classList.contains("hidden")) return;
    if (e.target.isContentEditable || /^(INPUT|TEXTAREA)$/.test(e.target.tagName)) return;
    const sel = $(".sl-slide.is-free .part.sel", stage);
    if (!sel || !e.key.startsWith("Arrow")) return;
    e.preventDefault();
    const step = e.shiftKey ? 2 : 0.5;
    const hit = findSlide(state.slideSel);
    const b = [...(hit.sl.boxes?.[sel.dataset.part] || [0, 0, 20, 10])];
    if (e.key === "ArrowLeft") b[0] -= step;
    if (e.key === "ArrowRight") b[0] += step;
    if (e.key === "ArrowUp") b[1] -= step;
    if (e.key === "ArrowDown") b[1] += step;
    const boxes = { ...(hit.sl.boxes || {}) };
    boxes[sel.dataset.part] = b.map((v) => +v.toFixed(3));
    Object.assign(sel.style, { left: b[0] + "%", top: b[1] + "%" });
    await patchSlides({ slide: { id: state.slideSel, free: true, boxes } }, "");
  });
}

/* ---------------------------------------------------------- trình chiếu */

/* Dùng lại đúng `renderSlide()` của màn sửa: cái chiếu lên tường giống hệt cái
   vừa sửa, không phải hai đoạn code dựng ra hai thứ hơi khác nhau. */
function presentAt(i) {
  const deck = deckOf("deck");
  if (!deck.length) return;
  state.presentAt = Math.max(0, Math.min(i, deck.length - 1));
  const sl = deck[state.presentAt];
  const keep = state.slideSel;
  state.slideSel = sl.id;
  const stage = $("#slStage"), tmp = document.createElement("div");
  tmp.id = "slStage";
  stage.id = "slStageOff";
  document.body.appendChild(tmp);
  renderSlide(sl);
  $("#presentStage").innerHTML = tmp.innerHTML;
  tmp.remove();
  stage.id = "slStage";
  state.slideSel = keep;
  $$("#presentStage [contenteditable]").forEach((el) =>
    el.removeAttribute("contenteditable"));
  hydrateDiagrams($("#presentStage"));
  $("#presentNotes").textContent = sl.notes || "(slide này không có lời nói)";
  $("#presentNum").textContent = `${state.presentAt + 1} / ${deck.length}`;
}

function openPresent() {
  if (!deckOf("deck").length) return slStatus("Chưa có slide nào để chiếu.");
  $("#present").classList.remove("hidden");
  const i = deckOf("deck").findIndex((x) => x.id === state.slideSel);
  presentAt(i < 0 ? 0 : i);
  document.documentElement.requestFullscreen?.().catch(() => {});
}

function closePresent() {
  $("#present").classList.add("hidden");
  $("#presentNotes").classList.add("hidden");
  if (document.fullscreenElement) document.exitFullscreen?.().catch(() => {});
}

function wirePresent() {
  document.addEventListener("keydown", (e) => {
    const on = !$("#present").classList.contains("hidden");
    const inSlides = !$("#slides").classList.contains("hidden");
    if (!on) {
      // F5 mở trình chiếu; ↑↓ chuyển slide khi không đang gõ chữ
      if (inSlides && e.key === "F5") { e.preventDefault(); return openPresent(); }
      if (!inSlides || /^(INPUT|TEXTAREA)$/.test(e.target.tagName)
          || e.target.isContentEditable) return;
      const deck = deckOf("deck");
      const at = deck.findIndex((x) => x.id === state.slideSel);
      if (e.key === "ArrowDown" && at < deck.length - 1) {
        e.preventDefault(); selectSlide(deck[at + 1].id);
      } else if (e.key === "ArrowUp" && at > 0) {
        e.preventDefault(); selectSlide(deck[at - 1].id);
      }
      return;
    }
    e.preventDefault();
    if (["ArrowRight", "PageDown", " ", "Enter"].includes(e.key)) presentAt(state.presentAt + 1);
    else if (["ArrowLeft", "PageUp", "Backspace"].includes(e.key)) presentAt(state.presentAt - 1);
    else if (e.key === "Home") presentAt(0);
    else if (e.key === "End") presentAt(deckOf("deck").length - 1);
    else if (e.key.toLowerCase() === "s") $("#presentNotes").classList.toggle("hidden");
    else if (e.key === "Escape") closePresent();
  });
  $("#presentStage").onclick = () => presentAt(state.presentAt + 1);
}

function wireSlides() {
  $("#slBack").onclick = () => { showScreen("reader"); slStatus(""); };
  $("#slidesBtn").onclick = () => { if (!$("#slidesBtn").disabled) openSlides(); };
  wireOutline();

  ["#slHead", "#slBullets", "#slFigNote", "#slNotes"].forEach(
    (s) => ($(s).oninput = countEditor));

  $("#slGenBtn").onclick = async () => {
    const btn = $("#slGenBtn"), old = btn.textContent;
    const has = deckOf("deck").length || deckOf("backup").length;
    // Không có dàn ý thì không dựng: nội dung phải qua tay người dùng trước.
    // Đó là toàn bộ lý do tách pass này làm hai bước.
    if (!outlineOf()) {
      slTab("outline");
      slStatus("Chưa có dàn ý. Bấm Soạn nội dung trước, soát xong mới dựng slide.");
      return;
    }
    if (has && !confirm("Dựng lại bộ slide từ dàn ý?\n\nSlide bạn đã sửa tay thì"
        + " giữ nguyên, không dựng đè. Phần còn lại dựng mới. Tốn tiền.")) return;
    btn.disabled = true;
    btn.textContent = "Đang dựng…";
    try {
      const res = await buildDeck();
      state.doc.slides = res.slides;
      state.slideSel = null;
      slTab("deck");
      renderStrip();
      const deck = res.slides.deck || [];
      const bad = deck.filter((s) => s.warn?.length).length;
      reportCost(`Đã dựng ${deck.length} slide` +
                 (bad ? ` · ${bad} slide có cảnh báo` : ""), res.run, res.total);
      slStatus(bad ? `${bad} slide có cảnh báo — xem viền đỏ ở dải bên trái.` : "");
    } catch (e) {
      slStatus("Lỗi: " + e.message);
    } finally {
      btn.disabled = false;
      btn.textContent = old;
    }
  };

  $("#slEdit").onsubmit = async (e) => {
    e.preventDefault();
    try {
      await patchSlides({
        slide: {
          id: state.slideSel,
          headline: $("#slHead").value.trim(),
          bullets: $("#slBullets").value.split("\n").map((x) => x.trim()).filter(Boolean),
          notes: $("#slNotes").value.trim(),
          figure: $("#slFig").value,
          figure_note: $("#slFigNote").value.trim(),
        },
      }, "Đã lưu slide.");
      selectSlide(state.slideSel);
    } catch (err) { slStatus("Lỗi: " + err.message); }
  };

  $("#slRegen").onclick = async () => {
    const btn = $("#slRegen"), old = btn.textContent;
    const hint = prompt("Muốn slide này khác đi ở chỗ nào? (để trống cũng được)") ?? null;
    if (hint === null) return;
    btn.disabled = true;
    btn.textContent = "Đang viết lại…";
    try {
      const r = await fetch(`/api/doc/${state.doc.id}/slides/${state.slideSel}/regen`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ hint, model: sel.value }),
      });
      if (!r.ok) throw new Error((await r.json()).detail || "không viết lại được");
      const res = await r.json();
      const hit = findSlide(state.slideSel);
      if (hit) state.doc.slides[hit.key][hit.i] = res.slide;
      renderStrip();
      selectSlide(state.slideSel);
      reportCost("Đã viết lại slide", res.run, res.total);
      slStatus($("#statusLine").textContent);
    } catch (e) {
      slStatus("Lỗi: " + e.message);
    } finally {
      btn.disabled = false;
      btn.textContent = old;
    }
  };

  $("#slPresent").onclick = openPresent;

  $("#slAdd").onclick = async () => {
    try {
      const r = await fetch(`/api/doc/${state.doc.id}/slides`, {
        method: "PATCH", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ add: state.slideSel || "" }),
      });
      if (!r.ok) throw new Error((await r.json()).detail || "không thêm được");
      const { slides, new_id } = await r.json();
      state.doc.slides = slides; state.slideSel = new_id;
      renderStrip(); selectSlide(new_id); slStatus("Đã thêm slide trắng.");
    } catch (e) { slStatus("Lỗi: " + e.message); }
  };

  $("#slDup").onclick = async () => {
    if (!state.slideSel) return;
    try {
      const r = await fetch(`/api/doc/${state.doc.id}/slides`, {
        method: "PATCH", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ duplicate: state.slideSel }),
      });
      if (!r.ok) throw new Error((await r.json()).detail || "không nhân đôi được");
      const { slides, new_id } = await r.json();
      state.doc.slides = slides; state.slideSel = new_id;
      renderStrip(); selectSlide(new_id); slStatus("Đã nhân đôi slide.");
    } catch (e) { slStatus("Lỗi: " + e.message); }
  };

  $("#slFree").onclick = () => {
    const hit = findSlide(state.slideSel);
    if (hit) toggleFree(!hit.sl.free);
  };

  $("#slCardAdd").onclick = async () => {
    const hit = findSlide(state.slideSel);
    if (!hit) return;
    const cards = [...(hit.sl.cards || []), { title: "Ý mới", bullets: ["Nội dung"] }];
    try {
      await patchSlides({ slide: { id: state.slideSel, cards } }, "Đã thêm thẻ.");
      selectSlide(state.slideSel);
    } catch (e) { slStatus("Lỗi: " + e.message); }
  };

  $("#slCardDel").onclick = async () => {
    const hit = findSlide(state.slideSel);
    if (!hit || !(hit.sl.cards || []).length) return;
    const cards = hit.sl.cards.slice(0, -1);
    try {
      await patchSlides({ slide: { id: state.slideSel, cards } }, "Đã bớt một thẻ.");
      selectSlide(state.slideSel);
    } catch (e) { slStatus("Lỗi: " + e.message); }
  };

  $("#slArtCopy").onclick = () => {
    navigator.clipboard.writeText($("#slArtPrompt").value);
    const b = $("#slArtCopy"); b.textContent = "✓ Đã chép";
    setTimeout(() => (b.textContent = "⧉ Chép prompt"), 1200);
  };

  $("#slArtFile").onchange = async (e) => {
    const f = e.target.files?.[0];
    if (!f || !state.slideSel) return;
    const msg = $("#slArtMsg");
    msg.textContent = "Đang tải lên…";
    try {
      const fd = new FormData();
      fd.append("file", f);
      const r = await fetch(`/api/doc/${state.doc.id}/slides/${state.slideSel}/image`,
                           { method: "POST", body: fd });
      if (!r.ok) throw new Error((await r.json()).detail || "không tải lên được");
      const { slide } = await r.json();
      const hit = findSlide(state.slideSel);
      if (hit) state.doc.slides[hit.key][hit.i] = slide;
      await measureFigures();
      renderStrip(); selectSlide(state.slideSel);
      msg.textContent = "Đã gắn ảnh vào slide.";
    } catch (err) {
      msg.textContent = "Lỗi: " + err.message;
    } finally { e.target.value = ""; }
  };

  $("#slMove").onclick = async () => {
    const hit = findSlide(state.slideSel);
    if (!hit) return;
    const to = hit.key === "deck" ? "backup" : "deck";
    const order = {
      deck: deckOf("deck").map((s) => s.id),
      backup: deckOf("backup").map((s) => s.id),
    };
    order[hit.key] = order[hit.key].filter((i) => i !== state.slideSel);
    order[to] = [...order[to], state.slideSel];
    try {
      await patchSlides({ order }, to === "backup"
        ? "Đã chuyển sang bộ dự phòng." : "Đã đưa vào bộ chính.");
      selectSlide(state.slideSel);
    } catch (e) { slStatus("Lỗi: " + e.message); }
  };

  $("#slDrop").onclick = async () => {
    if (!confirm("Xoá hẳn slide này?")) return;
    try {
      const gone = state.slideSel;
      state.slideSel = null;
      await patchSlides({ drop: [gone] }, "Đã xoá slide.");
    } catch (e) { slStatus("Lỗi: " + e.message); }
  };

  $("#slExportBtn").onclick = (e) => {
    e.stopPropagation();
    $("#slExportMenu").classList.toggle("hidden");
  };
  document.addEventListener("click", () => $("#slExportMenu").classList.add("hidden"));
  $$("#slExportMenu a").forEach((a) => (a.onclick = (e) => {
    e.preventDefault();
    if (!deckOf("deck").length && !deckOf("backup").length) {
      return slStatus("Chưa có slide nào để tải về.");
    }
    window.open(`/api/doc/${state.doc.id}/export?fmt=${a.dataset.fmt}`, "_blank");
    if (a.dataset.fmt === "slides-pdf") {
      slStatus("Trang in đã mở ở tab mới — chọn khổ ngang và “Lưu thành PDF”." +
               " Sơ đồ cần vài giây để vẽ xong trước khi hộp in hiện ra.");
    } else if (a.dataset.fmt === "pptx") {
      slStatus("Đang tải .pptx — mở bằng PowerPoint, LibreOffice hoặc Google Slides." +
               " Sơ đồ là shape rời nên sửa chữ và kéo được; lời người nói nằm ở" +
               " phần ghi chú của từng slide.");
    }
  }));
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
  fs.oninput = () => { setPref("font", fs.value); applyReaderPrefs(); };
  cw.oninput = () => { setPref("width", cw.value); applyReaderPrefs(); };

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

function wirePdfPane() {
  $("#pdfBtn").onclick = () => togglePdf($("#pdfPane").classList.contains("hidden"));
  $("#pdfClose").onclick = () => togglePdf(false);
  $("#pdfPrev").onclick = () => pdfGo(pdfv.page - 1);
  $("#pdfNext").onclick = () => pdfGo(pdfv.page + 1);
  $("#pdfZoomIn").onclick = () => pdfZoom(25);
  $("#pdfZoomOut").onclick = () => pdfZoom(-25);
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
}

/* --------------------------------------------------- tìm trong bài */

const find = { hits: [], i: -1 };

/** Bọc mọi lần khớp trong <mark>, giữ nguyên chữ hoa thường của bản gốc. */
function markUp(text, q) {
  const low = text.toLowerCase(), needle = q.toLowerCase();
  let out = "", i = 0;
  for (;;) {
    const j = low.indexOf(needle, i);
    if (j < 0) return out + esc(text.slice(i));
    out += esc(text.slice(i, j)) + `<mark class="hit">${esc(text.slice(j, j + q.length))}</mark>`;
    i = j + q.length;
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
  const needle = q.toLowerCase();
  $$("#doc .en, #doc .vi, #doc .gl").forEach((cell) => {
    const text = cell.textContent;
    if (!text.toLowerCase().includes(needle)) return;
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
    if (!r.ok) throw new Error((await r.json()).detail || "không lưu được");
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
  host.innerHTML = `<div class="doc-inner">${blocks
    .filter((b) => b.type !== "reference")
    .filter((b) => !b.hidden || state.showHidden)
    .map((b) => pairHTML(b, translations[b.id], notes[b.id]))
    .join("")}</div>`;
  syncHiddenBar(nHidden);
  wirePairs();
  repaintHighlights();
  hydrateDiagrams(host);
  applyReaderPrefs();
  host.onscroll = onDocScroll;
}

function pairHTML(b, vi, note) {
  const cls = (b.type === "heading" ? "h" + Math.min(b.level || 1, 3) : b.type)
    + (b.marker ? " li" : "")
    + (b.type === "equation" && b.figure ? " eq-img" : "");
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
         <button data-act="explain" title="Giải thích — đoạn này đang làm gì trong lập luận của bài?">💡</button>
         <button data-act="copy" title="Chép bản dịch">⧉</button>
         <button data-act="edit" title="Sửa tay bản dịch. Miễn phí, và bản sửa được ghi vào bộ nhớ dịch nên đoạn y hệt ở bài khác cũng dùng bản của bạn.">✎</button>` : ""}
       ${b.hidden
         ? `<button data-act="unhide" title="Đưa khối này trở lại mạch đọc">↩</button>`
         : `<button data-act="hide" title="Ẩn khối này khỏi mạch đọc (giữ nguyên bản dịch, hiện lại được)">⊘</button>`}
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
                title="Khung cắt sai? Kéo lại khung trên trang PDF gốc.">✂</button>` : ""}
       </figure>`
    : "";
  const gl = state.doc.plain?.[b.id] || "";
  return `<div class="pair ${cls}${b.hidden ? " is-hidden" : ""}" id="p-${esc(b.id)}" data-id="${esc(b.id)}" data-section="${esc(b.section || "")}">
    ${fig}${tools}
    <div class="en">${mk}${sci(b.text)}</div>
    <div class="vi" data-vi>${mk}${viHTML}</div>
    <div class="gl" data-gl>${sci(gl)}</div>
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

  const cancel = () => { cell.innerHTML = before; };
  const save = async () => {
    const val = ta.value;
    if (val === raw) return cancel();
    try {
      const r = await fetch(`/api/doc/${state.doc.id}/translation`, {
        method: "PATCH", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ block_id: id, [which]: val }),
      });
      if (!r.ok) throw new Error((await r.json()).detail || "không lưu được");
      const res = await r.json();
      if (which === "vi") state.doc.translations[id] = res.vi;
      else state.doc.plain[id] = res.plain;
      cell.innerHTML = sci(which === "vi" ? res.vi : res.plain);
      cell.classList.add("was-edited");
      status("Đã lưu bản sửa — bộ nhớ dịch cũng được cập nhật");
    } catch (e) {
      status("Lỗi: " + e.message);
      cancel();
    }
  };
  cell.addEventListener("click", (e) => {
    const b = e.target.closest("[data-e]");
    if (!b) return;
    e.stopPropagation();
    (b.dataset.e === "save" ? save : cancel)();
  });
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
      const ref = e.target.closest("[data-figref]");
      if (ref) { e.stopPropagation(); return openFigPeek(ref.dataset.figref); }
      const cut = e.target.closest("[data-crop]");
      if (cut) { e.stopPropagation(); return openCrop(cut.dataset.crop); }
      const act = e.target.closest("[data-act]")?.dataset.act;
      if (act === "hide") return setBlockHidden(el.dataset.id, true);
      if (act === "unhide") return setBlockHidden(el.dataset.id, false);
      if (act === "explain") return explainBlock(el.dataset.id);
      if (act === "edit") return editCell(el, "vi");
      if (act === "copy") {
        navigator.clipboard.writeText($("[data-vi]", el).textContent.trim());
        e.target.textContent = "✓";
        setTimeout(() => (e.target.textContent = "⧉"), 900);
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

function renderUsage() {
  const u = state.doc.usage || {};
  const cached = u.cached_tokens ? ` · ${(u.cached_tokens / 1000).toFixed(1)}k đọc từ cache` : "";
  $("#usageBox").textContent =
    `${((u.prompt_tokens || 0) / 1000).toFixed(1)}k vào · ${((u.completion_tokens || 0) / 1000).toFixed(1)}k ra` +
    cached + (u.cost ? ` · $${u.cost.toFixed(4)}` : "");
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
  status("Sẽ dừng khi xong mẻ đang chạy — mẻ đó đã trả tiền rồi nên để chạy nốt cho khỏi phí.");
}

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
    btn.textContent = Object.keys(state.doc.translations || {}).length ? "Dịch tiếp" : "Dịch";
    $("#progress").classList.add("hidden");
    return;
  }

  try {
    if (!state.doc.brief) {
      status("Đang đọc toàn bài để dựng tóm lược và chốt bảng thuật ngữ…");
      const r = await fetch(`/api/doc/${state.doc.id}/brief`, { method: "POST" });
      if (!r.ok) throw new Error((await r.json()).detail || "Không dựng được tóm lược");
      const res = await r.json();
      state.doc.brief = res.brief;
      $("#docTitleVi").textContent = state.doc.brief.title_vi || state.doc.title;
      renderSide();
      reportCost("Đọc toàn bài xong", res.run, res.total);
    }

    let stoppedAt = -1;
    for (let i = 0; i < state.chunks; i++) {
      if (state.stopping) { stoppedAt = i; break; }
      if (chunkDone(i)) { bar((i + 1) / state.chunks); continue; }
      // mẻ nào không chứa khối nào trong phần đã chọn thì bỏ hẳn, khỏi gọi model
      if (only && !(state.doc.chunk_ids?.[i] || []).some((id) => only.has(id))) {
        bar((i + 1) / state.chunks); continue;
      }
      status(`Đang dịch phần ${i + 1}/${state.chunks}${refine ? " (có soát lại)" : ""}…`);
      const d = await streamChunk(i, refine, mode, only);
      if (d?.reused) reusedTotal += d.reused;
      const tag = d?.reused
        ? ` (${d.reused} đoạn lấy lại từ bộ nhớ dịch, ${d.generated || 0} đoạn dịch mới)`
        : "";
      reportCost(`Xong phần ${i + 1}/${state.chunks}${tag}`, d?.run, d?.usage);
      bar((i + 1) / state.chunks);
    }
    const spent = ` — phiên này tốn ${money(state.session)}` +
      (reusedTotal ? `, ${reusedTotal} đoạn lấy lại miễn phí từ bộ nhớ dịch` : "") +
      (state.doc.usage?.cost ? `, cả bài ${money(state.doc.usage.cost)}` : "");
    status(stoppedAt >= 0
      ? `Đã dừng ở phần ${stoppedAt + 1}/${state.chunks}${spent}.` +
        " Bấm Dịch tiếp để chạy nốt — phần đã xong không dịch lại."
      : `Dịch xong${spent}. Bấm 💡 trên từng đoạn để xem nó đang làm gì trong lập luận.`);
  } catch (e) {
    status("Lỗi: " + e.message);
  } finally {
    state.translating = false;
    state.stopping = false;
    btn.disabled = false;
    $("#docModel").disabled = false;
    btn.textContent = "Dịch tiếp";
    $("#progress").classList.add("hidden");
    renderSide();
    renderUsage();
    syncSlidesBtn();      // dịch xong cả bài thì mở khoá nút dựng slide
  }

  function bar(f) { $("#progressBar").style.width = Math.round(f * 100) + "%"; }
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
    es.addEventListener("block", (e) => {
      const { id, vi, plain } = JSON.parse(e.data);
      if (plain !== undefined) {
        (state.doc.plain ||= {})[id] = plain;
        const g = $(`#p-${CSS.escape(id)} [data-gl]`);
        if (g) g.innerHTML = sci(plain);
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
      es.close(); resolve(d);
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
    if (!r.ok) throw new Error((await r.json()).detail || "lỗi");
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

/* ------------------------------------------------------------ hỏi đáp */

/* Model trả lời bằng Markdown và LaTeX. Gán thẳng vào textContent thì người đọc
   thấy nguyên dấu sao, dấu gạch đầu dòng và \[ \]. Đây là bộ dựng tối giản, chỉ
   nhận đúng những thứ model hay dùng.

   An toàn: escape TOÀN BỘ trước, rồi mới chèn thẻ. Nhờ vậy dù model trả về thẻ
   HTML hay <script> thì chúng cũng chỉ là chữ, không chạy được. */

/** Dọn ký hiệu LaTeX thuần trình bày. Cố ý không dựng công thức — cả tool này
    vốn giữ công thức ở dạng chữ, không render LaTeX. */
function tidyMath(s) {
  return s
    .replace(/\\text\s*\{([^{}]*)\}/g, "$1")
    .replace(/\\mathrm\s*\{([^{}]*)\}/g, "$1")
    .replace(/\\[,;:!]/g, " ")
    .replace(/\\\s/g, " ")
    .trim();
}

function mdInline(s) {
  return s
    .replace(/\^\{([^{}]*)\}/g, "<sup>$1</sup>")
    .replace(/_\{([^{}]*)\}/g, "<sub>$1</sub>")
    .replace(/`([^`]+)`/g, "<code>$1</code>")
    .replace(/\*\*([^*]+)\*\*/g, "<b>$1</b>")
    .replace(/(?<![*\w])\*([^*\n]+)\*(?!\w)/g, "<i>$1</i>")
    .replace(/\\\((.+?)\\\)/g, (_, m) => `<code>${tidyMath(m)}</code>`)
    .replace(/\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g,
             '<a href="$2" target="_blank" rel="noopener">$1</a>');
}

function renderMd(src) {
  const out = [];
  let list = null, quote = false, fence = null, math = null;
  const shut = () => {
    if (list) { out.push(`</${list}>`); list = null; }
    if (quote) { out.push("</blockquote>"); quote = false; }
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
  let painting = false;
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
    let buf = "", answer = "";
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
