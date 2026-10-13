// Kiểm tra luồng bằng chứng phía trình duyệt mà không gọi mạng hoặc model.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');

const elements = new Map();
function element(selector) {
  if (!elements.has(selector)) {
    elements.set(selector, {
      value: '', innerHTML: '', textContent: '', disabled: false,
      hidden: false, classList: { toggle() {} },
      querySelectorAll: () => [],
      setAttribute() {}, removeAttribute() {},
    });
  }
  return elements.get(selector);
}

const paper = { id: 'p1', title: 'Original research paper' };
const question = { id: 'q1', kind: 'question', title: 'Kết quả có đủ chắc chắn?' };
const evidence = {
  id: 'e1', kind: 'evidence', question_id: 'q1', stance: 'support',
  title: '…HotpotQA exact match improved…', body: '', chunk_id: 'c1',
  source: { title: paper.title, page: 2, text: 'Opening paragraph unrelated to the result.' },
};
const state = {
  items: [question, evidence], papers: [paper], evidence: [evidence],
  experiments: [], gaps: [], relations: [], claims: [], reproducibility: [],
};
const hits = [{ chunk_id: 'c1', paper_id: 'p1', title: paper.title,
  page: 2, text: '…HotpotQA exact match improved…', stance: 'unclassified' }];
