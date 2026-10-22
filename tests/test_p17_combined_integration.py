"""Combined P17 acceptance: Agent run reads and writes share the migrated main DB."""
import os
from unittest.mock import patch


def test_agent_ledger_and_stream_read_same_main_database(tmp_path):
    import paths
    import agent_runs
    from storage import db as store

    with patch.object(paths, "DATA_DIR", str(tmp_path)):
        store.close_all()
        agent_runs.INITIALIZED.clear()
        try:
            assert agent_runs._db_file(create=False) == store.main_db_path()
            assert not os.path.exists(store.main_db_path())
            with agent_runs._db() as conn:
                database_file = conn.execute("PRAGMA database_list").fetchone()[2]
                assert os.path.normcase(database_file) == os.path.normcase(store.main_db_path())
                conn.execute(
                    "INSERT INTO runs VALUES (?,?,?,?,?)",
                    ("p17-integration-run", "integration-owner", "request-1", "fingerprint", "{}"))
            with agent_runs._read_db() as conn:
                assert conn is not None
                assert conn.execute("SELECT id FROM runs WHERE id=?",
                                    ("p17-integration-run",)).fetchone()[0] == "p17-integration-run"
            assert not os.path.exists(tmp_path / "agent_runs.sqlite3")
        finally:
            agent_runs.INITIALIZED.clear()
            store.close_all()


def test_missing_db_read_path_does_not_create_database(tmp_path):
    import paths
    import agent_runs
    from storage import db as store
    with patch.object(paths, "DATA_DIR", str(tmp_path)):
        store.close_all()
        try:
            assert not os.path.exists(agent_runs._db_file(create=False))
            with agent_runs._read_db() as conn:
                assert conn is None
            assert not os.path.exists(store.main_db_path())
        finally:
            store.close_all()
