-- NiyamSetu — initial PostgreSQL schema
--
-- STATUS: PROPOSAL, NOT APPLIED.  See backend/app/db/README.md.
--
-- This file has never been run. No PostgreSQL server has been reached from
-- this repository, no database has been created, and neither SQLAlchemy nor
-- psycopg2 is a project dependency yet. The standing stop condition in
-- CLAUDE.md §24 holds: Phase 3 waits on the owner reviewing Phase 2's
-- segmentation output and on answers to D-8 (is PostgreSQL reachable on all
-- five machines) and D-10 (which documents form the corpus).
--
-- It is written now, and committed, because CLAUDE.md §12 and
-- docs/pdf-extraction.md §12 both argue for designing the segment tables
-- against *real* segments rather than a sketch — and the real segments now
-- exist. Every design note below cites the measurement that drove it.
--
-- Written: 2026-11-12, against the artefacts produced by
--   python -m backend.app.document_processing.cli --manifest data/manifest.csv
--   python -m backend.app.document_processing.segmentation_cli --all
-- i.e. 4 versions, 57 pages, 501 segments, 151 removed header lines.
--
-- Target: PostgreSQL 14+ (needs GENERATED ALWAYS AS IDENTITY and JSONB).
-- Access layer: SQLAlchemy Core plus these plain .sql files, applied by a
-- small runner script. Not Alembic — decided (D-4) and confirmed by the owner.
--
-- ---------------------------------------------------------------------------
-- WHAT CHANGED FROM THE CLAUDE.md §11.3 SKETCH, AND WHY
-- ---------------------------------------------------------------------------
-- The sketch predates segmentation. Running it produced seven requirements it
-- did not anticipate:
--
--  1. segments.parent_id  — clause hierarchy. Measured depths 0..4 across the
--     corpus, and 241 of 254 segments in the capital-adequacy document have a
--     parent. Person 2 cannot render a clause tree and Person 4 cannot align
--     sub-clauses without this.
--  2. segments.segment_type — the sketch assumed every segment is a clause.
--     Reality: clause 355, paragraph 138, preamble 4, annexure 3, heading 1.
--     Text that could not be attributed is kept as preamble/unclassified
--     rather than discarded, so the type has to be stored to tell them apart.
--  3. segments.level and marker_style — needed to report per-style accuracy
--     (Experiment B) and to explain a nesting decision after the fact.
--  4. segment_pages.line_index_start/end — the sketch had character offsets
--     only. Line indices are what make a removed header line locatable, and
--     what the preview frontend highlights with.
--  5. document_versions.offset_basis — a real contract change, see the note on
--     that column. The sketch assumed offsets into raw text; the pipeline
--     produces offsets into normalised text.
--  6. page_removed_lines — an entity the sketch has no equivalent for.
--     Header/footer removal deletes text, and the project's rule is that
--     source evidence is never lost, so the removal must be reversible *from
--     the database*, not only from a JSON file.
--  7. segments.text_search — a stored tsvector rather than an expression
--     index, so Person 5's keyword baseline has a stable column to query.
--
-- ---------------------------------------------------------------------------

BEGIN;

-- ---------------------------------------------------------------------------
-- Migration bookkeeping
-- ---------------------------------------------------------------------------
-- A table rather than a file on disk, so "which migrations has this database
-- had" is answerable from the database itself. The runner inserts a row inside
-- the same transaction as the migration, so a failed migration leaves no row
-- and is retried rather than skipped.

