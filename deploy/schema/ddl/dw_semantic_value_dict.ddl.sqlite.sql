CREATE TABLE IF NOT EXISTS "dw_semantic_value_dict" (
  "value_id"    TEXT PRIMARY KEY,
  "source_id"   TEXT NOT NULL,
  "snapshot_id" TEXT NOT NULL,
  "column_id"   TEXT NOT NULL,
  "table_id"    TEXT NOT NULL,
  "value_text"  TEXT NOT NULL,
  "value_norm"  TEXT NOT NULL
);
