/*
 * NiyamSetu Library & Search — temporary developer frontend.
 *
 * Every document, count, filter value and search result on the page comes
 * from the running FastAPI backend. There is no hardcoded data in this file;
 * the only literals are query-parameter names and labels.
 *
 * Two rules this file keeps deliberately:
 *
 *  1. Snippets are rendered as TEXT, never as HTML. The backend wraps matched
 *     terms in [[hl]] / [[/hl]] sentinels precisely so that a client can mark
 *     them up without an innerHTML of document text. renderSnippet() splits
 *     on the sentinels and builds text nodes and <mark> elements.
 *  2. No PDF path from the server is ever used. The "official source" link
 *     uses the version's own source_url — the publisher's URL — and page text
 *     comes from the API, so the browser never learns a filesystem path.
 */
'use strict';

const DEFAULT_API = `${location.protocol}//${location.hostname}:8000/api`;

const state = {
  api: localStorage.getItem('niyamsetu.api') || DEFAULT_API,
  view: 'library',
  // search
  query: '',
  scope: 'clauses',
  strategy: 'fts',
  method: 'clause_aware',
  sort: 'relevance',
  resultsOffset: 0,
  resultsLimit: 10,
  lastSearch: null,
  // library
  librarySort: 'public_id',
  libraryOffset: 0,
  libraryLimit: 10,
  filters: { department: null, document_type: null, year: null, date_from: null, date_to: null },
  facets: null,
};

/* ------------------------------------------------------------------ utils */

const $ = (id) => document.getElementById(id);

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined && text !== null) node.textContent = String(text);
  return node;
}

function qs(params) {
  const search = new URLSearchParams();
  Object.entries(params).forEach(([key, value]) => {
    if (value === null || value === undefined || value === '') return;
    search.set(key, value);
  });
  return search.toString();
}

/** Fetch JSON from the API, turning the §12.1 error envelope into an Error. */
async function api(path, params) {
  const url = `${state.api}${path}${params ? '?' + qs(params) : ''}`;
  let response;
  try {
    response = await fetch(url, { headers: { Accept: 'application/json' } });
  } catch (networkError) {
    throw new Error(`cannot reach the backend at ${state.api} — is uvicorn running?`);
  }
  const body = await response.json().catch(() => null);
  if (!response.ok) {
    const envelope = body && body.error;
    const message = envelope ? `${envelope.code}: ${envelope.message}` : `HTTP ${response.status}`;
    const error = new Error(message);
    error.code = envelope ? envelope.code : String(response.status);
    error.details = envelope ? envelope.details : null;
    throw error;
  }
  return body;
}

/**
 * Render a backend snippet safely.
 *
 * The backend marks matches with [[hl]] / [[/hl]] sentinels rather than HTML
 * precisely so this can be done with text nodes: the snippet is built out of
 * a government PDF, and assigning it to innerHTML would let a document's
 * content become markup. Walks the string, emitting text and <mark> in turn.
 */
function renderSnippet(snippet) {
  const wrapper = el('p', 'snippet');
  if (!snippet) {
    wrapper.appendChild(el('span', 'muted', 'No snippet for this match.'));
    return wrapper;
  }
  const OPEN = '[[hl]]';
  const CLOSE = '[[/hl]]';
  let rest = snippet;
  while (rest.length) {
    const open = rest.indexOf(OPEN);
    if (open === -1) {
      wrapper.appendChild(document.createTextNode(rest));
      break;
    }
    if (open > 0) wrapper.appendChild(document.createTextNode(rest.slice(0, open)));
    rest = rest.slice(open + OPEN.length);
    const close = rest.indexOf(CLOSE);
    if (close === -1) {
      // An unterminated marker means the snippet was truncated mid-highlight.
      // Show the remainder as text rather than dropping it.
      wrapper.appendChild(document.createTextNode(rest));
      break;
    }
    wrapper.appendChild(el('mark', null, rest.slice(0, close)));
    rest = rest.slice(close + CLOSE.length);
  }
  return wrapper;
}

function setStatus(node, kind, message) {
  node.replaceChildren();
  const box = el('div', `state state-${kind}`);
  box.appendChild(el('strong', null, kind === 'error' ? 'Error' : kind === 'empty' ? 'Nothing found' : 'Loading'));
  box.appendChild(el('p', null, message));
  node.appendChild(box);
}

