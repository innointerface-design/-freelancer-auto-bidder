"""Explicitly configure the server auto-bidding gate without changing user accounts."""
import argparse
import io
import os
from pathlib import Path
import re
import stat
import tempfile

from dotenv.parser import parse_stream


KEY = 'PLATFORM_AUTOBID_APPROVED'


class ConfigurationError(Exception):
    pass


def read_configuration(path):
    try:
        before = path.lstat()
        if not stat.S_ISREG(before.st_mode):
            raise ConfigurationError('The existing .env must be a regular file, not a symlink.')
        descriptor = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0))
        with os.fdopen(descriptor, 'rb') as handle:
            opened = os.fstat(handle.fileno())
            if (before.st_dev, before.st_ino) != (opened.st_dev, opened.st_ino):
                raise ConfigurationError('The .env changed while opening it. Try again.')
            original = handle.read()
        if path.is_symlink():
            raise ConfigurationError('The existing .env must not be a symlink.')
        if original.startswith(b'\xef\xbb\xbf'):
            raise ConfigurationError('The .env has a UTF-8 BOM, which the app does not load consistently. Remove it privately before retrying.')
        text = original.decode('utf-8')
    except FileNotFoundError:
        raise ConfigurationError('Existing .env not found. Run setup_local.py first; no configuration was created.') from None
    except (OSError, UnicodeError):
        raise ConfigurationError('Could not safely read the existing UTF-8 .env; no configuration was changed.') from None
    bindings = list(parse_stream(io.StringIO(text)))
    if any(binding.error for binding in bindings):
        raise ConfigurationError('The .env has invalid syntax. Fix it privately before changing automation.')
    return original, text, bindings


def _updated_assignment(original, enabled):
    match = re.fullmatch(
        r"(?P<prefix>\s*(?:export[ \t]+)?(?:'PLATFORM_AUTOBID_APPROVED'|PLATFORM_AUTOBID_APPROVED)[ \t]*)"
        r'(?P<equal>=)?(?P<space>[ \t]*)(?P<rest>[\s\S]*)', original,
    )
    if match is None:
        raise ConfigurationError('Could not safely update the automation setting; no configuration was changed.')
    rest = match['rest']
    if rest.startswith(("'", '"')):
        expression = r"'(?:\\'|[^'])*'" if rest[0] == "'" else r'"(?:\\"|[^"])*"'
        value = re.match(expression, rest)
        if value is None:
            raise ConfigurationError('The automation setting has invalid quoting; no configuration was changed.')
        suffix = rest[value.end():]
    else:
        line = re.fullmatch(r'(?P<value>[^\r\n]*)(?P<ending>\r\n|\n|\r)?', rest)
        if line is None:
            raise ConfigurationError('Could not safely update the automation setting; no configuration was changed.')
        comment = re.search(r'[ \t]+#[^\r\n]*\Z', line['value'])
        if not match['equal'] and line['value'].startswith('#'):
            suffix = ' ' + line['value']
        else:
            suffix = comment.group(0) if comment else re.search(r'[ \t]*\Z', line['value']).group(0)
        suffix += line['ending'] or ''
    return match['prefix'] + '=' + match['space'] + ("'true'" if enabled else "'false'") + suffix


def updated_configuration(text, bindings, enabled):
    found = False
    chunks = []
    for binding in bindings:
        if binding.key == KEY:
            chunks.append(_updated_assignment(binding.original.string, enabled))
            found = True
        else:
            chunks.append(binding.original.string)
    updated = ''.join(chunks)
    if not found:
        ending = re.search(r'\r\n|\n|\r', text)
        newline = ending.group(0) if ending else '\n'
        if updated and not updated.endswith(('\r', '\n')):
            updated += newline
        updated += KEY + ("='true'" if enabled else "='false'") + newline
    return updated.encode('utf-8')


def _private_write(path, prefix, content):
    descriptor, filename = tempfile.mkstemp(prefix=prefix, dir=path.parent)
    target = Path(filename)
    try:
        with os.fdopen(descriptor, 'wb') as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
    except BaseException:
        target.unlink(missing_ok=True)
        raise
    return target


def configure(path, enabled):
    original, text, bindings = read_configuration(path)
    updated = updated_configuration(text, bindings, enabled)
    if updated == original:
        if os.name == 'posix':
            descriptor = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0))
            with os.fdopen(descriptor, 'rb') as handle:
                if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
                    raise ConfigurationError('The existing .env must be a regular file.')
                os.fchmod(handle.fileno(), 0o600)
        return None
    backup = _private_write(path, '.env.autobid-backup-', original)
    temporary = None
    try:
        temporary = _private_write(path, '.env.autobid-write-', updated)
        if read_configuration(path)[0] != original:
            raise ConfigurationError('The .env changed during this operation. No replacement was made; try again.')
        os.replace(temporary, path)
        temporary = None
    except OSError:
        raise ConfigurationError('Could not replace .env safely. The original configuration and private backup were preserved.') from None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return backup


def _report_override(enabled):
    inherited = os.environ.get(KEY)
    if inherited is not None and (inherited.lower() == 'true') != enabled:
        print('An inherited PLATFORM_AUTOBID_APPROVED value overrides this .env setting.')
        print('Update or unset that flag in the launch environment before restarting web and worker.')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('enable', 'disable', 'status'))
    args = parser.parse_args(argv)
    path = Path(__file__).resolve().with_name('.env')
    try:
        if args.action == 'status':
            _, _, bindings = read_configuration(path)
            values = {binding.key: binding.value for binding in bindings if binding.key is not None}
            enabled = (values.get(KEY) or '').lower() == 'true'
            print('Saved server auto-bidding configuration: ' + ('ON' if enabled else 'OFF') + '.')
            _report_override(enabled)
            print('This reports the saved configuration; running web/worker processes were not inspected.')
            return
        enabled = args.action == 'enable'
        backup = configure(path, enabled)
        print('Saved server auto-bidding configuration: ' + ('ON' if enabled else 'OFF') + '.')
        print('Private backup: ' + backup.name if backup else 'The configuration was already set; no backup was needed.')
        _report_override(enabled)
        print('Restart BOTH web and worker for this setting to take effect.')
        if enabled:
            print('For your account: connect Freelancer, enable Auto-Pilot in Settings, then Save.')
            print('Existing account permissions, filters, schedules and bid limits still apply.')
        else:
            print('Automatic bids and automatic client greetings will be disabled after both processes restart.')
        print('User accounts, tokens, billing and saved per-user preferences were not changed.')
    except ConfigurationError as error:
        parser.exit(1, 'Error: ' + str(error) + '\n')
    except OSError:
        parser.exit(1, 'Error: Could not safely update .env. Check file access privately.\n')


if __name__ == '__main__':
    main()
