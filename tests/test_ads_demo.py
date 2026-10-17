import pytest
from backend import db
from tools.ads_demo import seed


def test_demo_setup_is_idempotent_has_no_invented_findings_and_no_schedule(tmp_path, monkeypatch):
    monkeypatch.setattr(db, 'DATA', tmp_path)
    db.init()
    first = seed()
    assert seed() == first
    assert len(db.rows('SELECT id FROM profiles')) == 1
    assert not db.rows('SELECT id FROM findings')
    assert not db.rows('SELECT id FROM runs')
    job = db.rows('SELECT * FROM jobs WHERE id=?', (first['job'],))[0]
    assert 'librain.ru' in job['urls']
    assert job['schedule_kind'] == 'none'


def test_demo_does_not_adopt_an_unmarked_existing_profile(tmp_path, monkeypatch):
    monkeypatch.setattr(db, 'DATA', tmp_path)
    db.init()
    db.execute('INSERT INTO profiles(name,created_at) VALUES (?,?)', ('Existing', db.now()))
    with pytest.raises(RuntimeError):
        seed()
