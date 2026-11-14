/*
 * NiyamSetu Compare — temporary developer frontend.
 *
 * NOT the product Compare interface (CLAUDE.md §5.4, §7, D-1).
 *
 * Three rules this file keeps, and they are the reason it exists:
 *
 *  1. **Nothing is hardcoded.** Every document, version, clause, page number,
 *     similarity score and citation is read from the live API. There is no
 *     sample comparison in this file. If the backend is down the page says
 *     so; if the corpus has no version pairs the page says that instead of
 *     offering a pair that does not exist.
 *
 *  2. **Clause text is inserted as a text node, never as HTML.** The text
 *     comes out of a government PDF, and handing a browser markup assembled
 *     from document content is how a document becomes a script. Every
 *     insertion below uses `textContent` or `document.createTextNode`; the
 *     word-level diff builds real <span> elements and puts the text inside
 *     them, rather than concatenating a string of tags.
 *
 *  3. **Uncertainty is shown, not smoothed.** A low-confidence match, an
 *     ambiguous pairing, and evidence that did not resolve are each rendered
 *     distinctly, because §5.4 forbids presenting a speculative change as a
 *     verified fact.
 */

'use strict';

const DEFAULT_API = `${location.origin}/api`;
const PAGE_SIZE = 25;

const state = {
  apiBase: DEFAULT_API,
  options: null,
  documents: [],
  current: null,     // the last comparison payload
  offset: 0,
  running: false,
  // Monotonic id for the in-flight comparison. A response whose id is no
  // longer the latest is discarded rather than rendered: without this, two
  // requests in flight can land out of order and the slower, older one wins.
  requestSeq: 0,
  // Set when a filter changes while a request is in flight. The run that is
  // finishing re-runs once instead of the change being dropped. See
  // runComparison.
  rerunQueued: false,
};

/* ------------------------------------------------------------------ helpers */

const $ = (id) => document.getElementById(id);

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined && text !== null) node.textContent = String(text);
  return node;
}

function clear(node) {
  while (node.firstChild) node.removeChild(node.firstChild);
}

function pill(text, kind) {
  return el('span', `pill pill-${kind || 'muted'}`, text);
}

function fmtScore(value) {
  if (value === null || value === undefined) return '—';
  return `${(value * 100).toFixed(1)}%`;
}

/** Fetch JSON, surfacing the API's own error envelope rather than a status code. */
async function api(path, options) {
  const response = await fetch(`${state.apiBase}${path}`, options);
  const text = await response.text();
  let body = null;
  try {
    body = text ? JSON.parse(text) : null;
  } catch (err) {
    throw new Error(`the API returned a non-JSON body (HTTP ${response.status})`);
  }
  if (!response.ok) {
    const envelope = body && body.error;
    throw new Error(
      envelope ? `${envelope.code}: ${envelope.message}` : `HTTP ${response.status}`
    );
  }
  return body;
}

/* ------------------------------------------------------------------- health */

async function checkHealth() {
  const node = $('api-status');
  node.className = 'pill pill-muted';
  node.textContent = 'checking backend…';
  try {
    const body = await api('/health');
    const ok = body.database === 'ok';
    node.className = `pill pill-${ok ? 'good' : 'warn'}`;
    node.textContent = ok ? `backend ok · v${body.version}` : `database ${body.database}`;
    return ok;
  } catch (err) {
    node.className = 'pill pill-bad';
    node.textContent = 'backend unreachable';
    return false;
  }
}

/* ------------------------------------------------------------------ options */

