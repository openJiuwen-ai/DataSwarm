CREATE TABLE IF NOT EXISTS `dw_semantic_alerts` (
  `alert_id`           VARCHAR(150) NOT NULL,
  `event_id`           VARCHAR(150) NOT NULL,
  `source_id`          VARCHAR(100) NOT NULL,
  `metric_id`          VARCHAR(100) NULL,
  `severity`           VARCHAR(20) NOT NULL,
  `message`            VARCHAR(2000) NOT NULL,
  `status`             VARCHAR(20) NOT NULL DEFAULT 'open',
  `created_at`         VARCHAR(30) NOT NULL,
  `resolved_at`        VARCHAR(30) NULL,
  PRIMARY KEY (`alert_id`),
  KEY `idx_sem_alerts_src` (`source_id`, `status`, `created_at`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