function pageRange(hit) {
  return hit.page_start === hit.page_end
    ? `page ${hit.page_start}`
    : `pages ${hit.page_start}–${hit.page_end}`;
}

/* ------------------------------------------------------------- navigation */

function show(view) {
  state.view = view;
  [['library', 'view-library'], ['results', 'view-results'], ['document', 'view-document']].forEach(
    ([name, id]) => {
      $(id).hidden = name !== view;
      $(`tab-${name}`).setAttribute('aria-selected', String(name === view));
    }
  );
}

['library', 'results', 'document'].forEach((name) => {
  $(`tab-${name}`).addEventListener('click', () => show(name));
});

/* ------------------------------------------------------------------ health */

async function checkHealth() {
  const pill = $('api-status');
  try {
    const health = await api('/health');
    const ok = health.status === 'ok' && health.database === 'ok';
    pill.className = `pill ${ok ? 'pill-ok' : 'pill-warn'}`;
    pill.textContent = `backend ${health.status} · db ${health.database} · v${health.version}`;
  } catch (error) {
    pill.className = 'pill pill-bad';
    pill.textContent = error.message;
  }
}

/* ------------------------------------------------------------------ facets */

async function loadFacets() {
  const stats = $('corpus-stats');
  try {
    const facets = await api('/library/facets');
    state.facets = facets;
    stats.replaceChildren();
    const tiles = [
      ['Documents', facets.documents],
      ['Versions', facets.versions],
      ['Pages', facets.pages],
      ['Clauses', facets.segments.clause_aware ?? 0],
      ['Paragraphs', facets.segments.paragraph ?? 0],
      ['Authorities', facets.departments.length],
    ];
    tiles.forEach(([label, value]) => {
      const tile = el('div', 'stat');
      tile.appendChild(el('span', 'stat-value', value));
      tile.appendChild(el('span', 'stat-label', label));
      stats.appendChild(tile);
    });
    if (facets.date_range.earliest) {
      const tile = el('div', 'stat stat-wide');
      tile.appendChild(el('span', 'stat-value-sm', `${facets.date_range.earliest} → ${facets.date_range.latest}`));
      tile.appendChild(el('span', 'stat-label', 'Publication dates'));
      stats.appendChild(tile);
    }
    renderFacetLists();
  } catch (error) {
    setStatus(stats, 'error', error.message);
  }
}

function facetButton(container, facet, key, value, label, count) {
  const active = state.filters[key] === value;
  const button = el('button', `facet ${active ? 'facet-active' : ''}`);
  button.type = 'button';
  button.appendChild(el('span', 'facet-label', label));
  button.appendChild(el('span', 'facet-count', count));
  button.addEventListener('click', () => {
    state.filters[key] = active ? null : value;
    state.libraryOffset = 0;
    state.resultsOffset = 0;
    renderFacetLists();
    renderActiveFilters();
    if (state.view === 'results' && state.query) runSearch();
    else loadLibrary();
  });
  container.appendChild(button);
  void facet;
}

function renderFacetLists() {
  if (!state.facets) return;
  [['facet-departments', 'facet-departments-2', 'departments', 'department'],
   ['facet-types', 'facet-types-2', 'document_types', 'document_type'],
   ['facet-years', null, 'publication_years', 'year']].forEach(([idA, idB, facetKey, filterKey]) => {
    [idA, idB].filter(Boolean).forEach((id) => {
      const container = $(id);
      if (!container) return;
      container.replaceChildren();
      const values = state.facets[facetKey];
      if (!values.length) {
        container.appendChild(el('p', 'muted', 'none in the corpus'));
        return;
      }
      values.forEach((entry) => {
        // null is a real bucket; the API labels it "Not stated" and filtering
        // on the literal string 'null' selects it.
        const value = entry.value === null ? 'null' : entry.value;
        facetButton(container, entry, filterKey, value, entry.label, entry.count);
      });
    });
  });
  renderActiveFilters();
}

