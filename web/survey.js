/* Loupe — màn hình kho survey.

   File riêng, cố ý: đây là cơ chế thứ hai và nó không dùng chung trạng thái nào
   với luồng đọc-hiểu trong app.js. Chỗ duy nhất chạm vào nhau là `showScreen()`
   và mấy hàm tiện dụng nhỏ (`$`, `esc`, `sci`) — đều đọc, không ghi.

   Ba tab:
     Hỏi đáp          — nhật ký từng vòng hiện dần theo SSE, rồi câu trả lời
     Bảng so sánh     — bài × facet, dựng từ phiếu, mở không tốn tiền
     Tra đoạn & bản đồ — tra thuần (miễn phí) + đồ thị thực thể

   Mọi danh sách dài đều **vẽ dần** (`SV_PAGE` + `svWatchTail`): kho sinh ra để
   chứa vài trăm bài, vẽ hết một lượt là trang khựng ngay lúc mở.

   Quy ước tiền, phải giữ đúng trên giao diện: nút nào tiêu tiền thì **ghi giá
   ngay trên nút**, giống nút "Căn chỉnh bằng model" ở màn soát. */

const SV = {
  id: "",           // kho đang mở
  survey: null,
  papers: [],
  facets: [],
  es: null,         // EventSource của lượt hỏi đang chạy
  found: [],        // kết quả tìm bài trên web
  answer: "",
  cites: new Set(),
  models: {},       // model đã phân giải cho kho này (xem `svShowModels`)
  hits: [],         // kết quả tra đoạn, đã lấy về nhưng chưa vẽ hết
  shownPapers: 0,   // số bài đã vẽ — xem `svPage`
  shownHits: 0,
  surveys: [],      // danh sách kho, dùng cho ô "chuyển bài sang kho khác"
};

/* Vẽ dần thay vì vẽ hết.

   Kho survey sinh ra để chứa vài chục tới vài trăm bài, và mỗi bài trong danh
   sách là một `<li>` có tiêu đề, chip trạng thái và ba nút. Vẽ hết một lượt thì
   `innerHTML` dựng hàng nghìn node cho một cột mà mắt chỉ nhìn thấy mười dòng
   đầu — trang khựng lúc mở, và khựng lại mỗi lần `svLoad()` chạy sau một thao
   tác nhỏ.

   Trang đầu 30 dòng là quá đủ cho một cột cao chừng ấy. Phần còn lại nạp khi
   người dùng cuộn tới, qua `IntersectionObserver` — không cần nghe sự kiện
   `scroll`, nên không có hàm nào chạy 60 lần một giây. */
const SV_PAGE = 30;

const SV_KEY = "docdoc:survey";   // giữ tiền tố cũ, xem chú thích ở PREF trong app.js

/* ---------------------------------------------------------------- tiện ích */

const svFetch = async (url, opt) => {
  const r = await fetch(url, opt);
  if (!r.ok) {
    let msg = r.statusText;
    try { msg = (await r.json()).detail || msg; } catch (_) { /* body rỗng */ }
    throw new Error(msg);
  }
  return r.json();
};

/* `money()` dùng lại của app.js — KHÔNG khai lại ở đây.

   Hai file này là hai `<script>` thường, tức chung một phạm vi toàn cục. Khai
   `const money` sau khi app.js đã có `function money` là `SyntaxError: Identifier
   'money' has already been declared` **ngay lúc phân tích cú pháp**, và cả
   survey.js không chạy dòng nào. Đã vỡ đúng vậy, và `node --check` không thấy vì
   nó soát từng file một. Chỉ trình duyệt thật mới bắt được.

   Vì thế mọi tên khai ở cấp ngoài cùng của file này đều có tiền tố `sv`. */

/* Markdown tối giản cho câu trả lời: tiêu đề, đậm, nghiêng, mã, bảng, danh sách.
   Không kéo thư viện về — câu trả lời do prompt của ta định khuôn nên phạm vi
   cú pháp hẹp và biết trước. Escape TRƯỚC, luôn luôn. */
function svMd(src) {
  const lines = esc(src).split("\n");
  const out = [];
  let inList = false, inTable = false;
  const inline = (s) => s
    .replace(/`([^`]+)`/g, "<code>$1</code>")
    .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
    .replace(/(^|\W)\*([^*\n]+)\*/g, "$1<em>$2</em>")
    .replace(/\[([A-Za-z0-9]{3,40})\]/g, '<a class="sv-cite" data-cite="$1">[$1]</a>');

  const close = () => {
    if (inList) { out.push("</ul>"); inList = false; }
    if (inTable) { out.push("</tbody></table></div>"); inTable = false; }
  };
  for (const raw of lines) {
    const line = raw.trimEnd();
    let m;
    if ((m = line.match(/^(#{1,4})\s+(.*)$/))) {
      close();
      const lv = Math.min(6, m[1].length + 1);
      out.push(`<h${lv}>${inline(m[2])}</h${lv}>`);
    } else if (/^\s*\|.*\|\s*$/.test(line)) {
      const cells = line.trim().slice(1, -1).split("|").map((c) => inline(c.trim()));
      if (/^[\s|:-]+$/.test(line)) continue;          // hàng kẻ phân cách
      if (!inTable) {
        close();
        out.push('<div class="sv-tablewrap"><table><thead><tr>'
          + cells.map((c) => `<th>${c}</th>`).join("") + "</tr></thead><tbody>");
        inTable = true;
      } else {
        out.push("<tr>" + cells.map((c) => `<td>${c}</td>`).join("") + "</tr>");
      }
    } else if ((m = line.match(/^\s*[-*]\s+(.*)$/))) {
      if (inTable) close();
      if (!inList) { out.push("<ul>"); inList = true; }
      out.push(`<li>${inline(m[1])}</li>`);
    } else if (!line.trim()) {
      close();
    } else {
      close();
      out.push(`<p>${inline(line)}</p>`);
    }
  }
  close();
  return out.join("\n");
}

/* ------------------------------------------------------------ nạp dữ liệu */

async function svLoadList() {
  let d = await svFetch("/api/surveys");

  /* Chưa có kho nào thì TỰ TẠO một cái, đừng để màn hình rơi vào trạng thái
     "chưa chọn kho". Trạng thái đó làm `SV.id` rỗng, và mọi nút sau đó gọi vào
     `/api/survey//find` — dấu gạch đôi khớp nhầm route khác và trả về
     "Method Not Allowed", một thông báo chẳng liên quan gì tới việc người dùng
     vừa làm. Bắt người ta phải tạo kho trước khi được tìm cũng là bắt họ học
     một khái niệm chưa cần tới. */
  if (!d.surveys.length) {
    await svFetch("/api/survey", {
      method: "POST", headers: { "content-type": "application/json" },
      body: JSON.stringify({ name: "Kho của tôi" }),
    });
    d = await svFetch("/api/surveys");
  }

  /* Kho là thứ có NHIỀU, và cái chọn kho phải nói ra điều đó ngay.

     Bản trước chỉ liệt kê các kho đang có, còn "tạo kho mới" nằm trong menu `⋯`
     — nên người dùng mở lên thấy đúng một dòng và tưởng công cụ chỉ có một kho.
     Nhét luôn "＋ Tạo kho mới" vào cuối danh sách: chỗ người ta nhìn khi muốn
     đổi kho cũng chính là chỗ người ta nhìn khi muốn thêm kho. */
  SV.surveys = d.surveys;
  const pick = $("#svPick");
  pick.innerHTML = d.surveys.map((s) =>
    `<option value="${esc(s.id)}">${esc(s.name)} · ${s.papers} bài</option>`).join("")
    + '<option disabled>──────────</option>'
    + '<option value="__new">＋ Tạo kho mới…</option>';
  const want = SV.id || localStorage.getItem(SV_KEY) || d.surveys[0].id;
  SV.id = d.surveys.some((s) => s.id === want) ? want : d.surveys[0].id;
  pick.value = SV.id;
  pick.title = d.surveys.length > 1
    ? `${d.surveys.length} kho — mỗi kho là một chủ đề riêng, tìm kiếm không lẫn sang nhau`
    : "Tạo thêm kho cho chủ đề khác — hai kho không lẫn nội dung sang nhau";
  await svLoad();
  svBackends(d);
}

/* Mỗi kho là một chủ đề riêng biệt: `bm25()` và bộ tìm dense đều lọc theo
   `survey_id`, nên hỏi trong kho này không bao giờ lôi ra bài của kho kia. Đó là
   lý do nên tách kho theo chủ đề thay vì đổ chung — đổ chung thì mỗi câu hỏi
   phải cạnh tranh với tài liệu chẳng liên quan, và phiếu toàn kho (`corpus_digest`,
   phần được cache) phình ra vô ích. */
async function svNewSurvey(name) {
  const s = await svFetch("/api/survey", {
    method: "POST", headers: { "content-type": "application/json" },
    body: JSON.stringify({ name }),
  });
  SV.id = s.id;
  await svLoadList();
}

/* Chốt chặn cuối: không cho request nào rời trình duyệt khi chưa có mã kho.
   Gọi ở đầu mọi hành động — rẻ, và nó biến một lớp lỗi thành không thể xảy ra. */
async function svNeedId() {
  if (!SV.id) await svLoadList();
  return !!SV.id;
}

/* Model đang chạy phải hiện ra ở MỌI nơi tiêu tiền, không chỉ trong ô chọn.

   Ô chọn để trống nghĩa là "theo .env", mà .env thì người dùng không đọc được từ
   trình duyệt — nên nếu chỉ hiện ô trống, họ không có cách nào biết mình đang
   chạy bằng model nào. Đó là thứ quyết định cả chất lượng lẫn hoá đơn. */
const svShort = (id) => (id || "").split("/").pop().replace(/^~/, "");

function svShowModels() {
  const m = SV.models || {};
  if (!m.strong) return;
  const txt = `tổng hợp: ${svShort(m.strong)} · các bước khác: ${svShort(m.fast)}`;
  const tip = `Model tổng hợp câu trả lời và bản tổng hợp: ${m.strong} (${m.strong_src})\n`
    + `Model cho lập kế hoạch, chấm lại, đọc đoạn, nạp bài: ${m.fast} (${m.fast_src})\n`
    + "Đổi ở Hỏi đáp → Tuỳ chọn & model.";
  $$(".sv-modelnote").forEach((el) => { el.textContent = txt; el.title = tip; });
}

/* Danh sách model dùng chung với luồng đọc-hiểu (`state.models`, nạp từ
   /api/config lúc khởi động). Ô trống nghĩa là "theo .env" — giữ lựa chọn đó
   hiện ra thay vì âm thầm chốt một model, để người dùng biết mình đang ở mặc
   định nào và đổi được mà không phải sửa file. */
