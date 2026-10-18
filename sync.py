"""Daily schedule sync. Never print credentials or HTTP response bodies."""
import os
import json
import hashlib
import re
from pathlib import Path
from datetime import datetime, timedelta, time
from urllib.parse import unquote, urlparse, urlencode
from zoneinfo import ZoneInfo
import requests
from notifications import change_message

PROGRAM = 'uvLzhep9c6NWCBtvRal2FKab2Ou1vHgp'
BASE = 'https://sk.skolkovo.ru'
MARKER = 'skolkovo_' + PROGRAM
LOGIN_URL = 'https://id.skolkovo.ru/?' + urlencode({
    'client_id': '12',
    'redirect_uri': 'https://sk.skolkovo.ru/test_skolkovo_id_callback',
    'response_type': 'code',
    'return_url': 'https://www.skolkovo.ru/',
    'scope': '',
})


def required(name):
    value = os.environ.get(name)
    if not value:
        raise RuntimeError('Missing setting: ' + name)
    return value


def log_login_chain(response, stage):
    # Only fixed route names and statuses are logged, never query values or tokens.
    allowed = {'/', '/account', '/test_skolkovo_id_callback'}
    for hop in [*response.history, response]:
        parsed = urlparse(hop.url)
        route = parsed.path if parsed.path in allowed else '[hidden route]'
        if parsed.path.startswith('/autologin/'):
            route = '/autologin/[hidden]'
        host = parsed.hostname if parsed.hostname in {
            'id.skolkovo.ru', 'sk.skolkovo.ru', 'www.skolkovo.ru'} else '[other host]'
        print('Login stage:', stage, 'HTTP:', hop.status_code, 'host:', host, 'route:', route)


def get_session():
    # This adapter is based on observed requests, not yet verified live.
    session = requests.Session()
    session.headers['User-Agent'] = 'Mozilla/5.0 SkolkovoCalendarSync/0.1'
    session.get('https://www.skolkovo.ru/?login=true', timeout=30).raise_for_status()
    session.get(LOGIN_URL, timeout=30).raise_for_status()
    xsrf = next((c.value for c in session.cookies if c.name == 'XSRF-TOKEN'), None)
    if not xsrf:
        raise RuntimeError('Login adapter: XSRF cookie not found; login flow needs adjustment')
    response = session.post('https://id.skolkovo.ru/', json={
        'email': required('SKOLKOVO_EMAIL'), 'password': required('SKOLKOVO_PASSWORD'),
        'phone': '7', 'type': 'email', 'isCode': False, 'remember': False,
    }, headers={'X-XSRF-TOKEN': unquote(xsrf), 'X-Inertia': 'true',
                'X-Requested-With': 'XMLHttpRequest',
                'Origin': 'https://id.skolkovo.ru', 'Referer': LOGIN_URL},
        timeout=30)
    log_login_chain(response, 'submit')
    if response.status_code == 409:
        location = response.headers.get('X-Inertia-Location', '')
        if location.startswith('/'):
            location = 'https://id.skolkovo.ru' + location
        if urlparse(location).hostname not in {'id.skolkovo.ru', 'sk.skolkovo.ru', 'www.skolkovo.ru'}:
            raise RuntimeError('Unexpected login redirect')
        response = session.get(location, timeout=30)
        log_login_chain(response, 'redirect')
    if response.status_code >= 400:
        raise RuntimeError('Login failed, HTTP ' + str(response.status_code))
    candidates = [unquote(c.value).strip().strip('"') for c in session.cookies
                  if c.name == 'auth._token.local'
                  and c.domain.lstrip('.') in {'www.skolkovo.ru', 'skolkovo.ru'}]
    for value in candidates:
        token = value[7:] if value.startswith('Bearer ') else value
        if token.count('.') == 2 and not any(c.isspace() for c in token):
            session.headers.update({
                'Authorization': 'Bearer ' + token,
                'Origin': 'https://www.skolkovo.ru',
                'Referer': 'https://www.skolkovo.ru/',
                'Accept': 'application/json',
                'X-Requested-With': 'XMLHttpRequest',
            })
            print('API auth cookie: found')
            return session
    print('API auth cookie: not found')
    # The observed login flow puts the API token in /autologin/<token>.
    for hop in reversed([*response.history, response]):
        path = urlparse(hop.url).path
        if '/autologin/' in path:
            token = unquote(path.split('/autologin/', 1)[1].split('/')[0])
            if token.count('.') == 2:
                session.headers.update({
                    'Authorization': 'Bearer ' + token,
                    'Origin': 'https://www.skolkovo.ru',
                    'Referer': 'https://www.skolkovo.ru/',
                    'Accept': 'application/json',
                    'X-Requested-With': 'XMLHttpRequest',
                })
                return session
    raise RuntimeError('Login adapter: API token not found; callback flow needs adjustment')


