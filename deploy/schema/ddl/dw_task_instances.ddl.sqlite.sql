CREATE TABLE IF NOT EXISTS "dw_task_instances" (
  "instance_id" TEXT,
  "ref_type" TEXT,
  "ref_id" TEXT,
  "trigger_type" TEXT,
  "status" TEXT,
  "started_at" TEXT,
  "finished_at" TEXT,
  "artifact_dir" TEXT,
  "error_message" TEXT,
  "created_at" TEXT,
  PRIMARY KEY ("instance_id")
);