async function loadOptions() {
  const documentSelect = $('document');
  clear(documentSelect);
  documentSelect.disabled = true;
  documentSelect.appendChild(el('option', null, 'loading…'));

  let payload;
  try {
    payload = await api('/compare/options?include_single_version=true');
  } catch (err) {
    clear(documentSelect);
    documentSelect.appendChild(el('option', null, 'could not load'));
    showCorpusNote(`Could not load comparison options — ${err.message}`, 'note-bad');
    return;
  }

  state.options = payload;
  // Only documents that actually have two or more ok versions can be compared.
  // The rest are kept out of the picker but counted in the note, so the corpus
  // is described honestly rather than looking smaller than it is.
  state.documents = payload.documents.filter((doc) => doc.versions.length >= 2);

  clear(documentSelect);
  if (!state.documents.length) {
    documentSelect.appendChild(el('option', null, 'no document has two issues'));
    documentSelect.disabled = true;
    $('base-version').disabled = true;
    $('compare-version').disabled = true;
    $('run').disabled = true;
    $('swap').disabled = true;
    const corpus = payload.corpus || {};
    showCorpusNote(
      `This corpus has ${corpus.documents ?? '?'} documents and ` +
      `${corpus.ok_versions ?? '?'} successfully extracted versions, but ` +
      `${corpus.documents_with_pairs ?? 0} of them have a second version — so ` +
      `there is nothing to compare. Load a corpus with genuine version pairs ` +
      `(see data/version_pairs/) and try again. No pair is invented to fill ` +
      `this screen.`,
      'note-bad'
    );
    return;
  }

  state.documents.forEach((doc) => {
    const option = el('option', null, `${doc.document_id} · ${doc.title}`);
    option.value = doc.document_id;
    documentSelect.appendChild(option);
  });
  documentSelect.disabled = false;

  const corpus = payload.corpus || {};
  showCorpusNote(
    `${state.documents.length} document(s) with more than one issue · ` +
    `${payload.comparable_pairs} comparable pair(s), of which ` +
    `${payload.declared_supersedes_pairs} have an authority-declared order ` +
    `(the newer issue's own metadata names the older one as the version it ` +
    `supersedes). Corpus: ${corpus.documents} documents, ` +
    `${corpus.ok_versions} extracted versions.`,
    'note-muted'
  );

  onDocumentChange();
}

function showCorpusNote(text, kind) {
  const node = $('corpus-note');
  node.className = `note ${kind || 'note-muted'}`;
  node.textContent = text;
  node.hidden = false;
}

function currentDocument() {
  return state.documents.find((doc) => doc.document_id === $('document').value);
}

function onDocumentChange() {
  const doc = currentDocument();
  const base = $('base-version');
  const compare = $('compare-version');
  clear(base);
  clear(compare);
  if (!doc) return;

  // `versions` arrives newest first. The base (older) default is therefore the
  // second entry and the compare (newer) default is the first, which is the
  // consecutive pair a reader almost always wants.
  doc.versions.forEach((version) => {
    [base, compare].forEach((select) => {
      const option = el(
        'option',
        null,
        `${version.version_id} · ${version.version_label}` +
        ` (${version.page_count}p, ${version.clause_count} clauses)`
      );
      option.value = version.version_id;
      select.appendChild(option);
    });
  });

  compare.selectedIndex = 0;
  base.selectedIndex = Math.min(1, doc.versions.length - 1);
  base.disabled = false;
  compare.disabled = false;
  $('run').disabled = false;
  $('swap').disabled = false;
  describePair();
}

function describePair() {
  const doc = currentDocument();
  const node = $('pair-note');
  if (!doc) {
    node.hidden = true;
    return;
  }
  const baseId = $('base-version').value;
  const compareId = $('compare-version').value;

  if (baseId === compareId) {
    node.className = 'note note-bad';
    node.textContent =
      'Both selections are the same issue. Choose two different issues — ' +
      'comparing a version with itself is refused by the API, not answered ' +
      'with an empty diff.';
    node.hidden = false;
    $('run').disabled = true;
    return;
  }
  $('run').disabled = false;

  const pair = doc.pairs.find(
    (candidate) =>
      candidate.base_version_id === baseId && candidate.compare_version_id === compareId
  );
  const reversed = doc.pairs.find(
    (candidate) =>
      candidate.base_version_id === compareId && candidate.compare_version_id === baseId
  );

  if (pair && pair.supersedes_declared) {
    node.className = 'note note-good';
    node.textContent =
      `${compareId} declares that it supersedes ${baseId}: the order of this ` +
      `diff is asserted by the issuing authority's own metadata, not inferred ` +
      `from publication dates or titles.`;
  } else if (reversed) {
    node.className = 'note';
    node.textContent =
      `These two are the wrong way round — ${baseId} is the newer issue. The ` +
      `API will swap them and say so in a DIRECTION_SWAPPED warning; it never ` +
      `reverses a diff silently.`;
  } else if (pair) {
    node.className = 'note';
    node.textContent =
      `No supersedes link is recorded between these two issues, so "older" and ` +
      `"newer" come from their publication dates. That is a hint, not proof ` +
      `(CLAUDE.md §11.1).` +
      (pair.consecutive ? '' : ' They are also not consecutive: another issue sits between them.');
  } else {
    node.hidden = true;
    return;
  }
  node.hidden = false;
}

