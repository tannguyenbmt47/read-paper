/* Thư viện kiểu Zotero / Mendeley — màn #library.

   Ba khung: cây thư mục & nhãn (trái) · bảng bài báo sắp theo cột (giữa) · chi
   tiết bài đang chọn (phải). Nạp SAU app.js và survey.js nên dùng nhờ được
   `$`, `$$`, `esc`, `xacNhan`, `nhapChu`, `baoNhanh`, `tenBai`, `tiLeDich`,
   `dauTienDo`, `catGon`, `openDoc`, `showScreen`, `khongDau` — cùng lối với
   survey.js.

   Màn đầu (#start) vẫn giữ danh sách gọn để mở nhanh bài đang đọc; đây là chỗ
   QUẢN LÝ: biết bài nào của ai, năm nào, đăng ở đâu, gắn nhãn, xuất BibTeX.

   Thông tin thư mục học (tác giả, năm, nơi đăng, DOI) server tự tra ngầm sau
   khi nạp bài, qua Semantic Scholar / arXiv / Crossref — miễn phí. Trường nào
   người dùng đã sửa tay thì lượt tra sau không ghi đè (`db.set_meta`). */

const TV = {
  docs: [], folders: [], tags: [],
  loc: "all",                 // all | doing | todo | none | <mã thư mục> | tag:<nhãn>
  q: "",
  sort: { key: "them", dir: -1 },
  chon: new Set(),            // các bài đang chọn (Ctrl/⌘/Shift + bấm)
  focus: null,                // bài đang hiện ở khung chi tiết
  neo: null,                  // mốc cho Shift + bấm
};

async function tvOpen() {
  showScreen("library");
  if (location.hash !== "#thu-vien") location.hash = "thu-vien";
  await tvLoad();
}

async function tvLoad() {
  const [docs, folders, tags] = await Promise.all([
    fetch("/api/docs").then((r) => r.json()),
    fetch("/api/folders").then((r) => (r.ok ? r.json() : [])),
    fetch("/api/tags").then((r) => (r.ok ? r.json() : [])),
  ]);
  Object.assign(TV, { docs, folders, tags });
  // bài đã xoá ở chỗ khác thì bỏ khỏi tập chọn / khung chi tiết
  const con = new Set(docs.map((d) => d.id));
  TV.chon.forEach((id) => { if (!con.has(id)) TV.chon.delete(id); });
  if (TV.focus && !con.has(TV.focus)) TV.focus = null;
  if (TV.loc.startsWith("tag:") && !tags.some((t) => "tag:" + t.tag === TV.loc)) TV.loc = "all";
  if (!["all", "doing", "todo", "none"].includes(TV.loc) && !TV.loc.startsWith("tag:")
      && !folders.some((f) => f.id === TV.loc)) TV.loc = "all";
  tvNav();
  tvRows();
  tvDetail();
}

/* ------------------------------------------------------------ hiển thị */

/** Tên viết tắt của nơi đăng cho cột bảng. Tên đầy đủ để ở khung chi tiết —
    "Proceedings of the 64th Annual Meeting of the Association for Computational
    Linguistics (Volume 1: Long Papers)" không có chỗ trong một ô bảng. */
const TV_VENUE = [
  [/computational linguistics|\bACL\b/i, "ACL"], [/empirical methods|EMNLP/i, "EMNLP"],
  [/neural information processing|NeurIPS|NIPS/i, "NeurIPS"],
  [/learning representations|ICLR/i, "ICLR"], [/international conference on machine learning|ICML/i, "ICML"],
  [/computer vision and pattern|CVPR/i, "CVPR"], [/international conference on computer vision|ICCV/i, "ICCV"],
  [/european conference on computer vision|ECCV/i, "ECCV"], [/AAAI/i, "AAAI"],
  [/robot learning|CoRL/i, "CoRL"], [/robotics: science and systems|RSS/i, "RSS"],
  [/^arxiv/i, "arXiv"],
];
function tvVenue(v) {
  if (!v) return "";
  for (const [re, ten] of TV_VENUE) if (re.test(v)) return ten;
  return catGon(v, 28);
}

