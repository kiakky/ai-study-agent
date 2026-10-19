
import sqlite3
from contextlib import closing
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = PROJECT_ROOT / "database" / "study_agent.db"


def get_connection():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)

    connection = sqlite3.connect(DB_PATH)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")

    return connection


SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    telegram_user_id INTEGER PRIMARY KEY,
    timezone TEXT NOT NULL DEFAULT 'Asia/Singapore',
    preferences_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS exams (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    telegram_user_id INTEGER NOT NULL,
    course_id INTEGER,
    course_name TEXT NOT NULL,
    exam_title TEXT NOT NULL,
    exam_date TEXT,
    scope_json TEXT NOT NULL DEFAULT '[]',
    source_reference TEXT,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (telegram_user_id)
        REFERENCES users(telegram_user_id)
);

CREATE TABLE IF NOT EXISTS topics (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    exam_id INTEGER NOT NULL,
    title TEXT NOT NULL,
    confidence_level INTEGER,
    importance_weight REAL DEFAULT 1.0,
    source_reference TEXT,
    FOREIGN KEY (exam_id) REFERENCES exams(id)
);

CREATE TABLE IF NOT EXISTS study_plans (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    exam_id INTEGER NOT NULL,
    version INTEGER NOT NULL DEFAULT 1,
    status TEXT NOT NULL DEFAULT 'proposed'
        CHECK (status IN ('proposed', 'approved', 'rejected', 'superseded')),
    reasoning TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (exam_id) REFERENCES exams(id),
    UNIQUE (exam_id, version)
);

CREATE TABLE IF NOT EXISTS study_sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    plan_id INTEGER NOT NULL,
    topic_id INTEGER,
    starts_at TEXT NOT NULL,
    ends_at TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'scheduled',
    google_event_id TEXT,
    FOREIGN KEY (plan_id) REFERENCES study_plans(id),
    FOREIGN KEY (topic_id) REFERENCES topics(id)
);

CREATE TABLE IF NOT EXISTS quizzes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    exam_id INTEGER NOT NULL,
    topic_id INTEGER,
    difficulty INTEGER NOT NULL DEFAULT 1,
    generated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (exam_id) REFERENCES exams(id),
    FOREIGN KEY (topic_id) REFERENCES topics(id)
);

CREATE TABLE IF NOT EXISTS questions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    quiz_id INTEGER NOT NULL,
    question_text TEXT NOT NULL,
    question_type TEXT NOT NULL DEFAULT 'mcq',
    options_json TEXT NOT NULL DEFAULT '[]',
    correct_answer TEXT NOT NULL,
    explanation TEXT,
    source_reference TEXT,
    FOREIGN KEY (quiz_id) REFERENCES quizzes(id)
);

CREATE TABLE IF NOT EXISTS quiz_attempts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    quiz_id INTEGER NOT NULL,
    telegram_user_id INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'in_progress'
        CHECK (status IN ('in_progress', 'completed', 'abandoned')),
    score REAL,
    started_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    completed_at TEXT,
    FOREIGN KEY (quiz_id) REFERENCES quizzes(id),
    FOREIGN KEY (telegram_user_id)
        REFERENCES users(telegram_user_id)
);

CREATE TABLE IF NOT EXISTS quiz_answers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    attempt_id INTEGER NOT NULL,
    question_id INTEGER NOT NULL,
    answer TEXT NOT NULL,
    is_correct INTEGER,
    answered_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (attempt_id) REFERENCES quiz_attempts(id),
    FOREIGN KEY (question_id) REFERENCES questions(id),
    UNIQUE (attempt_id, question_id)
);

CREATE TABLE IF NOT EXISTS topic_progress (
    telegram_user_id INTEGER NOT NULL,
    topic_id INTEGER NOT NULL,
    total_answered INTEGER NOT NULL DEFAULT 0,
    correct_answers INTEGER NOT NULL DEFAULT 0,
    mastery_score REAL NOT NULL DEFAULT 0,
    last_practiced TEXT,
    PRIMARY KEY (telegram_user_id, topic_id),
    FOREIGN KEY (telegram_user_id)
        REFERENCES users(telegram_user_id),
    FOREIGN KEY (topic_id) REFERENCES topics(id)
);

CREATE TABLE IF NOT EXISTS approval_requests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    telegram_user_id INTEGER NOT NULL,
    plan_id INTEGER,
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'approved', 'rejected', 'expired')),
    proposed_changes_json TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    decided_at TEXT,
    FOREIGN KEY (telegram_user_id)
        REFERENCES users(telegram_user_id),
    FOREIGN KEY (plan_id) REFERENCES study_plans(id)
);

