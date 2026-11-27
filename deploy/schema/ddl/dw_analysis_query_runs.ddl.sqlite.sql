CREATE TABLE IF NOT EXISTS dw_analysis_query_runs (
  workspace_id TEXT NOT NULL,
  analysis_id TEXT NOT NULL,
  query_key TEXT NOT NULL,
  query_id TEXT NOT NULL,
  owner_token TEXT NOT NULL,
  status TEXT NOT NULL,
  lease_until REAL NOT NULL,
  expires_at REAL NOT NULL,
  result_json TEXT,
  result_hash TEXT,
  PRIMARY KEY (workspace_id, analysis_id, query_key)
);