function svFillModels(d) {
  const opt = (cur) => '<option value="">Theo .env (mặc định)</option>'
    + (state.models || []).map((m) =>
      `<option value="${esc(m.id)}" title="${esc(m.label || m.id)}"`
      + `${m.id === cur ? " selected" : ""}>${esc(shortLabel(m))}</option>`).join("");
  // Cùng một thiết lập, hiện ở hai chỗ: người dùng đang ở tab Tổng hợp thì
  // không nên phải đi sang tab khác mới đổi được model dựng bản tổng hợp.
  ["#svModel", "#svSynModel", "#svLecModel"].forEach((sel) => {
    const el = $(sel);
    if (el) { el.innerHTML = opt(d.model || ""); el.value = d.model || ""; }
  });
  $("#svFastModel").innerHTML = opt(d.fast_model || "");
  $("#svFastModel").value = d.fast_model || "";
}

/* Nói thật về bộ máy đang chạy. Chạy kém âm thầm (không nạp được model nên rơi
   về BM25) là kiểu hỏng khó phát hiện nhất, nên nó phải hiện ra. */
function svBackends(d) {
  const e = d.embed || {}, r = d.rerank || {};
  const model = (e.model || "").split("/").pop();

  // Nhãn ngắn để liếc, chi tiết đầy đủ nằm ở tooltip. Bày cả ba câu ra thanh
  // trên thì nó dài hơn cả tên kho, mà đây là thứ người ta xem một lần rồi thôi.
  let label, tip;
  if (e.backend === "off") {
    label = "BM25";
    tip = "Tìm ngữ nghĩa đang tắt (EMBED_BACKEND=off) — chỉ còn tìm theo từ khoá.";
  } else if (e.err) {
    label = "BM25 (vector lỗi)";
    tip = "Không nạp được model vector: " + e.err;
  } else {
    label = "lai · " + model;
    tip = `Tìm lai: BM25 + ${model}`
      + (e.ready ? ` (đang chạy trên ${e.device})` : " (nạp khi dùng lần đầu)");
  }
  const rr = r.backend === "off" ? "chấm lại bằng model"
    : r.err ? "chấm lại bằng model (cross-encoder lỗi)"
      : "chấm lại: cross-encoder + model";
  tip += "\n" + rr;
  if (!d.web_search) tip += "\nTìm bài trên web thường: chưa có SEARCH_API_KEY (arXiv/OpenAlex/Crossref vẫn chạy).";

  const el = $("#svEngine");
  el.textContent = label;
  el.title = tip;
}

async function svLoad() {
  if (!SV.id) return;
  localStorage.setItem(SV_KEY, SV.id);
  const d = await svFetch(`/api/survey/${SV.id}`);
  SV.survey = d;
  SV.papers = d.papers;
  SV.facets = d.facets;
  $("#svBudget").value = d.budget_usd ?? 0.5;
  SV.models = d.models || {};
  svFillModels(d);
  svShowModels();
  svRenderPapers();
  svRenderHistory(d.runs || []);
  const st = d.stats;
  $("#svStats").textContent =
    `${st.papers} bài · ${st.chunks} đoạn · ${st.carded} phiếu · đã tiêu ${money(st.spent)}`;
  // Kho đã có bài thì gấp khối thêm bài lại, nhường chỗ cho danh sách.
  $("#svAddBox").classList.toggle("is-shut", st.papers > 0);
}

function svRenderPapers(more = false) {
  const el = $("#svPapers");
  const btn = $("#svMorePapers");
  $("#svCount").textContent = SV.papers.length
    ? `Tài liệu · ${SV.papers.length}` : "Tài liệu";

  if (!SV.papers.length) {
    el.innerHTML = '<li class="muted small">Chưa có bài nào. Kéo PDF vào ô bên trên.</li>';
    btn.classList.add("hidden");
    // Kho trống thì mở sẵn khối thêm bài — đóng nó lại lúc này là giấu đúng thứ
    // duy nhất người dùng cần làm.
    $("#svAddBox").classList.remove("is-shut");
    return;
  }
  if (!more) SV.shownPapers = 0;
  const chip = (p) => {
    if (p.status === "carded") return '<i class="chip ok">đủ</i>';
    if (p.status === "indexed") return '<i class="chip">chưa bơm</i>';
    if (p.status === "abstract_only") return '<i class="chip warn">chỉ abstract</i>';
    if (p.status === "failed") return '<i class="chip bad">lỗi</i>';
    return `<i class="chip">${esc(p.status)}</i>`;
  };
  const slice = SV.papers.slice(SV.shownPapers, SV.shownPapers + SV_PAGE);
  const html = slice.map((p) => `
    <li class="sv-paper" data-pid="${esc(p.id)}">
      <div class="sv-ptitle">${esc(p.title || "(không tiêu đề)")}</div>
      <div class="sv-pmeta">
        ${chip(p)}
        ${p.year ? `<span>${p.year}</span>` : ""}
        ${p.venue ? `<span>${esc(p.venue)}</span>` : ""}
        ${p.cites ? `<span>${p.cites} trích dẫn</span>` : ""}
        ${p.loupe_doc_id ? `<a href="#doc=${esc(p.loupe_doc_id)}" title="Mở trong phần Dịch">đọc →</a>` : ""}
      </div>
      <div class="sv-pact">
        ${p.status === "indexed"
          ? '<button class="btn xs" data-act="enrich">Bơm nội dung · ~$0,034</button>' : ""}
        ${p.card ? '<button class="btn xs" data-act="recard">Bóc lại phiếu · ~$0,01</button>' : ""}
        <button class="btn xs" data-act="edit" title="Sửa tiêu đề, năm, nơi đăng — miễn phí">Sửa</button>
        ${SV.surveys.length > 1
          ? '<button class="btn xs" data-act="move" title="Chuyển sang kho khác — giữ nguyên phiếu, câu ngữ cảnh, cây tóm lược và bài giảng, không tốn tiền">Chuyển kho</button>' : ""}
        <button class="btn xs is-danger" data-act="drop">Bỏ</button>
      </div>
    </li>`).join("");

  if (more) el.insertAdjacentHTML("beforeend", html);
  else el.innerHTML = html;
  SV.shownPapers += slice.length;

  const left = SV.papers.length - SV.shownPapers;
  btn.classList.toggle("hidden", left <= 0);
  btn.textContent = `Xem thêm ${left} bài…`;
  svWatchTail(btn, () => svRenderPapers(true));
}

/* Nạp tiếp khi cái nút "xem thêm" lọt vào tầm nhìn.

   Dùng `IntersectionObserver` chứ không nghe `scroll`: trình duyệt tự báo khi
   phần tử vào khung nhìn, nên không có hàm nào chạy liên tục trong lúc cuộn.
   Ngắt quan sát ngay sau khi nạp, rồi lần vẽ sau gắn lại — nếu không thì một
   phần tử bị quan sát nhiều lần và nạp nhảy cóc mấy trang một lúc. */
/* Sửa metadata của bài — mở ngay trong thẻ, cùng lối với hộp chuyển kho.

   Tiêu đề bóc từ PDF sai là chuyện thường, và nó KHÔNG chỉ xấu: tiêu đề nằm
   trong chỉ mục toàn văn, trong phiếu toàn kho gửi cho model, và là thứ dùng để
   tra Semantic Scholar cho phần đối chiếu. Đã gặp một bài bóc còn mỗi "Question
   Answering" — hỏng cả ba chỗ cùng lúc, mà trước bản này không có cách nào sửa. */
const SV_META = [
  ["title", "Tiêu đề", "text"],
  ["year", "Năm", "number"],
  ["venue", "Nơi đăng", "text"],
  ["authors", "Tác giả", "text"],
  ["url", "Liên kết", "text"],
];

function svEditBox(li, pid) {
  if (li.querySelector(".sv-edit")) return;
  const p = SV.papers.find((x) => x.id === pid) || {};
  const box = document.createElement("div");
  box.className = "sv-move sv-edit";
  box.innerHTML = SV_META.map(([k, nhan, kieu]) =>
      `<label>${nhan}<input class="input" data-k="${k}" type="${kieu}"
        value="${esc(p[k] == null ? "" : String(p[k]))}"></label>`).join("")
    + `<div class="sv-moveact">
         <button class="btn xs" data-go="1">Lưu · miễn phí</button>
         <button class="btn xs" data-cancel="1">Thôi</button>
       </div>
       <p class="small muted">Tiêu đề cũng là thứ dùng để đánh chỉ mục và để tra
         bài trên Semantic Scholar, nên sửa đúng thì phần đối chiếu ở tab Bài
         giảng cũng tra lại được.</p>`;
  li.appendChild(box);
  box.querySelector("input").focus();
  box.querySelector("input").select();

  box.onkeydown = (e) => {
    if (e.key === "Enter") box.querySelector("[data-go]").click();
    if (e.key === "Escape") box.remove();
  };
  box.onclick = async (e) => {
    if (e.target.dataset.cancel) { box.remove(); return; }
    if (!e.target.dataset.go) return;
    const body = {};
    box.querySelectorAll("input[data-k]").forEach((i) => { body[i.dataset.k] = i.value; });
    e.target.disabled = true;
    try {
      await svFetch(`/api/survey/${SV.id}/paper/${pid}`, {
        method: "PATCH", headers: { "content-type": "application/json" },
        body: JSON.stringify(body),
      });
      svProg("đã lưu · miễn phí", true);
      await svLoad();
    } catch (err) {
      box.querySelector("p").textContent = err.message;
      box.querySelector("p").classList.add("sv-moveerr");
      e.target.disabled = false;
    }
  };
}

/* Chuyển bài sang kho khác — mở ngay tại chỗ, không mở hộp thoại.

   Nạp nhầm kho là chuyện thường, và cách chữa hiển nhiên (bỏ đi, nạp lại) ném
   mất phần đắt nhất: phiếu, câu ngữ cảnh từng đoạn, cây tóm lược, vector, bài
   giảng. Nút này giữ nguyên tất cả và **không tốn đồng nào**, nên nó phải nói
   ra điều đó — người dùng không có cách nào tự biết. */
