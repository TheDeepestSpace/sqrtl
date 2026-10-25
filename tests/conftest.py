import os
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
EXAMPLES = ROOT / "examples"
EXAMPLE_DIRS = sorted(p for p in EXAMPLES.iterdir() if (p / "design.sql").is_file())
EXAMPLE_IDS = [p.name for p in EXAMPLE_DIRS]


@pytest.fixture(scope="session")
def incrementer_sql() -> str:
    return (EXAMPLES / "incrementer" / "design.sql").read_text()


@pytest.fixture(scope="session")
def dsn() -> str:
    """The Postgres DSN for the equivalence tests.

    If SQRTL_DSN is set (CI always sets it) the database must be reachable and a
    failure is a test failure. Otherwise the default DSN is tried and the test
    is skipped when no server is listening.
    """
    import psycopg
    from sqrtl.sim import default_dsn

    url = default_dsn()
    try:
        psycopg.connect(url, connect_timeout=3).close()
    except psycopg.Error as e:
        if os.environ.get("SQRTL_DSN"):
            pytest.fail(f"SQRTL_DSN={url} is not reachable: {e}")
        pytest.skip(f"no PostgreSQL at {url} (set SQRTL_DSN to run the equivalence tests)")
    return url
