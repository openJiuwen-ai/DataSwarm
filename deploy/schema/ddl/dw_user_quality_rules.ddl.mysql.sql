CREATE TABLE IF NOT EXISTS `dw_user_quality_rules` (
  `rule_id`           VARCHAR(64)  NOT NULL,
  `rule_name`         VARCHAR(200) NULL,
  `db_name`           VARCHAR(100) NULL,
  `target_table`      VARCHAR(200) NOT NULL,
  `target_column`     VARCHAR(200) NULL,
  `rule_type`         VARCHAR(20)  NOT NULL,
  `rule_config_json`  TEXT       NULL,
  `check_sql`         TEXT       NULL,
  `source_session_id` VARCHAR(100) NULL,
  `enabled`           TINYINT      NULL,
  `created_at`        VARCHAR(30)  NULL,
  `updated_at`        VARCHAR(30)  NULL,
  PRIMARY KEY (`rule_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
