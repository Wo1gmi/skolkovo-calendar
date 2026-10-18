import copy
from datetime import date, datetime
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock

from daily_schedule_bot import State, TelegramError, lesson_messages, parse_day, process_update
from notifications import TZ


class DateTests(unittest.TestCase):
    def test_dates_from_user_request(self):
        today = date(2026, 10, 18)
        self.assertEqual(parse_day('18 октября', today), today)
        self.assertEqual(parse_day('14 ноября', today), date(2026, 11, 14))
        self.assertEqual(parse_day('  14 НОЯБРЯ  ', today), date(2026, 11, 14))

    def test_rollover_and_explicit_year(self):
        self.assertEqual(parse_day('14 января', date(2026, 10, 18)), date(2027, 1, 14))
        self.assertEqual(parse_day('14 ноября 2025', date(2026, 10, 18)), date(2025, 11, 14))
        self.assertEqual(parse_day('29 февраля', date(2026, 10, 18)), date(2028, 2, 29))

    def test_reject_invalid_or_extra_text(self):
        for value in ('31 ноября', '0 октября', '32 января', '29 февраля 2027',
                      '18.10.2026', '18 октября удали всё', 'привет', '18 Октябьря'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                parse_day(value, date(2026, 10, 18))


class LessonTests(unittest.TestCase):
    def setUp(self):
        self.day = date(2026, 11, 14)
        self.item = dict(id=1, title='Финансы · Иван Петров (Контрольная)', teachers='Иван Петров',
                         note='Контрольная. Взять калькулятор.', room='201', cohort='МФТИ-2024',
                         schedule_title='1 пара', start='2026-11-14 10:00:00', end='2026-11-14 11:30:00')

    def test_complete_lesson_format(self):
        result = lesson_messages([self.item], self.day, self.day)
        self.assertEqual(result, ['1 пара · 14 ноября\n10:00–11:30\nФинансы\n'
                                 'Преподаватель: Иван Петров\nПримечание: Контрольная. Взять калькулятор.\nАудитория: 201'])
        self.assertNotIn('МФТИ', result[0])

    def test_exactly_one_message_per_lesson_and_correct_order(self):
        items = [dict(self.item, id=i, schedule_title=f'{i} пара',
                      start=f'2026-11-14 {8+i:02}:00:00', end=f'2026-11-14 {8+i:02}:30:00') for i in range(1, 5)]
        for count in (3, 4):
            result = lesson_messages(list(reversed(items[:count])), self.day, self.day)
            self.assertEqual(len(result), count)
            self.assertTrue(result[0].startswith('1 пара'))
            self.assertTrue(result[-1].startswith(f'{count} пара'))

    def test_physical_education_and_other_days_are_removed(self):
        physical = dict(self.item, id=2, title='Элективные дисциплины по физической культуре и спорту')
        other_day = dict(self.item, id=3, start='2026-11-15 10:00:00', end='2026-11-15 11:30:00')
        self.assertEqual(len(lesson_messages([self.item, physical, other_day], self.day, self.day)), 1)
        self.assertEqual(lesson_messages([physical], self.day, self.day), ['На 14 ноября занятий нет.'])

    def test_identical_source_id_is_not_duplicated(self):
        self.assertEqual(len(lesson_messages([self.item, self.item], self.day, self.day)), 1)
        with self.assertRaises(RuntimeError):
            lesson_messages([self.item, dict(self.item, note='Другой текст')], self.day, self.day)

    def test_missing_number_is_not_invented(self):
        item = dict(self.item, schedule_title=None)
        self.assertIn('номер не указан', lesson_messages([item], self.day, self.day)[0])

    def test_next_year_is_explicit(self):
        self.assertIn('14 ноября 2026', lesson_messages([self.item], self.day, date(2025, 12, 1))[0])

    def test_timezone_day_boundary(self):
        item = dict(self.item, start='2026-11-13T22:00:00Z', end='2026-11-13T23:00:00Z')
        self.assertIn('01:00–02:00', lesson_messages([item], self.day, self.day)[0])

    def test_invalid_duration_aborts_entire_response(self):
        with self.assertRaises(RuntimeError):
            lesson_messages([self.item, dict(self.item, id=2, end=self.item['start'])], self.day, self.day)

    def test_long_notes_stay_in_one_message(self):
        result = lesson_messages([dict(self.item, note='Контрольная ' + '📝' * 5000)], self.day, self.day)
        self.assertEqual(len(result), 1)
        self.assertLessEqual(len(result[0].encode('utf-16-le')) // 2, 4096)
        self.assertIn('Контрольная', result[0])


class IncomingTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name) / 'state.sqlite3'
        self.state = State(self.path)
        self.bot = MagicMock()
        self.bot.chat_id = '123'
        self.loader = MagicMock(return_value=[])
        self.update = {'update_id': 10, 'message': {'message_id': 1,
                       'date': int(datetime(2026, 10, 18, tzinfo=TZ).timestamp()),
                       'chat': {'id': 123, 'type': 'private'}, 'from': {'is_bot': False}, 'text': '14 ноября'}}

    def tearDown(self):
        self.state.close()
        self.directory.cleanup()

    def test_allowed_request_fetches_requested_day(self):
        process_update(self.update, self.bot, self.state, self.loader)
        self.loader.assert_called_once_with(date(2026, 11, 14))
        self.bot.send.assert_called_once_with('На 14 ноября занятий нет.')
        self.assertEqual(self.state.offset, 11)

    def test_request_returns_four_lessons_as_four_messages(self):
        self.loader.return_value = [dict(id=i, title='Финансы', teachers='Иван Петров',
            note='Тест' if i == 2 else 'Лекция', schedule_title=f'{i} пара',
            start=f'2026-11-14 {8+i:02}:00:00', end=f'2026-11-14 {8+i:02}:30:00') for i in range(1, 5)]
        process_update(self.update, self.bot, self.state, self.loader)
        self.assertEqual(self.bot.send.call_count, 4)
        for i, call in enumerate(self.bot.send.call_args_list, 1):
            self.assertIn(f'{i} пара · 14 ноября', call.args[0])
            self.assertIn('Преподаватель: Иван Петров', call.args[0])
        self.assertIn('Примечание: Тест', self.bot.send.call_args_list[1].args[0])

    def test_unauthorized_chat_or_group_cannot_get_schedule(self):
        for chat in ({'id': 999, 'type': 'private'}, {'id': 123, 'type': 'group'}):
            with self.subTest(chat=chat):
                update = copy.deepcopy(self.update)
                update['message']['chat'] = chat
                process_update(update, self.bot, self.state, self.loader)
        self.loader.assert_not_called()
        self.bot.send.assert_not_called()

    def test_non_text_is_ignored(self):
        self.update['message'].pop('text')
        process_update(self.update, self.bot, self.state, self.loader)
        self.loader.assert_not_called()
        self.bot.send.assert_not_called()

    def test_help_and_invalid_date_do_not_fetch_schedule(self):
        self.update['message']['text'] = '31 ноября'
        process_update(self.update, self.bot, self.state, self.loader)
        self.loader.assert_not_called()
        self.assertIn('Не удалось распознать дату', self.bot.send.call_args.args[0])

    def test_duplicate_update_not_resent(self):
        process_update(self.update, self.bot, self.state, self.loader)
        process_update(self.update, self.bot, self.state, self.loader)
        self.assertEqual(self.bot.send.call_count, 1)

    def test_source_error_is_not_reported_as_no_lessons(self):
        self.loader.side_effect = RuntimeError('private source data')
        process_update(self.update, self.bot, self.state, self.loader)
        text = self.bot.send.call_args.args[0]
        self.assertIn('Не удалось получить расписание на 14 ноября', text)
        self.assertNotIn('private', text)
        self.assertNotIn('занятий нет', text)

    def test_partial_delivery_resumes_after_restart(self):
        self.state.prepare(10, ['1 пара', '2 пара', '3 пара', '4 пара'])
        self.bot.send.side_effect = [None, TelegramError('temporary')]
        with self.assertRaises(TelegramError):
            process_update(self.update, self.bot, self.state, self.loader)
        self.assertEqual(self.state.offset, 0)
        self.state.close()
        self.state = State(self.path)
        self.bot.send.reset_mock(side_effect=True)
        process_update(self.update, self.bot, self.state, self.loader)
        self.assertEqual([call.args[0] for call in self.bot.send.call_args_list], ['2 пара', '3 пара', '4 пара'])
        self.loader.assert_not_called()
        self.assertEqual(self.state.offset, 11)
        self.assertIsNone(self.state.pending(10))
