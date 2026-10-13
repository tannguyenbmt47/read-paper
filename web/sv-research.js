/* Sổ nghiên cứu: tra cứu miễn phí; chỉ gọi model khi người dùng yêu cầu trả lời. */
const svResearch = { data: null, question: "", suggestions: [], editingNote: "", request: 0, searchRequest: 0, searched: false, query: "", answerRequest: 0, answerBusy: false, drafts: {}, saving: {}, section: "rsQuestions" };

const RS_SECTIONS = {
  rsQuestions: ["01", "Bắt đầu bằng câu hỏi nghiên cứu"],
  rsExperiments: ["02", "Đối chiếu kết quả và điều kiện đo"],
  rsGaps: ["03", "Xem giới hạn đã được ghi nhận"],
  rsRelations: ["04", "Kiểm tra điểm chung giữa các bài"],
  rsClaims: ["05", "Theo dõi nhận định cần xác minh"],
  rsNotes: ["06", "Lưu lại quá trình nghiên cứu"],
  rsRepro: ["07", "Rà thông tin cần thiết để tái lập"],
};

function svRsOpen(section, scroll = true) {
  if (!RS_SECTIONS[section]) return;
  svResearch.section = section;
  Object.keys(RS_SECTIONS).forEach((id) => { $("#" + id).hidden = id !== section; });
  $("#svPaneResearch").querySelectorAll(".rs-nav [data-rs-to]").forEach((button) => {
    const on = button.dataset.rsTo === section;
    button.classList.toggle("is-on", on);
    if (on) button.setAttribute("aria-current", "page");
    else button.removeAttribute("aria-current");
  });
  $("#rsContextIndex").textContent = `MỤC ${RS_SECTIONS[section][0]} / 07`;
  $("#rsContextHint").textContent = RS_SECTIONS[section][1];
  if (scroll) {
    const pane = $("#svPaneResearch");
    const context = $("#rsContextIndex");
    const top = context.getBoundingClientRect().top - pane.getBoundingClientRect().top + pane.scrollTop - 12;
    pane.scrollTo({ top: Math.max(0, top), behavior: matchMedia("(prefers-reduced-motion: reduce)").matches ? "instant" : "smooth" });
  }
}

function svRsCite(id, page) {
  return id ? `<button type="button" class="sv-cite rs-cite-link" data-cite="${esc(id)}">[Nguồn${page != null ? " · trang " + (Number(page) + 1) : ""}]</button>`
    : '<span class="muted">Chưa gắn đoạn nguồn</span>';
}

function svRsPaper(id) {
  return esc(svResearch.data?.papers.find((p) => p.id === id)?.title || "Bài không còn trong kho");
}

/* Dựng ký hiệu trong đoạn nguồn; không tạo liên kết hình của bài đang đọc. */
function svRsExcerpt(text) {
  let html = esc(text).replace(/\\\((.+?)\\\)/gs, (_, math) => mathTeX(math));
  for (let i = 0; i < 4; i++) {
    const next = html.replace(/\^\{([^{}]*)\}/g, "<sup>$1</sup>")
      .replace(/_\{([^{}]*)\}/g, "<sub>$1</sub>");
    if (next === html) break;
    html = next;
  }
  return html;
}

function svRsEmpty(text) {
  return `<p class="small muted rs-empty">${esc(text)}</p>`;
}

function svRsStatus(msg, error = false) {
  const el = $("#rsStatus");
  el.textContent = msg;
  el.classList.toggle("err", error);
}

/* Đổi kho phải xoá ngay nội dung đang xem. Nếu đợi API trả về, người đọc có
   thể bấm nguồn hoặc sửa ghi chú của kho cũ trong lúc mã kho đã đổi. */
