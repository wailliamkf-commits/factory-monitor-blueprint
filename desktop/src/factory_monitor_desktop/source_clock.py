"""Parse OCR-read camera clocks and check their observed progression."""

from __future__ import annotations

import math
import re
from datetime import datetime


_CAMERA_DATETIME = re.compile(
    r"(?<!\d)([0-9]{4}-[0-9]{2}-[0-9]{2})[^\d]{0,32}"
    r"([0-9]{2}:[0-9]{2}:[0-9]{2})(?!\d)"
)
_DATE_TOKEN = re.compile(r"(?<!\d)[0-9]{4}-[0-9]{2}-[0-9]{2}(?!\d)")
_TIME_TOKEN = re.compile(r"(?<!\d)[0-9]{2}:[0-9]{2}:[0-9]{2}(?!\d)")


def parse_camera_datetime(text: str) -> datetime | None:
    """Parse one unique complete timestamp; allow up to 32 non-numeric separator chars.

    The caller joins OCR observations in visual order. This does not validate
    OCR truth, camera identity, or layout calibration.
    """
    if not isinstance(text, str):
        return None
    matches = list(_CAMERA_DATETIME.finditer(text))
    dates = list(_DATE_TOKEN.finditer(text))
    times = list(_TIME_TOKEN.finditer(text))
    if len(matches) != 1 or len(dates) != 1 or len(times) != 1:
        return None
    if dates[0].span() != matches[0].span(1) or times[0].span() != matches[0].span(2):
        return None
    date_text, time_text = matches[0].groups()
    try:
        return datetime.strptime(f"{date_text} {time_text}", "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None


class ClockTracker:
    """Classify readable clock progression against the sampling clock."""

    def __init__(self, stall_seconds: float = 10, tolerance_seconds: float = 2) -> None:
        if not math.isfinite(stall_seconds) or stall_seconds <= 0:
            raise ValueError("stall_seconds must be finite and positive")
        if not math.isfinite(tolerance_seconds) or tolerance_seconds < 0:
            raise ValueError("tolerance_seconds must be finite and nonnegative")
        self.stall_seconds = float(stall_seconds)
        self.tolerance_seconds = float(tolerance_seconds)
        self._last_value: datetime | None = None
        self._last_elapsed: float | None = None
        self._unchanged_since: float | None = None
        self._segment_value: datetime | None = None
        self._segment_elapsed: float | None = None

    def _reset(self) -> None:
        self._last_value = None
        self._last_elapsed = None
        self._unchanged_since = None
        self._segment_value = None
        self._segment_elapsed = None

    def _rebaseline(self, value: datetime, elapsed_seconds: float) -> None:
        self._last_value = value
        self._last_elapsed = elapsed_seconds
        self._unchanged_since = None
        self._segment_value = value
        self._segment_elapsed = elapsed_seconds

    def observe(self, value: datetime | None, elapsed_seconds: float) -> str:
        """Return ``unknown``, ``advancing``, or ``suspected_stalled``.

        Gaps longer than ``stall_seconds`` are unknown and rebaseline.
        """
        try:
            elapsed = float(elapsed_seconds)
        except (TypeError, ValueError, OverflowError):
            self._reset()
            return "unknown"
        if not math.isfinite(elapsed) or elapsed < 0:
            self._reset()
            return "unknown"
        if value is None or not isinstance(value, datetime):
            self._reset()
            return "unknown"
        if (self._last_elapsed is None or self._last_value is None
                or self._segment_value is None or self._segment_elapsed is None):
            self._rebaseline(value, elapsed)
            return "unknown"
        if elapsed <= self._last_elapsed:
            self._rebaseline(value, elapsed)
            return "unknown"

        elapsed_delta = elapsed - self._last_elapsed
        if elapsed_delta > self.stall_seconds:
            self._rebaseline(value, elapsed)
            return "unknown"

        try:
            camera_delta = (value - self._last_value).total_seconds()
        except (TypeError, OverflowError):
            self._rebaseline(value, elapsed)
            return "unknown"

        if camera_delta == 0:
            if self._unchanged_since is None:
                self._unchanged_since = self._last_elapsed
            self._last_elapsed = elapsed
            if elapsed - self._unchanged_since >= self.stall_seconds:
                self._segment_value = value
                self._segment_elapsed = elapsed
                return "suspected_stalled"
            return "unknown"

        segment_camera_delta = (value - self._segment_value).total_seconds()
        segment_elapsed_delta = elapsed - self._segment_elapsed
        if (camera_delta > 0
                and abs(camera_delta - elapsed_delta) <= self.tolerance_seconds
                and abs(segment_camera_delta - segment_elapsed_delta) <= self.tolerance_seconds):
            self._last_value = value
            self._last_elapsed = elapsed
            self._unchanged_since = None
            return "advancing"

        self._rebaseline(value, elapsed)
        return "unknown"
