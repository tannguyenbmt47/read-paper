/* Loupe — màn Bản đồ của kho survey.

   Màn chính của phần Tìm hiểu, và là màn MỞ RA ĐẦU TIÊN. Lý do: các tab kia đều
   đòi người dùng làm gì đó trước khi thấy được gì — Tổng hợp và Bài giảng phải
   bấm dựng (tốn tiền), Hỏi đáp phải biết trước mình cần hỏi gì. Bản đồ thì có
   ngay, miễn phí, và trả lời đúng câu đầu tiên của người làm survey: *các bài này
   nối với nhau ở đâu*.

   Bài và thực thể (phương pháp, tập dữ liệu, độ đo…) nằm trên cùng một mặt phẳng.
   Thực thể chung của hai bài nằm giữa hai bài đó — đó chính là cây cầu. Bấm một
   điểm thì thấy nó nối với gì, đoạn nào nói về nó, và từ đó hỏi tiếp.

   Bố cục là Fruchterman–Reingold chạy MỘT LƯỢT rồi đứng yên, không mô phỏng
   liên tục: đồ thị cứ rung rinh thì không đọc được nhãn, và kéo một điểm cũng
   chỉ dời đúng điểm đó chứ không làm cả bản đồ trôi đi.

   Mọi tên ngoài cùng nằm trong `BanDo` — file này chung phạm vi toàn cục với
   app.js và survey.js (xem chú thích về `money` ở đầu survey.js). */