function svResearchReset() {
  ++svResearch.request;
  ++svResearch.searchRequest;
  ++svResearch.answerRequest;
  svResearch.answerBusy = false;
  svResearch.data = null;
  svResearch.question = "";
  svResearch.suggestions = [];
  svResearch.searched = false;
  svResearch.editingNote = "";
  svResearch.query = "";
  svResearch.drafts = {};
  ["rsAnswerBody", "rsStarters", "rsProgress", "rsEvidence", "rsSuggestions", "rsExperimentBody", "rsGapBody", "rsRelationBody",
    "rsClaimBody", "rsNoteList", "rsReproBody"].forEach((id) => { $("#" + id).innerHTML = ""; });
  ["rsCountPapers", "rsCountQuestions", "rsCountEvidence", "rsCountPending",
    "rsNavQuestions", "rsNavExperiments", "rsNavGaps", "rsNavRelations",
    "rsNavClaims", "rsNavNotes", "rsNavRepro"].forEach((id) => { $("#" + id).textContent = "—"; });
  $("#rsQuestionPick").innerHTML = "";
  $("#rsSuggest").disabled = true;
  ["rsQuestionInput", "rsSuggestQuery", "rsClaimInput", "rsClaimCite",
    "rsNoteTitle", "rsNoteBody", "rsExperimentFilter"].forEach((id) => { $("#" + id).value = ""; });
  $("#rsExperimentStatus").value = "all";
  $("#rsExperimentCount").textContent = "";
  $("#rsNoteKind").value = "note";
  $("#rsAddNote").textContent = "Lưu vào dự án";
  svRsStatus("Đang tải thông tin của kho vừa chọn…");
}

async function svResearchLoad() {
  const sid = SV.id;
  if (!sid) return;
  const req = ++svResearch.request;
  const data = await svFetch(`/api/survey/${sid}/research`);
  if (SV.id !== sid || req !== svResearch.request) return;
  svResearch.data = data;
  if (!data.items.some((it) => it.kind === "question" && it.id === svResearch.question)) {
    ++svResearch.searchRequest;
    ++svResearch.answerRequest;
    svResearch.answerBusy = false;
    $("#rsAnswerBody").innerHTML = "";
    svResearch.question = data.items.find((it) => it.kind === "question")?.id || "";
    svResearch.suggestions = [];
    svResearch.searched = false;
  }
  svResearchRender();
  svRsOpen(svResearch.section, false);
  svRsStatus(`${data.papers.length} bài · ${data.items.length} ghi chép · các bảng được cập nhật từ kho · $0`);
  if (svResearch.question && !svResearch.searched) await svRsSearch();
  await svRsLoadAnswer();
}

async function svRsWrite(body, id = "") {
  const sid = SV.id;
  await svFetch(`/api/survey/${sid}/research/items${id ? "/" + id : ""}`, {
    method: id ? "PATCH" : "POST", headers: { "content-type": "application/json" },
    body: JSON.stringify(body),
  });
  if (sid === SV.id) await svResearchLoad();
}

