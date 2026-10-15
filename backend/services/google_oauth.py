from contextlib import closing
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import json
from pathlib import Path
import secrets
import time

from google.auth.exceptions import RefreshError
from google.auth.transport.requests import Request as GoogleRequest
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from database import get_connection
from deployment import ROOT, load_deployment, validate_public_url
from services import profiles
from services.token_cipher import TokenStorageError, WindowsTokenCipher
from models.planning import BusyPeriod

FREEBUSY_SCOPE = 'https://www.googleapis.com/auth/calendar.freebusy'
EVENT_SCOPE = 'https://www.googleapis.com/auth/calendar.events.owned'
COOKIE_NAME = '__Secure-study-google'
FLOW_SECONDS = 600
CLIENT_PATH = ROOT / 'backend' / 'credentials' / 'google_multiuser_client.json'
SETTINGS_PATH = ROOT / 'config' / 'google_oauth.json'


class OAuthUnavailable(Exception):
    pass


class InvalidOAuthRequest(Exception):
    pass


class ReconnectRequired(Exception):
    pass


@dataclass(frozen=True)
class OAuthSettings:
    enabled: bool
    origin: str
    client_path: Path = CLIENT_PATH
    scopes: tuple[str, ...] = (FREEBUSY_SCOPE,)
    event_writes_enabled: bool = False

    def __post_init__(self):
        if self.origin:
            object.__setattr__(self, 'origin', validate_public_url(self.origin))
        if not self.scopes or FREEBUSY_SCOPE not in self.scopes:
            raise ValueError('Calendar availability permission is required.')

    @property
    def redirect_uri(self):
        return self.origin + '/oauth/google/callback'


def load_settings():
    enabled = False
    event_writes_enabled = False
    if SETTINGS_PATH.exists():
        try:
            values = json.loads(SETTINGS_PATH.read_text(encoding='utf-8-sig'))
            if (not isinstance(values, dict) or set(values)-{'enabled','event_writes_enabled'}
                    or type(values.get('enabled')) is not bool
                    or type(values.get('event_writes_enabled',False)) is not bool):
                raise OAuthUnavailable('Google OAuth settings require boolean feature flags.')
            enabled = values['enabled']
            event_writes_enabled = values.get('event_writes_enabled',False)
        except (OSError, ValueError):
            raise OAuthUnavailable('Google OAuth settings are invalid.') from None
    return OAuthSettings(enabled, load_deployment().mini_app_url,event_writes_enabled=event_writes_enabled)


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


