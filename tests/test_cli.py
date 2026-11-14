import pytest

from r4py import RSCRIPT, R, get_env
from r4py import __main__ as cli


@pytest.mark.parametrize("entry, executable", [(cli.main, RSCRIPT), (cli.r_main, R)])
def test_cli_passes_arguments_without_parsing(entry, executable, monkeypatch):
    monkeypatch.setattr(cli.Path, "is_file", lambda self: True)
    monkeypatch.setattr(cli.sys, "argv", ["r4py", "-e", 'cat("a b")', "--args", "-x"])
    monkeypatch.setattr(cli.os, "name", "posix")
    calls = []

    def execve(path, args, env):
        calls.append((path, args, env))
        raise SystemExit(7)

    monkeypatch.setattr(cli.os, "execve", execve)
    with pytest.raises(SystemExit) as result:
        entry()
    assert result.value.code == 7
    assert calls == [
        (executable, [str(executable), "-e", 'cat("a b")', "--args", "-x"], get_env())
    ]


def test_missing_runtime_is_an_error(monkeypatch, capsys):
    monkeypatch.setattr(cli.Path, "is_file", lambda self: False)
    assert cli.main() == 1
    assert "bundled R is missing" in capsys.readouterr().err


def test_windows_preserves_exit_status(monkeypatch):
    monkeypatch.setattr(cli.Path, "is_file", lambda self: True)
    monkeypatch.setattr(cli.os, "name", "nt")
    monkeypatch.setattr(cli.subprocess, "call", lambda *args, **kwargs: 9)
    assert cli.main() == 9
