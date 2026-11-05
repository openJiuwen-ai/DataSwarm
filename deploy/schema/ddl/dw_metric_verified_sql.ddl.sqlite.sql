CREATE TABLE IF NOT EXISTS "dw_metric_verified_sql" (
  "verified_id" TEXT PRIMARY KEY,
  "workspace_id" TEXT NOT NULL,
  "revision_ids_json" TEXT,
  "query_spec_digest" TEXT NOT NULL,
  "sql_text" TEXT NOT NULL,
  "dialect" TEXT,
  "scope_json" TEXT,
  "schema_snapshot_id" TEXT,
  "release_id" TEXT,
  "confirmed_by" TEXT,
  "confirmed_at" TEXT,
  "hit_count" INTEGER DEFAULT 0,
  "created_at" TEXT NOT NULL
);
