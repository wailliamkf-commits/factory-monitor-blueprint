from factory_monitor.config import default_config
from factory_monitor.store import EventStore


def test_desktop_retention_never_deletes_unreviewed_evidence(tmp_path):
    from factory_monitor_desktop.app import ProtectedRuntime
    config = default_config()
    config['evidence']['retain_completed'] = 1
    controller = ProtectedRuntime(config, tmp_path)
    store = EventStore(tmp_path / 'events.sqlite3')
    for i in range(3):
        store.create_event(dict(id=str(i), camera_id='CAM01', kind='material_candidate',
                                triggered_at=float(i + 1), status='complete', reason='test', layout_version=1,
                                analysis_status='uncertain', completed_at=float(i + 2), review_label='pending'))
    controller._prune_completed()
    assert len(store.list_events()) == 3
    store.close()


def test_evidence_symlink_cannot_escape_data_directory(tmp_path):
    from factory_monitor_desktop.app import safe_evidence_path
    root = tmp_path / 'data'
    root.mkdir()
    outside = tmp_path / 'outside.mp4'
    outside.write_bytes(b'outside')
    link = root / 'escape.mp4'
    try:
        link.symlink_to(outside)
    except OSError:
        import pytest
        pytest.skip('platform does not permit creating a symlink for this check')
    assert safe_evidence_path({'evidence_path': str(link)}, root) is None
