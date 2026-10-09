/* Màn slide v2 — tạo, sửa tại chỗ, trình chiếu, tải về.

   Vẽ slide là việc của `SlideVe.ve()` (web/slide-ve.js), dùng chung với file tải
   về. File này chỉ lo tương tác. Nạp sau app.js nên dùng nhờ `$`, `$$`, `esc`,
   `xacNhan`, `baoTin`, `baoNhanh`, `apiErr`, `money`, `showScreen`, `state`.

   Luồng cố ý chỉ có MỘT nút tốn tiền ("Tạo slide", kèm giá ước tính) — bản cũ
   có hai nút tốn tiền khác nhau (soạn dàn ý, dựng slide) và ~12 điều khiển trên
   mỗi slide (báo cáo test S10, nhận xét chuyên gia D1). Sửa thì bấm thẳng vào
   chữ trên slide; đổi hình thì bấm vào hình. */

const SLD = { bo: [], chon: null, phut: 15, dangTao: false };

const sldAnh = (s) => (s.anh ? `/api/doc/${state.doc.id}/img/${s.anh}.png?v=${s.anh_ver || ""}` : "");
const sldCtx = (sua = false) => ({
  bo: SLD.bo, anh: sldAnh, sua,
  ten_bai: state.doc?.brief?.title_vi || state.doc?.title || "",
});

/** Nút "Slide" trên thanh đọc: cần tóm lược (mạch lập luận) — không cần dịch xong,
    vì slide dựng từ toàn văn gốc + mạch lập luận đã chốt. */
function syncSlidesBtn() {
  const b = $("#slidesBtn");
  if (!b || !state.doc) return;
  b.disabled = false;
  b.title = state.doc.brief
    ? "Bộ slide trình bày bài này — bố cục theo mạch lập luận, sửa ngay trên slide"
    : "Bộ slide — cần bấm Dịch trước để tool đọc toàn bài và chốt mạch lập luận";
}

async function openSlides() {
  showScreen("slides");
  $("#slTen").textContent = state.doc.brief?.title_vi || state.doc.title || "";
  const r = await fetch(`/api/doc/${state.doc.id}/slides`);
  const d = r.ok ? await r.json() : { bo: [] };
  SLD.bo = d.bo || [];
  SLD.phut = d.phut || 15;
  $("#slPhut").value = String(SLD.phut);
  if (!SLD.bo.some((s) => s.id === SLD.chon)) SLD.chon = SLD.bo[0]?.id || null;
  sldVe();
}

function sldVe() {
  const co = SLD.bo.length > 0;
  $("#slKhung").classList.toggle("hidden", !co);
  $("#slTrong").classList.toggle("hidden", co);
  // Chưa có bộ thì màn trống đã có ô chọn độ dài + nút tạo; để thêm một bộ trên
  // thanh trên là hai ô chọn cùng việc, và nút Trình chiếu xám chẳng để làm gì.
  for (const id of ["#slPhut", "#slTao", "#slChieu"]) $(id).classList.toggle("hidden", !co);
  $("#slTai").closest(".menu").classList.toggle("hidden", !co);
  if (!co) return sldTrong();
  sldDai();
  sldSan();
}

/* ------------------------------------------------------------ màn trống */

