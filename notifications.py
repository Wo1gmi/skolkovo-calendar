"""Human-readable Telegram messages; calendar comparisons stay unchanged."""
import re
from datetime import datetime
from difflib import SequenceMatcher
from zoneinfo import ZoneInfo

MONTHS = ('января февраля марта апреля мая июня июля августа сентября '
          'октября ноября декабря').split()
TZ = ZoneInfo('Europe/Moscow')


def format_date(value):
    if value.tzinfo is not None:
        value = value.astimezone(TZ)
    return f'{value.day} {MONTHS[value.month - 1]} {value:%H:%M}'


def letters_digits(value):
    # Preserve every letter (including case) and digit, ignore presentation.
    return ''.join(c for c in value if c.isalnum())


def changed_text(old, new):
    if letters_digits(old) == letters_digits(new):
        return None
    # Keep only the changed words; common suffixes such as «(онлайн)» vanish.
    old_words, new_words = old.split(), new.split()
    matcher = SequenceMatcher(None, [letters_digits(w) for w in old_words],
                              [letters_digits(w) for w in new_words], autojunk=False)
    before, after = [], []
    for tag, a, b, c, d in matcher.get_opcodes():
        if tag != 'equal':
            before.extend(old_words[a:b])
            after.extend(new_words[c:d])
    return (' '.join(before) or '—') + ' → ' + (' '.join(after) or '—')


def description_fields(event):
    result = {}
    label = None
    for line in (event.get('description') or '').replace(r'\n', '\n').splitlines():
        key, separator, value = line.partition(':')
        if separator:
            label = key.strip()
            result[label] = value.strip()
        elif label:
            result[label] += ' ' + line.strip()
        elif line.strip():
            label = 'Описание'
            result[label] = line.strip()
    return result


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

    # The API repeats teachers/notes in the title. Report each change once.
    def title_remainder(event, fields):
        text = letters_digits(event.get('summary') or '')
        for value in sorted(fields.values(), key=len, reverse=True):
            value = letters_digits(value)
            if value:
                text = text.replace(value, '')
        return text

    if title_remainder(previous, old_fields) != title_remainder(body, new_fields):
        delta = changed_text(previous.get('summary') or '', title)
        if delta:
            differences.insert(0, 'Название: ' + delta)
    if any(moment(previous, field) != moment(body, field) for field in ('start', 'end')):
        differences.append('Время: ' + when(previous) + ' → ' + when(body))
    if not differences:
        return None
    # A subject separated by «·» is enough context; details follow below.
    return '🔄 Изменено: ' + title.split('·', 1)[0].strip() + '\n' + when(body) + '\n' + '\n'.join(differences)