/** "Ha, Schmidhuber" · "Vaswani và cs." — đúng lối cột Creator của Zotero. */
function tvTacGia(m) {
  const ds = (m?.authors || []).map((a) => String(a).trim().split(/\s+/).pop());
  if (!ds.length) return "";
  if (ds.length === 1) return ds[0];
  if (ds.length === 2) return ds.join(", ");
  return ds[0] + " và cs.";
}

const tvNgay = (giay) => {
  if (!giay) return "";
  const d = new Date(giay * 1000);
  return `${String(d.getDate()).padStart(2, "0")}/${String(d.getMonth() + 1).padStart(2, "0")}/${d.getFullYear()}`;
};

const TV_SORT = {
  ten: (a, b) => tenBai(a).ten.localeCompare(tenBai(b).ten, "vi"),
  tacgia: (a, b) => tvTacGia(a.meta).localeCompare(tvTacGia(b.meta), "vi"),
  nam: (a, b) => (a.meta?.year || 0) - (b.meta?.year || 0),
  noidang: (a, b) => tvVenue(a.meta?.venue).localeCompare(tvVenue(b.meta?.venue), "vi"),
  them: (a, b) => (a.created_at || 0) - (b.created_at || 0),
  tiendo: (a, b) => tiLeDich(a) - tiLeDich(b),
};

/** Các bài đang hiện: lọc theo khung trái + ô tìm, rồi sắp theo cột đang chọn. */
function tvViewDocs() {
  let ds = TV.docs;
  const L = TV.loc;
  if (L === "doing") ds = ds.filter((d) => tiLeDich(d) > 0 && tiLeDich(d) < 1);
  else if (L === "todo") ds = ds.filter((d) => tiLeDich(d) === 0);
  else if (L === "none") ds = ds.filter((d) => !d.folder_id);
  else if (L.startsWith("tag:")) ds = ds.filter((d) => (d.tags || []).includes(L.slice(4)));
  else if (L !== "all") ds = ds.filter((d) => d.folder_id === L);
  const q = khongDau(TV.q.trim());
  if (q) {
    ds = ds.filter((d) => khongDau([tenBai(d).ten, d.title, d.meta?.title_goc,
      (d.meta?.authors || []).join(" "), d.meta?.venue, d.meta?.year,
      (d.tags || []).join(" "), d.source].join(" ")).includes(q));
  }
  const f = TV_SORT[TV.sort.key] || TV_SORT.them;
  return [...ds].sort((a, b) => TV.sort.dir * f(a, b) || (b.created_at - a.created_at));
}

