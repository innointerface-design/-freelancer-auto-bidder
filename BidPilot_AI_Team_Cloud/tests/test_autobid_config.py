"""Operator opt-in changes only its saved flag and preserves private configuration."""
import importlib.util
import io
import os
from pathlib import Path
import stat

from dotenv import dotenv_values
import pytest


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('bidpilot_autobid_config', ROOT / 'configure_autobid.py')
configure = importlib.util.module_from_spec(spec)
spec.loader.exec_module(configure)


@pytest.fixture
def private_config(tmp_path, monkeypatch):
    monkeypatch.setattr(configure, '__file__', str(tmp_path / 'configure_autobid.py'))
    monkeypatch.delenv(configure.KEY, raising=False)
    path = tmp_path / '.env'
    return path


def values(path):
    return dotenv_values(stream=io.StringIO(path.read_bytes().decode('utf-8-sig')), interpolate=False)


def test_enable_preserves_other_bytes_comments_and_existing_account_files(private_config, capsys):
    original = (
        b'# Private configuration\r\n'
        b"FERNET_KEY='synthetic-encryption-key'\r\n"
        b"ADMIN_PASSWORD_B64='c3ludGhldGljLXByaXZhdGU='\r\n"
        b"OPENAI_API_KEY='synthetic-private-secret'\r\n"
        b"LITERAL='keep ${VALUE} and \\\'quotes\\\' exactly'\r\n"
        b'export PLATFORM_AUTOBID_APPROVED = "false" # operator choice\r\n'
        b"PLATFORM_AUTOBID_APPROVED=false # duplicated old value\r\n"
        b'UNRELATED=unchanged\r\n'
    )
    private_config.write_bytes(original)
    database = private_config.with_name('bidpilot_local.db')
    database.write_bytes(b'synthetic-customer-database-not-opened')
    before_values = values(private_config)
    configure.main(['enable'])
    expected = original.replace(b'"false" # operator', b"'true' # operator").replace(
        b'=false # duplicated', b"='true' # duplicated")
    assert private_config.read_bytes() == expected
    assert values(private_config) == {**before_values, configure.KEY: 'true'}
    backups = list(private_config.parent.glob('.env.autobid-backup-*'))
    assert len(backups) == 1 and backups[0].read_bytes() == original
    assert database.read_bytes() == b'synthetic-customer-database-not-opened'
    output = capsys.readouterr().out
    assert 'configuration: ON' in output and 'Restart BOTH web and worker' in output
    assert 'enable Auto-Pilot in Settings, then Save' in output
    assert 'synthetic-private-secret' not in output and 'synthetic-encryption-key' not in output
    assert not list(private_config.parent.glob('.env.autobid-write-*'))
    if os.name == 'posix':
        assert stat.S_IMODE(private_config.stat().st_mode) == 0o600
        assert stat.S_IMODE(backups[0].stat().st_mode) == 0o600


@pytest.mark.parametrize('original', [
    "PLATFORM_AUTOBID_APPROVED='true'\n",
    "export PLATFORM_AUTOBID_APPROVED=true # enabled\n",
    "'PLATFORM_AUTOBID_APPROVED'=true\n",
    "PLATFORM_AUTOBID_APPROVED='t\nrue' # multiline value\n",
])
def test_disable_updates_supported_assignment_forms_only(private_config, original):
    private_config.write_text('KEEP=unchanged\n' + original)
    configure.main(['disable'])
    assert values(private_config)[configure.KEY] == 'false'
    assert private_config.read_text().startswith('KEEP=unchanged\n')
    if '# ' in original:
        assert original[original.index('# '):] in private_config.read_text()


@pytest.mark.parametrize('original', [
    '', '# existing comment', 'KEEP=raw', 'KEEP=raw\r\n',
    'PLATFORM_AUTOBID_APPROVED\n', 'PLATFORM_AUTOBID_APPROVED # keep comment\n',
])
def test_missing_or_unassigned_flag_is_explicitly_added(private_config, original):
    private_config.write_bytes(original.encode())
    configure.main(['enable'])
    assert values(private_config)[configure.KEY] == 'true'
    if not original.startswith(configure.KEY):
        assert private_config.read_bytes().startswith(original.encode())
    if '# keep comment' in original:
        assert '# keep comment' in private_config.read_text()


def test_status_is_read_only_and_distinguishes_saved_from_running_configuration(private_config, capsys):
    original = b"PRIVATE='never-print-this'\nPLATFORM_AUTOBID_APPROVED='false'\n"
    private_config.write_bytes(original)
    before = private_config.stat()
    configure.main(['status'])
    assert private_config.read_bytes() == original
    assert private_config.stat().st_mtime_ns == before.st_mtime_ns
    assert list(private_config.parent.iterdir()) == [private_config]
    output = capsys.readouterr().out
    assert 'configuration: OFF' in output
    assert 'running web/worker processes were not inspected' in output
    assert 'never-print-this' not in output


