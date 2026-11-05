CREATE TABLE IF NOT EXISTS "dw_semantic_runs" (
  "run_id" TEXT PRIMARY KEY,
  "source_id" TEXT NOT NULL,
  "trigger_type" TEXT NOT NULL,
  "status" TEXT NOT NULL,
  "scope_json" TEXT,
  "collector_version" TEXT,
  "rule_version" TEXT,
  "base_snapshot_id" TEXT,
  "current_snapshot_id" TEXT,
  "semantica_stage_id" TEXT,
  "semantica_version" TEXT,
  "started_at" TEXT NOT NULL,
  "finished_at" TEXT,
  "error_json" TEXT,
  "created_at" TEXT NOT NULL
);
