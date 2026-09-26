"""Real Qt operator console; the capture and evidence core stays byte-pinned."""
from __future__ import annotations

import json
import shutil
import time
from pathlib import Path

import cv2
from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import (
    QCheckBox, QDialog, QHBoxLayout, QLabel, QPlainTextEdit, QPushButton,
    QSplitter, QToolBar, QVBoxLayout,
)

from factory_monitor.gui.app import MainWindow
from factory_monitor.runtime import RuntimeController
from factory_monitor.store import EventStore
from .alerts import AlertInbox


def safe_evidence_path(event: dict, data_dir: Path) -> Path | None:
    """Resolve once before opening; never launch an arbitrary URI from event text."""
    for value in (event.get('evidence_path'), event.get('preview_path')):
        if not isinstance(value, str) or not value:
            continue
        try:
            path = Path(value).resolve()
            if path.is_relative_to(data_dir.resolve()) and path.is_file():
                return path
        except (OSError, ValueError):
            continue
    return None


class ProtectedRuntime(RuntimeController):
    """Keep unreviewed evidence; disk admission is supervised by the desktop."""

    def _prune_completed(self) -> None:
        # The old core deletes by completion count regardless of human review.
        # This desktop edition never deletes evidence automatically.
        return


def plain_label(text: str = '') -> QLabel:
    label = QLabel(text)
    label.setTextFormat(Qt.PlainText)
    label.setWordWrap(True)
    return label


class AlertPopup(QDialog):
    action = Signal(str, str)

    def __init__(self, parent=None):
        super().__init__(parent, Qt.Tool | Qt.WindowStaysOnTopHint)
        self.setModal(False)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setWindowTitle('候选异常 · 待人工查看')
        self.setMinimumWidth(480)
        self.setMaximumWidth(560)
        self.event_id = ''
        layout = QVBoxLayout(self)
        self.source_badge = plain_label()
        self.source_badge.setStyleSheet('color:#f4c56b;font-weight:600;')
        self.heading = plain_label()
        self.heading.setStyleSheet('font-size:22px;font-weight:700;')
        self.state = plain_label()
        self.reason = plain_label()
        self.queue_label = plain_label()
        for widget in (self.source_badge, self.heading, self.state, self.reason, self.queue_label):
            layout.addWidget(widget)
        layout.addWidget(plain_label('仅提示画面候选；模型结果不能确定意图或违规。'))
        buttons = QHBoxLayout()
        self.evidence_button = QPushButton('查看证据')
        self.ack_button = QPushButton('已阅')
        self.snooze_button = QPushButton('稍后 60 秒')
        for button, action in ((self.evidence_button, 'evidence'), (self.ack_button, 'acknowledged'),
                               (self.snooze_button, 'snooze')):
            button.clicked.connect(lambda checked=False, value=action: self.action.emit(self.event_id, value))
            buttons.addWidget(button)
        layout.addLayout(buttons)
        decisions = QHBoxLayout()
        self.confirm_button = QPushButton('人工确认候选')
        self.false_alarm_button = QPushButton('标记误报')
        for button, action in ((self.confirm_button, 'confirmed'), (self.false_alarm_button, 'false_alarm')):
            button.clicked.connect(lambda checked=False, value=action: self.action.emit(self.event_id, value))
            decisions.addWidget(button)
        layout.addLayout(decisions)
        self.setStyleSheet('QDialog{background:#17212b;color:#e8eef4;} QLabel{color:#e8eef4;} '
                           'QPushButton{padding:9px 12px;}')

    def display(self, event: dict, count: int, data_dir: Path):
        self.event_id = event['id']
        source = event.get('input_source', 'unknown')
        self.source_badge.setText({'demo': '合成测试输入 · 不是现场行为识别',
                                  'video': '本地录像回放 · 不是当前现场',
                                  'live': '实时窗口输入 · 现场准确率未验收'}.get(source, '历史事件 · 输入来源未核实'))
        kind = {'material_candidate': '物料变化候选', 'station_absence': '工位无人候选'}.get(
            event.get('kind'), '待复核候选')
        self.heading.setText(f"{event.get('camera_id', '?')}  ·  {kind}")
        state = {'pending': '正在等待模型复核', 'supported': '模型支持候选，仍需人工确认',
                 'dismissed': '模型未支持候选，仍需人工查看', 'uncertain': '模型无法确定 / 未启用，请人工查看',
                 'timeout': '模型复核超时，请人工查看', 'error': '模型服务异常，请人工查看'}
        self.state.setText(state.get(event.get('analysis_status'), '分析状态未知，请人工查看'))
        analysis = event.get('analysis')
        detail = str(analysis.get('reason') or analysis.get('error') or '') if isinstance(analysis, dict) else ''
        self.reason.setText(f"触发原因：{str(event.get('reason', '未说明'))[:700]}\n{detail[:400]}")
        self.queue_label.setText(f"待处理 {count}{'+' if count >= 1000 else ''} 项 · 当前 {event['id'][:32]}")
        self.evidence_button.setEnabled(safe_evidence_path(event, data_dir) is not None)

    def closeEvent(self, event):
        if self.event_id:
            self.action.emit(self.event_id, 'snooze')
        event.accept()


