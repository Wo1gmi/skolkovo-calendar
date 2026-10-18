"""Reply to Russian dates with one Telegram message per lesson."""
import argparse
from datetime import date, datetime, timedelta
import fcntl
import json
import os
from pathlib import Path
import re
import sqlite3
import time

import requests

from notifications import MONTHS, TZ
from schedule_rules import excluded_title
from sync import get_session, load_schedule, required

HELP = 'Напиши дату, например «18 октября» или «14 ноября». Можно указать год: «14 ноября 2027».'


def parse_day(text, today=None):
    today = today or datetime.now(TZ).date()
    match = re.fullmatch(r'\s*(\d{1,2})\s+([а-яё]+)(?:\s+(\d{4}))?\s*', text.casefold())
    if not match or match[2] not in MONTHS:
        raise ValueError('Invalid date')
    day, month = int(match[1]), MONTHS.index(match[2]) + 1
    if match[3]:
        return date(int(match[3]), month, day)
    # Dates without a year mean the next occurrence, including today.
    for year in range(today.year, min(today.year + 9, 10000)):
        try:
            result = date(year, month, day)
        except ValueError:
            continue
        if result >= today:
            return result
    raise ValueError('Invalid date')


def day_label(day, today=None):
    today = today or datetime.now(TZ).date()
    text = f'{day.day} {MONTHS[day.month - 1]}'
    return text + (f' {day.year}' if day.year != today.year else '')


def local_time(raw):
    value = datetime.fromisoformat(raw)
    return value.replace(tzinfo=TZ) if value.tzinfo is None else value.astimezone(TZ)


def lesson_messages(items, day, today=None):
    lessons = {}
    for item in items:
        if excluded_title(item['title']) or local_time(item['start']).date() != day:
            continue
        key = str(item['id'])
        if key in lessons and lessons[key] != item:
            raise RuntimeError('Conflicting source IDs')
        lessons[key] = item
    result = []
    for item in sorted(lessons.values(), key=lambda i: (local_time(i['start']), str(i['id']))):
        start, end = local_time(item['start']), local_time(item['end'])
        if end <= start:
            raise RuntimeError('Invalid lesson duration')
        number = str(item.get('schedule_title') or 'Пара (номер не указан)')
        title = item['title']
        if item.get('teachers') or item.get('note'):
            title = title.split('·', 1)[0].strip()
        lines = [number + ' · ' + day_label(day, today),
                 start.strftime('%H:%M') + '–' + (end.strftime('%H:%M') if end.date() == day
                                                 else day_label(end.date(), today) + ' ' + end.strftime('%H:%M')),
                 title]
        for key, label in (('teachers', 'Преподаватель'), ('note', 'Примечание'), ('room', 'Аудитория')):
            if item.get(key):
                lines.append(label + ': ' + str(item[key]))
        text = '\n'.join(lines)
        # Keep one Telegram message per lesson even with an exceptionally long note.
        if len(text.encode('utf-16-le')) // 2 > 4096:
            text = text.encode('utf-16-le')[:7900].decode('utf-16-le', errors='ignore') + '\n…'
        result.append(text)
    return result or ['На ' + day_label(day, today) + ' занятий нет.']


def fetch_day(day):
    return load_schedule(get_session(), day, day + timedelta(days=1))


class TelegramError(RuntimeError):
    pass


class Telegram:
    def __init__(self, token, chat_id):
        self.base = 'https://api.telegram.org/bot' + token + '/'
        self.chat_id = str(chat_id)
        self.session = requests.Session()

    def call(self, method, payload):
        try:
            response = self.session.post(self.base + method, json=payload, timeout=55)
            data = response.json()
        except (requests.RequestException, ValueError):
            raise TelegramError('Telegram connection failed') from None
        if not response.ok or not data.get('ok'):
            # Never include response bodies or token-bearing request URLs in logs.
            raise TelegramError('Telegram request failed (HTTP ' + str(response.status_code) + ')')
        return data.get('result')

    def check_webhook(self):
        if self.call('getWebhookInfo', {}).get('url'):
            raise TelegramError('A Telegram webhook is configured; polling cannot run alongside it')

    def updates(self, offset, timeout=40):
        return self.call('getUpdates', {'offset': offset, 'timeout': timeout,
                                       'limit': 100, 'allowed_updates': ['message']})

    def send(self, text):
        return self.call('sendMessage', {'chat_id': self.chat_id, 'text': text,
                                         'link_preview_options': {'is_disabled': True}})