async function sldTrong() {
  const coBrief = !!state.doc.brief;
  $("#slTrong").innerHTML = `
    <div class="sl2-trong-hop">
      <h2>Chưa có bộ slide</h2>
      <p>Tool đọc toàn bài cùng mạch lập luận đã chốt, rồi viết nội dung cho từng slide.
        Mỗi slide mang một vai trong lập luận (vấn đề, khoảng trống, ý tưởng, cơ chế,
        bằng chứng, giới hạn), và mỗi vai có bố cục riêng. Vì vậy bộ slide đổi dáng
        theo đúng mạch bài. Muốn sửa thì bấm thẳng vào chữ trên slide.</p>
      <div class="sl2-trong-chon">
        <label for="slPhutTrong">Độ dài buổi nói</label>
        <select id="slPhutTrong" class="input input-sm">
          <option value="10">10 phút · ~10 slide</option>
          <option value="15" selected>15 phút · ~13 slide</option>
          <option value="20">20 phút · ~16 slide</option>
        </select>
      </div>
      <button id="slTaoTrong" class="btn btn-primary" ${coBrief ? "" : "disabled"}>Tạo slide</button>
      <p class="hint" id="slGiaTrong">${coBrief ? "Đang ước giá…"
        : "Cần bấm <b>Dịch</b> trước. Slide dựng từ mạch lập luận mà lượt dịch đầu tiên chốt lại."}</p>
    </div>`;
  $("#slPhutTrong").value = String(SLD.phut);
  $("#slPhutTrong").onchange = (e) => { SLD.phut = +e.target.value; sldGia(); };
  $("#slTaoTrong").onclick = () => sldTao();
  if (coBrief) sldGia();
}

async function sldGia() {
  const el = $("#slGiaTrong");
  try {
    const g = await fetch(`/api/doc/${state.doc.id}/slides/gia?phut=${SLD.phut}`).then((r) => r.json());
    const txt = g.lo != null ? `~${money(g.lo)}–${money(g.hi)} · một lượt gọi model · khoảng 1 phút` : "một lượt gọi model · khoảng 1 phút";
    if (el) el.textContent = txt;
    const b = $("#slTaoTrong");
    if (b && g.lo != null) b.textContent = `Tạo slide · ~${money(g.hi)}`;
  } catch { if (el) el.textContent = ""; }
}

async function sldTao() {
  if (SLD.dangTao) return;
  if (SLD.bo.length) {
    // Nói giá thật: bản đầu ghi "dưới 1 xu" cho một lượt đo được $0,047.
    let gia = "";
    try {
      const g = await (await fetch(`/api/doc/${state.doc.id}/slides/gia?phut=${SLD.phut}`)).json();
      if (g.lo != null) gia = ` Ước ${money(g.lo)}–${money(g.hi)}.`;
    } catch { /* không ước được giá thì vẫn hỏi */ }
    if (!(await xacNhan("Tạo lại cả bộ slide?",
      `Bộ hiện tại sẽ được thay bằng bộ mới, kể cả chữ bạn đã sửa tay.\nTốn một lượt gọi model.${gia}`,
      { ok: "Tạo lại", cancel: "Thôi" }))) return;
  }
  SLD.dangTao = true;
  const nut = [$("#slTao"), $("#slTaoTrong")].filter(Boolean);
  const t0 = Date.now();
  const dh = setInterval(() => {
    const giay = Math.round((Date.now() - t0) / 1000);
    nut.forEach((b) => { b.disabled = true; b.textContent = `Đang soạn… ${giay} giây`; });
  }, 500);
  try {
    const r = await fetch(`/api/doc/${state.doc.id}/slides/tao`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ phut: SLD.phut }),
    });
    if (!r.ok) throw new Error(await apiErr(r, "Không soạn được slide"));
    const d = await r.json();
    SLD.bo = d.slides.bo || [];
    SLD.chon = SLD.bo[0]?.id || null;
    state.doc.usage = d.total;
    sldVe();
    baoNhanh(`Đã soạn ${SLD.bo.length} slide · ${money(d.run.cost)}`);
  } catch (e) {
    // Lỗi phải nói bằng lời và mời thử lại — không đẩy exception thô (S11).
    if (await xacNhan("Chưa soạn được slide", e.message, { ok: "Thử lại", cancel: "Để sau" })) {
      SLD.dangTao = false; clearInterval(dh);
      return sldTao();
    }
  } finally {
    clearInterval(dh);
    SLD.dangTao = false;
    nut.forEach((b) => { b.disabled = false; });
    if ($("#slTao")) $("#slTao").textContent = "Tạo lại cả bộ";
    if ($("#slTaoTrong")) $("#slTaoTrong").textContent = "Tạo slide";
  }
}

