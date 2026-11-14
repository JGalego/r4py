import os

from r4py import R_HOME, get_env


def test_environment_selects_bundled_r_without_mutating_parent(monkeypatch):
    monkeypatch.setenv("R_HOME", "/other/R")
    monkeypatch.setenv("RHOME", "/other/R")
    monkeypatch.setenv("R_ARCH", "/other-architecture")
    monkeypatch.setenv("R_LD_LIBRARY_PATH", "/other/libraries")
    before = dict(os.environ)

    env = get_env({"R_HOME": "/override/R", "R_LIBS_USER": "/my/packages"})

    assert env["R_HOME"] == env["RHOME"] == str(R_HOME)
    assert env["R_SHARE_DIR"] == str(R_HOME / "share")
    assert env["R_INCLUDE_DIR"] == str(R_HOME / "include")
    assert env["R_DOC_DIR"] == str(R_HOME / "doc")
    assert env["R_LIBS_USER"] == "/my/packages"
    assert "R_ARCH" not in env
    assert "R_LD_LIBRARY_PATH" not in env
    assert dict(os.environ) == before
