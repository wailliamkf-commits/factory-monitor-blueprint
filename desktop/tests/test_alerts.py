import math

import pytest

from factory_monitor_desktop.alerts import AlertInbox


def event(event_id: str, timestamp: float = 100.0) -> dict:
    return {
        "id": event_id,
        "camera_id": "CAM01",
        "kind": "material_candidate",
        "triggered_at": timestamp,
        "status": "candidate",
        "reason": "entered_material_roi",
        "analysis_status": "pending",
    }


def test_observe_is_idempotent_but_merges_a_real_analysis_change(tmp_path):
    inbox = AlertInbox(tmp_path / "alerts.sqlite3")
    try:
        first = event("evt-1", 100)
        assert inbox.observe(first, 101.0) is True
        assert inbox.observe(first, 102.0) is False

        assert inbox.observe({"id": "evt-1", "analysis_status": "timeout", "analysis": {"error": "deadline"}}, 103.0) is True
        item = inbox.get("evt-1")
        assert type(item["triggered_at"]) is int
        assert item["camera_id"] == "CAM01"
        assert item["analysis_status"] == "timeout"
        assert item["analysis"]["error"] == "deadline"
        assert inbox.observe({"id": "evt-1", "analysis_status": "timeout", "analysis": {"error": "deadline"}}, 104.0) is False
    finally:
        inbox.close()


@pytest.mark.parametrize("action", ["acknowledged", "confirmed", "false_alarm"])
def test_late_runtime_update_never_reopens_a_resolved_alert(tmp_path, action):
    inbox = AlertInbox(tmp_path / "alerts.sqlite3")
    try:
        inbox.observe(event("evt-1"), 101.0)
        inbox.resolve("evt-1", action, 102.0)

        assert inbox.observe({"id": "evt-1", "analysis_status": "supported", "analysis": {"reason": "visible facts"}}, 103.0)
        item = inbox.get("evt-1")
        assert item["analysis_status"] == "supported"
        assert item["alert_state"] == action
        assert inbox.pending(103.0) == []
    finally:
        inbox.close()


def test_restart_preserves_fifo_order_and_resolved_state(tmp_path):
    database = tmp_path / "alerts.sqlite3"
    first = AlertInbox(database)
    first.observe(event("evt-first"), 101.0)
    first.observe(event("evt-second"), 102.0)
    first.resolve("evt-first", "acknowledged", 103.0)
    first.close()

    reopened = AlertInbox(database)
    try:
        rows = reopened.pending(104.0)
        assert [row["id"] for row in rows] == ["evt-second"]
        assert rows[0]["alert_state"] == "pending"
        assert reopened.get("evt-first")["alert_state"] == "acknowledged"
    finally:
        reopened.close()


def test_snooze_survives_restart_and_returns_to_pending_at_expiry(tmp_path):
    database = tmp_path / "alerts.sqlite3"
    first = AlertInbox(database)
    first.observe(event("evt-snoozed"), 101.0)
    first.snooze("evt-snoozed", 200.0)
    first.close()

    reopened = AlertInbox(database)
    try:
        before = reopened.pending(199.0)
        assert before[0]["alert_state"] == "snoozed"
        assert before[0]["snoozed_until"] == 200.0

        after = reopened.pending(200.0)
        assert after[0]["alert_state"] == "pending"
        assert after[0]["snoozed_until"] is None
    finally:
        reopened.close()


def test_pending_is_fifo_and_query_limit_is_bounded(tmp_path):
    inbox = AlertInbox(tmp_path / "alerts.sqlite3")
    try:
        for index in range(4):
            inbox.observe(event(f"evt-{index}", 100.0 + index), 110.0 + index)

        rows = inbox.pending(120.0, limit=2)
        assert [row["id"] for row in rows] == ["evt-0", "evt-1"]
        with pytest.raises(ValueError):
            inbox.pending(120.0, limit=0)
        with pytest.raises(ValueError):
            inbox.pending(120.0, limit=10001)
    finally:
        inbox.close()


def test_invalid_identity_times_actions_and_missing_ids_are_rejected(tmp_path):
    inbox = AlertInbox(tmp_path / "alerts.sqlite3")
    try:
        with pytest.raises(ValueError):
            inbox.observe({"id": "", "camera_id": "CAM01", "kind": "x", "triggered_at": 1.0}, 1.0)
        with pytest.raises(ValueError):
            inbox.observe(event("evt-bad-time", math.inf), 1.0)
        with pytest.raises(ValueError):
            inbox.observe(event("evt-bad-now"), math.nan)
        with pytest.raises(ValueError):
            inbox.resolve("missing", "delete", 1.0)
        with pytest.raises(ValueError):
            inbox.resolve("missing", [], 1.0)
        with pytest.raises(KeyError):
            inbox.resolve("missing", "acknowledged", 1.0)
        with pytest.raises(KeyError):
            inbox.snooze("missing", 10.0)
        assert inbox.get("missing") is None
    finally:
        inbox.close()