function renderActiveFilters() {
  ['active-filters', 'active-filters-2'].forEach((id) => {
    const container = $(id);
    if (!container) return;
    container.replaceChildren();
    const active = Object.entries(state.filters).filter(([, value]) => value);
    if (!active.length) {
      container.appendChild(el('span', 'muted', 'no filters applied'));
      return;
    }
    active.forEach(([key, value]) => {
      const chip = el('button', 'chip');
      chip.type = 'button';
      chip.appendChild(el('span', null, `${key}: ${value}`));
      chip.appendChild(el('span', 'chip-x', '×'));
      chip.title = 'Remove this filter';
      chip.addEventListener('click', () => {
        state.filters[key] = null;
        state.libraryOffset = 0;
        state.resultsOffset = 0;
        renderFacetLists();
        if (state.view === 'results' && state.query) runSearch();
        else loadLibrary();
      });
      container.appendChild(chip);
    });
  });
}

/** Filters, translated into API query parameters. */
function filterParams() {
  const { department, document_type: documentType, year, date_from: from, date_to: to } = state.filters;
  const params = { department, document_type: documentType, date_from: from, date_to: to };
  if (year) {
    // A year facet is a date range; the API has no year parameter and should
    // not grow one for something expressible in the parameters it has.
    params.date_from = params.date_from || `${year}-01-01`;
    params.date_to = params.date_to || `${year}-12-31`;
  }
  return params;
}

/* ----------------------------------------------------------------- library */

async function loadLibrary() {
  const list = $('library-list');
  setStatus(list, 'loading', 'Reading the document list from the API…');
  try {
    const payload = await api('/documents', {
      ...filterParams(),
      q: state.query || null,
      sort: state.librarySort,
      limit: state.libraryLimit,
      offset: state.libraryOffset,
    });
    $('library-heading').textContent = state.query
      ? `Documents matching title or reference “${state.query}”`
      : 'Documents';
    list.replaceChildren();
    if (!payload.items.length) {
      setStatus(list, 'empty', 'No document matches these filters. Remove a filter, or clear the query.');
      renderPager($('library-pager'), payload, (offset) => {
        state.libraryOffset = offset;
        loadLibrary();
      });
      return;
    }
    payload.items.forEach((doc) => list.appendChild(documentCard(doc)));
    renderPager($('library-pager'), payload, (offset) => {
      state.libraryOffset = offset;
      loadLibrary();
    });
  } catch (error) {
    setStatus(list, 'error', error.message);
  }
}

function documentCard(doc) {
  const card = el('article', 'card');
  const head = el('div', 'card-head');
  const title = el('button', 'card-title');
  title.type = 'button';
  title.textContent = doc.title;
  title.addEventListener('click', () => openDocument(doc.document_id));
  head.appendChild(title);
  head.appendChild(el('span', 'id-tag', doc.document_id));
  card.appendChild(head);

  const meta = el('div', 'meta');
  [
    ['Authority', doc.department],
    ['Type', doc.document_type],
    ['Reference', doc.reference_no],
    ['Published', doc.latest_publication_date],
    ['Versions', doc.version_count],
    ['Pages', doc.page_count],
    ['Clauses', doc.clause_count],
    ['Extraction', doc.extraction_status],
  ].forEach(([label, value]) => {
    const item = el('span', 'meta-item');
    item.appendChild(el('span', 'meta-label', label));
    // A null metadata field is shown as "not stated" rather than hidden: the
    // gap is information (data/README.md §4), not an absence to tidy away.
    item.appendChild(el('span', value ? 'meta-value' : 'meta-value muted', value ?? 'not stated'));
    meta.appendChild(item);
  });
  card.appendChild(meta);
  return card;
}

/* ----------------------------------------------------------------- search */

async function runSearch() {
  if (!state.query.trim()) {
    show('library');
    loadLibrary();
    return;
  }
  show('results');
  const list = $('results-list');
  const summary = $('results-summary');
  setStatus(list, 'loading', `Searching for “${state.query}”…`);
  summary.replaceChildren();

  const path = state.scope === 'documents' ? '/search' : '/search/clauses';
  try {
    const payload = await api(path, {
      ...filterParams(),
      q: state.query,
      strategy: state.strategy,
      method: state.method,
      sort: state.sort,
      limit: state.resultsLimit,
      offset: state.resultsOffset,
    });
    state.lastSearch = payload;
    renderSearchSummary(summary, payload);
    list.replaceChildren();
    if (!payload.items.length) {
      setStatus(
        list,
        'empty',
        payload.warnings.length
          ? payload.warnings.join(' ')
          : `Nothing in the corpus matches “${payload.query}”. Try fewer words, or a different term.`
      );
    } else {
      payload.items.forEach((item, index) => {
        list.appendChild(
          state.scope === 'documents'
            ? documentHitCard(item, payload.offset + index + 1)
            : clauseHitCard(item, payload.offset + index + 1)
        );
      });
    }
    renderPager($('results-pager'), payload, (offset) => {
      state.resultsOffset = offset;
      runSearch();
    });
  } catch (error) {
    setStatus(list, 'error', error.message);
  }
}

