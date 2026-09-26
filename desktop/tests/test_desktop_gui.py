import importlib.util
import os
import time

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from factory_monitor.config import default_config, save_config
from factory_monitor.store import EventStore


@pytest.fixture(scope='module')
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def window(app, tmp_path):
    assert importlib.util.find_spec('factory_monitor_desktop.app'), 'desktop alert implementation missing'
    from factory_monitor_desktop.app import DesktopWindow
    config = default_config()
    config['review']['enabled'] = False
    path = tmp_path / 'config.json'
    save_config(config, path)
    instance = DesktopWindow(path, tmp_path / 'data', beeper=lambda: None)
    instance.show()
    yield instance
    instance.close()
    app.processEvents()


def event(window, ident='e1', **fields):
    data = dict(id=ident, camera_id='CAM01', kind='material_candidate', triggered_at=time.time(),
                status='candidate', reason='Synthetic visible change', layout_version=1,
                analysis_status='uncertain', review_label='pending', synthetic=True,
                analysis={'reason': 'Model disabled; manual review required'})
    data.update(fields)
    store = EventStore(window.data_dir / 'events.sqlite3')
    try:
        store.create_event(data)
    finally:
        store.close()
    window.handle_runtime_message({'type': 'candidate', 'event': data})
    window.refresh_alerts()
    return data


def test_candidate_displays_real_nonmodal_popup_before_model(window):
    event(window)
    assert window.alert_popup.isVisible()
    assert window.alert_popup.event_id == 'e1'
    assert 'CAM01' in window.alert_popup.heading.text()
    assert '合成' in window.alert_popup.source_badge.text()
    assert not window.alert_popup.isModal()


def test_burst_is_single_popup_and_all_events_durable(window):
    for i in range(10):
        event(window, f'burst-{i}')
    assert len(window.alert_inbox.pending(time.time())) == 10
    assert window.alert_popup.event_id == 'burst-0'
    assert '10' in window.inbox_button.text()


def test_confirm_persists_core_label_and_does_not_reopen_on_late_model(window):
    data = event(window)
    QTest.mouseClick(window.alert_popup.confirm_button, Qt.LeftButton)
    store = EventStore(window.data_dir / 'events.sqlite3')
    try:
        assert store.get_event('e1')['review_label'] == 'confirmed'
    finally:
        store.close()
    window.handle_runtime_message({'type': 'analysis', 'event_id': data['id'],
                                   'analysis_status': 'supported', 'analysis': {'reason': 'late'}})
    window.refresh_alerts()
    assert not window.alert_inbox.pending(time.time())
    assert not window.alert_popup.isVisible()


def test_live_display_capture_suppresses_popup_without_losing_alert(window):
    window.source_selector.setCurrentText('live')
    window.config['source']['backend'] = 'display'
    event(window)
    assert not window.alert_popup.isVisible()
    assert window.alert_inbox.pending(time.time())
    assert '遮挡' in window.overlay_status.text()


def test_popup_text_is_plain_and_evidence_outside_data_root_is_rejected(window, tmp_path):
    path = tmp_path / 'outside.html'
    path.write_text('must not open')
    event(window, reason='<a href="http://example.invalid">click</a>', evidence_path=str(path))
    assert window.alert_popup.reason.textFormat() == Qt.PlainText
    assert not window.alert_popup.evidence_button.isEnabled()


def test_unknown_and_timeout_never_display_normal(window):
    event(window, analysis_status='timeout')
    assert '超时' in window.alert_popup.state.text()
    assert '正常' not in window.alert_popup.state.text()


def test_snooze_moves_to_next_event_and_preserves_first(window):
    event(window, 'a')
    event(window, 'b')
    QTest.mouseClick(window.alert_popup.snooze_button, Qt.LeftButton)
    assert window.alert_popup.event_id == 'b'
    assert window.alert_inbox.get('a') is not None


def test_device_page_opens_without_starting_monitor(window):
    QTest.mouseClick(window.device_button, Qt.LeftButton)
    assert window.device_dialog.isVisible()
    assert window.runtime is None


def test_device_page_never_runs_gpu_subprocess_on_ui_thread(window, monkeypatch):
    from factory_monitor_desktop import profiles
    from PySide6.QtWidgets import QPlainTextEdit
    import json

    def forbidden_probe():
        pytest.fail('GPU subprocess must not run on the UI thread')

    class CachedSampler:
        def snapshot(self):
            return dict(total_mib=8192, free_mib=4096, used_mib=4096,
                        monotonic=time.monotonic(), error=None, latched=False)

    monkeypatch.setattr(profiles, '_nvidia_smi', forbidden_probe)
    window.resource_sampler = CachedSampler()
    window.show_device_profile()
    report = json.loads(window.device_dialog.findChild(QPlainTextEdit).toPlainText())
    assert report['hardware']['nvidia_vram_total_mb'] == 8192
    window.resource_sampler = None
    window.show_device_profile()
    report = json.loads(window.device_dialog.findChild(QPlainTextEdit).toPlainText())
    assert report['hardware']['nvidia_vram_total_mb'] is None


def test_restart_more_than_twenty_completed_events_preserves_inbox(app, tmp_path):
    from factory_monitor_desktop.app import DesktopWindow
    path = tmp_path / 'config.json'
    save_config(default_config(), path)
    data_dir = tmp_path / 'data'
    store = EventStore(data_dir / 'events.sqlite3')
    for i in range(25):
        store.create_event(dict(id=f'old-{i}', camera_id='CAM01', kind='station_absence',
            triggered_at=1000 + i, status='complete', reason='synthetic', layout_version=1,
            analysis_status='uncertain', recording_status='complete', completed_at=1100 + i,
            review_label='pending', synthetic=True))
    store.close()
    instance = DesktopWindow(path, data_dir, beeper=lambda: None)
    try:
        assert len(instance.alert_inbox.pending(time.time())) == 25
        assert instance.alert_inbox.get('old-0') is not None
    finally:
        instance.close()


def test_live_display_is_blocked_before_runtime_creation(window):
    calls = []
    window.runtime_factory = lambda *args: calls.append(args)
    window.source_selector.setCurrentText('live')
    window.config['source'].update(backend='display', calibrated=True)
    window.start_runtime()
    assert calls == []
    assert window.runtime is None
    assert '整屏' in window.status_label.text()


@pytest.mark.parametrize('source', ['demo', 'video'])
def test_display_guard_does_not_block_nonlive_sources(window, monkeypatch, source):
    from factory_monitor.gui.app import MainWindow
    calls = []
    monkeypatch.setattr(MainWindow, 'start_runtime', lambda self: calls.append(source))
    window.source_selector.setCurrentText(source)
    window.config['source']['backend'] = 'display'
    window.start_runtime()
    assert calls == [source]


def test_popup_waits_while_evidence_player_is_visible(window):
    from PySide6.QtWidgets import QDialog
    event(window)
    window.player = QDialog(window)
    window.player.show()
    window.refresh_alerts()
    assert not window.alert_popup.isVisible()
    assert len(window.alert_inbox.pending(time.time())) == 1
    window.player.close()
    window.refresh_alerts()
    assert window.alert_popup.isVisible()
