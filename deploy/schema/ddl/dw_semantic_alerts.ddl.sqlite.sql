CREATE TABLE IF NOT EXISTS "dw_semantic_alerts" (
  "alert_id" TEXT PRIMARY KEY,
  "event_id" TEXT NOT NULL,
  "source_id" TEXT NOT NULL,
  "metric_id" TEXT,
  "severity" TEXT NOT NULL,
  "message" TEXT NOT NULL,
  "status" TEXT NOT NULL DEFAULT 'open',
  "created_at" TEXT NOT NULL,
  "resolved_at" TEXT
);