function svMoveBox(li, pid) {
  if (li.querySelector(".sv-move")) return;      // đã mở rồi
  const others = SV.surveys.filter((s) => s.id !== SV.id);
  if (!others.length) return;

  const box = document.createElement("div");
  box.className = "sv-move";
  box.innerHTML = `<label>Chuyển sang
      <select class="input">${others.map((s) =>
        `<option value="${esc(s.id)}">${esc(s.name)} · ${s.papers} bài</option>`).join("")}</select>
    </label>
    <div class="sv-moveact">
      <button class="btn xs" data-go="1">Chuyển · miễn phí</button>
      <button class="btn xs" data-cancel="1">Thôi</button>
    </div>
    <p class="small muted">Giữ nguyên phiếu, câu ngữ cảnh, cây tóm lược, vector và
      bài giảng — không bóc lại, không gọi model.</p>`;
  li.appendChild(box);
  box.querySelector("select").focus();

  box.onclick = async (e) => {
    if (e.target.dataset.cancel) { box.remove(); return; }
    if (!e.target.dataset.go) return;
    const to = box.querySelector("select").value;
    e.target.disabled = true;
    try {
      const d = await svFetch(`/api/survey/${SV.id}/paper/${pid}/move`, {
        method: "POST", headers: { "content-type": "application/json" },
        body: JSON.stringify({ to }),
      });
      const ten = (SV.surveys.find((s) => s.id === to) || {}).name || "kho kia";
      svProg(`đã chuyển sang “${ten}” · miễn phí`
        + (d.entities ? ` · ${d.entities} thực thể đi theo` : ""), true);
      await svLoadList();          // số bài của CẢ HAI kho đều đổi
    } catch (err) {
      // 409 = kho đích đã có đúng bài này. Nói thẳng ra thay vì "lỗi".
      box.querySelector("p").textContent = err.message;
      box.querySelector("p").classList.add("sv-moveerr");
      e.target.disabled = false;
    }
  };
}

function svWatchTail(el, load) {
  if (el._io) { el._io.disconnect(); el._io = null; }
  if (el.classList.contains("hidden")) return;
  el._io = new IntersectionObserver((ents) => {
    if (ents.some((e) => e.isIntersecting)) { el._io.disconnect(); el._io = null; load(); }
  }, { rootMargin: "200px" });
  el._io.observe(el);
}

function svRenderHistory(runs) {
  // 8 lượt gần nhất là đủ; cũ hơn thì mở bằng nút. Vẽ hết vài trăm lượt vào một
  // cột không ai cuộn tới là công dựng DOM bỏ đi.
  const show = runs.slice(0, 8);
  $("#svHistory").innerHTML = show.length
    ? '<h3 class="sv-h3">Đã hỏi</h3>' + show.map((r) =>
      `<div class="sv-runrow">
         <button class="sv-runitem" data-run="${esc(r.id)}">
           <span>${esc(r.question)}</span><i>${money(r.cost)}</i></button>
         <button class="sv-runx" data-dropRun="${esc(r.id)}" title="Xoá lượt hỏi này">×</button>
       </div>`).join("")
      + (runs.length > show.length
        ? `<button class="sv-link" id="svAllRuns">Xem cả ${runs.length} lượt…</button>` : "")
    : "";
  const all = $("#svAllRuns");
  if (all) all.onclick = () => svRenderAllRuns(runs);

  $("#svHistory").onclick = async (e) => {
    const x = e.target.closest("[data-dropRun]");
    if (!x) return;
    e.stopPropagation();            // đừng mở lượt hỏi mà mình vừa xoá
    if (!confirm("Xoá lượt hỏi này khỏi lịch sử?")) return;
    await svFetch(`/api/survey/${SV.id}/run/${x.dataset.dropRun}`, { method: "DELETE" });
    await svLoad();
  };
}

function svRenderAllRuns(runs) {
  $("#svHistory").innerHTML = '<h3 class="sv-h3">Đã hỏi</h3>' + runs.map((r) =>
    `<button class="sv-runitem" data-run="${esc(r.id)}">
       <span>${esc(r.question)}</span><i>${money(r.cost)}</i></button>`).join("");
}

/* ------------------------------------------------------------- nạp bài */

/* `free = true` cho những việc KHÔNG gọi model. Gắn tên model vào một dòng ghi
   "miễn phí" thì tự mâu thuẫn, và người dùng có lý do để tưởng là vừa bị tính
   tiền. Tên model chỉ nên hiện ở đúng chỗ tiền đi ra. */
function svProg(msg, free = false) {
  const m = free ? "" : (SV.models || {}).fast;
  $("#svProg").textContent = msg ? (m ? `${msg} · ${svShort(m)}` : msg) : "";
}

/* Mở SSE tiến trình TRƯỚC khi POST, giống màn nạp bài của luồng đọc: mở sau thì
   những bước đầu đã chạy xong và người dùng nhìn vào một ô trống. */
function svWatch() {
  const es = new EventSource(`/api/survey/${SV.id}/progress`);
  es.addEventListener("step", (e) => {
    const d = JSON.parse(e.data);
    svProg(d.msg);
  });
  es.onerror = () => es.close();
  return es;
}

async function svUpload(files) {
  if (!await svNeedId()) return;
  const fd = new FormData();
  [...files].forEach((f) => fd.append("files", f));
  fd.append("enrich", $("#svEnrich").checked ? "1" : "0");
  const es = svWatch();
  try {
    const d = await svFetch(`/api/survey/${SV.id}/papers`, { method: "POST", body: fd });
    const bad = d.papers.filter((p) => p.status === "failed");
    svProg(`nạp xong ${d.papers.length - bad.length}/${d.papers.length} bài · ${money(d.cost)}`
      + (bad.length ? ` · lỗi: ${bad.map((b) => b.title).join(", ")}` : ""));
    await svLoad();
  } catch (e) {
    svProg("Lỗi: " + e.message);
  } finally {
    es.close();
  }
}

/* ------------------------------------------------------------ hỏi đáp */

function svStepBox(kind, html) {
  $("#svSteps").insertAdjacentHTML("beforeend",
    `<div class="sv-step sv-step-${kind}">${html}</div>`);
  $("#svSteps").scrollTop = $("#svSteps").scrollHeight;
}

async function svAsk() {
  const q = $("#svQ").value.trim();
  if (!q || !await svNeedId()) return;
  svStopAsk();
  SV.answer = "";
  SV.cites = new Set();
  $("#svSteps").innerHTML = "";
  $("#svAnswer").innerHTML = "";
  $("#svWarns").innerHTML = "";
  $("#svCost").textContent = `đang chạy… (${svShort((SV.models || {}).fast)} tìm, `
    + `${svShort((SV.models || {}).strong)} tổng hợp)`;
  $("#svAsk").disabled = true;
  $("#svStop").classList.remove("hidden");

  const p = new URLSearchParams({
    q, budget: $("#svBudget").value || "0",
    entail: $("#svEntail").checked ? "1" : "0",
    cache: $("#svCache").checked ? "1" : "0",
  });
  const es = new EventSource(`/api/survey/${SV.id}/ask?${p}`);
  SV.es = es;

  es.addEventListener("cached", () =>
    svStepBox("cached", "Đã hỏi câu này rồi và kho chưa đổi — lấy lại bản cũ, <b>miễn phí</b>."));

  es.addEventListener("plan", (e) => {
    const d = JSON.parse(e.data);
    svStepBox("plan",
      `<b>Bảng kiểm</b> <i class="small muted">(${esc(d.intent || "")})</i>
       <ol class="sv-checklist">${(d.sub_questions || []).map((s) =>
        `<li id="sv-${esc(s.id)}"><b>${esc(s.id)}</b> ${esc(s.ask)}</li>`).join("")}</ol>
       ${d.pseudo_doc ? `<details class="small"><summary>đoạn văn giả dùng để tìm (query2doc)</summary>
         <p class="muted">${esc(d.pseudo_doc)}</p></details>` : ""}`);
  });

  es.addEventListener("search", (e) => {
    const d = JSON.parse(e.data);
    const l = d.lists || {};
    svStepBox("search",
      `<b>Vòng ${d.step} · tìm</b> — ${d.found} đoạn
       <span class="small muted">(BM25 ${l.bm25 || 0} · vector ${l.dense || 0} · đồ thị ${l.graph || 0})</span>
       <div class="sv-qs">${(d.queries || []).map((q) => `<code>${esc(q)}</code>`).join(" ")}</div>`);
  });

  es.addEventListener("read", (e) => {
    const d = JSON.parse(e.data);
    svStepBox("read",
      `<b>Vòng ${d.step} · đọc</b> — ${d.findings.length} phát hiện
       <ul class="sv-finds">${d.findings.map((f) =>
        `<li><b>${esc(f.for)}</b> ${esc(f.finding)}
          <a class="sv-cite" data-cite="${esc(f.chunk)}">[${esc(f.chunk)}]</a></li>`).join("")}</ul>`);
    (d.covered || []).forEach((id) => $("#sv-" + id)?.classList.add("is-done"));
  });

  es.addEventListener("gap", (e) => {
    const d = JSON.parse(e.data);
    if (!(d.missing || []).length) return;
    svStepBox("gap",
      `<b>Còn thiếu</b> <span class="small muted">· đã tiêu ${money(d.spent)}</span>
       <ul>${d.missing.map((m) =>
        `<li><b>${esc(m.id)}</b> ${esc(m.why)}${m.next_q ? ` → tìm tiếp: <code>${esc(m.next_q)}</code>` : ""}</li>`).join("")}</ul>`);
  });

  es.addEventListener("synth", (e) => {
    const d = JSON.parse(e.data);
    svStepBox("synth", `<b>Tổng hợp</b> từ ${d.evidence} đoạn, ${d.findings} phát hiện`
      + (d.model ? ` <span class="muted">bằng ${esc(svShort(d.model))}</span>` : "")
      + (d.stopped ? ` <span class="sv-stopped">— dừng sớm: ${esc(d.stopped)}</span>` : ""));
  });

  es.addEventListener("answer", (e) => {
    const d = JSON.parse(e.data);
    if (d.text) SV.answer += d.text;
    $("#svAnswer").innerHTML = svMd(SV.answer);
  });

  es.addEventListener("check", (e) => {
    const w = JSON.parse(e.data).warns || [];
    $("#svWarns").innerHTML = w.length
      ? `<div class="sv-warnbox"><b>${w.length} chỗ cần soát lại</b><ul>` + w.map((x) =>
        `<li><i>${esc(x.kind)}</i> ${esc(x.msg)}${x.text ? `<br><span class="muted">“${esc(x.text)}”</span>` : ""}</li>`)
        .join("") + "</ul></div>"
      : '<div class="sv-okbox">Soát cơ học không thấy vấn đề: số liệu khớp đoạn đã trích, mã đoạn có thật.</div>';
  });

  es.addEventListener("error", (e) => {
    let msg = "mất kết nối";
    try { msg = JSON.parse(e.data).msg; } catch (_) { /* lỗi mạng, không có body */ }
    svStepBox("err", `<b>Lỗi</b> ${esc(msg)}`);
    svStopAsk();
  });

  es.addEventListener("done", (e) => {
    const d = JSON.parse(e.data);
    $("#svCost").textContent =
      `${d.steps || 0} vòng · ${d.warns || 0} cảnh báo · ${money(d.cost)} · ${d.secs}s`
      + (d.stopped ? ` · DỪNG SỚM: ${d.stopped}` : "");
    svStopAsk();
    svLoad();
  });
}

