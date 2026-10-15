"""Loopback-only browser QA with synthetic signed identity and temporary SQLite.

Never imports the bot, Brightspace or Calendar. Never loads .env. No authentication
bypass is installed in the production app: the ordinary HMAC validator still runs.
"""
import hashlib
import argparse
import hmac
import json
import os
from pathlib import Path
import secrets
import sys
import tempfile
import time
from unittest.mock import patch
from urllib.parse import urlencode

import uvicorn
from fastapi.responses import HTMLResponse, Response

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--mock-events',action='store_true',help='Use synthetic Google event services only, never real credentials.')
    args=parser.parse_args()
    token = secrets.token_hex(32)  # Ephemeral synthetic signer, never a real bot token.
    user_id = 900000 + secrets.randbelow(1000000000)
    with patch('dotenv.load_dotenv'), patch.dict(os.environ, {'TELEGRAM_BOT_TOKEN': token}):
        import api
    import database
    from routes import profile as profile_routes, planning as planning_routes
    from services.google_oauth import GoogleOAuthService, OAuthSettings
    from services.planning import PlanningService
    from services.calendar_actions import CalendarActions
    from services import profiles
    from models.preferences import StudyPreferences
    disabled_calendar = GoogleOAuthService(settings=OAuthSettings(False, ''))
    if args.mock_events:
        from test_calendar_actions import EventGoogle
        from test_google_oauth import SyntheticCipher
        from urllib.parse import parse_qs,urlsplit
        disabled_calendar=GoogleOAuthService(settings=OAuthSettings(True,'https://synthetic.example',event_writes_enabled=True),provider=EventGoogle(),cipher=SyntheticCipher())

    def preview_html():
        html = (ROOT / 'frontend' / 'index.html').read_text(encoding='utf-8')
        return HTMLResponse(html.replace('https://telegram.org/js/telegram-web-app.js', '/__preview__/telegram.js'))

    def preview_setup():
        html = (ROOT / 'frontend' / 'setup.html').read_text(encoding='utf-8')
        return HTMLResponse(html.replace('https://telegram.org/js/telegram-web-app.js', '/__preview__/telegram.js'))

    def preview_study():
        html = (ROOT / 'frontend' / 'study.html').read_text(encoding='utf-8')
        return HTMLResponse(html.replace('https://telegram.org/js/telegram-web-app.js', '/__preview__/telegram.js'))

    def synthetic_telegram():
        fields = {'auth_date': str(int(time.time())), 'user': json.dumps({'id': user_id, 'first_name': 'Alex (test)'})}
        secret = hmac.new(b'WebAppData', token.encode(), hashlib.sha256).digest()
        fields['hash'] = hmac.new(secret, '\n'.join(f'{k}={v}' for k, v in sorted(fields.items())).encode(), hashlib.sha256).hexdigest()
        script = 'window.Telegram = {WebApp: {initData: ' + json.dumps(urlencode(fields)) + '}};'
        script += "document.addEventListener('DOMContentLoaded', () => { const note=document.querySelector('#preview-note'); if(note) note.hidden=false; });"
        return Response(script, media_type='application/javascript')

    api.app.router.routes[:] = [route for route in api.app.router.routes if getattr(route, 'path', None) not in {'/', '/setup','/study'}]
    api.app.add_api_route('/', preview_html, include_in_schema=False)
    api.app.add_api_route('/setup', preview_setup, include_in_schema=False)
    api.app.add_api_route('/study', preview_study, include_in_schema=False)
    api.app.add_api_route('/__preview__/telegram.js', synthetic_telegram, include_in_schema=False)
    with tempfile.TemporaryDirectory(prefix='preview-', dir=ROOT / 'tests') as folder:
        with patch.object(database, 'DB_PATH', Path(folder) / 'preview.db'), \
             patch.object(profile_routes, 'google_service', return_value=disabled_calendar), \
             patch.object(planning_routes, 'planning_service', side_effect=lambda: PlanningService(calendar=disabled_calendar)), \
             patch.object(planning_routes, 'calendar_actions', side_effect=lambda:CalendarActions(calendar=disabled_calendar)):
            if args.mock_events:
                database.initialize_database(backfill_approvals=False)
                profiles.save_preferences(user_id,StudyPreferences(study_windows=[{'start':'18:00','end':'20:00'}]))
                link=disabled_calendar.begin(user_id,write_access=True)['authorization_start_url']
                ticket=parse_qs(urlsplit(link).query)['ticket'][0]
                url,cookie=disabled_calendar.start_browser(ticket)
                state=parse_qs(urlsplit(url).query)['state'][0]
                disabled_calendar.callback(state,cookie,code='synthetic-preview-account')
                from services.calendar_testing import CalendarTestService
                CalendarTestService(PlanningService(calendar=disabled_calendar),testers={user_id}).prepare(user_id)
            print('Local synthetic preview: http://127.0.0.1:8765 · Ctrl+C to stop. Results are temporary.')
            uvicorn.run(api.app, host='127.0.0.1', port=8765, access_log=False)


if __name__ == '__main__':
    main()