const calls = [];
const context = vm.createContext({
  console,
  document: { readyState: 'loading', addEventListener() {} },
  $: element,
  SV: { id: 's1' },
  esc: (x) => String(x ?? '').replace(/&/g, '&amp;').replace(/</g, '&lt;')
    .replace(/>/g, '&gt;').replace(/"/g, '&quot;'),
  matchMedia: () => ({ matches: true }),
  svFetch: async (url, opts = {}) => {
    calls.push({ url, opts });
    if (url.includes('/research/suggest?q=')) return { hits };
    if (url.endsWith('/research')) return state;
    if (url.includes('/research/answer/') && !opts.method) return null;
    if (opts.method === 'PATCH') {
      Object.assign(evidence, JSON.parse(opts.body));
      return evidence;
    }
    throw Error(`Truy vấn kiểm thử không mong đợi: ${url}`);
  },
});
const script = fs.readFileSync(path.join(__dirname, '../web/sv-research.js'), 'utf8');
vm.runInContext(script, context);
vm.runInContext("svResearch.data = globalThis.testData; svResearch.question = 'q1'; svRsWire(); svResearchRender();", 
  Object.assign(context, { testData: state }));

async function run() {
  const html = fs.readFileSync(path.join(__dirname, '../web/index.html'), 'utf8');
  assert.ok(html.indexOf('id="rsSuggestions"') < html.indexOf('id="rsEvidence"'),
    'Kết quả tìm kiếm phải xuất hiện trước ma trận đã lưu');
  assert.match(element('#rsEvidence').innerHTML, /data-rs-reclass="e1"/);
  assert.match(element('#rsEvidence').innerHTML, /data-rs-save-reason="e1"/);
  assert.match(element('#rsEvidence').innerHTML, /HotpotQA exact match improved/);
  assert.doesNotMatch(element('#rsEvidence').innerHTML, /Opening paragraph unrelated/);

  element('#rsSuggestQuery').value = 'HotpotQA';
  await element('#rsSuggest').onclick();
  assert.match(element('#rsSuggestions').innerHTML, /data-rs-chunk="c1"/);
  assert.match(element('#rsSuggestions').innerHTML, /✓ Ủng hộ/);

  // Sửa phân loại ở bản đã lưu, không cần tìm lại đoạn nguồn.
  const select = { dataset: { rsReclass: 'e1' }, value: 'oppose',
    closest: (selector) => selector === "[data-rs-reason]" ? null : select };
  await element('#svPaneResearch').onchange({ target: select });
  assert.equal(evidence.stance, 'oppose');
  assert.ok(calls.some((call) => call.opts.method === 'PATCH' &&
    JSON.parse(call.opts.body).stance === 'oppose'));

  // Ghi lý do qua thẻ bằng chứng và kiểm tra sau khi nạp lại dữ liệu.
  const textarea = { value: 'Chỉ đánh giá trên một tập dữ liệu.' };
  const card = { querySelector: () => textarea };
  const button = { dataset: { rsSaveReason: 'e1' }, closest: () => card };
  await element('#svPaneResearch').onclick({ target: {
    closest: (selector) => (selector.includes('data-rs-to') || selector === '[data-rs-starter]') ? null : button,
  } });
  assert.equal(evidence.body, textarea.value);
  assert.match(element('#rsEvidence').innerHTML, /Chỉ đánh giá trên một tập dữ liệu/);
  assert.match(element('#rsEvidence').innerHTML, /data-rs-lane="oppose"/);
  // Đổi nhãn từ kết quả tìm phải giữ nguyên nhận xét đã lưu.
  const resultButton = { dataset: { rsChunk: 'c1', rsStance: 'support' } };
  await element('#svPaneResearch').onclick({ target: {
    closest: (selector) => (selector.includes('data-rs-to') || selector === '[data-rs-starter]') ? null : resultButton,
  } });
  assert.equal(evidence.stance, 'support');
  assert.equal(evidence.body, textarea.value);
  assert.equal(calls.filter((call) => call.opts.method === 'POST').length, 0);
  assert.equal(vm.runInContext('svRsExcerpt("F^{i}_{s} <script>")', context),
    'F<sup>i</sup><sub>s</sub> &lt;script&gt;');

  // Chọn câu hỏi tự tìm nguồn và bỏ từ khoá thủ công của câu hỏi trước.
  element('#rsSuggestQuery').value = 'Từ khoá cũ';
  const searches = calls.filter(c => c.url.includes('/suggest?')).length;
  await element('#rsQuestionPick').onchange({ target: { value: 'q1' } });
  assert.equal(element('#rsSuggestQuery').value, '');
  assert.equal(calls.filter(c => c.url.includes('/suggest?')).length, searches + 1);
  assert.ok(calls.filter(c => c.url.includes('/suggest?')).at(-1).url.includes(encodeURIComponent(question.title)));

  // Hai lần tự lưu được ghi tuần tự, nhận xét mới nhất phải thắng.
  const reason = { dataset: { rsReason: 'e1' }, value: 'Bản thứ nhất', closest: () => reason };
  const firstSave = element('#svPaneResearch').onchange({ target: reason });
  reason.value = 'Bản mới nhất';
  const secondSave = element('#svPaneResearch').onchange({ target: reason });
  await Promise.all([firstSave, secondSave]);
  assert.equal(evidence.body, 'Bản mới nhất');

  // Mở lại câu hỏi chỉ đọc câu trả lời đã lưu, không gửi yêu cầu sinh mới.
  const fetchBeforeAnswer = context.svFetch;
  const answer = { findings: [{ answer: 'Kết quả đúng câu hỏi.', quote: 'Original source quotation.',
    chunk_id: 'c1', title: paper.title, page: 2 }], missing: [], cached: true, cost_usd: 0 };
  let paidCalls = 0;
  context.svFetch = async (url, opts = {}) => {
    if (url.includes('/research/answer/')) {
      if (opts.method === 'POST') paidCalls++;
      return answer;
    }
    return fetchBeforeAnswer(url, opts);
  };
  await element('#rsQuestionPick').onchange({ target: { value: 'q1' } });
  assert.match(element('#rsAnswerBody').innerHTML, /Kết quả đúng câu hỏi/);
  assert.match(element('#rsAnswerBody').innerHTML, /dùng lại bản đã lưu/);
  assert.equal(paidCalls, 0);

  // Dựng lại giao diện trong lúc chờ không được bật nút gọi model lần hai.
  let resolveAnswer;
  context.svFetch = () => { paidCalls++; return new Promise(resolve => { resolveAnswer = resolve; }); };
  const lateAnswer = element('#rsAnswerRun').onclick();
  vm.runInContext('svResearchRender()', context);
  assert.equal(element('#rsAnswerRun').disabled, true);
  await element('#rsAnswerRun').onclick();
  assert.equal(paidCalls, 1);
  vm.runInContext('svResearchReset()', context);
  resolveAnswer(answer);
  await lateAnswer;
  assert.equal(element('#rsAnswerBody').innerHTML, '');
  context.svFetch = fetchBeforeAnswer;
  vm.runInContext("svResearch.data = testData; svResearch.question = 'q1'; svResearchRender();", context);

  // Kết quả tìm đến trễ sau khi đổi kho không được hiện sang kho mới.
  const originalFetch = context.svFetch;
  let resolveSearch;
  context.svFetch = () => new Promise(resolve => { resolveSearch = resolve; });
  const lateSearch = element('#rsSuggest').onclick();
  context.SV.id = 's2';
  vm.runInContext('svResearchReset()', context);
  resolveSearch({ hits });
  await lateSearch;
  assert.equal(element('#rsSuggestions').innerHTML, '');
  context.svFetch = originalFetch;

  console.log('Sổ nghiên cứu: tìm nguồn, đổi phân loại, lưu lý do và tải lại đều đạt.');
}

run().catch((err) => { console.error(err); process.exitCode = 1; });
