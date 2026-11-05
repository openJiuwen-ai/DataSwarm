CREATE TABLE IF NOT EXISTS `dw_semantic_value_dict` (
  `value_id`    VARCHAR(64)  NOT NULL COMMENT 'deterministic: md5({snapshot_id}|{column_id}|{value_norm})[:32]',
  `source_id`   VARCHAR(100) NOT NULL,
  `snapshot_id` VARCHAR(200) NOT NULL,
  `column_id`   VARCHAR(500) NOT NULL COMMENT 'col:{source_type}:{source_id}:{db}.{tbl}.{col}',
  `table_id`    VARCHAR(500) NOT NULL,
  `value_text`  VARCHAR(500) NOT NULL,
  `value_norm`  VARCHAR(500) NOT NULL COMMENT 'NFKC+lower，全等/子串匹配面',
  PRIMARY KEY (`value_id`),
  KEY `idx_svd_snap` (`snapshot_id`),
  KEY `idx_svd_col` (`column_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
