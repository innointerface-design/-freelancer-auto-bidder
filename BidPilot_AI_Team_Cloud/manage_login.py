"""Local operator recovery for one account's failed-sign-in counter."""
import argparse
import os
from pathlib import Path
import re


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('unlock',))
    parser.add_argument('--email', required=True, help='Existing account email; never enter a password here.')
    args = parser.parse_args(argv)
    email = args.email.strip().lower()
    if len(email) > 240 or not re.fullmatch(r'[^@\s]+@[^@\s]+\.[^@\s]+', email):
        parser.exit(1, 'Error: Enter a valid existing account email.\n')
    # Relative SQLite URLs must resolve exactly as they do when starting the app.
    os.chdir(Path(__file__).resolve().parent)
    try:
        from app import db
        from app.auth_limits import unlock_account_failures
        from sqlalchemy.exc import SQLAlchemyError
    except (ImportError, RuntimeError):
        parser.exit(1, 'Error: Load the existing private application configuration and installed requirements first.\n')
    try:
        with db.SessionLocal() as session:
            user = session.query(db.User).filter_by(email=email).first()
            if user is None:
                parser.exit(1, 'Error: Account not found; no changes were made.\n')
            unlock_account_failures(session, email)
            db.audit(session, user.id, 'login_unlocked',
                     'Local operator cleared account sign-in failures; network limits remain in force.')
            session.commit()
    except SQLAlchemyError:
        parser.exit(1, 'Error: Could not open the existing application database. Check private configuration.\n')
    print('Account sign-in failure counter cleared. Sign in with the existing password.')
    print('Shared network/request limits still apply. Password, plan, account status and sessions were not changed.')


if __name__ == '__main__':
    main()