def load_schedule(session, first, last):
    items = {}
    cursor = first
    while cursor < last:
        end = min(cursor + timedelta(days=7), last)
        r = session.get(BASE + '/api/schedule/calendar', params={
            'lang': 'ru', 'start': cursor.isoformat(), 'end': end.isoformat(), 'hash': PROGRAM
        }, timeout=30)
        if r.status_code != 200:
            raise RuntimeError('Schedule request failed, HTTP ' + str(r.status_code))
        payload = r.json()
        if not isinstance(payload, dict) or not isinstance(payload.get('data'), list):
            raise RuntimeError('Unexpected schedule format; no calendar changes made')
        for item in payload['data']:
            for field in ('id', 'title', 'start', 'end'):
                if field not in item:
                    raise RuntimeError('Incomplete schedule item')
            if first <= datetime.fromisoformat(item['start']).date() < last:
                key = str(item['id'])
                if key in items and items[key] != item:
                    raise RuntimeError('Conflicting source IDs')
                items[key] = item
        cursor = end
    return list(items.values())


def group_one_only(item):
    text = ' '.join(str(item.get(k) or '') for k in ('title', 'note')).lower()
    groups = set()
    for match in re.finditer(r'\b(?:гр\.?|групп(?:а|ы|е|у|ой)?)\s*(\d+(?:(?:\s*(?:и|/|,|&|-)\s*)(?:(?:гр\.?|групп(?:а|ы)?)\s*)?\d+)*)', text):
        groups.update(re.findall(r'\d+', match.group(1)))
    return groups == {'1'}


NORMAL_COLORS = ('1', '3', '5', '7', '9', '10')
SUBJECT_COLORS = json.loads(Path(__file__).with_name('subject_colors.json').read_text(encoding='utf-8'))['subjects']
if any(entry['color_id'] not in (*NORMAL_COLORS, '6', '8') for entry in SUBJECT_COLORS):
    raise RuntimeError('Invalid subject color in dictionary')


def normalize_title(value):
    return re.sub(r'\s+', ' ', value.lower().replace('ё', 'е')).strip()


def educational_environment(item):
    return bool(re.search(r'образова\w*\s+сред\w*', normalize_title(item['title'])))


def project_management(item):
    title = normalize_title(item['title'])
    return bool(re.search(r'проектн\w*\s+менеджмен\w*|project\s+management', title))


def event_color(item):
    if educational_environment(item):
        return '8'
    if group_one_only(item):
        return '8'
    text = normalize_title(' '.join(str(item.get(k) or '') for k in ('title', 'note')))
    assessment = r'\b(?:тест\w*|контрольн\w*|сдач\w*|сдать|работ\w*|итогов\w*|сочинен\w*|эссе|экзамен\w*|зач[её]т\w*|проверочн\w*|рубежн\w*|аттестац\w*|коллоквиум\w*|защит\w*|дедлайн\w*|домашн\w*\s+задан\w*|quiz\w*|test\w*|exam\w*|midterm\w*|final\w*|assessment\w*|essay\w*|submission\w*|deadline\w*)\b'
    if re.search(assessment, text):
        return '11'
    if project_management(item):
        return '7'
    title = normalize_title(item['title'])
    for entry in SUBJECT_COLORS:
        if any(normalize_title(alias) in title for alias in entry['aliases']):
            return entry['color_id']
    # New subjects receive a stable allowed color until added to the dictionary.
    subject = re.split(r'\s*[·|]\s*|\b(?:гр\.|группа)\s*\d', title)[0].strip()
    index = int(hashlib.sha256(subject.encode()).hexdigest(), 16) % len(NORMAL_COLORS)
    return NORMAL_COLORS[index]


def event_body(item, timezone):
    def local_time(raw):
        value = datetime.fromisoformat(raw)
        return value.replace(tzinfo=timezone) if value.tzinfo is None else value.astimezone(timezone)
    start = local_time(item['start'])
    end = local_time(item['end'])
    if end <= start:
        raise RuntimeError('Invalid lesson duration')
    description = '\n'.join(f'{label}: {item[key]}' for key, label in (
        ('teachers', 'Преподаватель'), ('note', 'Примечание'),
        ('schedule_title', 'Пара')) if item.get(key))
    return {'colorId': event_color(item),
            'summary': item['title'], 'location': item.get('room') or '',
            'description': description,
            'start': {'dateTime': start.isoformat(), 'timeZone': str(timezone)},
            'end': {'dateTime': end.isoformat(), 'timeZone': str(timezone)},
            'extendedProperties': {'private': {'sync_source': MARKER, 'source_id': str(item['id'])}}}


