CREATE TABLE IF NOT EXISTS `dw_metric_releases` (
  `release_id`        VARCHAR(100) NOT NULL,
  `workspace_id`      VARCHAR(100) NOT NULL,
  `label`             VARCHAR(200) NULL,
  `status`            VARCHAR(20) NULL DEFAULT 'draft',
  `revision_ids_json` TEXT NULL,
  `manifest_json`     MEDIUMTEXT NULL,
  `checksum`          VARCHAR(64) NULL,
  `projection_graph`  VARCHAR(300) NULL,
  `projection_status` VARCHAR(20) NULL DEFAULT 'pending',
  `error_code`        VARCHAR(60) NULL,
  `error_message`     VARCHAR(1000) NULL,
  `projected_at`      VARCHAR(30) NULL,
  `published_at`      VARCHAR(30) NULL,
  `published_by`      VARCHAR(100) NULL,
  `created_at`        VARCHAR(30) NOT NULL,
  PRIMARY KEY (`release_id`),
  KEY `idx_metric_rel_ws_status` (`workspace_id`, `status`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
