CREATE INDEX IF NOT EXISTS "idx_metric_vsql_ws_digest" ON "dw_metric_verified_sql"("workspace_id", "query_spec_digest");
