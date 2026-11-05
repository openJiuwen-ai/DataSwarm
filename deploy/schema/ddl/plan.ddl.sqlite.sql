CREATE TABLE IF NOT EXISTS "plan" (
  "plan_id"       TEXT,
  "plan_name"     TEXT,
  "biz_database"  TEXT,
  "biz_domain"    TEXT,
  "status"        TEXT,
  "source_type"   TEXT,
  "source_config"  TEXT,
  "layer_config"   TEXT,
  "purpose"        TEXT,
  "content_json"   TEXT,
  "plan_file_path" TEXT,
  "created_at"     TEXT,
  "updated_at"    TEXT,
  "created_by"    TEXT,
  PRIMARY KEY ("plan_id")
);