class GoogleProvider:
    def calendar(self,value):
        credentials=Credentials.from_authorized_user_info(value,scopes=value['scopes'])
        return build('calendar','v3',credentials=credentials,cache_discovery=False)

    def get_event(self,value,event_id):
        try:
            return self.calendar(value).events().get(calendarId='primary',eventId=event_id,fields='id,status,summary,start,end,extendedProperties/private,etag').execute()
        except HttpError as error:
            if error.resp.status==404: return None
            raise

    def create_event(self,value,event_id,body):
        return self.calendar(value).events().insert(calendarId='primary',body={'id':event_id,**body},sendUpdates='none').execute()

    def reschedule_event(self,value,event_id,body,etag):
        request=self.calendar(value).events().patch(calendarId='primary',eventId=event_id,
            body={'start':body['start'],'end':body['end']},sendUpdates='none')
        request.headers['If-Match']=etag
        return request.execute()

    def cancel_event(self,value,event_id,etag):
        request=self.calendar(value).events().delete(calendarId='primary',eventId=event_id,sendUpdates='none')
        request.headers['If-Match']=etag
        request.execute()

    def flow(self, settings, verifier, state=None):
        try:
            config = json.loads(settings.client_path.read_text(encoding='utf-8-sig'))
            web = config.get('web', {})
            if (not web.get('client_id') or not web.get('client_secret')
                    or web.get('auth_uri') != 'https://accounts.google.com/o/oauth2/auth'
                    or web.get('token_uri') != 'https://oauth2.googleapis.com/token'
                    or settings.redirect_uri not in web.get('redirect_uris', [])):
                raise OAuthUnavailable('Configure the separate Web application client with the exact redirect URI.')
            return Flow.from_client_config(config, scopes=list(settings.scopes), redirect_uri=settings.redirect_uri,
                                          code_verifier=verifier, state=state)
        except (OSError, ValueError, AttributeError):
            raise OAuthUnavailable('Google Web application OAuth credentials are not configured.') from None

    def authorization_url(self, settings, verifier, state):
        flow = self.flow(settings, verifier, state)
        url, returned_state = flow.authorization_url(
            state=state, access_type='offline', prompt='consent', include_granted_scopes='false',
        )
        if returned_state != state:
            raise InvalidOAuthRequest('OAuth state mismatch.')
        return url

    def exchange(self, settings, verifier, state, code):
        flow = self.flow(settings, verifier, state)
        flow.fetch_token(code=code)
        granted = flow.credentials.granted_scopes if flow.credentials.granted_scopes is not None else flow.credentials.scopes
        if not set(settings.scopes).issubset(granted or []):
            raise ReconnectRequired('Required calendar permission was not granted.')
        value = json.loads(flow.credentials.to_json())
        value['scopes'] = list(granted)
        return value

    def refresh(self, value):
        credentials = Credentials.from_authorized_user_info(value, scopes=value['scopes'])
        credentials.refresh(GoogleRequest())
        return json.loads(credentials.to_json())

    def freebusy(self, value, start, end):
        credentials = Credentials.from_authorized_user_info(value, scopes=value['scopes'])
        service = build('calendar', 'v3', credentials=credentials, cache_discovery=False)
        response = service.freebusy().query(body={
            'timeMin': start.isoformat(), 'timeMax': end.isoformat(), 'items': [{'id': 'primary'}],
        }).execute()
        calendar = response.get('calendars', {}).get('primary')
        if not calendar or calendar.get('errors'):
            raise ReconnectRequired('Google did not return calendar availability.')
        return calendar.get('busy', [])


