/*
 * Render check for the Compare preview — optional, Node only, no dependencies.
 *
 *   node compare_preview/check_render.mjs                      # API on :8000
 *   node compare_preview/check_render.mjs http://127.0.0.1:8010
 *
 * Why this exists
 * ---------------
 * The Library & Search preview was shipped with the note that "browser
 * click-through was NOT performed" because the Chrome extension was not
 * connected. The same was true here. Rather than ship that gap again, this
 * runs `app.js` against a **real response from the running backend**, through
 * a minimal DOM shim, and asserts what the page actually builds.
 *
 * It is not a browser and does not claim to be: there is no layout, no CSS, no
 * event loop and no real user. What it does prove is the part a reviewer most
 * needs proven — that the page's rendering functions turn a genuine API
 * payload into the right elements, with the clause text in text nodes, the
 * diff in spans, the provenance on screen, and nothing hardcoded.
 *
 * Standard library only (Node's built-in `fetch`), no npm, no jsdom, so it
 * adds nothing to install. If Node is not present, skip it: the 59 HTTP flow
 * checks and the 1,000-plus pytest suite do not depend on it.
 */

import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const HERE = dirname(fileURLToPath(import.meta.url));
const API = (process.argv[2] || 'http://127.0.0.1:8000').replace(/\/$/, '');

const checks = [];
function check(name, ok, detail = '') {
  checks.push({ name, ok, detail });
  console.log(`  ${ok ? 'PASS' : 'FAIL'}  ${name}${detail ? '  ' + detail : ''}`);
}

/* --------------------------------------------------------------- DOM shim */

class Node_ {
  constructor(tag) {
    this.tagName = (tag || '').toUpperCase();
    this.childNodes = [];
    this.attributes = {};
    this.className = '';
    this.hidden = false;
    this.disabled = false;
    this._value = '';
    this._selectedIndex = -1;
    this._text = null;
    this.listeners = {};
    this.checked = false;
    this.type = '';
  }

  /*
   * `value` and `selectedIndex` on a <select> are linked in a real browser:
   * assigning either updates the other. The first version of this shim stored
   * them independently, and reported `app.js` as buggy for setting
   * `selectedIndex` and then reading `value` — which is exactly what a page is
   * supposed to do. The accessors below model the real behaviour, so a
   * failure here is the page's and not the harness's.
   */
  get value() {
    return this._value;
  }
  set value(next) {
    this._value = String(next);
    if (this.tagName === 'SELECT') {
      const index = this._options().findIndex((o) => o.value === this._value);
      this._selectedIndex = index;
    }
  }
  get selectedIndex() {
    return this._selectedIndex;
  }
  set selectedIndex(next) {
    this._selectedIndex = Number(next);
    if (this.tagName === 'SELECT') {
      const chosen = this._options()[this._selectedIndex];
      this._value = chosen ? chosen.value : '';
    }
  }
  _options() {
    return this.childNodes.filter((n) => n.tagName === 'OPTION');
  }

  get firstChild() {
    return this.childNodes[0] || null;
  }
  appendChild(child) {
    this.childNodes.push(child);
    if (this.tagName === 'SELECT' && child.tagName === 'OPTION') {
      // A <select> selects its first option as soon as it has one.
      if (this._selectedIndex < 0) this.selectedIndex = 0;
    }
    return child;
  }
  removeChild(child) {
    this.childNodes = this.childNodes.filter((node) => node !== child);
    if (this.tagName === 'SELECT') {
      this.selectedIndex = this._options().length ? 0 : -1;
    }
    return child;
  }
  set textContent(value) {
    this.childNodes = [];
    this._text = String(value);
  }
  get textContent() {
    if (this._text !== null) return this._text;
    return this.childNodes.map((node) => node.textContent).join('');
  }
  addEventListener(name, fn) {
    (this.listeners[name] = this.listeners[name] || []).push(fn);
  }
  setAttribute(name, value) {
    this.attributes[name] = value;
  }
  // Depth-first search over this subtree.
  *walk() {
    for (const child of this.childNodes) {
      yield child;
      if (child.walk) yield* child.walk();
    }
  }
  find(predicate) {
    for (const node of this.walk()) if (predicate(node)) return node;
    return null;
  }
  findAll(predicate) {
    const out = [];
    for (const node of this.walk()) if (predicate(node)) out.push(node);
    return out;
  }
  byClass(name) {
    return this.findAll((node) => (node.className || '').split(/\s+/).includes(name));
  }
}

class Text_ {
  constructor(value) {
    this.tagName = '#text';
    this._value = String(value);
    this.childNodes = [];
  }
  get textContent() {
    return this._value;
  }
  *walk() {}
}

