CREATE TABLE IF NOT EXISTS `dw_semantic_runs` (
  `run_id`             VARCHAR(100) NOT NULL,
  `source_id`          VARCHAR(100) NOT NULL,
  `trigger_type`       VARCHAR(30) NOT NULL,
  `status`             VARCHAR(30) NOT NULL,
  `scope_json`         TEXT NULL,
  `collector_version`  VARCHAR(100) NULL,
  `rule_version`       VARCHAR(100) NULL,
  `base_snapshot_id`   VARCHAR(150) NULL,
  `current_snapshot_id` VARCHAR(150) NULL,
  `semantica_stage_id` VARCHAR(500) NULL,
  `semantica_version`  VARCHAR(150) NULL,
  `started_at`         VARCHAR(30) NOT NULL,
  `finished_at`        VARCHAR(30) NULL,
  `error_json`         TEXT NULL,
  `created_at`         VARCHAR(30) NOT NULL,
  PRIMARY KEY (`run_id`),
  KEY `idx_sem_runs_src` (`source_id`, `created_at`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