function svStopAsk() {
  if (SV.es) { SV.es.close(); SV.es = null; }
  $("#svAsk").disabled = false;
  $("#svStop").classList.add("hidden");
}

/* ---------------------------------------------------------- tổng hợp */

function svPaperChip(id) {
  const nm = (SV.synth?.paper_names || {})[id];
  return nm
    ? `<span class="sv-pchip" title="${esc(id)}">${esc(nm)}</span>`
    : `<span class="sv-pchip is-bad" title="mã bài không có trong kho">${esc(id)}</span>`;
}

const svPaperList = (ids) => (ids || []).map(svPaperChip).join(" ");
const svCite = (c) => (c ? ` <a class="sv-cite" data-cite="${esc(c)}">[${esc(c)}]</a>` : "");

function svRenderSynth(d, stale) {
  const el = $("#svSyn");
  SV.synth = d;
  if (!d) {
    el.innerHTML = '<p class="muted">Chưa dựng. Bấm <b>Dựng</b> ở trên — cần ít nhất '
      + 'một bài đã bơm nội dung.</p>';
    return;
  }
  const P = (x) => (x ? `<p>${esc(x)}</p>` : "");
  const parts = [];

  if (stale) {
    parts.push('<div class="sv-stalebox">Kho đã đổi từ lúc dựng bản này — '
      + 'nội dung bên dưới có thể thiếu bài mới. Dựng lại để cập nhật.</div>');
  }

  parts.push(`<h2 class="sv-syn-h1">${esc(d.title || "")}</h2>`);
  if (d.scope) parts.push(`<p class="sv-scope">${esc(d.scope)}</p>`);

  const pb = d.problem || {};
  if (pb.statement || pb.why_hard) {
    parts.push('<h3 class="sv-syn-h2">Bài toán</h3>', P(pb.statement));
    if (pb.why_hard) parts.push(`<p><b>Khó ở đâu.</b> ${esc(pb.why_hard)}</p>`);
    if ((pb.framings || []).length) {
      parts.push('<p class="small muted">Cùng một hiện tượng nhưng đặt vấn đề khác nhau '
        + 'thì cách giải khác hẳn — đây là chỗ chia rẽ sâu nhất.</p><ul class="sv-framings">');
      pb.framings.forEach((f) => parts.push(
        `<li><b>${esc(f.name)}</b> — ${esc(f.desc)} ${svPaperList(f.papers)}</li>`));
      parts.push("</ul>");
    }
  }

  if ((d.approaches || []).length) {
    parts.push('<h3 class="sv-syn-h2">Các hướng tiếp cận</h3>');
    d.approaches.forEach((a) => {
      parts.push('<div class="sv-appr">',
        `<h4>${esc(a.name)}</h4>`,
        P(a.idea),
        a.mechanism ? `<p><b>Cơ chế.</b> ${esc(a.mechanism)}</p>` : "",
        // `bet` là trường có giá trị nhất — nó nói hướng này sụp khi nào.
        a.bet ? `<p class="sv-bet"><b>Đặt cược vào:</b> ${esc(a.bet)}</p>` : "",
        a.cost ? `<p class="small"><b>Cái giá.</b> ${esc(a.cost)}</p>` : "",
        (a.papers || []).length ? `<p class="sv-plist">${svPaperList(a.papers)}</p>` : "");
      if ((a.evidence || []).length) {
        parts.push("<ul>");
        a.evidence.forEach((e) => parts.push(`<li>${esc(e.claim)}${svCite(e.cite)}</li>`));
        parts.push("</ul>");
      }
      parts.push("</div>");
    });
  }

  if ((d.novelty || []).length) {
    // Mỗi bài một mục — kho trăm bài thì phần này dài bằng cả phần còn lại.
    parts.push('<h3 class="sv-syn-h2">Tính mới</h3><ul class="sv-nov" id="svNovList">');
    d.novelty.slice(0, SV_PAGE).forEach((n) => parts.push(
      `<li>${svPaperChip(n.paper)} ${esc(n.new)}${svCite(n.cite)}`
      + (n.assembled ? `<br><span class="muted small">Phần ghép sẵn: ${esc(n.assembled)}</span>` : "")
      + "</li>"));
    parts.push("</ul>");
    if (d.novelty.length > SV_PAGE) {
      parts.push(`<button class="sv-link" id="svMoreNov">Xem thêm ${d.novelty.length - SV_PAGE} bài…</button>`);
    }
  }

  if ((d.tensions || []).length) {
    parts.push('<h3 class="sv-syn-h2">Chỗ các bài nói ngược nhau</h3>');
    d.tensions.forEach((t) => {
      parts.push('<div class="sv-tension">', `<b>${esc(t.about)}</b><ul>`);
      (t.sides || []).forEach((s) => parts.push(
        `<li>${svPaperList(s.papers)} ${esc(s.claim)}${svCite(s.cite)}</li>`));
      parts.push("</ul>",
        t.why ? `<p class="small muted">Vì sao khác nhau: ${esc(t.why)}</p>` : "", "</div>");
    });
  }

  if ((d.lineage || []).length) {
    parts.push('<h3 class="sv-syn-h2">Kế thừa</h3>',
      '<p class="small muted">Tính từ đồ thị thực thể, không hỏi model — mỗi liên hệ '
      + 'kèm mã đoạn đã đọc ra nó, bấm vào kiểm được.</p><div class="sv-lin" id="svLinList">');
    d.lineage.slice(0, SV_PAGE).forEach((g) => parts.push(svLinRow(g)));
    parts.push("</div>");
    if (d.lineage.length > SV_PAGE) {
      parts.push(`<button class="sv-link" id="svMoreLin">Xem thêm ${d.lineage.length - SV_PAGE} liên hệ…</button>`);
    }
  }

  if ((d.gaps || []).length) {
    parts.push('<h3 class="sv-syn-h2">Khoảng trống còn lại</h3><ul>');
    d.gaps.forEach((g) => parts.push(`<li><b>${esc(g.gap)}</b> — ${esc(g.why)}</li>`));
    parts.push("</ul>");
  }

  if ((d.read_order || []).length) {
    parts.push('<h3 class="sv-syn-h2">Nên đọc theo thứ tự</h3><ol class="sv-order">');
    d.read_order.forEach((r) => parts.push(
      `<li>${svPaperChip(r.paper)} <span class="muted">${esc(r.why)}</span></li>`));
    parts.push("</ol>");
  }

  const done = () => {
    // Nạp tiếp khi nút lọt vào tầm nhìn — cùng cơ chế với danh sách tài liệu.
    const nov = $("#svMoreNov");
    if (nov) svWatchTail(nov, () => svMoreSyn("novelty", "#svNovList", nov, svNovRow));
    const lin = $("#svMoreLin");
    if (lin) svWatchTail(lin, () => svMoreSyn("lineage", "#svLinList", lin, svLinRow));
  };
  parts.push(`<p class="sv-cost">${d.papers || 0} bài · dựng hết ${money(d.cost)}`
    + (d.model ? ` · bằng <b>${esc(svShort(d.model))}</b>` : "") + "</p>");
  el.innerHTML = parts.join("");
  done();
}

function svLinRow(g) {
  return `<div class="sv-edge"><b>${esc(g.src)}</b> ${esc(g.rel)} <b>${esc(g.dst)}</b>`
    + ` <span class="muted">${esc((g.paper_title || "").slice(0, 46))}`
    + `${g.year ? " · " + g.year : ""}</span>${svCite(g.cite)}</div>`;
}

function svNovRow(n) {
  return `<li>${svPaperChip(n.paper)} ${esc(n.new)}${svCite(n.cite)}`
    + (n.assembled ? `<br><span class="muted small">Phần ghép sẵn: ${esc(n.assembled)}</span>` : "")
    + "</li>";
}

/* Vẽ nốt phần còn lại của một mục trong bản tổng hợp. Vẽ HẾT phần còn lại chứ
   không chia trang tiếp: tới đây người dùng đã chủ động đòi xem thêm, và các
   mục này không dài tới mức phải chia lần nữa. */
function svMoreSyn(key, sel, btn, row) {
  const box = $(sel);
  if (!box) return;
  box.insertAdjacentHTML("beforeend", SV.synth[key].slice(SV_PAGE).map(row).join(""));
  btn.remove();
}

function svSynWarns(w) {
  $("#svSynWarns").innerHTML = (w || []).length
    ? `<div class="sv-warnbox"><b>${w.length} chỗ cần soát lại</b><ul>`
      + w.map((x) => `<li><i>${esc(x.kind)}</i> ${esc(x.msg)}`
        + (x.text ? `<br><span class="muted">“${esc(x.text)}”</span>` : "") + "</li>").join("")
      + "</ul></div>"
    : "";
}

async function svLoadSynth() {
  if (!await svNeedId()) return;
  const d = await svFetch(`/api/survey/${SV.id}/synthesis`);
  svRenderSynth(d.synth, d.stale);
  svSynWarns(d.synth?.warns);
  $("#svSynMd").href = `/api/survey/${SV.id}/synthesis?fmt=md`;
  $("#svSynMd").classList.toggle("hidden", !d.synth);
  $("#svSynDrop").classList.toggle("hidden", !d.synth);
  $("#svSynGo").textContent = d.synth ? "Dựng lại · ~$0,09" : "Dựng · ~$0,09";
  $("#svSynGo").title = `Chạy bằng ${(SV.models || {}).strong || "?"}`;
  $("#svSynProg").textContent = d.carded
    ? "" : "Chưa bài nào có phiếu — bơm nội dung cho vài bài ở cột trái trước.";
}

