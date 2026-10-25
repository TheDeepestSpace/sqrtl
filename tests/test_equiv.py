"""Every example under examples/: Postgres trace == expected.csv, and
`sqrtl check` (Postgres vs. Icarus) passes."""

import io
import shutil

import pytest

from sqrtl.check import check
from sqrtl.cli import main
from sqrtl.parse import parse_design
from sqrtl.sim import normalize_csv, run_sim

from conftest import EXAMPLE_DIRS, EXAMPLE_IDS

needs_iverilog = pytest.mark.skipif(shutil.which("iverilog") is None, reason="iverilog not installed")


def _load(example):
    design_sql = (example / "design.sql").read_text()
    return parse_design(design_sql), design_sql, (example / "stim.sql").read_text()


@pytest.mark.parametrize("example", EXAMPLE_DIRS, ids=EXAMPLE_IDS)
def test_sim_matches_expected(dsn, example):
    design, design_sql, stim_sql = _load(example)
    trace, stim = run_sim(design, design_sql, stim_sql, dsn)
    assert trace == normalize_csv((example / "expected.csv").read_text())
    assert stim.splitlines()[0] == ",".join(["cycle"] + [s.name for s in design.inputs])


@needs_iverilog
@pytest.mark.parametrize("example", EXAMPLE_DIRS, ids=EXAMPLE_IDS)
def test_check(dsn, example):
    design, design_sql, stim_sql = _load(example)
    out = io.StringIO()
    rc = check(design, design_sql, stim_sql, (example / "expected.csv").read_text(), dsn, out=out)
    assert rc == 0, out.getvalue()
    assert out.getvalue().startswith("ok:")


@needs_iverilog
def test_check_cli_reports_mismatch(dsn, tmp_path, capsys):
    src = EXAMPLE_DIRS[EXAMPLE_IDS.index("incrementer")]
    ex = tmp_path / "incrementer"
    shutil.copytree(src, ex)
    (ex / "expected.csv").write_text((ex / "expected.csv").read_text().replace("2,1,6", "2,1,7"))
    assert main(["check", str(ex), "--dsn", dsn]) == 1
    out = capsys.readouterr().out
    assert "-2,1,7\n+2,1,6\n" in out


def test_stimulus_gap_is_rejected(dsn, tmp_path):
    src = EXAMPLE_DIRS[EXAMPLE_IDS.index("incrementer")]
    design, design_sql, stim_sql = _load(src)
    from sqrtl.parse import SqrtlError
    with pytest.raises(SqrtlError, match="stimulus has gap at cycle 3"):
        run_sim(design, design_sql, stim_sql.replace("(3, false, B'00000000'),\n", ""), dsn)


def test_cli_diagnostic_format(tmp_path, capsys):
    ex = tmp_path / "broken"
    ex.mkdir()
    (ex / "design.sql").write_text("CREATE TABLE broken_in (cycle integer PRIMARY KEY);\n")
    assert main(["transpile", str(ex)]) == 2
    assert capsys.readouterr().err == f"{ex / 'design.sql'}:1:14: expected CREATE TABLE then CREATE VIEW\n"
