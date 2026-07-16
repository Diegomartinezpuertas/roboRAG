"""Tests for the SQLite-backed zone store."""

from robot_zones.zone_store import ZoneStore

AREA = {'x_min': -1.0, 'y_min': -2.0, 'x_max': 0.5, 'y_max': -0.5}


def test_empty_store(tmp_path):
    store = ZoneStore(str(tmp_path / 'z.db'))
    assert store.load_all() == {}


def test_save_and_load(tmp_path):
    store = ZoneStore(str(tmp_path / 'z.db'))
    store.save('cocina', AREA)
    zones = store.load_all()
    assert set(zones) == {'cocina'}
    assert zones['cocina'] == AREA


def test_save_overwrites(tmp_path):
    store = ZoneStore(str(tmp_path / 'z.db'))
    store.save('cocina', AREA)
    store.save('cocina', {'x_min': 0.0, 'y_min': 0.0, 'x_max': 1.0, 'y_max': 1.0})
    assert store.load_all()['cocina']['x_max'] == 1.0
    assert len(store.load_all()) == 1


def test_delete(tmp_path):
    store = ZoneStore(str(tmp_path / 'z.db'))
    store.save('cocina', AREA)
    assert store.delete('cocina') is True
    assert store.delete('cocina') is False  # already gone
    assert store.load_all() == {}


def test_persists_across_instances(tmp_path):
    db = str(tmp_path / 'z.db')
    ZoneStore(db).save('salon', AREA)
    assert 'salon' in ZoneStore(db).load_all()
