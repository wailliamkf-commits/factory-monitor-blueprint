"""Run a real desktop/runtime scenario using explicitly synthetic input.

This does not record a screen. Pair it with the native recorder or an OS
recording tool. Operator button actions and timeout are scripted probes.
"""
from __future__ import annotations

import argparse
import json
import multiprocessing
import os
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / 'desktop/src'), str(ROOT / 'implementation/src')]

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication
from factory_monitor.config import default_config, save_config
from factory_monitor.store import EventStore
from factory_monitor_desktop.app import DesktopWindow
from factory_monitor_desktop.qt_bootstrap import configure_qt_platform_plugin_path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--seconds', type=int, default=125)
    parser.add_argument('--render-recording', action='store_true', help='Record actual Qt paints; NOT OS screen capture')
    args = parser.parse_args()
    if not 125 <= args.seconds <= 180:
        parser.error('duration must be 125..180 seconds; preserve real 30s-pre/60s-post evidence')
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    config = default_config()
    config['review']['enabled'] = False
    config['detection']['fps'] = 2
    config['source'].update(calibrated=True, expected_size=[960, 540],
                            window_title='SYNTHETIC TEN CAMERA DEMO - NOT SEETONG')
    for camera in config['cameras']:
        camera['material_roi'] = []
        camera['exit_line'] = []
        camera['station_roi'] = [[.05, .05], [.35, .05], [.35, .35], [.05, .35]]
        camera['absence_seconds'] = 31
        camera['schedule'] = {'days': list(range(7)), 'active': [['00:00', '24:00']], 'breaks': []}
    config_path = output / 'config.json'
    save_config(config, config_path)
    started = time.monotonic()
    log = []

    def record(kind, **values):
        row = {'elapsed_seconds': round(time.monotonic() - started, 3), 'kind': kind, **values}
        log.append(row)
        with (output / 'session.jsonl').open('a', encoding='utf-8') as stream:
            stream.write(json.dumps(row, ensure_ascii=False) + '\n')

    class DemoWindow(DesktopWindow):
        def handle_runtime_message(self, message):
            if message.get('type') in {'candidate', 'analysis', 'event_updated', 'error'}:
                record('runtime_message', message=message)
            super().handle_runtime_message(message)

    configure_qt_platform_plugin_path()
    app = QApplication.instance() or QApplication(sys.argv[:1])
    window = DemoWindow(config_path, output / 'data', beeper=lambda: None)
    area = app.primaryScreen().availableGeometry()
    width, height = min(1360, area.width() - 20), min(820, area.height() - 20)
    window.setGeometry(area.x() + 10, area.y() + 10, width, height)
    window.source_banner.setText('合成输入 / 自动测试操作 · 未调用视觉模型 · 非 Seetong 现场')
    window.show()
    window.raise_()
    app.processEvents()
    (output / 'process.json').write_text(json.dumps({'pid': os.getpid(), 'title': window.windowTitle(),
        'scope': 'actual desktop application with synthetic runtime and scripted operator actions',
        'field_gate': 'NOT_TESTED'}, ensure_ascii=False, indent=2))

    recorder = None
    if args.render_recording:
        from render_recording import RenderRecording
        recorder = RenderRecording(output / 'application-demo.mp4', window)

    def operate(action):
        popup = window.alert_popup
        if not popup.event_id:
            record('action_unavailable', action=action)
            return
        record('scripted_operator_action', event_id=popup.event_id, action=action)
        {'acknowledged': popup.ack_button, 'snooze': popup.snooze_button,
         'false_alarm': popup.false_alarm_button, 'confirmed': popup.confirm_button}[action].click()

    def inject_timeout():
        pending = window.alert_inbox.pending(time.time())
        due = [x for x in pending if x['alert_state'] == 'pending']
        if not due:
            record('fault_injection_unavailable')
            return
        item = due[0]
        analysis = {'reason': '合成故障注入：模拟模型超时；本演示未调用模型'}
        store = EventStore(window.data_dir / 'events.sqlite3')
        store.update_event(item['id'], analysis_status='timeout', analysis=analysis)
        store.close()
        record('synthetic_fault_injection', event_id=item['id'], fault='model_timeout')
        window.handle_runtime_message({'type': 'analysis', 'event_id': item['id'],
                                       'analysis_status': 'timeout', 'analysis': analysis})

    def evidence():
        store = EventStore(window.data_dir / 'events.sqlite3')
        events = store.list_events()
        store.close()
        candidates = [x for x in events if x.get('evidence_path') and Path(x['evidence_path']).is_file()]
        if candidates:
            item = candidates[0]
            record('scripted_evidence_open', event_id=item['id'], path=item['evidence_path'])
            window.handle_alert_action(item['id'], 'evidence')
        else:
            record('evidence_missing')

    def finish():
        window.stop_runtime()
        if window.player:
            record('playback_readback', frames_decoded=window.player.frames_decoded)
        store = EventStore(window.data_dir / 'events.sqlite3')
        events = store.list_events()
        store.close()
        pending = window.alert_inbox.pending(time.time(), limit=1000)
        window.grab().save(str(output / 'desktop-final.png'))
        geometry = window.geometry()
        window.close()
        # Open a new actual window on the same durable files, then read back.
        restored = DesktopWindow(config_path, output / 'data', beeper=lambda: None)
        restored.setGeometry(geometry)
        restored.show()
        restored.source_banner.setText('重启恢复检查 · 合成历史事件 · 非现场')
        after = restored.alert_inbox.pending(time.time(), limit=1000)
        report = {'scope': 'real Qt/runtime; synthetic inputs; scripted actions; no visual model',
                  'elapsed_seconds': round(time.monotonic() - started, 3),
                  'events': events, 'pending_before_restart': len(pending),
                  'pending_after_restart': len(after),
                  'restart_preserved': {x['id'] for x in pending} == {x['id'] for x in after},
                  'recording_complete': sum(x.get('recording_status') == 'complete' for x in events),
                  'field_gate': 'NOT_TESTED', 'screen_recording': 'Qt render recording; NOT OS screen capture' if recorder else 'separate native recorder required'}
        (output / 'demo-report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2))
        record('restart_readback', preserved=report['restart_preserved'])
        if recorder:
            recorder.window = restored
        def close_demo():
            if recorder:
                recorder.close()
            restored.close()
            app.quit()
        QTimer.singleShot(6000, close_demo)

    QTimer.singleShot(5000, lambda: (record('start_runtime', source='demo'), window.start_button.click()))
    for second, action in [(45, 'acknowledged'), (50, 'snooze'), (55, 'false_alarm'), (60, 'confirmed')]:
        QTimer.singleShot(second * 1000, lambda value=action: operate(value))
    QTimer.singleShot(70000, inject_timeout)
    QTimer.singleShot(103000, evidence)
    QTimer.singleShot(115000, lambda: (window.player.close() if window.player else None, window.device_button.click()))
    QTimer.singleShot(args.seconds * 1000, finish)
    return app.exec()


if __name__ == '__main__':
    multiprocessing.freeze_support()
    raise SystemExit(main())
