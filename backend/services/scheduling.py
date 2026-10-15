
"""Approval-backed study scheduling for the single-account prototype."""

import json
import logging
import uuid

from contextlib import closing
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from googleapiclient.errors import HttpError

from database import get_connection
from services.google_calendar import (
    create_event,
    get_calendar_service,
    list_events,
)

log = logging.getLogger(__name__)

SG = ZoneInfo("Asia/Singapore")
APPROVAL_MINUTES = 10
RECLAIM_SECONDS = 120


class SlotOccupied(Exception):
    pass


def utc_now():
    return datetime.now(timezone.utc)


def db_time(value):
    return value.astimezone(timezone.utc).strftime(
        "%Y-%m-%d %H:%M:%S"
    )


def parse_time(value):
    if "dateTime" in value:
        dt = datetime.fromisoformat(
            value["dateTime"].replace("Z", "+00:00")
        )
        return (
            dt if dt.tzinfo else dt.replace(tzinfo=SG)
        ).astimezone(SG)

    return datetime.combine(
        date.fromisoformat(value["date"]),
        time.min,
        SG,
    )


def times(event):
    return parse_time(event["start"]), parse_time(event["end"])


def is_busy(events, start, end, exclude_id=None):
    for event in events:
        if event.get("id") == exclude_id:
            continue

        if event.get("status") == "cancelled":
            continue

        if event.get("transparency") == "transparent":
            continue

        if "start" not in event or "end" not in event:
            continue

        event_start, event_end = times(event)

        if event_start < end and start < event_end:
            return True

    return False


def find_available_session():
    """Choose a free 30-minute slot, starting tomorrow at 2 PM."""
    first_day = datetime.now(SG).date() + timedelta(days=1)
    last_day = first_day + timedelta(days=7)

    events = list_events(
        datetime.combine(
            first_day, time.min, SG
        ).isoformat(),
        datetime.combine(
            last_day, time.min, SG
        ).isoformat(),
    )

    for offset in range(7):
        day = first_day + timedelta(days=offset)

        for hour in range(14, 21):
            for minute in (0, 30):
                start = datetime.combine(
                    day, time(hour, minute), SG
                )
                end = start + timedelta(minutes=30)

                if not is_busy(events, start, end):
                    return start, end

    raise SlotOccupied(
        "No free afternoon slots in the next seven days."
    )


def expire_pending(conn):
    now = db_time(utc_now())

    conn.execute(
        """
        UPDATE approval_requests
        SET status='expired',
            resolution_reason='timeout',
            decided_at=?
        WHERE status='pending'
          AND expires_at IS NOT NULL
          AND expires_at <= ?
          AND EXISTS (
              SELECT 1
              FROM calendar_schedule_requests c
              WHERE c.approval_id=approval_requests.id
                AND c.status='pending'
          )
        """,
        (now, now),
    )


def cleanup_expired():
    with closing(get_connection()) as conn, conn:
        expire_pending(conn)


def save_proposal(user_id, start, end):
    """Atomically replace old pending proposals with a new one."""
    event_id = uuid.uuid4().hex

    event = {
        "id": event_id,
        "summary": "[TEST] AI Study Agent - Revision",
        "description": (
            "Approved through the AI Study Agent Telegram bot."
        ),
        "start": {
            "dateTime": start.isoformat(),
            "timeZone": "Asia/Singapore",
        },
        "end": {
            "dateTime": end.isoformat(),
            "timeZone": "Asia/Singapore",
        },
    }

    now = utc_now()

    with closing(get_connection()) as conn, conn:
        conn.execute("BEGIN IMMEDIATE")
        expire_pending(conn)

        conn.execute(
            """
            UPDATE approval_requests
            SET status='expired',
                resolution_reason='superseded',
                decided_at=?
            WHERE telegram_user_id=?
              AND status='pending'
              AND id IN (
                  SELECT approval_id
                  FROM calendar_schedule_requests
                  WHERE status='pending'
              )
            """,
            (db_time(now), user_id),
        )

        cursor = conn.execute(
            """
            INSERT INTO approval_requests
                (telegram_user_id, proposed_changes_json, expires_at)
            VALUES (?, ?, ?)
            """,
            (
                user_id,
                json.dumps(event),
                db_time(
                    now + timedelta(minutes=APPROVAL_MINUTES)
                ),
            ),
        )

        approval_id = cursor.lastrowid

        conn.execute(
            """
            INSERT INTO calendar_schedule_requests
                (approval_id, google_event_id)
            VALUES (?, ?)
            """,
            (approval_id, event_id),
        )

    return approval_id


