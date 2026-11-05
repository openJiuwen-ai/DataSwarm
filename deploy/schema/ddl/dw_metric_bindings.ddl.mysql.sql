CREATE TABLE IF NOT EXISTS `dw_metric_bindings` (
  `binding_id`       VARCHAR(100) NOT NULL,
  `revision_id`      VARCHAR(100) NOT NULL,
  `workspace_id`     VARCHAR(100) NOT NULL,
  `datasource_alias` VARCHAR(100) NULL,
  `datasource_ref`   VARCHAR(500) NULL,
  `source_table`     VARCHAR(300) NULL,
  `column_map_json`  TEXT NULL,
  `verify_status`    VARCHAR(20) NULL DEFAULT 'unbound',
  `verify_detail`    VARCHAR(2000) NULL,
  `verified_at`      VARCHAR(30) NULL,
  `created_at`       VARCHAR(30) NOT NULL,
  PRIMARY KEY (`binding_id`),
  KEY `idx_metric_bind_rev` (`revision_id`),
  KEY `idx_metric_bind_ws` (`workspace_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
