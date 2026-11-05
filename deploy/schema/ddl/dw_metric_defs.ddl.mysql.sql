CREATE TABLE IF NOT EXISTS `dw_metric_defs` (
  `metric_id`           VARCHAR(100) NOT NULL,
  `workspace_id`        VARCHAR(100) NOT NULL,
  `metric_key`          VARCHAR(200) NOT NULL,
  `name`                VARCHAR(200) NOT NULL,
  `description`         VARCHAR(2000) NULL,
  `owner`               VARCHAR(100) NULL,
  `status`              VARCHAR(20) NULL DEFAULT 'draft',
  `current_revision_id` VARCHAR(100) NULL,
  `created_at`          VARCHAR(30) NOT NULL,
  `updated_at`          VARCHAR(30) NOT NULL,
  PRIMARY KEY (`metric_id`),
  KEY `idx_metric_defs_ws_key` (`workspace_id`, `metric_key`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