/* ------------------------------------------------------------ dải thu nhỏ */

function sldDai() {
  const ctx = sldCtx(false);
  $("#slDai").innerHTML = SLD.bo.map((s, i) => `
    <div class="sl2-thu${s.id === SLD.chon ? " is-on" : ""}" data-id="${esc(s.id)}" draggable="true"
         tabindex="0" title="${esc(SlideVe.NHAN_VAI[s.vai] || "")}">
      <span class="sl2-thu-so">${i + 1}${(s.canh_bao || []).length ? '<i title="Có cảnh báo">!</i>' : ""}</span>
      ${SlideVe.ve(s, ctx)}</div>`).join("");
  $$("#slDai .sl2-thu").forEach((el) => {
    el.onclick = () => { SLD.chon = el.dataset.id; sldDai(); sldSan(); };
    el.onkeydown = (e) => {
      const i = SLD.bo.findIndex((s) => s.id === el.dataset.id);
      if (e.key === "ArrowDown" || e.key === "ArrowUp") {
        e.preventDefault();
        const nx = SLD.bo[i + (e.key === "ArrowDown" ? 1 : -1)];
        if (nx) { SLD.chon = nx.id; sldDai(); sldSan(); $(`#slDai [data-id="${nx.id}"]`)?.focus(); }
      }
    };
    // Kéo để đổi thứ tự — thả lên một thẻ khác là chèn vào trước thẻ đó.
    el.ondragstart = (e) => { e.dataTransfer.setData("text/sld", el.dataset.id); el.classList.add("keo"); };
    el.ondragend = () => el.classList.remove("keo");
    el.ondragover = (e) => { e.preventDefault(); el.classList.add("tha"); };
    el.ondragleave = () => el.classList.remove("tha");
    el.ondrop = async (e) => {
      e.preventDefault(); el.classList.remove("tha");
      const tu = e.dataTransfer.getData("text/sld");
      if (!tu || tu === el.dataset.id) return;
      const ids = SLD.bo.map((s) => s.id).filter((x) => x !== tu);
      ids.splice(ids.indexOf(el.dataset.id), 0, tu);
      await sldGui(`/slides/thu-tu`, "POST", { ids });
    };
  });
  requestAnimationFrame(() => $$("#slDai .sld").forEach(SlideVe.vuaKhung));
}

/* ------------------------------------------------------------ sân khấu sửa */

function sldHienTai() { return SLD.bo.find((s) => s.id === SLD.chon); }

function sldSan() {
  const s = sldHienTai();
  if (!s) return;
  $("#slSan").innerHTML = SlideVe.ve(s, sldCtx(true));
  const el = $("#slSan .sld");
  SlideVe.vuaKhung(el);
  $("#slLoi").value = s.loi_noi || "";
  const coVai = !!SlideVe.NHAN_VAI[s.vai];
  $("#slVietLai").disabled = !coVai;
  $("#slVietLai").title = coVai ? "Nhờ model viết lại riêng slide này, giữ nguyên vai. Rẻ — toàn văn bài đi qua cache."
    : "Slide này dựng từ thông tin bài, sửa trực tiếp trên slide";
  const cb = s.canh_bao || [];
  $("#slCanh").classList.toggle("hidden", !cb.length);
  $("#slCanh").innerHTML = cb.length ? `<b>Cần soát</b><ul>${cb.map((c) => `<li>${esc(c)}</li>`).join("")}</ul>` : "";

  // Sửa tại chỗ: rời ô là lưu. Enter = xong (Shift+Enter xuống dòng), Esc = huỷ.
  $$("#slSan [data-p]").forEach((o) => {
    const cu = o.innerText;
    o.onkeydown = (e) => {
      if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); o.blur(); }
      if (e.key === "Escape") { o.innerText = cu; o.blur(); }
    };
    o.onblur = () => {
      const moi = o.innerText.replace(/\s+\n/g, "\n").trim();
      if (moi === cu.trim()) return;
      sldLuu(o.dataset.p, moi);
    };
  });
  $$("#slSan [data-chon-hinh]").forEach((f) => (f.onclick = sldMoHinh));
}