function svResearchRender() {
  const d = svResearch.data;
  if (!d) return;
  const qs = d.items.filter((it) => it.kind === "question");
  $("#rsStarters").innerHTML = (d.question_starters || []).length ?
    '<p class="small muted">Gợi ý từ tài liệu trong kho · chọn để lưu câu hỏi và tự tìm nguồn</p>' +
    d.question_starters.map((q, i) => `<button class="btn xs" data-rs-starter="${i}">${esc(q.title)}</button>`).join("") : "";
  const notes = d.items.filter((it) => it.kind === "note" || it.kind === "issue");
  const pending = d.claims.filter((c) => c.status !== "reviewed" || c.check !== "text_check_needed").length;
  [
    ["#rsCountPapers", d.papers.length], ["#rsCountQuestions", qs.length],
    ["#rsCountEvidence", d.evidence.length], ["#rsCountPending", pending],
    ["#rsNavQuestions", qs.length], ["#rsNavExperiments", d.experiments.length],
    ["#rsNavGaps", d.gaps.length], ["#rsNavRelations", d.relations.length],
    ["#rsNavClaims", d.claims.length], ["#rsNavNotes", notes.length],
    ["#rsNavRepro", d.reproducibility.length],
  ].forEach(([id, count]) => { $(id).textContent = count; });
  $("#rsQuestionPick").innerHTML = qs.length ? qs.map((it) =>
    `<option value="${esc(it.id)}">${esc(it.title)}</option>`).join("") :
    '<option value="">Hãy thêm một câu hỏi nghiên cứu</option>';
  $("#rsQuestionPick").value = svResearch.question;
  $("#rsSuggest").disabled = !svResearch.question;
  $("#rsAnswerRun").disabled = !svResearch.question || svResearch.answerBusy;
  const evidences = d.evidence.filter((it) => it.question_id === svResearch.question);
  const covered = new Set(evidences.filter((it) => it.chunk_id).map((it) => it.paper_id)).size;
  const withoutReason = evidences.filter((it) => !it.body?.trim()).length;
  $("#rsProgress").textContent = svResearch.question ?
    `${covered}/${d.papers.length} bài đã có nguồn được lưu · ${withoutReason} đoạn chưa ghi lý do. Số nguồn không thể hiện mức chắc chắn của kết luận.` : "";
  const unlinked = d.evidence.filter((it) => !it.question_id);
  const buckets = [
    ["support", "Bằng chứng ủng hộ"], ["oppose", "Bằng chứng phản bác"],
    ["unclear", "Chưa đủ rõ"],
  ];
  $("#rsEvidence").innerHTML = qs.length ?
    `<div class="rs-qheading"><div><b>Bằng chứng đã phân loại · ${evidences.length}</b>
      <p class="small muted rs-evidence-question">${esc(qs.find((it) => it.id === svResearch.question)?.title || "")}</p></div>
      <button class="sv-link" data-rs-delete="${esc(svResearch.question)}">Xoá câu hỏi</button></div>`
    + `<div class="rs-matrix">${buckets.map(([key, label]) => {
      const items = evidences.filter((it) => it.stance === key);
      return `<div class="rs-mcol" data-rs-lane="${key}"><h4>${label} <span class="rs-badge">${items.length}</span></h4>`
        + (items.length ? items.map((it) => `<article class="rs-hit rs-evidence-card" data-rs-evidence="${esc(it.id)}">
            <div class="rs-hit-head">${esc(it.source.title || "Nguồn không còn tồn tại")}</div>
            <p class="rs-snippet">${svRsExcerpt(it.title || it.source.text)}</p>
            ${it.body ? `<p class="rs-reason">${esc(it.body)}</p>` : ""}
            <div class="rs-row rs-evidence-actions">${svRsCite(it.chunk_id, it.source.page)}
              <select class="input rs-stance-select" data-rs-reclass="${esc(it.id)}" aria-label="Đổi phân loại bằng chứng">
                ${buckets.map(([stance, name]) => `<option value="${stance}" ${it.stance === stance ? "selected" : ""}>${name}</option>`).join("")}
              </select>
              <button class="sv-link" data-rs-delete="${esc(it.id)}">Bỏ</button></div>
            <details class="rs-reason-editor"><summary>${it.body ? "Sửa lý do đánh giá" : "Ghi lý do đánh giá"}</summary>
              <label for="rs-reason-${esc(it.id)}" class="small muted">Nhận xét của bạn về mức độ liên quan, điều kiện hoặc giới hạn của đoạn này</label>
              <textarea id="rs-reason-${esc(it.id)}" class="input" data-rs-reason="${esc(it.id)}" maxlength="6000" rows="3" placeholder="Ví dụ: Kết quả chỉ áp dụng trên tập kiểm tra này, chưa đủ để kết luận tổng quát.">${esc(svResearch.drafts[it.id] ?? it.body ?? "")}</textarea>
              <small class="muted">Tự lưu khi rời ô nhận xét.</small>
              <button class="btn xs" data-rs-save-reason="${esc(it.id)}">Lưu nhận xét</button>
            </details>
          </article>`).join("") : svRsEmpty("Chưa có đoạn được phân loại")) + '</div>';
    }).join("")}</div>` : svRsEmpty("Thêm câu hỏi để xây dựng ma trận bằng chứng.");
  if (unlinked.length) {
    $("#rsEvidence").innerHTML += `<details class="rs-paper"><summary>Bằng chứng chưa gắn câu hỏi · ${unlinked.length}</summary>
      <div class="rs-unlinked">${unlinked.map((it) => `<article class="rs-entry">
        <b>${esc(it.title)}</b><p class="small muted">${esc(it.source.title || "Nguồn đã bị xoá")}</p>
        ${svRsCite(it.chunk_id, it.source.page)}<div class="rs-row">
        ${svResearch.question ? `<button class="btn xs" data-rs-link="${esc(it.id)}">Gắn vào câu hỏi đang xem</button>` : ""}
        <button class="sv-link" data-rs-delete="${esc(it.id)}">Xoá phân loại</button></div>
      </article>`).join("")}</div></details>`;
  }

  $("#rsSuggestions").innerHTML = svResearch.suggestions.length ?
    `<h4>Đoạn nguồn để đối chiếu · ${svResearch.suggestions.length}</h4>`
    + `<p class="small muted">Từ khóa đang dùng: ${esc(svResearch.query)}</p>`
    + `<p class="small muted rs-hint">Mở đoạn gốc, kiểm tra ngữ cảnh rồi chọn phân loại. Có thể lưu nhiều đoạn từ cùng một bài.</p>`
    + svResearch.suggestions.map((hit) => {
      const classified = evidences.find((it) => it.chunk_id === hit.chunk_id);
      return `<article class="rs-hit">
      <div class="rs-hit-head">${esc(hit.title)} <span class="rs-page-label">${hit.page != null ? `· trang ${Number(hit.page) + 1}` : ""}</span></div>
      <p class="rs-snippet">${svRsExcerpt(hit.text)}</p>
      ${hit.matches?.length ? `<p class="small muted">Dấu hiệu trong đoạn: ${esc(hit.matches.join(" · "))}. Cần đọc để xác định có trả lời câu hỏi.</p>` : ""}
      <div class="rs-row">${svRsCite(hit.chunk_id, hit.page)}
        ${[["support", "Ủng hộ"], ["oppose", "Phản bác"], ["unclear", "Chưa rõ"]].map(([key, label]) =>
          `<button class="btn xs ${classified?.stance === key ? "rs-stance-active" : ""}" data-rs-stance="${key}" data-rs-chunk="${esc(hit.chunk_id)}" ${classified?.stance === key ? "disabled" : ""}>${classified?.stance === key ? "✓ " : ""}${label}</button>`).join("")}
        <button class="sv-link" data-rs-source="${esc(hit.chunk_id)}">Dùng làm nguồn kiểm chứng</button>
      </div></article>`;
    }).join("") : (svResearch.searched ? svRsEmpty("Không tìm thấy đoạn khớp từ khoá. Hãy thử tên phương pháp, dữ liệu, độ đo hoặc cụm từ tiếng Anh trong bài gốc. Chưa có kết luận nào được rút ra.") : "");

  svRsRenderExperiments();

  const gaps = d.gaps;
  $("#rsGapBody").innerHTML = gaps.length ? gaps.slice(0, 100).map((g) =>
    `<article class="rs-entry rs-source-card"><span class="rs-badge">${esc(g.label)}</span>
      <b>${esc(g.title)}</b><p>${esc(g.text)}</p>
      <small class="muted">${esc(g.evidence)} · ${esc(g.scope)}</small>
      <div class="rs-row">${svRsCite(g.chunk_id, g.page)}</div></article>`).join("") :
    svRsEmpty("Chưa có trường giới hạn hoặc khoảng trống trong phiếu đã bóc.");

  const rels = d.relations;
  $("#rsRelationBody").innerHTML = rels.length ? rels.slice(0, 120).map((r) =>
    `<article class="rs-entry rs-source-card"><b>${svRsPaper(r.src)} ↔ ${svRsPaper(r.dst)}</b>
      <p>${esc(r.rel)}: <strong>${esc(r.detail)}</strong></p>
      <div class="rs-row">${r.sources.map((s) => svRsCite(s, null)).join(" · ")}</div>
      <small class="muted">${esc(r.scope)}</small></article>`).join("") :
    svRsEmpty("Chưa tìm được tập dữ liệu chung có dẫn chứng trong cả hai bài. Có thể kiểm tra phiếu và toàn văn.");

  const claims = d.claims;
  $("#rsClaimBody").innerHTML = claims.length ? claims.map((c) => {
    const label = c.check === "no_source" ? "Chưa có nguồn" :
      c.check === "number_missing" ? "Số chưa khớp nguồn" : "Cần kiểm tra ý nghĩa";
    return `<article class="rs-entry"><div class="rs-row"><b>${esc(c.title)}</b><span class="rs-badge">${esc(label)}</span></div>
      <p class="small muted">${esc(c.note)}</p><p>${svRsCite(c.chunk_id, c.source_page)}
      ${esc(c.source_title)}</p><div class="rs-row">
      ${c.check === "text_check_needed" ? `<button class="btn xs" data-rs-reviewed="${esc(c.id)}" ${c.status === "reviewed" ? "disabled" : ""}>${c.status === "reviewed" ? "Đã đọc và đánh dấu" : "Đánh dấu tôi đã kiểm tra"}</button>` : ""}
      <button class="sv-link" data-rs-delete="${esc(c.id)}">Xoá</button></div></article>`;
  }).join("") : svRsEmpty("Chưa lưu nhận định. Nhập một nhận định cùng mã đoạn để kiểm tra.");

  $("#rsNoteList").innerHTML = notes.length ? notes.map((n) =>
    `<article class="rs-entry"><div class="rs-row"><span class="rs-badge">${n.kind === "issue" ? "Vấn đề" : "Ghi chú"}</span>
      <b>${esc(n.title)}</b><span class="grow"></span><span class="small muted">${n.status === "resolved" ? "Đã giải quyết" : "Đang theo dõi"}</span></div>
      <p>${esc(n.body)}</p><div class="rs-row"><button class="sv-link" data-rs-edit="${esc(n.id)}">Sửa</button>
        ${n.kind === "issue" ? `<button class="sv-link" data-rs-resolve="${esc(n.id)}">${n.status === "resolved" ? "Mở lại" : "Đã giải quyết"}</button>` : ""}
        <button class="sv-link" data-rs-delete="${esc(n.id)}">Xoá</button></div></article>`).join("") :
    svRsEmpty("Chưa có ghi chú hoặc vấn đề cần giải quyết.");

  const repro = d.reproducibility;
  $("#rsReproBody").innerHTML = repro.length ? repro.map((p) => {
    const found = p.checks.filter((c) => c.status === "signal_found").length;
    const total = p.checks.length;
    const percent = total ? Math.round(found / total * 100) : 0;
    return `<details class="rs-paper"><summary><span class="rs-paper-title"><b>${esc(p.title)}</b>
      <small class="muted">${found}/${total} hạng mục có dấu hiệu trong tài liệu</small></span>
      <span class="rs-audit-meter" role="img" aria-label="${found} trên ${total} hạng mục có dấu hiệu"><span style="width:${percent}%"></span></span></summary>
      <div class="rs-audit">${p.checks.map((check) => `<div class="rs-auditrow">
        <span>${esc(check.label)}</span><span>${check.status === "signal_found" ? '<span class="rs-ok">Có dấu hiệu</span>' :
          check.status === "card_only" ? '<span class="rs-warn">Chỉ có trong phiếu</span>' :
          check.status === "not_checked" ? 'Chưa kiểm tra' : 'Chưa tìm thấy'}</span>
        <span class="small muted rs-check-detail"><span class="rs-check-note">${esc(check.note)}</span>${svRsCite(check.chunk_id, null)}
        ${check.value ? `<span class="rs-url">${esc(check.value)}</span>` : ""}</span></div>`).join("")}</div></details>`;
  }).join("") : svRsEmpty("Chưa có bài nào để kiểm tra mức đầy đủ phục vụ tái lập.");
}