CREATE TABLE IF NOT EXISTS schema_migrations (
    filename    TEXT        PRIMARY KEY,
    checksum    CHAR(64)    NOT NULL,   -- SHA-256 of the file as applied
    applied_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ---------------------------------------------------------------------------
-- documents — the logical policy identity
-- ---------------------------------------------------------------------------
-- A policy that has versions. NOT a file and NOT a version (CLAUDE.md §11.1).

CREATE TABLE documents (
    id              BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,

    -- The API-facing identifier. CLAUDE.md §12 requires that payloads carry
    -- these strings and never database integers.
    public_id       TEXT        NOT NULL UNIQUE,

    -- The content-derived fingerprint the pipeline assigns:
    -- 'DOC-' || substr(sha256(document_group), 1, 16). This is how a
    -- re-ingested file is recognised as belonging to an existing document, and
    -- it is why the pipeline never has to guess (D-9). It coexists with
    -- public_id rather than replacing it: the fingerprint is for matching, the
    -- public_id is for citing. See backend/app/document_processing/identity.py.
    fingerprint     TEXT        NOT NULL UNIQUE,

    title           TEXT        NOT NULL,

    -- Nullable, and that is deliberate. One of the four sample documents does
    -- not print its issuing department, and data/README.md §4 is explicit that
    -- a blank is a documented gap while a guess is a silent data error.
    department      TEXT,
    document_type   TEXT,
    reference_no    TEXT,

    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT documents_public_id_shape CHECK (public_id ~ '^DOC-[0-9]{4,}$'),
    CONSTRAINT documents_fingerprint_shape CHECK (fingerprint ~ '^DOC-[0-9a-f]{16}$'),
    CONSTRAINT documents_title_not_blank CHECK (btrim(title) <> ''),

    -- Free text, but constrained to the vocabulary CLAUDE.md §11.3 lists, so a
    -- typo cannot quietly create a new document type that breaks Person 2's
    -- classification labels and Person 5's filters.
    CONSTRAINT documents_type_vocabulary CHECK (
        document_type IS NULL OR document_type IN (
            'notification', 'circular', 'scheme', 'guideline',
            'amendment', 'other'
        )
    )
);

COMMENT ON COLUMN documents.fingerprint IS
    'Content-derived id from identity.document_id_from_group(); used to match a '
    're-ingested file to an existing document. public_id is what APIs cite.';

-- ---------------------------------------------------------------------------
-- document_versions — one published issue of a policy
-- ---------------------------------------------------------------------------

CREATE TABLE document_versions (
    id                      BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    public_id               TEXT        NOT NULL UNIQUE,

    -- 'VER-' || substr(sha256(file bytes), 1, 16). Deterministic, so
    -- re-processing the same PDF is idempotent.
    fingerprint             TEXT        NOT NULL UNIQUE,

    document_id             BIGINT      NOT NULL
                                        REFERENCES documents(id)
                                        ON DELETE RESTRICT,

    -- Human-assigned. The pipeline never infers version order from a
    -- publication date (CLAUDE.md §11.1).
    version_label           TEXT        NOT NULL,
    supersedes_version_id   BIGINT      REFERENCES document_versions(id)
                                        ON DELETE SET NULL,

    publication_date        DATE,       -- nullable: often not printed
    source_url              TEXT,
    retrieved_at            TIMESTAMPTZ,

    original_filename       TEXT        NOT NULL,
    stored_path             TEXT        NOT NULL,
    file_checksum           CHAR(64)    NOT NULL UNIQUE,  -- duplicate detection
    file_size_bytes         BIGINT      NOT NULL,
    page_count              INTEGER     NOT NULL,

    extraction_status       TEXT        NOT NULL,
    extraction_tool         TEXT        NOT NULL,  -- e.g. 'pypdf 6.18.1'

    -- WHICH TEXT THE CHARACTER OFFSETS REFER TO.
    --
    -- A deviation from CLAUDE.md §11.4, which specified offsets into raw text.
    -- The pipeline produces offsets into *normalised* text, because:
    --   * normalization.collapse_blank_lines changes a page's line count, so
    --     raw and normalised text share no line numbering, and mapping between
    --     them needs a per-page offset map that §11.4 puts out of MVP scope;
    --   * the API serves text=normalized by default (§12.3), so offsets on the
    --     same basis as the displayed text are correct for the common path.
    --
    -- Stored per version rather than assumed globally, so a corpus that mixes
    -- the two (--use-raw-text exists) stays self-describing and no consumer has
    -- to guess. THIS COLUMN IS THE CONTRACT POINT THAT NEEDS TEAM SIGN-OFF.
    offset_basis            TEXT        NOT NULL DEFAULT 'normalized_text',

    processing_warnings     JSONB       NOT NULL DEFAULT '[]'::jsonb,
    ingested_at             TIMESTAMPTZ NOT NULL DEFAULT now(),

    UNIQUE (document_id, version_label),

    CONSTRAINT versions_public_id_shape CHECK (public_id ~ '^VER-[0-9]{4,}$'),
    CONSTRAINT versions_fingerprint_shape CHECK (fingerprint ~ '^VER-[0-9a-f]{16}$'),
    CONSTRAINT versions_checksum_shape CHECK (file_checksum ~ '^[0-9a-f]{64}$'),
    CONSTRAINT versions_page_count_positive CHECK (page_count > 0),
    CONSTRAINT versions_size_positive CHECK (file_size_bytes > 0),

    -- 'pending' exists for a row created before extraction runs; the pipeline
    -- itself only ever returns the other three.
    CONSTRAINT versions_status_vocabulary CHECK (
        extraction_status IN ('pending', 'ok', 'partial', 'failed')
    ),
    CONSTRAINT versions_offset_basis_vocabulary CHECK (
        offset_basis IN ('normalized_text', 'original_text')
    ),

    -- A version cannot supersede itself. The data-quality checks found this
    -- worth asserting: a one-row cycle is the easy mistake to make by hand in
    -- the manifest, and it breaks Compare's version ordering.
    CONSTRAINT versions_no_self_supersede CHECK (
        supersedes_version_id IS NULL OR supersedes_version_id <> id
    )
);

COMMENT ON COLUMN document_versions.offset_basis IS
    'Which page text segment_pages.char_start/char_end index: normalized_text '
    '(the pipeline default) or original_text. Deviates from CLAUDE.md 11.4 - '
    'see the inline note. Needs team sign-off.';

COMMENT ON COLUMN document_versions.supersedes_version_id IS
    'Human-asserted version order. Never inferred from publication_date or '
    'title similarity (CLAUDE.md 11.1, D-9). Longer cycles are not expressible '
    'as a CHECK and are caught by evaluation/data_quality.py.';

-- ---------------------------------------------------------------------------
-- pages — page-level extracted text, the unit of provenance
-- ---------------------------------------------------------------------------

CREATE TABLE pages (
    id                  BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    version_id          BIGINT  NOT NULL
                                REFERENCES document_versions(id)
                                ON DELETE CASCADE,

    -- 1-based, matching the PDF's PHYSICAL page order, not any number printed
    -- on the page. Where they differ the discrepancy is a manifest note, never
    -- a correction applied here (data/README.md §6).
    page_number         INTEGER NOT NULL,

    -- Byte-for-byte what the extractor returned. NEVER rewritten; normalisation
    -- is additive (CLAUDE.md §10.2). Enforced by the trigger below rather than
    -- left to convention.
    raw_text            TEXT    NOT NULL,
    normalized_text     TEXT,

    -- The text segmentation actually ran on: normalised, with running headers,
    -- footers and printed page numbers removed. Stored rather than recomputed
    -- so a segment's offsets can be checked against it directly.
    cleaned_text        TEXT,

    char_count          INTEGER NOT NULL,
    word_count          INTEGER NOT NULL,
    is_empty            BOOLEAN NOT NULL,

    -- Heuristic, and reported as one: enough letters/digits to be worth
    -- indexing. False is a signal to look, not a verdict that the page is
    -- broken.
    has_meaningful_text BOOLEAN NOT NULL,

    UNIQUE (version_id, page_number),

    CONSTRAINT pages_number_positive CHECK (page_number >= 1),
    CONSTRAINT pages_char_count_non_negative CHECK (char_count >= 0),
    CONSTRAINT pages_empty_is_consistent CHECK (
        is_empty = (btrim(coalesce(normalized_text, raw_text)) = '')
    )
);

-- raw_text is immutable, enforced rather than trusted. This is the single most
-- important invariant in the project: everything downstream claims to be
-- traceable to text that was never altered.
CREATE OR REPLACE FUNCTION pages_forbid_raw_text_update()
RETURNS TRIGGER AS $$
BEGIN
    IF NEW.raw_text IS DISTINCT FROM OLD.raw_text THEN
        RAISE EXCEPTION
            'pages.raw_text is immutable (page % of version %); normalisation '
            'is additive and must write to normalized_text or cleaned_text',
            OLD.page_number, OLD.version_id;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER pages_raw_text_immutable
    BEFORE UPDATE ON pages
    FOR EACH ROW EXECUTE FUNCTION pages_forbid_raw_text_update();

-- ---------------------------------------------------------------------------
-- page_removed_lines — the header/footer removal audit trail
-- ---------------------------------------------------------------------------
-- No equivalent in the CLAUDE.md §11.3 sketch, and it is not optional.
--
-- Cleaning is the only stage that DELETES text. Its guarantee is that removal
-- is reversible: each line is recorded with the index it occupied, so the
-- original page reconstructs exactly. If that trail lives only in a JSON file,
-- then a database-backed deployment has silently lost source evidence - the
-- one thing CLAUDE.md §24 rule 6 forbids.
--
-- Measured on the corpus: 151 rows across 4 versions (108 for the
-- capital-adequacy document alone: 72 running-header lines + 36 page numbers).

CREATE TABLE page_removed_lines (
    id              BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    page_id         BIGINT  NOT NULL REFERENCES pages(id) ON DELETE CASCADE,

    -- Index into raw_text split on newlines, blank lines included. This
    -- exactness is what makes reinsertion exact.
    line_index      INTEGER NOT NULL,
    line_text       TEXT    NOT NULL,

    reason          TEXT    NOT NULL,

    -- Human-readable justification, e.g. 'repeated at top position 0 on 36/37
    -- pages (97%, threshold 90%)'. A line removed without stated evidence would
    -- be indistinguishable from a bug, so the column is NOT NULL and non-blank.
    evidence        TEXT    NOT NULL,

    UNIQUE (page_id, line_index),

    CONSTRAINT removed_lines_index_non_negative CHECK (line_index >= 0),
    CONSTRAINT removed_lines_evidence_not_blank CHECK (btrim(evidence) <> ''),
    CONSTRAINT removed_lines_reason_vocabulary CHECK (
        reason IN ('repeated_header', 'repeated_footer', 'printed_page_number')
    )
);

-- ---------------------------------------------------------------------------
-- segments — paragraphs and clauses, both methods side by side
-- ---------------------------------------------------------------------------

CREATE TABLE segments (
    id                  BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    public_id           TEXT    NOT NULL UNIQUE,  -- 'CLS-0000123', cited by APIs

    -- 'SEG-' || substr(sha256(version_id|method|ordinal), 1, 16). Stable across
    -- re-segmentation with the same pipeline, which is what stops a re-run
    -- invalidating every committed annotation and citation. Deliberately does
    -- NOT hash the text: a normalisation fix would otherwise change every id.
    fingerprint         TEXT    NOT NULL UNIQUE,

    version_id          BIGINT  NOT NULL
                                REFERENCES document_versions(id)
                                ON DELETE CASCADE,

    segmentation_method TEXT    NOT NULL,

    -- Document order within (version, method). Dense from 0.
    ordinal             INTEGER NOT NULL,

    -- NEW vs the sketch. Reality on this corpus: clause 355, paragraph 138,
    -- preamble 4, annexure 3, heading 1. Unattributable text is preserved as
    -- preamble/unclassified rather than discarded, so the distinction has to be
    -- storable.
    segment_type        TEXT    NOT NULL,

    -- As printed, with no reformatting: '3.2', '(iv)', 'Annexure II'. NULL when
    -- the segment carries no number - which is always true of the paragraph
    -- baseline, by construction. Longest observed: 8 characters.
    clause_number       TEXT,
    heading             TEXT,

    text                TEXT    NOT NULL,

    -- Cheap answer to "where is this clause", for a citation. The precise
    -- answer is segment_pages.
    page_start          INTEGER NOT NULL,
    page_end            INTEGER NOT NULL,

    -- NEW vs the sketch. Clause hierarchy, observed to depth 4; 241 of 254
    -- segments in the longest document have a parent. ON DELETE CASCADE would
    -- silently remove a subtree, so parents are restricted: a delete that would
    -- orphan children must be deliberate.
    parent_id           BIGINT  REFERENCES segments(id) ON DELETE RESTRICT,

    -- Nesting depth implied by the marker style, 0 for top level. Kept
    -- alongside parent_id rather than derived from it: the two can disagree,
    -- and that disagreement is itself diagnostic.
    level               INTEGER NOT NULL DEFAULT 0,

    -- Which pattern matched: numeric_dotted, paren_number, paren_roman,
    -- paren_alpha, annexure, structural, spelled. Needed for per-style accuracy
    -- in Experiment B and to explain a nesting decision afterwards.
    marker_style        TEXT,

    -- '(i)' is both roman one and letter nine. Where sequence context cannot
    -- resolve it the segmenter defaults to roman and sets this flag. 31
    -- occurrences across the corpus. Flagged, never silently resolved.
    clause_number_ambiguous BOOLEAN NOT NULL DEFAULT FALSE,

    -- Reserved for downstream annotation (classification labels, extracted
    -- entities). The backend writes detection details and nothing else;
    -- NLP modules write here only through an agreed mechanism (CLAUDE.md §12.3).
    metadata            JSONB   NOT NULL DEFAULT '{}'::jsonb,

    -- Stored generated column rather than an expression index, so Person 5's
    -- keyword baseline has a stable thing to SELECT and to rank on, and so the
    -- tsvector configuration is recorded in one place. 'english' is wrong for
    -- the Devanagari letterhead text, which is a documented limitation rather
    -- than something to paper over - see the note below.
    text_search         TSVECTOR
                        GENERATED ALWAYS AS (to_tsvector('english', text)) STORED,

    UNIQUE (version_id, segmentation_method, ordinal),

    CONSTRAINT segments_public_id_shape CHECK (public_id ~ '^CLS-[0-9]{7,}$'),
    CONSTRAINT segments_fingerprint_shape CHECK (fingerprint ~ '^SEG-[0-9a-f]{16}$'),
    CONSTRAINT segments_ordinal_non_negative CHECK (ordinal >= 0),
    CONSTRAINT segments_level_non_negative CHECK (level >= 0),
    CONSTRAINT segments_page_range_ordered CHECK (page_end >= page_start),
    CONSTRAINT segments_page_start_positive CHECK (page_start >= 1),

    -- The project's load-bearing rule, as a constraint: no empty segments.
    CONSTRAINT segments_text_not_blank CHECK (btrim(text) <> ''),

    CONSTRAINT segments_method_vocabulary CHECK (
        segmentation_method IN ('paragraph', 'clause_aware')
    ),
    CONSTRAINT segments_type_vocabulary CHECK (
        segment_type IN (
            'paragraph', 'clause', 'heading', 'annexure',
            'preamble', 'unclassified'
        )
    ),

    -- The baseline is the control condition for Experiment B. If it ever
    -- acquired clause numbers or headings the comparison would be meaningless,
    -- so the database refuses them rather than relying on the code.
    CONSTRAINT segments_paragraph_method_has_no_clause_structure CHECK (
        segmentation_method <> 'paragraph'
        OR (clause_number IS NULL AND heading IS NULL AND marker_style IS NULL)
    ),

    CONSTRAINT segments_no_self_parent CHECK (parent_id IS NULL OR parent_id <> id)
);

COMMENT ON COLUMN segments.text_search IS
    'to_tsvector(''english'', text). Wrong stemmer for the Devanagari '
    'letterhead text that survives into some preamble segments, and that is a '
    'recorded limitation: no extractor available to this project reads that '
    'Devanagari correctly anyway (Experiment A section 3).';

COMMENT ON COLUMN segments.fingerprint IS
    'Stable across re-segmentation; keyed on (version, method, ordinal), NOT on '
    'the text, so a normalisation fix does not invalidate every annotation.';

-- ---------------------------------------------------------------------------
-- segment_pages — precise multi-page provenance
-- ---------------------------------------------------------------------------
-- One row per page a segment actually touches. A clause spanning pages 3-4 gets
-- page_start=3, page_end=4 AND two rows here. A spanning clause is never
-- collapsed onto one page (CLAUDE.md §11.4).
--
-- Measured: 45 of 501 segments span a page break.

CREATE TABLE segment_pages (
    segment_id          BIGINT  NOT NULL REFERENCES segments(id) ON DELETE CASCADE,
    page_id             BIGINT  NOT NULL REFERENCES pages(id) ON DELETE CASCADE,

    -- Denormalised from pages.page_number so an ordering query and the
    -- provenance checks do not need the join. Kept honest by the trigger below.
    page_number         INTEGER NOT NULL,

    -- Half-open range into the page text named by
    -- document_versions.offset_basis.
    char_start          INTEGER NOT NULL,
    char_end            INTEGER NOT NULL,

    -- NEW vs the sketch. Line indices are what the preview frontend highlights
    -- with and what makes a removed line locatable relative to a segment.
    line_index_start    INTEGER NOT NULL,
    line_index_end      INTEGER NOT NULL,

    PRIMARY KEY (segment_id, page_number),

    CONSTRAINT segment_pages_number_positive CHECK (page_number >= 1),
    CONSTRAINT segment_pages_char_range_ordered CHECK (char_end >= char_start),
    CONSTRAINT segment_pages_char_start_non_negative CHECK (char_start >= 0),
    CONSTRAINT segment_pages_line_range_ordered CHECK (
        line_index_end >= line_index_start
    )
);

-- Keeps the denormalised page_number equal to the page it references, and the
-- segment's page range consistent with the pages it touches. Both are
-- cross-row invariants that a CHECK cannot express, and both are exactly what
-- evaluation/data_quality.py checks on the JSON artefacts — so the database
-- enforces what the file-based pipeline can only report.
CREATE OR REPLACE FUNCTION segment_pages_validate()
RETURNS TRIGGER AS $$
DECLARE
    actual_page_number INTEGER;
    actual_version_id  BIGINT;
    segment_version_id BIGINT;
    page_limit         INTEGER;
BEGIN
    SELECT p.page_number, p.version_id INTO actual_page_number, actual_version_id
      FROM pages p WHERE p.id = NEW.page_id;

    IF actual_page_number IS DISTINCT FROM NEW.page_number THEN
        RAISE EXCEPTION
            'segment_pages.page_number (%) does not match pages.page_number (%)',
            NEW.page_number, actual_page_number;
    END IF;

    SELECT s.version_id INTO segment_version_id
      FROM segments s WHERE s.id = NEW.segment_id;

    IF segment_version_id IS DISTINCT FROM actual_version_id THEN
        RAISE EXCEPTION
            'segment % belongs to version % but page % belongs to version %',
            NEW.segment_id, segment_version_id, NEW.page_id, actual_version_id;
    END IF;

    SELECT length(
               CASE v.offset_basis
                   WHEN 'original_text' THEN p.raw_text
                   ELSE coalesce(p.normalized_text, p.raw_text)
               END
           )
      INTO page_limit
      FROM pages p
      JOIN document_versions v ON v.id = p.version_id
     WHERE p.id = NEW.page_id;

    IF NEW.char_end > page_limit THEN
        RAISE EXCEPTION
            'segment_pages.char_end (%) is past the end of page % (% characters)',
            NEW.char_end, NEW.page_number, page_limit;
    END IF;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER segment_pages_validated
    BEFORE INSERT OR UPDATE ON segment_pages
    FOR EACH ROW EXECUTE FUNCTION segment_pages_validate();

-- Every segment must have at least one segment_pages row. Postgres cannot
-- express that as a constraint on segments (the child rows do not exist yet at
-- insert time), so it is a DEFERRED constraint trigger: checked at COMMIT,
-- which is exactly when "one version per transaction" (CLAUDE.md §10.2) makes
-- the answer knowable.
-- Checks only the row that fired it, by id. A whole-table scan here would be
-- both slow on every insert and wrong in a way that matters: it would fail a
-- transaction because of some *other* pre-existing bad row, which makes the
-- error message point at the wrong segment and makes a repair impossible
-- without disabling the trigger.
CREATE OR REPLACE FUNCTION segments_require_provenance()
RETURNS TRIGGER AS $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM segment_pages sp WHERE sp.segment_id = NEW.id
    ) THEN
        RAISE EXCEPTION
            'segment % has no segment_pages row; a segment without page '
            'provenance must not be stored', NEW.fingerprint;
    END IF;
    RETURN NULL;
