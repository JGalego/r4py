import hashlib
import ssl

import pytest

import build_r_runtime as builder


def test_corrupt_source_archive_is_rejected(tmp_path, monkeypatch):
    archive = tmp_path / "R.tar.gz"
    archive.write_bytes(b"not the R source")
    monkeypatch.setattr(
        builder, "SOURCE_SHA256", hashlib.sha256(b"R source").hexdigest()
    )
    with pytest.raises(ValueError, match="SHA-256 mismatch"):
        builder._verify_archive(archive)


def test_relocation_keeps_r_options_and_the_native_rscript(tmp_path):
    (tmp_path / "bin/exec").mkdir(parents=True)
    (tmp_path / "etc").mkdir()
    (tmp_path / "bin/config").write_text(
        'makefiles="-f ${R_HOME}/etc${R_ARCH}/Makeconf '
        '-f ${R_SHARE_DIR}/make/config.mk"\n'
        'query="${MAKE} -s ${makefiles} print R_HOME=${R_HOME}"\n'
    )
    (tmp_path / "etc/Makeconf").write_text("include $(R_SHARE_DIR)/make/vars.mk\n")
    r = tmp_path / "bin/R"
    r.write_text(
        '#!/bin/sh\nR_HOME_DIR="/old/R"\n'
        'echo "old relocation logic"\nR_HOME="${R_HOME_DIR}"\n'
        'R_SHARE_DIR="/old/R/share"\nR_INCLUDE_DIR="/old/R/include"\n'
        'R_DOC_DIR="/old/R/doc"\n'
        'exec "${R_HOME}/bin/exec/R" "$@"\n'
    )
    (tmp_path / "bin/Rscript").write_bytes(b"native Rscript")
    builder._relocate(tmp_path)
    builder._relocate(tmp_path)
    assert "/old/R" not in r.read_text()
    assert 'R_SHARE_DIR="${R_HOME_DIR}/share"' in r.read_text()
    assert 'R_INCLUDE_DIR="${R_HOME_DIR}/include"' in r.read_text()
    assert 'R_DOC_DIR="${R_HOME_DIR}/doc"' in r.read_text()
    assert 'exec "${R_HOME}/bin/exec/R" "$@"' in r.read_text()
    assert (tmp_path / "bin/Rscript.bin").read_bytes() == b"native Rscript"
    assert (
        'exec "${RHOME}/bin/Rscript.bin" "$@"' in (tmp_path / "bin/Rscript").read_text()
    )


def test_invalid_build_arguments_do_not_start_a_build(monkeypatch, tmp_path):
    monkeypatch.setattr(builder, "RUNTIME", tmp_path / "r-runtime")
    for arguments in (
        ["--jobs", "0"],
        ["--runtime", "/R", "--source-archive", "/R.tar.gz"],
    ):
        monkeypatch.setattr("sys.argv", ["build_r_runtime.py", *arguments])
        with pytest.raises(SystemExit) as result:
            builder.main()
        assert result.value.code == 2


def test_certificates_survive_repeated_runtime_preparation(tmp_path, monkeypatch):
    (tmp_path / "etc").mkdir()
    source = tmp_path / "system-certificates.pem"
    source.write_bytes(b"system certificates")
    paths = ssl.get_default_verify_paths()._replace(cafile=str(source))
    monkeypatch.setattr(builder.ssl, "get_default_verify_paths", lambda: paths)
    builder._bundle_certificates(tmp_path)
    source.unlink()
    builder._bundle_certificates(tmp_path)
    assert (tmp_path / "etc/curl-ca-bundle.crt").read_bytes() == b"system certificates"


def test_existing_runtime_is_never_overwritten(monkeypatch, tmp_path):
    runtime = tmp_path / "r-runtime"
    runtime.mkdir()
    sentinel = runtime / "keep"
    sentinel.write_text("existing R")
    monkeypatch.setattr(builder, "RUNTIME", runtime)
    monkeypatch.setattr("sys.argv", ["build_r_runtime.py"])
    with pytest.raises(SystemExit) as result:
        builder.main()
    assert result.value.code == 2
    assert sentinel.read_text() == "existing R"
