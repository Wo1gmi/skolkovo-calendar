import io
import os
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import sync
from notifications import TZ


class LiveAuditTests(unittest.TestCase):
    def test_audit_cannot_write_or_notify_even_with_apply_enabled(self):
        day = datetime.now(TZ).date() + timedelta(days=1)
        item = dict(id=1, title='Private subject · Private Teacher', teachers='Private Teacher',
                    note='Private note', start=str(day) + ' 10:00:00', end=str(day) + ' 11:30:00')
        old = dict(sync.event_body(item, TZ), id='google1')
        old['extendedProperties']['private']['content_hash'] = 'old'
        old['location'] = 'old room'
        api = MagicMock()
        api.list.return_value.execute.return_value = {'items': [old]}
        service_account = MagicMock()
        build = MagicMock()
        build.return_value.events.return_value = api
        modules = {'google.oauth2': SimpleNamespace(service_account=service_account),
                   'googleapiclient.discovery': SimpleNamespace(build=build)}
        settings = dict(SCHEDULE_TIMEZONE='Europe/Moscow', APPLY='true',
                        GOOGLE_SERVICE_ACCOUNT_JSON='{}', GOOGLE_CALENDAR_ID='private-calendar')
        output = io.StringIO()
        with patch.dict(os.environ, settings, clear=True), patch.dict('sys.modules', modules), \
             patch('sync.get_session'), patch('sync.load_schedule', return_value=[item]), \
             patch('sync.notify_change') as notify, patch('sync.send_telegram') as send, redirect_stdout(output):
            counts = sync.main(audit=True)
        self.assertEqual(counts['patch'], 1)
        for method in (api.insert, api.patch, api.delete, notify, send):
            method.assert_not_called()
        self.assertIn('"pending_notifications": 1', output.getvalue())
        self.assertNotIn('Private', output.getvalue())
        self.assertNotIn('old room', output.getvalue())
        self.assertNotIn('private-calendar', output.getvalue())
