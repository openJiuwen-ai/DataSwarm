CREATE TABLE IF NOT EXISTS "dw_datasources" (
  "source_id" TEXT PRIMARY KEY,
  "source_type" TEXT NOT NULL,
  "name" TEXT,
  "description" TEXT,
  "config" TEXT NOT NULL,
  "db_ref" TEXT NOT NULL,
  "engine" TEXT NOT NULL DEFAULT 'sqlalchemy',
  "enabled" INTEGER NOT NULL DEFAULT 1,
  "config_version" INTEGER NOT NULL DEFAULT 1,
  "created_at" TEXT NOT NULL,
  "updated_at" TEXT NOT NULL
);
