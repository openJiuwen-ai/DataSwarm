CREATE INDEX IF NOT EXISTS "idx_metric_qlog_ws" ON "dw_metric_query_log"("workspace_id", "created_at");
