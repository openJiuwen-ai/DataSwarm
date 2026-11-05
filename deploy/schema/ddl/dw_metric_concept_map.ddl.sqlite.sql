CREATE TABLE IF NOT EXISTS "dw_metric_concept_map" (
  "mapping_id" TEXT PRIMARY KEY,
  "workspace_id" TEXT NOT NULL,
  "revision_id" TEXT NOT NULL,
  "relation_type" TEXT NOT NULL,
  "target_ref" TEXT NOT NULL,
  "status" TEXT DEFAULT 'candidate',
  "evidence" TEXT,
  "confirmed_by" TEXT,
  "confirmed_at" TEXT,
  "created_at" TEXT NOT NULL
);
