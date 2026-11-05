CREATE TABLE IF NOT EXISTS "dw_semantic_change_events" (
  "event_id" TEXT PRIMARY KEY,
  "run_id" TEXT NOT NULL,
  "source_id" TEXT NOT NULL,
  "object_id" TEXT NOT NULL,
  "object_kind" TEXT NOT NULL,
  "change_type" TEXT NOT NULL,
  "severity" TEXT NOT NULL,
  "old_fingerprint" TEXT,
  "new_fingerprint" TEXT,
  "impact_json" TEXT,
  "status" TEXT NOT NULL DEFAULT 'open',
  "created_at" TEXT NOT NULL,
  "resolved_at" TEXT
);
