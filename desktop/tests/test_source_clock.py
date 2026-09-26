from datetime import datetime, timedelta

from factory_monitor_desktop.source_clock import ClockTracker, parse_camera_datetime

START = datetime(2026, 9, 26, 19, 20, 0)


def stamp(seconds):
    return START + timedelta(seconds=seconds)


def observe(tracker, *samples):
    return [tracker.observe(value, elapsed) for value, elapsed in samples]


def test_parser_accepts_one_complete_ascii_timestamp_and_rejects_ambiguity():
    expected = datetime(2026, 9, 26, 19, 20, 7)
    for text in ("2026-09-26 星期六 19:20:07", "2026-09-26 E# 19:20:07",
                 "标题 2026-09-26\n星期六 19:20:07"):
        assert parse_camera_datetime(text) == expected
    for text in ("2026-09-26 19:20:0O", "2026-09-26 E# 19:20:0O", "2026-02-30 19:20:07",
                 "2026-09-26 25:20:07", "2026-09-26", "19:20:07",
                 "2026-09-26 19:20:07 / 2026-09-26 19:20:08",
                 "2026-09-26 19:20:07 and 19:20:08", "2026-09-26 19:20:07 2026-09-27"):
        assert parse_camera_datetime(text) is None


def test_tracker_requires_consistent_motion_and_rebaselines_fast_slow_or_reversed_samples():
    tracker = ClockTracker()
    assert observe(tracker, (stamp(0), 0), (stamp(5), 5), (stamp(10), 10)) == [
        "unknown", "advancing", "advancing"]
    for delta in (2, 10, -3):
        tracker = ClockTracker(tolerance_seconds=1)
        assert observe(tracker, (stamp(0), 0), (stamp(delta), 5), (stamp(delta + 5), 10)) == [
            "unknown", "unknown", "advancing"]


def test_tracker_stall_resume_long_gap_and_unreadable_reset():
    tracker = ClockTracker(stall_seconds=10)
    assert observe(tracker, (stamp(0), 0), (stamp(0), 5), (stamp(0), 10), (stamp(0), 15),
                   (stamp(1), 16)) == ["unknown", "unknown", "suspected_stalled",
                                      "suspected_stalled", "advancing"]
    assert observe(tracker, (stamp(30), 30), (stamp(35), 35)) == ["unknown", "advancing"]
    assert observe(tracker, (None, 36), (stamp(40), 40), (stamp(45), 45)) == [
        "unknown", "unknown", "advancing"]


def test_tracker_handles_midnight_and_rejects_cumulative_half_or_double_speed():
    tracker = ClockTracker()
    assert observe(tracker, (datetime(2026, 9, 26, 23, 59, 58), 0),
                   (datetime(2026, 9, 27, 0, 0, 3), 5)) == ["unknown", "advancing"]
    for rate, drift_index in ((0.5, 2), (2.0, 1)):
        tracker = ClockTracker(tolerance_seconds=2)
        assert tracker.observe(stamp(0), 0) == "unknown"
        states = [tracker.observe(stamp(round(t * rate)), t) for t in (2, 4, 6, 8, 10)]
        assert states[drift_index] == "unknown"


def test_tracker_resets_on_invalid_or_reversed_elapsed_clock():
    for bad in (float("nan"), float("inf"), -1):
        tracker = ClockTracker()
        assert observe(tracker, (stamp(0), 0), (stamp(5), 5)) == ["unknown", "advancing"]
        assert observe(tracker, (stamp(10), bad), (stamp(15), 10), (stamp(20), 15)) == [
            "unknown", "unknown", "advancing"]
    tracker = ClockTracker()
    assert observe(tracker, (stamp(0), 0), (stamp(5), 5), (stamp(10), 4), (stamp(15), 9)) == [
        "unknown", "advancing", "unknown", "advancing"]
