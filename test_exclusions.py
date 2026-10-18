import copy
import unittest
from datetime import date
from unittest.mock import patch

from notifications import TZ, change_message
from schedule_rules import excluded_title
from sync import MARKER, event_body, notify_change, plan


class ExclusionTests(unittest.TestCase):
    def setUp(self):
        self.first, self.last = date(2026, 10, 18), date(2027, 10, 18)
        self.item = dict(id=1, title='Элективные дисциплины по физической культуре и спорту · Степин',
                         start='2026-11-06 10:00:00', end='2026-11-06 11:30:00')
        self.old = dict(event_body(self.item, TZ), id='managed-physical-education')

    def test_names_and_variants(self):
        for title in ('Элективные дисциплины по физической культуре и спорту',
                      'Элективные занятия по физре', 'Элективы по физкультуре',
                      'ЭЛЕКТИВНЫЕ КУРСЫ ПО ФИЗИЧЕСКОЙ КУЛЬТУРЕ',
                      'Элективные дисциплины по физической культуре · Степин'):
            with self.subTest(title=title):
                self.assertTrue(excluded_title(title))

    def test_other_subjects_are_retained(self):
        for title in ('Методы теоретической физики', 'Элективные курсы по физике',
                      'Прикладная физическая культура', 'Искусственный интеллект в бизнесе',
                      'Финансы · Преподаватель (элективные занятия по физре)'):
            with self.subTest(title=title):
                self.assertFalse(excluded_title(title))

    def test_excluded_source_never_inserts(self):
        self.assertEqual(plan([self.item], [], TZ, self.first, self.last), [])

    def test_existing_excluded_events_deleted_even_when_regular_deletions_disabled(self):
        for regular_deletions in (True, False):
            self.assertEqual(plan([self.item], [self.old], TZ, self.first, self.last,
                                  delete=regular_deletions), [('delete', self.old['id'], None)])

    def test_past_excluded_event_is_removed_even_when_not_in_source(self):
        old = copy.deepcopy(self.old)
        old['start']['dateTime'] = '2026-10-01T10:00:00+03:00'
        self.assertEqual(plan([], [old], TZ, self.first, self.last), [('delete', old['id'], None)])

    def test_manually_created_event_not_deleted(self):
        old = dict(self.old, extendedProperties={})
        self.assertEqual(plan([self.item], [old], TZ, self.first, self.last, delete=True), [])

    def test_other_subject_not_deleted_by_exclusion(self):
        other = dict(self.item, id=2, title='Методы теоретической физики')
        existing = dict(event_body(other, TZ), id='physics')
        existing['extendedProperties']['private']['content_hash'] = plan([other], [], TZ, self.first, self.last)[0][2]['extendedProperties']['private']['content_hash']
        self.assertEqual(plan([self.item, other], [self.old, existing], TZ, self.first, self.last),
                         [('delete', self.old['id'], None)])

    def test_empty_source_guard_still_protects_other_subjects(self):
        other = dict(self.item, id=2, title='Финансы')
        old = dict(event_body(other, TZ), id='finance')
        with self.assertRaises(RuntimeError):
            plan([], [self.old, old], TZ, self.first, self.last, delete=True)

    def test_excluded_source_id_cleans_up_old_title(self):
        old = dict(self.old, summary='Старое название')
        self.assertEqual(plan([self.item], [old], TZ, self.first, self.last), [('delete', old['id'], None)])

    @patch.dict('os.environ', TELEGRAM_BOT_TOKEN='test', TELEGRAM_CHAT_ID='test')
    @patch('sync.send_telegram')
    def test_no_notifications_for_any_excluded_action(self, send):
        for kind in ('insert', 'patch', 'delete'):
            self.assertIsNone(change_message(kind, self.old, self.old))
            notify_change(kind, self.old, self.old)
        send.assert_not_called()