function tvNav() {
  const n = (f) => TV.docs.filter(f).length;
  const muc = (loc, nhan, so, extra = "") =>
    `<li><button class="tv-loc${TV.loc === loc ? " is-on" : ""}" data-loc="${esc(loc)}"${extra}>`
    + `<span>${nhan}</span><i>${so}</i></button></li>`;
  const thuMuc = TV.folders.map((f) =>
    muc(f.id, `${ico("folder")}${esc(f.name)}`, f.count ?? n((d) => d.folder_id === f.id),
        ` data-folder="${esc(f.id)}" title="Bấm đúp để đổi tên · thả bài vào đây để chuyển"`)).join("");
  const nhan = TV.tags.map((t) => muc("tag:" + t.tag, `<b class="tv-tag">#</b>${esc(t.tag)}`, t.count)).join("");
  $("#tvNav").innerHTML = `
    <h3>Thư viện</h3>
    <ul>
      ${muc("all", `${ico("file")}Tất cả bài`, TV.docs.length)}
      ${muc("doing", "Đang dịch dở", n((d) => tiLeDich(d) > 0 && tiLeDich(d) < 1))}
      ${muc("todo", "Chưa dịch", n((d) => tiLeDich(d) === 0))}
      ${muc("none", "Chưa xếp thư mục", n((d) => !d.folder_id), ` data-folder="none"`)}
    </ul>
    <h3>Thư mục <button class="icon-btn tv-them" id="tvNewFolder" title="Tạo thư mục mới">${ico("folder-plus")}</button></h3>
    <ul>${thuMuc || `<li class="tv-trong">Chưa có thư mục nào</li>`}</ul>
    <h3>Nhãn</h3>
    <ul>${nhan || `<li class="tv-trong">Gắn nhãn ở khung chi tiết bên phải</li>`}</ul>`;

  $$("#tvNav [data-loc]").forEach((b) => {
    b.onclick = () => { TV.loc = b.dataset.loc; tvNav(); tvRows(); };
    if (b.dataset.folder && b.dataset.folder !== "none") {
      b.ondblclick = async () => {
        const f = TV.folders.find((x) => x.id === b.dataset.folder);
        const ten = await nhapChu("Đổi tên thư mục", "", f?.name || "");
        if (!ten || ten === f?.name) return;
        const r = await fetch(`/api/folders/${f.id}`, { method: "PATCH",
          headers: { "Content-Type": "application/json" }, body: JSON.stringify({ name: ten }) });
        if (!r.ok) return baoTin("Không đổi được tên", await apiErr(r, "lỗi"));
        tvLoad();
      };
    }
    // Thả bài vào thư mục — kéo một dòng ĐANG CHỌN là kéo cả nhóm.
    if (b.dataset.folder) {
      b.ondragover = (e) => { e.preventDefault(); b.classList.add("tha"); };
      b.ondragleave = () => b.classList.remove("tha");
      b.ondrop = async (e) => {
        e.preventDefault(); b.classList.remove("tha");
        const ids = JSON.parse(e.dataTransfer.getData("text/loupe-ids") || "[]");
        if (ids.length) tvMove(ids, b.dataset.folder === "none" ? null : b.dataset.folder);
      };
    }
  });
  $("#tvNewFolder").onclick = async () => {
    const ten = await nhapChu("Thư mục mới", "Ví dụ: Luận văn · RAG · Đọc tuần này", "");
    if (!ten) return;
    const r = await fetch("/api/folders", { method: "POST",
      headers: { "Content-Type": "application/json" }, body: JSON.stringify({ name: ten }) });
    if (!r.ok) return baoTin("Không tạo được thư mục", await apiErr(r, "lỗi"));
    tvLoad();
  };
}

function tvRows() {
  const ds = tvViewDocs();
  $$("#tvTable th[data-sort]").forEach((th) => {
    th.classList.toggle("is-sort", th.dataset.sort === TV.sort.key);
    th.dataset.dir = th.dataset.sort === TV.sort.key ? (TV.sort.dir > 0 ? "↑" : "↓") : "";
  });
  $("#tvRows").innerHTML = ds.map((d) => {
    const { ten, voDanh } = tenBai(d);
    const m = d.meta || {};
    const goc = m.title_goc && khongDau(m.title_goc) !== khongDau(ten) ? m.title_goc : "";
    const lop = [TV.chon.has(d.id) ? "da-chon" : "", TV.focus === d.id ? "is-focus" : ""].join(" ");
    return `<tr data-id="${esc(d.id)}" class="${lop}" draggable="true" tabindex="0">
      <td class="c-dau">${dauTienDo(d)}</td>
      <td class="c-ten"><b class="${voDanh ? "vo-danh" : ""}">${esc(ten)}</b>${
        goc ? `<span>${esc(goc)}</span>` : ""}${
        (d.tags || []).map((t) => `<em class="tv-chip">${esc(t)}</em>`).join("")}</td>
      <td class="c-tg" title="${esc((m.authors || []).join(", "))}">${esc(tvTacGia(m)) || `<i class="tv-thieu">—</i>`}</td>
      <td class="c-nam">${esc(m.year || "")}</td>
      <td class="c-nd" title="${esc(m.venue || "")}">${esc(tvVenue(m.venue))}</td>
      <td class="c-ngay">${esc(tvNgay(d.created_at))}</td></tr>`;
  }).join("");
  const rong = !ds.length;
  $("#tvEmpty").classList.toggle("hidden", !rong);
  $("#tvEmpty").textContent = TV.docs.length
    ? "Không có bài nào khớp — đổi bộ lọc bên trái hoặc ô tìm."
    : "Thư viện còn trống. Bấm ＋ Nạp bài để thêm bài đầu tiên.";
  tvSelBar();

  $$("#tvRows tr").forEach((tr) => {
    const id = tr.dataset.id;
    tr.onclick = (e) => tvClick(id, e);
    tr.ondblclick = () => openDoc(id);
    tr.onkeydown = (e) => {
      if (e.key === "Enter") openDoc(id);
      if (e.key === "ArrowDown" || e.key === "ArrowUp") {
        e.preventDefault();
        const nx = e.key === "ArrowDown" ? tr.nextElementSibling : tr.previousElementSibling;
        if (nx) { nx.focus(); tvClick(nx.dataset.id, {}); }
      }
    };
    tr.ondragstart = (e) => {
      const ids = TV.chon.has(id) ? [...TV.chon] : [id];
      e.dataTransfer.setData("text/loupe-ids", JSON.stringify(ids));
      e.dataTransfer.effectAllowed = "move";
    };
  });
}

