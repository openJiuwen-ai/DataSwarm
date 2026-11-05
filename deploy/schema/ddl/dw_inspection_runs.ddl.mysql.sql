CREATE TABLE IF NOT EXISTS `dw_inspection_runs` (
  `run_id`               VARCHAR(100) NOT NULL,
  `db_ref_alias`         VARCHAR(200) NULL,
  `db_identity`          VARCHAR(300) NULL,
  `db_type`              VARCHAR(20)  NULL,
  `status`               VARCHAR(20)  NULL,
  `persistence_status`   VARCHAR(20)  NULL,
  `started_at`           VARCHAR(30)  NULL,
  `finished_at`          VARCHAR(30)  NULL,
  `artifact_dir`         VARCHAR(500) NULL,
  `session_id`           VARCHAR(100) NULL,
  `workspace_id`         VARCHAR(100) NULL,
  `health_scores_json`   TEXT       NULL,
  `summary_json`         TEXT       NULL,
  `created_at`           VARCHAR(30)  NULL,
  -- 历史报告预留（首装即终态）
  `db_name`              VARCHAR(200) NULL,
  `inspected_tables_json` TEXT      NULL,
  `report_start`         VARCHAR(30)  NULL,
  `report_end`           VARCHAR(30)  NULL,
  `reported_at`          VARCHAR(30)  NULL,
  PRIMARY KEY (`run_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