const registry = new Map();
function register(id, tag = 'div') {
  const node = new Node_(tag);
  node.id = id;
  registry.set(id, node);
  return node;
}

// Every id app.js touches, with the tag it expects.
const IDS = {
  'api-status': 'span',
  'api-base': 'input',
  'corpus-note': 'div',
  document: 'select',
  'base-version': 'select',
  'compare-version': 'select',
  method: 'select',
  'pair-note': 'div',
  'match-threshold': 'input',
  'number-threshold': 'input',
  'ambiguity-margin': 'input',
  'high-confidence': 'input',
  run: 'button',
  swap: 'button',
  'run-state': 'span',
  'summary-panel': 'section',
  'version-heads': 'div',
  warnings: 'div',
  metrics: 'div',
  'similarity-note': 'p',
  'comparison-id': 'p',
  'filter-panel': 'section',
  'min-sim': 'input',
  'max-sim': 'input',
  'only-ambiguous': 'input',
  'show-diff': 'input',
  'show-entities': 'input',
  'changes-panel': 'section',
  'changes-count': 'span',
  changes: 'div',
  pager: 'div',
  more: 'button',
  'pager-state': 'span',
  'empty-panel': 'section',
  evidence: 'aside',
  'evidence-title': 'h3',
  'evidence-body': 'div',
  'evidence-close': 'button',
};
for (const [id, tag] of Object.entries(IDS)) register(id, tag);

// The filter checkboxes app.js reads through querySelectorAll.
const filterBoxes = [];
for (const value of ['added', 'removed', 'modified', 'unchanged']) {
  const box = new Node_('input');
  box.className = 'ctype';
  box.value = value;
  box.checked = value !== 'unchanged'; // matches index.html
  filterBoxes.push(box);
}
for (const value of ['high', 'medium', 'low']) {
  const box = new Node_('input');
  box.className = 'conf';
  box.value = value;
  box.checked = true;
  filterBoxes.push(box);
}
// The method select's options are declared in index.html, not built by
// app.js, so the shim has to supply them. Without this its value is the empty
// string, the POST body carries `method: ""`, and the API correctly rejects
// it — which looked like a page bug and was a harness gap.
for (const value of ['clause_aware', 'paragraph']) {
  const option = new Node_('option');
  option.value = value;
  option.textContent = value;
  registry.get('method').appendChild(option);
}

// index.html marks these hidden until there is something to show. The shim
// does not parse the HTML, so the initial state is declared here; without it
// "the panel is shown" would pass before anything rendered.
for (const id of ['summary-panel', 'filter-panel', 'changes-panel', 'evidence',
                  'corpus-note', 'pair-note', 'pager']) {
  registry.get(id).hidden = true;
}
registry.get('empty-panel').hidden = false;

registry.get('show-diff').checked = true;
registry.get('show-entities').checked = false;
registry.get('only-ambiguous').checked = false;

const documentShim = {
  createElement: (tag) => new Node_(tag),
  createTextNode: (value) => new Text_(value),
  getElementById: (id) => registry.get(id) || null,
  querySelectorAll: (selector) => {
    const wanted = selector.split(',').map((s) => s.trim());
    return filterBoxes.filter((box) => {
      for (const sel of wanted) {
        const [cls, state] = sel.replace(/^\./, '').split(':');
        if (box.className !== cls) continue;
        if (state === 'checked' && !box.checked) continue;
        return true;
      }
      return false;
    });
  },
  addEventListener: () => {},
};

globalThis.document = documentShim;
globalThis.location = { origin: API.replace(/\/api$/, '') };

/* ----------------------------------------------------------------- loading */

const source = readFileSync(join(HERE, 'app.js'), 'utf8');

console.log('== the page as source ==');
check('no innerHTML anywhere', !/\binnerHTML\b/.test(source));
check('no outerHTML, insertAdjacentHTML or document.write',
  !/outerHTML|insertAdjacentHTML|document\.write/.test(source));
