-- NiyamSetu — public_id allocation
--
-- 0001 requires every citable row to carry a `public_id` matching a fixed
-- shape ('DOC-0001', 'VER-0001', 'CLS-0000123'), because CLAUDE.md §12 says
-- API payloads carry those strings and never database integers. It does not
-- say who allocates them.
--
-- The obvious answer — derive them from the identity `id` — does not work as a
-- generated column: a GENERATED column cannot reference the identity value of
-- its own row. The next idea, INSERT ... RETURNING id followed by an UPDATE,
-- works but doubles the statement count for all 501 segments and leaves a
-- window in which the row exists with a placeholder public_id.
--
-- So allocation gets its own sequences and column defaults. The loader then
-- omits `public_id` entirely and PostgreSQL assigns it, inside the same
-- transaction as the row, with no second statement and no placeholder.
--
-- Note these are *separate* sequences from the identity columns, deliberately.
-- The public_id is the project's external, citable identifier; coupling it to
-- the physical primary key would mean a future partition, re-key or backfill
-- silently renumbered every citation a teammate had already written down.
--
-- Zero padding: lpad to the width 0001's CHECK constraints require — 4 digits
-- for documents and versions, 7 for segments. A sequence running past the pad
-- width simply produces a longer string, which the '{4,}' / '{7,}' quantifiers
-- still accept, so this does not become a ceiling at 9999.

BEGIN;

CREATE SEQUENCE documents_public_id_seq AS BIGINT START WITH 1 INCREMENT BY 1;
CREATE SEQUENCE versions_public_id_seq  AS BIGINT START WITH 1 INCREMENT BY 1;
CREATE SEQUENCE segments_public_id_seq  AS BIGINT START WITH 1 INCREMENT BY 1;

ALTER TABLE documents
    ALTER COLUMN public_id
    SET DEFAULT 'DOC-' || lpad(nextval('documents_public_id_seq')::text, 4, '0');

ALTER TABLE document_versions
    ALTER COLUMN public_id
    SET DEFAULT 'VER-' || lpad(nextval('versions_public_id_seq')::text, 4, '0');

ALTER TABLE segments
    ALTER COLUMN public_id
    SET DEFAULT 'CLS-' || lpad(nextval('segments_public_id_seq')::text, 7, '0');

COMMENT ON SEQUENCE documents_public_id_seq IS
    'Allocates documents.public_id. Separate from the identity column so a '
    'citation written down by a teammate is never renumbered by a re-key.';

COMMIT;
