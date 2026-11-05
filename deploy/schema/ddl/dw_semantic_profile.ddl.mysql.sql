CREATE TABLE IF NOT EXISTS `dw_semantic_profile` (
  `column_id`          VARCHAR(500) NOT NULL,
  `source_id`          VARCHAR(100) NOT NULL,
  `table_id`           VARCHAR(500) NOT NULL,
  `null_ratio`         DOUBLE NULL,
  `distinct_count`     BIGINT NULL,
  `top_k_json`         TEXT NULL,
  `is_sensitive`       TINYINT NOT NULL DEFAULT 0,
  `sensitivity_class`  VARCHAR(50) NULL,
  `profile_version`    VARCHAR(100) NULL,
  `observed_at`        VARCHAR(30) NOT NULL,
  PRIMARY KEY (`column_id`),
  KEY `idx_sem_profile_src` (`source_id`, `table_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
