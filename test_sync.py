import unittest
import json
from datetime import date
from zoneinfo import ZoneInfo
from sync import plan, event_body, MARKER

class SyncTests(unittest.TestCase):
    def setUp(self):
        self.tz = ZoneInfo('Europe/Moscow')
        self.item = {'id': 1, 'title': 'Занятие', 'start': '2026-10-06 10:00:00',
                     'end': '2026-10-06 11:30:00', 'room': None}
        self.first, self.last = date(2026, 10, 1), date(2027, 10, 1)

    def run_plan(self, items, existing, delete=False):
        return plan(items, existing, self.tz, self.first, self.last, delete)

    def test_idempotence_and_reschedule(self):
        action = self.run_plan([self.item], [])[0]
        event = dict(action[2], id='google1')
        self.assertEqual(self.run_plan([self.item], [event]), [])
        moved = dict(self.item, start='2026-10-07 10:00:00', end='2026-10-07 11:30:00')
        self.assertEqual(self.run_plan([moved], [event])[0][:2], ('patch', 'google1'))
        self.assertTrue(event['start']['dateTime'].endswith('+03:00'))

    def test_deletion_only_managed_in_window(self):
        old = dict(self.run_plan([self.item], [])[0][2], id='google1')
        future = dict(old, id='far', start={'dateTime': '2028-01-01T10:00:00+03:00'},
                      extendedProperties={'private': {'sync_source': MARKER, 'source_id': '3'}})
        manual = dict(old, id='manual', extendedProperties={})
        other = dict(self.item, id=2)
        actions = self.run_plan([other], [old, manual, future], True)
        self.assertEqual([a[1] for a in actions if a[0] == 'delete'], ['google1'])
        with self.assertRaises(RuntimeError):
            self.run_plan([], [old], True)

    def test_invalid_duration(self):
        with self.assertRaises(RuntimeError):
            event_body(dict(self.item, end=self.item['start']), self.tz)

if __name__ == '__main__':
    unittest.main()
