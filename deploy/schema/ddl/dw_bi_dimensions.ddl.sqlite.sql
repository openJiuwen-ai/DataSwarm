CREATE TABLE IF NOT EXISTS "dw_bi_dimensions" (
  "dimension_id" TEXT PRIMARY KEY,
  "name" TEXT NOT NULL,
  "label" TEXT,
  "description" TEXT,
  "dim_type" TEXT DEFAULT 'category',
  "value_source" TEXT DEFAULT 'manual',
  "value_sql" TEXT,
  "value_list" TEXT,
  "datasource_ref" TEXT,
  "created_at" TEXT NOT NULL,
  "updated_at" TEXT NOT NULL
);
