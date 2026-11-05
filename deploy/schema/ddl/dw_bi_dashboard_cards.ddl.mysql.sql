CREATE TABLE IF NOT EXISTS `dw_bi_dashboard_cards` (
  `card_id`         VARCHAR(100) NOT NULL,
  `dashboard_id`    VARCHAR(100) NULL,
  `metric_id`       VARCHAR(100) NULL,
  `title`           VARCHAR(200) NULL,
  `card_type`       VARCHAR(20) NULL DEFAULT 'metric',
  `chart_spec`      TEXT NULL,
  `echarts_option`  TEXT NULL,
  `sql_query`       TEXT NULL,
  `datasource_ref`  VARCHAR(500) NULL,
  `sparkline_sql`   TEXT NULL,
  `grid_pos`        VARCHAR(500) NULL,
  `display_format`  VARCHAR(50) NULL,
  `trend_direction` VARCHAR(20) NULL,
  `order_index`     INT NULL DEFAULT '0',
  `created_at`      VARCHAR(30) NULL,
  `updated_at`      VARCHAR(30) NULL,
  PRIMARY KEY (`card_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