END;
$$ LANGUAGE plpgsql;

CREATE CONSTRAINT TRIGGER segments_provenance_required
    AFTER INSERT OR UPDATE ON segments
    DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW EXECUTE FUNCTION segments_require_provenance();

-- ---------------------------------------------------------------------------
-- ingestion_jobs — processing log
-- ---------------------------------------------------------------------------
-- Present from the start even though ingestion is synchronous for the MVP
-- (D-5). Having the table now means switching to a background task later is
-- additive rather than a migration.

CREATE TABLE ingestion_jobs (
    id              BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    version_id      BIGINT      REFERENCES document_versions(id)
                                ON DELETE SET NULL,
    status          TEXT        NOT NULL,
    started_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at     TIMESTAMPTZ,
    error_message   TEXT,
    stats           JSONB       NOT NULL DEFAULT '{}'::jsonb,

    CONSTRAINT jobs_status_vocabulary CHECK (
        status IN ('running', 'succeeded', 'failed')
    ),
    CONSTRAINT jobs_finished_after_started CHECK (
        finished_at IS NULL OR finished_at >= started_at
    ),
    -- A failed job must say why. An unexplained failure is unactionable.
    CONSTRAINT jobs_failure_has_a_reason CHECK (
        status <> 'failed' OR btrim(coalesce(error_message, '')) <> ''
    )
);

