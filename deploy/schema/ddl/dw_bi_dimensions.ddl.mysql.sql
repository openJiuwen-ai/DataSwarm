CREATE TABLE IF NOT EXISTS `dw_bi_dimensions` (
  `dimension_id`   VARCHAR(100) NOT NULL,
  `name`           VARCHAR(200) NULL,
  `label`          VARCHAR(200) NULL,
  `description`    VARCHAR(2000) NULL,
  `dim_type`       VARCHAR(20) NULL DEFAULT 'category',
  `value_source`   VARCHAR(20) NULL DEFAULT 'manual',
  `value_sql`      VARCHAR(5000) NULL,
  `value_list`     VARCHAR(4000) NULL,
  `datasource_ref` VARCHAR(500) NULL,
  `created_at`     VARCHAR(30) NULL,
  `updated_at`     VARCHAR(30) NULL,
  PRIMARY KEY (`dimension_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
