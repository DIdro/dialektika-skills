-- New table for API/agent-launched Telemost recordings. Created automatically
-- by Base.metadata.create_all() on startup; this file is the canonical record.

CREATE TABLE recordings (
	id VARCHAR NOT NULL,
	user_id VARCHAR NOT NULL,
	url VARCHAR NOT NULL,
	num_speakers INTEGER,
	quick BOOLEAN NOT NULL,
	status VARCHAR NOT NULL,
	stop_requested BOOLEAN NOT NULL,
	display VARCHAR,
	sink VARCHAR,
	audio_path VARCHAR,
	duration_sec FLOAT,
	stop_reason VARCHAR,
	job_id VARCHAR,
	error_message TEXT,
	created_at DATETIME NOT NULL,
	updated_at DATETIME NOT NULL,
	PRIMARY KEY (id),
	FOREIGN KEY(user_id) REFERENCES users (id)
);

CREATE INDEX ix_recordings_user_id ON recordings (user_id);
CREATE INDEX ix_recordings_status ON recordings (status);
