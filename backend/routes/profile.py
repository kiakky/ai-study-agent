from datetime import timedelta
from typing import Literal

from fastapi import Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import AwareDatetime, BaseModel, ConfigDict, model_validator

from models.preferences import StudyPreferences
from services import profiles
from services.google_oauth import (
    COOKIE_NAME, FLOW_SECONDS, GoogleOAuthService, InvalidOAuthRequest,
    OAuthUnavailable, ReconnectRequired,
)
from services.token_cipher import TokenStorageError


class SkipIntegration(BaseModel):
    model_config = ConfigDict(extra='forbid')
    integration: Literal['google', 'brightspace']


class AvailabilityWindow(BaseModel):
    model_config = ConfigDict(extra='forbid')
    start: AwareDatetime
    end: AwareDatetime

    @model_validator(mode='after')
    def valid_window(self):
        if self.end <= self.start or self.end - self.start > timedelta(days=31):
            raise ValueError('Choose an availability window of up to 31 days.')
        return self


def google_service():
    return GoogleOAuthService()


def oauth_call(operation):
    try:
        return operation()
    except InvalidOAuthRequest:
        raise HTTPException(400, 'This Google connection link is invalid, expired, or already used. Start again from Telegram.') from None
    except (OAuthUnavailable, TokenStorageError):
        raise HTTPException(503, 'Google connection is not configured or its secure storage is unavailable. You can skip it.') from None
    except ReconnectRequired:
        raise HTTPException(409, 'Google access needs attention. Return to Telegram and reconnect your own account.') from None
    except Exception:
        raise HTTPException(503, 'Google connection is temporarily unavailable. Try again from Telegram.') from None


def require_oauth_origin(request, service):
    if str(request.base_url).rstrip('/') != service.settings.origin:
        raise HTTPException(400, 'Use the configured public HTTPS origin for Google authorization.')


def register_profile_routes(app, authenticate):
    @app.get('/api/profile')
    def get_profile(user=Depends(authenticate)):
        profile = profiles.get_profile(user['id'])
        profile['google'] = oauth_call(lambda: google_service().status(user['id']))
        return profile

    @app.put('/api/preferences')
    def update_preferences(body: StudyPreferences, user=Depends(authenticate)):
        return profiles.save_preferences(user['id'], body)

    @app.post('/api/onboarding/skip')
    def skip_integration(body: SkipIntegration, user=Depends(authenticate)):
        return profiles.choose_integration(user['id'], body.integration, 'skipped')

    @app.get('/api/studyplan')
    def studyplan(user=Depends(authenticate)):
        return profiles.current_study_plan(user['id'])

    @app.post('/api/integrations/google/connect')
    def connect_google(user=Depends(authenticate)):
        return oauth_call(lambda: google_service().begin(user['id']))

    @app.delete('/api/integrations/google')
    def disconnect_google(user=Depends(authenticate)):
        service = google_service()
        oauth_call(lambda: service.disconnect(user['id']))
        return service.status(user['id'])

    @app.post('/api/integrations/google/availability')
    def availability(body: AvailabilityWindow, user=Depends(authenticate)):
        return oauth_call(lambda: google_service().availability(user['id'], body.start, body.end))

    @app.get('/oauth/google/start', include_in_schema=False)
    def oauth_start(request: Request, ticket: str = ''):
        service = google_service()
        require_oauth_origin(request, service)
        location, browser = oauth_call(lambda: service.start_browser(ticket))
        response = RedirectResponse(location, status_code=303)
        response.set_cookie(COOKIE_NAME, browser, max_age=FLOW_SECONDS, secure=True, httponly=True,
                            samesite='lax', path='/oauth/google')
        return response

    @app.get('/oauth/google/callback', include_in_schema=False)
    def oauth_callback(request: Request, state: str = '', code: str | None = None, error: str | None = None):
        service = google_service()
        require_oauth_origin(request, service)
        connected = oauth_call(lambda: service.callback(state, request.cookies.get(COOKIE_NAME), code, error))
        title = 'Google Calendar connected' if connected else 'Google connection cancelled'
        message = 'Return to Telegram. If you explicitly approved exact events before this consent, the unchanged approval may now continue until its expiry. Connecting Google by itself never authorizes events.'
        response = HTMLResponse(
            '<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
            '<link rel="stylesheet" href="/static/styles.css"><title>Study Agent connection</title>'
            '<body><main class="app-shell"><section class="empty"><h1>' + title + '</h1><p>' + message + '</p></section></main></body></html>'
        )
        response.delete_cookie(COOKIE_NAME, path='/oauth/google', secure=True, httponly=True, samesite='lax')
        return response