/* --------------------------------------------------------------- comparison */

function numberOrNull(id) {
  const raw = $(id).value.trim();
  if (raw === '') return null;
  const value = Number(raw);
  return Number.isFinite(value) ? value : null;
}

function checkedValues(className) {
  return Array.from(document.querySelectorAll(`.${className}:checked`)).map(
    (box) => box.value
  );
}

function buildRequest(offset) {
  const request = {
    base_version_id: $('base-version').value,
    compare_version_id: $('compare-version').value,
    method: $('method').value,
    include_text: true,
    include_diff: $('show-diff').checked,
    include_entities: $('show-entities').checked,
    include_explanation: true,
    limit: PAGE_SIZE,
    offset: offset,
  };

  const types = checkedValues('ctype');
  if (types.length && types.length < 4) request.change_types = types;
  const confidence = checkedValues('conf');
  if (confidence.length && confidence.length < 3) request.confidence = confidence;

  const minSim = numberOrNull('min-sim');
  const maxSim = numberOrNull('max-sim');
  if (minSim !== null) request.min_similarity = minSim;
  if (maxSim !== null) request.max_similarity = maxSim;
  if ($('only-ambiguous').checked) request.only_ambiguous = true;

  [
    ['match-threshold', 'match_threshold'],
    ['number-threshold', 'number_match_threshold'],
    ['ambiguity-margin', 'ambiguity_margin'],
    ['high-confidence', 'high_confidence_similarity'],
  ].forEach(([inputId, field]) => {
    const value = numberOrNull(inputId);
    if (value !== null) request[field] = value;
  });

  return request;
}

/**
 * Run a comparison and render it.
 *
 * Two rules here exist because the first version of this function broke both,
 * and a browser session caught it:
 *
 *  1. **A filter change is never dropped.** The old code returned early when
 *     a request was already in flight (`if (state.running) return;`). Ticking
 *     two filters in quick succession — entirely normal, and unavoidable on
 *     the 707x929-clause pair where a comparison takes over a second — left
 *     the page showing the result of the *first* request while the controls
 *     showed the second state. Now a change arriving mid-flight sets
 *     `rerunQueued`, and the finishing run starts one more.
 *
 *  2. **A stale response can never win.** Each run takes the next
 *     `requestSeq`; a response is rendered only if its id is still the latest.
 *     Without this, a slow earlier request landing after a fast later one
 *     would overwrite the newer result.
 */
async function runComparison(offset) {
  if (state.running) {
    // Only a fresh run is worth queueing. A queued "load more" would append
    // the same page twice, because the offset it captured is already stale.
    if (offset === 0) state.rerunQueued = true;
    return;
  }
  state.running = true;
  const seq = ++state.requestSeq;
  const append = offset > 0;
  $('run-state').textContent = append ? 'loading more…' : 'comparing…';
  $('run').disabled = true;

  let payload;
  try {
    payload = await api('/compare', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(buildRequest(offset)),
    });
  } catch (err) {
    if (seq === state.requestSeq) {
      $('run-state').textContent = '';
      const node = $('pair-note');
      node.className = 'note note-bad';
      node.textContent = `The comparison failed — ${err.message}`;
      node.hidden = false;
    }
    return;
  } finally {
    state.running = false;
    $('run').disabled = false;
    if (state.rerunQueued) {
      state.rerunQueued = false;
      // Re-read the controls as they are *now*, not as they were when the
      // dropped change fired.
      setTimeout(() => runComparison(0), 0);
    }
  }

  // A response for a request that has since been superseded must not render.
  if (seq !== state.requestSeq) return;

  state.offset = offset;
  if (append && state.current) {
    state.current.items = state.current.items.concat(payload.items);
    state.current.total = payload.total;
    renderChanges(state.current, payload.total);
  } else {
    state.current = payload;
    renderSummary(payload);
    renderChanges(payload, payload.total);
  }
  $('run-state').textContent = `${payload.took_ms.toFixed(0)} ms`;
  $('empty-panel').hidden = true;
  $('summary-panel').hidden = false;
  $('filter-panel').hidden = false;
  $('changes-panel').hidden = false;
}

