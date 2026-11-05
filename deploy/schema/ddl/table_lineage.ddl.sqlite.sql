CREATE TABLE IF NOT EXISTS "table_lineage" (
  "lineage_id"     TEXT,
  "plan_id"        TEXT,
  "source_db"      TEXT,
  "source_table"   TEXT,
  "target_db"      TEXT,
  "target_table"   TEXT,
  "join_key"       TEXT,
  "lineage_type"   TEXT,
  "transformation" TEXT,
  "task_id"        TEXT,
  "business_ref"   TEXT,
  "created_at"     TEXT,
  PRIMARY KEY ("lineage_id")
);