CREATE TABLE IF NOT EXISTS agent_actions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    telegram_user_id INTEGER NOT NULL,
    action_type TEXT NOT NULL,
    status TEXT NOT NULL,
    details_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (telegram_user_id)
        REFERENCES users(telegram_user_id)
);

CREATE TABLE IF NOT EXISTS lms_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    course_id INTEGER NOT NULL,
    resource_id TEXT NOT NULL,
    resource_type TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    last_modified TEXT,
    checked_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (course_id, resource_id, resource_type)
);

CREATE INDEX IF NOT EXISTS idx_exams_user
    ON exams(telegram_user_id);

CREATE INDEX IF NOT EXISTS idx_sessions_plan
    ON study_sessions(plan_id);

CREATE INDEX IF NOT EXISTS idx_attempts_user
    ON quiz_attempts(telegram_user_id);

CREATE INDEX IF NOT EXISTS idx_approvals_user_status
    ON approval_requests(telegram_user_id, status);
"""

SCHEMA += """
CREATE TABLE IF NOT EXISTS user_onboarding (
    telegram_user_id INTEGER PRIMARY KEY,
    stage TEXT NOT NULL DEFAULT 'google'
        CHECK(stage IN ('google','brightspace','preferences','completed')),
    google_choice TEXT NOT NULL DEFAULT 'pending',
    brightspace_choice TEXT NOT NULL DEFAULT 'pending',
    completed_at TEXT,
    awaiting_input TEXT,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (telegram_user_id) REFERENCES users(telegram_user_id)
);

CREATE TABLE IF NOT EXISTS google_connections (
    telegram_user_id INTEGER PRIMARY KEY,
    status TEXT NOT NULL DEFAULT 'disconnected'
        CHECK(status IN ('disconnected','connected','expired','revoked','reconnect_required')),
    encrypted_credentials BLOB,
    scopes_json TEXT NOT NULL DEFAULT '[]',
    expires_at REAL,
    generation INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (telegram_user_id) REFERENCES users(telegram_user_id)
);

CREATE TABLE IF NOT EXISTS google_oauth_requests (
    state_hash TEXT PRIMARY KEY,
    ticket_hash TEXT NOT NULL UNIQUE,
    telegram_user_id INTEGER NOT NULL,
    generation INTEGER NOT NULL,
    phase TEXT NOT NULL DEFAULT 'issued'
        CHECK(phase IN ('issued','authorizing','processing','completed','failed')),
    encrypted_verifier BLOB NOT NULL,
    browser_hash TEXT,
    expires_at REAL NOT NULL,
    FOREIGN KEY (telegram_user_id) REFERENCES users(telegram_user_id)
);

CREATE TABLE IF NOT EXISTS quiz_submissions (
    telegram_user_id INTEGER NOT NULL,
    submission_id TEXT NOT NULL,
    quiz_id INTEGER NOT NULL,
    request_hash TEXT NOT NULL,
    attempt_id INTEGER NOT NULL UNIQUE,
    result_json TEXT NOT NULL,
    PRIMARY KEY (telegram_user_id, submission_id),
    FOREIGN KEY (telegram_user_id) REFERENCES users(telegram_user_id),
    FOREIGN KEY (quiz_id) REFERENCES quizzes(id),
    FOREIGN KEY (attempt_id) REFERENCES quiz_attempts(id)
);

