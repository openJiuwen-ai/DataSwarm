CREATE TABLE IF NOT EXISTS `dw_semantic_change_events` (
  `event_id`           VARCHAR(150) NOT NULL,
  `run_id`             VARCHAR(100) NOT NULL,
  `source_id`          VARCHAR(100) NOT NULL,
  `object_id`          VARCHAR(500) NOT NULL,
  `object_kind`        VARCHAR(30) NOT NULL,
  `change_type`        VARCHAR(30) NOT NULL,
  `severity`           VARCHAR(20) NOT NULL,
  `old_fingerprint`    VARCHAR(150) NULL,
  `new_fingerprint`    VARCHAR(150) NULL,
  `impact_json`        TEXT NULL,
  `status`             VARCHAR(20) NOT NULL DEFAULT 'open',
  `created_at`         VARCHAR(30) NOT NULL,
  `resolved_at`        VARCHAR(30) NULL,
  PRIMARY KEY (`event_id`),
  KEY `idx_sem_events_src` (`source_id`, `status`, `created_at`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
