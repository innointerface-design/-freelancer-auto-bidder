"""Local operator diagnostics and recovery for an existing account."""
import argparse
import getpass
import os
from pathlib import Path
import re
import sys
import time
import warnings


def _hidden_password(parser, prompt):
    # getpass can fall back to echoing stdin when no terminal is available.
    # Refuse that path rather than risk putting a customer's password in logs.
    if not sys.stdin.isatty() or not sys.stderr.isatty():
        parser.exit(1, 'Error: Password entry requires an interactive terminal; passwords cannot be piped.\n')
    try:
        with warnings.catch_warnings():
            warnings.simplefilter('error', getpass.GetPassWarning)
            return getpass.getpass(prompt)
    except (getpass.GetPassWarning, EOFError, KeyboardInterrupt):
        parser.exit(1, '\nError: Secure password entry was cancelled or unavailable; no changes were made.\n')


def _database_location(parser, engine):
    if engine.dialect.name == 'sqlite':
        database = engine.url.database
        if not database or database == ':memory:' or not Path(database).is_file():
            parser.exit(1, 'Error: Existing SQLite database not found; no database was created. Check the original .env and application folder.\n')
        return 'SQLite file: ' + str(Path(database).resolve())
    # Never expose DATABASE_URL: it may contain a database password.
    return 'Database backend: ' + engine.dialect.name + ' (connection details hidden)'


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('check', 'unlock', 'reset-password'))
    parser.add_argument('--email', required=True, help='Existing account email; never enter a password here.')
    parser.add_argument('--verify-password', action='store_true', help='For check only: compare a password entered privately in the terminal.')
    args = parser.parse_args(argv)
    if args.verify_password and args.action != 'check':
        parser.error('--verify-password is only supported with check')
    email = args.email.strip().lower()
    if len(email) > 240 or not re.fullmatch(r'[^@\s]+@[^@\s]+\.[^@\s]+', email):
        parser.exit(1, 'Error: Enter a valid existing account email.\n')
    # Relative SQLite URLs must resolve exactly as they do when starting the app.
    os.chdir(Path(__file__).resolve().parent)
    try:
        from app import db
        from app.auth_limits import unlock_account_failures
        from sqlalchemy import update
        from sqlalchemy.exc import SQLAlchemyError
    except (ImportError, RuntimeError):
        parser.exit(1, 'Error: Load the existing private application configuration and installed requirements first.\n')
    location = _database_location(parser, db.engine)
    try:
        # Resolve and snapshot the existing account before asking for a secret.
        # Password hashing and terminal input must not hold a database lock.
        with db.SessionLocal() as session:
            user = session.query(db.User).filter_by(email=email).first()
            if user is None:
                print(location)
                parser.exit(1, 'Error: Account not found in this database; no changes were made. Check the original .env/database before creating any account.\n')
            uid, original_hash = user.id, user.password_hash
            if args.action == 'check':
                enabled = bool(user.active)
                plan = user.plan_tier
                subscription = user.subscription_status
            elif args.action == 'reset-password' and not user.active:
                parser.exit(1, 'Error: Account is disabled. An authorized administrator must review its Enabled status; no changes were made.\n')
        if args.action == 'check':
            print(location)
            print('Account found: yes')
            print('Enabled: ' + ('yes' if enabled else 'no'))
            print(f'Plan: {plan}; subscription: {subscription}')
            if args.verify_password:
                entered = _hidden_password(parser, 'Existing account password (hidden): ')
                try:
                    matched = db.verify_password(entered, original_hash)
                except UnicodeEncodeError:
                    parser.exit(1, 'Error: Password contains invalid text encoding; no changes were made.\n')
                del entered
                with db.SessionLocal() as session:
                    user = session.get(db.User, uid)
                    if not user or user.email != email or user.password_hash != original_hash or bool(user.active) != enabled:
                        parser.exit(1, 'Error: Account changed during this check. Run check again.\n')
                print('Password matches stored credential: ' + ('yes' if matched else 'no'))
                if matched and enabled:
                    print('Credentials match this database. If browser sign-in still fails, check that the web process uses this same installation/database and manually enter these credentials.')
                elif not enabled:
                    print('This account is disabled; an administrator must review its Enabled status.')
                else:
                    print('The entered password does not match this database. Recover this existing account; do not recreate it or pay again.')
            print('Diagnostic only: no account, plan, session or sign-in counters were changed.')
            return
        if args.action == 'reset-password':
            password = _hidden_password(parser, 'New account password (12-256 characters, hidden): ')
            if not 12 <= len(password) <= 256 or not password.strip():
                parser.exit(1, 'Error: Choose a non-blank password containing 12 to 256 characters; no changes were made.\n')
            confirmation = _hidden_password(parser, 'Confirm new password (hidden): ')
            if password != confirmation:
                parser.exit(1, 'Error: Password confirmation does not match; no changes were made.\n')
            try:
                new_hash = db.hash_password(password)
            except UnicodeEncodeError:
                parser.exit(1, 'Error: Password contains invalid text encoding; no changes were made.\n')
            del password, confirmation
        with db.SessionLocal() as session:
            # Match login's lock order: account identifier before User.
            unlock_account_failures(session, email)
            if args.action == 'reset-password':
                session.execute(update(db.User).where(db.User.id == uid).values(lock_version=db.User.lock_version + 1),
                                execution_options={'synchronize_session': False})
                session.expire_all()
                user = session.get(db.User, uid)
                if not user or user.email != email or user.password_hash != original_hash or not user.active:
                    parser.exit(1, 'Error: Account changed during recovery. Run check again; no changes were made.\n')
                user.password_hash = new_hash
                user.updated = int(time.time())
                session.query(db.AuthSession).filter_by(user_id=uid).delete(synchronize_session=False)
                db.audit(session, uid, 'password_reset_local',
                         'Local operator reset the existing account password and revoked its sessions; paid entitlement and account status preserved.')
            else:
                db.audit(session, uid, 'login_unlocked',
                         'Local operator cleared account sign-in failures; network limits remain in force.')
            session.commit()
    except SQLAlchemyError:
        parser.exit(1, 'Error: Could not open the existing application database. Check private configuration.\n')
    if args.action == 'reset-password':
        print('Existing account password reset. All sessions for this account were revoked.')
        print('Plan, expiry, account status, saved API keys and other accounts were preserved. Shared network/request limits still apply.')
        return
    print('Account sign-in failure counter cleared. Sign in with the existing password.')
    print('Shared network/request limits still apply. Password, plan, account status and sessions were not changed.')


if __name__ == '__main__':
    main()
