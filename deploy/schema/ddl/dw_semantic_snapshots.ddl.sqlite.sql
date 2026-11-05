CREATE TABLE IF NOT EXISTS "dw_semantic_snapshots" (
  "snapshot_id" TEXT PRIMARY KEY,
  "run_id" TEXT NOT NULL,
  "source_id" TEXT NOT NULL,
  "status" TEXT NOT NULL,
  "scope_json" TEXT,
  "snapshot_json" TEXT NOT NULL,
  "object_count" INTEGER NOT NULL DEFAULT 0,
  "relation_count" INTEGER NOT NULL DEFAULT 0,
  "semantica_version" TEXT,
  "published_at" TEXT,
  "created_at" TEXT NOT NULL
);
