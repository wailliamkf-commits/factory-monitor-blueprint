"""Record actual Qt application paints, NOT operating-system screen pixels.

Used only for a clearly labelled synthetic engineering demonstration. No OS
permission, screen contents, other application's window or camera is accessed.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import cv2
import numpy as np
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor, QFont, QImage, QPainter
from PySide6.QtWidgets import QApplication


class RenderRecording:
    def __init__(self, output: Path, window, fps=8):
        self.path = output
        self.window = window
        self.started = time.monotonic()
        self.fps = fps
        self.frames = 0
        self.width = (window.width() // 2) * 2
        self.height = (window.height() // 2) * 2 + 60
        self.writer = cv2.VideoWriter(str(output), cv2.VideoWriter_fourcc(*'mp4v'), fps,
                                      (self.width, self.height))
        if not self.writer.isOpened():
            raise RuntimeError('Application-render recording encoder did not open')
        self.timer = QTimer(window)
        self.timer.timeout.connect(self.capture)
        self.timer.start(round(1000 / fps))
        self.capture()

    def capture(self):
        # Only explicit application's visible top-level widgets, in their real
        # geometry. This uses live widget paints, not screenshots of the OS.
        canvas = QImage(self.width, self.height, QImage.Format_RGB888)
        canvas.fill(QColor('#101923'))
        painter = QPainter(canvas)
        painter.drawPixmap(0, 60, self.window.grab())
        origin = self.window.geometry().topLeft()
        for widget in QApplication.topLevelWidgets():
            if widget is self.window or not widget.isVisible():
                continue
            if widget.parent() is not self.window:
                continue
            relative = widget.geometry().topLeft() - origin
            painter.drawPixmap(relative.x(), relative.y() + 60, widget.grab())
        painter.fillRect(0, 0, self.width, 60, QColor('#0b3042'))
        painter.setPen(Qt.white)
        painter.setFont(QFont('Arial', 13))
        painter.drawText(14, 24, '实际应用界面渲染录制 · 合成输入 · 自动测试操作 · 非系统读屏 / 非现场验收')
        painter.drawText(14, 48, f'运行时间 {time.monotonic() - self.started:.1f} 秒 · 未调用视觉模型')
        painter.end()
        rgb = np.frombuffer(canvas.bits(), dtype=np.uint8).reshape(self.height, canvas.bytesPerLine())
        rgb = rgb[:, :self.width * 3].reshape(self.height, self.width, 3)
        self.writer.write(cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR))
        self.frames += 1

    def close(self):
        self.timer.stop()
        self.writer.release()
        metadata = {'capture_kind': 'Qt application paint recording, not OS screen capture',
                    'frames_written': self.frames, 'fps': self.fps,
                    'video_seconds': self.frames / self.fps,
                    'wall_seconds': round(time.monotonic() - self.started, 3),
                    'scope': 'synthetic runtime, scripted operator actions, no visual model',
                    'field_gate': 'NOT_TESTED'}
        self.path.with_suffix('.json').write_text(json.dumps(metadata, ensure_ascii=False, indent=2))