function svRsRenderExperiments() {
  const results = svResearch.data?.experiments || [];
  const query = $("#rsExperimentFilter").value.trim().toLocaleLowerCase();
  const status = $("#rsExperimentStatus").value;
  const filtered = results.filter((r) => {
    if (status === "matched" && !r.number_in_source) return false;
    if (status === "unmatched" && r.number_in_source) return false;
    return !query || [r.title, r.claim, r.dataset, r.metric, r.baseline, r.value]
      .some((v) => String(v || "").toLocaleLowerCase().includes(query));
  });
  $("#rsExperimentCount").textContent = `${filtered.length}/${results.length} kết quả`;
  const tr = filtered.slice(0, 150).map((r) => `<tr>
    <td><strong>${esc(r.title)}</strong><p class="small muted">${esc(r.claim)}</p>${svRsCite(r.chunk_id, r.page)}</td>
    <td>${esc(r.dataset) || "—"}</td><td>${esc(r.metric) || "—"}</td>
    <td>${esc(r.baseline) || "—"}</td><td><b>${esc(r.value) || "—"}</b></td>
    <td>${r.number_in_source ? '<span class="rs-ok">Số có trong nguồn</span>' : '<span class="rs-warn">Cần đối chiếu số</span>'}
      <small>${esc(r.comparison_note)}</small></td></tr>`).join("");
  $("#rsExperimentBody").innerHTML = filtered.length ?
    `<table class="rs-table"><thead><tr><th>Bài và nhận định</th><th>Dữ liệu</th><th>Độ đo</th><th>Đối chứng</th><th>Giá trị</th><th>Khả năng so sánh</th></tr></thead><tbody>${tr}</tbody></table>`
    + (filtered.length > 150 ? svRsEmpty(`Đang hiện 150/${filtered.length} dòng. Thêm điều kiện lọc để xem các kết quả khác.`) : "") :
    svRsEmpty(results.length ? "Không có kết quả khớp bộ lọc." : "Chưa có kết quả định lượng trong phiếu bài. Cần bóc phiếu trước khi đối chiếu.");
}