/** Bấm dòng: chọn để xem chi tiết. Ctrl/⌘ bật/tắt một bài, Shift chọn cả dải —
    đúng lối của Zotero và trình quản lý file. Bấm đúp hoặc Enter để mở đọc. */
function tvClick(id, e) {
  const ds = tvViewDocs().map((d) => d.id);
  if (e.shiftKey && TV.neo && ds.includes(TV.neo)) {
    const [a, b] = [ds.indexOf(TV.neo), ds.indexOf(id)].sort((x, y) => x - y);
    ds.slice(a, b + 1).forEach((x) => TV.chon.add(x));
  } else if (e.ctrlKey || e.metaKey) {
    TV.chon.has(id) ? TV.chon.delete(id) : TV.chon.add(id);
    TV.neo = id;
  } else {
    // bấm thường = chọn ĐÚNG bài này (như Zotero), để Ctrl + bấm sau đó cộng thêm
    TV.chon = new Set([id]);
    TV.neo = id;
  }
  TV.focus = id;
  tvRows();
  tvDetail();
}

function tvSelBar() {
  const n = TV.chon.size;
  const bar = $("#tvSel");
  bar.classList.toggle("hidden", n < 2);
  if (n < 2) return;
  const tm = TV.folders.map((f) => `<option value="${esc(f.id)}">${esc(f.name)}</option>`).join("");
  bar.innerHTML = `<b>${n} bài đã chọn</b>
    <select id="tvSelMove" class="input input-sm" aria-label="Chuyển vào thư mục">
      <option value="">Chuyển vào thư mục…</option><option value="__none">Chưa xếp</option>${tm}</select>
    <button class="btn btn-sm" id="tvSelTag">Gắn nhãn</button>
    <button class="btn btn-sm" id="tvSelBib">BibTeX</button>
    <button class="btn btn-sm tv-xoa" id="tvSelDel">Xoá</button>
    <button class="btn btn-sm" id="tvSelNone">Bỏ chọn</button>`;
  $("#tvSelMove").onchange = (e) => {
    const v = e.target.value;
    if (v) tvMove([...TV.chon], v === "__none" ? null : v);
  };
  $("#tvSelTag").onclick = async () => {
    const t = await nhapChu("Gắn nhãn cho " + n + " bài", "Một nhãn, ví dụ: đọc sau · RAG · cần trích", "");
    if (!t) return;
    for (const id of TV.chon) {
      const d = TV.docs.find((x) => x.id === id);
      await tvPutTags(id, [...(d?.tags || []), t]);
    }
    tvLoad();
  };
  $("#tvSelBib").onclick = () => tvBib([...TV.chon]);
  $("#tvSelDel").onclick = () => tvDelete([...TV.chon]);
  $("#tvSelNone").onclick = () => { TV.chon.clear(); tvRows(); };
}

/* ------------------------------------------------------- khung chi tiết */