class State:
    """Persist replies and delivery progress before acknowledging an update."""
    def __init__(self, path):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path)
        os.chmod(path, 0o600)
        self.db.executescript('''
            CREATE TABLE IF NOT EXISTS cursor (id INTEGER PRIMARY KEY CHECK (id = 1), value INTEGER);
            INSERT OR IGNORE INTO cursor VALUES (1, 0);
            CREATE TABLE IF NOT EXISTS outbox
                (update_id INTEGER PRIMARY KEY, messages TEXT NOT NULL, sent INTEGER NOT NULL DEFAULT 0);
        ''')
        self.db.commit()

    @property
    def offset(self):
        return self.db.execute('SELECT value FROM cursor WHERE id=1').fetchone()[0]

    def pending(self, update_id):
        row = self.db.execute('SELECT messages, sent FROM outbox WHERE update_id=?', (update_id,)).fetchone()
        return (json.loads(row[0]), row[1]) if row else None

    def prepare(self, update_id, messages):
        with self.db:
            self.db.execute('INSERT INTO outbox(update_id,messages) VALUES (?,?)',
                            (update_id, json.dumps(messages, ensure_ascii=False)))

    def advance(self, update_id, count):
        with self.db:
            self.db.execute('UPDATE outbox SET sent=? WHERE update_id=?', (count, update_id))

    def acknowledge(self, update_id):
        with self.db:
            self.db.execute('UPDATE cursor SET value=MAX(value, ?) WHERE id=1', (update_id + 1,))
            self.db.execute('DELETE FROM outbox WHERE update_id=?', (update_id,))

    def close(self):
        self.db.close()


def process_update(update, bot, state, loader=fetch_day):
    update_id = update['update_id']
    if update_id < state.offset:
        return
    message = update.get('message') or {}
    chat = message.get('chat') or {}
    # This is a personal bot: other chats never receive the user's schedule.
    if (chat.get('type') != 'private' or str(chat.get('id')) != bot.chat_id
            or message.get('from', {}).get('is_bot') or not isinstance(message.get('text'), str)):
        state.acknowledge(update_id)
        return
    pending = state.pending(update_id)
    if pending is None:
        today = datetime.fromtimestamp(message['date'], TZ).date()
        text = message['text'].strip()
        if text in ('/start', '/help'):
            messages = [HELP]
        else:
            try:
                day = parse_day(text, today)
            except ValueError:
                messages = ['Не удалось распознать дату. ' + HELP]
            else:
                try:
                    messages = lesson_messages(loader(day), day, today)
                except Exception:
                    messages = ['Не удалось получить расписание на ' + day_label(day, today) + '. Попробуй ещё раз позже.']
        state.prepare(update_id, messages)
        pending = (messages, 0)
    messages, sent = pending
    for index in range(sent, len(messages)):
        bot.send(messages[index])
        state.advance(update_id, index + 1)
    state.acknowledge(update_id)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--once', action='store_true', help='Process pending messages once and exit')
    parser.add_argument('--state', default='work/telegram-bot.sqlite3')
    args = parser.parse_args()
    bot = Telegram(required('TELEGRAM_BOT_TOKEN'), required('TELEGRAM_CHAT_ID'))
    required('SKOLKOVO_EMAIL')
    required('SKOLKOVO_PASSWORD')
    bot.check_webhook()
    # One receiver per state file. Do not run a second receiver with another state.
    state_path = Path(args.state)
    state_path.parent.mkdir(parents=True, exist_ok=True)
    with open(str(state_path) + '.lock', 'w') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError('The daily schedule bot is already running') from None
        state = State(state_path)
        try:
            while True:
                try:
                    updates = bot.updates(state.offset, timeout=0 if args.once else 40)
                    for update in sorted(updates, key=lambda row: row['update_id']):
                        process_update(update, bot, state)
                    if args.once:
                        # Acknowledge the last delivered update at Telegram too.
                        bot.updates(state.offset, timeout=0)
                        return
                except TelegramError:
                    if args.once:
                        raise
                    print('Telegram temporarily unavailable; retrying', flush=True)
                    time.sleep(5)
        finally:
            state.close()


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        print(str(error) if isinstance(error, (TelegramError, RuntimeError)) else 'Daily bot failed: ' + type(error).__name__)
        raise SystemExit(1)