class EvidencePlayer(QDialog):
    """Local decoding is observed; requesting an OS opener is not called playback."""

    def __init__(self, path: Path, parent=None):
        super().__init__(parent)
        self.setWindowTitle('本地证据回放 · ' + path.name)
        self.setStyleSheet('QDialog{background:#17212b;} QLabel{color:#e8eef4;}')
        self.resize(820, 520)
        layout = QVBoxLayout(self)
        self.state = plain_label('正在读取本地视频')
        self.image = QLabel()
        self.image.setAlignment(Qt.AlignCenter)
        layout.addWidget(self.state)
        layout.addWidget(self.image, 1)
        self.capture = cv2.VideoCapture(str(path))
        self.frames_decoded = 0
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.next_frame)
        if not self.capture.isOpened():
            self.state.setText('本地文件无法解码；没有播放成功')
        else:
            fps = self.capture.get(cv2.CAP_PROP_FPS)
            fps = fps if 0 < fps <= 60 else 5
            self.timer.start(round(1000 / fps))
            self.next_frame()

    def next_frame(self):
        ok, frame = self.capture.read()
        if not ok:
            self.timer.stop()
            self.state.setText(f'回放结束 · 实际解码 {self.frames_decoded} 帧')
            return
        self.frames_decoded += 1
        height, width = frame.shape[:2]
        image = QImage(frame.data, width, height, frame.strides[0], QImage.Format_BGR888).copy()
        pixmap = QPixmap.fromImage(image).scaled(780, 430, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        self.image.setPixmap(pixmap)
        self.state.setText(f'本地证据播放中 · 已解码 {self.frames_decoded} 帧')

    def closeEvent(self, event):
        self.timer.stop()
        self.capture.release()
        super().closeEvent(event)


class DesktopWindow(MainWindow):
    def __init__(self, config_path: Path, data_dir: Path, *, runtime_factory=None, beeper=None,
                 routing_summary: dict | None = None, resource_sampler=None):
        self._closing = False
        self._last_sound_at = -float('inf')
        self._last_alert_id = ''
        self._last_disk_check = 0.0
        self._disk_fault = ''
        self.routing_summary = routing_summary
        self.resource_sampler = resource_sampler
        super().__init__(config_path, data_dir, runtime_factory=runtime_factory or ProtectedRuntime,
                         notifier=lambda *_: None, beeper=beeper)
        self.setWindowTitle('工厂监控 · 告警与复核 v0.2')
        self.open_evidence_button.setText('播放本地证据')
        self.resize(1360, 820)
        self.alert_inbox = AlertInbox(self.data_dir / 'alerts.sqlite3')
        self.alert_popup = AlertPopup(self)
        self.alert_popup.action.connect(self.handle_alert_action)
        self.device_dialog = None
        self.player = None
        self.overlay_status = plain_label('')
        self.statusBar().addPermanentWidget(self.overlay_status, 1)
        self.resource_status = plain_label('')
        self.statusBar().addPermanentWidget(self.resource_status)
        toolbar = QToolBar('工作台', self)
        toolbar.setMovable(False)
        self.addToolBar(toolbar)
        self.inbox_button = QPushButton('待处理 0')
        self.inbox_button.clicked.connect(self.refresh_alerts)
        self.device_button = QPushButton('设备与模型')
        self.device_button.clicked.connect(self.show_device_profile)
        self.sound_checkbox = QCheckBox('声音提醒（10 秒冷却）')
        self.settings_button = QPushButton('显示 / 收起设置')
        self.settings_button.clicked.connect(self.toggle_settings)
        for widget in (self.inbox_button, self.device_button, self.settings_button, self.sound_checkbox):
            toolbar.addWidget(widget)
        self.toggle_settings()
        self.alert_timer = QTimer(self)
        self.alert_timer.timeout.connect(self.refresh_alerts)
        self.alert_timer.start(500)
        self.reconcile_event_store()
        self.refresh_alerts()

    def toggle_settings(self):
        splitter = self.findChild(QSplitter)
        if splitter:
            splitter.setSizes([900, 0] if splitter.sizes()[-1] > 0 else [800, 560])

    def notify_operator(self, title, body):
        # Legacy toast/bell is replaced by the durable queue, so model updates
        # cannot create an uncontrolled second notification channel.
        self.statusBar().showMessage(f'{title}：{body}', 8000)

    def upsert_event(self, event: dict, *, alert=False):
        previous = self.alert_inbox.get(event['id']) if hasattr(self, 'alert_inbox') else None
        # Snapshot before the core trims its display cache to 20 completed rows.
        merged = {**(previous or {}), **self._events.get(event['id'], {}), **event}
        super().upsert_event(merged, alert=False)
        if not hasattr(self, 'alert_inbox') or self._closing:
            return
        merged['input_source'] = (previous or {}).get('input_source') or (
            'demo' if merged.get('synthetic') else self.source_selector.currentText()
            if alert or self.runtime is not None else 'unknown')
        self.alert_inbox.observe(merged, time.time())
        # Core's explicit human review wins over an old inbox snapshot.
        if merged.get('review_label') in {'confirmed', 'false_alarm'}:
            self.alert_inbox.resolve(event['id'], merged['review_label'], time.time())
        self.refresh_alerts()

    def popup_allowed(self):
        if self.source_selector.currentText() != 'live':
            return True
        source = self.config['source']
        title = source.get('window_title', '').strip()
        return bool(source.get('calibrated') and source.get('backend') in {'wgc', 'screencapturekit', 'auto'}
                    and title and not title.startswith('工厂监控'))

    def refresh_alerts(self):
        if self.resource_sampler is not None:
            sample = self.resource_sampler.snapshot()
            if sample.get('latched'):
                state = '8GB 保护：压力过高，已停发 AI 复核'
            elif (sample.get('error') or sample.get('monotonic') is None
                  or time.monotonic() - sample['monotonic'] > 10):
                state = '8GB 保护：资源监测未知，AI 复核受限'
            else:
                state = f"显存 {sample['used_mib'] / 1024:.1f}/{sample['total_mib'] / 1024:.1f} GiB · 8GB 保护"
            self.resource_status.setText(state)
        if self._closing or not hasattr(self, 'alert_inbox'):
            return
        now = time.time()
        pending = self.alert_inbox.pending(now, limit=1000)
        due = [entry for entry in pending if entry.get('alert_state') != 'snoozed']
        self.inbox_button.setText(f"待处理 {len(pending)}{'+' if len(pending) == 1000 else ''}")
        viewing_evidence = self.player is not None and self.player.isVisible()
        allowed = self.popup_allowed() and not viewing_evidence
        explanation = ('回放期间暂停浮层，候选继续保存' if viewing_evidence else
                       '' if allowed else '浮层已抑制：避免遮挡被采集屏幕；事件仍保存')
        self.overlay_status.setText(self._disk_fault or explanation)
        if not due or not allowed:
            self.alert_popup.hide()
        else:
            current = next((x for x in due if x['id'] == self.alert_popup.event_id), due[0])
            self.alert_popup.display(current, len(pending), self.data_dir)
            if not self.alert_popup.isVisible():
                geometry = self.geometry()
                self.alert_popup.move(geometry.right() - 545, geometry.top() + 110)
                self.alert_popup.show()
            if current['id'] != self._last_alert_id:
                self._last_alert_id = current['id']
                if self.sound_checkbox.isChecked() and time.monotonic() - self._last_sound_at >= 10:
                    self._beeper()
                    self._last_sound_at = time.monotonic()
        if self.runtime and time.monotonic() - self._last_disk_check > 10:
            self._last_disk_check = time.monotonic()
            if shutil.disk_usage(self.data_dir).free < 1024**3:
                self._disk_fault = '磁盘可用空间不足 1 GiB：已停止采集，请归档证据后继续'
                self.stop_runtime()
                self.overlay_status.setText(self._disk_fault)

    def start_runtime(self):
        if self.source_selector.currentText() == 'live' and self.config['source'].get('backend') == 'display':
            self.status_label.setText('此桌面预览尚不支持受保护的整屏采集；请使用已校准的目标窗口采集')
            self.log('已阻止 live + display：主界面与证据窗口尚未实现可验证的采集区域隔离。')
            return
        if shutil.disk_usage(self.data_dir).free < 1024**3:
            self._disk_fault = '可用空间不足 1 GiB，未启动；请先归档证据'
            self.overlay_status.setText(self._disk_fault)
            return
        self._disk_fault = ''
        super().start_runtime()

    def handle_alert_action(self, event_id, action):
        event = self.alert_inbox.get(event_id)
        if event is None:
            return
        try:
            if action == 'evidence':
                path = safe_evidence_path(event, self.data_dir)
                if path is None:
                    self.status_label.setText('证据缺失或路径不在当前数据目录，未打开')
                    return
                if self.player:
                    self.player.close()
                self.player = EvidencePlayer(path, self)
                self.player.show()
                return
            if action == 'snooze':
                self.alert_inbox.snooze(event_id, time.time() + 60)
            else:
                if action in {'confirmed', 'false_alarm'}:
                    store = EventStore(self.data_dir / 'events.sqlite3')
                    try:
                        store.update_event(event_id, review_label=action)
                        updated = store.get_event(event_id)
                    finally:
                        store.close()
                    self._events[event_id] = updated
                    super().upsert_event(updated, alert=False)
                self.alert_inbox.resolve(event_id, action, time.time())
            self.alert_popup.hide()
            self.status_label.setText('操作已保存到本机；已阅与人工确认分别记录')
            self.refresh_alerts()
        except Exception as error:
            self.status_label.setText(f'操作未完成，待办保留：{error}')

    def open_selected_evidence(self):
        event_id = self.selected_event_id()
        if event_id:
            self.handle_alert_action(event_id, 'evidence')

    def show_device_profile(self):
        from .profiles import probe_hardware, recommend_profile
        if self.device_dialog:
            self.device_dialog.close()
        dialog = QDialog(self)
        dialog.setWindowTitle('设备与模型 · 先测量，再升级')
        dialog.resize(720, 580)
        layout = QVBoxLayout(dialog)
        layout.addWidget(plain_label('设备建议不是性能承诺。GPU 信息来自后台缓存；未启用缓存时可使用 --probe 单独探测。'))
        text = QPlainTextEdit()
        text.setReadOnly(True)
        layout.addWidget(text)
        hardware = probe_hardware(include_gpu=False)
        sample = self.resource_sampler.snapshot() if self.resource_sampler else None
        if (sample and not sample.get('error') and sample.get('monotonic') is not None
                and 0 <= time.monotonic() - sample['monotonic'] <= 10):
            hardware['nvidia_vram_total_mb'] = int(sample['total_mib'])
            hardware['nvidia_vram_free_mb'] = int(sample['free_mib'])
            hardware['gpu_probe_status'] = 'cached_background_sample'
        text.setPlainText(json.dumps({'hardware': hardware, 'recommendation': recommend_profile(hardware),
                                     'resource_guard': sample if sample else '未启用',
                                     'routing': self.routing_summary or '沿用核心本地配置；未设置扩展路由'},
                                    ensure_ascii=False, indent=2))
        self.device_dialog = dialog
        dialog.show()

    def closeEvent(self, event):
        if self._closing:
            event.accept()
            return
        self._closing = True
        self.alert_timer.stop()
        self.alert_popup.hide()
        if self.player:
            self.player.close()
        if self.device_dialog:
            self.device_dialog.close()
        super().closeEvent(event)
        self.alert_inbox.close()
