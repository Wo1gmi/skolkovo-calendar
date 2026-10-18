"""Explicitly rewrite one selected lesson from source and send its current card."""
import argparse
from datetime import date, timedelta
import hashlib
import json
import re

from notifications import TZ, lesson_card, moment
from schedule_rules import excluded_title
from sync import MARKER, event_body, get_session, load_schedule, normalize_title, required, send_telegram


def select_lesson(items, day, subject, start_time):
    if not subject.strip() or not re.fullmatch(r'\d{2}:\d{2}', start_time):
        raise RuntimeError('Subject and exact start time are required')
    matches = []
    for item in items:
        if excluded_title(item['title']):
            continue
        body = event_body(item, TZ)
        start = moment(body, 'start').astimezone(TZ)
        if (start.date() == day and start.strftime('%H:%M') == start_time and
                normalize_title(item['title'].split('·', 1)[0]) == normalize_title(subject)):
            matches.append(item)
    if len(matches) != 1:
        raise RuntimeError('Expected exactly one source lesson; no changes made')
    return matches[0]


def refresh_selected(item, existing, api, calendar):
    if excluded_title(item['title']):
        raise RuntimeError('Excluded lesson cannot be refreshed')
    candidates = [event for event in existing
                  if event.get('extendedProperties', {}).get('private', {}).get('sync_source') == MARKER
                  and event.get('extendedProperties', {}).get('private', {}).get('source_id') == str(item['id'])]
    if len(candidates) != 1:
        raise RuntimeError('Expected exactly one managed calendar event; no changes made')
    old = candidates[0]
    body = event_body(item, TZ)
    comparable = {key: value for key, value in body.items() if key != 'colorId'}
    digest = hashlib.sha256(json.dumps(comparable, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    private = dict(old.get('extendedProperties', {}).get('private', {}))
    private.update(body['extendedProperties']['private'], content_hash=digest)
    body['extendedProperties'] = {'private': private}
    body.pop('colorId', None)
    # This explicit operation refreshes even when the source hash is unchanged.
    api.patch(calendarId=calendar, eventId=old['id'], body=body).execute()
    saved = api.get(calendarId=calendar, eventId=old['id']).execute()
    if (any((saved.get(key) or '') != (body.get(key) or '') for key in ('summary', 'description', 'location'))
            or any(moment(saved, key) != moment(body, key) for key in ('start', 'end'))):
        raise RuntimeError('Calendar verification failed; current card was not sent')
    send_telegram(lesson_card('🔄 Актуализировано: ', saved))
    return 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--date', required=True)
    parser.add_argument('--subject', required=True)
    parser.add_argument('--time', required=True)
    args = parser.parse_args()
    day = date.fromisoformat(args.date)
    for name in ('TELEGRAM_BOT_TOKEN', 'TELEGRAM_CHAT_ID', 'GOOGLE_SERVICE_ACCOUNT_JSON', 'GOOGLE_CALENDAR_ID'):
        required(name)
    item = select_lesson(load_schedule(get_session(), day, day + timedelta(days=1)), day, args.subject, args.time)
    from google.oauth2 import service_account
    from googleapiclient.discovery import build
    credentials = service_account.Credentials.from_service_account_info(
        json.loads(required('GOOGLE_SERVICE_ACCOUNT_JSON')), scopes=['https://www.googleapis.com/auth/calendar.events'])
    api = build('calendar', 'v3', credentials=credentials, cache_discovery=False).events()
    calendar = required('GOOGLE_CALENDAR_ID')
    existing, token = [], None
    while True:
        result = api.list(calendarId=calendar, privateExtendedProperty='sync_source=' + MARKER,
                          maxResults=2500, pageToken=token, showDeleted=False).execute()
        existing.extend(result.get('items', []))
        token = result.get('nextPageToken')
        if not token:
            break
    count = refresh_selected(item, existing, api, calendar)
    print('Events refreshed and current cards sent:', count)


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        print(str(error) if isinstance(error, RuntimeError) else 'Refresh failed: ' + type(error).__name__)
        raise SystemExit(1)
