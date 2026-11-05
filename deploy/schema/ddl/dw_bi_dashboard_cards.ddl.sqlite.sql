CREATE TABLE IF NOT EXISTS "dw_bi_dashboard_cards" (
  "card_id" TEXT PRIMARY KEY,
  "dashboard_id" TEXT NOT NULL,
  "metric_id" TEXT,
  "title" TEXT NOT NULL,
  "card_type" TEXT DEFAULT 'metric',
  "chart_spec" TEXT,
  "echarts_option" TEXT,
  "sql_query" TEXT,
  "datasource_ref" TEXT,
  "sparkline_sql" TEXT,
  "grid_pos" TEXT DEFAULT '{}',
  "display_format" TEXT,
  "trend_direction" TEXT,
  "order_index" INTEGER DEFAULT 0,
  "created_at" TEXT NOT NULL,
  "updated_at" TEXT NOT NULL
);
