"""Rebuild the synthetic migration fixture; no working database is distributed."""
from pathlib import Path
import sqlite3

def pytest_sessionstart(session):
    folder = Path(__file__).parent / 'fixtures'
    target = folder / 'radar-0.6.2-fixture.db'
    if not target.exists():
        with sqlite3.connect(target) as connection:
            connection.executescript((folder / 'radar-0.6.2-fixture.sql').read_text(encoding='utf-8'))
