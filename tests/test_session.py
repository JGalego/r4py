import math
import os
import sys
import warnings

import pytest

from r4py import RSCRIPT, RError, RSession, RWarning
from r4py.session import to_r


@pytest.mark.parametrize(
    ("value", "literal"),
    [
        (None, "NULL"),
        (True, "TRUE"),
        (3, "3L"),
        (2**40, "1099511627776.0"),
        (1.5, "1.5"),
        (float("inf"), "Inf"),
        (float("-inf"), "-Inf"),
        (float("nan"), "NaN"),
        ('a"b\\c\n', '"a\\"b\\\\c\\n"'),
        ("é", '"é"'),
        ([1, 2, 3], "c(1L, 2L, 3L)"),
        ([1, 2.5], "c(1.0, 2.5)"),
        ([1, None], "c(1L, NA)"),
        ([True, False], "c(TRUE, FALSE)"),
        (["a", None], 'c("a", NA)'),
        ([1, "a"], 'list(1L, "a")'),
        ([], "list()"),
        ([None], "list(NULL)"),
        ({"a": 1, "b c": [1.5]}, 'list("a" = 1L, "b c" = c(1.5))'),
        ((1, 2), "c(1L, 2L)"),
    ],
)
def test_to_r(value: object, literal: str) -> None:
    assert to_r(value) == literal


def test_to_r_rejects_unsupported_values() -> None:
    with pytest.raises(TypeError):
        to_r(object())
    with pytest.raises(ValueError):
        to_r("a\x00b")


def require_runtime() -> None:
    if not RSCRIPT.is_file():
        if os.environ.get("R4PY_REQUIRE_RUNTIME") == "1":
            pytest.fail("Runtime tests were required, but bundled R is missing.")
        pytest.skip("Build the R runtime first.")


@pytest.fixture(scope="module")
def r() -> RSession:
    require_runtime()
    with RSession() as session:
        yield session  # type: ignore[misc]


@pytest.mark.runtime
def test_scalars_and_vectors(r: RSession) -> None:
    assert r.eval("mean(c(1, 2, 3, 4))") == 2.5
    assert r.eval("1:3") == [1, 2, 3]
    assert r.eval("c(2, 3)[1]") == 2.0
    assert isinstance(r.eval("c(2, 3)[1]"), float)
    assert r.eval("TRUE") is True
    assert r.eval("NULL") is None
    assert r.eval("character(0)") == []
    assert r.eval("c(1, NA)") == [1.0, None]
    assert r.eval("NA_character_") is None
    assert r.eval("c(a = 1, b = 2)") == {"a": 1.0, "b": 2.0}
    assert r.eval("list(x = 1L, y = list('a', 2L))") == {"x": 1, "y": ["a", 2]}
    assert r.eval("matrix(1:4, 2)") == [[1, 3], [2, 4]]
    assert r.eval("factor(c('u', 'v'))") == ["u", "v"]
    assert r.eval("as.Date('2024-02-29')") == "2024-02-29"


@pytest.mark.runtime
def test_special_floats(r: RSession) -> None:
    nan = r.eval("NaN")
    assert isinstance(nan, float) and math.isnan(nan)
    assert r.eval("Inf") == math.inf
    assert r.eval("-Inf") == -math.inf
    assert r.eval("0.1 + 0.2") == 0.1 + 0.2
    assert r.eval("1e300") == 1e300


@pytest.mark.runtime
def test_unicode_and_escapes_round_trip(r: RSession) -> None:
    for text in [
        "héllo wörld",
        "日本語",
        'quote " back \\ slash',
        "tab\tnew\nline",
        "\x01\x1f",
        "😀",
    ]:
        r.assign("s", text)
        assert r.eval("s") == text
        assert r.eval("nchar(s, type = 'chars')") == len(text)


@pytest.mark.runtime
def test_assign_and_call(r: RSession) -> None:
    r.assign("x", [1, 2, 3, None])
    assert r.eval("sum(x, na.rm = TRUE)") == 6
    r.assign("big", 2**40)
    assert r.eval("big") == 2**40
    assert r.call("stats::sd", [1, 2, 3, 4]) == pytest.approx(1.2909944487358056)
    assert r.call("paste", "a", "b", sep="-") == "a-b"
    assert r.call("rev", {"a": 1, "b": 2}) == {"b": 2, "a": 1}


@pytest.mark.runtime
def test_state_persists_and_restart_clears_it() -> None:
    require_runtime()
    with RSession() as r:
        r.eval("counter <- 41")
        assert r.eval("counter + 1") == 42
        r.restart()
        assert r.alive
        with pytest.raises(RError, match="counter"):
            r.eval("counter")


@pytest.mark.runtime
def test_data_frame_without_pandas(
    r: RSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setitem(sys.modules, "pandas", None)
    frame = r.eval("data.frame(a = 1:2, b = c('x', NA), stringsAsFactors = TRUE)")
    assert frame == {"a": [1, 2], "b": ["x", None]}
    assert r.eval("data.frame(a = 1)") == {"a": [1.0]}
    assert r.eval("data.frame()") == {}


@pytest.mark.runtime
def test_data_frame_with_pandas(r: RSession) -> None:
    pandas = pytest.importorskip("pandas")
    frame = r.eval("head(mtcars, 3)")
    assert isinstance(frame, pandas.DataFrame)
    assert frame.shape == (3, 11)
    assert list(frame["mpg"]) == [21.0, 21.0, 22.8]
    r.assign(
        "df", pandas.DataFrame({"a": [1, 2], "b": ["x", "y"], "c": [0.5, float("nan")]})
    )
    assert r.eval("nrow(df)") == 2
    assert r.eval("sapply(df, class)") == {
        "a": "integer",
        "b": "character",
        "c": "numeric",
    }
    assert r.eval("is.na(df$c)") == [False, True]


@pytest.mark.runtime
def test_output_messages_and_warnings(
    r: RSession, capsys: pytest.CaptureFixture[str]
) -> None:
    assert r.eval('print("shown"); cat("raw\\n"); 7') == 7
    assert r.eval('print("hidden")', echo=False) == "hidden"
    out = capsys.readouterr()
    assert "shown" in out.out and "raw" in out.out and "hidden" not in out.out
    r.eval('message("note")')
    assert "note" in capsys.readouterr().err
    with pytest.warns(RWarning, match="careful"):
        assert r.eval('warning("careful"); 1') == 1


@pytest.mark.runtime
def test_errors_leave_the_session_usable(r: RSession) -> None:
    with pytest.raises(RError, match="boom"):
        r.eval("stop('boom')")
    with pytest.raises(RError):
        r.eval("this is not R")
    with pytest.raises(RError, match="cannot convert"):
        r.eval("function(x) x")
    with pytest.raises(RError, match="cannot convert"):
        r.eval("as.raw(1)")
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        assert r.eval("1 + 1") == 2


@pytest.mark.runtime
def test_crash_raises_and_session_can_restart() -> None:
    require_runtime()
    r = RSession()
    with pytest.raises(RError, match="exited"):
        r.eval("quit(save = 'no', status = 3)")
    assert not r.alive
    with pytest.raises(RError, match="closed"):
        r.eval("1")
    r.restart()
    assert r.eval("1 + 1") == 2
    r.close()
    r.close()
    assert not r.alive