def get_request(approval_id):
    with closing(get_connection()) as conn:
        row = conn.execute(
            """
            SELECT
                a.id,
                a.telegram_user_id,
                a.status AS approval_status,
                a.proposed_changes_json,
                a.expires_at,
                a.resolution_reason,
                c.status AS calendar_status,
                c.updated_at,
                c.google_event_id,
                c.last_error
            FROM approval_requests a
            JOIN calendar_schedule_requests c
              ON c.approval_id = a.id
            WHERE a.id = ?
            """,
            (approval_id,),
        ).fetchone()

        return dict(row) if row else None


def reject(approval_id, user_id):
    with closing(get_connection()) as conn, conn:
        conn.execute("BEGIN IMMEDIATE")
        expire_pending(conn)

        result = conn.execute(
            """
            UPDATE approval_requests
            SET status='rejected',
                resolution_reason='user_rejected',
                decided_at=?
            WHERE id=?
              AND telegram_user_id=?
              AND status='pending'
              AND EXISTS (
                  SELECT 1
                  FROM calendar_schedule_requests c
                  WHERE c.approval_id=approval_requests.id
                    AND c.status='pending'
              )
            """,
            (db_time(utc_now()), approval_id, user_id),
        )

        if result.rowcount:
            conn.execute(
                """
                UPDATE calendar_schedule_requests
                SET status='rejected',
                    updated_at=CURRENT_TIMESTAMP
                WHERE approval_id=?
                """,
                (approval_id,),
            )
            return True

        return False


def claim(approval_id, user_id):
    """Claim a pending or recoverable approved request."""
    with closing(get_connection()) as conn, conn:
        conn.execute("BEGIN IMMEDIATE")
        expire_pending(conn)

        row = conn.execute(
            """
            SELECT
                a.status AS approval_status,
                a.proposed_changes_json,
                c.status AS calendar_status,
                c.updated_at
            FROM approval_requests a
            JOIN calendar_schedule_requests c
              ON c.approval_id=a.id
            WHERE a.id=?
              AND a.telegram_user_id=?
            """,
            (approval_id, user_id),
        ).fetchone()

        if row is None:
            return "not_found", None

        if row["approval_status"] in ("expired", "rejected"):
            return row["approval_status"], None

        if row["calendar_status"] == "created":
            return "created", None

        if row["calendar_status"] == "processing":
            last = datetime.fromisoformat(
                row["updated_at"]
            ).replace(tzinfo=timezone.utc)

            if utc_now() - last < timedelta(
                seconds=RECLAIM_SECONDS
            ):
                return "processing", None

        event = json.loads(row["proposed_changes_json"])
        start, _ = times(event)

        if (
            row["approval_status"] == "pending"
            and start <= datetime.now(SG)
        ):
            conn.execute(
                """
                UPDATE approval_requests
                SET status='expired',
                    resolution_reason='start_passed',
                    decided_at=?
                WHERE id=?
                """,
                (db_time(utc_now()), approval_id),
            )
            return "expired", None

        if row["approval_status"] == "pending":
            conn.execute(
                """
                UPDATE approval_requests
                SET status='approved',
                    decided_at=?
                WHERE id=?
                """,
                (db_time(utc_now()), approval_id),
            )

        conn.execute(
            """
            UPDATE calendar_schedule_requests
            SET status='processing',
                last_error=NULL,
                updated_at=CURRENT_TIMESTAMP
            WHERE approval_id=?
            """,
            (approval_id,),
        )

        return "claimed", event