CREATE TABLE IF NOT EXISTS calendar_schedule_requests (
    approval_id INTEGER PRIMARY KEY,
    google_event_id TEXT NOT NULL UNIQUE,
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN (
            'pending', 'processing', 'created',
            'failed', 'rejected'
        )),
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (approval_id)
        REFERENCES approval_requests(id)
);
"""

SCHEMA += """
CREATE TABLE IF NOT EXISTS learning_goals (
    id TEXT PRIMARY KEY,
    telegram_user_id INTEGER NOT NULL,
    request_id TEXT NOT NULL,
    request_hash TEXT NOT NULL,
    source TEXT NOT NULL CHECK(source IN ('user','brightspace')),
    goal_json TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (telegram_user_id) REFERENCES users(telegram_user_id),
    UNIQUE(telegram_user_id, request_id)
);
CREATE TABLE IF NOT EXISTS study_proposals (
    id TEXT PRIMARY KEY,
    telegram_user_id INTEGER NOT NULL,
    goal_id TEXT NOT NULL,
    request_id TEXT NOT NULL,
    request_hash TEXT NOT NULL,
    replaces_id TEXT,
    status TEXT NOT NULL DEFAULT 'draft' CHECK(status IN ('draft','approved','rejected','superseded')),
    proposal_json TEXT NOT NULL,
    calendar_generation INTEGER,
    approved_digest TEXT,
    approved_at TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (telegram_user_id) REFERENCES users(telegram_user_id),
    FOREIGN KEY (goal_id) REFERENCES learning_goals(id),
    FOREIGN KEY (replaces_id) REFERENCES study_proposals(id),
    UNIQUE(telegram_user_id, request_id)
);
"""


SCHEMA += """
CREATE TABLE IF NOT EXISTS calendar_write_actions (
    id TEXT PRIMARY KEY,
    proposal_id TEXT NOT NULL UNIQUE,
    telegram_user_id INTEGER NOT NULL,
    proposal_digest TEXT NOT NULL,
    connection_generation INTEGER NOT NULL,
    calendar_target TEXT NOT NULL DEFAULT 'primary' CHECK(calendar_target='primary'),
    approval_expires_at TEXT NOT NULL,
    approval_request_ids_json TEXT NOT NULL DEFAULT '[]',
    status TEXT NOT NULL DEFAULT 'ready' CHECK(status IN ('ready','processing','partial','completed','blocked')),
    lease_until REAL NOT NULL DEFAULT 0,
    lease_token TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (proposal_id) REFERENCES study_proposals(id),
    FOREIGN KEY (telegram_user_id) REFERENCES users(telegram_user_id)
);
CREATE TABLE IF NOT EXISTS calendar_event_intents (
    action_id TEXT NOT NULL,
    session_index INTEGER NOT NULL,
    google_event_id TEXT NOT NULL UNIQUE,
    payload_json TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','uncertain','created','blocked')),
    PRIMARY KEY(action_id,session_index),
    FOREIGN KEY (action_id) REFERENCES calendar_write_actions(id)
);
CREATE TABLE IF NOT EXISTS calendar_action_audit (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    action_id TEXT NOT NULL,
    event TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (action_id) REFERENCES calendar_write_actions(id)
);
"""


# Additive Calendar conversation/individual-operation state. No record backfills.
CALENDAR_UX_SCHEMA = """
CREATE TABLE IF NOT EXISTS calendar_reviews (
    id TEXT PRIMARY KEY,
    telegram_user_id INTEGER NOT NULL REFERENCES users(telegram_user_id),
    proposal_id TEXT NOT NULL REFERENCES study_proposals(id),
    proposal_digest TEXT NOT NULL,
    connection_generation INTEGER NOT NULL,
    expires_at TEXT NOT NULL,
    presented INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'pending',
    approved_at TEXT,
    adjustment_text TEXT,
    oauth_state_hash TEXT,
    action_id TEXT REFERENCES calendar_write_actions(id),
    notified INTEGER NOT NULL DEFAULT 0,
    error TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS calendar_session_changes (
    id TEXT PRIMARY KEY,
    telegram_user_id INTEGER NOT NULL REFERENCES users(telegram_user_id),
    action_id TEXT NOT NULL,
    session_index INTEGER NOT NULL,
    kind TEXT NOT NULL CHECK(kind IN ('reschedule','cancel')),
    old_payload_json TEXT NOT NULL,
    new_payload_json TEXT,
    remote_etag TEXT NOT NULL,
    connection_generation INTEGER NOT NULL,
    preferences_digest TEXT NOT NULL,
    timezone TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    lease_until REAL NOT NULL DEFAULT 0,
    lease_token TEXT,
    attempted INTEGER NOT NULL DEFAULT 0,
    error TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(action_id,session_index) REFERENCES calendar_event_intents(action_id,session_index)
);
CREATE UNIQUE INDEX IF NOT EXISTS calendar_one_active_change
ON calendar_session_changes(action_id,session_index)
WHERE status IN ('approved','processing','uncertain');
"""
SCHEMA += CALENDAR_UX_SCHEMA


# Stage 1 conversational memory; independent of saved profiles and Calendar state.
CONVERSATION_SCHEMA = """
CREATE TABLE IF NOT EXISTS conversation_sessions (
    telegram_user_id INTEGER PRIMARY KEY REFERENCES users(telegram_user_id),
    generation INTEGER NOT NULL DEFAULT 0,
    lease_until REAL NOT NULL DEFAULT 0,
    lease_token TEXT
);
CREATE TABLE IF NOT EXISTS conversation_turns (
    telegram_user_id INTEGER NOT NULL REFERENCES users(telegram_user_id),
    message_id INTEGER NOT NULL,
    generation INTEGER NOT NULL,
    request_hash TEXT NOT NULL,
    user_text TEXT NOT NULL DEFAULT '',
    assistant_text TEXT,
    status TEXT NOT NULL CHECK(status IN ('pending','completed','failed','cleared')),
    error_code TEXT,
    delivered INTEGER NOT NULL DEFAULT 0,
    telegram_reply_id INTEGER,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY(telegram_user_id,message_id)
);
CREATE INDEX IF NOT EXISTS conversation_context
ON conversation_turns(telegram_user_id,generation,message_id);
"""
CALENDAR_AGENT_SCHEMA = """
CREATE TABLE IF NOT EXISTS conversation_tool_effects (
    telegram_user_id INTEGER NOT NULL REFERENCES users(telegram_user_id),
    message_id INTEGER NOT NULL,
    ordinal INTEGER NOT NULL,
    request_hash TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('pending','completed')),
    result_json TEXT,
    presented INTEGER NOT NULL DEFAULT 0,
    decided INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (telegram_user_id,message_id,ordinal)
);
"""
SCHEMA += CONVERSATION_SCHEMA + CALENDAR_AGENT_SCHEMA


LMS_SCHEMA = """
CREATE TABLE IF NOT EXISTS lms_connections (
    id TEXT PRIMARY KEY,
    telegram_user_id INTEGER NOT NULL,
    provider TEXT NOT NULL DEFAULT 'brightspace',
    session_binding TEXT NOT NULL UNIQUE,
    account_reference TEXT,
    status TEXT NOT NULL DEFAULT 'authorized' CHECK(status IN ('authorized','disconnected')),
    FOREIGN KEY (telegram_user_id) REFERENCES users(telegram_user_id)
);
CREATE TABLE IF NOT EXISTS lms_courses (
    connection_id TEXT NOT NULL,
    course_id TEXT NOT NULL,
    name TEXT NOT NULL,
    activity TEXT NOT NULL DEFAULT 'unknown' CHECK(activity IN ('active','inactive','unknown')),
    metadata_json TEXT NOT NULL,
    metadata_hash TEXT NOT NULL,
    checked_at TEXT NOT NULL,
    PRIMARY KEY(connection_id,course_id),
    FOREIGN KEY (connection_id) REFERENCES lms_connections(id)
);
CREATE TABLE IF NOT EXISTS lms_sources (
    id TEXT PRIMARY KEY,
    connection_id TEXT NOT NULL,
    course_id TEXT,
    resource_key TEXT NOT NULL,
    metadata_json TEXT NOT NULL,
    metadata_hash TEXT NOT NULL,
    checked_at TEXT NOT NULL,
    UNIQUE(connection_id,resource_key),
    FOREIGN KEY (connection_id) REFERENCES lms_connections(id)
);
CREATE TABLE IF NOT EXISTS lms_source_revisions (
    id TEXT PRIMARY KEY,
    source_id TEXT NOT NULL,
    version_hash TEXT NOT NULL,
    metadata_snapshot_json TEXT NOT NULL,
    original_payload_json TEXT,
    extraction_json TEXT,
    original_file_reference TEXT,
    content_hash TEXT,
    retrieved_at TEXT NOT NULL,
    UNIQUE(source_id,version_hash),
    FOREIGN KEY (source_id) REFERENCES lms_sources(id)
);
"""


SCHEMA += LMS_SCHEMA


def initialize_database(*, backfill_approvals=True, include_lms=False, include_practice=False):
    with closing(get_connection()) as connection, connection:
        connection.executescript(SCHEMA if include_lms else SCHEMA.replace(LMS_SCHEMA, ""))

        if include_practice:
            from services.practice_store import SCHEMA as PRACTICE_SCHEMA
            connection.executescript(PRACTICE_SCHEMA)

        # Add new columns without deleting existing data.
        columns = {
            row["name"] for row in connection.execute(
                "PRAGMA table_info(approval_requests)"
            )
        }

        if "expires_at" not in columns:
            connection.execute(
                "ALTER TABLE approval_requests ADD COLUMN expires_at TEXT"
            )

        if "resolution_reason" not in columns:
            connection.execute(
                "ALTER TABLE approval_requests ADD COLUMN resolution_reason TEXT"
            )

        calendar_columns = {
            row["name"] for row in connection.execute(
                "PRAGMA table_info(calendar_schedule_requests)"
            )
        }

        if "last_error" not in calendar_columns:
            connection.execute(
                "ALTER TABLE calendar_schedule_requests ADD COLUMN last_error TEXT"
            )

        # Give old, still-pending requests an expiration time.
        if backfill_approvals:
            connection.execute("""
            UPDATE approval_requests
            SET expires_at = datetime(created_at, '+10 minutes')
            WHERE status = 'pending'
              AND expires_at IS NULL
              AND id IN (
                  SELECT approval_id
                  FROM calendar_schedule_requests
              )
            """)

        connection.execute("""
            CREATE INDEX IF NOT EXISTS idx_approval_expiry
            ON approval_requests(status, expires_at)
        """)

    print(f"Database initialised: {DB_PATH}")


if __name__ == "__main__":
    initialize_database()
