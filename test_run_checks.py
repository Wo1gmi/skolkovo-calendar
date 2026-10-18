import os
import subprocess
import unittest
from unittest.mock import patch

import run_checks


SETTINGS = dict(SKOLKOVO_EMAIL='test', SKOLKOVO_PASSWORD='test',
                GOOGLE_SERVICE_ACCOUNT_JSON='{}', GOOGLE_CALENDAR_ID='test',
                TELEGRAM_BOT_TOKEN='test', TELEGRAM_CHAT_ID='test')


@patch.dict(os.environ, SETTINGS, clear=True)
class VerificationTests(unittest.TestCase):
    @patch('run_checks.send_telegram')
    @patch('run_checks.sync_schedule', return_value=dict(insert=1, patch=2, delete=0))
    @patch('run_checks.subprocess.run')
    def test_tests_then_live_sync_and_dated_report(self, tests, sync, send):
        order = []
        tests.side_effect = lambda *a, **k: order.append('tests')
        def update():
            order.append('sync')
            self.assertEqual(os.environ['APPLY'], 'true')
            self.assertEqual(os.environ['RESET_COLORS'], 'false')
            return dict(insert=1, patch=2, delete=0)
        sync.side_effect = update
        run_checks.main()
        self.assertEqual(order, ['tests', 'sync'])
        self.assertTrue(tests.call_args.kwargs['check'])
        message = send.call_args.args[0]
        self.assertIn('Обновлено: 2', message)
        self.assertIn(run_checks.format_date(run_checks.datetime.now(run_checks.TZ)), message)

    @patch('run_checks.send_telegram')
    @patch('run_checks.sync_schedule')
    @patch('run_checks.subprocess.run', side_effect=subprocess.CalledProcessError(1, ['tests']))
    def test_failed_tests_prevent_calendar_writes(self, tests, sync, send):
        with self.assertRaises(subprocess.CalledProcessError):
            run_checks.main()
        sync.assert_not_called()
        self.assertIn('❌', send.call_args.args[0])

    @patch('run_checks.send_telegram')
    @patch('run_checks.sync_schedule', side_effect=ValueError('private http response'))
    @patch('run_checks.subprocess.run')
    def test_failed_sync_reports_without_private_details(self, tests, sync, send):
        with self.assertRaises(ValueError):
            run_checks.main()
        message = send.call_args.args[0]
        self.assertIn('ValueError', message)
        self.assertNotIn('private', message)
        self.assertNotIn('✅', message)

    @patch('run_checks.sync_schedule')
    @patch('run_checks.subprocess.run')
    def test_offline_fixture_cannot_fake_live_update(self, tests, sync):
        os.environ['SCHEDULE_FILE'] = 'fixture.json'
        with self.assertRaises(RuntimeError):
            run_checks.main()
        tests.assert_not_called()
        sync.assert_not_called()

    @patch('run_checks.sync_schedule')
    @patch('run_checks.subprocess.run')
    def test_missing_telegram_configuration_stops_before_writes(self, tests, sync):
        os.environ.pop('TELEGRAM_CHAT_ID')
        with self.assertRaises(RuntimeError):
            run_checks.main()
        sync.assert_not_called()
        tests.assert_not_called()
