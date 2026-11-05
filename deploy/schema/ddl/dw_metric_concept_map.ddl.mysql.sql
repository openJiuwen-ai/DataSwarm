CREATE TABLE IF NOT EXISTS `dw_metric_concept_map` (
  `mapping_id`   VARCHAR(100) NOT NULL,
  `workspace_id` VARCHAR(100) NOT NULL,
  `revision_id`  VARCHAR(100) NOT NULL,
  `relation_type` VARCHAR(40) NOT NULL,
  `target_ref`   VARCHAR(400) NOT NULL,
  `status`       VARCHAR(20) NULL DEFAULT 'candidate',
  `evidence`     VARCHAR(1000) NULL,
  `confirmed_by` VARCHAR(100) NULL,
  `confirmed_at` VARCHAR(30) NULL,
  `created_at`   VARCHAR(30) NOT NULL,
  PRIMARY KEY (`mapping_id`),
  KEY `idx_metric_cmap_rev` (`revision_id`, `status`),
  KEY `idx_metric_cmap_ws` (`workspace_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
