CREATE TABLE IF NOT EXISTS `dw_metric_verified_sql` (
  `verified_id`         VARCHAR(100) NOT NULL,
  `workspace_id`        VARCHAR(100) NOT NULL,
  `revision_ids_json`   TEXT NULL,
  `query_spec_digest`   VARCHAR(64) NOT NULL,
  `sql_text`            MEDIUMTEXT NOT NULL,
  `dialect`             VARCHAR(20) NULL,
  `scope_json`          TEXT NULL,
  `schema_snapshot_id`  VARCHAR(100) NULL,
  `release_id`          VARCHAR(100) NULL,
  `confirmed_by`        VARCHAR(100) NULL,
  `confirmed_at`        VARCHAR(30) NULL,
  `hit_count`           INT NULL DEFAULT 0,
  `created_at`          VARCHAR(30) NOT NULL,
  PRIMARY KEY (`verified_id`),
  KEY `idx_metric_vsql_ws_digest` (`workspace_id`, `query_spec_digest`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
