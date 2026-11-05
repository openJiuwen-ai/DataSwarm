CREATE TABLE IF NOT EXISTS `dw_semantic_snapshots` (
  `snapshot_id`        VARCHAR(150) NOT NULL,
  `run_id`             VARCHAR(100) NOT NULL,
  `source_id`          VARCHAR(100) NOT NULL,
  `status`             VARCHAR(30) NOT NULL,
  `scope_json`         TEXT NULL,
  `snapshot_json`      MEDIUMTEXT NOT NULL,
  `object_count`       INT NOT NULL DEFAULT 0,
  `relation_count`     INT NOT NULL DEFAULT 0,
  `semantica_version`  VARCHAR(150) NULL,
  `published_at`       VARCHAR(30) NULL,
  `created_at`         VARCHAR(30) NOT NULL,
  PRIMARY KEY (`snapshot_id`),
  KEY `idx_sem_snap_src` (`source_id`, `status`, `published_at`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
