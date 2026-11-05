CREATE TABLE IF NOT EXISTS `dw_semantic_join_status` (
  `join_node_id` VARCHAR(64)  NOT NULL COMMENT 'JoinPathMeta.node_id = md5(left|right)',
  `source_id`    VARCHAR(100) NOT NULL,
  `status`       VARCHAR(20)  NOT NULL COMMENT 'verified|rejected',
  `note`         VARCHAR(500) NULL,
  `confirmed_by` VARCHAR(100) NOT NULL,
  `confirmed_at` VARCHAR(30)  NOT NULL,
  PRIMARY KEY (`join_node_id`),
  KEY `idx_sjs_source` (`source_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
