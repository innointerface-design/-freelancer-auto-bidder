"""Setup must stop before getpass falls back to visible password entry."""
import importlib.util
from pathlib import Path
import warnings

import pytest


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('bidpilot_audit_setup', ROOT / 'setup_local.py')
setup = importlib.util.module_from_spec(spec)
spec.loader.exec_module(setup)


@pytest.mark.parametrize('failure', ['warning', 'eof', 'interrupt'])
@pytest.mark.parametrize('fail_at', [1, 2])
def test_setup_secure_input_failure_never_echoes_or_writes_configuration(
        tmp_path, monkeypatch, capsys, failure, fail_at):
    secret = 'Synthetic-private-password-123!'
    monkeypatch.setattr(setup, '__file__', str(tmp_path / 'setup_local.py'))
    monkeypatch.setattr('sys.argv', ['setup_local.py'])
    monkeypatch.setattr('builtins.input', lambda *args: 'owner@example.test')
    calls = 0

    def unavailable(*args):
        nonlocal calls
        calls += 1
        if calls != fail_at:
            return secret
        if failure == 'warning':
            warnings.warn('Password may be echoed', setup.getpass.GetPassWarning)
            pytest.fail('Warning must prevent echoed stdin fallback')
        if failure == 'eof':
            raise EOFError
        raise KeyboardInterrupt

    monkeypatch.setattr(setup.getpass, 'getpass', unavailable)
    with pytest.raises(SystemExit, match='Secure password entry'):
        setup.main()
    assert calls == fail_at
    assert not list(tmp_path.iterdir())
    output = capsys.readouterr()
    assert secret not in output.out + output.err