@pytest.mark.parametrize('action', ['enable', 'disable', 'status'])
def test_missing_configuration_is_not_created(private_config, action, capsys):
    with pytest.raises(SystemExit) as error:
        configure.main([action])
    assert error.value.code == 1
    assert not private_config.exists()
    assert not list(private_config.parent.iterdir())
    assert 'setup_local.py first' in capsys.readouterr().err


@pytest.mark.skipif(os.name != 'posix', reason='Unix symlink protection')
@pytest.mark.parametrize('action', ['enable', 'disable', 'status'])
def test_symlink_configuration_is_refused(private_config, action):
    target = private_config.with_name('other-private-file')
    target.write_text("PLATFORM_AUTOBID_APPROVED='false'\n")
    private_config.symlink_to(target)
    with pytest.raises(SystemExit) as error:
        configure.main([action])
    assert error.value.code == 1
    assert private_config.is_symlink()
    assert target.read_text() == "PLATFORM_AUTOBID_APPROVED='false'\n"
    assert not list(private_config.parent.glob('.env.autobid-*'))


def test_default_invocation_does_not_implicitly_enable_or_write(private_config):
    original = b"PLATFORM_AUTOBID_APPROVED='false'\n"
    private_config.write_bytes(original)
    with pytest.raises(SystemExit) as error:
        configure.main([])
    assert error.value.code == 2
    assert private_config.read_bytes() == original
    assert list(private_config.parent.iterdir()) == [private_config]


def test_inherited_flag_override_is_reported_without_echoing_value(private_config, monkeypatch, capsys):
    private_config.write_text("PLATFORM_AUTOBID_APPROVED='false'\n")
    monkeypatch.setenv(configure.KEY, 'private-invalid-override')
    configure.main(['enable'])
    assert values(private_config)[configure.KEY] == 'true'
    assert os.environ[configure.KEY] == 'private-invalid-override'
    output = capsys.readouterr().out
    assert 'overrides this .env setting' in output
    assert 'Update or unset that flag' in output
    assert 'private-invalid-override' not in output


def test_repeated_enable_does_not_accumulate_backups(private_config):
    private_config.write_text("PLATFORM_AUTOBID_APPROVED='false'\n")
    configure.main(['enable'])
    original = private_config.read_bytes()
    configure.main(['enable'])
    assert private_config.read_bytes() == original
    assert len(list(private_config.parent.glob('.env.autobid-backup-*'))) == 1


def test_parse_failure_does_not_echo_secret_or_touch_configuration(private_config, capsys):
    original = b"PRIVATE='synthetic-do-not-echo\n"
    private_config.write_bytes(original)
    with pytest.raises(SystemExit) as error:
        configure.main(['enable'])
    assert error.value.code == 1
    assert private_config.read_bytes() == original
    assert not list(private_config.parent.glob('.env.autobid-*'))
    assert 'synthetic-do-not-echo' not in capsys.readouterr().err


def test_failed_atomic_replacement_leaves_original_and_private_backup(private_config, monkeypatch):
    original = b"PRIVATE='synthetic-preserved'\nPLATFORM_AUTOBID_APPROVED='false'\n"
    private_config.write_bytes(original)
    def denied(*args):
        raise PermissionError('arbitrary-private-error-detail')
    monkeypatch.setattr(configure.os, 'replace', denied)
    with pytest.raises(SystemExit) as error:
        configure.main(['enable'])
    assert error.value.code == 1
    assert private_config.read_bytes() == original
    assert [p.read_bytes() for p in private_config.parent.glob('.env.autobid-backup-*')] == [original]
    assert not list(private_config.parent.glob('.env.autobid-write-*'))


@pytest.mark.parametrize('action', ['enable', 'disable', 'status'])
def test_bom_is_refused_instead_of_reporting_a_flag_the_app_would_not_read(private_config, action, capsys):
    original = b"\xef\xbb\xbfPLATFORM_AUTOBID_APPROVED='true'\n"
    private_config.write_bytes(original)
    with pytest.raises(SystemExit) as error:
        configure.main([action])
    assert error.value.code == 1
    assert 'UTF-8 BOM' in capsys.readouterr().err
    assert private_config.read_bytes() == original
    assert not list(private_config.parent.glob('.env.autobid-*'))


def test_concurrent_private_edit_is_not_overwritten(private_config, monkeypatch):
    original = b"PLATFORM_AUTOBID_APPROVED='false'\n"
    changed = original + b"PRIVATE='new-private-edit'\n"
    private_config.write_bytes(original)
    write = configure._private_write
    def externally_edited(path, prefix, content):
        result = write(path, prefix, content)
        if prefix == '.env.autobid-write-':
            path.write_bytes(changed)
        return result
    monkeypatch.setattr(configure, '_private_write', externally_edited)
    with pytest.raises(SystemExit) as error:
        configure.main(['enable'])
    assert error.value.code == 1
    assert private_config.read_bytes() == changed
    assert [p.read_bytes() for p in private_config.parent.glob('.env.autobid-backup-*')] == [original]
    assert not list(private_config.parent.glob('.env.autobid-write-*'))
