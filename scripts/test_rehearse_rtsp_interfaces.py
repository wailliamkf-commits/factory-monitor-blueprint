import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPT_PATH = Path(__file__).with_name("rehearse_rtsp_interfaces.py")
SPEC = importlib.util.spec_from_file_location("rehearse_rtsp_interfaces", SCRIPT_PATH)
rehearsal = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(rehearsal)


class RehearseRtspInterfacesTests(unittest.TestCase):
    def test_duration_is_bounded_to_ninety_seconds(self):
        with self.assertRaises(ValueError):
            rehearsal.validate_duration(91)
        with self.assertRaises(ValueError):
            rehearsal.validate_duration(0)
        self.assertEqual(rehearsal.validate_duration(30), 30)

    def test_expired_work_budget_raises_instead_of_granting_grace_time(self):
        with self.assertRaises(TimeoutError):
            rehearsal.require_time_remaining(0.0, now=0.1, cap=5.0)

    def test_success_requires_clean_exit_and_total_time_within_budget(self):
        self.assertFalse(rehearsal.is_success(True, False, True, 10.0, 60.0))
        self.assertFalse(rehearsal.is_success(True, True, True, 60.01, 60.0))
        self.assertTrue(rehearsal.is_success(True, True, True, 59.99, 60.0))

    def test_child_environment_drops_media_mtx_overrides_only(self):
        result = rehearsal.filtered_child_environment({"PATH": "/bin", "MTX_RTSPADDRESS": ":8554", "MTX_PATHDEFAULTS_SOURCE": "rtsp://external"})
        self.assertEqual(result, {"PATH": "/bin"})

    def test_server_config_is_loopback_tcp_and_disables_other_services(self):
        config = rehearsal.build_mediamtx_config(18554)
        self.assertIn("rtspAddress: 127.0.0.1:18554", config)
        self.assertIn("rtspTransports: [tcp]", config)
        for setting in ("rtmp: no", "hls: no", "webrtc: no", "srt: no", "api: no", "metrics: no"):
            self.assertIn(setting, config)
        self.assertNotIn("0.0.0.0", config)

    def test_cleanup_terminates_every_owned_process(self):
        class FakeProcess:
            def __init__(self):
                self.terminated = False
                self.killed = False

            def poll(self):
                return None if not self.terminated else 0

            def terminate(self):
                self.terminated = True

            def wait(self, timeout=None):
                if not self.terminated:
                    raise rehearsal.subprocess.TimeoutExpired("fake", timeout)
                return 0

            def kill(self):
                self.killed = True

        first, second = FakeProcess(), FakeProcess()
        rehearsal.cleanup_processes([first, second], grace_seconds=0.1)
        self.assertTrue(first.terminated)
        self.assertTrue(second.terminated)
        self.assertFalse(first.killed)
        self.assertFalse(second.killed)

    def test_cleanup_kills_a_process_that_ignores_terminate(self):
        class StubbornProcess:
            def __init__(self):
                self.killed = False

            def poll(self):
                return None

            def terminate(self):
                pass

            def wait(self, timeout=None):
                raise rehearsal.subprocess.TimeoutExpired("stubborn", timeout)

            def kill(self):
                self.killed = True

        process = StubbornProcess()
        cleaned = rehearsal.cleanup_processes([process], grace_seconds=0.01)
        self.assertTrue(process.killed)
        self.assertFalse(cleaned)

    def test_config_directory_survives_until_owned_process_cleanup(self):
        class ConfigReaderProcess:
            def __init__(self, config_path):
                self.config_path = config_path
                self.running = True
                self.config_existed_at_terminate = False

            def poll(self):
                return None if self.running else 0

            def terminate(self):
                self.config_existed_at_terminate = self.config_path.exists()
                self.running = False

            def wait(self, timeout=None):
                return 0

            def kill(self):
                self.running = False

        process = None
        with tempfile.TemporaryDirectory() as parent:
            managed = rehearsal.ManagedTemporaryDirectory([], "rtsp-test-", Path(parent), 0.1, 0.1)
            with managed as temp_dir:
                config_path = Path(temp_dir) / "server.yml"
                config_path.write_text("config", encoding="utf-8")
                process = ConfigReaderProcess(config_path)
                managed.processes.append(process)
            self.assertFalse(Path(temp_dir).exists())
        self.assertTrue(managed.cleanup_succeeded)
        self.assertTrue(process.config_existed_at_terminate)

    def test_report_counters_only_count_decoded_frames(self):
        states = [
            {"path": "ch01_main", "decoded_frames": 5, "probe_ok": True},
            {"path": "ch01_sub", "decoded_frames": 0, "probe_ok": True},
        ]
        counters = rehearsal.summarize_states(states)
        self.assertEqual(counters["path_count"], 2)
        self.assertEqual(counters["probe_ok_count"], 2)
        self.assertEqual(counters["decoded_path_count"], 1)
        self.assertEqual(counters["decoded_frame_count"], 5)

    def test_command_timeout_stops_the_child_process(self):
        with self.assertRaises(rehearsal.subprocess.TimeoutExpired):
            rehearsal.run_command([sys.executable, "-c", "import time; time.sleep(5)"], timeout=0.05)


if __name__ == "__main__":
    unittest.main()