class GoogleOAuthService:
    def __init__(self, settings=None, provider=None, cipher=None, clock=None):
        self.settings = settings or load_settings()
        self.provider = provider or GoogleProvider()
        self.cipher = cipher or WindowsTokenCipher()
        self.clock = clock or time.time

    def status(self, user_id):
        with closing(get_connection()) as connection:
            row = connection.execute('SELECT status, expires_at, scopes_json FROM google_connections WHERE telegram_user_id=?', (user_id,)).fetchone()
        status = row['status'] if row else 'disconnected'
        # Access-token expiry is routine: availability/event calls refresh it.
        if status == 'expired':
            status = 'connected'
        granted=set(json.loads(row['scopes_json'])) if row else set()
        return {'available': self.settings.enabled and bool(self.settings.origin), 'status': status,
                'scope': 'calendar_events_owned' if EVENT_SCOPE in granted else 'free_busy_only',
                'event_writes_enabled': self.settings.enabled and self.settings.event_writes_enabled,
                'event_permission_granted':EVENT_SCOPE in granted,'calendar_actions_available':True}

    def require_available(self):
        if not self.settings.enabled or not self.settings.origin:
            raise OAuthUnavailable('Google connection is unavailable until the developer configures OAuth. You can skip it.')

    def begin(self, user_id, write_access=False):
        self.require_available()
        if write_access and not self.settings.event_writes_enabled:
            raise OAuthUnavailable('Calendar event authorization is not enabled.')
        # A routine reconnect must not silently downgrade an already-granted scope.
        retain_events=self.settings.event_writes_enabled and self.status(user_id)['event_permission_granted']
        flow_settings=replace(self.settings,scopes=(FREEBUSY_SCOPE,EVENT_SCOPE)) if write_access or retain_events else self.settings
        profiles.ensure_profile(user_id)
        state, ticket = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        verifier = secrets.token_urlsafe(64)
        self.provider.flow(flow_settings, verifier, state)
        encrypted = self.cipher.encrypt(user_id, {'state': state, 'verifier': verifier, 'scopes': list(flow_settings.scopes)})
        with closing(get_connection()) as connection, connection:
            connection.execute('BEGIN IMMEDIATE')
            connection.execute('INSERT OR IGNORE INTO google_connections (telegram_user_id) VALUES (?)', (user_id,))
            generation = connection.execute('SELECT generation FROM google_connections WHERE telegram_user_id=?', (user_id,)).fetchone()[0]
            connection.execute("UPDATE google_oauth_requests SET phase='failed',encrypted_verifier=? WHERE telegram_user_id=? AND phase IN ('issued','authorizing','processing')", (b'', user_id))
            connection.execute('INSERT INTO google_oauth_requests (state_hash,ticket_hash,telegram_user_id,generation,encrypted_verifier,expires_at) VALUES (?,?,?,?,?,?)',
                               (digest(state), digest(ticket), user_id, generation, encrypted, self.clock()+FLOW_SECONDS))
        return {'authorization_start_url': self.settings.origin + '/oauth/google/start?ticket=' + ticket}

    def start_browser(self, ticket):
        self.require_available()
        if not isinstance(ticket, str) or not 20 <= len(ticket) <= 200:
            raise InvalidOAuthRequest('Invalid or expired OAuth link.')
        browser = secrets.token_urlsafe(32)
        with closing(get_connection()) as connection, connection:
            connection.execute('BEGIN IMMEDIATE')
            row = connection.execute("SELECT * FROM google_oauth_requests WHERE ticket_hash=? AND phase='issued' AND expires_at>?", (digest(ticket), self.clock())).fetchone()
            if not row:
                raise InvalidOAuthRequest('Invalid or expired OAuth link.')
            connection.execute("UPDATE google_oauth_requests SET phase='authorizing', browser_hash=? WHERE state_hash=?", (digest(browser), row['state_hash']))
        secret = self.cipher.decrypt(row['telegram_user_id'], row['encrypted_verifier'])
        if EVENT_SCOPE in secret['scopes'] and not self.settings.event_writes_enabled:
            raise OAuthUnavailable('Calendar event authorization was disabled.')
        flow_settings=replace(self.settings,scopes=tuple(secret['scopes']))
        return self.provider.authorization_url(flow_settings, secret['verifier'], secret['state']), browser

    def callback(self, state, browser, code=None, error=None):
        self.require_available()
        if not isinstance(state, str) or not 20 <= len(state) <= 200 or not browser or len(browser) > 200:
            raise InvalidOAuthRequest('Invalid or expired OAuth callback.')
        with closing(get_connection()) as connection, connection:
            connection.execute('BEGIN IMMEDIATE')
            row = connection.execute("SELECT * FROM google_oauth_requests WHERE state_hash=? AND phase='authorizing' AND expires_at>?", (digest(state), self.clock())).fetchone()
            if not row or not hmac.compare_digest(row['browser_hash'] or '', digest(browser)):
                raise InvalidOAuthRequest('Invalid or expired OAuth callback.')
            if error:
                connection.execute("UPDATE google_oauth_requests SET phase='failed',encrypted_verifier=? WHERE state_hash=?", (b'', row['state_hash']))
                return False
            if not code or len(code) > 4096:
                raise InvalidOAuthRequest('Google did not supply a valid authorization code.')
            connection.execute("UPDATE google_oauth_requests SET phase='processing' WHERE state_hash=?", (row['state_hash'],))
        try:
            secret = self.cipher.decrypt(row['telegram_user_id'], row['encrypted_verifier'])
            if EVENT_SCOPE in secret['scopes'] and not self.settings.event_writes_enabled:
                raise OAuthUnavailable('Calendar event authorization was disabled.')
            flow_settings=replace(self.settings,scopes=tuple(secret['scopes']))
            value = self.provider.exchange(flow_settings, secret['verifier'], state, code)
            if not value.get('token') or not value.get('refresh_token') or not set(secret['scopes']).issubset(value.get('scopes', [])):
                raise ReconnectRequired('Reconnect Google and grant offline availability access.')
            encrypted = self.cipher.encrypt(row['telegram_user_id'], value)
            expiry = token_expiry(value)
            with closing(get_connection()) as connection, connection:
                connection.execute('BEGIN IMMEDIATE')
                current = connection.execute('SELECT phase FROM google_oauth_requests WHERE state_hash=?', (row['state_hash'],)).fetchone()
                updated = connection.execute(
                    "UPDATE google_connections SET status='connected',encrypted_credentials=?,scopes_json=?,expires_at=?,generation=generation+1,updated_at=CURRENT_TIMESTAMP WHERE telegram_user_id=? AND generation=? AND ?='processing'",
                    (encrypted, json.dumps(secret['scopes']), expiry, row['telegram_user_id'], row['generation'], current['phase']),
                )
                if not updated.rowcount:
                    raise InvalidOAuthRequest('This OAuth request was superseded. Reconnect from Telegram.')
                connection.execute("UPDATE google_oauth_requests SET phase='completed',encrypted_verifier=? WHERE state_hash=?", (b'', row['state_hash']))
                connection.execute(
                    "UPDATE user_onboarding SET google_choice='connected', stage=CASE WHEN stage='google' THEN 'brightspace' ELSE stage END, updated_at=CURRENT_TIMESTAMP WHERE telegram_user_id=?",
                    (row['telegram_user_id'],),
                )
            return True
        except Exception:
            with closing(get_connection()) as connection, connection:
                connection.execute("UPDATE google_oauth_requests SET phase='failed',encrypted_verifier=? WHERE state_hash=?", (b'', row['state_hash']))
            raise ReconnectRequired('Google consent was not completed. Any previous connection was retained; return to Telegram to check it.') from None

    def disconnect(self, user_id):
        with closing(get_connection()) as connection, connection:
            connection.execute('BEGIN IMMEDIATE')
            connection.execute("UPDATE google_connections SET status='disconnected',encrypted_credentials=NULL,scopes_json='[]',expires_at=NULL,generation=generation+1,updated_at=CURRENT_TIMESTAMP WHERE telegram_user_id=?", (user_id,))
            connection.execute("UPDATE google_oauth_requests SET phase='failed',encrypted_verifier=? WHERE telegram_user_id=? AND phase IN ('issued','authorizing','processing')", (b'', user_id))

    def availability(self, user_id, start, end):
        if start.tzinfo is None or end.tzinfo is None or end <= start or end-start > timedelta(days=31):
            raise ValueError('Use an aware availability range of up to 31 days.')
        self.require_available()
        with closing(get_connection()) as connection:
            row = connection.execute('SELECT * FROM google_connections WHERE telegram_user_id=?', (user_id,)).fetchone()
        if not row or row['status'] not in {'connected', 'expired'} or not row['encrypted_credentials']:
            raise ReconnectRequired('Connect your own Google account first.')
        try:
            value = self.cipher.decrypt(user_id, row['encrypted_credentials'])
        except TokenStorageError:
            self.set_connection_state(user_id, row['generation'], 'reconnect_required')
            raise ReconnectRequired('Stored credentials cannot be unlocked. Reconnect Google.') from None
        try:
            if row['expires_at'] is not None and row['expires_at'] <= self.clock()+300:
                value = self.provider.refresh(value)
                with closing(get_connection()) as connection, connection:
                    saved = connection.execute(
                        "UPDATE google_connections SET status='connected',encrypted_credentials=?,expires_at=? WHERE telegram_user_id=? AND generation=? AND status IN ('connected','expired')",
                        (self.cipher.encrypt(user_id, value), token_expiry(value), user_id, row['generation']),
                    )
                    if not saved.rowcount:
                        raise ReconnectRequired('Your connection changed. Try again from Telegram.')
            busy = [BusyPeriod.model_validate(period).model_dump(mode='json')
                    for period in self.provider.freebusy(value, start, end)]
            # A connection can be disconnected/replaced while its request is in flight.
            with closing(get_connection()) as connection:
                current = connection.execute('SELECT generation,status FROM google_connections WHERE telegram_user_id=?', (user_id,)).fetchone()
            if not current or current['generation'] != row['generation'] or current['status'] not in {'connected','expired'}:
                raise ReconnectRequired('Your connection changed. Request availability again.')
            return {'busy': busy}
        except ReconnectRequired:
            self.set_connection_state(user_id, row['generation'], 'reconnect_required')
            raise
        except RefreshError as error:
            if not getattr(error, 'retryable', False):
                self.set_connection_state(user_id, row['generation'], 'revoked')
            raise ReconnectRequired('Google access could not be refreshed. Reconnect if access expired or was revoked.') from None
        except HttpError as error:
            if error.resp.status in (401, 403):
                self.set_connection_state(user_id, row['generation'], 'reconnect_required')
            raise ReconnectRequired('Google availability is unavailable. Review your connection.') from None

    def set_connection_state(self, user_id, generation, status):
        with closing(get_connection()) as connection, connection:
            connection.execute('UPDATE google_connections SET status=?,updated_at=CURRENT_TIMESTAMP WHERE telegram_user_id=? AND generation=?', (status, user_id, generation))

    def event_credentials(self,user_id,generation):
        """Internal-only credentials, never returned by a route or agent tool."""
        self.require_available()
        if not self.settings.event_writes_enabled:
            raise OAuthUnavailable('Calendar writes are disabled.')
        with closing(get_connection()) as db:
            row=db.execute('SELECT * FROM google_connections WHERE telegram_user_id=?',(user_id,)).fetchone()
        if not row or row['generation']!=generation or row['status'] not in {'connected','expired'}:
            raise ReconnectRequired('Reconnect and approve a fresh proposal for your own Calendar.')
        if EVENT_SCOPE not in json.loads(row['scopes_json']):
            raise ReconnectRequired('Additional Google consent is required; free/busy access remains available.')
        try:
            value=self.cipher.decrypt(user_id,row['encrypted_credentials'])
            if EVENT_SCOPE not in value.get('scopes',[]): raise ReconnectRequired('Event permission is missing.')
            if row['expires_at'] is not None and row['expires_at']<=self.clock()+300:
                value=self.provider.refresh(value)
                with closing(get_connection()) as db,db:
                    changed=db.execute("UPDATE google_connections SET encrypted_credentials=?,expires_at=?,status='connected' WHERE telegram_user_id=? AND generation=? AND status IN ('connected','expired')",
                                       (self.cipher.encrypt(user_id,value),token_expiry(value),user_id,generation))
                    if not changed.rowcount: raise ReconnectRequired('Connection changed during refresh.')
            if EVENT_SCOPE not in value.get('scopes',[]): raise ReconnectRequired('Event permission was revoked.')
            return value
        except RefreshError as error:
            if not getattr(error,'retryable',False): self.set_connection_state(user_id,generation,'revoked')
            raise ReconnectRequired('Google access could not be refreshed.') from None
        except TokenStorageError:
            self.set_connection_state(user_id,generation,'reconnect_required')
            raise ReconnectRequired('Reconnect your Google account.') from None

    def event_call(self,user_id,generation,operation):
        value=self.event_credentials(user_id,generation)
        try: return operation(self.provider,value)
        except HttpError as error:
            if error.resp.status in (401,403):
                self.set_connection_state(user_id,generation,'reconnect_required')
                raise ReconnectRequired('Google event permission needs renewed consent.') from None
            raise


def token_expiry(value):
    if not value.get('expiry'):
        raise ReconnectRequired('Google credentials lack an expiry time.')
    expiry = datetime.fromisoformat(value['expiry'].replace('Z', '+00:00'))
    return expiry.replace(tzinfo=timezone.utc).timestamp() if expiry.tzinfo is None else expiry.timestamp()
