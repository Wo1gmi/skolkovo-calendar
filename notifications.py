"""Human-readable Telegram messages; calendar comparisons stay unchanged."""
import re
from datetime import datetime
from zoneinfo import ZoneInfo

MONTHS = ('января февраля марта апреля мая июня июля августа сентября '
          'октября ноября декабря').split()
TZ = ZoneInfo('Europe/Moscow')


def format_date(value):
    if value.tzinfo is not None:
        value = value.astimezone(TZ)
    return f'{value.day} {MONTHS[value.month - 1]} {value:%H:%M}'


def text_key(value):
    # Keep word and number boundaries: «Гр.1» == «гр 1», but «1, 2» != «12».
    # Capitalisation is presentation; ё/е and different digits remain distinct.
    return tuple(re.findall(r'[^\W\d_]+|\d+', value.casefold()))


def changed_text(old, new):
    if text_key(old) == text_key(new):
        return None
    # Complete changed fields retain context (group numbers, names, negations).
    return (old.strip() or '—') + ' → ' + (new.strip() or '—')


DESCRIPTION_LABELS = ('Преподаватель', 'Примечание', 'Поток', 'Пара')


def description_fields(event):
    result = {}
    label = None
    labels = '|'.join(DESCRIPTION_LABELS)
    for line in (event.get('description') or '').replace(r'\n', '\n').splitlines():
        match = re.match(r'^(' + labels + r'):\s*(.*)$', line)
        if match:
            label = match[1]
            result[label] = match[2].strip()
        elif line.strip():
            # A colon in a note (e.g. «Ссылка: ...») is still part of that note.
            label = label or 'Описание'
            result[label] = (result.get(label, '') + ' ' + line.strip()).strip()
    return result


def subject(event):
    # «·» separates the subject from the API's repeated teacher/note suffix.
    # Suffix rendering is not an independent source field: do not diff it.
    return (event.get('summary') or 'Занятие').split('·', 1)[0].strip()


def moment(event, field):
    value = event.get(field, {})
    raw = value.get('dateTime')
    if raw:
        parsed = datetime.fromisoformat(raw)
        zone = ZoneInfo(value.get('timeZone') or str(TZ))
        return parsed.replace(tzinfo=zone) if parsed.tzinfo is None else parsed
    return None


def when(event):
    start, end = moment(event, 'start'), moment(event, 'end')
    if start:
        text = format_date(start)
        if end:
            local_end = end.astimezone(TZ)
            text += '–' + (local_end.strftime('%H:%M') if start.astimezone(TZ).date() == local_end.date()
                           else format_date(end))
        return text
    return 'время не указано'


def change_message(kind, previous, body):
    event = previous if kind == 'delete' else body
    title = event.get('summary') or 'Занятие'
    if kind in ('delete', 'insert'):
        prefix = '❌ Отменено: ' if kind == 'delete' else '➕ Новое занятие: '
        return prefix + title + '\n' + when(event)

    old_fields, new_fields = description_fields(previous), description_fields(body)
    differences = []
    for label in dict.fromkeys([*old_fields, *new_fields]):
        if label == 'Поток':
            continue
        delta = changed_text(old_fields.get(label, ''), new_fields.get(label, ''))
        if delta:
            differences.append(label + ': ' + delta)
    delta = changed_text(previous.get('location') or '', body.get('location') or '')
    if delta:
        differences.append('Аудитория: ' + delta)

    delta = changed_text(subject(previous), subject(body))
    if delta:
        differences.insert(0, 'Название: ' + delta)
    if any(moment(previous, field) != moment(body, field) for field in ('start', 'end')):
        differences.append('Время: ' + when(previous) + ' → ' + when(body))
    if not differences:
        return None
    # A subject separated by «·» is enough context; details follow below.
    return '🔄 Изменено: ' + subject(body) + '\n' + when(body) + '\n' + '\n'.join(differences)
