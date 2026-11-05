-- 建模计划主表
CREATE TABLE IF NOT EXISTS `plan` (
  `plan_id`      VARCHAR(64)  NOT NULL COMMENT 'UUID-style plan identifier',
  `plan_name`    VARCHAR(200) NOT NULL COMMENT 'Human-readable plan name',
  `biz_database` VARCHAR(100) NOT NULL COMMENT 'Target business database name',
  `biz_domain`   VARCHAR(100) NOT NULL COMMENT 'Business domain',
  `status`       VARCHAR(20)  NOT NULL DEFAULT 'designing' COMMENT 'designing|approved|importing|building|completed|failed',
  `source_type`  VARCHAR(20)  NOT NULL DEFAULT 'csv' COMMENT 'csv|api|database',
  `source_config`  TEXT        NULL COMMENT 'JSON: source connection details or file list',
  `layer_config`   TEXT        NULL COMMENT 'JSON: ODS/DWD/DWS/ADS layer design spec',
  `purpose`        VARCHAR(500) NULL COMMENT 'Plan purpose description',
  `content_json`   TEXT        NULL COMMENT 'Full plan JSON (denormalized, legacy)',
  `plan_file_path` VARCHAR(512) NULL COMMENT 'Absolute path to plan JSON file',
  `created_at`     DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `updated_at`   DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `created_by`   VARCHAR(64)  NULL DEFAULT 'system',
  PRIMARY KEY (`plan_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