/**
 * Forget the displayed comparison because the selection no longer matches it.
 *
 * Changing the document or either issue used to leave the previous result on
 * screen, under a picker that now described a different pair — so a diff of
 * DOC-0003 sat beneath a selector reading DOC-0006, with nothing to say it was
 * stale. Clearing it is the honest behaviour: the page shows the empty state
 * and the user presses Compare.
 */
function invalidateComparison() {
  if (!state.current) return;
  state.current = null;
  state.offset = 0;
  state.requestSeq += 1;   // any in-flight response is now stale
  state.rerunQueued = false;
  clear($('changes'));
  $('summary-panel').hidden = true;
  $('filter-panel').hidden = true;
  $('changes-panel').hidden = true;
  $('pager').hidden = true;
  $('empty-panel').hidden = false;
  $('evidence').hidden = true;
  $('run-state').textContent = 'selection changed — press Compare';
}

/* ------------------------------------------------------------------ summary */

function versionHead(label, version) {
  const box = el('div', 'version-head');
  box.appendChild(el('h3', null, `${label}: ${version.version_label}`));
  const list = el('dl');
  const rows = [
    ['version', version.version_id],
    ['document', version.document_id],
    ['published', version.publication_date || 'not stated'],
    ['pages', String(version.page_count)],
    // Labelled "document ref", not "reference", because that is what it is:
    // the schema stores reference_no on `documents`, so both issues of a
    // circular carry the same value. Labelling it "reference" inside a
    // per-version card displayed the 2019 issue with the 2026 issue's number.
    ['document ref', version.document_reference_no || 'not stated'],
    ['offsets into', version.offset_basis],
  ];
  if (version.supersedes_version_id) {
    rows.push(['supersedes', version.supersedes_version_id]);
  }
  rows.forEach(([term, value]) => {
    list.appendChild(el('dt', null, term));
    list.appendChild(el('dd', null, value));
  });
  box.appendChild(list);
  return box;
}

function renderSummary(payload) {
  const heads = $('version-heads');
  clear(heads);
  heads.appendChild(versionHead('Older', payload.base_version));
  heads.appendChild(versionHead('Newer', payload.compare_version));

  const warnings = $('warnings');
  clear(warnings);
  (payload.warnings || []).forEach((text) => {
    const note = el('div', 'note note-bad', text);
    warnings.appendChild(note);
  });
  if (!payload.same_document) {
    warnings.appendChild(
      el(
        'div',
        'note note-bad',
        'These two versions are not declared versions of the same document. ' +
        'Read every row as a text comparison, not an authority-confirmed ' +
        'amendment.'
      )
    );
  }

  const metrics = $('metrics');
  clear(metrics);
  const counts = payload.summary.counts;
  [
    ['added', 'added', counts.added],
    ['removed', 'removed', counts.removed],
    ['modified', 'modified', counts.modified],
    ['unchanged', 'unchanged', counts.unchanged],
  ].forEach(([key, cls, value]) => {
    const box = el('div', `metric metric-${cls}`);
    box.appendChild(el('b', null, String(value)));
    box.appendChild(el('span', null, key));
    metrics.appendChild(box);
  });

  const extra = [
    ['clauses, older', payload.clause_counts.base],
    ['clauses, newer', payload.clause_counts.compare],
    ['ambiguous', payload.summary.ambiguous_matches],
    ['renumbered', payload.summary.renumbered_matches],
    ['reordered', payload.summary.reordered_matches],
    ['mean similarity', fmtScore(payload.summary.similarity_mean)],
  ];
  extra.forEach(([label, value]) => {
    const box = el('div', 'metric');
    box.appendChild(el('b', null, String(value)));
    box.appendChild(el('span', null, label));
    metrics.appendChild(box);
  });

  $('similarity-note').textContent = payload.summary.similarity_mean_note;
  $('comparison-id').textContent =
    `comparison_id ${payload.comparison_id} · method ${payload.segmentation_method} · ` +
    `thresholds ${JSON.stringify(payload.config)}`;
}

