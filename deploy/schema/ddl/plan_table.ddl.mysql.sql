-- 建模计划的明细表设计（reuse_of_plan_id 为运行期幂等 ADD COLUMN 的终态列）
CREATE TABLE IF NOT EXISTS `plan_table` (
  `plan_id`          VARCHAR(64)  NOT NULL COMMENT 'FK -> plan',
  `table_name`       VARCHAR(200) NOT NULL COMMENT 'Fully qualified: bizdb.table_name',
  `layer`            VARCHAR(10)  NOT NULL COMMENT 'ODS|DWD|DWS|ADS',
  `ddl_sql`          TEXT         NULL COMMENT 'Generated DDL for this table',
  `etl_sql`          TEXT         NULL COMMENT 'ETL INSERT INTO ... SELECT for this table',
  `source_tables`    TEXT         NULL COMMENT 'JSON array of upstream fully-qualified table names',
  `row_count`        BIGINT       NULL DEFAULT '0',
  `status`           VARCHAR(20)  NOT NULL DEFAULT 'designed' COMMENT 'designed|ddl_created|data_loaded|etl_done|failed',
  `created_at`       DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `updated_at`       DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `reuse_of_plan_id` VARCHAR(64)  NULL COMMENT '复用既有 plan 时指向原 plan',
  PRIMARY KEY (`plan_id`, `table_name`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