function svBuildSynth() {
  if (!SV.id) return;
  const btn = $("#svSynGo");
  btn.disabled = true;
  $("#svSynWarns").innerHTML = "";
  $("#svSynProg").textContent = "đang bắt đầu…";

  const es = new EventSource(`/api/survey/${SV.id}/synthesis/build`);
  es.addEventListener("stage", (e) => {
    const d = JSON.parse(e.data);
    $("#svSynProg").textContent = `${d.msg}… (${svShort((SV.models || {}).strong)})`;
  });
  es.addEventListener("done", (e) => {
    const d = JSON.parse(e.data);
    svRenderSynth(d.synth, false);
    svSynWarns(d.synth.warns);
    $("#svSynProg").textContent = `xong · ${money(d.cost)} · ${d.secs}s`;
    $("#svSynMd").classList.remove("hidden");
    $("#svSynDrop").classList.remove("hidden");
    btn.textContent = "Dựng lại · ~$0,09";
    btn.disabled = false;
    es.close();
    svLoad();
  });
  es.addEventListener("error", (e) => {
    let msg = "mất kết nối";
    try { msg = JSON.parse(e.data).msg; } catch (_) { /* lỗi mạng, không có body */ }
    $("#svSynProg").textContent = "Lỗi: " + msg;
    btn.disabled = false;
    es.close();
  });
}

/* ------------------------------------------------------ bảng trích xuất */

/* Sửa cột của bảng so sánh.

   Cột dựng thẳng từ `card` nên **thêm/bớt cột không gọi model**: ô nào phiếu đã
   có thì hiện ngay, ô nào chưa có thì trống cho tới lần bóc lại phiếu. Vì thế
   thao tác này miễn phí và nói ra được điều đó.

   Mỗi dòng là `khoá | Nhãn`: khoá phải khớp tên trường trong phiếu (`task`,
   `method`, `datasets`…), nhãn là thứ hiện trên đầu cột. Dạng một dòng một cột
   dễ sửa hơn hẳn một cái bảng có nút thêm/xoá từng hàng, mà lại sắp xếp lại
   được bằng cách kéo dòng. */
async function svEditFacets() {
  if (!await svNeedId()) return;
  const cur = (SV.facets || []).map((f) => `${f.key} | ${f.label}`).join("\n");
  const got = prompt(
    "Mỗi dòng một cột, dạng:  khoá | Nhãn hiện trên bảng\n"
    + "Khoá phải trùng tên trường trong phiếu (task, problem, idea, method,\n"
    + "datasets, metrics, baselines, novelty, limitations…).\n\n"
    + "Miễn phí — bảng dựng thẳng từ phiếu đã bóc, không gọi model.",
    cur);
  if (got === null) return;

  const facets = got.split("\n").map((line) => {
    const [k, ...rest] = line.split("|");
    const key = (k || "").trim();
    return key ? { key, label: (rest.join("|") || key).trim() || key } : null;
  }).filter(Boolean);
  if (!facets.length) {
    alert("Cần ít nhất một cột.");
    return;
  }
  await svFetch(`/api/survey/${SV.id}`, {
    method: "PATCH", headers: { "content-type": "application/json" },
    body: JSON.stringify({ facets }),
  });
  await svLoad();
  await svGrid();
}

/* Cắt chữ Ở ĐÂY, không cắt bằng CSS.

   Bản trước dùng `-webkit-line-clamp` và nó **không ăn** — Chrome trả về
   `display: flow-root` chứ không phải `-webkit-box`, nên chữ không bị cắt và
   một ô mười dòng kéo cả hàng cao gần một màn hình. Hàng cao thì thanh cuộn
   ngang bị đẩy xuống tận đáy, kéo ngang thành cực hình.

   Cắt bằng JS thì không phụ thuộc thuộc tính CSS nào cả, hàng nào cũng thấp như
   nhau, và DOM nhẹ hơn hẳn. Bảng này để LIẾC mà so sánh, không phải để đọc —
   ai cần nguyên văn thì rê chuột (`title`) hoặc mở bài. */
const SV_CELL = 190;   // ký tự mỗi ô, đủ 3–4 dòng ở bề ngang cột 300px

function svClip(s) {
  s = String(s || "").replace(/\s+/g, " ").trim();
  if (s.length <= SV_CELL) return s;
  // Cắt ở ranh giới từ, đừng cắt giữa chữ.
  const cut = s.slice(0, SV_CELL);
  const sp = cut.lastIndexOf(" ");
  return (sp > SV_CELL * 0.6 ? cut.slice(0, sp) : cut) + "…";
}

async function svGrid() {
  const d = await svFetch(`/api/survey/${SV.id}/matrix`);
  SV.grid = d;
  SV.shownRows = 0;
  svRenderGrid();
  $("#svCsv").href = `/api/survey/${SV.id}/matrix?fmt=csv`;
  $("#svMd").href = `/api/survey/${SV.id}/matrix?fmt=md`;
}

function svGridRow(d, r) {
  return "<tr>"
      + `<td class="sv-gt" title="${esc(r.title)}">${esc(svClip(r.title))}`
      + `${r.has_card ? "" : ' <i class="chip">chưa có phiếu</i>'}</td>`
      + `<td>${r.year || ""}</td>`
      + d.facets.map((f) => {
        const v = r.cells[f.key] || "";
        return `<td title="${esc(v)}">${esc(svClip(v))}</td>`;
      }).join("")
      + "</tr>";
}

/* Bảng cũng vẽ dần: mỗi hàng có (2 + số facet) ô, nên kho trăm bài với chín cột
   là hơn một nghìn ô — dựng hết một lượt thì mở tab là khựng. */
function svRenderGrid(more = false) {
  const d = SV.grid;
  if (!d) return;
  const head = ["Bài", "Năm", ...d.facets.map((f) => f.label)];
  const tb = $("#svGrid tbody");

  if (!more) {
    $("#svGrid").innerHTML =
      "<thead><tr>" + head.map((h) => `<th>${esc(h)}</th>`).join("") + "</tr></thead><tbody>"
      + (d.rows.length ? "" : `<tr><td colspan="${head.length}" class="muted">Chưa có phiếu nào. `
        + "Bơm nội dung cho vài bài ở cột trái để bảng có dữ liệu.</td></tr>")
      + "</tbody>";
    SV.shownRows = 0;
  }
  const slice = d.rows.slice(SV.shownRows, SV.shownRows + SV_PAGE);
  ($("#svGrid tbody") || tb).insertAdjacentHTML("beforeend",
    slice.map((r) => svGridRow(d, r)).join(""));
  SV.shownRows += slice.length;

  const btn = $("#svMoreRows");
  const left = d.rows.length - SV.shownRows;
  btn.classList.toggle("hidden", left <= 0);
  btn.textContent = `Xem thêm ${left} bài…`;
  svWatchTail(btn, () => svRenderGrid(true));
}

/* ------------------------------------------------------ bản đồ và đoạn */

async function svGraph() {
  const d = await svFetch(`/api/survey/${SV.id}/graph`);
  if (!d.entities.length) {
    $("#svGraph").innerHTML = '<p class="muted small">Chưa có thực thể nào — bơm nội dung cho vài bài trước.</p>';
    return;
  }
  const by = {};
  d.entities.forEach((e) => { (by[e.kind] = by[e.kind] || []).push(e); });
  const nice = { method: "Phương pháp", dataset: "Tập dữ liệu", metric: "Độ đo",
    task: "Bài toán", model: "Mô hình", concept: "Khái niệm", org: "Tổ chức" };
  const names = Object.fromEntries(d.entities.map((e) => [e.id, e.name]));
  // Mỗi loại chỉ vẽ 24 chip đầu; loại nào dài hơn thì mở bằng nút. Kho lớn có
  // hàng trăm thực thể, và một bức tường chip thì không ai đọc.
  const CHIPS = 24;
  $("#svGraph").innerHTML =
    Object.entries(by).map(([k, list]) => `
      <div class="sv-gcol"><h4>${esc(nice[k] || k)} · ${list.length}</h4>
        <div data-ents="${esc(k)}">${list.slice(0, CHIPS).map((e) =>
          `<span class="sv-ent" data-ent="${esc(e.name)}">${esc(e.name)}<i>${e.papers}</i></span>`
        ).join("")}</div>
        ${list.length > CHIPS
          ? `<button class="sv-link" data-more-ents="${esc(k)}">+${list.length - CHIPS} nữa</button>`
          : ""}</div>`).join("")
    + '<div class="sv-edges"><h4>Quan hệ các bài phát biểu</h4>'
    + (d.edges.length
      ? d.edges.slice(0, 60).map((g) =>
        `<div class="sv-edge">${esc(names[g.src] || "?")}
           <b>${esc(g.rel)}</b> ${esc(names[g.dst] || "?")}
           ${g.chunk_id ? `<a class="sv-cite" data-cite="${esc(g.chunk_id)}">[nguồn]</a>` : ""}
           ${g.note ? `<span class="muted small">${esc(g.note)}</span>` : ""}</div>`).join("")
      : '<p class="muted small">Chưa có quan hệ nào.</p>')
    + (d.edges.length > 60 ? `<p class="small muted">…và ${d.edges.length - 60} quan hệ nữa.</p>` : "")
    + "</div>";

  $("#svGraph").querySelectorAll("[data-more-ents]").forEach((b) => {
    b.onclick = () => {
      const k = b.dataset.moreEnts;
      $(`[data-ents="${k}"]`, $("#svGraph")).insertAdjacentHTML("beforeend",
        by[k].slice(CHIPS).map((e) =>
          `<span class="sv-ent" data-ent="${esc(e.name)}">${esc(e.name)}<i>${e.papers}</i></span>`
        ).join(""));
      b.remove();
    };
  });
}

async function svSearch() {
  const q = $("#svSearch").value.trim();
  if (!q || !await svNeedId()) return;
  $("#svHits").innerHTML = '<p class="muted small">đang tra…</p>';
  // Lấy về nhiều hơn số vẽ ra: tra là miễn phí và nhanh (không gọi model), nên
  // chi phí thật nằm ở việc dựng DOM chứ không ở việc lấy dữ liệu.
  const d = await svFetch(`/api/survey/${SV.id}/search?q=${encodeURIComponent(q)}&limit=60`);
  SV.hits = d.hits;
  SV.shownHits = 0;
  svRenderHits();
}

function svRenderHits(more = false) {
  const el = $("#svHits");
  const btn = $("#svMoreHits");
  if (!SV.hits.length) {
    el.innerHTML = '<p class="muted small">Không tra được đoạn nào khớp.</p>';
    btn.classList.add("hidden");
    return;
  }
  const html = SV.hits.slice(SV.shownHits, SV.shownHits + SV_PAGE).map((h) => `
    <div class="sv-hit">
      <div class="sv-hmeta">
        <b>${esc(h.title)}</b> ${h.year || ""} · ${esc(h.section || "")}
        ${h.level > 0 ? `<i class="chip">tóm lược tầng ${h.level}</i>` : `<span>tr.${h.page || "?"}</span>`}
        <a class="sv-cite" data-cite="${esc(h.id)}">[${esc(h.id)}]</a>
      </div>
      ${h.ctx ? `<div class="sv-hctx">${esc(h.ctx)}</div>` : ""}
      <div class="sv-htext">${sci(h.text)}</div>
      ${h.vi ? `<div class="sv-hvi">${sci(h.vi)}</div>` : ""}
    </div>`).join("");

  if (more) el.insertAdjacentHTML("beforeend", html);
  else el.innerHTML = html;
  SV.shownHits = Math.min(SV.shownHits + SV_PAGE, SV.hits.length);

  const left = SV.hits.length - SV.shownHits;
  btn.classList.toggle("hidden", left <= 0);
  btn.textContent = `Xem thêm ${left} đoạn…`;
  svWatchTail(btn, () => svRenderHits(true));
}