def has_other_processing_slot(approval_id, start, end):
    """Check locally in-flight Calendar operations."""
    with closing(get_connection()) as conn:
        rows = conn.execute(
            """
            SELECT a.proposed_changes_json
            FROM approval_requests a
            JOIN calendar_schedule_requests c
              ON c.approval_id=a.id
            WHERE a.status='approved'
              AND c.status='processing'
              AND a.id<>?
            """,
            (approval_id,),
        ).fetchall()

    for row in rows:
        other_start, other_end = times(
            json.loads(row[0])
        )

        if other_start < end and start < other_end:
            return True

    return False


def get_google_event(event_id):
    service = get_calendar_service()

    try:
        return service.events().get(
            calendarId="primary",
            eventId=event_id,
        ).execute()

    except HttpError as error:
        if error.resp.status == 404:
            return None

        raise


def ensure_event(approval_id, event):
    """Reconcile the reserved ID before inserting an event."""
    existing = get_google_event(event["id"])

    if existing:
        return existing

    start, end = times(event)

    if start <= datetime.now(SG):
        raise SlotOccupied(
            "The proposed start time has already passed."
        )

    if has_other_processing_slot(
        approval_id, start, end
    ):
        raise SlotOccupied(
            "Another approved session is being processed at that time."
        )

    if is_busy(
        list_events(start.isoformat(), end.isoformat()),
        start,
        end,
    ):
        raise SlotOccupied(
            "Your Calendar now has another event at that time."
        )

    try:
        return create_event(event)

    except HttpError as error:
        if error.resp.status == 409:
            existing = get_google_event(event["id"])

            if existing:
                return existing

        raise


def mark_created(approval_id, user_id, event_id):
    with closing(get_connection()) as conn, conn:
        conn.execute("BEGIN IMMEDIATE")

        updated = conn.execute(
            """
            UPDATE calendar_schedule_requests
            SET status='created',
                last_error=NULL,
                updated_at=CURRENT_TIMESTAMP
            WHERE approval_id=?
              AND status <> 'created'
            """,
            (approval_id,),
        )

        if updated.rowcount:
            conn.execute(
                """
                INSERT INTO agent_actions
                    (
                        telegram_user_id,
                        action_type,
                        status,
                        details_json
                    )
                VALUES (?, 'calendar_create', 'created', ?)
                """,
                (
                    user_id,
                    json.dumps({
                        "approval_id": approval_id,
                        "event_id": event_id,
                    }),
                ),
            )


def mark_failed(approval_id, reason):
    with closing(get_connection()) as conn, conn:
        conn.execute(
            """
            UPDATE calendar_schedule_requests
            SET status='failed',
                last_error=?,
                updated_at=CURRENT_TIMESTAMP
            WHERE approval_id=?
              AND status='processing'
            """,
            (reason[:300], approval_id),
        )


def reconcile_incomplete():
    """Recover remote successes after a restart; never create here."""
    with closing(get_connection()) as conn:
        rows = conn.execute(
            """
            SELECT
                a.id,
                a.telegram_user_id,
                c.google_event_id,
                c.status,
                c.updated_at
            FROM approval_requests a
            JOIN calendar_schedule_requests c
              ON c.approval_id=a.id
            WHERE a.status='approved'
              AND c.status IN ('processing', 'failed')
            """
        ).fetchall()

    for row in rows:
        try:
            remote = get_google_event(
                row["google_event_id"]
            )

            if remote:
                mark_created(
                    row["id"],
                    row["telegram_user_id"],
                    remote["id"],
                )

            elif row["status"] == "processing":
                last = datetime.fromisoformat(
                    row["updated_at"]
                ).replace(tzinfo=timezone.utc)

                if utc_now() - last >= timedelta(
                    seconds=RECLAIM_SECONDS
                ):
                    mark_failed(
                        row["id"],
                        "Interrupted operation; safe to retry original approval",
                    )

        except Exception:
            log.exception(
                "Could not reconcile Calendar request %s",
                row["id"],
            )
