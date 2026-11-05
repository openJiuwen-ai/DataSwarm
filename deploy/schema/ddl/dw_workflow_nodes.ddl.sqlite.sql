CREATE TABLE IF NOT EXISTS "dw_workflow_nodes" (
  "node_id" TEXT,
  "workflow_id" TEXT,
  "task_id" TEXT,
  "group_name" TEXT,
  "node_name" TEXT,
  "upstream_nodes" TEXT,
  "wait_review" INTEGER,
  "order_index" INTEGER,
  "created_at" TEXT,
  PRIMARY KEY ("node_id")
);