/* ------------------------------------------------------------------ changes */

function confidencePillKind(confidence) {
  if (confidence === 'high') return 'good';
  if (confidence === 'medium') return 'warn';
  return 'low';
}

/** Build the word-level diff as real elements, with the text in text nodes. */
function renderDiff(runs, which) {
  const wrap = el('div', 'clause-text');
  runs.forEach((run) => {
    const text = which === 'old' ? run.old : run.new;
    if (!text) return;
    if (run.op === 'equal') {
      wrap.appendChild(document.createTextNode(text));
      return;
    }
    const isDeletion = which === 'old';
    const span = el('span', isDeletion ? 'd-delete' : 'd-insert');
    span.appendChild(document.createTextNode(text));
    wrap.appendChild(span);
  });
  return wrap;
}

function renderSide(change, which) {
  const payload = change[which];
  const side = el('div', 'side');
  side.appendChild(
    el('div', 'side-label', which === 'old' ? 'Older issue' : 'Newer issue')
  );

  if (!payload) {
    side.appendChild(
      el(
        'div',
        'side-absent',
        which === 'old'
          ? 'No matching clause was found in the older issue.'
          : 'No matching clause was found in the newer issue.'
      )
    );
    return side;
  }

  const meta = el('div', 'side-meta');
  meta.appendChild(document.createTextNode(payload.clause_id));
  if (payload.clause_number) {
    meta.appendChild(el('span', 'sep', '·'));
    meta.appendChild(document.createTextNode(`clause ${payload.clause_number}`));
  }
  meta.appendChild(el('span', 'sep', '·'));
  meta.appendChild(
    document.createTextNode(
      payload.page_start === payload.page_end
        ? `page ${payload.page_start}`
        : `pages ${payload.page_start}–${payload.page_end}`
    )
  );
  meta.appendChild(el('span', 'sep', '·'));
  meta.appendChild(document.createTextNode(payload.segment_type));
  side.appendChild(meta);

  if (change.word_diff && change.old && change.new) {
    side.appendChild(renderDiff(change.word_diff, which));
  } else {
    side.appendChild(el('div', 'clause-text', payload.text || ''));
  }

  if (payload.entities && payload.entities.length) {
    const tags = el('div', 'entity-tags');
    payload.entities.forEach((entity) => {
      tags.appendChild(el('span', 'entity-tag', `${entity.label}: ${entity.text}`));
    });
    side.appendChild(tags);
  }

  return side;
}

