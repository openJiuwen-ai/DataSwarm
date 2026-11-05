CREATE TABLE IF NOT EXISTS "dw_metric_defs" (
  "metric_id" TEXT PRIMARY KEY,
  "workspace_id" TEXT NOT NULL,
  "metric_key" TEXT NOT NULL,
  "name" TEXT NOT NULL,
  "description" TEXT,
  "owner" TEXT,
  "status" TEXT DEFAULT 'draft',
  "current_revision_id" TEXT,
  "created_at" TEXT NOT NULL,
  "updated_at" TEXT NOT NULL
);