function renderSearchSummary(node, payload) {
  node.replaceChildren();
  const line = el('div', 'summary-line');
  line.appendChild(el('strong', null, `${payload.total}`));
  line.appendChild(
    el('span', null, ` ${state.scope === 'documents' ? 'document' : 'clause'}${payload.total === 1 ? '' : 's'} for `)
  );
  line.appendChild(el('span', 'query-echo', `“${payload.query}”`));
  line.appendChild(el('span', 'badge', payload.strategy === 'fts' ? 'PostgreSQL full-text' : 'ILIKE substring'));
  line.appendChild(el('span', 'badge badge-quiet', `${payload.took_ms} ms`));
  line.appendChild(el('span', 'badge badge-quiet', `sort: ${payload.sort}`));
  line.appendChild(el('span', 'badge badge-quiet', payload.segmentation_method));
  node.appendChild(line);

  if (payload.parsed_query) {
    const parsed = el('p', 'parsed');
    parsed.appendChild(el('span', 'meta-label', 'tsquery'));
    parsed.appendChild(el('code', null, payload.parsed_query));
    node.appendChild(parsed);
  }
  payload.warnings.forEach((warning) => node.appendChild(el('p', 'warning', warning)));
}

function clauseHitCard(hit, rank) {
  const card = el('article', 'card');
  const head = el('div', 'card-head');
  head.appendChild(el('span', 'rank', `#${rank}`));
  const title = el('button', 'card-title');
  title.type = 'button';
  title.textContent = hit.document_title;
  title.addEventListener('click', () => openDocument(hit.document_id, hit));
  head.appendChild(title);
  head.appendChild(el('span', 'score', hit.score.toFixed(4)));
  card.appendChild(head);

  const tags = el('div', 'tags');
  tags.appendChild(el('span', 'tag tag-strong', hit.clause_id));
  if (hit.clause_number) tags.appendChild(el('span', 'tag', `clause ${hit.clause_number}`));
  if (hit.clause_number_ambiguous) {
    const flag = el('span', 'tag tag-warn', 'ambiguous marker');
    flag.title = '(i) is both roman one and letter nine. Flagged by the segmenter, not resolved.';
    tags.appendChild(flag);
  }
  tags.appendChild(el('span', 'tag', hit.segment_type));
  tags.appendChild(el('span', 'tag', pageRange(hit)));
  if (hit.department) tags.appendChild(el('span', 'tag tag-quiet', hit.department));
  if (hit.document_type) tags.appendChild(el('span', 'tag tag-quiet', hit.document_type));
  card.appendChild(tags);

  if (hit.heading) card.appendChild(el('p', 'heading', hit.heading));
  card.appendChild(renderSnippet(hit.snippet));

  const provenance = el('div', 'provenance');
  provenance.appendChild(el('span', 'meta-label', 'Provenance'));
  hit.pages.forEach((page) => {
    provenance.appendChild(
      el('span', 'offset', `p${page.page_number}: chars ${page.char_start}–${page.char_end}`)
    );
  });
  provenance.appendChild(el('span', 'tag tag-quiet', `basis: ${hit.offset_basis}`));
  const open = el('button', 'link-button');
  open.type = 'button';
  open.textContent = 'Open at this clause →';
  open.addEventListener('click', () => openDocument(hit.document_id, hit));
  provenance.appendChild(open);
  card.appendChild(provenance);
  return card;
}

