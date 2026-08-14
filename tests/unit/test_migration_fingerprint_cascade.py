"""Tests for the 2.88.2 audio_fingerprints cascade migration."""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))

from database import Database  # noqa: E402


@pytest.fixture
def db(tmp_path):
    Database._instance = None
    yield Database(str(tmp_path / 'test.db'))
    Database._instance = None


def _rebuild_pre_migration_shape(conn):
    """Recreate audio_fingerprints without the FK, as builds before 2.88.2 had it."""
    conn.execute("PRAGMA foreign_keys = OFF")
    conn.execute("DROP TABLE IF EXISTS audio_fingerprints")
    conn.execute("""
        CREATE TABLE audio_fingerprints (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            pattern_id INTEGER UNIQUE,
            fingerprint BLOB,
            duration REAL,
            created_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
        )
    """)
    conn.commit()
    conn.execute("PRAGMA foreign_keys = ON")


def _add_pattern(conn, template='buy now'):
    pattern_id = conn.execute(
        "INSERT INTO ad_patterns (scope, podcast_id, text_template) "
        "VALUES ('podcast', 'slug', ?)", (template,),
    ).lastrowid
    conn.commit()
    return pattern_id


def _add_fingerprint(conn, pattern_id, blob):
    conn.execute(
        "INSERT INTO audio_fingerprints (pattern_id, fingerprint, duration) "
        "VALUES (?, ?, 1.0)", (pattern_id, blob),
    )
    conn.commit()


def _has_fk(conn):
    return any(fk['table'] == 'ad_patterns' for fk in
               conn.execute("PRAGMA foreign_key_list(audio_fingerprints)").fetchall())


def test_migration_adds_the_foreign_key(db):
    conn = db.get_connection()
    _rebuild_pre_migration_shape(conn)
    assert not _has_fk(conn)

    db._migrate_fingerprint_cascade(conn)

    assert _has_fk(conn)


def test_live_fingerprints_survive_the_rebuild(db):
    conn = db.get_connection()
    _rebuild_pre_migration_shape(conn)
    pattern_id = _add_pattern(conn)
    _add_fingerprint(conn, pattern_id, b'keep-me')

    db._migrate_fingerprint_cascade(conn)

    rows = conn.execute(
        "SELECT pattern_id, fingerprint, duration FROM audio_fingerprints").fetchall()
    assert len(rows) == 1
    assert rows[0]['pattern_id'] == pattern_id
    assert rows[0]['fingerprint'] == b'keep-me'
    assert rows[0]['duration'] == 1.0


def test_orphans_are_archived_not_dropped(db):
    conn = db.get_connection()
    _rebuild_pre_migration_shape(conn)
    pattern_id = _add_pattern(conn)
    _add_fingerprint(conn, pattern_id, b'orphan-me')
    conn.execute("DELETE FROM ad_patterns WHERE id = ?", (pattern_id,))
    conn.commit()

    db._migrate_fingerprint_cascade(conn)

    assert conn.execute("SELECT COUNT(*) FROM audio_fingerprints").fetchone()[0] == 0
    archived = conn.execute(
        "SELECT pattern_id, fingerprint FROM _orphaned_audio_fingerprints").fetchall()
    assert len(archived) == 1
    assert archived[0]['pattern_id'] == pattern_id
    assert archived[0]['fingerprint'] == b'orphan-me'


def test_deleting_a_pattern_cascades_after_migration(db):
    conn = db.get_connection()
    _rebuild_pre_migration_shape(conn)
    pattern_id = _add_pattern(conn)
    _add_fingerprint(conn, pattern_id, b'cascade-me')

    db._migrate_fingerprint_cascade(conn)
    conn.execute("DELETE FROM ad_patterns WHERE id = ?", (pattern_id,))
    conn.commit()

    assert conn.execute("SELECT COUNT(*) FROM audio_fingerprints").fetchone()[0] == 0


def test_migration_is_idempotent(db):
    conn = db.get_connection()
    _rebuild_pre_migration_shape(conn)
    pattern_id = _add_pattern(conn)
    _add_fingerprint(conn, pattern_id, b'keep-me')

    db._migrate_fingerprint_cascade(conn)
    db._migrate_fingerprint_cascade(conn)

    assert _has_fk(conn)
    assert conn.execute("SELECT COUNT(*) FROM audio_fingerprints").fetchone()[0] == 1