const BanDo = (() => {
  // Loại thực thể → nhãn + màu bôi (token `--c-*`). Trùng với KINDS bên graph.py.
  const LOAI = {
    method: ["Phương pháp", "b"], model: ["Mô hình", "v"], dataset: ["Tập dữ liệu", "g"],
    metric: ["Độ đo", "p"], task: ["Bài toán", "y"], concept: ["Khái niệm", ""],
    org: ["Tổ chức", ""],
  };
  // Trần số thực thể vẽ một lúc. Quá trần thì giữ thực thể chung trước, rồi
  // thực thể có nhiều quan hệ — một bức tường điểm thì không ai đọc được.
  const TRAN = 140;

  const S = {
    sid: "", data: null, nodes: [], links: [], by: {}, ke: {},
    chon: "", an: new Set(), chiChung: false,
    k: 1, x: 0, y: 0, viTri: {}, gia: null, dang: false,
  };

  const $b = (sel) => document.querySelector(sel);

  /* ------------------------------------------------------------ dữ liệu */

  async function mo(sid) {
    const moi = sid !== S.sid;
    S.sid = sid;
    if (moi) { S.chon = ""; S.viTri = {}; S.an.clear(); }
    $b("#bdMsg").textContent = "đang nạp…";
    let d;
    try {
      d = await svFetch(`/api/survey/${sid}/ban-do`);
    } catch (e) {
      $b("#bdMsg").textContent = "Không nạp được bản đồ: " + e.message;
      return;
    }
    if (sid !== S.sid) return;
    S.data = d;
    if (moi) {
      // Kho nhiều thực thể thì mở sẵn ở chế độ "điểm chung": đúng câu hỏi đầu
      // tiên, và đỡ một bức tường điểm.
      const chung = d.entities.filter((e) => e.papers.length > 1).length;
      S.chiChung = d.entities.length > TRAN && chung >= 8;
    }
    $b("#bdChung").checked = S.chiChung;
    veLoai();
    dung();
    if (moi || !S.k) vuaKhung();
    ve();
    if (S.chon && !S.by[S.chon]) S.chon = "";
    S.chon ? hien(S.chon) : tongQuan();
    giaDung().catch(() => {});
  }

  function dung() {
    const d = S.data;
    let ents = d.entities.filter((e) => !S.an.has(e.kind) && (!S.chiChung || e.papers.length > 1));
    const bac = {};
    d.edges.forEach((g) => { bac[g.src] = (bac[g.src] || 0) + 1; bac[g.dst] = (bac[g.dst] || 0) + 1; });
    if (ents.length > TRAN) {
      ents = ents.slice().sort((a, b) => (b.papers.length - a.papers.length)
        || ((bac[b.id] || 0) - (bac[a.id] || 0))).slice(0, TRAN);
    }
    const nodes = d.papers.map((p) => ({ id: "P" + p.id, bai: true, ref: p, m: 4 }));
    ents.forEach((e) => nodes.push({ id: e.id, bai: false, ref: e, m: 1, bac: bac[e.id] || 0,
      r: 5 + 3.2 * Math.sqrt(e.papers.length + (bac[e.id] || 0) / 2) }));
    const by = Object.fromEntries(nodes.map((n) => [n.id, n]));
    const links = [];
    ents.forEach((e) => e.papers.forEach((pid) => {
      if (by["P" + pid]) links.push({ a: by["P" + pid], b: by[e.id], qh: false });
    }));
    const cap = {};          // một cặp thực thể chỉ vẽ một nét, dù nhiều bài cùng nói
    d.edges.forEach((g) => {
      if (!by[g.src] || !by[g.dst]) return;
      const key = g.src < g.dst ? g.src + g.dst : g.dst + g.src;
      if (cap[key]) { cap[key].rel.push(g); return; }
      cap[key] = { a: by[g.src], b: by[g.dst], qh: true, rel: [g] };
      links.push(cap[key]);
    });
    const ke = {};
    links.forEach((l) => {
      (ke[l.a.id] = ke[l.a.id] || new Set()).add(l.b.id);
      (ke[l.b.id] = ke[l.b.id] || new Set()).add(l.a.id);
    });
    S.nodes = nodes; S.links = links; S.by = by; S.ke = ke;
    if (boCuc()) {
      // Tách chồng phải biết mức phóng THẬT: cỡ điểm cố định trên màn, nên ở
      // mức 0,5 một thẻ bài chiếm gấp đôi chỗ trên mặt phẳng so với ở mức 1.
      vuaKhung();
      tachChong(nodes, S.k);
      nodes.forEach((n) => { S.viTri[n.id] = { x: n.x, y: n.y }; });
      vuaKhung();
    }
  }

  /* ------------------------------------------------------------ bố cục */

  // Số giả ngẫu nhiên theo mã: cùng một kho mở lại phải ra cùng một bản đồ,
  // không thì người dùng mất mốc "bài X nằm góc trên bên trái".
  function bam(s) {
    let h = 2166136261;
    for (let i = 0; i < s.length; i++) h = Math.imul(h ^ s.charCodeAt(i), 16777619);
    return ((h >>> 0) % 10000) / 10000;
  }

  function boCuc() {
    const N = S.nodes;
    const bai = N.filter((n) => n.bai);
    // Khoảng cách lý tưởng tính theo DIỆN TÍCH sân vẽ, đúng công thức gốc của
    // Fruchterman–Reingold: K = C·√(diện tích / số điểm). Để K cố định thì kho
    // đông điểm bị thu nhỏ còn nửa — mà cỡ điểm và nhãn cố định trên màn (xem
    // `dat`), nên thu nhỏ là chúng chồng lên nhau. K theo sân thì bản đồ vừa
    // khung ở mức phóng ~1, đúng mức mà phép tách chồng tính cho.
    const svg = $b("#bdSvg");
    const W = Math.max(360, (svg.clientWidth || 900) - 160), H = Math.max(300, (svg.clientHeight || 600) - 60);
    const K = Math.min(90, Math.max(26, 0.62 * Math.sqrt((W * H) / Math.max(1, N.length))));
    const tl = H / W;
    const R = Math.min(W, H / tl) * (bai.length > 1 ? 0.36 : 0);
    bai.forEach((n, i) => {
      const cu = S.viTri[n.id];
      // Bắt đầu từ bên TRÁI và trải theo bề ngang: sân vẽ là khổ ngang, hai bài
      // xếp trên–dưới thì cả bản đồ thành một cột hẹp và phải thu nhỏ mới vừa.
      const t = (i / Math.max(1, bai.length)) * Math.PI * 2 + Math.PI;
      n.x = cu ? cu.x : R * Math.cos(t);
      n.y = cu ? cu.y : R * Math.sin(t) * tl;
    });
    N.filter((n) => !n.bai).forEach((n) => {
      const cu = S.viTri[n.id];
      if (cu) { n.x = cu.x; n.y = cu.y; return; }
      const ps = n.ref.papers.map((p) => S.by["P" + p]).filter(Boolean);
      const cx = ps.reduce((s, p) => s + p.x, 0) / (ps.length || 1);
      const cy = ps.reduce((s, p) => s + p.y, 0) / (ps.length || 1);
      // Thực thể của một bài thì toả ra phía ngoài bài đó, thực thể chung thì
      // nằm giữa các bài — vị trí ban đầu đã gần đúng, FR chỉ phải gỡ chỗ chồng.
      const goc = bam(n.id) * Math.PI * 2;
      const xa = ps.length > 1 ? K * 0.5 : K * (1 + bam(n.id + "r"));
      n.x = cx * (ps.length > 1 ? 0.5 : 1.25) + Math.cos(goc) * xa;
      n.y = cy * (ps.length > 1 ? 0.5 : 1.25) + Math.sin(goc) * xa;
    });

    const giuYen = Object.keys(S.viTri).length >= N.length;
    const VONG = giuYen ? 0 : 260;
    let t = K * 1.2;
    for (let v = 0; v < VONG; v++) {
      N.forEach((n) => { n.dx = 0; n.dy = 0; });
      for (let i = 0; i < N.length; i++) {
        const a = N[i];
        for (let j = i + 1; j < N.length; j++) {
          const b = N[j];
          let dx = a.x - b.x, dy = a.y - b.y;
          let d2 = dx * dx + dy * dy;
          if (d2 < 0.01) { dx = 0.1 * (bam(a.id) - 0.5); dy = 0.1; d2 = 0.02; }
          // Đẩy nhau mạnh hơn khi cả hai là bài: thẻ bài to, chồng lên nhau là
          // mất nhãn — thứ duy nhất cho biết đó là bài nào.
          const d = Math.sqrt(d2);
          const f = (K * K * a.m * b.m) / d;
          a.dx += dx / d * f / a.m; a.dy += dy / d * f / a.m;
          b.dx -= dx / d * f / b.m; b.dy -= dy / d * f / b.m;
        }
      }
      S.links.forEach((l) => {
        const dx = l.a.x - l.b.x, dy = l.a.y - l.b.y;
        const d = Math.sqrt(dx * dx + dy * dy) || 0.1;
        const f = (d * d) / (K * (l.qh ? 1.4 : 1.9));
        l.a.dx -= dx / d * f / l.a.m; l.a.dy -= dy / d * f / l.a.m;
        l.b.dx += dx / d * f / l.b.m; l.b.dy += dy / d * f / l.b.m;
      });
      N.forEach((n) => {
        // Kéo nhẹ về tâm, để thành phần rời không trôi xa vô tận.
        n.dx -= n.x * 0.06; n.dy -= n.y * 0.06 / tl;
        const d = Math.sqrt(n.dx * n.dx + n.dy * n.dy) || 1;
        n.x += n.dx / d * Math.min(d, t);
        n.y += n.dy / d * Math.min(d, t);
      });
      t = Math.max(0.6, t * 0.985);
    }
    if (VONG) hoaQuanhBai(N);
    N.forEach((n) => { S.viTri[n.id] = { x: n.x, y: n.y }; });
    return VONG > 0;
  }

  /* Khái niệm "lá" — chỉ một bài nhắc tới, không quan hệ nào — xếp thành vòng
     quanh bài của nó. FR để chúng tự rơi thì chúng dồn vào hai phía của thẻ bài
     (thẻ rộng mà thấp), rồi phép tách chồng gạt chúng thành hai hàng thẳng trên
     dưới như một cái bảng. Giữ THỨ TỰ GÓC mà FR đã tìm ra, chỉ dàn đều lại. */
  function hoaQuanhBai(N) {
    const coQh = new Set();
    S.links.forEach((l) => { if (l.qh) { coQh.add(l.a.id); coQh.add(l.b.id); } });
    const la = {};
    N.forEach((n) => {
      if (!n.bai && n.ref.papers.length === 1 && !coQh.has(n.id)) {
        (la[n.ref.papers[0]] = la[n.ref.papers[0]] || []).push(n);
      }
    });
    Object.entries(la).forEach(([pid, ds]) => {
      const P = S.by["P" + pid];
      if (!P) return;
      const a0 = tenBaiNgan(P.ref).length * 3.9 + 16 + 30, b0 = 19 + 30;
      ds.sort((u, v) => Math.atan2((u.y - P.y) * a0 / b0, u.x - P.x)
        - Math.atan2((v.y - P.y) * a0 / b0, v.x - P.x));
      let i = 0, vong = 0;
      while (i < ds.length) {
        const a = a0 + vong * 34, b = b0 + vong * 34;
        const cap = Math.max(6, Math.floor((2 * Math.PI * Math.sqrt((a * a + b * b) / 2)) / 36));
        const nhom = ds.slice(i, i + cap);
        nhom.forEach((n, j) => {
          const t = -Math.PI + (2 * Math.PI * (j + 0.5)) / nhom.length + vong * 0.3;
          n.x = P.x + a * Math.cos(t); n.y = P.y + b * Math.sin(t);
        });
        i += cap; vong++;
      }
    });
  }

  /* FR coi mọi điểm là một chấm, nhưng thẻ bài rộng cả trăm px: điểm "trục" của
     bài (chính phương pháp của nó) nối chặt với bài nên bị kéo nằm ĐÈ lên tên
     bài. Vài lượt đẩy tách theo hình thật — thẻ là hộp chữ nhật, khái niệm là
     vòng tròn — tính theo cỡ ở mức phóng 1, vì cỡ điểm trên màn không đổi. */
  function tachChong(N, k) {
    const hop = (n) => (n.bai
      ? [tenBaiNgan(n.ref).length * 3.9 + 16, 19]
      : [n.r + 4, n.r + 4]).map((v) => v / k);
    for (let v = 0; v < 40; v++) {
      let dich = false;
      for (let i = 0; i < N.length; i++) {
        for (let j = i + 1; j < N.length; j++) {
          const a = N[i], b = N[j];
          if (!a.bai && !b.bai && v % 2) continue;
          const [aw, ah] = hop(a), [bw, bh] = hop(b);
          const dx = b.x - a.x, dy = b.y - a.y;
          const ox = aw + bw - Math.abs(dx), oy = ah + bh - Math.abs(dy);
          if (ox <= 0 || oy <= 0) continue;
          dich = true;
          // Đẩy dọc theo đường nối hai tâm, một đoạn bằng phần chồng ngắn hơn:
          // giữ được dáng toả tròn quanh bài. Đẩy theo trục thì khái niệm của
          // một bài xếp thành hai hàng thẳng trên dưới thẻ, trông như bảng.
          const ma = a.bai ? 0.15 : 0.5, mb = b.bai ? 0.15 : 0.5;
          const d = Math.hypot(dx, dy) || 1;
          const s = Math.min(ox, oy) + 1;
          a.x -= dx / d * s * ma; a.y -= dy / d * s * ma;
          b.x += dx / d * s * mb; b.y += dy / d * s * mb;
        }
      }
      if (!dich) break;
    }
  }

  /* ------------------------------------------------------------ vẽ */

  function tenBaiNgan(p) {
    let t = (p.title || p.title_vi || "(không tiêu đề)").split(/[:—]/)[0].trim();
    // Tên bóc từ PDF hay viết hoa toàn bộ ("LATENT ACTION PRETRAINING…") — trên
    // thẻ hẹp thì nó vừa dài vừa như đang hét. Từ ngắn toàn hoa (tên tắt) giữ nguyên.
    if (t === t.toUpperCase() && /[A-Z]{4}/.test(t)) {
      t = t.toLowerCase().replace(/(^|\s)(\S)/g, (m, a, b) => a + b.toUpperCase());
    }
    return t.length > 28 ? t.slice(0, 27).replace(/\s+\S*$/, "") + "…" : t;
  }

  function ve() {
    const svg = $b("#bdSvg");
    const L = S.links.map((l, i) =>
      `<line class="bd-l ${l.qh ? "qh" : "nhac"}" data-i="${i}"></line>`).join("");
    const nodes = S.nodes.map((n) => {
      if (n.bai) {
        const p = n.ref;
        return `<g class="bd-n bd-bai ${p.co_do_thi ? "" : "chua"}" data-id="${esc(n.id)}">
          <rect></rect><text>${esc(tenBaiNgan(p))}</text>
          <title>${esc(p.title || "")}${p.title_vi ? "\n" + esc(p.title_vi) : ""}</title></g>`;
      }
      const e = n.ref;
      const mau = (LOAI[e.kind] || LOAI.concept)[1];
      // Nhãn luôn hiện cho điểm chung và điểm "trục" (từ ba quan hệ trở lên —
      // thường là chính phương pháp của bài); điểm lẻ chỉ hiện khi phóng vào.
      const luon = e.papers.length > 1 || n.bac >= 3;
      return `<g class="bd-n bd-tt ${e.papers.length > 1 ? "chung" : ""} ${luon ? "luon" : ""} ${e.nguon === "phieu" ? "phieu" : ""}"
          data-id="${esc(n.id)}" data-mau="${mau}">
        <circle r="${n.r.toFixed(1)}"></circle>
        <text dy="${(n.r + 12).toFixed(1)}">${esc(e.name)}</text>
        <title>${esc(e.name)} · ${esc((LOAI[e.kind] || LOAI.concept)[0])} · ${e.papers.length} bài</title></g>`;
    }).join("");
    svg.innerHTML = `<g class="bd-vp"><g class="bd-ls">${L}</g><g class="bd-ns">${nodes}</g></g>`;
    // Thẻ bài phải vừa đúng chữ — đo sau khi chữ đã vào DOM.
    svg.querySelectorAll(".bd-bai").forEach((g) => {
      const tx = g.querySelector("text");
      const w = tx.getComputedTextLength() + 22;
      const r = g.querySelector("rect");
      r.setAttribute("x", -w / 2); r.setAttribute("y", -15);
      r.setAttribute("width", w); r.setAttribute("height", 30); r.setAttribute("rx", 8);
      tx.setAttribute("text-anchor", "middle"); tx.setAttribute("dy", "4.5");
      S.by[g.dataset.id].w = w;
    });
    datVp();
    $b("#bdMsg").textContent = "";
    $b("#bdRong").classList.toggle("hidden", S.nodes.length > 0);
    toSang();
  }

  /* Điểm và nhãn giữ NGUYÊN CỠ trên màn hình (`scale(1/k)`), chỉ khoảng cách
     giữa chúng co giãn theo mức phóng. Phóng cả điểm lẫn chữ thì xem toàn kho
     chữ còn 6px, phóng vào thì vòng tròn to bằng nắm tay — đằng nào cũng không
     đọc được. Phóng vào là để TÁCH các điểm ra, nên đúng ra chỉ khoảng cách đổi. */
  function dat() {
    const svg = $b("#bdSvg");
    const z = (1 / S.k).toFixed(4);
    svg.querySelectorAll(".bd-n").forEach((g) => {
      const n = S.by[g.dataset.id];
      g.setAttribute("transform", `translate(${n.x.toFixed(1)},${n.y.toFixed(1)}) scale(${z})`);
    });
    svg.querySelectorAll(".bd-l").forEach((el) => {
      const l = S.links[el.dataset.i];
      el.setAttribute("x1", l.a.x.toFixed(1)); el.setAttribute("y1", l.a.y.toFixed(1));
      el.setAttribute("x2", l.b.x.toFixed(1)); el.setAttribute("y2", l.b.y.toFixed(1));
    });
  }

  function datVp() {
    dat();
    const vp = $b("#bdSvg .bd-vp");
    if (vp) vp.setAttribute("transform", `translate(${S.x.toFixed(1)},${S.y.toFixed(1)}) scale(${S.k.toFixed(3)})`);
    // Nhãn của thực thể chỉ có một bài thì hiện khi đã phóng to: ở mức xem cả
    // bản đồ, mấy chục nhãn chồng lên nhau còn tệ hơn không có nhãn.
    $b("#bdSvg").classList.toggle("gan", S.k >= 1.25);
  }

  function vuaKhung() {
    const svg = $b("#bdSvg");
    const W = svg.clientWidth || 800, H = svg.clientHeight || 600;
    if (!S.nodes.length) { S.k = 1; S.x = W / 2; S.y = H / 2; return; }
    let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
    S.nodes.forEach((n) => {
      x0 = Math.min(x0, n.x); x1 = Math.max(x1, n.x);
      y0 = Math.min(y0, n.y); y1 = Math.max(y1, n.y);
    });
    // Cỡ điểm tính bằng px màn hình (xem `dat`), nên chừa lề bằng px: nửa thẻ
    // bài hai bên, một dòng nhãn trên dưới.
    const LX = 200, LY = 70;
    S.k = Math.min(2.2, Math.max(0.15, Math.min((W - LX) / Math.max(1, x1 - x0),
      (H - LY) / Math.max(1, y1 - y0))));
    S.x = W / 2 - ((x0 + x1) / 2) * S.k;
    S.y = H / 2 - ((y0 + y1) / 2) * S.k;
  }

  /* Tô sáng: điểm đang chọn + hàng xóm; hoặc các điểm khớp ô tìm. Phần còn lại
     mờ đi chứ không ẩn — ẩn đi thì mất cái nhìn "nó nằm ở đâu trong cả kho". */
  function toSang() {
    const svg = $b("#bdSvg");
    const q = khongDau(($b("#svSearch").value || "").trim().toLowerCase());
    let sang = null;
    if (S.chon && S.by[S.chon]) {
      sang = new Set([S.chon, ...(S.ke[S.chon] || [])]);
    } else if (q.length >= 2) {
      sang = new Set(S.nodes.filter((n) => khongDau((n.bai
        ? (n.ref.title || "") + " " + (n.ref.title_vi || "") : n.ref.name).toLowerCase()).includes(q))
        .map((n) => n.id));
      // Không điểm nào khớp tên thì tô các BÀI có đoạn khớp (sau khi Enter);
      // còn chưa tra thì để nguyên — mờ cả bản đồ vì một chữ đang gõ dở là
      // làm người dùng tưởng vừa bấm hỏng gì đó.
      if (!sang.size) sang = S.traBai && S.traBai.q === q ? new Set(S.traBai.ids) : null;
    }
    svg.classList.toggle("mo", !!sang);
    svg.querySelectorAll(".bd-n").forEach((g) => {
      g.classList.toggle("sang", !!sang && sang.has(g.dataset.id));
      g.classList.toggle("dang-chon", g.dataset.id === S.chon);
    });
    svg.querySelectorAll(".bd-l").forEach((el) => {
      const l = S.links[el.dataset.i];
      el.classList.toggle("sang", !!sang && (S.chon
        ? (l.a.id === S.chon || l.b.id === S.chon) : sang.has(l.a.id) && sang.has(l.b.id)));
    });
  }

  function veLoai() {
    const co = new Set(S.data.entities.map((e) => e.kind));
    $b("#bdLoai").innerHTML = Object.entries(LOAI).filter(([k]) => co.has(k)).map(([k, [nhan, mau]]) =>
      `<button class="bd-loai-nut ${S.an.has(k) ? "tat" : ""}" data-loai="${k}" data-mau="${mau}"
         title="Bấm để ${S.an.has(k) ? "hiện" : "ẩn"} loại này"><i></i>${nhan}</button>`).join("");
  }

  /* ------------------------------------------------------------ khung bên */

  function pan(html) {
    $b("#bdPan").innerHTML = html;
    $b("#bdPan").scrollTop = 0;
  }

  function nutTT(e) {
    const mau = (LOAI[e.kind] || LOAI.concept)[1];
    return `<button class="bd-tag" data-chon="${esc(e.id)}" data-mau="${mau}"><i></i>${esc(e.name)}${
      e.papers.length > 1 ? ` <b>${e.papers.length}</b>` : ""}</button>`;
  }

  function nutBai(pid) {
    const p = S.data.papers.find((x) => x.id === pid);
    return p ? `<button class="bd-baitag" data-chon="P${esc(pid)}">${esc(tenBaiNgan(p))}</button>` : "";
  }

  function tongQuan() {
    const d = S.data;
    const chung = d.entities.filter((e) => e.papers.length > 1);
    const chua = d.papers.filter((p) => !p.co_do_thi);
    const coNguon = d.edges.length;
    let html = `<h3 class="bd-h">Kho này trên bản đồ</h3>
      <p class="bd-so"><b>${d.papers.length}</b> bài · <b>${d.entities.length}</b> khái niệm ·
        <b>${coNguon}</b> quan hệ có nguồn</p>`;
    if (!d.papers.length) {
      html += `<p class="muted small">Kho chưa có bài nào. Thêm bài ở cột trái.</p>`;
      return pan(html);
    }
    if (chua.length) {
      html += `<div class="bd-goiy">
        <p><b>${chua.length}/${d.papers.length} bài chưa có đồ thị riêng.</b> Các điểm của
          chúng lấy từ phiếu tóm tắt, nên chỉ có tên mà không có đoạn nguồn, và hiếm khi
          trùng tên với bài khác. Dựng đồ thị thì model đọc toàn văn từng bài, bóc ra
          phương pháp, tập dữ liệu, độ đo cùng quan hệ giữa chúng.</p>
        <button class="btn btn-primary xs" data-bd="dung">${esc(S.gia
          ? `Dựng đồ thị · ~${money(S.gia.usd)}` : "Dựng đồ thị…")}</button></div>`;
    }
    if (chung.length) {
      html += `<h4 class="bd-h4">Cầu nối giữa các bài</h4>
        <p class="muted small">Khái niệm xuất hiện ở từ hai bài trở lên, nhiều bài nhất trước.</p>
        <ul class="bd-ds">${chung.slice(0, 30).map((e) => `<li>${nutTT(e)}<span class="muted small">${
          e.papers.map((p) => esc(tenBaiNgan(d.papers.find((x) => x.id === p) || {}))).join(" · ")}</span></li>`).join("")}</ul>`;
    } else if (d.papers.length > 1) {
      html += `<p class="muted small bd-goiy-nho">Chưa thấy khái niệm nào chung giữa các bài${
        chua.length ? " — dựng đồ thị để tìm" : ""}.</p>`;
    }
    html += `<h4 class="bd-h4">Các bài</h4><ul class="bd-ds">${d.papers.map((p) =>
      `<li>${nutBai(p.id)}<span class="muted small">${
        d.entities.filter((e) => e.papers.includes(p.id)).length} khái niệm${
        p.co_do_thi ? "" : " · từ phiếu"}</span></li>`).join("")}</ul>
      <p class="muted small bd-meo">Bấm một điểm để xem nó nối với gì. Cuộn để phóng,
        kéo nền để di, kéo một điểm để dời nó. Gõ vào ô tìm để tô sáng, Enter để tra
        đoạn văn.</p>`;
    pan(html);
  }

  function chiTietBai(n) {
    const p = n.ref;
    const card = (SV.papers.find((x) => x.id === p.id) || {}).card || {};
    const ents = S.data.entities.filter((e) => e.papers.includes(p.id));
    const chung = ents.filter((e) => e.papers.length > 1);
    const khac = {};
    chung.forEach((e) => e.papers.forEach((q) => {
      if (q !== p.id) (khac[q] = khac[q] || []).push(e);
    }));
    const dong = (nhan, v) => v ? `<p class="bd-dong"><b>${nhan}.</b> ${esc(catGon(v, 300))}</p>` : "";
    pan(`<button class="sv-link bd-lui" data-bd="tq">← Tổng quan</button>
      <h3 class="bd-h">${esc(p.title_vi || p.title)}</h3>
      ${p.title_vi ? `<p class="muted small">${esc(p.title)}</p>` : ""}
      ${p.year ? `<p class="muted small">${p.year}</p>` : ""}
      ${p.tldr ? `<p class="bd-tldr">${esc(p.tldr)}</p>` : ""}
      <div class="bd-nut">
        ${p.loupe_doc_id ? `<a class="btn xs" href="#doc=${esc(p.loupe_doc_id)}">Đọc bài</a>` : ""}
        <button class="btn xs" data-bd="giang" data-pid="${esc(p.id)}">Bài giảng</button>
        <button class="btn xs" data-bd="hoi" data-q="${esc(`Bài «${tenBaiNgan(p)}» giải quyết vấn đề gì, bằng cách nào, và khác các bài còn lại trong kho ở đâu?`)}">Hỏi về bài này…</button>
      </div>
      ${dong("Vấn đề", card.problem)}${dong("Ý tưởng", card.idea)}${dong("Cách làm", card.method)}
      ${Object.keys(khac).length ? `<h4 class="bd-h4">Điểm chung với bài khác</h4>
        <ul class="bd-ds">${Object.entries(khac).map(([q, es]) =>
          `<li>${nutBai(q)}<div class="bd-tags">${es.map(nutTT).join("")}</div></li>`).join("")}</ul>` : ""}
      <h4 class="bd-h4">Khái niệm của bài · ${ents.length}</h4>
      ${p.co_do_thi ? "" : `<p class="muted small">Lấy từ phiếu tóm tắt, chưa có đoạn nguồn.</p>`}
      <div class="bd-tags">${ents.map(nutTT).join("")}</div>`);
  }

  async function chiTietTT(n) {
    const e = n.ref;
    const [nhan] = LOAI[e.kind] || LOAI.concept;
    const dau = `<button class="sv-link bd-lui" data-bd="tq">← Tổng quan</button>
      <h3 class="bd-h">${esc(e.name)}</h3>
      <p class="muted small">${esc(nhan)} · có ở ${e.papers.length} bài${e.nguon === "phieu" ? " · lấy từ phiếu" : ""}</p>
      <div class="bd-tags">${e.papers.map(nutBai).join("")}</div>
      <div class="bd-nut"><button class="btn xs" data-bd="hoi" data-q="${esc(
        `«${e.name}» được các bài trong kho dùng thế nào, và khác nhau ở đâu?`)}">Hỏi về «${esc(catGon(e.name, 30))}»…</button></div>`;
    pan(dau + '<p class="muted small">đang tìm đoạn nhắc tới…</p>');
    let d;
    try {
      d = await svFetch(`/api/survey/${S.sid}/thuc-the?ten=${encodeURIComponent(e.name)}`);
    } catch (err) {
      if (S.chon === n.id) pan(dau + `<p class="muted small">Lỗi: ${esc(err.message)}</p>`);
      return;
    }
    if (S.chon !== n.id) return;          // người dùng đã bấm sang điểm khác
    const qh = (d.edges || []).map((g) => `<li class="bd-qh">
        <span>${esc(g.src_name)}</span> <b>${esc(g.rel)}</b> <span>${esc(g.dst_name)}</span>
        ${g.chunk_id ? `<a class="sv-cite" data-cite="${esc(g.chunk_id)}">[nguồn]</a>` : ""}
        ${g.note ? `<div class="muted small">${esc(g.note)} · theo ${esc(catGon(g.paper_title || "", 40))}</div>` : ""}</li>`).join("");
    const doan = (d.chunks || []).map((c) => `<li class="bd-doan" data-cite="${esc(c.id)}">
        <div class="muted small">${esc(catGon(c.title, 50))}${c.section ? " · " + esc(catGon(c.section, 40)) : ""}</div>
        <div>${sci(catGon(c.vi || c.text, 280))}</div></li>`).join("");
    pan(dau
      + (qh ? `<h4 class="bd-h4">Quan hệ các bài phát biểu</h4><ul class="bd-ds">${qh}</ul>` : "")
      + `<h4 class="bd-h4">Đoạn nhắc tới · ${(d.chunks || []).length}</h4>`
      + (doan ? `<ul class="bd-ds">${doan}</ul>` : '<p class="muted small">Không tìm thấy đoạn nào chứa nguyên cụm tên này.</p>'));
  }

  function hien(id) {
    const n = S.by[id];
    if (!n) return tongQuan();
    n.bai ? chiTietBai(n) : chiTietTT(n);
  }

  function chon(id) {
    S.chon = id || "";
    // Gọi trước khi bản đồ nạp xong (bấm tên bài ở cột trái lúc đang ở tab
    // khác) thì giữ lại, `mo()` sẽ mở đúng điểm này khi vẽ xong.
    if (!S.data) return;
    if (!S.by[S.chon]) S.chon = "";
    toSang();
    hien(S.chon);
  }

  /* Chọn theo tên — để chỗ khác trong màn (chip thực thể ở câu trả lời…) nhảy
     được về bản đồ. */
  function chonTen(ten) {
    const k = khongDau(ten.toLowerCase());
    const n = S.nodes.find((x) => !x.bai && khongDau(x.ref.name.toLowerCase()) === k);
    if (n) chon(n.id);
  }

  /* ------------------------------------------------------------ dựng đồ thị */

  async function giaDung() {
    const sid = S.sid;
    const g = await svFetch(`/api/survey/${sid}/do-thi/gia`);
    if (sid !== S.sid) return;
    S.gia = g.bai ? g : null;
    const b = $b("#bdDung");
    b.classList.toggle("hidden", !g.bai);
    b.textContent = `Dựng đồ thị · ~${money(g.usd)}`;
    b.title = `${g.bai} bài chưa có đồ thị riêng · chạy bằng ${tenModel(g.model)}`;
    if (!S.chon) tongQuan();
  }

  async function dungDoThi() {
    if (S.dang || !S.gia) return;
    const g = S.gia;
    const ok = await xacNhan(`Dựng đồ thị cho ${g.bai} bài?`,
      `Model ${tenModel(g.model)} đọc toàn văn từng bài và bóc ra phương pháp, tập dữ liệu, `
      + `độ đo cùng quan hệ giữa chúng. Mỗi quan hệ kèm đoạn nguồn để bấm kiểm lại.\n\n`
      + `Ước tính ~${money(g.usd)} cho cả ${g.bai} bài.\n\n`
      + g.ten.map((t) => "· " + t).join("\n"),
      { ok: `Dựng · ~${money(g.usd)}` });
    if (!ok) return;
    S.dang = true;
    const b = $b("#bdDung");
    b.disabled = true;
    const sid = S.sid;
    const es = new EventSource(`/api/survey/${sid}/do-thi/dung`);
    const msg = (t) => { $b("#bdMsg").textContent = t; };
    let tong = 0;
    msg(`đang dựng 0/${g.bai} bài…`);
    const xong = () => { es.close(); S.dang = false; b.disabled = false; };
    es.addEventListener("bai", (ev) => {
      const d = JSON.parse(ev.data);
      tong += d.cost || 0;
      msg(`${d.xong}/${d.bai} bài · ${catGon(d.title, 40)}: ${d.entities} khái niệm, ${d.edges} quan hệ · ${money(tong)}`);
    });
    es.addEventListener("hong", (ev) => {
      const d = JSON.parse(ev.data);
      msg(`${d.xong}/${d.bai} · hỏng ở ${catGon(d.title, 40)}: ${d.msg}`);
    });
    es.addEventListener("done", async (ev) => {
      const d = JSON.parse(ev.data);
      xong();
      if (sid !== S.sid) return;
      S.viTri = {};                       // bố cục cũ không còn đúng với cạnh mới
      await mo(sid);
      msg(`Xong · đã tốn ${money(d.cost)}`);
    });
    es.onerror = () => { if (S.dang) { xong(); msg("Mất kết nối giữa chừng. Bài nào đã dựng xong vẫn được giữ."); } };
  }

  /* ------------------------------------------------------------ chuột */

  function noi() {
    const svg = $b("#bdSvg");
    let keo = null;      // {kieu: "nen"|"diem", id, x0, y0, sx, sy, di}

    const toaDo = (e) => {
      const r = svg.getBoundingClientRect();
      return [e.clientX - r.left, e.clientY - r.top];
    };

    svg.addEventListener("wheel", (e) => {
      e.preventDefault();
      const [px, py] = toaDo(e);
      const k2 = Math.min(4, Math.max(0.15, S.k * Math.exp(-e.deltaY * 0.0015)));
      // Phóng lấy CON TRỎ làm tâm, cùng công thức với ô xem trước hình.
      S.x = px - (px - S.x) * k2 / S.k;
      S.y = py - (py - S.y) * k2 / S.k;
      S.k = k2;
      datVp();
    }, { passive: false });

    svg.addEventListener("pointerdown", (e) => {
      const g = e.target.closest(".bd-n");
      const [px, py] = toaDo(e);
      keo = g
        ? { kieu: "diem", id: g.dataset.id, x0: px, y0: py, di: 0 }
        : { kieu: "nen", x0: px, y0: py, sx: S.x, sy: S.y, di: 0 };
      svg.setPointerCapture(e.pointerId);
      svg.classList.add("dang-keo");
    });
    svg.addEventListener("pointermove", (e) => {
      if (!keo) return;
      const [px, py] = toaDo(e);
      const dx = px - keo.x0, dy = py - keo.y0;
      keo.di = Math.max(keo.di, Math.abs(dx) + Math.abs(dy));
      if (keo.kieu === "nen") {
        S.x = keo.sx + dx; S.y = keo.sy + dy;
        datVp();
      } else if (keo.di > 3) {
        const n = S.by[keo.id];
        n.x = (px - S.x) / S.k; n.y = (py - S.y) / S.k;
        S.viTri[n.id] = { x: n.x, y: n.y };
        dat();
      }
    });
    const tha = () => {
      if (!keo) return;
      // Di chưa tới 4px thì là cú bấm, không phải cú kéo.
      if (keo.di <= 3) chon(keo.kieu === "diem" ? (keo.id === S.chon ? "" : keo.id) : "");
      keo = null;
      svg.classList.remove("dang-keo");
    };
    svg.addEventListener("pointerup", tha);
    svg.addEventListener("pointercancel", tha);

    $b("#bdVua").onclick = () => { vuaKhung(); datVp(); };
    $b("#bdGan").onclick = () => phong(1.3);
    $b("#bdXa").onclick = () => phong(1 / 1.3);
    $b("#bdXep").onclick = () => { S.viTri = {}; dung(); vuaKhung(); ve(); };
    $b("#bdChung").onchange = (e) => { S.chiChung = e.target.checked; dung(); vuaKhung(); ve(); };
    $b("#bdLoai").onclick = (e) => {
      const b = e.target.closest("[data-loai]");
      if (!b) return;
      const k = b.dataset.loai;
      S.an.has(k) ? S.an.delete(k) : S.an.add(k);
      veLoai(); dung(); ve();
    };
    $b("#bdDung").onclick = dungDoThi;

    // Ô tìm: gõ thì tô sáng ngay, Enter thì tra đoạn văn (BM25 + vector, miễn phí).
    const o = $b("#svSearch");
    o.addEventListener("input", () => {
      if (S.chon) { S.chon = ""; tongQuan(); }
      toSang();
    });
    o.addEventListener("keydown", (e) => {
      if (e.key === "Enter") { e.preventDefault(); tra(); }
      if (e.key === "Escape") { o.value = ""; toSang(); }
    });

    $b("#bdPan").addEventListener("click", (e) => {
      const c = e.target.closest("[data-chon]");
      if (c) { chon(c.dataset.chon); return; }
      const a = e.target.closest("[data-bd]");
      if (!a) return;
      if (a.dataset.bd === "tq") chon("");
      if (a.dataset.bd === "dung") dungDoThi();
      if (a.dataset.bd === "hoi") {
        svTab("svTabAsk");
        $b("#svQ").value = a.dataset.q;
        $b("#svQ").focus();
      }
      if (a.dataset.bd === "giang") { SV.lecPid = a.dataset.pid; svTab("svTabLec"); }
    });

    new ResizeObserver(() => { if (S.data && !S.dang) datVp(); }).observe(svg);
  }

  function phong(h) {
    const svg = $b("#bdSvg");
    const px = svg.clientWidth / 2, py = svg.clientHeight / 2;
    const k2 = Math.min(4, Math.max(0.15, S.k * h));
    S.x = px - (px - S.x) * k2 / S.k;
    S.y = py - (py - S.y) * k2 / S.k;
    S.k = k2;
    datVp();
  }

  async function tra() {
    const q = $b("#svSearch").value.trim();
    if (!q) return;
    S.chon = "";
    toSang();
    pan(`<button class="sv-link bd-lui" data-bd="tq">← Tổng quan</button>
      <h3 class="bd-h">Đoạn khớp «${esc(catGon(q, 40))}»</h3>
      <div id="svHits" class="sv-hits"></div>
      <button id="svMoreHits" class="sv-link hidden">Xem thêm…</button>`);
    try {
      await svSearch();
      S.traBai = { q: khongDau(q.toLowerCase()), ids: SV.hits.map((h) => "P" + h.paper_id) };
      toSang();
    } catch (e) {
      const el = $b("#svHits");
      if (el) el.innerHTML = `<p class="muted small">Lỗi: ${esc(e.message)}</p>`;
    }
  }

  return { mo, noi, chon, chonTen };
})();
