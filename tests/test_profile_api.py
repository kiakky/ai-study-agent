from contextlib import closing
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from fastapi.testclient import TestClient
from starlette.middleware import Middleware
from starlette.middleware.trustedhost import TrustedHostMiddleware

from test_api import api, database, signed_data, TEST_TOKEN
from test_google_oauth import MockGoogle, SyntheticCipher
from routes import profile as profile_routes
from services.google_oauth import COOKIE_NAME, GoogleOAuthService, OAuthSettings

ROOT = Path(__file__).resolve().parents[1]
ORIGIN = 'https://study.example'


class ProfileAPITests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory(prefix='quiz-test-', dir=ROOT / 'tests')
        self.addCleanup(self.folder.cleanup)
        self.database_patch = patch.object(database, 'DB_PATH', Path(self.folder.name) / 'test.db')
        self.database_patch.start()
        self.addCleanup(self.database_patch.stop)
        self.token_patch = patch.object(api, 'BOT_TOKEN', TEST_TOKEN)
        self.token_patch.start()
        self.addCleanup(self.token_patch.stop)
        self.provider = MockGoogle()
        self.service = GoogleOAuthService(OAuthSettings(True, ORIGIN), self.provider, SyntheticCipher())
        self.service_patch = patch.object(profile_routes, 'google_service', return_value=self.service)
        self.service_patch.start()
        self.addCleanup(self.service_patch.stop)
        self.middleware = list(api.app.user_middleware)
        api.app.user_middleware = [middleware for middleware in self.middleware if middleware.cls is not TrustedHostMiddleware]
        api.app.user_middleware.append(Middleware(TrustedHostMiddleware, allowed_hosts=['study.example','localhost']))
        api.app.middleware_stack = None
        self.addCleanup(self.restore_middleware)
        self.client = TestClient(api.app, base_url=ORIGIN)
        self.client.__enter__()
        self.addCleanup(self.client.__exit__, None, None, None)
        self.headers = {'X-Telegram-Init-Data': signed_data(101)}
        self.other = {'X-Telegram-Init-Data': signed_data(202)}

    def restore_middleware(self):
        api.app.user_middleware = self.middleware
        api.app.middleware_stack = None

    def test_profile_endpoints_require_signed_identity_and_never_gate_demo_quizzes(self):
        for method, path, body in [('GET','/api/profile',None),('GET','/api/studyplan',None),
            ('PUT','/api/preferences',{}),('POST','/api/onboarding/skip',{'integration':'google'}),
            ('POST','/api/integrations/google/connect',None),('DELETE','/api/integrations/google',None),
            ('POST','/api/integrations/google/availability',{})]:
            self.assertEqual(self.client.request(method,path,json=body).status_code,401)
        self.service.settings = OAuthSettings(False, ORIGIN)
        self.assertEqual(self.client.get('/api/quizzes',headers=self.other).status_code,200)
        self.assertEqual(self.client.get('/api/profile',headers=self.headers).json()['google']['status'],'disconnected')
        self.assertEqual(self.client.post('/api/integrations/google/connect',headers=self.headers).status_code,503)
        for asset in ['/setup','/static/setup.js','/static/setup.css']:
            self.assertEqual(self.client.get(asset).status_code,200)

    def test_preferences_are_owned_validated_and_resume_setup(self):
        for integration in ['google','brightspace']:
            self.assertEqual(self.client.post('/api/onboarding/skip',json={'integration':integration},headers=self.headers).status_code,200)
        saved = {'study_days':['mon','fri'],'study_windows':[{'start':'18:00','end':'19:30'}],
                 'session_minutes':20,'practice_format':'mixed','question_count':8,'notes':'Synthetic preferences'}
        response=self.client.put('/api/preferences',json=saved,headers=self.headers)
        self.assertEqual(response.status_code,200)
        self.assertEqual(response.json()['onboarding']['stage'],'completed')
        self.assertEqual(self.client.get('/api/profile',headers=self.other).json()['preferences']['notes'],'')
        saved['session_minutes']=0
        self.assertEqual(self.client.put('/api/preferences',json=saved,headers=self.headers).status_code,422)
        self.assertEqual(self.client.get('/api/profile',headers=self.headers).json()['preferences']['session_minutes'],20)
        self.assertEqual(self.client.get('/api/studyplan',headers=self.headers).json()['state'],'not_generated')

    def test_google_callback_cookie_single_use_and_redacted_response(self):
        response=self.client.post('/api/integrations/google/connect',headers=self.headers)
        self.assertEqual(response.status_code,200)
        link=response.json()['authorization_start_url']
        started=self.client.get(link,follow_redirects=False)
        self.assertEqual(started.status_code,303)
        self.assertIn('Secure',started.headers['set-cookie'])
        self.assertIn('HttpOnly',started.headers['set-cookie'])
        self.assertIn('SameSite=lax',started.headers['set-cookie'])
        state=parse_qs(urlsplit(started.headers['location']).query)['state'][0]
        callback=self.client.get('/oauth/google/callback',params={'state':state,'code':'account-a'})
        self.assertEqual(callback.status_code,200)
        self.assertNotIn('synthetic-access',callback.text)
        self.assertNotIn(state,callback.text)
        public=self.client.get('/api/profile',headers=self.headers).json()
        self.assertEqual(public['google']['status'],'connected')
        self.assertNotIn('refresh_token',json.dumps(public))
        self.assertEqual(self.client.get('/api/profile',headers=self.other).json()['google']['status'],'disconnected')
        self.assertEqual(self.client.get('/oauth/google/callback',params={'state':state,'code':'a'}).status_code,400)
        self.assertEqual(self.client.get('/oauth/google/callback?state=bogus&code=secret').status_code,400)

    def test_wrong_origin_and_missing_cookie_do_not_exchange_tokens(self):
        link=self.client.post('/api/integrations/google/connect',headers=self.headers).json()['authorization_start_url']
        started=self.client.get(link,follow_redirects=False)
        state=parse_qs(urlsplit(started.headers['location']).query)['state'][0]
        self.client.cookies.clear()
        self.assertEqual(self.client.get('/oauth/google/callback',params={'state':state,'code':'account-a'}).status_code,400)
        self.assertEqual(self.provider.exchanges,[])
        with TestClient(api.app,base_url='http://localhost') as local:
            self.assertEqual(local.get('/oauth/google/start?ticket=synthetic').status_code,400)

    def test_warning_redirect_losing_ticket_or_cookie_fails_closed(self):
        link=self.client.post('/api/integrations/google/connect',headers=self.headers).json()['authorization_start_url']
        self.assertEqual(self.client.get('/oauth/google/start',follow_redirects=False).status_code,400)
        self.assertEqual(self.provider.exchanges,[])
        started=self.client.get(link,follow_redirects=False)
        self.assertEqual(started.status_code,303)
        state=parse_qs(urlsplit(started.headers['location']).query)['state'][0]
        self.client.cookies.clear()
        response=self.client.get('/oauth/google/callback',params={'state':state,'code':'synthetic-code'})
        self.assertEqual(response.status_code,400)
        self.assertNotIn(state,response.text)
        self.assertEqual(self.provider.exchanges,[])
        self.assertEqual(self.client.get('/api/profile',headers=self.headers).json()['google']['status'],'disconnected')

    def test_availability_is_bound_to_the_caller_and_range_is_validated(self):
        start=datetime.now(timezone.utc)
        window={'start':start.isoformat(),'end':(start+timedelta(hours=1)).isoformat()}
        self.assertEqual(self.client.post('/api/integrations/google/availability',headers=self.other,json=window).status_code,409)
        window['end']=(start+timedelta(days=32)).isoformat()
        self.assertEqual(self.client.post('/api/integrations/google/availability',headers=self.headers,json=window).status_code,422)


if __name__ == '__main__':
    unittest.main()
