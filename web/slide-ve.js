/* Bộ vẽ slide — MỘT hàm duy nhất cho mọi nơi.

   Bản cũ vẽ slide ở ba chỗ (xem trước trong app, file HTML xuất ra, PowerPoint),
   và mỗi lần sửa phải sửa cả ba — lệch một chỗ là file tải về khác bản trên màn
   hình. File này TỰ CHỨA: không dùng hàm nào của app.js, nên server nhúng
   nguyên văn nó vào file tải về (`main._xuat_slide`). Xem trước, trình chiếu và
   file tải về vì thế là cùng một đoạn code.

   Bố cục suy ra từ VAI của slide trong lập luận (`server/slide.py`): mỗi vai một
   khuôn, nên bộ slide đổi dáng theo mạch bài thay vì lặp một khuôn thẻ. Chất liệu
   là "Sổ tay khoa học" của trang web: giấy ngà kẻ ô, nét mực, bút dạ quang, giấy
   nhớ dán băng keo — xem `web/slide.css`.

   `ve(s, ctx)` trả về HTML của MỘT slide. `ctx`:
     bo       — cả bộ (để tính lộ trình và dấu "2/4 · Cách làm")
     anh(s)   — URL ảnh của slide (app: /api/…; file tải về: data URI)
     sua      — true thì chữ sửa được tại chỗ (contenteditable + data-p)
     ten_bai  — tên bài cho chân slide */

