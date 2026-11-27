CREATE TABLE IF NOT EXISTS dw_analysis_query_runs (
  workspace_id VARCHAR(100) NOT NULL,
  analysis_id VARCHAR(100) NOT NULL,
  query_key VARCHAR(64) NOT NULL,
  query_id VARCHAR(64) NOT NULL,
  owner_token VARCHAR(64) NOT NULL,
  status VARCHAR(24) NOT NULL,
  lease_until DOUBLE NOT NULL,
  expires_at DOUBLE NOT NULL,
  result_json LONGTEXT,
  result_hash VARCHAR(64),
  PRIMARY KEY (workspace_id, analysis_id, query_key)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