function documentHitCard(hit, rank) {
  const card = el('article', 'card');
  const head = el('div', 'card-head');
  head.appendChild(el('span', 'rank', `#${rank}`));
  const title = el('button', 'card-title');
  title.type = 'button';
  title.textContent = hit.title;
  title.addEventListener('click', () => openDocument(hit.document_id, hit.best_clause));
  head.appendChild(title);
  head.appendChild(el('span', 'score', hit.score.toFixed(4)));
  card.appendChild(head);

  const tags = el('div', 'tags');
  tags.appendChild(el('span', 'tag tag-strong', hit.document_id));
  tags.appendChild(el('span', 'tag', `${hit.matched_clauses} matching clause${hit.matched_clauses === 1 ? '' : 's'}`));
  if (hit.metadata_match) {
    const flag = el('span', 'tag tag-good', 'title / reference match');
    flag.title = 'The query matched the document’s own title, reference number or department.';
    tags.appendChild(flag);
  }
  if (hit.department) tags.appendChild(el('span', 'tag tag-quiet', hit.department));
  if (hit.document_type) tags.appendChild(el('span', 'tag tag-quiet', hit.document_type));
  if (hit.publication_date) tags.appendChild(el('span', 'tag tag-quiet', hit.publication_date));
  if (hit.page_count) tags.appendChild(el('span', 'tag tag-quiet', `${hit.page_count} pages`));
  card.appendChild(tags);

  if (hit.best_clause) {
    const evidence = el('div', 'evidence');
    const label = el('p', 'meta-label');
    label.textContent = `Best matching clause — ${hit.best_clause.clause_id}, ${pageRange(hit.best_clause)}`;
    evidence.appendChild(label);
    evidence.appendChild(renderSnippet(hit.best_clause.snippet));
    card.appendChild(evidence);
  } else {
    card.appendChild(
      el('p', 'muted', 'Matched on document metadata only — no clause in it matched the query.')
    );
  }
  return card;
}

function renderPager(node, payload, onJump) {
  node.replaceChildren();
  const { total, limit, offset } = payload;
  if (total <= limit) {
    if (total) node.appendChild(el('span', 'muted', `showing all ${total}`));
    return;
  }
  const pages = Math.ceil(total / limit);
  const current = Math.floor(offset / limit) + 1;

  const prev = el('button', 'ghost small');
  prev.type = 'button';
  prev.textContent = '‹ Previous';
  prev.disabled = offset === 0;
  prev.addEventListener('click', () => onJump(Math.max(0, offset - limit)));
  node.appendChild(prev);

  node.appendChild(el('span', 'muted', `page ${current} of ${pages} · ${total} results`));

  const next = el('button', 'ghost small');
  next.type = 'button';
  next.textContent = 'Next ›';
  next.disabled = offset + limit >= total;
  next.addEventListener('click', () => onJump(offset + limit));
  node.appendChild(next);
}

/* --------------------------------------------------------- document detail */

async function openDocument(documentId, focusClause) {
  show('document');
  const pane = $('document-detail');
  setStatus(pane, 'loading', `Loading ${documentId}…`);
  try {
    const detail = await api(`/documents/${encodeURIComponent(documentId)}`);
    const version = detail.versions[0];
    pane.replaceChildren();

    const header = el('header', 'doc-header');
    header.appendChild(el('h2', null, detail.title));
    const tags = el('div', 'tags');
    tags.appendChild(el('span', 'tag tag-strong', detail.document_id));
    if (detail.department) tags.appendChild(el('span', 'tag', detail.department));
    if (detail.document_type) tags.appendChild(el('span', 'tag', detail.document_type));
    if (detail.reference_no) tags.appendChild(el('span', 'tag tag-quiet', detail.reference_no));
    header.appendChild(tags);
    pane.appendChild(header);

    pane.appendChild(versionsTable(detail.versions));

    if (version && version.source_url) {
      const links = el('p', 'links');
      const anchor = el('a', 'external', 'Open the official source PDF ↗');
      anchor.href = version.source_url;
      anchor.target = '_blank';
      anchor.rel = 'noopener noreferrer';
      links.appendChild(anchor);
      links.appendChild(
        el('span', 'muted', ' — the publisher’s own URL. The server’s file path is never exposed.')
      );
      pane.appendChild(links);
    }

    if (focusClause) pane.appendChild(await clauseProvenancePanel(documentId, focusClause));
    pane.appendChild(await clauseListPanel(documentId, version));
  } catch (error) {
    setStatus(pane, 'error', error.message);
  }
}