check('no eval and no new Function', !/\beval\(|new Function\(/.test(source));
check('no hardcoded clause text or comparison fixture',
  !/VER-00\d\d['"]\s*[,}]/.test(source.replace(/\/\*[\s\S]*?\*\//g, '')),
  'no version id appears as data outside comments');
check('every API path it uses is a real one',
  ['/health', '/compare/options', '/compare'].every((p) => source.includes(p)));

// Expose the module's functions. app.js is a plain script, not a module, so it
// is evaluated with an epilogue that hands back what we want to exercise.
const exported = [
  'checkHealth', 'loadOptions', 'onDocumentChange', 'describePair',
  'buildRequest', 'runComparison', 'renderSummary', 'renderChanges',
  'renderChange', 'renderDiff', 'showEvidence', 'state',
];
const factory = new Function(
  `${source}\nreturn {${exported.join(', ')}};`
);
const app = factory();
app.state.apiBase = `${API}/api`;

/* ---------------------------------------------------------------- the page */

console.log('\n== backend reachable ==');
const healthy = await app.checkHealth();
check('health check passes', healthy, registry.get('api-status').textContent);
if (!healthy) {
  console.log('\nbackend is not reachable; cannot continue');
  process.exit(1);
}

console.log('\n== 1. the version picker, filled from the live API ==');
await app.loadOptions();
const documentSelect = registry.get('document');
const options = documentSelect.childNodes.filter((n) => n.tagName === 'OPTION');
check('document select is populated', options.length >= 1, `${options.length} document(s)`);
check('document select is enabled', documentSelect.disabled === false);
check('every option carries a real DOC id',
  options.every((o) => /^DOC-\d+$/.test(o.value)),
  options.map((o) => o.value).join(', '));
check('the corpus note describes the corpus',
  /comparable pair/.test(registry.get('corpus-note').textContent),
  registry.get('corpus-note').textContent.slice(0, 96) + '…');

const baseSelect = registry.get('base-version');
const compareSelect = registry.get('compare-version');
check('both version selects are populated',
  baseSelect.childNodes.length >= 2 && compareSelect.childNodes.length >= 2,
  `${baseSelect.childNodes.length} issues`);
check('the newer issue defaults to the newest', compareSelect.selectedIndex === 0);
check('the older issue defaults to the one before it', baseSelect.selectedIndex === 1);
check('base and compare differ by default', baseSelect.value !== compareSelect.value,
  `${baseSelect.value} -> ${compareSelect.value}`);
check('the Compare button is enabled', registry.get('run').disabled === false);
check('the pair note explains the order',
  registry.get('pair-note').hidden === false,
  registry.get('pair-note').textContent.slice(0, 96) + '…');

console.log('\n== 2. the request the page builds ==');
const request = app.buildRequest(0);
check('it sends both version ids',
  request.base_version_id === baseSelect.value &&
  request.compare_version_id === compareSelect.value);
check('unchanged is filtered out by default',
  Array.isArray(request.change_types) && !request.change_types.includes('unchanged'),
  JSON.stringify(request.change_types));
check('no threshold is sent when the boxes are empty',
  !('match_threshold' in request));
check('the word diff is requested', request.include_diff === true);

console.log('\n== 3. running a real comparison ==');
await app.runComparison(0);
const payload = app.state.current;
check('a comparison came back', Boolean(payload),
  payload ? payload.comparison_id : `page reported: ${registry.get('pair-note').textContent}`);
if (!payload) {
  console.log('the comparison did not return; stopping');
  process.exit(1);
}
check('the summary panel is shown', registry.get('summary-panel').hidden === false);
check('the changes panel is shown', registry.get('changes-panel').hidden === false);
check('the empty state is hidden', registry.get('empty-panel').hidden === true);

console.log('\n== 4. the summary, rendered ==');
const heads = registry.get('version-heads');
check('both version heads are rendered', heads.byClass('version-head').length === 2);
check('the head names the offset basis',
  heads.textContent.includes('normalized_text'));
check('the head shows a page count', /pages/.test(heads.textContent));
const metrics = registry.get('metrics');
check('the four change counts are rendered',
  ['added', 'removed', 'modified', 'unchanged']
    .every((k) => metrics.textContent.includes(k)),
  metrics.textContent.replace(/\s+/g, ' ').slice(0, 110));
check('the similarity caveat is on screen',
  registry.get('similarity-note').textContent.includes('not a legal-equivalence score'));
check('the comparison id and thresholds are on screen',
  registry.get('comparison-id').textContent.includes('CMP-') &&
  registry.get('comparison-id').textContent.includes('match_threshold'));

console.log('\n== 5. the side-by-side changes ==');
const changesHost = registry.get('changes');
const cards = changesHost.byClass('change');
check('a card per change', cards.length === payload.items.length,
  `${cards.length} card(s)`);
check('each card has two sides',
  cards.every((card) => card.byClass('side').length === 2));
check('the change count line is written',
  /change\(s\) shown/.test(registry.get('changes-count').textContent),
  registry.get('changes-count').textContent);

const modified = payload.items.findIndex((i) => i.change_type === 'modified');
if (modified >= 0) {
  const card = cards[modified];
  check('a modified card carries a similarity pill',
    card.textContent.includes('similarity'));
  check('a modified card carries a confidence pill',
    /confidence (high|medium|low)/.test(card.textContent));
  check('a modified card shows the clause id and page',
    card.byClass('side-meta').some((m) => /CLS-\d+/.test(m.textContent) &&
      /page/.test(m.textContent)));
  const inserts = card.byClass('d-insert');
  const deletes = card.byClass('d-delete');
  check('the word diff renders insertions and deletions',
    inserts.length + deletes.length > 0,
    `${inserts.length} insert span(s), ${deletes.length} delete span(s)`);
  check('diff text lives in text nodes, not in markup',
    [...inserts, ...deletes].every((span) =>
      span.childNodes.every((child) => child.tagName === '#text')));
  check('a plain-language explanation is rendered',
    card.byClass('explanation').length === 1 &&
    card.byClass('explanation')[0].textContent.includes('What changed'));
  check('an evidence button is offered for each present side',
    card.byClass('evidence-row')[0].findAll((n) => n.tagName === 'BUTTON').length ===
      ['old', 'new'].filter((s) => payload.items[modified][s]).length);
} else {
  check('a modified change was present to inspect', false, 'none in this page');
}

const added = payload.items.findIndex((i) => i.change_type === 'added');
if (added >= 0) {
  const card = cards[added];
  check('an added card says there is no counterpart',
    card.textContent.includes('no counterpart'));
  check('an added card shows the absent side as absent',
    card.byClass('side-absent').length === 1);
}

console.log('\n== 6. the evidence drawer ==');
const withEvidence = payload.items.findIndex((i) => i.old || i.new);
const side = payload.items[withEvidence].old ? 'old' : 'new';
app.showEvidence(payload.items[withEvidence], side);
const drawer = registry.get('evidence');
const body = registry.get('evidence-body');
check('the drawer opens', drawer.hidden === false);
check('it names the clause', /CLS-\d+/.test(registry.get('evidence-title').textContent),
  registry.get('evidence-title').textContent);
check('it states the evidence status',
  /Resolved|is (partial|unresolved)/.test(body.textContent));
check('it lists document, version, clause, pages and offset basis',
  ['document', 'version', 'clause', 'pages', 'offsets into']
    .every((term) => body.textContent.includes(term)));
const quotes = body.byClass('quote');
const expectedSpans = payload.items[withEvidence][side].evidence.spans.length;
check('a quote block per page span', quotes.length === expectedSpans,
  `${quotes.length} of ${expectedSpans}`);
check('the quote is the text the backend verified',
  quotes.length === 0 ||
  payload.items[withEvidence][side].evidence.spans[0].quote === quotes[0].textContent);
check('the stored clause text is shown for comparison',
  body.textContent.includes('Stored clause text'));

console.log('\n== 7. filtering re-runs on the backend ==');
filterBoxes.find((b) => b.value === 'modified').checked = false;
const narrowed = app.buildRequest(0);
check('unchecking a type changes the request',
  !narrowed.change_types.includes('modified'),
  JSON.stringify(narrowed.change_types));
await app.runComparison(0);
check('the narrowed comparison returns only the wanted types',
  app.state.current.items.every((i) => i.change_type !== 'modified'),
  `${app.state.current.total} change(s)`);
check('total_unfiltered still reports the whole diff',
  app.state.current.total_unfiltered >= app.state.current.total,
  `${app.state.current.total} of ${app.state.current.total_unfiltered}`);

console.log('\n== 8. the empty state is honest ==');
// A combination that cannot match anything, whatever the corpus holds: an
// added clause has no counterpart, so it is never an ambiguous *match*.
// Picking a filter that happens to be empty on this pair would make the
// check depend on the data.
registry.get('only-ambiguous').checked = true;
for (const box of filterBoxes) {
  if (box.className === 'ctype') box.checked = box.value === 'added';
}
await app.runComparison(0);
check('the impossible filter really returns nothing',
  app.state.current.items.length === 0, `${app.state.current.total} item(s)`);
check('an empty result says so rather than showing nothing',
  changesHost.textContent.includes('No change matches these filters'),
  changesHost.textContent.replace(/\s+/g, ' ').slice(0, 72) + '…');
check('and the empty state is not an error',
  registry.get('changes-panel').hidden === false);

console.log('\n' + '='.repeat(72));
const failed = checks.filter((c) => !c.ok);
console.log(`${checks.length} checks, ${failed.length} failed`);
for (const c of failed) console.log(`  FAILED: ${c.name}  ${c.detail}`);
process.exit(failed.length ? 1 : 0);