async function tvDetail() {
  const box = $("#tvDetail");
  const d = TV.docs.find((x) => x.id === TV.focus);
  if (!d) {
    box.innerHTML = `<div class="tv-goi-y"><b>Chọn một bài</b>
      <p>Bấm một dòng để xem và sửa thông tin. Bấm đúp để mở đọc.</p>
      <p><kbd>Ctrl</kbd>/<kbd>⌘</kbd> + bấm để chọn nhiều, <kbd>Shift</kbd> để chọn cả dải.
      Kéo bài thả vào thư mục bên trái để chuyển.</p></div>`;
    return;
  }
  const full = await fetch(`/api/doc/${d.id}/meta`).then((r) => (r.ok ? r.json() : { data: {} }));
  if (TV.focus !== d.id) return;          // người dùng đã bấm sang bài khác
  const m = full.data || {};
  const tay = new Set(full.sua_tay || []);
  const { ten } = tenBai(d);
  const pct = Math.round(tiLeDich(d) * 100);
  const nguon = { semanticscholar: "Semantic Scholar", arxiv: "arXiv", crossref: "Crossref", tay: "nhập tay" }[full.nguon] || "";
  const truong = (k, nhan, giaTri, ph, nhieu = false) => `
    <label class="tv-f"><span>${nhan}${tay.has(k) ? ` <i title="Đã sửa tay — lượt tra sau không ghi đè">✎</i>` : ""}</span>
    ${nhieu
      ? `<textarea class="input" data-meta="${k}" rows="2" placeholder="${esc(ph)}">${esc(giaTri)}</textarea>`
      : `<input class="input" data-meta="${k}" value="${esc(giaTri)}" placeholder="${esc(ph)}">`}</label>`;
  const lk = [
    m.doi ? `<a href="https://doi.org/${esc(m.doi)}" target="_blank" rel="noopener">DOI</a>` : "",
    m.arxiv ? `<a href="https://arxiv.org/abs/${esc(m.arxiv)}" target="_blank" rel="noopener">arXiv:${esc(m.arxiv)}</a>` : "",
    m.url && !m.arxiv ? `<a href="${esc(m.url)}" target="_blank" rel="noopener">Trang bài</a>` : "",
  ].filter(Boolean).join(" · ");
  const tm = [`<option value="">Chưa xếp</option>`].concat(TV.folders.map((f) =>
    `<option value="${esc(f.id)}"${f.id === d.folder_id ? " selected" : ""}>${esc(f.name)}</option>`)).join("");

  box.innerHTML = `
    <div class="tv-d-head">
      <h2>${esc(ten)}</h2>
      ${m.title_goc ? `<p class="tv-goc">${esc(m.title_goc)}</p>` : ""}
      <div class="tv-d-nut">
        <button class="btn btn-primary btn-sm" id="tvOpenDoc">Mở đọc →</button>
        <button class="btn btn-sm" id="tvRefetch" title="Tra lại từ Semantic Scholar / arXiv / Crossref. Trường đã sửa tay giữ nguyên.">Lấy lại thông tin</button>
      </div>
    </div>
    ${truong("authors", "Tác giả", (m.authors || []).join("; "), "Họ tên, ngăn bằng dấu ;", true)}
    <div class="tv-2">
      ${truong("year", "Năm", m.year || "", "2024")}
      ${truong("venue", "Nơi đăng", m.venue || "", "NeurIPS, ACL, arXiv…")}
    </div>
    <div class="tv-2">
      ${truong("doi", "DOI", m.doi || "", "10.xxxx/…")}
      ${truong("arxiv", "arXiv", m.arxiv || "", "2401.12345")}
    </div>
    ${lk ? `<p class="tv-lk">${lk}</p>` : ""}
    <label class="tv-f"><span>Nhãn</span>
      <div class="tv-tags" id="tvTags">${(d.tags || []).map((t) =>
        `<em class="tv-chip">${esc(t)}<button type="button" data-bo="${esc(t)}" aria-label="Bỏ nhãn ${esc(t)}">×</button></em>`).join("")}
        <input id="tvTagIn" list="tvTagList" placeholder="thêm nhãn rồi Enter"></div>
      <datalist id="tvTagList">${TV.tags.map((t) => `<option value="${esc(t.tag)}">`).join("")}</datalist></label>
    <label class="tv-f"><span>Thư mục</span><select class="input" id="tvFolder">${tm}</select></label>
    ${m.abstract ? `<details class="tv-abs"><summary>Tóm tắt (abstract)</summary><p>${esc(m.abstract)}</p></details>` : ""}
    <dl class="tv-so">
      <dt>Tiến độ</dt><dd>${pct >= 100 ? "đã dịch xong" : `đã dịch ${pct}%`} · ${d.blocks} khối</dd>
      <dt>Model</dt><dd>${esc(tenModel(d.model))}</dd>
      <dt>Đã tốn</dt><dd>${d.cost_usd ? "$" + (+d.cost_usd).toFixed(2).replace(".", ",") : "chưa tốn gì"}</dd>
      <dt>Đã thêm</dt><dd>${esc([tvNgay(d.created_at), catGon(tenNguon(d.source), 34)].filter(Boolean).join(" · "))}</dd>
      ${nguon ? `<dt>Thông tin từ</dt><dd>${esc(nguon)}</dd>` : ""}
    </dl>
    <div class="tv-d-cuoi">
      <button class="btn btn-sm" id="tvCopyBib">Chép BibTeX</button>
      <button class="btn btn-sm tv-xoa" id="tvDelOne">Xoá bài</button>
    </div>`;

  $("#tvOpenDoc").onclick = () => openDoc(d.id);
  $("#tvRefetch").onclick = async (e) => {
    e.target.disabled = true; e.target.textContent = "Đang tra…";
    const r = await fetch(`/api/doc/${d.id}/meta/fetch`, { method: "POST" });
    if (!r.ok) baoTin("Chưa tìm ra thông tin", await apiErr(r, "lỗi"));
    tvLoad();
  };
  // Lưu khi rời ô — không có nút Lưu, như Zotero. Chỉ gửi khi giá trị thật sự đổi.
  $$("#tvDetail [data-meta]").forEach((el) => {
    const cu = el.value;
    el.onchange = async () => {
      if (el.value === cu) return;
      const r = await fetch(`/api/doc/${d.id}/meta`, { method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ [el.dataset.meta]: el.value }) });
      if (!r.ok) { baoTin("Không lưu được", await apiErr(r, "lỗi")); el.value = cu; return; }
      baoNhanh("Đã lưu.");
      tvLoad();
    };
  });
  const them = async (t) => {
    t = (t || "").trim();
    if (!t) return;
    await tvPutTags(d.id, [...(d.tags || []), t]);
    tvLoad();
  };
  $("#tvTagIn").onkeydown = (e) => { if (e.key === "Enter") { e.preventDefault(); them(e.target.value); } };
  $("#tvTagIn").onchange = (e) => {             // chọn từ danh sách gợi ý
    if (TV.tags.some((t) => t.tag === e.target.value)) them(e.target.value);
  };
  $$("#tvTags [data-bo]").forEach((b) => (b.onclick = async () => {
    await tvPutTags(d.id, (d.tags || []).filter((t) => t !== b.dataset.bo));
    tvLoad();
  }));
  $("#tvFolder").onchange = (e) => tvMove([d.id], e.target.value || null);
  $("#tvCopyBib").onclick = async () => {
    const t = await fetch(`/api/docs/bibtex?ids=${d.id}`).then((r) => r.text());
    try { await navigator.clipboard.writeText(t); baoNhanh("Đã chép BibTeX."); }
    catch { baoTin("BibTeX", t); }
  };
  $("#tvDelOne").onclick = () => tvDelete([d.id]);
}