/** Ghi một trường theo đường dẫn `buoc.1.mo_ta` — gửi cả trường gốc (`buoc`). */
async function sldLuu(path, val) {
  const s = sldHienTai();
  const ks = path.split(".");
  const goc = ks[0];
  let gt = val;
  if (ks.length > 1) {
    gt = JSON.parse(JSON.stringify(s[goc] || []));
    let o = gt;
    for (let i = 1; i < ks.length - 1; i++) o = o[ks[i]];
    o[ks[ks.length - 1]] = val;
  }
  await sldGui(`/slides/${s.id}`, "PATCH", { [goc]: gt }, "Đã lưu.");
}

async function sldGui(duong, method, body, xong = "") {
  try {
    const r = await fetch(`/api/doc/${state.doc.id}${duong}`, {
      method, headers: { "Content-Type": "application/json" },
      body: body ? JSON.stringify(body) : undefined,
    });
    if (!r.ok) throw new Error(await apiErr(r, "Không lưu được"));
    const d = await r.json();
    SLD.bo = (d.bo || d.slides?.bo) || SLD.bo;
    if (!SLD.bo.some((s) => s.id === SLD.chon)) SLD.chon = SLD.bo[0]?.id || null;
    sldVe();
    if (xong) $("#slTrangThai").textContent = xong;
    return d;
  } catch (e) {
    baoTin("Không lưu được", e.message);
    sldSan();
  }
}

/* ------------------------------------------------------------ chọn hình */

function sldMoHinh() {
  const ds = (state.doc.blocks || []).filter((b) => b.figure && b.type !== "equation");
  const s = sldHienTai();
  $("#slHinhLuoi").innerHTML = ds.length ? ds.map((b) => `
    <button type="button" class="sl2-hinh-o${b.id === s.hinh ? " is-on" : ""}" data-hinh="${esc(b.id)}">
      <img src="/api/doc/${state.doc.id}/img/${esc(b.figure)}.png?v=${esc((b.figure_rect || []).map(Math.round).join("_"))}" alt="" loading="lazy">
      <span>${esc((state.doc.translations?.[b.id] || b.text || "").slice(0, 90))}</span></button>`).join("")
    : `<p class="hint">Bài này không có hình/bảng nào cắt được.</p>`;
  $("#slHinh").classList.remove("hidden");
  $$("#slHinhLuoi [data-hinh]").forEach((b) => (b.onclick = async () => {
    $("#slHinh").classList.add("hidden");
    await sldGui(`/slides/${s.id}`, "PATCH", { hinh: b.dataset.hinh }, "Đã đổi hình.");
  }));
}

/* ------------------------------------------------------------ trình chiếu */

let sldChieuI = 0;
function sldChieu(i) {
  if (!SLD.bo.length) return;
  sldChieuI = Math.max(0, Math.min(i, SLD.bo.length - 1));
  const s = SLD.bo[sldChieuI];
  $("#presentStage").innerHTML = SlideVe.ve(s, sldCtx(false));
  requestAnimationFrame(() => SlideVe.vuaKhung($("#presentStage .sld")));
  $("#presentNotes").textContent = s.loi_noi || "(slide này chưa có lời nói)";
  $("#presentNum").textContent = `${sldChieuI + 1} / ${SLD.bo.length}`;
}
function sldMoChieu() {
  if (!SLD.bo.length) return;
  $("#present").classList.remove("hidden");
  sldChieu(Math.max(0, SLD.bo.findIndex((s) => s.id === SLD.chon)));
  document.documentElement.requestFullscreen?.().catch(() => {});
}
function sldDongChieu() {
  $("#present").classList.add("hidden");
  $("#presentNotes").classList.add("hidden");
  if (document.fullscreenElement) document.exitFullscreen?.().catch(() => {});
}

