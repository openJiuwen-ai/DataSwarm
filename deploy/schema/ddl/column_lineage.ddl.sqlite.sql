CREATE TABLE IF NOT EXISTS "column_lineage" (
  "column_lineage_id" TEXT,
  "plan_id"           TEXT,
  "source_db"         TEXT,
  "source_table"      TEXT,
  "source_column"     TEXT,
  "target_db"         TEXT,
  "target_table"      TEXT,
  "target_column"     TEXT,
  "transformation"    TEXT,
  "task_id"           TEXT,
  "business_ref"      TEXT,
  "created_at"        TEXT,
  PRIMARY KEY ("column_lineage_id")
);