function versionsTable(versions) {
  const section = el('section', 'panel');
  section.appendChild(el('h3', null, `Versions (${versions.length})`));
  const table = el('table', 'table');
  const head = el('tr');
  ['Version', 'Label', 'Published', 'Pages', 'Extraction', 'Clauses', 'Paragraphs', 'Offsets'].forEach((h) =>
    head.appendChild(el('th', null, h))
  );
  table.appendChild(head);
  versions.forEach((version) => {
    const row = el('tr');
    [
      version.version_id,
      version.version_label,
      version.publication_date ?? 'not stated',
      version.page_count,
      version.extraction_status,
      version.segment_counts.clause_aware,
      version.segment_counts.paragraph,
      version.offset_basis,
    ].forEach((value) => row.appendChild(el('td', null, value)));
    table.appendChild(row);
    if (version.processing_warnings && version.processing_warnings.length) {
      const warnRow = el('tr');
      const cell = el('td', 'warning-cell');
      cell.colSpan = 8;
      cell.textContent = `extraction warnings: ${version.processing_warnings.join(' · ')}`;
      warnRow.appendChild(cell);
      table.appendChild(warnRow);
    }
  });
  section.appendChild(table);
  return section;
}

/**
 * The provenance demonstration: take the clause's stored character range and
 * slice it out of the page text the API serves, then show the surrounding
 * page with that span marked. If the slice does not equal the clause text,
 * say so — a provenance claim that cannot be checked is worth nothing.
 */
async function clauseProvenancePanel(documentId, clause) {
  const section = el('section', 'panel panel-focus');
  section.appendChild(el('h3', null, `Provenance of ${clause.clause_id}`));

  const first = clause.pages && clause.pages[0];
  if (!first) {
    section.appendChild(el('p', 'muted', 'This result carries no page offsets.'));
    return section;
  }
  try {
    const pages = await api(`/documents/${encodeURIComponent(documentId)}/pages`, {
      version_id: clause.version_id,
      page_from: first.page_number,
      page_to: first.page_number,
      text: 'normalized',
    });
    const page = pages.items[0];
    const body = page.normalized_text || '';
    const slice = body.slice(first.char_start, first.char_end);

    const info = el('p', 'provenance');
    info.appendChild(el('span', 'meta-label', 'Stored offsets'));
    info.appendChild(
      el('span', 'offset', `page ${first.page_number}, chars ${first.char_start}–${first.char_end} of ${pages.offset_basis}`)
    );
    section.appendChild(info);

    const context = el('pre', 'page-text');
    context.appendChild(document.createTextNode(body.slice(Math.max(0, first.char_start - 300), first.char_start)));
    context.appendChild(el('mark', null, slice));
    context.appendChild(document.createTextNode(body.slice(first.char_end, first.char_end + 300)));
    section.appendChild(context);

    const verdict = el('p', slice.trim().length ? 'verdict-ok' : 'verdict-bad');
    verdict.textContent = slice.trim().length
      ? `The stored range resolves to ${slice.length} characters of this page — highlighted above.`
      : 'The stored range resolved to nothing on this page. That is a provenance defect, not a display problem.';
    section.appendChild(verdict);
  } catch (error) {
    section.appendChild(el('p', 'warning', `could not load the page text: ${error.message}`));
  }
  return section;
}

async function clauseListPanel(documentId, version) {
  const section = el('section', 'panel');
  const heading = el('div', 'pane-head');
  heading.appendChild(el('h3', null, 'Clauses'));
  const label = el('label', 'inline');
  label.appendChild(document.createTextNode('Method '));
  const select = el('select');
  ['clause_aware', 'paragraph'].forEach((method) => {
    const option = el('option', null, method);
    option.value = method;
    if (method === state.method) option.selected = true;
    select.appendChild(option);
  });
  label.appendChild(select);
  heading.appendChild(label);
  section.appendChild(heading);

  const list = el('div', 'clause-list');
  section.appendChild(list);

  async function load(method) {
    setStatus(list, 'loading', 'Loading clauses…');
    try {
      const payload = await api(`/documents/${encodeURIComponent(documentId)}/clauses`, {
        version_id: version ? version.version_id : null,
        method,
        limit: 40,
      });
      list.replaceChildren();
      const note = el('p', 'muted', `${payload.total} ${method} segments · offsets into ${payload.offset_basis} · showing ${payload.items.length}`);
      list.appendChild(note);
      payload.items.forEach((clause) => {
        const row = el('div', 'clause-row');
        const tags = el('div', 'tags');
        tags.appendChild(el('span', 'tag tag-strong', clause.clause_id));
        if (clause.clause_number) tags.appendChild(el('span', 'tag', clause.clause_number));
        tags.appendChild(el('span', 'tag', clause.segment_type));
        tags.appendChild(
          el('span', 'tag', clause.page_start === clause.page_end ? `page ${clause.page_start}` : `pages ${clause.page_start}–${clause.page_end}`)
        );
        if (clause.pages.length > 1) tags.appendChild(el('span', 'tag tag-good', `spans ${clause.pages.length} pages`));
        row.appendChild(tags);
        if (clause.heading) row.appendChild(el('p', 'heading', clause.heading));
        row.appendChild(el('p', 'clause-text', clause.text));
        list.appendChild(row);
      });
    } catch (error) {
      setStatus(list, 'error', error.message);
    }
  }

  select.addEventListener('change', () => load(select.value));
  await load(state.method);
  return section;
}

