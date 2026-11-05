CREATE TABLE IF NOT EXISTS "dw_metric_bindings" (
  "binding_id" TEXT PRIMARY KEY,
  "revision_id" TEXT NOT NULL,
  "workspace_id" TEXT NOT NULL,
  "datasource_alias" TEXT,
  "datasource_ref" TEXT,
  "source_table" TEXT,
  "column_map_json" TEXT,
  "verify_status" TEXT DEFAULT 'unbound',
  "verify_detail" TEXT,
  "verified_at" TEXT,
  "created_at" TEXT NOT NULL
);
