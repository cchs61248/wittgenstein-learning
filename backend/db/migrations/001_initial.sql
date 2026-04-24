CREATE TABLE IF NOT EXISTS users (
    user_id TEXT PRIMARY KEY,
    email TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS sessions (
    session_id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(user_id),
    content_hash TEXT NOT NULL,
    total_stages INTEGER NOT NULL DEFAULT 0,
    current_stage_id INTEGER DEFAULT 0,
    status TEXT DEFAULT 'active',
    raw_content_summary TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS stage_progress (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL REFERENCES sessions(session_id),
    stage_id INTEGER NOT NULL,
    status TEXT DEFAULT 'pending',
    attempts INTEGER DEFAULT 0,
    best_score REAL DEFAULT 0.0,
    understanding_notes TEXT DEFAULT '{}',
    completed_at TIMESTAMP,
    UNIQUE(session_id, stage_id)
);

CREATE TABLE IF NOT EXISTS qa_records (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    stage_id INTEGER NOT NULL,
    question_id TEXT NOT NULL,
    question_text TEXT NOT NULL,
    question_type TEXT,
    user_answer TEXT,
    score REAL,
    feedback TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS concept_mastery (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL REFERENCES users(user_id),
    concept_name TEXT NOT NULL,
    mastery_score REAL DEFAULT 0.0,
    total_exposures INTEGER DEFAULT 0,
    confusion_patterns TEXT DEFAULT '[]',
    successful_analogies TEXT DEFAULT '[]',
    last_tested TIMESTAMP,
    UNIQUE(user_id, concept_name)
);

CREATE TABLE IF NOT EXISTS user_learning_profile (
    user_id TEXT PRIMARY KEY REFERENCES users(user_id),
    preferred_style TEXT DEFAULT 'concrete',
    avg_attempts_per_stage REAL DEFAULT 1.5,
    strong_domains TEXT DEFAULT '[]',
    weak_domains TEXT DEFAULT '[]',
    optimal_stage_length INTEGER DEFAULT 500,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id);
CREATE INDEX IF NOT EXISTS idx_stage_progress_session ON stage_progress(session_id);
CREATE INDEX IF NOT EXISTS idx_qa_records_session ON qa_records(session_id, stage_id);
CREATE INDEX IF NOT EXISTS idx_concept_mastery_user ON concept_mastery(user_id);
