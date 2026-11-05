CREATE TABLE IF NOT EXISTS "dw_knowledge_documents" (
  "version_id" TEXT PRIMARY KEY,
  "workspace_id" TEXT NOT NULL,
  "document_id" TEXT NOT NULL,
  "source_id" TEXT NOT NULL DEFAULT '',
  "name" TEXT NOT NULL,
  "sha256" TEXT NOT NULL,
  "status" TEXT NOT NULL DEFAULT 'candidate',
  "payload_json" TEXT NOT NULL,
  "created_at" TEXT NOT NULL,
  "updated_at" TEXT NOT NULL
);
