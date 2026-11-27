CREATE TABLE IF NOT EXISTS dw_analysis_sessions (
  analysis_id TEXT PRIMARY KEY,
  workspace_id TEXT NOT NULL,
  owner_session_id TEXT NOT NULL,
  state_json TEXT NOT NULL,
  revision INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
