CREATE TABLE IF NOT EXISTS `dw_datasources` (
  `source_id`   VARCHAR(100) NOT NULL,
  `source_type` VARCHAR(50)  NULL,
  `name`        VARCHAR(200) NULL,
  `description` VARCHAR(500) NULL,
  `config`      VARCHAR(4000) NULL,
  `db_ref`      VARCHAR(500) NULL,
  `engine`      VARCHAR(32)  NOT NULL DEFAULT 'sqlalchemy',
  `enabled`     BOOLEAN      NOT NULL DEFAULT 1,
  `config_version` INT       NOT NULL DEFAULT 1,
  `created_at`  VARCHAR(30)  NULL,
  `updated_at`  VARCHAR(30)  NULL,
  PRIMARY KEY (`source_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
