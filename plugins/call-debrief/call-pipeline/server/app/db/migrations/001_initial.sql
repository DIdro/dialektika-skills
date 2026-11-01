CREATE TABLE disk_state (
	id INTEGER NOT NULL, 
	usage_pct FLOAT NOT NULL, 
	free_gb FLOAT NOT NULL, 
	warning BOOLEAN NOT NULL, 
	updated_at DATETIME NOT NULL, 
	PRIMARY KEY (id)
);

CREATE TABLE users (
	id VARCHAR NOT NULL, 
	name VARCHAR NOT NULL, 
	bearer_token VARCHAR NOT NULL, 
	mionika_read_token TEXT, 
	mionika_active_project_keys TEXT, 
	context_summary TEXT, 
	system_prompt_override TEXT, 
	rate_limit_overrides TEXT, 
	created_at DATETIME NOT NULL, 
	updated_at DATETIME NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (bearer_token)
);

CREATE TABLE jobs (
	id VARCHAR NOT NULL, 
	user_id VARCHAR NOT NULL, 
	status VARCHAR NOT NULL, 
	mode VARCHAR NOT NULL, 
	language VARCHAR NOT NULL, 
	diarize BOOLEAN NOT NULL, 
	dedupe_key VARCHAR, 
	audio_path VARCHAR NOT NULL, 
	audio_size_bytes INTEGER, 
	audio_duration_sec FLOAT, 
	transcript_path VARCHAR, 
	draft_path VARCHAR, 
	retry_count INTEGER NOT NULL, 
	error_message TEXT, 
	created_at DATETIME NOT NULL, 
	updated_at DATETIME NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(user_id) REFERENCES users (id)
);

CREATE INDEX ix_jobs_user_id ON jobs (user_id);

CREATE INDEX ix_jobs_dedupe_key ON jobs (dedupe_key);

CREATE INDEX ix_jobs_status ON jobs (status);

CREATE TABLE job_events (
	id INTEGER NOT NULL, 
	job_id VARCHAR NOT NULL, 
	event VARCHAR NOT NULL, 
	stage VARCHAR NOT NULL, 
	duration_ms INTEGER, 
	error TEXT, 
	created_at DATETIME NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(job_id) REFERENCES jobs (id)
);

CREATE INDEX ix_job_events_job_id ON job_events (job_id);
