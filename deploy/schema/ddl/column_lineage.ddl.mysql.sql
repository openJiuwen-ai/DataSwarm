-- 列级血缘边表（活跃表；退役的空表 dw_column_lineage 已于 2026-08-27 从存量库删除且不入真相源；
-- task_id 为 v2 任务驱动统一血缘的终态列）
CREATE TABLE IF NOT EXISTS `column_lineage` (
  `column_lineage_id` VARCHAR(64)  NOT NULL COMMENT 'UUID-style edge identifier',
  `plan_id`           VARCHAR(64)  NOT NULL COMMENT 'FK -> plan',
  `source_db`         VARCHAR(100) NOT NULL,
  `source_table`      VARCHAR(200) NOT NULL,
  `source_column`     VARCHAR(200) NOT NULL,
  `target_db`         VARCHAR(100) NOT NULL,
  `target_table`      VARCHAR(200) NOT NULL,
  `target_column`     VARCHAR(200) NOT NULL,
  `transformation`    TEXT         NULL COMMENT 'SQL expression',
  `task_id`           VARCHAR(64)  NULL COMMENT '产出该边的编排任务 ID（writer 写入）',
  `business_ref`      VARCHAR(512) NULL COMMENT '写入时归属业务数据源 ref（规范化，跨数据源隔离键）',
  `created_at`        DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`column_lineage_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
