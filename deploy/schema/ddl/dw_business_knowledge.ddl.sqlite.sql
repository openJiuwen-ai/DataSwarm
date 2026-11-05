CREATE TABLE IF NOT EXISTS "dw_business_knowledge" (
  "asset_id" TEXT PRIMARY KEY,
  "workspace_id" TEXT NOT NULL,
  "asset_type" TEXT NOT NULL,
  "title" TEXT NOT NULL,
  "content" TEXT NOT NULL,
  "scope_json" TEXT NOT NULL DEFAULT '{}',
  "source_json" TEXT NOT NULL DEFAULT '{}',
  "status" TEXT NOT NULL DEFAULT 'candidate',
  "revision" INTEGER NOT NULL DEFAULT 1,
  "owner" TEXT NOT NULL DEFAULT '',
  "created_at" TEXT NOT NULL,
  "updated_at" TEXT NOT NULL,
  "published_at" TEXT NOT NULL DEFAULT ''
);
