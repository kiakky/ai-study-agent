"""No .env reads, production SQLite access, or external service calls."""
import hashlib
import hmac
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from unittest.mock import patch
from urllib.parse import urlencode
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
TEST_TOKEN = "synthetic-test-token-not-a-real-credential"
with patch("dotenv.load_dotenv"), patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": TEST_TOKEN}):
    import api
import database
from fastapi.testclient import TestClient
from starlette.middleware.trustedhost import TrustedHostMiddleware


def signed_data(user_id=101, age=0, extra=None, user=None):
    fields = {
        "auth_date": str(int(time.time()) - age),
        "user": json.dumps(user if user is not None else {"id": user_id, "first_name": "Test Student"}),
        **(extra or {}),
    }
    secret = hmac.new(b"WebAppData", TEST_TOKEN.encode(), hashlib.sha256).digest()
    fields["hash"] = hmac.new(secret, "\n".join(f"{k}={v}" for k, v in sorted(fields.items())).encode(), hashlib.sha256).hexdigest()
    return urlencode(fields)


class QuizAPITests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="quiz-test-", dir=ROOT / "tests")
        self.addCleanup(self.temp.cleanup)
        self.db_patch = patch.object(database, "DB_PATH", Path(self.temp.name) / "test.db")
        self.db_patch.start()
        self.addCleanup(self.db_patch.stop)
        self.token_patch = patch.object(api, "BOT_TOKEN", TEST_TOKEN)
        self.token_patch.start()
        self.addCleanup(self.token_patch.stop)
        self.client = TestClient(api.app, base_url="http://localhost")
        self.client.__enter__()
        self.addCleanup(self.client.__exit__, None, None, None)
        self.headers = {"X-Telegram-Init-Data": signed_data()}
        self.other = {"X-Telegram-Init-Data": signed_data(202)}

    def quiz(self, headers=None):
        headers = headers or self.headers
        listing = self.client.get('/api/quizzes', headers=headers).json()
        return self.client.get(f'/api/quizzes/{listing[0]["id"]}', headers=headers).json()

    def submission(self, quiz):
        return {"submission_id": str(uuid4()), "answers": [
            {"question_id": q["id"], "option": "A"} for q in quiz["questions"]
        ]}

    def submit(self, quiz, body, headers=None):
        return self.client.post(f'/api/quizzes/{quiz["id"]}/submit', json=body, headers=headers or self.headers)

    def test_authentication_and_static_frontend(self):
        for path in ['/api/me', '/api/quizzes', '/api/grades', '/api/attempts/1', '/api/quizzes/1']:
            self.assertEqual(self.client.get(path).status_code, 401)
        self.assertEqual(self.client.post('/api/quizzes/1/submit', json={}).status_code, 401)
        for data in [signed_data(age=86401), signed_data(age=-120), signed_data() + '&auth_date=1',
                     signed_data().replace('Test+Student', 'Imposter'), 'bad',
                     signed_data(user=[]), signed_data(user={"id": True}), signed_data(user={"id": -2})]:
            with self.subTest(data_type=data[:12]):
                self.assertEqual(self.client.get('/api/me', headers={'X-Telegram-Init-Data': data}).status_code, 401)
        for extra in [{}, {"signature": "synthetic-telegram-ed25519-field"}]:
            self.assertEqual(self.client.get('/api/me', headers={'X-Telegram-Init-Data': signed_data(extra=extra)}).status_code, 200)
        with patch.object(api, 'BOT_TOKEN', ''):
            self.assertEqual(self.client.get('/api/me', headers=self.headers).status_code, 401)
        for path in ['/', '/static/styles.css', '/static/app.js', '/static/icon.svg', '/health']:
            self.assertEqual(self.client.get(path).status_code, 200)
        for path in ['/static/.env', '/static/../backend/api.py', '/database/study_agent.db', '/__preview__/telegram.js',
                     '/docs', '/redoc', '/openapi.json', '/static/index.html', '/static/new-private-file.txt', '/config/deployment.json']:
            self.assertEqual(self.client.get(path).status_code, 404)
        self.assertEqual(self.client.get('/api/me', headers=self.headers).headers['cache-control'], 'no-store')

    def test_host_validation_and_dynamic_verified_registration(self):
        self.assertEqual(self.client.get('/health', headers={'Host': 'unapproved.example'}).status_code, 400)
        self.assertEqual(self.client.get('/health', headers={'Host': 'localhost', 'X-Forwarded-Host': 'unapproved.example'}).status_code, 200)
        with closing(database.get_connection()) as conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM users').fetchone()[0], 0)
        self.assertEqual(self.client.get('/api/me', headers=self.other).status_code, 200)
        with closing(database.get_connection()) as conn:
            self.assertEqual([row[0] for row in conn.execute('SELECT telegram_user_id FROM users')], [202])
        oversized = {'X-Telegram-Init-Data': signed_data(user={'id': 2**80})}
        self.assertEqual(self.client.get('/api/me', headers=oversized).status_code, 401)

    def test_https_same_origin_contract_with_synthetic_users(self):
        # ASGI HTTPS-origin simulation, not a claim of external TLS/tunnel testing.
        original = list(api.app.user_middleware)
        try:
            api.app.user_middleware = [m for m in original if m.cls is not TrustedHostMiddleware]
            from starlette.middleware import Middleware
            api.app.user_middleware.append(Middleware(TrustedHostMiddleware, allowed_hosts=['study.example']))
            api.app.middleware_stack = None
            with TestClient(api.app, base_url='https://study.example') as client:
                page = client.get('/')
                self.assertIn('src="/static/app.js"', page.text)
                self.assertEqual(client.get('/static/app.js').status_code, 200)
                self.assertEqual(client.get('/api/quizzes').status_code, 401)
                for headers in [self.headers, self.other]:
                    quiz_id = client.get('/api/quizzes', headers=headers).json()[0]['id']
                    quiz = client.get(f'/api/quizzes/{quiz_id}', headers=headers).json()
                    result = client.post(f'/api/quizzes/{quiz_id}/submit', json=self.submission(quiz), headers=headers)
                    self.assertEqual(result.status_code, 200)
                    self.assertEqual(len(client.get('/api/grades', headers=headers).json()), 1)
        finally:
            api.app.user_middleware = original
            api.app.middleware_stack = None

    def test_quiz_has_no_answers_and_is_seeded_once(self):
        first = self.quiz()
        self.assertEqual(first, self.quiz())
        self.assertTrue(first['demo'])
        self.assertEqual(first['question_count'], 10)
        for question in first['questions']:
            self.assertEqual(set(question), {'id', 'text', 'options'})
        with closing(database.get_connection()) as conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM questions').fetchone()[0], 10)

    def test_grading_history_review_and_deliberate_retake(self):
        quiz = self.quiz()
        result = self.submit(quiz, self.submission(quiz))
        self.assertEqual(result.status_code, 200)
        data = result.json()
        self.assertEqual((data['score'], data['correct'], data['total']), (30.0, 3, 10))
        self.assertEqual(len(data['feedback']), 10)
        self.assertEqual(data, self.client.get(f'/api/attempts/{data["attempt_id"]}', headers=self.headers).json())
        second = self.submit(quiz, self.submission(quiz)).json()
        self.assertNotEqual(data['attempt_id'], second['attempt_id'])
        grades = self.client.get('/api/grades', headers=self.headers).json()
        self.assertEqual([g['id'] for g in grades], [second['attempt_id'], data['attempt_id']])
        self.assertEqual(self.client.get('/api/grades?limit=1&offset=1', headers=self.headers).json()[0]['id'], data['attempt_id'])
        self.assertEqual(self.client.get('/api/me', headers=self.headers).json()['stats']['completed'], 2)

    def test_retries_are_atomic_and_persistent(self):
        quiz = self.quiz()
        body = self.submission(quiz)
        with ThreadPoolExecutor(max_workers=4) as pool:
            responses = list(pool.map(lambda _: self.submit(quiz, body), range(6)))
        self.assertTrue(all(r.status_code == 200 for r in responses))
        self.assertTrue(all(r.json() == responses[0].json() for r in responses))
        body['answers'].reverse()
        with TestClient(api.app, base_url="http://localhost") as restarted:
            response = restarted.post(f'/api/quizzes/{quiz["id"]}/submit', json=body, headers=self.headers)
        self.assertEqual(response.json(), responses[0].json())
        with closing(database.get_connection()) as conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM quiz_attempts').fetchone()[0], 1)
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM quiz_answers').fetchone()[0], 10)
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM quiz_submissions').fetchone()[0], 1)
        body['answers'][0]['option'] = 'B'
        self.assertEqual(self.submit(quiz, body).status_code, 409)

    def test_other_student_cannot_access_quiz_attempt_or_grades(self):
        quiz = self.quiz()
        body = self.submission(quiz)
        result = self.submit(quiz, body).json()
        other_quiz = self.quiz(self.other)
        self.assertNotEqual(quiz['id'], other_quiz['id'])
        self.assertEqual(self.client.get(f'/api/quizzes/{quiz["id"]}', headers=self.other).status_code, 404)
        self.assertEqual(self.submit(quiz, body, self.other).status_code, 404)
        self.assertEqual(self.client.get(f'/api/attempts/{result["attempt_id"]}', headers=self.other).status_code, 404)
        self.assertEqual(self.client.get('/api/grades', headers=self.other).json(), [])
        self.assertEqual(self.client.get('/api/me', headers=self.other).json()['stats']['completed'], 0)
        other_body = self.submission(other_quiz)
        other_body['submission_id'] = body['submission_id']
        self.assertEqual(self.submit(other_quiz, other_body, self.other).status_code, 200)

    def test_invalid_answers_do_not_write_attempts(self):
        quiz = self.quiz()
        for change, expected in [
            (lambda b: b.pop('submission_id'), 422),
            (lambda b: b.update(submission_id='bad'), 422),
            (lambda b: b['answers'].pop(), 400),
            (lambda b: b['answers'].append(b['answers'][0]), 400),
            (lambda b: b['answers'][0].update(question_id=999999), 400),
            (lambda b: b['answers'][0].update(option='E'), 422),
            (lambda b: b.update(answers=[]), 422),
        ]:
            body = self.submission(quiz)
            change(body)
            self.assertEqual(self.submit(quiz, body).status_code, expected)
        self.assertEqual(self.client.get('/api/grades', headers=self.headers).json(), [])
        self.assertEqual(self.client.get('/api/grades?offset=-1', headers=self.headers).status_code, 422)

    def test_snapshot_and_legacy_review(self):
        quiz = self.quiz()
        body = self.submission(quiz)
        result = self.submit(quiz, body).json()
        with closing(database.get_connection()) as conn, conn:
            conn.execute("UPDATE questions SET explanation='Changed after marking' WHERE quiz_id=?", (quiz['id'],))
        self.assertEqual(self.client.get(f'/api/attempts/{result["attempt_id"]}', headers=self.headers).json(), result)
        self.assertEqual(self.submit(quiz, body).json(), result)
        with closing(database.get_connection()) as conn, conn:
            conn.execute('DELETE FROM quiz_submissions')
        legacy = self.client.get(f'/api/attempts/{result["attempt_id"]}', headers=self.headers).json()
        self.assertEqual(legacy['score'], result['score'])
        self.assertEqual(len(legacy['feedback']), 10)

    def test_database_failure_rolls_back_and_retry_succeeds(self):
        quiz = self.quiz()
        body = self.submission(quiz)
        with closing(database.get_connection()) as conn, conn:
            conn.execute("""CREATE TRIGGER fail_receipt BEFORE INSERT ON quiz_submissions
                            BEGIN SELECT RAISE(ABORT, 'Synthetic failure'); END""")
        with TestClient(api.app, base_url="http://localhost", raise_server_exceptions=False) as client:
            response = client.post(f'/api/quizzes/{quiz["id"]}/submit', json=body, headers=self.headers)
            self.assertEqual(response.status_code, 500)
        with closing(database.get_connection()) as conn, conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM quiz_attempts').fetchone()[0], 0)
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM quiz_answers').fetchone()[0], 0)
            conn.execute('DROP TRIGGER fail_receipt')
        self.assertEqual(self.submit(quiz, body).status_code, 200)

    def test_additive_initialization_preserves_existing_data(self):
        quiz = self.quiz()
        result = self.submit(quiz, self.submission(quiz)).json()
        database.initialize_database()
        database.initialize_database()
        self.assertEqual(self.client.get(f'/api/attempts/{result["attempt_id"]}', headers=self.headers).json(), result)

    def test_api_restart_preserves_pending_approval_metadata(self):
        self.quiz()
        with closing(database.get_connection()) as conn, conn:
            cursor = conn.execute(
                "INSERT INTO approval_requests (telegram_user_id, proposed_changes_json) VALUES (?, '{}')",
                (101,),
            )
            approval_id = cursor.lastrowid
            conn.execute(
                "INSERT INTO calendar_schedule_requests (approval_id, google_event_id) VALUES (?, ?)",
                (approval_id, 'synthetic-event-id'),
            )
            before = dict(conn.execute('SELECT * FROM approval_requests WHERE id=?', (approval_id,)).fetchone())
        with TestClient(api.app, base_url='http://localhost') as restarted:
            self.assertEqual(restarted.get('/health').status_code, 200)
        with closing(database.get_connection()) as conn:
            after = dict(conn.execute('SELECT * FROM approval_requests WHERE id=?', (approval_id,)).fetchone())
        self.assertEqual(after, before)


if __name__ == '__main__':
    unittest.main()
