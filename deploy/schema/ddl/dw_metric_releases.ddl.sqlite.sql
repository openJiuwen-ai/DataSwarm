CREATE TABLE IF NOT EXISTS "dw_metric_releases" (
  "release_id" TEXT PRIMARY KEY,
  "workspace_id" TEXT NOT NULL,
  "label" TEXT,
  "status" TEXT DEFAULT 'draft',
  "revision_ids_json" TEXT,
  "manifest_json" TEXT,
  "checksum" TEXT,
  "projection_graph" TEXT,
  "projection_status" TEXT DEFAULT 'pending',
  "error_code" TEXT,
  "error_message" TEXT,
  "projected_at" TEXT,
  "published_at" TEXT,
  "published_by" TEXT,
  "created_at" TEXT NOT NULL
);
