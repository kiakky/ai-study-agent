
"""Google Calendar API operations for the single-account prototype."""

from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

BASE_DIR = Path(__file__).resolve().parent.parent
CREDENTIALS_FILE = BASE_DIR / "credentials" / "google_credentials.json"
TOKEN_FILE = BASE_DIR / "credentials" / "token.json"
SCOPES = ["https://www.googleapis.com/auth/calendar.events"]


def authenticate():
    creds = None

    if TOKEN_FILE.exists():
        creds = Credentials.from_authorized_user_file(
            str(TOKEN_FILE), SCOPES
        )

    if creds and creds.expired and creds.refresh_token:
        creds.refresh(Request())

    if not creds or not creds.valid:
        flow = InstalledAppFlow.from_client_secrets_file(
            str(CREDENTIALS_FILE), SCOPES
        )
        creds = flow.run_local_server(port=0)

    TOKEN_FILE.write_text(creds.to_json(), encoding="utf-8")
    return creds


def get_calendar_service():
    return build(
        "calendar",
        "v3",
        credentials=authenticate(),
        cache_discovery=False,
    )


def list_events(time_min, time_max, calendar_id="primary"):
    """Fetch all pages, including recurring and all-day events."""
    service = get_calendar_service()
    events = []
    token = None

    while True:
        response = service.events().list(
            calendarId=calendar_id,
            timeMin=time_min,
            timeMax=time_max,
            singleEvents=True,
            showDeleted=False,
            orderBy="startTime",
            maxResults=2500,
            pageToken=token,
        ).execute()

        events.extend(response.get("items", []))
        token = response.get("nextPageToken")

        if not token:
            return events


def create_event(event_body, calendar_id="primary"):
    return get_calendar_service().events().insert(
        calendarId=calendar_id,
        body=event_body,
    ).execute()


def update_event(event_id, changes, calendar_id="primary"):
    return get_calendar_service().events().patch(
        calendarId=calendar_id,
        eventId=event_id,
        body=changes,
    ).execute()


def delete_event(event_id, calendar_id="primary"):
    get_calendar_service().events().delete(
        calendarId=calendar_id,
        eventId=event_id,
    ).execute()

    return True
