"""Shared fixtures for fleet unit tests."""
import os
import sys
import pytest

# Put fleet/ on sys.path so `import db` works
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'fleet'))


@pytest.fixture
def tmp_db(tmp_path):
    """Provide a fresh fleet DB at a temp file path. Returns the path as str.

    Sets FLEET_TEST_DB env var so db.get_conn() uses this path instead of
    the real fleet.db. Tears down cleanly after each test.
    """
    db_file = str(tmp_path / "test_fleet.db")
    # Save and override env var
    old = os.environ.get("FLEET_TEST_DB")
    os.environ["FLEET_TEST_DB"] = db_file

    import db as dbmod
    dbmod.init_db(db_file)

    yield db_file

    # Teardown: close thread-local connection, restore env
    try:
        dbmod.close_all()
    except Exception:
        pass
    if old is None:
        os.environ.pop("FLEET_TEST_DB", None)
    else:
        os.environ["FLEET_TEST_DB"] = old