/* ------------------------------------------------------------- thao tác */

async function tvPutTags(id, tags) {
  const r = await fetch(`/api/doc/${id}/tags`, { method: "PUT",
    headers: { "Content-Type": "application/json" }, body: JSON.stringify({ tags }) });
  if (!r.ok) baoTin("Không gắn được nhãn", await apiErr(r, "lỗi"));
}

async function tvMove(ids, folderId) {
  const truoc = ids.map((id) => [id, TV.docs.find((d) => d.id === id)?.folder_id || null]);
  const r = await fetch("/api/docs/move", { method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ ids, folder_id: folderId }) });
  if (!r.ok) return baoTin("Không chuyển được", await apiErr(r, "lỗi"));
  const ten = folderId ? TV.folders.find((f) => f.id === folderId)?.name : "Chưa xếp";
  // Hoàn lại theo TỪNG thư mục cũ — các bài có thể đến từ nhiều chỗ khác nhau.
  baoNhanh(`Đã chuyển ${ids.length} bài vào “${ten}”.`, async () => {
    const theo = new Map();
    truoc.forEach(([id, f]) => theo.set(f, [...(theo.get(f) || []), id]));
    for (const [f, ds] of theo) {
      await fetch("/api/docs/move", { method: "POST",
        headers: { "Content-Type": "application/json" }, body: JSON.stringify({ ids: ds, folder_id: f }) });
    }
    tvLoad();
  });
  tvLoad();
}

