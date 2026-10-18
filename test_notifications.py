import copy
import os
import unittest
from datetime import datetime
from unittest.mock import patch

from notifications import change_message, format_date
from sync import notify_change, plan, event_body
from notifications import TZ


class NotificationTests(unittest.TestCase):
    def setUp(self):
        self.item = dict(id=1, title='Искусственный интеллект в бизнесе · Ким Константин (Гр.1 (онлайн))',
                         teachers='Ким Константин', note='Гр.1 (онлайн)', cohort='МФТИ-2024',
                         schedule_title='1 пара', start='2026-10-18 10:00:00', end='2026-10-18 11:30:00')
        self.old = event_body(self.item, TZ)

    def test_user_example(self):
        new = event_body(dict(self.item, note='Лекция (онлайн)',
                             title=self.item['title'].replace('Гр.1', 'Лекция')), TZ)
        message = change_message('patch', self.old, new)
        self.assertEqual(message, '🔄 Изменено: Искусственный интеллект в бизнесе\n'
                         '18 октября 10:00–11:30\nПримечание: Гр.1 → Лекция')

    def test_cosmetic_change_still_updates_calendar_without_notification(self):
        old = dict(self.old, id='google1')
        changed = dict(self.item, title=self.item['title'].replace('(', '[').replace(')', ']'),
                       note='Гр 1 [онлайн]')
        actions = plan([changed], [old], TZ, datetime(2026, 10, 1).date(), datetime(2027, 1, 1).date())
        self.assertEqual(actions[0][0], 'patch')
        self.assertIsNone(change_message('patch', old, actions[0][2]))

    def test_changed_digit_in_group(self):
        new = event_body(dict(self.item, note='Гр.2 (онлайн)',
                             title=self.item['title'].replace('Гр.1', 'Гр.2')), TZ)
        message = change_message('patch', self.old, new)
        self.assertIn('Примечание: Гр.1 → Гр.2', message)
        self.assertNotIn('Название:', message)
        self.assertNotIn('МФТИ', message)

    def test_teacher_change(self):
        new = event_body(dict(self.item, teachers='Ким Александр',
                             title=self.item['title'].replace('Константин', 'Александр')), TZ)
        self.assertIn('Преподаватель: Константин → Александр', change_message('patch', self.old, new))
        self.assertNotIn('Название:', change_message('patch', self.old, new))

    def test_time_change_and_new_date(self):
        new = event_body(dict(self.item, start='2026-10-19 10:30:00', end='2026-10-19 12:00:00'), TZ)
        message = change_message('patch', self.old, new)
        self.assertIn('Время: 18 октября 10:00–11:30 → 19 октября 10:30–12:00', message)
        self.assertNotIn('Преподаватель:', message)

    def test_title_only_change(self):
        new = copy.deepcopy(self.old)
        new['summary'] = new['summary'].replace('бизнесе', 'образовании')
        self.assertIn('Название: бизнесе → образовании', change_message('patch', self.old, new))

    def test_removed_field_and_changed_room(self):
        new = copy.deepcopy(self.old)
        new['description'] = new['description'].replace('Пара: 1 пара', '')
        new['location'] = '201'
        message = change_message('patch', self.old, new)
        self.assertIn('Пара: 1 пара → —', message)
        self.assertIn('Аудитория: — → 201', message)

    def test_color_and_equivalent_timezone_are_silent(self):
        new = copy.deepcopy(self.old)
        new['colorId'] = '3'
        new['start']['dateTime'] = '2026-10-18T07:00:00+00:00'
        self.assertIsNone(change_message('patch', self.old, new))

    def test_all_months_and_moscow_timezone(self):
        for month, name in enumerate(('января февраля марта апреля мая июня июля августа сентября октября ноября декабря').split(), 1):
            self.assertEqual(format_date(datetime(2026, month, 1, 9)), f'1 {name} 09:00')
        self.assertEqual(format_date(datetime.fromisoformat('2026-10-17T22:00:00+00:00')), '18 октября 01:00')

    @patch.dict(os.environ, TELEGRAM_BOT_TOKEN='test', TELEGRAM_CHAT_ID='test')
    @patch('sync.requests.post')
    def test_delivery_has_real_newlines_and_no_cosmetic_alert(self, post):
        post.return_value.status_code = 200
        post.return_value.json.return_value = {'ok': True}
        notify_change('insert', {}, self.old)
        text = post.call_args.kwargs['json']['text']
        self.assertIn('\n18 октября', text)
        self.assertNotIn(r'\n', text)
        post.reset_mock()
        notify_change('patch', self.old, self.old)
        post.assert_not_called()

    @patch.dict(os.environ, LMS_APPLY='false', LMS_REPORT='true')
    @patch('lms_sync.telegram')
    @patch('lms_sync.future_assignments', side_effect=lambda assignments: assignments)
    @patch('lms_sync.collect')
    @patch('lms_sync.LMS')
    def test_lms_report_date_format(self, client, collect, future, telegram):
        from lms_sync import main
        collect.return_value = ([dict(course='Финансы', title='ДЗ',
                                      due='2026-10-18T23:59:00+03:00', url='https://l.skolkovo.ru/')], [], [])
        main()
        self.assertIn('Сдать до: 18 октября 23:59', telegram.call_args.args[0])
        self.assertNotIn('18.10.2026', telegram.call_args.args[0])

    def test_calendar_has_no_cohort(self):
        self.assertNotIn('Поток', self.old['description'])
        self.assertNotIn('МФТИ-2024', self.old['description'])

    def test_existing_calendar_cohort_is_removed_without_alert(self):
        old = copy.deepcopy(self.old)
        old['description'] += '\nПоток: МФТИ-2024'
        self.assertIsNone(change_message('patch', old, self.old))
        old['id'] = 'google1'
        old['extendedProperties']['private']['content_hash'] = 'old-with-cohort'
        actions = plan([self.item], [old], TZ, datetime(2026, 10, 1).date(), datetime(2027, 1, 1).date())
        self.assertEqual(actions[0][0], 'patch')
        self.assertNotIn('Поток', actions[0][2]['description'])

    def test_cohort_is_never_part_of_notification(self):
        old = copy.deepcopy(self.old)
        old['description'] += '\nПоток: МФТИ-2024'
        new = copy.deepcopy(self.old)
        new['location'] = '201'
        message = change_message('patch', old, new)
        self.assertNotIn('Поток', message)
        self.assertNotIn('МФТИ', message)
        self.assertIn('18 октября', message)

    def test_lms_reminder_always_includes_due_date(self):
        from lms_sync import assignment_reminder
        assignment = dict(course='Финансы', title='ДЗ', due='2026-10-18T23:59:00+03:00',
                          url='https://l.skolkovo.ru/')
        for hours in (72, 24, 1):
            message = assignment_reminder(assignment, hours)
            self.assertIn('Сдать до: 18 октября 23:59', message)
            self.assertIn('Финансы', message)

    def test_cancelled_date_format(self):
        self.assertIn('\n18 октября 10:00–11:30', change_message('delete', self.old, None))


if __name__ == '__main__':
    unittest.main()