/* Ghi tuần tự theo bằng chứng để yêu cầu cũ không ghi đè lần sửa mới. */
async function svRsSaveReason(id, body) {
  const sid = SV.id;
  const key = sid + ":" + id;
  svResearch.drafts[id] = body;
  const save = (svResearch.saving[key] || Promise.resolve()).catch(() => {}).then(async () => {
    await svFetch(`/api/survey/${sid}/research/items/${id}`, {
      method: "PATCH", headers: { "content-type": "application/json" }, body: JSON.stringify({ body }),
    });
    if (SV.id !== sid) return;
    for (const item of [...(svResearch.data?.items || []), ...(svResearch.data?.evidence || [])]) {
      if (item.id === id) item.body = body;
    }
    if (svResearch.drafts[id] === body) delete svResearch.drafts[id];
    svRsStatus("Đã lưu nhận xét cho bằng chứng.");
  });
  svResearch.saving[key] = save;
  try { await save; }
  finally { if (svResearch.saving[key] === save) delete svResearch.saving[key]; }
}

async function svRsSearch() {
    const qid = svResearch.question;
    const sid = SV.id;
    const item = svResearch.data?.items.find((it) => it.id === qid);
    if (!item) return;
    const searchRequest = ++svResearch.searchRequest;
    const button = $("#rsSuggest");
    try {
      svRsStatus("Đang tìm các đoạn liên quan trong kho…");
      const query = $("#rsSuggestQuery").value.trim() || item.title;
      button.disabled = true;
      $("#rsSuggestions").innerHTML = svRsEmpty("Đang tìm đoạn nguồn…");
      const d = await svFetch(`/api/survey/${sid}/research/suggest?q=${encodeURIComponent(query)}`);
      if (SV.id !== sid || svResearch.question !== qid || searchRequest !== svResearch.searchRequest) return;
      svResearch.suggestions = d.hits;
      svResearch.query = d.query || query;
      svResearch.searched = true;
      svResearchRender();
      svRsStatus(`${d.hits.length} đoạn ứng viên · Chưa tự kết luận ủng hộ hay phản bác · $0`);
    } catch (e) {
      if (SV.id === sid && svResearch.question === qid && searchRequest === svResearch.searchRequest) {
        $("#rsSuggestions").innerHTML = svRsEmpty("Không thể tìm nguồn. Hãy thử lại.");
        svRsStatus(e.message, true);
      }
    } finally {
      if (SV.id === sid && svResearch.question === qid && searchRequest === svResearch.searchRequest) button.disabled = false;
    }
}