(function (goc) {
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

  /** `x^{2}`, `d_{i}` → chỉ số trên/dưới. Escape TRƯỚC rồi mới chèn thẻ. */
  function chu(s) {
    let t = esc(s);
    for (let i = 0; i < 4; i++) {
      const t2 = t.replace(/\^\{([^{}]*)\}/g, "<sup>$1</sup>").replace(/_\{([^{}]*)\}/g, "<sub>$1</sub>");
      if (t2 === t) break;
      t = t2;
    }
    // Chỉ số KHÔNG ngoặc (`z_t`, `W_c`, `h_{t+1}` đã xử lý ở trên, `R^32`): model vẫn
    // viết kiểu này dù prompt đòi `z_{t}`. Cùng luật hẹp với `_SUBSCRIPTISH` bên
    // app.js — gốc là MỘT chữ cái, chỉ số 1–2 ký tự — nên `paper_id` không bị chạm.
    return t
      .replace(/(?<![\w`>])([A-Za-z])_([A-Za-z0-9](?:\+[A-Za-z0-9]{1,2})?)(?![\w{])/g, "$1<sub>$2</sub>")
      .replace(/(?<![\w>])([A-Za-z])\^([A-Za-z0-9]{1,3})(?![\w{])/g, "$1<sup>$2</sup>");
  }

  const CHANG = [
    ["Bài toán", ["van_de", "khoang_trong", "yeu_cau"]],
    ["Cách làm", ["y_tuong", "co_che", "cong_thuc", "vi_du", "so_sanh"]],
    ["Bằng chứng", ["thiet_lap", "bang_chung", "so_lieu"]],
    ["Giới hạn & đúc kết", ["gioi_han", "dong_lai"]],
  ];
  const NHAN_VAI = {
    van_de: "Vấn đề", khoang_trong: "Khoảng trống", yeu_cau: "Thuộc tính cần có",
    y_tuong: "Ý tưởng cốt lõi", co_che: "Cơ chế", cong_thuc: "Công thức", vi_du: "Ví dụ chạy tay",
    so_sanh: "Khác gì cách cũ", thiet_lap: "Thiết lập thí nghiệm", doan_dich: "Trích đoạn",
    bang_chung: "Bằng chứng", so_lieu: "Con số chính", gioi_han: "Giới hạn", dong_lai: "Đúc kết",
  };

  /** Số đầu tiên trong chuỗi ("906 ± 21" → 906, "61,4" → 61.4) — để vẽ cột. */
  function soDau(t) {
    const m = String(t || "").replace(/(\d)[.,](\d{3})(?!\d)/g, "$1$2").match(/-?\d+(?:[.,]\d+)?/);
    return m ? parseFloat(m[0].replace(",", ".")) : NaN;
  }
  /** Ô bảng đối chiếu: "có"/"không" thành dấu, chữ khác giữ nguyên. */
  function oDoiChieu(v) {
    const t = String(v || "").trim().toLowerCase();
    if (/^(có|✓|yes|đạt)$/.test(t)) return ["co", "✓"];
    if (/^(không|✗|no|x|chưa)$/.test(t)) return ["khong", "✗"];
    if (/^(một phần|phần nào|hạn chế|partial)$/.test(t)) return ["mot-phan", "◐"];
    return ["", ""];
  }

  /** Các chặng có mặt trong bộ — chặng không có slide nào thì không đánh số. */
  function changCo(bo) {
    return CHANG.filter(([, vai]) => bo.some((s) => vai.includes(s.vai)));
  }

  function ve(s, ctx = {}) {
    const bo = ctx.bo || [s];
    const sua = !!ctx.sua;
    // Ô chữ: sửa được tại chỗ ở màn sửa, chữ thường ở mọi nơi khác.
    const o = (path, val, tag = "span", cls = "", ph = "") => {
      const v = val ?? "";
      if (!sua) return v ? `<${tag} class="${cls}">${chu(v)}</${tag}>` : "";
      return `<${tag} class="${cls}" contenteditable="plaintext-only" spellcheck="false"`
        + ` data-p="${path}" data-ph="${esc(ph)}">${chu(v)}</${tag}>`;
    };
    // `bat_buoc`: slide không có hình thì vô nghĩa (bằng chứng) → ô chờ to ở màn
    // sửa. Còn lại hình là tuỳ chọn → chỉ một nút nhỏ ở góc, để slide chưa có hình
    // vẫn dàn chữ đủ bề ngang chứ không chừa nửa slide cho một ô trống.
    const hinh = (lop = "", bat_buoc = false) => {
      const url = s.hinh && ctx.anh ? ctx.anh(s) : "";
      if (url) {
        return `<figure class="sld-hinh ${lop}"${sua ? ' data-chon-hinh="1" title="Bấm để đổi hình"' : ""}>`
          + `<span class="bang-keo"></span><img src="${esc(url)}" alt="">`
          + (s.nhan_hinh ? `<figcaption>${esc(s.nhan_hinh)}</figcaption>` : "") + `</figure>`;
      }
      // Ô chờ hình CHỈ hiện ở màn sửa — trình chiếu/file tải về không bao giờ
      // thấy lời nhắn cho người soạn (S2 của báo cáo test).
      if (!sua) return "";
      return bat_buoc ? `<figure class="sld-hinh trong ${lop}" data-chon-hinh="1"><span>＋ Bấm để chọn hình trong bài</span></figure>`
        : `<button type="button" class="sld-them-hinh" data-chon-hinh="1">＋ hình</button>`;
    };
    const coHinh = !!(s.hinh && ctx.anh && ctx.anh(s));
    // Lần thứ mấy vai này xuất hiện trong bộ — vai lặp lại thì đổi dáng (yêu cầu
    // "tránh dùng đi dùng lại"): cùng vai mà cùng khuôn thì mắt thấy lặp ngay.
    const lanThu = bo.slice(0, Math.max(0, bo.indexOf(s))).filter((x) => x.vai === s.vai).length;
    // Không ghi "Ví dụ trong bài": nguồn của ví dụ do model tự khai, không kiểm
    // được bằng máy (đã gặp ví dụ bịa khai là lấy từ 2WikiMQA). Chỉ nói chắc chiều
    // ngược lại — khi model tự nhận là minh hoạ.
    const nhanViDu = s.minh_hoa ? "Ví dụ minh hoạ, không lấy từ bài" : "Ví dụ";
    const cs = changCo(bo);
    // Trích đoạn không thuộc chặng nào: lấy chặng của slide gần nhất phía trước.
    let iChang = cs.findIndex(([, vai]) => vai.includes(s.vai));
    for (let k = bo.indexOf(s) - 1; iChang < 0 && k >= 0; k--) {
      iChang = cs.findIndex(([, vai]) => vai.includes(bo[k].vai));
    }
    const tiep = s.tiep && s.tiep[1] > 1 ? ` · phần ${s.tiep[0]}/${s.tiep[1]}` : "";
    const dau = iChang >= 0 ? `<div class="sld-dau">${iChang + 1}/${cs.length} · ${esc(cs[iChang][0])}`
      + `<span>${esc(NHAN_VAI[s.vai] || "")}${tiep}</span></div>` : "";
    const tieuDe = o("tieu_de", s.tieu_de, "h2", "sld-td", "Tiêu đề — một câu khẳng định");
    const so = bo.indexOf(s) + 1;
    const chan = s.vai === "mo_dau" ? ""
      : `<div class="sld-chan"><span>${esc(ctx.ten_bai || "")}</span><b>${so || ""}</b></div>`;

    let than = "";
    const ds = (key, f) => (s[key] || []).map(f).join("");

    switch (s.vai) {
      case "mo_dau": {
        const anh = hinh("nghieng");
        than = `<div class="sld-mo ${coHinh ? "co-hinh" : ""}"><div class="sld-mo-chu">
          <p class="sld-tay">Báo cáo seminar</p>
          ${o("tieu_de", s.tieu_de, "h1", "sld-ten", "Tên bài")}
          ${o("ten_goc", s.ten_goc, "p", "sld-goc", "Tên gốc")}
          ${o("tac_gia", s.tac_gia, "p", "sld-tg", "Tác giả")}
          ${o("noi_dang", s.noi_dang, "p", "sld-nd", "Nơi đăng · năm")}
          <p class="sld-nguoi">${sua || s.nguoi_noi ? "Trình bày " : ""}${o("nguoi_noi", s.nguoi_noi, "span", "", "tên người trình bày")}</p>
          ${s.nguon_bai ? `<p class="sld-nguon">${esc(s.nguon_bai)}</p>` : ""}
        </div>${anh}</div>`;
        break;
      }
      case "lo_trinh": {
        const muc = cs.map(([ten, vai], i) => {
          const tds = bo.filter((x) => vai.includes(x.vai)).slice(0, 2).map((x) => `<li>${chu(x.tieu_de)}</li>`).join("");
          return `<li class="sld-tram"><span class="sld-tram-so">${i + 1}</span><b>${esc(ten)}</b><ul>${tds}</ul></li>`;
        }).join("");
        than = `${tieuDe}<ol class="sld-lo">${muc}</ol>`;
        break;
      }
      case "van_de": {
        const anh = hinh();
        const note = `<div class="sld-note"><span class="bang-keo"></span><b class="sld-tay">${nhanViDu}</b>
          ${o("vi_du", s.vi_du, "p", "", "Một ví dụ cụ thể có thật trong bài")}</div>`;
        // Có hình (thường là Figure 1): chữ + ví dụ xếp dọc bên trái, hình bên phải.
        than = coHinh
          ? `${dau}${tieuDe}<div class="sld-vd co-hinh"><div class="sld-vd-cot">
              ${o("cau", s.cau, "p", "sld-lon", "Vấn đề và vì sao nó quan trọng")}${note}</div>${anh}</div>`
          : `${dau}${tieuDe}<div class="sld-vd">
              ${o("cau", s.cau, "p", "sld-lon", "Vấn đề và vì sao nó quan trọng")}${note}${anh}</div>`;
        break;
      }
      case "khoang_trong":
        than = `${dau}${tieuDe}<div class="sld-kt">
          <div class="sld-kt-cu"><b class="sld-tay">Cách đang làm</b>${o("cach_cu", s.cach_cu, "p", "", "Cách đang làm")}</div>
          <div class="sld-mui">→</div>
          <div class="sld-kt-hong"><b class="sld-tay">Hỏng ở đâu</b>${o("hong", s.hong, "p", "", "Hỏng ở đâu, trong kịch bản nào")}</div></div>
          ${s.he_qua || sua ? `<p class="sld-hq"><b class="sld-tay">Hệ quả</b> ${o("he_qua", s.he_qua, "span", "", "Hệ quả đo được")}</p>` : ""}`;
        break;
      case "yeu_cau":
        than = `${dau}${tieuDe}<ul class="sld-tc">${ds("tieu_chi", (t, i) => `<li><span class="sld-o"></span><div>
          ${o(`tieu_chi.${i}.ten`, t.ten, "b", "", "Tiêu chí")}${o(`tieu_chi.${i}.vi_sao`, t.vi_sao, "p", "", "Vì sao cần")}</div></li>`)}</ul>`;
        break;
      case "y_tuong": {
        const anh = hinh();
        const chu = `<p class="sld-tay">Ý tưởng cốt lõi</p>
          <p class="sld-yt-p">${o("cau", s.cau, "span", "sld-yt-cau", "Trực giác cốt lõi trong một câu")}</p>
          ${o("vi_sao", s.vi_sao, "p", "sld-yt-vs", "Vì sao nó đáp ứng các tiêu chí")}`;
        than = coHinh ? `${dau}${tieuDe}<div class="sld-yt co-hinh"><div class="sld-yt-chu">${chu}</div>${anh}</div>`
          : `${dau}${tieuDe}<div class="sld-yt">${chu}${anh}</div>`;
        break;
      }
      case "co_che": {
        const anh = hinh();
        const buoc = ds("buoc", (b, i) => `<li style="--i:${i}"><span class="sld-buoc-so">${i + 1}</span><div>
          ${o(`buoc.${i}.ten`, b.ten, "b", "", "Bước")}${o(`buoc.${i}.mo_ta`, b.mo_ta, "p", "", "Làm gì và vì sao cần")}</div></li>`);
        // Có hình: bước dọc bên trái + hình. Không hình: lần đầu là dòng chảy
        // ngang, lần sau là bậc thang — hai slide cơ chế không bao giờ cùng dáng.
        const dang = coHinh ? "co-hinh" : (lanThu % 2 ? "bac" : "ngang");
        const dan = s.dan || sua ? `<p class="sld-dan">${o("dan", s.dan, "span", "", "Thành phần này nhận gì, trả ra gì")}</p>` : "";
        than = `${dau}${tieuDe}${dan}<div class="sld-cc ${dang}"><ol class="sld-buoc">${buoc}</ol>${anh}</div>`;
        break;
      }
      case "cong_thuc": {
        // Trực giác TRƯỚC công thức, vai trò từng ký hiệu SAU nó — đúng trật tự
        // của skill viết tài liệu kỹ thuật: người nghe hiểu nó tính gì rồi mới nhìn.
        const url = s.hinh && ctx.anh ? ctx.anh(s) : "";
        const bt = url
          ? `<figure class="sld-ct-anh"${sua ? ' data-chon-hinh="1" title="Bấm để đổi công thức"' : ""}><img src="${esc(url)}" alt=""></figure>`
          : `<div class="sld-ct-chu">${o("bieu_thuc", s.bieu_thuc, "span", "", "Biểu thức, ví dụ y = f(x_{t})")}`
            + `${sua ? '<button type="button" class="sld-them-hinh tai-cho" data-chon-hinh="1">＋ ảnh công thức</button>' : ""}</div>`;
        const tp = ds("thanh_phan", (t, i) => `<li>${o(`thanh_phan.${i}.ky_hieu`, t.ky_hieu, "b", "sld-kh", "ký hiệu")}`
          + `${o(`thanh_phan.${i}.y_nghia`, t.y_nghia, "span", "", "vai trò")}</li>`);
        than = `${dau}${tieuDe}<div class="sld-ct">
          ${o("truc_giac", s.truc_giac, "p", "sld-ct-tg", "Công thức này tính gì, vì sao cần")}
          ${bt}<ul class="sld-ct-tp">${tp}</ul>
          ${s.danh_doi || sua ? `<p class="sld-ct-dd"><b class="sld-tay">Đánh đổi</b> ${o("danh_doi", s.danh_doi, "span", "", "Tăng/giảm thì được gì, mất gì")}</p>` : ""}</div>`;
        break;
      }
      case "so_sanh": {
        const cot = s.cot || [];
        const dau_bang = `<tr><th></th>${cot.map((c, j) => `<th class="${j === cot.length - 1 ? "cua-bai" : ""}">`
          + `${o(`cot.${j}`, c, "span", "", "cách làm")}</th>`).join("")}</tr>`;
        const hang = ds("hang", (h, i) => `<tr><th>${o(`hang.${i}.tieu_chi`, h.tieu_chi, "span", "", "tiêu chí")}</th>`
          + (h.o || []).map((v, j) => {
            const [lop, dauHieu] = oDoiChieu(v);
            const noi = sua ? o(`hang.${i}.o.${j}`, v, "span", "", "—")
              : (dauHieu ? `<i>${dauHieu}</i>` : chu(v));
            return `<td class="${lop} ${j === cot.length - 1 ? "cua-bai" : ""}">${noi}</td>`;
          }).join("") + "</tr>");
        than = `${dau}${tieuDe}<div class="sld-ss"><table>${dau_bang}${hang}</table>
          ${o("ket_luan", s.ket_luan, "p", "sld-ss-kl", "Điều bảng cho thấy")}</div>`;
        break;
      }
      case "thiet_lap": {
        const dl = ds("du_lieu", (d, i) => `<li>${o(`du_lieu.${i}.ten`, d.ten, "b", "", "tập dữ liệu")}`
          + `${o(`du_lieu.${i}.mo_ta`, d.mo_ta, "span", "", "loại câu hỏi, quy mô")}</li>`);
        const dc = (s.doi_chung || []).map((x, i) => `<li>${o(`doi_chung.${i}`, x, "span", "", "baseline")}</li>`).join("");
        const dd = ds("do_do", (d, i) => `<li>${o(`do_do.${i}.ten`, d.ten, "b", "", "thước đo")}`
          + `${o(`do_do.${i}.y_nghia`, d.y_nghia, "span", "", "đo cái gì")}</li>`);
        than = `${dau}${tieuDe}<div class="sld-tl3">
          <section><b class="sld-tay">Dữ liệu</b><ul class="sld-tl3-ds">${dl}</ul></section>
          <section><b class="sld-tay">So với</b><ul class="sld-tl3-chip">${dc}</ul></section>
          <section><b class="sld-tay">Đo bằng</b><ul class="sld-tl3-ds">${dd}</ul></section></div>
          ${s.mo_hinh_nen || sua ? `<p class="sld-tl3-nen"><b class="sld-tay">Mô hình nền</b> ${o("mo_hinh_nen", s.mo_hinh_nen, "span", "", "mô hình nền")}</p>` : ""}`;
        break;
      }
      case "doan_dich": {
        // Chữ là NGUYÊN VĂN bản dịch (server chép từ bài) — không có tầng tóm tắt
        // nào làm rơi ý. Cụm tô sáng do model chọn, server đã soát là chuỗi con thật.
        const toSang = (html) => (s.nhan_manh || []).reduce((h, p) => {
          const q = chu(p);
          const i = h.indexOf(q);
          return i < 0 ? h : h.slice(0, i) + `<mark>${q}</mark>` + h.slice(i + q.length);
        }, html);
        // Ở màn sửa vẫn hiện vệt tô sáng: lưu đọc `innerText` nên thẻ <mark> không lọt vào dữ liệu.
        const doan = (s.trich || []).map((t, i) => sua
          ? `<p contenteditable="plaintext-only" spellcheck="false" data-p="trich.${i}.chu">${toSang(chu(t.chu))}</p>`
          : `<p>${toSang(chu(t.chu))}</p>`).join("");
        const anh = hinh();
        const yc = s.y_chinh || sua ? `<p class="sld-dd-yc">${o("y_chinh", s.y_chinh, "span", "", "Điều cần nhớ từ đoạn này")}</p>` : "";
        than = `${dau}${tieuDe}${yc}<div class="sld-dd ${coHinh ? "co-hinh" : ""}">
          <div class="sld-dd-chu">${doan}${s.muc ? `<p class="sld-dd-muc">Trích bản dịch · ${chu(s.muc)}</p>` : ""}</div>${anh}</div>`;
        break;
      }
      case "vi_du": {
        // Ví dụ của bài hay nằm trong một bảng "Case Study" (ảnh) — có hình thì
        // hình chiếm bên phải, đầu vào/bước/đầu ra xếp dọc bên trái.
        const anh = hinh();
        const vao = `<div class="sld-note nho"><span class="bang-keo"></span><b class="sld-tay">Đầu vào${s.minh_hoa ? " · minh hoạ, không lấy từ bài" : ""}</b>${o("dau_vao", s.dau_vao, "p", "", "Một đầu vào có thật trong bài")}</div>`;
        const tl = `<ol class="sld-tl">${ds("buoc", (b, i) => `<li>${o(`buoc.${i}.ten`, b.ten, "b", "", "Bước")}${o(`buoc.${i}.mo_ta`, b.mo_ta, "p", "", "Đầu vào biến đổi ra sao")}</li>`)}</ol>`;
        const ra = `<div class="sld-ra"><b class="sld-tay">Đầu ra</b>${o("dau_ra", s.dau_ra, "p", "", "Kết quả")}</div>`;
        than = coHinh ? `${dau}${tieuDe}<div class="sld-vdu co-hinh"><div class="sld-vdu-cot">${vao}${tl}${ra}</div>${anh}</div>`
          : `${dau}${tieuDe}<div class="sld-vdu">${vao}${tl}${ra}${anh}</div>`;
        break;
      }
      case "bang_chung": {
        const anh = hinh("", true);
        // Hai slide bằng chứng liền nhau thì lật bên hình để mắt không thấy lặp.
        let lat = false;
        for (let k = bo.indexOf(s) - 1; k >= 0 && bo[k].vai === "bang_chung"; k--) lat = !lat;
        const so = ds("so", (x, i) => `<div class="sld-so">${o(`so.${i}.gia_tri`, x.gia_tri, "b", "", "số")}
          ${o(`so.${i}.nhan`, x.nhan, "span", "", "đo cái gì")}${o(`so.${i}.moc`, x.moc, "em", "", "so với gì")}</div>`);
        than = `${dau}${tieuDe}<div class="sld-bc ${anh ? "co-hinh" : "khong-hinh"} ${lat ? "lat" : ""}">${anh}
          <div class="sld-bc-chu">
            <b class="sld-tay">Cách đọc</b>${o("doc_hinh", s.doc_hinh, "p", "sld-doc", "Trục/cột là gì, nhìn vào đâu")}
            <p class="sld-kl-p">${o("ket_luan", s.ket_luan, "span", "sld-kl", "Điều hình cho thấy")}</p>
            ${so ? `<div class="sld-cac-so">${so}</div>` : ""}</div></div>`;
        break;
      }
      case "so_lieu":
        // Lần thứ hai trong bộ: số bên trái, lời bên phải — hai slide con số hay
        // đứng liền nhau (cùng chặng Bằng chứng), cùng dáng là thấy lặp ngay.
        {
          // Biểu đồ cột vẽ từ chính các số trong slide (đã soát với chữ của bài):
          // một con số đứng một mình không cho thấy nó lớn hay nhỏ.
          const ss = (s.so_sanh || []).map((x) => ({ ...x, v: soDau(x.gia_tri) })).filter((x) => isFinite(x.v));
          const max = Math.max(...ss.map((x) => Math.abs(x.v)), 0);
          const bd = ss.length >= 2 && max > 0 ? `<ul class="sld-cot">${ss.map((x) =>
            `<li class="${x.cua_bai ? "cua-bai" : ""}"><span class="sld-cot-nhan">${chu(x.nhan)}</span>`
            + `<span class="sld-cot-thanh"><i style="width:${(Math.abs(x.v) / max * 100).toFixed(1)}%"></i></span>`
            + `<b>${chu(x.gia_tri)}</b></li>`).join("")}</ul>` : "";
          const chuSo = `${o("gia_tri", s.gia_tri, "b", "sld-sl-so", "Con số")}
            ${o("nhan", s.nhan, "p", "sld-sl-nhan", "Đo cái gì")}
            ${o("moc", s.moc, "p", "sld-sl-moc", "So với gì")}
            ${o("y_nghia", s.y_nghia, "p", "sld-sl-yn", "Mức chênh ấy nói lên điều gì")}`;
          than = bd ? `${dau}${tieuDe}<div class="sld-sl co-bd"><div class="sld-sl-trai">${chuSo}</div>${bd}</div>`
            : `${dau}${tieuDe}<div class="sld-sl${lanThu % 2 ? " lat" : ""}">${chuSo}</div>`;
        }
        break;
      case "gioi_han":
        than = `${dau}${tieuDe}<ul class="sld-gh">${ds("muc", (m, i) => `<li><span class="sld-gh-dau">!</span><div>
          ${o(`muc.${i}.ten`, m.ten, "b", "", "Giới hạn")}${o(`muc.${i}.he_qua`, m.he_qua, "p", "", "Hệ quả")}</div></li>`)}</ul>`;
        break;
      case "dong_lai":
        than = `${dau}${tieuDe}<ol class="sld-dl">${(s.y || []).map((y, i) =>
          `<li class="sld-note"><span class="bang-keo"></span><span class="sld-dl-so">${i + 1}</span>${o(`y.${i}`, y, "p", "", "Điều mang về")}</li>`).join("")}</ol>
          <p class="sld-hoi"><b class="sld-tay">Câu hỏi thảo luận</b> ${o("cau_hoi", s.cau_hoi, "span", "", "Một câu hỏi mở")}</p>
          <p class="sld-cam-on">Cảm ơn · Hỏi đáp</p>`;
        break;
      default:
        than = tieuDe;
    }
    return `<section class="sld v-${esc(s.vai)}" data-id="${esc(s.id || "")}"><div class="sld-giay">${than}</div>${chan}</section>`;
  }

  /** Co chữ cho vừa khung — đo `scrollHeight`, giảm `--s`, đo lại (đúng thuật
      toán normAutofit của PowerPoint). Chỉ co tới 0,78: nhỏ hơn nữa là nhồi chữ,
      và lúc đó cần bớt chữ chứ không phải thu nhỏ (nhận xét B2 của chuyên gia). */
  function vuaKhung(el) {
    const giay = el && el.querySelector(".sld-giay");
    if (!giay) return;
    let k = 1, n = 0;
    giay.style.setProperty("--s", "1");
    while (giay.scrollHeight > giay.clientHeight + 2 && k > 0.78 && n++ < 12) {
      k = Math.max(0.78, k - 0.03);
      giay.style.setProperty("--s", k.toFixed(2));
    }
    el.classList.toggle("tran", giay.scrollHeight > giay.clientHeight + 2);
  }

  goc.SlideVe = { ve, vuaKhung, esc, chu, NHAN_VAI };
})(typeof window !== "undefined" ? window : globalThis);