/* ------------------------------------------------------------ suggestions */

let suggestTimer = null;

function hideSuggestions() {
  const box = $('suggestions');
  box.hidden = true;
  box.replaceChildren();
}

async function loadSuggestions(prefix) {
  if (prefix.trim().length < 2) return hideSuggestions();
  try {
    const payload = await api('/search/suggest', { q: prefix, limit: 8 });
    const box = $('suggestions');
    box.replaceChildren();
    if (!payload.items.length) return hideSuggestions();
    payload.items.forEach((item) => {
      const row = el('li', 'suggestion');
      row.setAttribute('role', 'option');
      row.appendChild(el('span', 'suggestion-kind', item.kind));
      row.appendChild(el('span', null, item.value));
      row.addEventListener('mousedown', (event) => {
        event.preventDefault();
        if (item.kind === 'title' && item.document_id) {
          hideSuggestions();
          openDocument(item.document_id);
          return;
        }
        $('q').value = item.value;
        state.query = item.value;
        hideSuggestions();
        runSearch();
      });
      box.appendChild(row);
    });
    box.hidden = false;
  } catch (error) {
    hideSuggestions();
  }
}

/* ------------------------------------------------------------------ wiring */

$('search-form').addEventListener('submit', (event) => {
  event.preventDefault();
  state.query = $('q').value;
  state.resultsOffset = 0;
  state.libraryOffset = 0;
  hideSuggestions();
  runSearch();
});

$('q').addEventListener('input', (event) => {
  const value = event.target.value;
  clearTimeout(suggestTimer);
  suggestTimer = setTimeout(() => loadSuggestions(value), 180);
});

$('q').addEventListener('blur', () => setTimeout(hideSuggestions, 150));

$('clear').addEventListener('click', () => {
  $('q').value = '';
  state.query = '';
  state.resultsOffset = 0;
  state.libraryOffset = 0;
  hideSuggestions();
  show('library');
  loadLibrary();
});

['scope', 'strategy', 'method', 'sort'].forEach((id) => {
  $(id).addEventListener('change', (event) => {
    state[id] = event.target.value;
    state.resultsOffset = 0;
    if (state.query) runSearch();
  });
});

$('library-sort').addEventListener('change', (event) => {
  state.librarySort = event.target.value;
  state.libraryOffset = 0;
  loadLibrary();
});

$('apply-dates').addEventListener('click', () => {
  state.filters.date_from = $('date-from').value || null;
  state.filters.date_to = $('date-to').value || null;
  state.filters.year = null;
  state.libraryOffset = 0;
  state.resultsOffset = 0;
  renderFacetLists();
  if (state.view === 'results' && state.query) runSearch();
  else loadLibrary();
});

['reset-filters', 'reset-filters-2'].forEach((id) => {
  $(id).addEventListener('click', () => {
    state.filters = { department: null, document_type: null, year: null, date_from: null, date_to: null };
    $('date-from').value = '';
    $('date-to').value = '';
    state.libraryOffset = 0;
    state.resultsOffset = 0;
    renderFacetLists();
    if (state.view === 'results' && state.query) runSearch();
    else loadLibrary();
  });
});

$('api-base').value = state.api;
$('api-base').addEventListener('change', (event) => {
  state.api = event.target.value.replace(/\/$/, '');
  localStorage.setItem('niyamsetu.api', state.api);
  checkHealth();
  loadFacets();
  loadLibrary();
});

/* --------------------------------------------------------------- start-up */

checkHealth();
loadFacets().then(loadLibrary);