function renderChange(change) {
  const card = el('div', `change change-${change.change_type}`);

  const head = el('div', 'change-head');
  head.appendChild(el('span', 'change-title', change.change_type.toUpperCase()));
  if (change.similarity !== null && change.similarity !== undefined) {
    head.appendChild(pill(`similarity ${fmtScore(change.similarity)}`, 'accent'));
  } else {
    head.appendChild(pill('no counterpart', 'muted'));
  }
  head.appendChild(
    pill(`confidence ${change.confidence}`, confidencePillKind(change.confidence))
  );
  if (change.match_basis) head.appendChild(pill(change.match_basis, 'muted'));
  if (change.renumbered) head.appendChild(pill('renumbered', 'muted'));
  if (change.reordered) head.appendChild(pill('reordered', 'muted'));
  if (change.ambiguous) head.appendChild(pill('ambiguous match', 'bad'));
  head.appendChild(el('span', 'spacer'));
  head.appendChild(el('span', 'mono', change.change_id));
  card.appendChild(head);

  const sides = el('div', 'sides');
  sides.appendChild(renderSide(change, 'old'));
  sides.appendChild(renderSide(change, 'new'));

  if (change.explanation) {
    const note = el('div', 'explanation');
    note.appendChild(el('strong', null, 'What changed: '));
    note.appendChild(document.createTextNode(change.explanation));
    if (change.diff_stats) {
      note.appendChild(
        el(
          'div',
          'muted small',
          `${change.diff_stats.words_added} words added, ` +
          `${change.diff_stats.words_removed} removed, ` +
          `${change.diff_stats.words_kept} kept · token overlap ` +
          `${fmtScore(change.diff_stats.token_overlap)}`
        )
      );
    }
    if (change.ambiguous && change.runner_up_clause_id) {
      note.appendChild(
        el(
          'div',
          'muted small',
          `The matcher nearly chose ${change.runner_up_clause_id} instead ` +
          `(${fmtScore(change.runner_up_similarity)}). Treat the pairing itself ` +
          `as unconfirmed.`
        )
      );
    }
    (change.notes || []).forEach((text) => {
      note.appendChild(el('div', 'muted small', text));
    });
    sides.appendChild(note);
  }

  if (change.entity_deltas && change.entity_deltas.length) {
    const block = el('div', 'deltas');
    const table = el('table');
    const header = el('tr');
    ['what', 'was', 'now'].forEach((label) => header.appendChild(el('th', null, label)));
    table.appendChild(header);
    change.entity_deltas.forEach((delta) => {
      const row = el('tr');
      row.appendChild(el('td', null, delta.friendly_label));
      row.appendChild(el('td', 'was', delta.old.length ? delta.old.join(', ') : 'not stated'));
      row.appendChild(el('td', 'now', delta.new.length ? delta.new.join(', ') : 'not stated'));
      table.appendChild(row);
    });
    block.appendChild(table);
    sides.appendChild(block);
  }

  const evidenceRow = el('div', 'evidence-row');
  ['old', 'new'].forEach((which) => {
    const payload = change[which];
    if (!payload) return;
    const status = payload.evidence.status;
    const button = el(
      'button',
      'ghost',
      `${which === 'old' ? 'Older' : 'Newer'} source · evidence ${status}`
    );
    button.addEventListener('click', () => showEvidence(change, which));
    evidenceRow.appendChild(button);
    if (status !== 'resolved') {
      evidenceRow.appendChild(pill(`${which}: ${status}`, 'bad'));
    }
  });
  sides.appendChild(evidenceRow);

  card.appendChild(sides);
  return card;
}

function renderChanges(payload, total) {
  const host = $('changes');
  clear(host);
  if (!payload.items.length) {
    host.appendChild(
      el(
        'div',
        'note note-muted',
        'No change matches these filters. That is an answer, not an error — ' +
        'try enabling "unchanged", or widening the similarity range.'
      )
    );
    $('pager').hidden = true;
    $('changes-count').textContent = `0 of ${total} shown`;
    return;
  }
  payload.items.forEach((change) => host.appendChild(renderChange(change)));
  $('changes-count').textContent =
    `${payload.items.length} of ${total} change(s) shown` +
    (payload.total_unfiltered !== total
      ? ` · ${payload.total_unfiltered} before filtering`
      : '');
  const more = payload.items.length < total;
  $('pager').hidden = !more;
  $('pager-state').textContent = more
    ? `${total - payload.items.length} more`
    : '';
}

/* ----------------------------------------------------------------- evidence */

