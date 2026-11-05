CREATE TABLE IF NOT EXISTS `dw_bi_dashboards` (
  `dashboard_id`     VARCHAR(100) NOT NULL,
  `name`             VARCHAR(200) NULL,
  `description`      VARCHAR(2000) NULL,
  `owner`            VARCHAR(50) NULL DEFAULT 'demo',
  `layout_json`      VARCHAR(4000) NULL,
  `filter_defaults`  VARCHAR(2000) NULL,
  `status`           VARCHAR(20) NULL DEFAULT 'active',
  `source_type`      VARCHAR(50) NULL,
  `source_ref`       VARCHAR(500) NULL,
  `created_at`       VARCHAR(30) NULL,
  `updated_at`       VARCHAR(30) NULL,
  PRIMARY KEY (`dashboard_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
