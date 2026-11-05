CREATE TABLE IF NOT EXISTS "dw_ontology_terms" (
  "term_id" TEXT PRIMARY KEY,
  "name" TEXT NOT NULL,
  "definition" TEXT NOT NULL,
  "synonyms" TEXT NOT NULL DEFAULT '[]',
  "status" TEXT NOT NULL DEFAULT 'active',
  "owner" TEXT NOT NULL DEFAULT '',
  "source" TEXT NOT NULL DEFAULT 'manual',
  "tags" TEXT NOT NULL DEFAULT '',
  "fingerprint" TEXT NOT NULL DEFAULT '',
  "created_at" TEXT NOT NULL,
  "updated_at" TEXT NOT NULL
);
