CREATE TABLE IF NOT EXISTS "plan_table" (
  "plan_id"          TEXT,
  "table_name"       TEXT,
  "layer"            TEXT,
  "ddl_sql"          TEXT,
  "etl_sql"          TEXT,
  "source_tables"    TEXT,
  "row_count"        INTEGER,
  "status"           TEXT,
  "created_at"       TEXT,
  "updated_at"       TEXT,
  "reuse_of_plan_id" TEXT,
  PRIMARY KEY ("plan_id", "table_name")
);
