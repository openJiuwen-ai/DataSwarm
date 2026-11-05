CREATE TABLE IF NOT EXISTS "dw_bi_dashboards" (
  "dashboard_id" TEXT PRIMARY KEY,
  "name" TEXT NOT NULL,
  "description" TEXT,
  "owner" TEXT DEFAULT 'demo',
  "layout_json" TEXT DEFAULT '{}',
  "filter_defaults" TEXT,
  "status" TEXT DEFAULT 'active',
  "source_type" TEXT,
  "source_ref" TEXT,
  "created_at" TEXT NOT NULL,
  "updated_at" TEXT NOT NULL
);
