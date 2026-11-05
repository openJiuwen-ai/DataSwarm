CREATE TABLE IF NOT EXISTS `dw_knowledge_events` (
  `event_id` VARCHAR(64) NOT NULL PRIMARY KEY,
  `workspace_id` VARCHAR(128) NOT NULL,
  `asset_id` VARCHAR(128) NOT NULL,
  `action` VARCHAR(32) NOT NULL,
  `actor` VARCHAR(256) NOT NULL,
  `payload_json` LONGTEXT NOT NULL,
  `created_at` VARCHAR(30) NOT NULL,
  KEY `idx_knowledge_event_asset` (`workspace_id`, `asset_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
