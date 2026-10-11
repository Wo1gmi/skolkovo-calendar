import unittest
from lms_sync import parse_due, candidates, safe_url, future_assignments, TZ, assignment_body, assignment_changed
from datetime import datetime


class LMSTest(unittest.TestCase):
    def test_due_only_explicit_label(self):
        due = parse_due('Открыто с: среда, 07 октября 2026, 00:00\nСрок сдачи: пятница, 16 октября 2026, 23:59')
        self.assertEqual(due.isoformat(), '2026-10-16T23:59:00+03:00')
        self.assertIsNone(parse_due('До встречи в среду!'))
        self.assertIsNone(parse_due('Дата публикации: 16 октября 2026, 23:59'))

    def test_file_instructions_not_date_guesses(self):
        self.assertTrue(candidates('Во время чтения делайте три типа заметок на полях.'))
        self.assertTrue(candidates('В рамках домашнего задания к заключительному занятию модуля'))
        self.assertEqual(candidates('Курс проводится осенью 2026 года.'), [])

    def test_red_all_day_before_actual_deadline(self):
        body = assignment_body({'course': 'Финансы', 'title': 'ДЗ', 'due': '2026-10-16T23:59:00+03:00', 'url': 'https://l.skolkovo.ru/'})
        self.assertEqual(body['start'], {'date': '2026-10-15'})
        self.assertEqual(body['end'], {'date': '2026-10-16'})
        self.assertEqual(body['colorId'], '11')
        self.assertIn('16.10.2026 23:59', body['description'])
        body = assignment_body({'course': 'Финансы', 'title': 'ДЗ', 'due': '2027-01-01T12:00:00+03:00', 'url': 'https://l.skolkovo.ru/'})
        self.assertEqual(body['start']['date'], '2026-12-31')

    def test_description_has_separate_clickable_url(self):
        url = 'https://l.skolkovo.ru/mod/assign/view.php?id=35342'
        body = assignment_body({'course': 'Финансы', 'title': 'ДЗ',
                                'due': '2026-10-16T23:59:00+03:00', 'url': url})
        self.assertEqual(len(body['description'].splitlines()), 3)
        self.assertEqual(body['description'].splitlines()[-1], url)
        self.assertNotIn(r'\n', body['description'])

    def test_notifications_ignore_presentation_changes(self):
        assignment = {'course': 'Финансы', 'title': 'ДЗ',
                      'due': '2026-10-16T23:59:00+03:00',
                      'url': 'https://l.skolkovo.ru/mod/assign/view.php?id=35342'}
        old = assignment_body(assignment)
        old['description'] = old['description'].replace('\n', r'\n')
        old['colorId'] = '5'
        self.assertFalse(assignment_changed(assignment, old))
        self.assertTrue(assignment_changed(dict(assignment, due='2026-10-17T23:59:00+03:00'), old))
        self.assertTrue(assignment_changed(dict(assignment, title='ДЗ 2'), old))
        self.assertTrue(assignment_changed(assignment, None))

    def test_only_strictly_future_deadlines(self):
        now = datetime(2026, 10, 11, 12, 0, tzinfo=TZ)
        items = [{'id': 'past', 'due': '2026-10-10T23:59:00+03:00'},
                 {'id': 'now', 'due': now.isoformat()},
                 {'id': 'future', 'due': '2026-10-16T23:59:00+03:00'}]
        self.assertEqual([a['id'] for a in future_assignments(items, now)], ['future'])

    def test_no_external_credentials_destination(self):
        self.assertEqual(safe_url('https://l.skolkovo.ru/my/'), 'https://l.skolkovo.ru/my/')
        for url in ('https://evil.test/', 'http://l.skolkovo.ru/', 'https://l.skolkovo.ru.evil.test/'):
            with self.assertRaises(RuntimeError):
                safe_url(url)


if __name__ == '__main__':
    unittest.main()