function svRsRenderAnswer(result) {
  const question = svResearch.data?.items.find((it) => it.id === svResearch.question)?.title || "";
  $("#rsAnswerBody").innerHTML = `<p><b>${esc(question)}</b></p><p class="small muted">Bản nháp AI · ${result.cached ? "dùng lại bản đã lưu · $0" : "$" + Number(result.cost_usd || 0).toFixed(5)}</p>` +
        result.findings.map((finding) => `<article class="rs-hit"><p>${svRsExcerpt(finding.answer)}</p>
          <details><summary>Đối chiếu trích đoạn gốc</summary><p>${svRsExcerpt(finding.quote)}</p></details>
          <p class="small muted">${esc(finding.title)}</p>${svRsCite(finding.chunk_id, finding.page)}</article>`).join("") +
        (result.missing.length ? `<div class="rs-empty"><b>Chưa đủ căn cứ</b><ul>${result.missing.map((text) => `<li>${esc(text)}</li>`).join("")}</ul></div>` : "");
}

async function svRsLoadAnswer() {
  const sid = SV.id, qid = svResearch.question, request = svResearch.answerRequest;
  if (!qid || svResearch.answerBusy) return;
  try {
    const result = await svFetch(`/api/survey/${sid}/research/answer/${qid}`);
    if (sid !== SV.id || qid !== svResearch.question || request !== svResearch.answerRequest || svResearch.answerBusy) return;
    if (result) svRsRenderAnswer(result);
    else $("#rsAnswerBody").innerHTML = "";
  } catch (err) {
    if (sid === SV.id && qid === svResearch.question && request === svResearch.answerRequest)
      $("#rsAnswerBody").innerHTML = svRsEmpty("Chưa tải được câu trả lời đã lưu: " + err.message);
  }
}

