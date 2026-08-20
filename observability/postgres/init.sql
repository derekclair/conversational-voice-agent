-- Voice Agent Observability Schema
-- Designed for conversation analysis, latency tracking, and multi-user readiness.

CREATE TABLE sessions (
    id              TEXT PRIMARY KEY,           -- thread_id (e.g. "lenovo-go-a1b2c3d4")
    device_id       TEXT NOT NULL,
    user_id         TEXT NOT NULL,
    started_at      TIMESTAMPTZ NOT NULL,
    ended_at        TIMESTAMPTZ,
    turns           INT DEFAULT 0,
    duration_s      REAL,
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE turns (
    id              SERIAL PRIMARY KEY,
    session_id      TEXT NOT NULL REFERENCES sessions(id),
    turn_number     INT NOT NULL,
    user_text       TEXT NOT NULL,
    agent_reply     TEXT NOT NULL,
    asr_ms          INT,
    agent_ms        INT,
    tts_ms          INT,
    total_ms        INT,
    tool_calls      JSONB DEFAULT '[]',
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE events (
    id              SERIAL PRIMARY KEY,
    timestamp       TIMESTAMPTZ NOT NULL,
    device_id       TEXT NOT NULL,
    session_id      TEXT,
    user_id         TEXT,
    event_type      TEXT NOT NULL,
    payload         JSONB DEFAULT '{}',
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

-- Indexes for common queries
CREATE INDEX idx_sessions_user ON sessions(user_id);
CREATE INDEX idx_sessions_started ON sessions(started_at);
CREATE INDEX idx_turns_session ON turns(session_id);
CREATE INDEX idx_events_type ON events(event_type);
CREATE INDEX idx_events_session ON events(session_id);
CREATE INDEX idx_events_timestamp ON events(timestamp);
