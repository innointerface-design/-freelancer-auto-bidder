"""Create a private .env once. Never includes example or chat-provided credentials."""
import argparse
import base64
import getpass
import os
from pathlib import Path
import re
import secrets

from cryptography.fernet import Fernet


def dotenv_value(value):
    # Compose respects single quotes; the app also loads dotenv with interpolate=False.
    return "'" + str(value).replace('\\', '\\\\').replace("'", "\\'") + "'"


def ask_email(prompt, optional=False):
    while True:
        value = input(prompt).strip().lower()
        if optional and not value:
            return ''
        if len(value) <= 240 and re.fullmatch(r'[^@\s]+@[^@\s]+\.[^@\s]+', value):
            return value
        print('Enter a valid email address.')


def ask_password(prompt):
    while True:
        value = getpass.getpass(prompt)
        if len(value) < 12 or len(value) > 256 or '\n' in value or '\r' in value:
            print('Use 12–256 characters, without line breaks.')
            continue
        if getpass.getpass('Confirm password: ') != value:
            print('Passwords did not match.')
            continue
        return value


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--production', action='store_true', help='Configure HTTPS Docker Compose deployment')
    args = parser.parse_args()
    path = Path(__file__).resolve().with_name('.env')
    if path.exists():
        print('Existing .env preserved. Edit it privately if configuration needs changing.')
        return
    email = ask_email('Owner/admin email: ')
    password = ask_password('Owner/admin password (12+ characters): ')
    second_email = ask_email('Second free founder email (optional; Enter skips): ', optional=True)
    if second_email == email:
        raise SystemExit('Second founder email must differ from owner email. No file written.')
    second_password = ask_password('Second founder password: ') if second_email else ''
    domain = ''
    public_url = 'http://127.0.0.1:8000'
    if args.production:
        domain = input('Public domain pointing to this server (e.g. bids.example.com): ').strip().lower()
        if not re.fullmatch(r'(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}', domain):
            raise SystemExit('Enter a domain only, without protocol/path. No file written.')
        public_url = 'https://' + domain
    values = {
        'APP_ENV': 'production' if args.production else 'development',
        'DATABASE_URL': 'sqlite:///./bidpilot_local.db',
        'SESSION_SECRET': secrets.token_urlsafe(48),
        'FERNET_KEY': Fernet.generate_key().decode(),
        'ADMIN_EMAIL': email,
        'ADMIN_PASSWORD_B64': base64.b64encode(password.encode('utf-8')).decode('ascii'),
        'SECOND_FOUNDER_EMAIL': second_email,
        'SECOND_FOUNDER_PASSWORD_B64': base64.b64encode(second_password.encode('utf-8')).decode('ascii'),
        'PUBLIC_URL': public_url,
        'SITE_DOMAIN': domain,
        'SECURE_COOKIES': str(args.production).lower(),
        'PLATFORM_AUTOBID_APPROVED': 'false',
        'ALLOW_PRODUCTION_CONNECTION': 'true',
        'SCAN_SECONDS': '60',
        'POSTGRES_PASSWORD': secrets.token_urlsafe(36),
        'GEMINI_API_KEY': '',
        'GEMINI_MODEL': 'gemini-3.5-flash-lite',
        'OPENAI_API_KEY': '',
        'OPENAI_MODEL': 'gpt-4.1-mini',
        'OPENAI_BASE_URL': 'https://api.openai.com/v1',
        'PAYMENT_JAZZCASH': '',
        'PAYMENT_EASYPAISA': '',
        'PAYMENT_BANK': '',
        'PAYMENT_STRIPE_URL': '',
        'PAYMENT_WHATSAPP': '',
    }
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'w', encoding='utf-8') as handle:
        handle.write('# Private configuration. Keep an encrypted backup; never commit or share.\n')
        for name, value in values.items():
            handle.write(f'{name}={dotenv_value(value)}\n')
    print('Private .env created. Automation is OFF until you explicitly enable it.')
    print('AI/payment credentials can be configured privately later; no live APIs were tested.')


if __name__ == '__main__':
    main()