function svRsWire() {
  $("#rsAnswerRun").onclick = async () => {
    const sid = SV.id, qid = svResearch.question;
    if (!qid || $("#rsAnswerRun").disabled) return;
    const request = ++svResearch.answerRequest;
    svResearch.answerBusy = true;
    $("#rsAnswerRun").disabled = true;
    $("#rsAnswerBody").innerHTML = svRsEmpty("Đang đối chiếu câu hỏi với các đoạn nguồn…");
    try {
      const result = await svFetch(`/api/survey/${sid}/research/answer/${qid}`, { method: "POST" });
      if (sid !== SV.id || qid !== svResearch.question || request !== svResearch.answerRequest) return;
      svRsRenderAnswer(result);
    } catch (err) {
      if (sid === SV.id && qid === svResearch.question && request === svResearch.answerRequest)
        $("#rsAnswerBody").innerHTML = svRsEmpty("Chưa tạo được câu trả lời: " + err.message);
    } finally {
      if (sid === SV.id && qid === svResearch.question && request === svResearch.answerRequest) {
        svResearch.answerBusy = false;
        $("#rsAnswerRun").disabled = false;
      }
    }
  };
  $("#rsRefresh").onclick = () => svResearchLoad().catch((e) => svRsStatus(e.message, true));
  $("#rsExperimentFilter").oninput = svRsRenderExperiments;
  $("#rsExperimentStatus").onchange = svRsRenderExperiments;
  $("#rsQuestionPick").onchange = async (e) => {
    ++svResearch.searchRequest;
    ++svResearch.answerRequest;
    svResearch.answerBusy = false;
    $("#rsAnswerBody").innerHTML = "";
    svResearch.question = e.target.value;
    $("#rsSuggestQuery").value = "";
    svResearch.query = "";
    svResearch.suggestions = [];
    svResearch.searched = false;
    svResearchRender();
    await svRsSearch();
    await svRsLoadAnswer();
  };
  $("#rsAddQuestion").onclick = async () => {
    const el = $("#rsQuestionInput");
    const button = $("#rsAddQuestion");
    if (button.disabled) return;
    if (!el.value.trim()) return svRsStatus("Hãy nhập câu hỏi nghiên cứu", true);
    const existing = svResearch.data?.items.find((it) => it.kind === "question" && it.title === el.value.trim());
    if (existing) {
      el.value = "";
      await $("#rsQuestionPick").onchange({ target: { value: existing.id } });
      return;
    }
    button.disabled = true;
    try {
      const sid = SV.id;
      const result = await svFetch(`/api/survey/${sid}/research/items`, {
        method: "POST", headers: { "content-type": "application/json" },
        body: JSON.stringify({ kind: "question", title: el.value.trim() }),
      });
      if (SV.id !== sid) return;
      el.value = "";
      $("#rsSuggestQuery").value = "";
      ++svResearch.answerRequest;
      svResearch.answerBusy = false;
      $("#rsAnswerBody").innerHTML = "";
      svResearch.question = result.id;
      svResearch.suggestions = [];
      ++svResearch.searchRequest;
      svResearch.searched = false;
      await svResearchLoad();
    } catch (e) { svRsStatus(e.message, true); }
    finally { button.disabled = false; }
  };
  $("#rsQuestionInput").onkeydown = (e) => {
    if (e.key === "Enter" && !e.isComposing) { e.preventDefault(); $("#rsAddQuestion").onclick(); }
  };
  $("#rsSuggest").onclick = svRsSearch;
  $("#rsSuggestQuery").onkeydown = (e) => {
    if (e.key === "Enter" && !e.isComposing) { e.preventDefault(); $("#rsSuggest").click(); }
  };
  $("#rsAddClaim").onclick = async () => {
    const title = $("#rsClaimInput").value.trim();
    const chunk_id = $("#rsClaimCite").value.trim();
    if (!title) return svRsStatus("Hãy nhập nhận định cần kiểm tra", true);
    const sid = SV.id;
    try {
      await svRsWrite({ kind: "claim", title, chunk_id });
      if (sid !== SV.id) return;
      $("#rsClaimInput").value = ""; $("#rsClaimCite").value = "";
    } catch (e) { if (sid === SV.id) svRsStatus(e.message, true); }
  };
  $("#rsAddNote").onclick = async () => {
    const title = $("#rsNoteTitle").value.trim();
    if (!title) return svRsStatus("Hãy nhập tiêu đề ghi chú", true);
    const sid = SV.id;
    const editingNote = svResearch.editingNote;
    try {
      await svRsWrite({ kind: $("#rsNoteKind").value, title, body: $("#rsNoteBody").value }, editingNote);
      if (sid !== SV.id) return;
      svResearch.editingNote = "";
      $("#rsNoteTitle").value = ""; $("#rsNoteBody").value = "";
      $("#rsAddNote").textContent = "Lưu vào dự án";
    } catch (e) { if (sid === SV.id) svRsStatus(e.message, true); }
  };
  $("#svPaneResearch").onclick = async (e) => {
    const target = e.target.closest("[data-rs-to]");
    if (target) {
      svRsOpen(target.dataset.rsTo);
      return;
    }
    const starter = e.target.closest("[data-rs-starter]");
    if (starter) {
      const q = svResearch.data?.question_starters?.[Number(starter.dataset.rsStarter)];
      if (q) { $("#rsQuestionInput").value = q.title; await $("#rsAddQuestion").onclick(); }
      return;
    }
    const btn = e.target.closest("[data-rs-chunk], [data-rs-link], [data-rs-source], [data-rs-delete], [data-rs-edit], [data-rs-reviewed], [data-rs-resolve], [data-rs-save-reason]");
    if (!btn) return;
    const data = svResearch.data;
    if (!data) return;
    try {
      if (btn.dataset.rsChunk) {
        const hit = svResearch.suggestions.find((h) => h.chunk_id === btn.dataset.rsChunk);
        if (!hit || !svResearch.question) return;
        const existing = data.evidence.find((it) => it.chunk_id === hit.chunk_id && it.question_id === svResearch.question);
        if (existing) await svRsWrite({ stance: btn.dataset.rsStance }, existing.id);
        else await svRsWrite({ kind: "evidence", title: hit.text,
          stance: btn.dataset.rsStance, chunk_id: hit.chunk_id, question_id: svResearch.question });
      } else if (btn.dataset.rsSaveReason) {
        const card = btn.closest("[data-rs-evidence]");
        const item = data.evidence.find((it) => it.id === btn.dataset.rsSaveReason);
        const textarea = card?.querySelector("textarea");
        if (!item || !textarea) return;
        await svRsSaveReason(item.id, textarea.value);
        svResearchRender();
      } else if (btn.dataset.rsLink) {
        if (!svResearch.question) return;
        await svRsWrite({ question_id: svResearch.question }, btn.dataset.rsLink);
      } else if (btn.dataset.rsSource) {
        const source = svResearch.suggestions.find((h) => h.chunk_id === btn.dataset.rsSource);
        $("#rsClaimCite").value = btn.dataset.rsSource;
        if (!$("#rsClaimInput").value.trim() && source) $("#rsClaimInput").value = source.text.slice(0, 240);
        svRsOpen("rsClaims");
        $("#rsClaimInput").focus({ preventScroll: true });
      } else if (btn.dataset.rsEdit) {
        const note = data.items.find((it) => it.id === btn.dataset.rsEdit);
        if (!note) return;
        svResearch.editingNote = note.id;
        $("#rsNoteKind").value = note.kind;
        $("#rsNoteTitle").value = note.title;
        $("#rsNoteBody").value = note.body;
        $("#rsAddNote").textContent = "Cập nhật ghi chú";
        $("#rsNoteTitle").focus();
      } else if (btn.dataset.rsReviewed || btn.dataset.rsResolve) {
        const id = btn.dataset.rsReviewed || btn.dataset.rsResolve;
        const item = data.items.find((it) => it.id === id);
        if (!item) return;
        await svRsWrite({ status: item.status === "resolved" ? "open" :
          btn.dataset.rsReviewed ? "reviewed" : "resolved" }, id);
      } else if (btn.dataset.rsDelete) {
        const id = btn.dataset.rsDelete;
        const sid = SV.id;
        if (!await xacNhan("Xoá ghi chép này?", "Thông tin tự ghi và phân loại bằng chứng này sẽ bị xoá.", {ok: "Xoá", hong: true})) return;
        if (sid !== SV.id) return;
        await svFetch(`/api/survey/${sid}/research/items/${id}`, { method: "DELETE" });
        await svResearchLoad();
      }
    } catch (err) { svRsStatus(err.message, true); }
  };
  $("#svPaneResearch").oninput = (e) => {
    const input = e.target.closest("[data-rs-reason]");
    if (input) svResearch.drafts[input.dataset.rsReason] = input.value;
  };
  $("#svPaneResearch").onchange = async (e) => {
    const reason = e.target.closest("[data-rs-reason]");
    if (reason) {
      try { await svRsSaveReason(reason.dataset.rsReason, reason.value); }
      catch (err) { svRsStatus("Chưa lưu được nhận xét. Nội dung vẫn còn trong ô: " + err.message, true); }
      return;
    }
    const input = e.target.closest("[data-rs-reclass]");
    if (!input || !svResearch.data) return;
    const item = svResearch.data.evidence.find((it) => it.id === input.dataset.rsReclass);
    if (!item || item.stance === input.value) return;
    try { await svRsWrite({ stance: input.value }, item.id); }
    catch (err) { input.value = item.stance; svRsStatus(err.message, true); }
  };
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", svRsWire);
} else {
  svRsWire();
}