def plan(items, existing, timezone, first, last, delete=False, reset_colors=False):
    desired = {str(i['id']): event_body(i, timezone) for i in items}
    current = {}
    for event in existing:
        private = event.get('extendedProperties', {}).get('private', {})
        if private.get('sync_source') != MARKER:
            continue
        key = private.get('source_id')
        if not key or key in current:
            raise RuntimeError('Duplicate or missing managed source ID')
        current[key] = event
    actions = []
    for key, body in desired.items():
        previous = current.get(key)
        force_color = (educational_environment({'title': body['summary']})
                       or project_management({'title': body['summary']})
                       or 'искусственный интеллект в бизнесе' in normalize_title(body['summary'])
                       or body['colorId'] == '11')
        # Color belongs to the user after creation; exclude it from source comparisons.
        comparable = {k: v for k, v in body.items() if k != 'colorId'}
        digest = hashlib.sha256(json.dumps(comparable, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        body['extendedProperties']['private']['content_hash'] = digest
        if previous is None:
            actions.append(('insert', None, body))
        elif (previous['extendedProperties']['private'].get('content_hash') != digest
              or ((reset_colors or force_color) and previous.get('colorId') != body.get('colorId'))):
            # Omitting colorId from PATCH preserves even the user's default calendar color.
            if not reset_colors and not force_color:
                body.pop('colorId', None)
            actions.append(('patch', previous['id'], body))
    if delete:
        if not desired and current:
            raise RuntimeError('Empty source: refusing mass deletion')
        for key, event in current.items():
            raw = event.get('start', {}).get('dateTime')
            if key not in desired and raw and first <= datetime.fromisoformat(raw).astimezone(timezone).date() < last:
                actions.append(('delete', event['id'], None))
    return actions



def notify_change(kind, previous, body):
    token = os.environ.get('TELEGRAM_BOT_TOKEN')
    chat = os.environ.get('TELEGRAM_CHAT_ID')
    if not token or not chat:
        return
    message = change_message(kind, previous, body)
    if not message:
        return
    send_telegram(message)


def send_telegram(message):
    token = required('TELEGRAM_BOT_TOKEN')
    chat = required('TELEGRAM_CHAT_ID')
    for offset in range(0, len(message), 3500):
        response = requests.post('https://api.telegram.org/bot' + token + '/sendMessage',
                                 json={'chat_id': chat, 'text': message[offset:offset+3500]},
                                 timeout=30)
        if response.status_code != 200 or not response.json().get('ok'):
            raise RuntimeError('Telegram notification failed; check bot secrets')


def main(audit=False):
    timezone = ZoneInfo(required('SCHEDULE_TIMEZONE'))
    first = datetime.now(timezone).date()
    # Rolling horizon, not a one-week import. Extend once API availability is verified.
    last = first + timedelta(days=int(os.environ.get('LOOKAHEAD_DAYS', '365')))
    fixture = os.environ.get('SCHEDULE_FILE')
    if fixture:
        with open(fixture) as stream:
            items = json.load(stream)['data']
        for item in items:
            event_body(item, timezone)
        print('Offline validation OK, lessons:', len(items))
        return
    items = load_schedule(get_session(), first, last)
    # Validate the entire source before any Google write.
    for item in items:
        event_body(item, timezone)
    from google.oauth2 import service_account
    from googleapiclient.discovery import build
    credentials = service_account.Credentials.from_service_account_info(
        json.loads(required('GOOGLE_SERVICE_ACCOUNT_JSON')),
        scopes=['https://www.googleapis.com/auth/calendar.events'])
    api = build('calendar', 'v3', credentials=credentials, cache_discovery=False).events()
    calendar = required('GOOGLE_CALENDAR_ID')
    existing, token = [], None
    while True:
        result = api.list(calendarId=calendar, privateExtendedProperty='sync_source=' + MARKER,
                          pageToken=token, maxResults=2500, showDeleted=False).execute()
        existing.extend(result.get('items', []))
        token = result.get('nextPageToken')
        if not token:
            break
    actions = plan(items, existing, timezone, first, last,
                   delete=os.environ.get('ENABLE_DELETIONS') == 'true',
                   reset_colors=os.environ.get('RESET_COLORS') == 'true')
    apply = not audit and os.environ.get('APPLY') == 'true'
    counts = {kind: sum(a[0] == kind for a in actions) for kind in ('insert', 'patch', 'delete')}
    print('Lessons:', len(items), 'planned:', counts, 'apply:', apply)
    previous_by_id = {e['id']: e for e in existing}
    if audit:
        notifications = sum(change_message(kind, previous_by_id.get(event_id, {}), body) is not None
                            for kind, event_id, body in actions)
        # Logs contain counts only: no subjects, names, notes, IDs or message drafts.
        print('Audit totals:', json.dumps({'source_events': len(items),
              'calendar_events': len(existing), 'pending_notifications': notifications,
              'silent_calendar_updates': len(actions) - notifications}))
        return counts
    if apply:
        for kind, event_id, body in actions:
            kwargs = {'calendarId': calendar}
            if event_id:
                kwargs['eventId'] = event_id
            if body:
                kwargs['body'] = body
            getattr(api, kind)(**kwargs).execute()
            # First import creates the baseline without hundreds of alerts.
            if existing:
                notify_change(kind, previous_by_id.get(event_id, {}), body)
    return counts


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        # Raw HTTP errors may contain sensitive URLs: report only controlled errors.
        print(str(error) if isinstance(error, RuntimeError) else 'Sync failed: ' + type(error).__name__)
        raise SystemExit(1)
