CREATE TABLE IF NOT EXISTS `dw_business_knowledge` (
  `asset_id` VARCHAR(64) NOT NULL PRIMARY KEY,
  `workspace_id` VARCHAR(128) NOT NULL,
  `asset_type` VARCHAR(32) NOT NULL,
  `title` VARCHAR(512) NOT NULL,
  `content` LONGTEXT NOT NULL,
  `scope_json` LONGTEXT NOT NULL,
  `source_json` LONGTEXT NOT NULL,
  `status` VARCHAR(32) NOT NULL DEFAULT 'candidate',
  `revision` INT NOT NULL DEFAULT 1,
  `owner` VARCHAR(256) NOT NULL DEFAULT '',
  `created_at` VARCHAR(30) NOT NULL,
  `updated_at` VARCHAR(30) NOT NULL,
  `published_at` VARCHAR(30) NOT NULL DEFAULT '',
  KEY `idx_dw_knowledge_scope` (`workspace_id`, `status`, `asset_type`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