/* Bấm vào một trích dẫn thì phải mở ra ĐÚNG đoạn đó. Trích dẫn mà không tra
   ngược được thì chỉ là trang trí, và người đọc không có cách nào kiểm tra. */
async function svOpenCite(cid) {
  try {
    const d = await svFetch(`/api/survey/${SV.id}/chunk/${cid}`);
    const c = d.chunk;
    const box = document.createElement("div");
    box.className = "modal";
    box.innerHTML = `<div class="modal-card">
      <header class="modal-head"><b>${esc(c.paper_title || "")}</b>
        <span class="grow"></span>
        ${c.loupe_doc_id ? `<a class="btn btn-ghost small" href="#doc=${esc(c.loupe_doc_id)}">Mở trong màn đọc</a>` : ""}
        <button class="btn btn-ghost" data-close>Đóng</button></header>
      <div class="modal-body">
        <p class="small muted">${esc(c.section || "")} · trang ${c.page || "?"} ·
          ${c.level > 0 ? `tóm lược tầng ${c.level}` : "đoạn gốc"} · <code>${esc(c.id)}</code></p>
        ${c.ctx ? `<p class="sv-hctx">${esc(c.ctx)}</p>` : ""}
        <p class="sv-quote">${sci(c.text)}</p>
        ${c.vi ? `<p class="sv-hvi">${sci(c.vi)}</p>` : ""}
        ${(d.around || []).filter((a) => a.id !== c.id).map((a) =>
          `<p class="sv-around">${sci(a.text.slice(0, 400))}</p>`).join("")}
      </div></div>`;
    box.onclick = (e) => {
      if (e.target === box || e.target.hasAttribute("data-close")) box.remove();
    };
    document.body.appendChild(box);
  } catch (e) {
    alert("Không mở được đoạn: " + e.message);
  }
}

/* -------------------------------------------------------- tìm bài mới */

async function svFindPapers() {
  const q = $("#svFind").value.trim();
  if (!q || !await svNeedId()) return;
  $("#svFindList").innerHTML = "đang tìm…";
  $("#svFindBox").classList.remove("hidden");
  try {
    const d = await svFetch(`/api/survey/${SV.id}/find`, {
      method: "POST", headers: { "content-type": "application/json" },
      body: JSON.stringify({ q, limit: 25, web: true }),
    });
    SV.found = d.results;
    // Nguồn nào hỏng thì nói ra. Im lặng trả về ít kết quả hơn là kiểu hỏng khó
    // phát hiện nhất: danh sách trông vẫn bình thường.
    const errs = (d.errs || []).length
      ? `<div class="sv-warnbox"><b>${d.errs.length} nguồn không trả lời được</b><ul>`
        + d.errs.map((e) => `<li>${esc(e)}</li>`).join("") + "</ul></div>"
      : "";
    $("#svFindList").innerHTML = errs + (d.results.length ? d.results.map((r, i) => `
      <label class="sv-found ${r.in_survey ? "is-have" : ""}">
        <input type="checkbox" data-i="${i}" ${r.in_survey || !r.pdf_url ? "" : "checked"}>
        <div>
          <b>${esc(r.title)}</b>
          <div class="sv-pmeta">
            ${r.year ? `<span>${r.year}</span>` : ""}
            ${r.venue ? `<span>${esc(r.venue)}</span>` : ""}
            <span>${r.cites} trích dẫn</span>
            <span>${esc(r.source)}</span>
            ${r.pdf_url ? '<i class="chip ok">có PDF mở</i>' : '<i class="chip warn">chỉ abstract</i>'}
            ${r.in_survey ? '<i class="chip">đã có trong kho</i>' : ""}
          </div>
          <p class="small muted">${esc((r.abstract || "").slice(0, 260))}</p>
        </div>
      </label>`).join("")
      : '<p class="muted">Không tìm thấy bài nào.</p>');
  } catch (e) {
    $("#svFindList").innerHTML = `<p class="err">Lỗi: ${esc(e.message)}</p>`;
  }
}

async function svAddFound() {
  const items = $$("#svFindList input:checked").map((el) => SV.found[+el.dataset.i]);
  if (!items.length || !await svNeedId()) return;
  $("#svFindBox").classList.add("hidden");
  const es = svWatch();
  try {
    const d = await svFetch(`/api/survey/${SV.id}/find/add`, {
      method: "POST", headers: { "content-type": "application/json" },
      body: JSON.stringify({ items, enrich: $("#svFindEnrich").checked ? 1 : 0 }),
    });
    svProg(`nạp ${d.papers.length} bài · ${money(d.cost)}`);
    await svLoad();
  } catch (e) {
    svProg("Lỗi: " + e.message);
  } finally { es.close(); }
}

async function svPickDocs() {
  if (!await svNeedId()) return;
  $("#svDocBox").classList.remove("hidden");
  const d = await svFetch(`/api/survey/${SV.id}/loupe-docs`);
  $("#svDocList").innerHTML = d.docs.length ? d.docs.map((x) => `
    <label class="sv-found ${x.in_survey ? "is-have" : ""}">
      <input type="checkbox" value="${esc(x.id)}" ${x.in_survey ? "disabled" : ""}>
      <div><b>${esc(x.title)}</b>
        <div class="sv-pmeta"><span>${x.translated}/${x.translatable} đoạn đã dịch</span>
          ${x.in_survey ? '<i class="chip">đã có trong kho</i>' : ""}</div></div>
    </label>`).join("") : '<p class="muted">Chưa có bài nào trong màn đọc.</p>';
}

/* ------------------------------------------------------------- gắn sự kiện */

async function svOpen() {
  showScreen("survey");
  location.hash = "survey";
  await svLoadList();
  // Mở ra là thấy bản tổng hợp — thứ để đọc mà hiểu. Hỏi đáp đứng sau, vì hỏi
  // đáp chỉ có ích khi người ta đã biết phải hỏi gì.
  svLoadSynth().catch(() => {});
}

