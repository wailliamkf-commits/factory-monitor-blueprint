"""Durable local alert inbox, independent of the frozen core event database."""

from __future__ import annotations

import json
import math
import sqlite3
import threading
from pathlib import Path
from typing import Any


_RESOLUTION_ACTIONS = {"acknowledged", "confirmed", "false_alarm"}
_FINAL_STATES = _RESOLUTION_ACTIONS
_MAX_PENDING_LIMIT = 10_000
_INTERNAL_FIELDS = {"alert_state", "snoozed_until", "alert_updated_at"}


def _finite_time(value: object, name: str) -> float:
    if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
        raise ValueError(f"{name} must be a finite nonnegative number")
    return float(value)


def _identifier(value: object, name: str) -> str:
    if type(value) is not str or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    return value


class AlertInbox:
    """Keep alert snapshots and operator workflow state in a separate SQLite file."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._connection = sqlite3.connect(self.path, timeout=5, check_same_thread=False)
        self._connection.row_factory = sqlite3.Row
        with self._connection:
            self._connection.execute("PRAGMA journal_mode=WAL")
            self._connection.execute("PRAGMA synchronous=FULL")
            self._connection.execute(
                """
                CREATE TABLE IF NOT EXISTS alerts (
                    event_id TEXT PRIMARY KEY,
                    first_seen REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    alert_state TEXT NOT NULL CHECK (
                        alert_state IN ('pending', 'snoozed', 'acknowledged', 'confirmed', 'false_alarm')
                    ),
                    snoozed_until REAL,
                    event_json TEXT NOT NULL
                )
                """
            )
            self._connection.execute(
                "CREATE INDEX IF NOT EXISTS alerts_fifo ON alerts(alert_state, first_seen, event_id)"
            )
        self._closed = False

    @staticmethod
    def _clean_event(event: object, *, require_complete_identity: bool) -> tuple[str, dict[str, Any]]:
        if type(event) is not dict:
            raise ValueError("event must be an object")
        cleaned = {key: value for key, value in event.items() if key not in _INTERNAL_FIELDS}
        event_id = _identifier(cleaned.get("id"), "event.id")
        for field in ("camera_id", "kind"):
            if field in cleaned:
                _identifier(cleaned[field], f"event.{field}")
            elif require_complete_identity:
                raise ValueError(f"event.{field} must be a non-empty string")
        if "triggered_at" in cleaned:
            _finite_time(cleaned["triggered_at"], "event.triggered_at")
        elif require_complete_identity:
            raise ValueError("event.triggered_at must be a finite nonnegative number")
        try:
            json.dumps(cleaned, ensure_ascii=False, allow_nan=False)
        except (TypeError, ValueError) as exc:
            raise ValueError("event must contain only finite JSON values") from exc
        return event_id, cleaned

    @staticmethod
    def _decode(row: sqlite3.Row) -> dict[str, Any]:
        event = json.loads(row["event_json"])
        event["alert_state"] = row["alert_state"]
        event["snoozed_until"] = row["snoozed_until"]
        event["alert_updated_at"] = row["updated_at"]
        return event

    def _ensure_open(self) -> None:
        if self._closed:
            raise RuntimeError("alert inbox is closed")

    def observe(self, event: dict, now: float) -> bool:
        """Insert a new alert or merge a changed snapshot; never reopen a resolved alert."""
        observed_at = _finite_time(now, "now")
        event_id, incoming = self._clean_event(event, require_complete_identity=False)
        with self._lock, self._connection:
            self._ensure_open()
            row = self._connection.execute(
                "SELECT * FROM alerts WHERE event_id = ?", (event_id,)
            ).fetchone()
            if row is None:
                self._clean_event(incoming, require_complete_identity=True)
                self._connection.execute(
                    "INSERT INTO alerts (event_id, first_seen, updated_at, alert_state, snoozed_until, event_json) "
                    "VALUES (?, ?, ?, 'pending', NULL, ?)",
                    (event_id, observed_at, observed_at, json.dumps(incoming, ensure_ascii=False, allow_nan=False)),
                )
                return True

            current = json.loads(row["event_json"])
            for identity_field in ("camera_id", "kind", "triggered_at"):
                if identity_field in incoming and identity_field in current and incoming[identity_field] != current[identity_field]:
                    raise ValueError(f"event.{identity_field} cannot change for an existing event id")
            merged = {**current, **incoming}
            if merged == current:
                return False
            self._connection.execute(
                "UPDATE alerts SET updated_at = ?, event_json = ? WHERE event_id = ?",
                (observed_at, json.dumps(merged, ensure_ascii=False, allow_nan=False), event_id),
            )
            return True

    def pending(self, now: float, limit: int = 100) -> list[dict[str, Any]]:
        """Return FIFO open alerts, including deferred alerts marked as snoozed."""
        current_time = _finite_time(now, "now")
        if type(limit) is not int or not 1 <= limit <= _MAX_PENDING_LIMIT:
            raise ValueError(f"limit must be an integer between 1 and {_MAX_PENDING_LIMIT}")
        with self._lock, self._connection:
            self._ensure_open()
            self._connection.execute(
                "UPDATE alerts SET alert_state = 'pending', snoozed_until = NULL, updated_at = ? "
                "WHERE alert_state = 'snoozed' AND snoozed_until <= ?",
                (current_time, current_time),
            )
            rows = self._connection.execute(
                "SELECT * FROM alerts WHERE alert_state IN ('pending', 'snoozed') "
                "ORDER BY first_seen ASC, event_id ASC LIMIT ?",
                (limit,),
            ).fetchall()
        return [self._decode(row) for row in rows]

    def resolve(self, event_id: str, action: str, now: float) -> None:
        """Persist an explicit operator action without changing the core event record."""
        identifier = _identifier(event_id, "event_id")
        resolved_at = _finite_time(now, "now")
        if type(action) is not str or action not in _RESOLUTION_ACTIONS:
            raise ValueError(f"action must be one of {', '.join(sorted(_RESOLUTION_ACTIONS))}")
        with self._lock, self._connection:
            self._ensure_open()
            row = self._connection.execute(
                "SELECT alert_state FROM alerts WHERE event_id = ?", (identifier,)
            ).fetchone()
            if row is None:
                raise KeyError(identifier)
            if row["alert_state"] in _FINAL_STATES and row["alert_state"] == action:
                return
            self._connection.execute(
                "UPDATE alerts SET alert_state = ?, snoozed_until = NULL, updated_at = ? WHERE event_id = ?",
                (action, resolved_at, identifier),
            )

    def snooze(self, event_id: str, until: float) -> None:
        """Defer an open alert until a finite timestamp; pending() reactivates it at expiry."""
        identifier = _identifier(event_id, "event_id")
        snoozed_until = _finite_time(until, "until")
        with self._lock, self._connection:
            self._ensure_open()
            row = self._connection.execute(
                "SELECT alert_state FROM alerts WHERE event_id = ?", (identifier,)
            ).fetchone()
            if row is None:
                raise KeyError(identifier)
            if row["alert_state"] in _FINAL_STATES:
                raise ValueError("resolved alerts cannot be snoozed")
            self._connection.execute(
                "UPDATE alerts SET alert_state = 'snoozed', snoozed_until = ? WHERE event_id = ?",
                (snoozed_until, identifier),
            )

    def get(self, event_id: str) -> dict[str, Any] | None:
        identifier = _identifier(event_id, "event_id")
        with self._lock:
            self._ensure_open()
            row = self._connection.execute(
                "SELECT * FROM alerts WHERE event_id = ?", (identifier,)
            ).fetchone()
        return self._decode(row) if row else None

    def close(self) -> None:
        with self._lock:
            if not self._closed:
                self._connection.close()
                self._closed = True
