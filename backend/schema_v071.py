"""Repeatable migration for stage 11: block type of a finding and the adapter that decided it.

Called at the end of schema_v070.migrate. Adds columns and an index; findings saved before stage 11 get a block
type from their stored fields (text, link, frame) once - rows that already have one are never touched.
"""
from .schema_v062 import _add


def migrate(connection):
    _add(connection, 'findings', 'block_type', "TEXT NOT NULL DEFAULT ''")
    _add(connection, 'findings', 'block_reason', "TEXT NOT NULL DEFAULT ''")
    _add(connection, 'findings', 'adapter', "TEXT NOT NULL DEFAULT ''")
    _add(connection, 'findings', 'block_evidence', "TEXT NOT NULL DEFAULT '{}'")
    connection.execute('CREATE INDEX IF NOT EXISTS findings_block ON findings(block_type)')
    from .adapters import classify_legacy
    for row in connection.execute("SELECT id,title,text,destination,image_url,source_url,frame_url FROM findings WHERE block_type=''").fetchall():
        block_type, reason = classify_legacy(dict(row))
        connection.execute("UPDATE findings SET block_type=?,block_reason=?,adapter='legacy' WHERE id=? AND block_type=''", (block_type, reason, row['id']))