function showEvidence(change, which) {
  const payload = change[which];
  const evidence = payload.evidence;
  $('evidence-title').textContent =
    `${which === 'old' ? 'Older' : 'Newer'} issue — ${payload.clause_id}`;

  const body = $('evidence-body');
  clear(body);

  const statusNote = el(
    'div',
    `note ${evidence.status === 'resolved' ? 'note-good' : 'note-bad'}`,
    evidence.status === 'resolved'
      ? 'Resolved: the stored character offsets were sliced out of the stored ' +
        'page text and the result matches this clause. The quote below is ' +
        'verbatim from the document.'
      : `Evidence is ${evidence.status}. No verbatim quote is offered, because ` +
        'a citation that cannot be checked must not be displayed as if it could.'
  );
  body.appendChild(statusNote);

  if (evidence.problems && evidence.problems.length) {
    const list = el('ul', 'problems');
    evidence.problems.forEach((problem) => list.appendChild(el('li', null, problem)));
    body.appendChild(list);
  }

  const identity = el('div', 'evidence-block');
  identity.appendChild(el('h4', null, 'Where this came from'));
  const list = el('dl');
  [
    ['document', evidence.document_id],
    ['version', evidence.version_id],
    ['clause', evidence.clause_id],
    ['pages', `${evidence.page_start}–${evidence.page_end}`],
    ['offsets into', evidence.offset_basis],
  ].forEach(([term, value]) => {
    list.appendChild(el('dt', null, term));
    list.appendChild(el('dd', null, value));
  });
  identity.appendChild(list);
  body.appendChild(identity);

  (evidence.spans || []).forEach((span) => {
    const block = el('div', 'evidence-block');
    block.appendChild(
      el(
        'h4',
        null,
        `Page ${span.page_number}, characters ${span.char_start}–${span.char_end}` +
        (span.resolved ? '' : ' (did not resolve)')
      )
    );
    if (span.quote) {
      block.appendChild(el('div', 'quote', span.quote));
    } else {
      block.appendChild(
        el('div', 'muted small', 'This span could not be sliced from the page.')
      );
    }
    body.appendChild(block);
  });

  const full = el('div', 'evidence-block');
  full.appendChild(el('h4', null, 'Stored clause text'));
  full.appendChild(el('div', 'clause-text', payload.text || ''));
  body.appendChild(full);

  $('evidence').hidden = false;
}

/* --------------------------------------------------------------------- wire */

function wire() {
  $('api-base').value = state.apiBase;
  $('api-base').addEventListener('change', async () => {
    state.apiBase = $('api-base').value.trim() || DEFAULT_API;
    if (await checkHealth()) loadOptions();
  });

  // Changing *what is being compared* invalidates the result on screen.
  // Changing a *filter* re-runs instead (below): a filter still describes the
  // same pair, so there is something to re-request.
  $('document').addEventListener('change', () => {
    invalidateComparison();
    onDocumentChange();
  });
  $('base-version').addEventListener('change', () => {
    invalidateComparison();
    describePair();
  });
  $('compare-version').addEventListener('change', () => {
    invalidateComparison();
    describePair();
  });

  $('swap').addEventListener('click', () => {
    const base = $('base-version');
    const compare = $('compare-version');
    const held = base.value;
    base.value = compare.value;
    compare.value = held;
    invalidateComparison();
    describePair();
  });

  $('run').addEventListener('click', () => runComparison(0));
  $('more').addEventListener('click', () =>
    runComparison(state.current ? state.current.items.length : 0)
  );

  // A filter change re-runs on the backend. Re-filtering in the browser would
  // mean the page showed a subset of one request while claiming to show the
  // result of another.
  document.querySelectorAll('.ctype, .conf').forEach((box) =>
    box.addEventListener('change', () => state.current && runComparison(0))
  );
  ['min-sim', 'max-sim', 'only-ambiguous', 'show-diff', 'show-entities'].forEach((id) =>
    $(id).addEventListener('change', () => state.current && runComparison(0))
  );
  $('method').addEventListener('change', () => state.current && runComparison(0));

  $('evidence-close').addEventListener('click', () => {
    $('evidence').hidden = true;
  });
  document.addEventListener('keydown', (event) => {
    if (event.key === 'Escape') $('evidence').hidden = true;
  });
}

async function start() {
  wire();
  if (await checkHealth()) {
    await loadOptions();
  } else {
    showCorpusNote(
      'The backend is not reachable. Start it with: .venv\\Scripts\\python -m ' +
      'uvicorn backend.app.api.main:app --reload',
      'note-bad'
    );
  }
}

start();