function svWire() {
  $("#goSurvey").onclick = svOpen;
  // Không còn nút "← Về trang chính" ở thanh trên: thanh công cụ bên trái đã là
  // đường quay lại, và hai lối làm cùng một việc chỉ tổ chiếm chỗ.

  /* Thanh công cụ bên trái. Gắn ở file này vì nó nạp sau app.js, nên tới đây cả
     `showScreen` lẫn `svOpen` đều đã có. */
  $$(".rail-item").forEach((b) => {
    b.onclick = () => {
      if (b.dataset.tool === "survey") { svOpen(); return; }
      svStopAsk();
      // Đang đọc dở một bài thì quay lại đúng chỗ đó, đừng đá về màn nhập —
      // mất chỗ đang đọc là cái giá quá đắt cho một cú bấm nhầm.
      if (state.doc && !$("#reader").classList.contains("hidden")) return;
      if (state.doc) { showScreen("reader"); location.hash = "doc=" + state.doc.id; return; }
      showScreen("start");
      location.hash = "";
      loadRecent();
    };
  });

  $("#svPick").onchange = async (e) => {
    if (e.target.value === "__new") {
      e.target.value = SV.id;                 // trả về kho cũ trước, phòng khi huỷ
      const name = prompt("Tên kho mới — mỗi kho là một chủ đề riêng:", "");
      if (name) await svNewSurvey(name);
      return;
    }
    SV.id = e.target.value;
    await svLoad();
  };

  // Menu ⋯ — chỗ để việc hiếm và việc phá huỷ, tách khỏi việc làm hằng ngày.
  const menu = $("#svMoreBox");
  $("#svMore").onclick = (e) => { e.stopPropagation(); menu.classList.toggle("hidden"); };
  document.addEventListener("click", () => menu.classList.add("hidden"));
  menu.onclick = (e) => e.stopPropagation();

  $("#svNew").onclick = async () => {
    menu.classList.add("hidden");
    const name = prompt("Tên kho mới — mỗi kho là một chủ đề riêng:", "");
    if (name) await svNewSurvey(name);
  };
  $("#svRename").onclick = async () => {
    menu.classList.add("hidden");
    if (!await svNeedId()) return;
    const name = prompt("Tên kho:", SV.survey?.name || "");
    if (!name) return;
    await svFetch(`/api/survey/${SV.id}`, {
      method: "PATCH", headers: { "content-type": "application/json" },
      body: JSON.stringify({ name }),
    });
    await svLoadList();
  };
  $("#svDrop").onclick = async () => {
    menu.classList.add("hidden");
    if (!SV.id) return;
    const n = SV.papers.length;
    if (!confirm(`Xoá kho "${SV.survey?.name || ""}"?\n\n`
      + `${n} bài, cùng toàn bộ đoạn, chỉ mục và lịch sử hỏi sẽ mất. Không hoàn lại được.`)) return;
    await svFetch(`/api/survey/${SV.id}`, { method: "DELETE" });
    SV.id = "";
    await svLoadList();
  };

  // Khối thêm bài gấp lại được — kho đã có bài thì nó chỉ chiếm chỗ.
  $("#svAddTog").onclick = () => $("#svAddBox").classList.toggle("is-shut");

  // nạp file
  $("#svFiles").onchange = (e) => { svUpload(e.target.files); e.target.value = ""; };
  const dz = $("#svDrop2");
  ["dragover", "dragenter"].forEach((ev) => dz.addEventListener(ev, (e) => {
    e.preventDefault(); dz.classList.add("is-over");
  }));
  ["dragleave", "drop"].forEach((ev) => dz.addEventListener(ev, (e) => {
    e.preventDefault(); dz.classList.remove("is-over");
  }));
  dz.addEventListener("drop", (e) => {
    if (e.dataTransfer?.files?.length) svUpload(e.dataTransfer.files);
  });

  $("#svUrlGo").onclick = async () => {
    const url = $("#svUrl").value.trim();
    if (!url || !await svNeedId()) return;
    svProg("đang tải…");
    try {
      await svFetch(`/api/survey/${SV.id}/papers/url`, {
        method: "POST", headers: { "content-type": "application/json" },
        body: JSON.stringify({ url, enrich: $("#svEnrich").checked ? 1 : 0 }),
      });
      $("#svUrl").value = "";
      svProg("xong");
      await svLoad();
    } catch (e) { svProg("Lỗi: " + e.message); }
  };

  $("#svFindGo").onclick = svFindPapers;
  $("#svFind").onkeydown = (e) => { if (e.key === "Enter") svFindPapers(); };
  $("#svFindClose").onclick = () => $("#svFindBox").classList.add("hidden");
  $("#svFindAdd").onclick = svAddFound;

  $("#svFromLoupe").onclick = svPickDocs;
  $("#svDocClose").onclick = () => $("#svDocBox").classList.add("hidden");
  $("#svDocAdd").onclick = async () => {
    const ids = $$("#svDocList input:checked").map((el) => el.value);
    if (!ids.length) return;
    $("#svDocBox").classList.add("hidden");
    svProg("đang kéo sang…");
    await svFetch(`/api/survey/${SV.id}/papers/import`, {
      method: "POST", headers: { "content-type": "application/json" },
      body: JSON.stringify({ doc_ids: ids }),
    });
    svProg("xong");
    await svLoad();
  };

  // thao tác trên từng bài
  $("#svPapers").onclick = async (e) => {
    const btn = e.target.closest("button[data-act]");
    if (!btn) return;
    const pid = btn.closest("[data-pid]").dataset.pid;
    const act = btn.dataset.act;
    if (act === "drop") {
      if (!confirm("Bỏ bài này khỏi kho?")) return;
      await svFetch(`/api/survey/${SV.id}/paper/${pid}`, { method: "DELETE" });
    } else if (act === "move") {
      svMoveBox(btn.closest("[data-pid]"), pid);
      return;                       // ô chọn tự lo phần còn lại
    } else if (act === "edit") {
      svEditBox(btn.closest("[data-pid]"), pid);
      return;
    } else {
      btn.disabled = true;
      svProg("đang chạy…");
      const es = svWatch();
      try {
        const d = await svFetch(`/api/survey/${SV.id}/paper/${pid}/${act}`, { method: "POST" });
        svProg(`xong · ${money(d.cost)}`);
      } catch (err) { svProg("Lỗi: " + err.message); } finally { es.close(); }
    }
    await svLoad();
  };

  // tab — nội dung của tab nào chỉ dựng khi mở tab đó
  const tabs = {
    svTabSyn: "svPaneSyn", svTabLec: "svPaneLec", svTabAsk: "svPaneAsk",
    svTabGrid: "svPaneGrid", svTabMap: "svPaneMap",
  };
  Object.entries(tabs).forEach(([tab, pane]) => {
    $("#" + tab).onclick = () => {
      Object.entries(tabs).forEach(([t, p]) => {
        $("#" + t).classList.toggle("is-on", t === tab);
        $("#" + p).classList.toggle("hidden", p !== pane);
      });
      if (pane === "svPaneSyn") svLoadSynth().catch(() => {});
      if (pane === "svPaneLec") svLoadLec().catch(() => {});
      if (pane === "svPaneGrid") svGrid().catch(() => {});
      if (pane === "svPaneMap") svGraph().catch(() => {});
    };
  });
  $("#svSynGo").onclick = svBuildSynth;

  /* Bỏ bản đã dựng. Dựng lại tốn tiền nên phải hỏi trước và nói rõ bao nhiêu —
     "bạn có chắc không" mà không kèm giá thì người dùng không có cơ sở để chắc. */
  $("#svSynDrop").onclick = async () => {
    if (!SV.id) return;
    if (!confirm("Bỏ bản tổng hợp này?\n\nDựng lại tốn khoảng $0,09.")) return;
    await svFetch(`/api/survey/${SV.id}/synthesis`, { method: "DELETE" });
    await svLoadSynth();
  };
  $("#svLecDrop").onclick = async () => {
    if (!SV.id || !SV.lecPid) return;
    if (!confirm("Bỏ bài giảng của bài này?\n\nDựng lại tốn khoảng $0,08.")) return;
    await svFetch(`/api/survey/${SV.id}/paper/${SV.lecPid}/lecture`, { method: "DELETE" });
    await svLoadLec();
  };
  $("#svFacets").onclick = svEditFacets;
  $("#svLecGo").onclick = svBuildLec;
  $("#svLecPick").onchange = (e) => {
    SV.lecPid = e.target.value;
    svLoadLec().catch(() => {});
  };

  // hỏi đáp
  $("#svAsk").onclick = svAsk;
  $("#svStop").onclick = () => {
    svStopAsk();
    $("#svCost").textContent = "đã dừng theo yêu cầu — phần đã tìm được vẫn giữ trong nhật ký";
  };
  $("#svQ").onkeydown = (e) => {
    if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) svAsk();
  };
  const patch = (body) => SV.id && svFetch(`/api/survey/${SV.id}`, {
    method: "PATCH", headers: { "content-type": "application/json" },
    body: JSON.stringify(body),
  }).catch(() => {});

  $("#svBudget").onchange = () => patch({ budget_usd: Number($("#svBudget").value) || 0 });
  // Model lưu theo KHO, không theo lượt hỏi: mỗi kho là một chủ đề, và chủ đề
  // khác nhau đáng được chạy bằng model khác nhau.
  const setStrong = async (e) => {
    await patch({ model: e.target.value });
    await svLoad();          // đồng bộ ô kia và dòng "model đang chạy"
  };
  $("#svModel").onchange = setStrong;
  $("#svSynModel").onchange = setStrong;
  $("#svLecModel").onchange = setStrong;
  $("#svFastModel").onchange = (e) => patch({ fast_model: e.target.value });

  $("#svSearchGo").onclick = svSearch;
  $("#svSearch").onkeydown = (e) => { if (e.key === "Enter") svSearch(); };

  // Trích dẫn bấm được, ở bất cứ đâu trong màn — nhật ký, câu trả lời, đồ thị,
  // kết quả tìm. Một listener trên cả màn thay vì gắn lại sau mỗi lần vẽ.
  $("#survey").addEventListener("click", (e) => {
    const a = e.target.closest("[data-cite]");
    if (a) { e.preventDefault(); svOpenCite(a.dataset.cite); return; }
    const ent = e.target.closest("[data-ent]");
    if (ent) {
      $("#svSearch").value = ent.dataset.ent;
      svSearch();
    }
  });

  // xem lại một lượt đã hỏi
  $("#svHistory").onclick = async (e) => {
    const b = e.target.closest("[data-run]");
    if (!b) return;
    const r = await svFetch(`/api/survey/${SV.id}/run/${b.dataset.run}`);
    SV.answer = r.answer;
    $("#svAnswer").innerHTML = svMd(r.answer);
    $("#svQ").value = r.question;
    $("#svSteps").innerHTML = "";
    $("#svCost").textContent = `xem lại · ${money(r.cost)} · ${r.steps.length} vòng`;
    $("#svWarns").innerHTML = (r.warns || []).length
      ? `<div class="sv-warnbox"><b>${r.warns.length} chỗ cần soát lại</b><ul>`
        + r.warns.map((x) => `<li><i>${esc(x.kind)}</i> ${esc(x.msg)}</li>`).join("") + "</ul></div>"
      : "";
  };
}

/* ------------------------------------------------------------ bài giảng

   Một bài, viết ra cho ĐỌC HIỂU ĐƯỢC. Khác hai tab kia ở chỗ nó không giả định
   người đọc đã biết gì: tab Tổng hợp nói về cả kho mà không đi sâu bài nào, tab
   Hỏi đáp đi sâu được nhưng đòi người ta biết trước phải hỏi gì.

   Ba thứ hiện ra TRƯỚC khi tiêu tiền, cố ý: hồ sơ đối chiếu (miễn phí, cho biết
   phần so sánh sẽ dày hay mỏng), model đang chạy, và giá ước tính trên nút. */

/* In đậm chỉ có tác dụng khi nó NGẮN — model hay viết `do`/`point` thành cả một
   đoạn, và in đậm nguyên đoạn thì mắt không còn chỗ bám. Cùng ngưỡng với
   `lecture.LEAD_MAX` bên server; đổi một bên phải đổi bên kia, không thì bản xem
   trong app và file Markdown xuất ra trông khác nhau. */
const SV_LEAD_MAX = 90;

function svLead(t) {
  t = String(t || "").trim();
  return t && t.length <= SV_LEAD_MAX ? `<b>${esc(t)}</b>` : svMd(t);
}

function svLecFillPick() {
  const el = $("#svLecPick");
  if (!el) return;
  const ok = (SV.papers || []).filter((p) => p.status === "carded" || p.status === "indexed");
  el.innerHTML = ok.length
    ? ok.map((p) => `<option value="${esc(p.id)}"${p.id === SV.lecPid ? " selected" : ""}>`
        + esc(svClip(p.title || "(không tiêu đề)").slice(0, 70)) + "</option>").join("")
    : '<option value="">— chưa bài nào có nội dung —</option>';
  if (!SV.lecPid && ok.length) SV.lecPid = ok[0].id;
  if (SV.lecPid) el.value = SV.lecPid;
}

async function svLoadLec() {
  if (!await svNeedId()) return;
  svLecFillPick();
  const pid = SV.lecPid;
  const go = $("#svLecGo");
  if (!pid) {
    $("#svLec").innerHTML = "";
    $("#svLecProg").textContent = "Kho chưa có bài nào đã bóc nội dung.";
    go.disabled = true;
    return;
  }
  go.disabled = false;
  go.title = `Chạy bằng ${(SV.models || {}).strong || "?"}`;

  const d = await svFetch(`/api/survey/${SV.id}/paper/${pid}/lecture`);
  svRenderLec(d.lecture, d.stale);
  svLecWarns(d.lecture?.warns);
  $("#svLecMd").href = `/api/survey/${SV.id}/paper/${pid}/lecture?fmt=md`;
  $("#svLecMd").classList.toggle("hidden", !d.lecture?.sections);
  $("#svLecDrop").classList.toggle("hidden", !d.lecture?.sections);
  go.textContent = d.lecture?.sections ? "Dựng lại · ~$0,08" : "Dựng · ~$0,08";
  $("#svLecProg").textContent = d.has_text
    ? "" : "Bài này chưa có nội dung đã bóc — nạp lại PDF trước.";
  svLecRefs(pid).catch(() => {});
}

