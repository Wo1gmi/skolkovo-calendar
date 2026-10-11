"""Run: python3 scripts/telegram_setup.py [--test].
Token is read from TELEGRAM_BOT_TOKEN or a hidden terminal prompt.
--test sends one message to TELEGRAM_CHAT_ID, without touching the calendar.
"""
import argparse
import getpass
import json
import os
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--test', action='store_true')
    args = parser.parse_args()
    token = os.environ.get('TELEGRAM_BOT_TOKEN') or getpass.getpass('Токен бота (ввод скрыт): ')
    method = 'sendMessage' if args.test else 'getUpdates'
    payload = None
    if args.test:
        chat = os.environ.get('TELEGRAM_CHAT_ID') or input('Chat ID: ').strip()
        payload = json.dumps({'chat_id': chat, 'text': '✅ Уведомления Сколково подключены. Это тестовое сообщение.'}).encode()
    request = Request('https://api.telegram.org/bot' + token + '/' + method,
                      data=payload, headers={'Content-Type': 'application/json'})
    try:
        with urlopen(request, timeout=20) as response:
            data = json.load(response)
    except HTTPError as error:
        print('Telegram вернул HTTP', error.code, '— проверь токен и Chat ID.')
        return 1
    except (URLError, ValueError):
        print('Не удалось получить ответ Telegram.')
        return 1
    if not data.get('ok'):
        print('Telegram отклонил запрос.')
        return 1
    if args.test:
        print('Тестовое сообщение отправлено.')
        return 0
    ids = sorted({item['message']['chat']['id'] for item in data.get('result', [])
                  if item.get('message', {}).get('chat', {}).get('type') == 'private'})
    if ids:
        print('ID личных чатов:', *ids)
        print('Выбери свой ID, если боту писали несколько человек.')
    else:
        print('Отправь своему боту /start и повтори.')
    return 0

if __name__ == '__main__':
    raise SystemExit(main())
