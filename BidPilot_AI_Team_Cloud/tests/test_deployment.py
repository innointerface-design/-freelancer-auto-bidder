"""Configuration generation must preserve private values and never overwrite a setup."""
import importlib.util
import base64
import os
from pathlib import Path
import stat
import tempfile
from unittest import mock

from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('bidpilot_setup', ROOT / 'setup_local.py')
setup = importlib.util.module_from_spec(spec)
spec.loader.exec_module(setup)


def test_private_setup_preserves_password_and_existing_file():
    password = "a test ${VALUE} ' quote \\ end 42"
    with tempfile.TemporaryDirectory() as folder:
        with mock.patch.object(setup, '__file__', str(Path(folder) / 'setup_local.py')), \
             mock.patch('sys.argv', ['setup_local.py']), \
             mock.patch('builtins.input', side_effect=['owner@example.org', '']), \
             mock.patch('getpass.getpass', side_effect=[password, password]):
            setup.main()
            path = Path(folder) / '.env'
            values = dotenv_values(path, interpolate=False)
            assert base64.b64decode(values['ADMIN_PASSWORD_B64']).decode('utf-8') == password
            assert values['PLATFORM_AUTOBID_APPROVED'] == 'false'
            assert values['GEMINI_API_KEY'] == ''
            assert values['OPENAI_API_KEY'] == ''
            if os.name == 'posix':
                assert stat.S_IMODE(path.stat().st_mode) == 0o600
            original = path.read_bytes()
            setup.main()
            assert path.read_bytes() == original


def test_production_setup_requires_valid_domain_before_creating_file():
    with tempfile.TemporaryDirectory() as folder:
        with mock.patch.object(setup, '__file__', str(Path(folder) / 'setup_local.py')), \
             mock.patch('sys.argv', ['setup_local.py', '--production']), \
             mock.patch('builtins.input', side_effect=['owner@example.org', '', 'https://bad/path']), \
             mock.patch('getpass.getpass', side_effect=['a long safe password', 'a long safe password']):
            try:
                setup.main()
            except SystemExit:
                pass
            else:
                raise AssertionError('Invalid domain accepted')
            assert not (Path(folder) / '.env').exists()


def test_setup_rejects_email_longer_than_database_limit():
    oversized = 'x' * 235 + '@example.org'
    with mock.patch('builtins.input', side_effect=[oversized, 'owner@example.org']):
        assert setup.ask_email('Owner email: ') == 'owner@example.org'


def test_backup_keeps_distinct_snapshots_and_cleans_failed_dump(tmp_path):
    if os.name != 'posix':
        import pytest
        pytest.skip('Ubuntu shell backup utility')
    import subprocess
    binaries = tmp_path / 'bin'
    binaries.mkdir()
    docker = binaries / 'docker'
    docker.write_text('#!/bin/sh\nprintf "synthetic backup payload\\n"\nexit "${BIDPILOT_TEST_DUMP_FAIL:-0}"\n')
    docker.chmod(0o755)
    date = binaries / 'date'
    date.write_text('#!/bin/sh\nprintf "20261029T100000Z\\n"\n')
    date.chmod(0o755)
    backup_dir = tmp_path / 'backups'
    env = {**os.environ, 'PATH': str(binaries) + os.pathsep + os.environ.get('PATH', '')}
    command = ['bash', str(ROOT / 'deploy' / 'backup.sh'), str(backup_dir)]
    for _ in range(2):
        subprocess.run(command, env=env, check=True, capture_output=True, text=True)
    snapshots = list(backup_dir.glob('*.dump'))
    assert len(snapshots) == 2
    assert all(p.read_text() == 'synthetic backup payload\n' for p in snapshots)
    assert all(stat.S_IMODE(p.stat().st_mode) == 0o600 for p in snapshots)
    failed = subprocess.run(command, env={**env, 'BIDPILOT_TEST_DUMP_FAIL': '1'}, capture_output=True, text=True)
    assert failed.returncode != 0
    assert set(backup_dir.iterdir()) == set(snapshots)
