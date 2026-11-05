CREATE TABLE IF NOT EXISTS "dw_semantic_join_status" (
  "join_node_id" TEXT PRIMARY KEY,
  "source_id"    TEXT NOT NULL,
  "status"       TEXT NOT NULL,
  "note"         TEXT,
  "confirmed_by" TEXT NOT NULL,
  "confirmed_at" TEXT NOT NULL
);