/* Hồ sơ đối chiếu: nạp riêng và KHÔNG chặn phần còn lại, vì nó đi ra mạng ngoài
   (Semantic Scholar) nên có thể chậm hoặc hỏng — mà bài giảng vẫn dựng được nếu
   thiếu nó. Miễn phí, nên nạp luôn chứ không đợi người dùng bấm. */
async function svLecRefs(pid) {
  const box = $("#svLecRefBox");
  $("#svLecRefN").textContent = "đang tra…";
  box.classList.remove("hidden");
  let d;
  try {
    d = await svFetch(`/api/survey/${SV.id}/paper/${pid}/lecture/refs`);
  } catch (_) {
    $("#svLecRefN").textContent = "không tra được";
    return;
  }
  const rs = d.refs || [];
  $("#svLecRefN").textContent = rs.length
    ? `${rs.length} bài được dẫn (trong ${d.n_refs} tham khảo) · miễn phí`
    : (d.why || "không khớp được bài này trên Semantic Scholar");
  $("#svLecRefs").innerHTML = rs.map((r) => `
    <div class="sv-ref">
      <b>${r.influential ? "★ " : ""}${esc(r.title)}</b>
      ${r.year ? `<span class="muted"> (${r.year})</span>` : ""}
      ${r.paper_id ? '<i class="chip ok">có trong kho</i>' : ""}
      ${r.gist ? `<p class="small muted">${esc(svClip(r.gist).slice(0, 260))}</p>` : ""}
      ${(r.why || []).map((w) =>
        `<p class="small sv-why">chỗ dẫn: “${esc(svClip(w).slice(0, 300))}”</p>`).join("")}
    </div>`).join("");
}

/* Gom cảnh báo TRÙNG LOẠI TRONG CÙNG MỘT MỤC lại thành một dòng gập được.

   Ba mươi hai dòng "số X không tìm thấy trong bài", khác nhau đúng con số, đẩy
   mọi cảnh báo khác ra khỏi tầm mắt — và một danh sách dài như thế thì người
   dùng thôi đọc, lúc đó cảnh báo THẬT cũng trôi theo. Đã thấy đúng vậy trên
   một bài thật. Nguyên nhân gốc đã sửa ở `lecture.CLAIM_SECTIONS`; gom ở đây là
   để lần sau có kêu nhiều thì cũng không nuốt mất phần còn lại. */
const SV_WARN_GROUP = 3;   // từ ngần này trở lên thì gập lại

function svLecWarns(w) {
  const box = $("#svLecWarns");
  if (!(w || []).length) { box.innerHTML = ""; return; }

  const groups = new Map();
  w.forEach((x) => {
    const k = `${x.section}|${x.kind}`;
    if (!groups.has(k)) groups.set(k, []);
    groups.get(k).push(x);
  });

  const one = (x) => `<i>${esc(x.kind)}</i> ${esc(x.msg)}`
    + (x.text ? `<br><span class="muted">“${esc(x.text)}”</span>` : "");

  const rows = [...groups.values()].map((g) => {
    if (g.length < SV_WARN_GROUP) return g.map((x) => `<li>${one(x)}</li>`).join("");
    const tieu = SV_LEC_TITLE[g[0].section] || g[0].section;
    return `<li><details><summary><i>${esc(g[0].kind)}</i> ${tieu} — `
      + `${g.length} chỗ</summary><ul>`
      + g.map((x) => `<li>${esc(x.msg)}</li>`).join("")
      + "</ul></details></li>";
  }).join("");

  box.innerHTML = `<div class="sv-warnbox"><b>${w.length} chỗ cần soát lại</b>`
    + `<ul>${rows}</ul></div>`;
}

/* Mỗi mục có hình dạng riêng nên phải dựng riêng — đổ chung một khuôn thì
   `mechanism` (thứ đáng đọc nhất) tụt xuống thành một danh sách phẳng, mà chính
   cặp "làm gì / vì sao cần" mới là chỗ người đọc hiểu ra cơ chế. */
const SV_LEC_ORDER = ["prereq", "problem", "why_hard", "mechanism",
                      "compare", "evidence", "limits", "check"];
const SV_LEC_TITLE = {
  prereq: "Cần biết trước", problem: "Bài toán",
  why_hard: "Vì sao cách hiển nhiên không xong",
  mechanism: "Cơ chế, chạy tay một ví dụ",
  compare: "Đặt cạnh những bài nó dẫn",
  evidence: "Số liệu nói gì, và không nói gì",
  limits: "Chỗ đáng ngờ", check: "Tự kiểm tra",
};

function svRenderLec(lec, stale) {
  const el = $("#svLec");
  if (!lec || !lec.sections) {
    el.innerHTML = '<p class="muted">Chưa dựng. Chọn bài rồi bấm <b>Dựng</b> — '
      + 'phần đối chiếu với các bài được dẫn lấy miễn phí từ Semantic Scholar, '
      + 'chỉ phần viết là tốn tiền.</p>';
    return;
  }
  const s = lec.sections;
  const out = [];
  if (stale) {
    out.push('<div class="sv-stale">Nội dung bài đã đổi sau khi dựng bài giảng này '
      + '— dựng lại để khớp.</div>');
  }
  const head = (k) => `<h3>${esc(SV_LEC_TITLE[k] || k)}</h3>`;

  for (const k of SV_LEC_ORDER) {
    const d = s[k];
    if (!d) continue;
    out.push(`<section class="sv-lecsec" data-sec="${k}">`, head(k));
    if (k === "prereq") {
      out.push("<dl class='sv-prereq'>" + (d.items || []).map((i) =>
        `<dt>${esc(i.term || "")}</dt><dd>${svMd(i.why || "")}</dd>`).join("") + "</dl>");
    } else if (k === "mechanism") {
      if (d.input) out.push(`<p class="sv-lecin"><b>Đầu vào lấy làm ví dụ:</b> ${svMd(d.input)}</p>`);
      out.push("<ol class='sv-steps-l'>" + (d.steps || []).map((st) =>
        `<li><div class="sv-do">${svLead(st.do)}</div>`
        + (st.why ? `<div class="sv-why"><b>Vì sao cần:</b> ${svMd(st.why)}</div>` : "")
        + (st.note ? `<div class="sv-note">${svMd(st.note)}</div>` : "")
        + "</li>").join("") + "</ol>");
    } else if (k === "compare") {
      out.push((d.items || []).map((i) =>
        `<div class="sv-cmp"><b>${esc(i.paper || "")}</b>`
        + `<p><span class="sv-tag">lấy</span> ${svMd(i.took || "")}</p>`
        + `<p><span class="sv-tag alt">khác</span> ${svMd(i.differs || "")}</p></div>`).join(""));
      if (d.placement) out.push(`<p class="sv-place">${svMd(d.placement)}</p>`);
    } else if (k === "evidence") {
      out.push("<ul class='sv-ev'>" + (d.items || []).map((i) =>
        `<li><b>${esc(i.number || "")}</b> — ${svMd(i.claim || "")}`
        + (i.setting ? ` <span class="muted">(${esc(i.setting)})</span>` : "")
        + "</li>").join("") + "</ul>");
      if (d.limits_of_evidence) {
        out.push(`<p class="sv-place"><b>Các số này không chứng minh:</b> `
          + `${svMd(d.limits_of_evidence)}</p>`);
      }
    } else if (k === "limits") {
      out.push("<ul class='sv-ev'>" + (d.items || []).map((i) =>
        `<li>${svLead(i.point)} — ${svMd(i.so_what || "")}</li>`).join("") + "</ul>");
    } else if (k === "check") {
      // Đáp án gập lại: hiện sẵn thì mắt đọc luôn và câu hỏi mất tác dụng — cái
      // giúp người ta nhớ là lúc TỰ dựng lại lời giải thích, không phải lúc đọc.
      out.push("<ol class='sv-quiz'>" + (d.items || []).map((i) =>
        `<li><div>${svMd(i.q || "")}</div>`
        + `<details><summary>đáp án</summary><div>${svMd(i.a || "")}</div></details></li>`
        ).join("") + "</ol>");
    } else {
      out.push(svMd(d.body || ""));
    }
    out.push("</section>");
  }
  out.push(`<p class="sv-cost">Dựng bằng ${esc(lec.model || "?")} · ${money(lec.cost)}`
    + (lec.n_refs_total ? ` · đối chiếu từ ${lec.n_refs_total} tham khảo (miễn phí)` : "")
    + "</p>");
  el.innerHTML = out.join("");
}

function svBuildLec() {
  if (!SV.id || !SV.lecPid) return;
  const pid = SV.lecPid;
  const btn = $("#svLecGo");
  btn.disabled = true;
  $("#svLecWarns").innerHTML = "";
  $("#svLecProg").textContent = "đang bắt đầu…";
  // Dựng dần: mục nào xong hiện ngay, nên mất kết nối giữa chừng vẫn đọc được
  // phần đã có — cùng lối với dựng slide theo mẻ.
  const acc = { sections: {}, warns: [], model: (SV.models || {}).strong, cost: 0 };

  const es = new EventSource(
    `/api/survey/${SV.id}/paper/${pid}/lecture/build`);
  es.addEventListener("stage", (e) => {
    const d = JSON.parse(e.data);
    $("#svLecProg").textContent = `${d.msg}… (${svShort((SV.models || {}).strong)})`;
  });
  es.addEventListener("section", (e) => {
    const d = JSON.parse(e.data);
    acc.sections[d.name] = d.data;
    acc.cost = d.cost;
    svRenderLec(acc, false);
    $("#svLecProg").textContent =
      `${SV_LEC_TITLE[d.name] || d.name}${d.redone ? " (viết lại cho sâu hơn)" : ""} · ${money(d.cost)}`;
  });
  es.addEventListener("done", (e) => {
    const d = JSON.parse(e.data);
    svRenderLec(d.lecture, false);
    svLecWarns(d.lecture.warns);
    $("#svLecProg").textContent = `xong · ${money(d.cost)} · ${d.secs}s`;
    $("#svLecMd").classList.remove("hidden");
    $("#svLecDrop").classList.remove("hidden");
    btn.textContent = "Dựng lại · ~$0,08";
    btn.disabled = false;
    es.close();
  });
  es.addEventListener("error", (e) => {
    let msg = "mất kết nối";
    try { msg = JSON.parse(e.data).msg; } catch (_) { /* lỗi mạng, không có body */ }
    $("#svLecProg").textContent = "Lỗi: " + msg;
    btn.disabled = false;
    es.close();
  });
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", svWire);
} else {
  svWire();
}

// Mở thẳng bằng #survey trên thanh địa chỉ, để bookmark được.
if (location.hash === "#survey") {
  showScreen("survey");
  svLoadList().catch(() => {});
}
