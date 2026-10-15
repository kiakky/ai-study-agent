from contextlib import closing
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
from types import SimpleNamespace
from urllib.parse import parse_qs, urlencode, urlsplit
import base64
import hashlib

from cryptography.fernet import Fernet
from google.auth.exceptions import RefreshError
from googleapiclient.errors import HttpError
from httplib2 import Response

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))
import database
from services import profiles
from services.google_oauth import (
    FREEBUSY_SCOPE, GoogleOAuthService, GoogleProvider, InvalidOAuthRequest,
    OAuthSettings, OAuthUnavailable, ReconnectRequired,
)
from services.token_cipher import TokenStorageError, WindowsTokenCipher


class SyntheticCipher:
    def __init__(self):
        self.key = Fernet(Fernet.generate_key())

    def encrypt(self, user_id, value):
        return self.key.encrypt(json.dumps({'owner':user_id,'value':value}).encode())

    def decrypt(self, user_id, value):
        decoded = json.loads(self.key.decrypt(bytes(value)))
        if decoded['owner'] != user_id:
            raise TokenStorageError('Synthetic owner mismatch')
        return decoded['value']


class MockGoogle:
    def __init__(self):
        self.exchanges = []
        self.reads = []
        self.refresh_error = None
        self.exchange_hook = None

    def flow(self, settings, verifier, state=None):
        return None

    def authorization_url(self, settings, verifier, state):
        self.last_verifier = verifier
        return 'https://accounts.google.com/o/oauth2/auth?' + urlencode({'state':state,'redirect_uri':settings.redirect_uri})

    def exchange(self, settings, verifier, state, code):
        self.exchanges.append((verifier, state, code))
        if self.exchange_hook:
            self.exchange_hook()
        return {'token': 'synthetic-access-'+code, 'refresh_token':'synthetic-refresh-'+code,
                'scopes':list(settings.scopes), 'expiry':(datetime.now(timezone.utc)+timedelta(hours=1)).isoformat(),
                'client_id':'synthetic-client','client_secret':'synthetic-secret','token_uri':'https://oauth2.googleapis.com/token'}

    def refresh(self, value):
        if self.refresh_error:
            raise self.refresh_error
        return {**value, 'expiry':(datetime.now(timezone.utc)+timedelta(hours=1)).isoformat()}

    def freebusy(self, value, start, end):
        self.reads.append(value['token'])
        return [{'start':start.isoformat(),'end':end.isoformat()}]


class GoogleOAuthTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory(prefix='quiz-test-', dir=ROOT / 'tests')
        self.addCleanup(self.folder.cleanup)
        self.database_patch = patch.object(database, 'DB_PATH', Path(self.folder.name) / 'oauth.db')
        self.database_patch.start()
        self.addCleanup(self.database_patch.stop)
        database.initialize_database(backfill_approvals=False)
        self.provider = MockGoogle()
        self.service = GoogleOAuthService(OAuthSettings(True,'https://study.example'), self.provider, SyntheticCipher())

    def browser_flow(self, user_id):
        link = self.service.begin(user_id)['authorization_start_url']
        ticket = parse_qs(urlsplit(link).query)['ticket'][0]
        url, cookie = self.service.start_browser(ticket)
        state = parse_qs(urlsplit(url).query)['state'][0]
        return state, cookie, ticket

    def connect(self, user_id, code):
        state, cookie, ticket = self.browser_flow(user_id)
        self.assertTrue(self.service.callback(state, cookie, code=code))
        return state, cookie

    def test_single_use_state_pkce_encrypted_storage_and_restart(self):
        state, cookie, ticket = self.browser_flow(101)
        self.assertGreaterEqual(len(self.provider.last_verifier), 43)
        self.assertLessEqual(len(self.provider.last_verifier), 128)
        self.assertRaises(InvalidOAuthRequest, self.service.start_browser, ticket)
        self.assertRaises(InvalidOAuthRequest, self.service.callback, state, 'wrong-cookie', code='account-a')
        self.assertEqual(self.provider.exchanges, [])
        self.service.callback(state, cookie, code='account-a')
        self.assertRaises(InvalidOAuthRequest, self.service.callback, state, cookie, code='account-a')
        with closing(database.get_connection()) as connection:
            row = connection.execute('SELECT * FROM google_connections WHERE telegram_user_id=101').fetchone()
            self.assertNotIn(b'synthetic-access', row['encrypted_credentials'])
            self.assertNotIn('token', json.dumps(self.service.status(101)))
        restarted = GoogleOAuthService(self.service.settings, self.provider, self.service.cipher)
        self.assertEqual(restarted.status(101)['status'], 'connected')
        self.assertEqual(profiles.get_profile(101)['onboarding']['stage'], 'brightspace')

    def test_users_cannot_swap_cookies_or_use_another_calendar(self):
        first_state, first_cookie, unused = self.browser_flow(101)
        second_state, second_cookie, unused = self.browser_flow(202)
        self.assertRaises(InvalidOAuthRequest, self.service.callback, first_state, second_cookie, code='wrong')
        self.service.callback(first_state, first_cookie, code='account-a')
        self.service.callback(second_state, second_cookie, code='account-b')
        start = datetime.now(timezone.utc)
        self.service.availability(101, start, start+timedelta(hours=1))
        self.service.availability(202, start, start+timedelta(hours=1))
        self.assertEqual(self.provider.reads, ['synthetic-access-account-a','synthetic-access-account-b'])
        self.assertRaises(ReconnectRequired, self.service.availability, 303, start, start+timedelta(hours=1))

    def test_expiry_cancellation_refresh_and_revocation_states(self):
        state, cookie, unused = self.browser_flow(101)
        with patch.object(self.service, 'clock', return_value=self.service.clock()+601):
            self.assertRaises(InvalidOAuthRequest, self.service.callback, state, cookie, code='a')
        state, cookie, unused = self.browser_flow(101)
        self.assertFalse(self.service.callback(state, cookie, error='access_denied'))
        self.assertEqual(self.service.status(101)['status'], 'disconnected')
        self.connect(101, 'a')
        with closing(database.get_connection()) as connection, connection:
            connection.execute('UPDATE google_connections SET expires_at=0 WHERE telegram_user_id=101')
        self.assertEqual(self.service.status(101)['status'], 'connected')
        start = datetime.now(timezone.utc)
        self.service.availability(101, start, start+timedelta(hours=1))
        self.assertEqual(self.service.status(101)['status'], 'connected')
        with closing(database.get_connection()) as connection, connection:
            connection.execute('UPDATE google_connections SET expires_at=0 WHERE telegram_user_id=101')
        self.provider.refresh_error = RefreshError('synthetic invalid_grant')
        self.assertRaises(ReconnectRequired, self.service.availability, 101, start, start+timedelta(hours=1))
        self.assertEqual(self.service.status(101)['status'], 'revoked')
        self.service.disconnect(101)
        self.assertEqual(self.service.status(101)['status'], 'disconnected')

    def test_reconnect_race_does_not_overwrite_newer_connection(self):
        state, cookie, unused = self.browser_flow(101)
        self.provider.exchange_hook = lambda: self.service.begin(101)
        self.assertRaises(ReconnectRequired, self.service.callback, state, cookie, code='stale')
        self.assertEqual(self.service.status(101)['status'], 'disconnected')

    def test_freebusy_redaction_range_and_disconnect_during_request(self):
        self.connect(101,'a')
        start=datetime.now(timezone.utc)
        self.provider.freebusy=lambda value,a,b:[{'start':a.isoformat(),'end':b.isoformat(),'summary':'private event must not escape'}]
        value=self.service.availability(101,start,start+timedelta(hours=1))
        self.assertEqual(set(value['busy'][0]),{'start','end'})
        self.assertRaises(ValueError,self.service.availability,101,start.replace(tzinfo=None),start+timedelta(hours=1))
        self.assertRaises(ValueError,self.service.availability,101,start,start+timedelta(days=32))
        def disconnect(value,a,b):
            self.service.disconnect(101)
            return []
        self.provider.freebusy=disconnect
        self.assertRaises(ReconnectRequired,self.service.availability,101,start,start+timedelta(hours=1))
        self.assertEqual(self.service.status(101)['status'],'disconnected')

    def test_freebusy_api_errors_never_fall_back_or_clear_transient_connections(self):
        self.connect(101,'a')
        start=datetime.now(timezone.utc)
        def transient(value,a,b): raise HttpError(Response({'status':'503'}),b'{}')
        self.provider.freebusy=transient
        self.assertRaises(ReconnectRequired,self.service.availability,101,start,start+timedelta(hours=1))
        self.assertEqual(self.service.status(101)['status'],'connected')
        def denied(value,a,b): raise HttpError(Response({'status':'403'}),b'{}')
        self.provider.freebusy=denied
        self.assertRaises(ReconnectRequired,self.service.availability,101,start,start+timedelta(hours=1))
        self.assertEqual(self.service.status(101)['status'],'reconnect_required')

    def test_missing_refresh_token_preserves_existing_connection(self):
        self.connect(101, 'original')
        original_exchange = self.provider.exchange
        def incomplete(*args):
            value = original_exchange(*args)
            value.pop('refresh_token')
            return value
        self.provider.exchange = incomplete
        state, cookie, unused = self.browser_flow(101)
        self.assertRaises(ReconnectRequired, self.service.callback, state, cookie, code='incomplete')
        self.assertEqual(self.service.status(101)['status'], 'connected')

    def test_disabled_oauth_and_redirect_validation(self):
        disabled = GoogleOAuthService(OAuthSettings(False,'https://study.example'),self.provider,self.service.cipher)
        self.assertRaises(OAuthUnavailable, disabled.begin, 101)
        self.assertRaises(ValueError, OAuthSettings, True, 'http://study.example')
        client = Path(self.folder.name) / 'synthetic-client.json'
        client.write_text(json.dumps({'web': {'client_id':'synthetic','client_secret':'synthetic',
            'auth_uri':'https://accounts.google.com/o/oauth2/auth','token_uri':'https://oauth2.googleapis.com/token',
            'redirect_uris':['https://wrong.example/oauth/google/callback']}}))
        self.assertRaises(OAuthUnavailable, GoogleProvider().flow, OAuthSettings(True,'https://study.example',client), 'synthetic-verifier')

    def test_google_library_generates_s256_pkce_and_supports_future_incremental_scopes(self):
        client = Path(self.folder.name) / 'synthetic-client.json'
        client.write_text(json.dumps({'web': {'client_id':'synthetic','client_secret':'synthetic',
            'auth_uri':'https://accounts.google.com/o/oauth2/auth','token_uri':'https://oauth2.googleapis.com/token',
            'redirect_uris':['https://study.example/oauth/google/callback']}}))
        settings = OAuthSettings(True,'https://study.example',client)
        verifier = 'synthetic-verifier-' + 'a'*64
        url = GoogleProvider().authorization_url(settings,verifier,'synthetic-state')
        parameters = parse_qs(urlsplit(url).query)
        expected = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip('=')
        self.assertEqual(parameters['code_challenge'],[expected])
        self.assertEqual(parameters['code_challenge_method'],['S256'])
        self.assertEqual(parameters['scope'],[FREEBUSY_SCOPE])
        self.assertEqual(parameters['redirect_uri'],[settings.redirect_uri])
        writing = OAuthSettings(True,'https://study.example',client,(FREEBUSY_SCOPE,'https://www.googleapis.com/auth/calendar.events.owned'))
        self.assertIn('calendar.events.owned',GoogleProvider().authorization_url(writing,verifier,'synthetic-state'))

    def test_partial_google_grant_is_rejected_even_if_requested_scopes_were_present(self):
        provider = GoogleProvider()
        credentials = SimpleNamespace(granted_scopes=[],scopes=[FREEBUSY_SCOPE])
        flow = SimpleNamespace(credentials=credentials,fetch_token=lambda **options: None)
        with patch.object(provider,'flow',return_value=flow):
            self.assertRaises(ReconnectRequired,provider.exchange,self.service.settings,'synthetic-verifier','synthetic-state','synthetic-code')

    @unittest.skipUnless(os.name == 'nt', 'Windows DPAPI test')
    def test_real_dpapi_binds_synthetic_credentials_to_owner(self):
        cipher = WindowsTokenCipher()
        encrypted = cipher.encrypt(101, {'token':'synthetic-only'})
        self.assertEqual(cipher.decrypt(101, encrypted), {'token':'synthetic-only'})
        self.assertRaises(TokenStorageError, cipher.decrypt, 202, encrypted)


if __name__ == '__main__':
    unittest.main()
