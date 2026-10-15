from contextlib import closing
import json

from database import get_connection
from models.preferences import StudyPreferences


def ensure_profile(user_id):
    with closing(get_connection()) as connection, connection:
        connection.execute('BEGIN IMMEDIATE')
        created = connection.execute(
            'INSERT OR IGNORE INTO users (telegram_user_id) VALUES (?)', (user_id,),
        ).rowcount > 0
        connection.execute(
            'INSERT OR IGNORE INTO user_onboarding (telegram_user_id) VALUES (?)', (user_id,),
        )
    return created


def get_profile(user_id):
    ensure_profile(user_id)
    with closing(get_connection()) as connection:
        user = connection.execute(
            'SELECT timezone, preferences_json FROM users WHERE telegram_user_id=?', (user_id,),
        ).fetchone()
        onboarding = connection.execute(
            'SELECT stage, google_choice, brightspace_choice, completed_at, awaiting_input FROM user_onboarding WHERE telegram_user_id=?',
            (user_id,),
        ).fetchone()
    saved = json.loads(user['preferences_json'])
    known = {key: value for key, value in saved.items() if key in StudyPreferences.model_fields}
    preferences = StudyPreferences.model_validate(known).model_dump(mode='json')
    return {'timezone': user['timezone'], 'preferences': preferences, 'preferences_saved': bool(known),
            'onboarding': dict(onboarding), 'brightspace': {'available': False, 'status': 'unavailable',
            'reason': 'Per-student Brightspace authorization is not implemented. The developer session is never shared.'}}


def choose_integration(user_id, integration, choice):
    if (integration, choice) not in {('google', 'skipped'), ('google', 'connected'), ('brightspace', 'skipped')}:
        raise ValueError('Unsupported onboarding choice.')
    ensure_profile(user_id)
    with closing(get_connection()) as connection, connection:
        if integration == 'google':
            connection.execute(
                "UPDATE user_onboarding SET google_choice=?, stage=CASE WHEN stage='google' THEN 'brightspace' ELSE stage END, updated_at=CURRENT_TIMESTAMP WHERE telegram_user_id=?",
                (choice, user_id),
            )
        else:
            connection.execute(
                "UPDATE user_onboarding SET brightspace_choice=?, stage='preferences', updated_at=CURRENT_TIMESTAMP WHERE telegram_user_id=? AND stage='brightspace'",
                (choice, user_id),
            )
    return get_profile(user_id)


def save_preferences(user_id, preferences):
    ensure_profile(user_id)
    with closing(get_connection()) as connection, connection:
        connection.execute('BEGIN IMMEDIATE')
        row = connection.execute('SELECT preferences_json FROM users WHERE telegram_user_id=?', (user_id,)).fetchone()
        saved = json.loads(row['preferences_json'])
        saved.update(preferences.model_dump(mode='json'))
        connection.execute('UPDATE users SET preferences_json=? WHERE telegram_user_id=?', (json.dumps(saved), user_id))
        connection.execute(
            "UPDATE user_onboarding SET stage=CASE WHEN stage='preferences' THEN 'completed' ELSE stage END, completed_at=CASE WHEN stage='preferences' THEN COALESCE(completed_at,CURRENT_TIMESTAMP) ELSE completed_at END, awaiting_input=NULL, updated_at=CURRENT_TIMESTAMP WHERE telegram_user_id=?",
            (user_id,),
        )
    return get_profile(user_id)


def expect_preference_notes(user_id):
    ensure_profile(user_id)
    with closing(get_connection()) as connection, connection:
        connection.execute("UPDATE user_onboarding SET awaiting_input='notes' WHERE telegram_user_id=?", (user_id,))


def consume_preference_notes(user_id, text):
    ensure_profile(user_id)
    with closing(get_connection()) as connection, connection:
        connection.execute('BEGIN IMMEDIATE')
        row = connection.execute(
            'SELECT u.preferences_json, o.awaiting_input FROM users u JOIN user_onboarding o ON o.telegram_user_id=u.telegram_user_id WHERE u.telegram_user_id=?', (user_id,),
        ).fetchone()
        if row['awaiting_input'] != 'notes':
            return False
        preferences = json.loads(row['preferences_json'])
        StudyPreferences(notes=text)
        preferences['notes'] = text
        connection.execute('UPDATE users SET preferences_json=? WHERE telegram_user_id=?', (json.dumps(preferences), user_id))
        connection.execute('UPDATE user_onboarding SET awaiting_input=NULL WHERE telegram_user_id=?', (user_id,))
    return True


def current_study_plan(user_id):
    with closing(get_connection()) as connection:
        rows = connection.execute(
            """SELECT p.id, p.version, p.status, e.course_name, e.exam_title
               FROM study_plans p JOIN exams e ON e.id=p.exam_id
               WHERE e.telegram_user_id=? ORDER BY p.created_at DESC, p.id DESC LIMIT 10""",
            (user_id,),
        ).fetchall()
        proposals = connection.execute('SELECT id,status,proposal_json FROM study_proposals WHERE telegram_user_id=? ORDER BY created_at DESC,id LIMIT 10',(user_id,)).fetchall()
    plans=[dict(row) for row in rows]
    plans += [{'id':row['id'],'status':row['status'],'version':1,'course_name':json.loads(row['proposal_json'])['subject'],
               'exam_title':'Study proposal (no Calendar events)','source':'unified'} for row in proposals]
    return {'plans':plans,'state':'available' if plans else 'not_generated','planning_available':True}
