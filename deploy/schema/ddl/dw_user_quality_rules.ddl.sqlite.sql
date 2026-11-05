CREATE TABLE IF NOT EXISTS "dw_user_quality_rules" (
  "rule_id" TEXT,
  "rule_name" TEXT,
  "db_name" TEXT,
  "target_table" TEXT,
  "target_column" TEXT,
  "rule_type" TEXT,
  "rule_config_json" TEXT,
  "check_sql" TEXT,
  "source_session_id" TEXT,
  "enabled" INTEGER,
  "created_at" TEXT,
  "updated_at" TEXT,
  PRIMARY KEY ("rule_id")
);
