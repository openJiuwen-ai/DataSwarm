CREATE TABLE IF NOT EXISTS "dw_knowledge_events" (
  "event_id" TEXT PRIMARY KEY,
  "workspace_id" TEXT NOT NULL,
  "asset_id" TEXT NOT NULL,
  "action" TEXT NOT NULL,
  "actor" TEXT NOT NULL,
  "payload_json" TEXT NOT NULL,
  "created_at" TEXT NOT NULL
);
