"""Run only on synthetic SQLite to prove the reviewed DDL is additive."""
from contextlib import closing
from pathlib import Path
import sqlite3
import tempfile
import unittest
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"backend"))
import database


class CalendarMigrationTests(unittest.TestCase):
    def test_only_two_new_tables_and_index_existing_schema_and_values_unchanged(self):
        root=Path(__file__).resolve().parent
        with tempfile.TemporaryDirectory(prefix='quiz-test-',dir=root) as folder:
            with closing(sqlite3.connect(Path(folder)/'migration.db')) as db:
                db.executescript(database.SCHEMA.replace(database.CALENDAR_UX_SCHEMA,''))
                db.execute('INSERT INTO users (telegram_user_id,preferences_json) VALUES (?,?)',(101,'{"notes":"synthetic preserved"}'))
                db.execute("INSERT INTO google_connections (telegram_user_id,status,encrypted_credentials) VALUES (101,'connected',?)",(b'synthetic opaque blob',))
                db.commit()
                schema=dict(db.execute("SELECT name,sql FROM sqlite_master WHERE type='table'"))
                values={name:db.execute('SELECT * FROM "'+name+'"').fetchall() for name in schema}
                db.executescript('BEGIN IMMEDIATE;\n'+database.CALENDAR_UX_SCHEMA+'\nCOMMIT;')
                after=dict(db.execute("SELECT name,sql FROM sqlite_master WHERE type='table'"))
                self.assertEqual(set(after)-set(schema),{'calendar_reviews','calendar_session_changes'})
                for name,ddl in schema.items():
                    self.assertEqual(after[name],ddl)
                    self.assertEqual(db.execute('SELECT * FROM "'+name+'"').fetchall(),values[name])
                self.assertEqual(db.execute('PRAGMA integrity_check').fetchone()[0],'ok')
                self.assertEqual(db.execute('PRAGMA foreign_key_check').fetchall(),[])
                self.assertIsNotNone(db.execute("SELECT 1 FROM sqlite_master WHERE type='index' AND name='calendar_one_active_change'").fetchone())
                db.executescript(database.CALENDAR_UX_SCHEMA)
                self.assertEqual(db.execute('PRAGMA integrity_check').fetchone()[0],'ok')


if __name__=='__main__':unittest.main()
