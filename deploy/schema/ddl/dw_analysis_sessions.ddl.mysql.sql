CREATE TABLE IF NOT EXISTS dw_analysis_sessions (
  analysis_id VARCHAR(100) PRIMARY KEY,
  workspace_id VARCHAR(100) NOT NULL,
  owner_session_id VARCHAR(200) NOT NULL,
  state_json LONGTEXT NOT NULL,
  revision BIGINT NOT NULL DEFAULT 0,
  created_at VARCHAR(40) NOT NULL,
  updated_at VARCHAR(40) NOT NULL,
  KEY idx_analysis_workspace (workspace_id, updated_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
