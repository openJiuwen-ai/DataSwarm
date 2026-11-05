CREATE TABLE IF NOT EXISTS "dw_layer_registry" (
  "table_name" TEXT,
  "layer" TEXT,
  "layer_comment" TEXT,
  "upstream_tables" TEXT,
  PRIMARY KEY ("table_name")
);