/* ------------------------------------------------------------ nối dây */

(function wireSlideV2() {
  $("#slidesBtn")?.addEventListener("click", () => openSlides());
  $("#slBack").onclick = () => { showScreen("reader"); location.hash = state.doc.id; };
  $("#slPhut").onchange = (e) => { SLD.phut = +e.target.value; };
  $("#slTao").onclick = () => sldTao();
  $("#slChieu").onclick = sldMoChieu;
  $("#slVietLai").onclick = async (e) => {
    const s = sldHienTai();
    if (!s) return;
    const b = e.target;
    b.disabled = true; b.textContent = "Đang viết lại…";
    try {
      const d = await sldGui(`/slides/${s.id}/viet-lai`, "POST", { goi_y: $("#slGoiY").value });
      if (d?.run) { state.doc.usage = d.total; $("#slTrangThai").textContent = `Đã viết lại · ${money(d.run.cost)}`; }
      $("#slGoiY").value = "";
    } finally { b.disabled = false; b.textContent = "↻ Viết lại slide này"; }
  };
  $("#slXoa").onclick = async () => {
    const s = sldHienTai();
    if (!s || !(await xacNhan("Xoá slide này?", s.tieu_de || "", { ok: "Xoá", cancel: "Thôi", hong: true }))) return;
    await sldGui(`/slides/${s.id}`, "DELETE", null, "Đã xoá slide.");
  };
  $("#slLoi").onchange = (e) => sldLuu("loi_noi", e.target.value.trim());
  $("#slHinhDong").onclick = () => $("#slHinh").classList.add("hidden");
  $("#slHinh").onmousedown = (e) => { if (e.target.id === "slHinh") $("#slHinh").classList.add("hidden"); };
  $("#slHinhBo").onclick = async () => {
    $("#slHinh").classList.add("hidden");
    const s = sldHienTai();
    if (s) await sldGui(`/slides/${s.id}`, "PATCH", { hinh: "" }, "Đã bỏ hình.");
  };
  $("#slTai").onclick = (e) => { e.stopPropagation(); $("#slTaiMenu").classList.toggle("hidden"); };
  document.addEventListener("click", () => $("#slTaiMenu")?.classList.add("hidden"));
  $$("#slTaiMenu a").forEach((a) => (a.onclick = (e) => {
    e.preventDefault();
    window.open(`/api/doc/${state.doc.id}/export?fmt=${a.dataset.fmt}`, "_blank");
    if (a.dataset.fmt === "slides-pdf") {
      $("#slTrangThai").textContent = "Trang in đã mở ở tab mới — chọn khổ ngang và “Lưu thành PDF”.";
    }
  }));
  document.addEventListener("keydown", (e) => {
    const chieu = !$("#present").classList.contains("hidden");
    if (chieu) {
      if (["ArrowRight", "PageDown", " "].includes(e.key)) { e.preventDefault(); sldChieu(sldChieuI + 1); }
      else if (["ArrowLeft", "PageUp"].includes(e.key)) { e.preventDefault(); sldChieu(sldChieuI - 1); }
      else if (e.key === "Escape") sldDongChieu();
      else if (e.key.toLowerCase() === "s") $("#presentNotes").classList.toggle("hidden");
      return;
    }
    if (!$("#slides").classList.contains("hidden") && e.key === "F5") { e.preventDefault(); sldMoChieu(); }
    if (e.key === "Escape" && !$("#slHinh").classList.contains("hidden")) $("#slHinh").classList.add("hidden");
  });
  $("#present").addEventListener("click", (e) => {
    if (e.target.closest(".present-bar, .present-notes")) return;
    sldChieu(sldChieuI + 1);
  });
})();
