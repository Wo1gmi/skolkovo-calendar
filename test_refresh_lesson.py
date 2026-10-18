from datetime import date
import unittest
from unittest.mock import MagicMock, patch

from notifications import TZ
from refresh_lesson import refresh_selected, select_lesson
from sync import event_body


class RefreshTests(unittest.TestCase):
    def setUp(self):
        self.item = dict(id=1, title='Искусственный интеллект в бизнесе · Ким Константин Станиславович (Лекция (онлайн))',
                         teachers='Ким Константин Станиславович', note='Лекция (онлайн)', schedule_title='1 пара',
                         start='2026-12-04 10:00:00', end='2026-12-04 11:30:00')
        self.old = dict(event_body(self.item, TZ), id='google1', colorId='9')
        self.api = MagicMock()
        self.api.get.return_value.execute.return_value = self.old

    @patch('refresh_lesson.send_telegram')
    def test_identical_event_is_rewritten_verified_and_announced(self, send):
        refresh_selected(self.item, [self.old], self.api, 'calendar')
        self.api.patch.assert_called_once()
        self.api.get.assert_called_once()
        body = self.api.patch.call_args.kwargs['body']
        self.assertNotIn('colorId', body)
        card = send.call_args.args[0]
        for detail in ('4 декабря 10:00–11:30', '1 пара', 'Ким Константин Станиславович', 'Лекция (онлайн)'):
            self.assertIn(detail, card)
        self.assertNotIn('→', card)
        self.assertNotIn('Изменилось:', card)
        self.assertNotIn('Поток', card)

    @patch('refresh_lesson.send_telegram')
    def test_missing_or_ambiguous_event_cannot_be_written(self, send):
        for events in ([], [self.old, self.old]):
            with self.assertRaises(RuntimeError):
                refresh_selected(self.item, events, self.api, 'calendar')
        self.api.patch.assert_not_called()
        send.assert_not_called()

    @patch('refresh_lesson.send_telegram')
    def test_manual_event_not_updated(self, send):
        old = dict(self.old, extendedProperties={})
        with self.assertRaises(RuntimeError):
            refresh_selected(self.item, [old], self.api, 'calendar')
        self.api.patch.assert_not_called()
        send.assert_not_called()

    @patch('refresh_lesson.send_telegram')
    def test_failed_readback_does_not_announce_success(self, send):
        self.api.get.return_value.execute.return_value = dict(self.old, summary='wrong')
        with self.assertRaises(RuntimeError):
            refresh_selected(self.item, [self.old], self.api, 'calendar')
        send.assert_not_called()

    def test_selection_is_limited_to_exact_date_subject_and_time(self):
        items = [self.item, dict(self.item, id=2, start='2026-12-04 11:45:00', end='2026-12-04 13:15:00')]
        self.assertIs(select_lesson(items, date(2026, 12, 4), 'Искусственный интеллект в бизнесе', '10:00'), self.item)
        for day, subject, time in ((date(2026, 12, 5), 'Искусственный интеллект в бизнесе', '10:00'),
                                   (date(2026, 12, 4), 'Другой предмет', '10:00'),
                                   (date(2026, 12, 4), 'Искусственный интеллект в бизнесе', '12:00')):
            with self.assertRaises(RuntimeError):
                select_lesson(items, day, subject, time)
