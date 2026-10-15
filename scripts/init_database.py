"""Initialize ONLY a new clone database. Existing data is never migrated/reset here."""
from contextlib import closing
from pathlib import Path
import sys
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
import database


def initialize_new():
    path = database.DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        path.touch(exist_ok=False)
    except FileExistsError:
        raise RuntimeError("Database already exists. Stop: use a reviewed backed-up migration, never reset it.") from None
    database.initialize_database(backfill_approvals=False, include_lms=True)
    with closing(database.get_connection()) as db:
        if db.execute("PRAGMA integrity_check").fetchone()[0] != "ok" or db.execute("PRAGMA foreign_key_check").fetchall():
            raise RuntimeError("New database integrity verification failed.")
    print("PASS: new database initialized; no credentials, accounts or private course content imported.")


if __name__ == "__main__":
    try:
        initialize_new()
    except RuntimeError as error:
        print(str(error))
        raise SystemExit(1)
