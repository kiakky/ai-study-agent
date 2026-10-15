"""Pure date/constraint tools shared by the UI and future agent orchestration."""
from datetime import datetime, timedelta, time, timezone
from models.planning import BusyPeriod
from models.preferences import effective_preferences


class PlanningError(ValueError):
    pass


def utc(value):
    return value.astimezone(timezone.utc)


def overlaps(start, end, periods):
    return any(start < period.end and end > period.start for period in periods)


def local_instant(day, clock, zone):
    value = datetime.combine(day, clock, zone)
    if value.astimezone(timezone.utc).astimezone(zone).replace(tzinfo=None) != value.replace(tzinfo=None):
        raise PlanningError('A selected local time does not exist because of daylight saving. Choose another window.')
    if value.utcoffset() != value.replace(fold=1).utcoffset():
        raise PlanningError('A selected local time is ambiguous because of daylight saving. Choose another window.')
    return utc(value)


DAY_NAMES = ['mon', 'tue', 'wed', 'thu', 'fri', 'sat', 'sun']


def validate_periods(sessions, start, end, zone, preferences, duration, excluded_days, busy, session_overrides=None):
    """Reject invalid model/tool suggestions; never silently move proposed times."""
    periods = [BusyPeriod.model_validate(session) for session in sessions]
    if not periods or len(periods) > 100:
        raise PlanningError('Supply between one and one hundred sessions.')
    occupied = set()
    for index, period in enumerate(periods):
        patch=(session_overrides or {}).get(index,(session_overrides or {}).get(str(index),{}))
        prefs=effective_preferences(preferences,patch)
        if not prefs['study_windows']:raise PlanningError('Choose study windows for this request.')
        minutes=patch.get('session_minutes',duration)
        a, b = utc(period.start), utc(period.end)
        if a < start or b > end or b - a != timedelta(minutes=minutes):
            raise PlanningError('Session dates or duration violate the requested study constraints.')
        first, last = a.astimezone(zone).date(), (b-timedelta(microseconds=1)).astimezone(zone).date()
        dates = {first, last}
        if first in occupied:
            raise PlanningError('Only one session may start on each local day.')
        occupied.add(first)
        selected = prefs['study_days']
        if any(DAY_NAMES[d.weekday()] in excluded_days or (selected and DAY_NAMES[d.weekday()] not in selected) for d in dates):
            raise PlanningError('A session violates selected or excluded study days.')
        within_window = False
        for day in (first-timedelta(days=1), first):
            for window in prefs['study_windows']:
                lower, upper = time.fromisoformat(window['start']), time.fromisoformat(window['end'])
                lo = local_instant(day, lower, zone)
                hi = local_instant(day+timedelta(days=upper<lower), upper, zone)
                if lo <= a and b <= hi:
                    within_window = True
        if not within_window:
            raise PlanningError('A session falls outside preferred study windows.')
        if overlaps(a, b, busy) or overlaps(a, b, periods[:index]):
            raise PlanningError('A session conflicts with a busy or excluded period.')
    return {'valid': True, 'sessions': [p.model_dump(mode='json') for p in periods]}


def find_periods(start, end, zone, preferences, duration, count, excluded_days, busy):
    """A bounded availability search, not goal generation or agent reasoning."""
    if not preferences['study_windows']:
        raise PlanningError('Set preferred study time windows in Setup before drafting.')
    slots = []
    day = start.astimezone(zone).date()-timedelta(days=1)
    final = end.astimezone(zone).date()
    selected = preferences['study_days']
    while day <= final and len(slots) < count:
        name = DAY_NAMES[day.weekday()]
        if (not selected or name in selected) and name not in excluded_days:
            candidates = []
            for window in preferences['study_windows']:
                lower, upper = time.fromisoformat(window['start']), time.fromisoformat(window['end'])
                a = max(start, local_instant(day, lower, zone))
                b = min(end, local_instant(day+timedelta(days=upper<lower), upper, zone))
                remainder = a.minute % 5
                cursor = a.replace(second=0, microsecond=0)
                if cursor < a or remainder:
                    cursor += timedelta(minutes=5-remainder if remainder else 5)
                while cursor+timedelta(minutes=duration) <= b:
                    stop = cursor+timedelta(minutes=duration)
                    dates = {cursor.astimezone(zone).date(), (stop-timedelta(microseconds=1)).astimezone(zone).date()}
                    permitted = all((not selected or DAY_NAMES[d.weekday()] in selected) and DAY_NAMES[d.weekday()] not in excluded_days for d in dates)
                    if permitted and not overlaps(cursor, stop, busy):
                        candidates.append((cursor, stop))
                        break
                    cursor += timedelta(minutes=5)
            if candidates:
                a, b = min(candidates)
                used = {datetime.fromisoformat(s['start']).astimezone(zone).date() for s in slots}
                if a.astimezone(zone).date() not in used and not overlaps(a, b, [BusyPeriod.model_validate(s) for s in slots]):
                    slots.append({'start':a.isoformat(), 'end':b.isoformat()})
        day += timedelta(days=1)
    return slots