-- ---------------------------------------------------------------------------
-- Indexes
-- ---------------------------------------------------------------------------
-- Chosen from the access patterns the five modules actually have, not
-- speculatively. The corpus is small (4 versions, 501 segments) so none of
-- these matters yet for speed; they are here so the schema documents the
-- intended queries.

-- Library list and filters (Person 2, Person 5).
CREATE INDEX documents_department_idx    ON documents (department);
CREATE INDEX documents_type_idx          ON documents (document_type);

-- Document view: a document's versions, newest first (Person 2, Person 4).
CREATE INDEX versions_document_idx       ON document_versions (document_id);
CREATE INDEX versions_publication_idx    ON document_versions (publication_date DESC NULLS LAST);
CREATE INDEX versions_supersedes_idx     ON document_versions (supersedes_version_id)
                                            WHERE supersedes_version_id IS NOT NULL;

-- Page text pane (Person 2).
CREATE INDEX pages_version_number_idx    ON pages (version_id, page_number);
CREATE INDEX removed_lines_page_idx      ON page_removed_lines (page_id);

-- "Give me this version's clauses by this method, in order" — the single most
-- common query in the project, used by all four consumers.
CREATE INDEX segments_version_method_ordinal_idx
    ON segments (version_id, segmentation_method, ordinal);

