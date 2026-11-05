CREATE TABLE IF NOT EXISTS "dw_semantic_profile" (
  "column_id" TEXT PRIMARY KEY,
  "source_id" TEXT NOT NULL,
  "table_id" TEXT NOT NULL,
  "null_ratio" REAL,
  "distinct_count" INTEGER,
  "top_k_json" TEXT,
  "is_sensitive" INTEGER NOT NULL DEFAULT 0,
  "sensitivity_class" TEXT,
  "profile_version" TEXT,
  "observed_at" TEXT NOT NULL
);
