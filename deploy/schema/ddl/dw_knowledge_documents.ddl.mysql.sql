CREATE TABLE IF NOT EXISTS `dw_knowledge_documents` (
  `version_id` VARCHAR(100) NOT NULL PRIMARY KEY,
  `workspace_id` VARCHAR(128) NOT NULL,
  `document_id` VARCHAR(128) NOT NULL,
  `source_id` VARCHAR(128) NOT NULL DEFAULT '',
  `name` VARCHAR(512) NOT NULL,
  `sha256` VARCHAR(64) NOT NULL,
  `status` VARCHAR(32) NOT NULL DEFAULT 'candidate',
  `payload_json` LONGTEXT NOT NULL,
  `created_at` VARCHAR(30) NOT NULL,
  `updated_at` VARCHAR(30) NOT NULL,
  KEY `idx_knowledge_document` (`workspace_id`, `document_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
