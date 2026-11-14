import os
import subprocess
import sys
from collections.abc import Mapping
from pathlib import Path

import pytest

from r4py import R_HOME, R_VERSION, RSCRIPT, R, get_env

pytestmark = pytest.mark.runtime


@pytest.fixture(autouse=True)
def require_runtime() -> None:
    if not RSCRIPT.is_file():
        if os.environ.get("R4PY_REQUIRE_RUNTIME") == "1":
            pytest.fail("Runtime tests were required, but bundled R is missing.")
        pytest.skip("Build the R runtime first.")


def rscript(
    *args: str,
    env: Mapping[str, str] | None = None,
    cwd: Path | None = None,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [RSCRIPT, "--vanilla", *args],
        env=env,
        cwd=cwd,
        capture_output=True,
        text=True,
        check=True,
        timeout=60,
    )


def test_native_paths_work_without_setting_an_environment(tmp_path: Path) -> None:
    env = dict(os.environ)
    for key in ("R_HOME", "RHOME", "R_ARCH", "R_LD_LIBRARY_PATH"):
        env.pop(key, None)
    result = rscript(
        "-e",
        "cat(as.character(getRversion())); cat('\\n'); cat(R.home())",
        env=env,
        cwd=tmp_path,
    )
    version, home = result.stdout.splitlines()
    assert version == ".".join(map(str, R_VERSION))
    assert Path(home).samefile(R_HOME)
    home = subprocess.check_output([R, "RHOME"], text=True, env=env).strip()
    assert Path(home).samefile(R_HOME)


def test_statistics_and_recommended_native_packages() -> None:
    result = rscript(
        "-e",
        "library(Matrix); library(MASS); library(survival); "
        "stopifnot(sum(diag(Matrix(diag(3)))) == 3); "
        "stopifnot(abs(coef(lm(mpg ~ wt, mtcars))[2] + 5.344472) < 1e-5); "
        "cat('ok')",
        env=get_env(),
    )
    assert result.stdout == "ok"


def test_https_package_downloads_work_without_a_system_ca_path(tmp_path: Path) -> None:
    env = dict(os.environ)
    env.pop("CURL_CA_BUNDLE", None)
    target = tmp_path / "PACKAGES.gz"
    rscript(
        "-e",
        "options(warn=2); download.file("
        "'https://cloud.r-project.org/src/contrib/PACKAGES.gz', "
        "commandArgs(TRUE)[1], method='libcurl', quiet=TRUE)",
        str(target),
        env=env,
    )
    assert target.read_bytes().startswith(b"\x1f\x8b")


def test_certificate_environment_override_is_preserved() -> None:
    result = rscript(
        "-e",
        "cat(Sys.getenv('CURL_CA_BUNDLE'))",
        env=get_env({"CURL_CA_BUNDLE": "/custom/certificates.pem"}),
    )
    assert result.stdout == "/custom/certificates.pem"


def test_scripts_stdin_and_argument_boundaries(tmp_path: Path) -> None:
    script = tmp_path / "script with spaces.R"
    script.write_text("cat(paste(commandArgs(TRUE), collapse='|'))")
    assert rscript(str(script), "a b", "-x", "quote'\"").stdout == "a b|-x|quote'\""
    result = subprocess.run(
        [RSCRIPT, "--vanilla", "-"],
        input="cat(6 * 7)\n",
        capture_output=True,
        text=True,
        check=True,
        timeout=60,
    )
    assert result.stdout == "42"


def test_graphics_and_r_cmd(tmp_path: Path) -> None:
    rscript(
        "-e",
        "options(warn=2); pdf('plot.pdf'); plot(1:3); dev.off(); "
        "png('plot.png', type='cairo'); plot(1:3); dev.off()",
        cwd=tmp_path,
    )
    assert (tmp_path / "plot.pdf").read_bytes().startswith(b"%PDF")
    assert (tmp_path / "plot.png").read_bytes().startswith(b"\x89PNG")
    args = [R, "--version"] if os.name == "nt" else [R, "CMD", "config", "CC"]
    result = subprocess.run(
        args,
        env=get_env(),
        capture_output=True,
        text=True,
        check=True,
        timeout=60,
    )
    if os.name == "nt":
        version = ".".join(map(str, R_VERSION))
        assert (result.stdout + result.stderr).startswith(f"R version {version}")
    else:
        assert result.stdout.strip()


def test_installing_and_loading_a_package_in_a_writable_library(tmp_path: Path) -> None:
    package = tmp_path / "tiny"
    (package / "R").mkdir(parents=True)
    (package / "DESCRIPTION").write_text(
        "Package: tiny\nVersion: 0.0.1\nTitle: A Test Package\n"
        "Description: Exercises a writable user library.\n"
        "Author: r4py contributors\nMaintainer: Tests <tests@example.invalid>\n"
        "License: MIT\n"
    )
    (package / "NAMESPACE").write_text("export(answer)\n")
    (package / "R/answer.R").write_text("answer <- function() 42\n")
    library = tmp_path / "user library"
    library.mkdir()
    result = rscript(
        "-e",
        "args <- commandArgs(TRUE); "
        "install.packages(args[1], lib=args[2], repos=NULL, type='source'); "
        "library(tiny, lib.loc=args[2]); stopifnot(answer() == 42); cat('ok')",
        str(package),
        str(library),
    )
    assert result.stdout.endswith("ok")
    assert (library / "tiny/DESCRIPTION").is_file()


def test_module_preserves_r_exit_status() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "r4py", "--vanilla", "-e", "quit(status=7)"],
        env=get_env({"PYTHONPATH": str(R_HOME.parent.parent)}),
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 7


def test_python_environment_overrides_conflicting_system_r() -> None:
    result = rscript(
        "-e",
        "cat(R.home())",
        env=get_env({"R_HOME": "/wrong", "RHOME": "/wrong", "R_ARCH": "/wrong"}),
    )
    assert Path(result.stdout).samefile(R_HOME)