-- Clause-tree rendering (Person 2) and sub-clause alignment (Person 4).
CREATE INDEX segments_parent_idx         ON segments (parent_id)
                                            WHERE parent_id IS NOT NULL;

-- Resolve a citation by its printed clause number within a version.
CREATE INDEX segments_clause_number_idx  ON segments (version_id, clause_number)
                                            WHERE clause_number IS NOT NULL;

-- Person 5's keyword-search baseline.
CREATE INDEX segments_text_search_idx    ON segments USING GIN (text_search);

-- "Which clauses are on page N" — the inverse provenance lookup, needed to
-- highlight a page and to validate provenance at scale.
CREATE INDEX segment_pages_page_idx      ON segment_pages (page_id);
CREATE INDEX segment_pages_number_idx    ON segment_pages (page_number);

CREATE INDEX jobs_version_idx            ON ingestion_jobs (version_id);

-- ---------------------------------------------------------------------------
-- Read-only convenience view
-- ---------------------------------------------------------------------------
-- The citation shape CLAUDE.md §12.2 mandates, assembled once here so Ask and
-- Compare cannot drift apart on it. A view rather than a table: it must never
-- be able to disagree with the segments it describes.

CREATE VIEW segment_citations AS
SELECT
    s.public_id        AS clause_id,
    s.fingerprint      AS clause_fingerprint,
    d.public_id        AS document_id,
    v.public_id        AS version_id,
    s.segmentation_method,
    s.segment_type,
    s.clause_number,
    s.heading,
    s.page_start,
    s.page_end,
    s.text,
    v.offset_basis
FROM segments s
JOIN document_versions v ON v.id = s.version_id
JOIN documents d         ON d.id = v.document_id
-- Only text from a completely extracted version is citable. A 'partial' or
-- 'failed' version is visible through the admin path but must not surface in a
-- citation, because its page text may be incomplete.
WHERE v.extraction_status = 'ok';

COMMENT ON VIEW segment_citations IS
    'The CLAUDE.md 12.2 citation shape. Excludes versions whose extraction was '
    'not fully ok, so an incomplete extraction cannot be cited as evidence.';

COMMIT;
