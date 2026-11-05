CREATE TABLE IF NOT EXISTS "dw_workflows" (
  "workflow_id" TEXT,
  "name" TEXT,
  "description" TEXT,
  "status" TEXT,
  "enabled" INTEGER,
  "schedule_type" TEXT,
  "schedule_cron" TEXT,
  "schedule_once_at" TEXT,
  "owner" TEXT,
  "created_at" TEXT,
  "updated_at" TEXT,
  PRIMARY KEY ("workflow_id")
);