async function tvDelete(ids) {
  const ds = ids.map((id) => TV.docs.find((d) => d.id === id)).filter(Boolean);
  const tien = ds.reduce((s, d) => s + (+d.cost_usd || 0), 0);
  const ok = await xacNhan(`Xoá ${ds.length} bài?`,
    ds.slice(0, 8).map((d) => "• " + tenBai(d).ten).join("\n")
    + (ds.length > 8 ? `\n… và ${ds.length - 8} bài nữa` : "")
    + `\n\nBản dịch, ghi chú, vệt bôi và slide của chúng mất hẳn — đã tốn $${tien.toFixed(2).replace(".", ",")} để dựng. Không hoàn lại được.`,
    { ok: "Xoá hẳn", cancel: "Thôi", hong: true });
  if (!ok) return;
  await fetch("/api/docs/delete", { method: "POST",
    headers: { "Content-Type": "application/json" }, body: JSON.stringify({ ids }) });
  ids.forEach((id) => TV.chon.delete(id));
  if (ids.includes(TV.focus)) TV.focus = null;
  tvLoad();
}

function tvBib(ids) {
  const q = ids?.length ? `ids=${ids.join(",")}`
    : TV.loc.startsWith("tag:") ? `tag=${encodeURIComponent(TV.loc.slice(4))}`
      : !["all", "doing", "todo"].includes(TV.loc) ? `folder=${encodeURIComponent(TV.loc)}`
        : `ids=${tvViewDocs().map((d) => d.id).join(",")}`;
  window.open(`/api/docs/bibtex?${q}`, "_blank");
}

/* ---------------------------------------------------------------- nối dây */

(function wireThuVienZotero() {
  $("#tvQ").addEventListener("input", (e) => { TV.q = e.target.value; tvRows(); });
  $$("#tvTable th[data-sort]").forEach((th) => (th.onclick = () => {
    const k = th.dataset.sort;
    TV.sort = TV.sort.key === k ? { key: k, dir: -TV.sort.dir } : { key: k, dir: k === "them" || k === "nam" ? -1 : 1 };
    tvRows();
  }));
  $("#tvAdd").onclick = () => { showScreen("start"); location.hash = ""; };
  $("#goLibrary")?.addEventListener("click", tvOpen);
  $("#tvBib").onclick = () => tvBib(TV.chon.size ? [...TV.chon] : null);
  $("#tvFetch").onclick = async (e) => {
    const b = e.target;
    const thieu = TV.docs.filter((d) => !d.meta || !Object.keys(d.meta).length).length;
    if (!thieu) return baoNhanh("Bài nào cũng đã có thông tin.");
    b.disabled = true; b.textContent = `Đang tra ${thieu} bài…`;
    try {
      const r = await fetch("/api/docs/meta/fetch-missing", { method: "POST" }).then((x) => x.json());
      baoNhanh(`Tìm được ${r.found} bài` + (r.missing ? `, ${r.missing} bài chưa tìm ra — điền tay ở khung bên phải.` : "."));
    } finally {
      b.disabled = false; b.textContent = "Lấy thông tin còn thiếu";
      tvLoad();
    }
  };
  document.addEventListener("keydown", (e) => {
    if ($("#library").classList.contains("hidden")) return;
    if (e.key === "Escape" && TV.chon.size) { TV.chon.clear(); tvRows(); }
    if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "a" && !/^(INPUT|TEXTAREA)$/.test(e.target.tagName)) {
      e.preventDefault();
      tvViewDocs().forEach((d) => TV.chon.add(d.id));
      tvRows();
    }
  });
  if (docHash().kind === "library") tvOpen();
})();
