CREATE TABLE IF NOT EXISTS `dw_layer_registry` (
  `table_name`      VARCHAR(100) NOT NULL,
  `layer`           VARCHAR(20)  NULL,
  `layer_comment`   VARCHAR(200) NULL,
  `upstream_tables` VARCHAR(500) NULL,
  PRIMARY KEY (`table_name`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
