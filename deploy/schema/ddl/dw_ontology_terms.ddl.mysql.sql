CREATE TABLE IF NOT EXISTS `dw_ontology_terms` (
  `term_id`            VARCHAR(50) NOT NULL,
  `name`               VARCHAR(200) NOT NULL,
  `definition`         TEXT NOT NULL,
  `synonyms`           VARCHAR(2000) NOT NULL DEFAULT '[]',
  `status`             VARCHAR(20) NOT NULL DEFAULT 'active',
  `owner`              VARCHAR(100) NOT NULL DEFAULT '',
  `source`             VARCHAR(20) NOT NULL DEFAULT 'manual',
  `tags`               VARCHAR(500) NOT NULL DEFAULT '',
  `fingerprint`        VARCHAR(80) NOT NULL DEFAULT '',
  `created_at`         VARCHAR(30) NOT NULL,
  `updated_at`         VARCHAR(30) NOT NULL,
  PRIMARY KEY (`term_id`),
  KEY `idx_ontology_terms_updated` (`updated_at`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
