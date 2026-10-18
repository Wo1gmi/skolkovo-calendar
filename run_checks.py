"""Run regression tests, update the real schedule, and report to Telegram."""
import os
from pathlib import Path
import subprocess
import sys
from datetime import datetime

from notifications import format_date, TZ
from sync import main as sync_schedule, required, send_telegram

ROOT = Path(__file__).resolve().parent


def main():
    # Validate configuration before starting a run that must write live changes.
    for name in ('SKOLKOVO_EMAIL', 'SKOLKOVO_PASSWORD',
                 'GOOGLE_SERVICE_ACCOUNT_JSON', 'GOOGLE_CALENDAR_ID',
                 'TELEGRAM_BOT_TOKEN', 'TELEGRAM_CHAT_ID'):
        required(name)
    if os.environ.get('SCHEDULE_FILE'):
        raise RuntimeError('SCHEDULE_FILE is set: live verification requires the real schedule')
    os.environ.setdefault('SCHEDULE_TIMEZONE', 'Europe/Moscow')
    os.environ['APPLY'] = 'true'
    os.environ['RESET_COLORS'] = 'false'
    try:
        subprocess.run([sys.executable, '-m', 'unittest', 'discover', '-v'],
                       cwd=ROOT, check=True)
        counts = sync_schedule()
        send_telegram('✅ Проверка завершена\n' + format_date(datetime.now(TZ)) +
                      '\nТесты пройдены. Расписание обновлено.' +
                      '\nДобавлено: ' + str(counts['insert']) +
                      '\nОбновлено: ' + str(counts['patch']) +
                      '\nУдалено: ' + str(counts['delete']))
    except Exception as error:
        # Calendar writes may have started; never claim rollback or leak HTTP errors.
        try:
            send_telegram('❌ Проверка не завершена\n' + format_date(datetime.now(TZ)) +
                          '\nЭтап завершился с ошибкой: ' + type(error).__name__ +
                          '\nПодробности — в журнале запуска GitHub Actions.')
        except Exception:
            print('Failed to send failure report to Telegram')
        raise


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        print('Verification failed:', str(error) if isinstance(error, RuntimeError)
              else type(error).__name__)
        raise SystemExit(1)
