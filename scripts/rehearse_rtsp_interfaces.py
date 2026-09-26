#!/usr/bin/env python3
"""Bounded, loopback-only RTSP interface rehearsal using synthetic fixtures."""

from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import json
import os
import re
import socket
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


SCOPE = "LOCAL_RTSP_TRANSPORT_ONLY"
MAX_DURATION_SECONDS = 90
CHANNELS = 9
MIN_DECODED_FRAMES = 5


def validate_duration(seconds: int) -> int:
    if seconds < 1 or seconds > MAX_DURATION_SECONDS:
        raise ValueError(f"duration must be between 1 and {MAX_DURATION_SECONDS} seconds")
    return seconds


def require_time_remaining(deadline: float, *, now: float | None = None, cap: float = 5.0) -> float:
    remaining = deadline - (time.monotonic() if now is None else now)
    if remaining <= 0:
        raise TimeoutError("total_work_budget_expired")
    return min(cap, remaining)


def is_success(protocol_checks: bool, cleanup_succeeded: bool, all_processes_exited: bool,
               elapsed_seconds: float, duration_limit: float) -> bool:
    return protocol_checks and cleanup_succeeded and all_processes_exited and elapsed_seconds <= duration_limit


def filtered_child_environment(environ: dict[str, str]) -> dict[str, str]:
    return {key: value for key, value in environ.items() if not key.startswith("MTX_")}


def build_mediamtx_config(port: int) -> str:
    if not 1 <= port <= 65535:
        raise ValueError("RTSP port is outside the valid range")
    paths = "\n".join(f"  ch{index:02d}_{role}: {{}}" for index in range(1, CHANNELS + 1) for role in ("main", "sub"))
    return (
        "logLevel: warn\n"
        "logDestinations: [stdout]\n"
        "rtsp: yes\n"
        f"rtspAddress: 127.0.0.1:{port}\n"
        "rtspTransports: [tcp]\n"
        "rtmp: no\n"
        "hls: no\n"
        "webrtc: no\n"
        "srt: no\n"
        "moq: no\n"
        "api: no\n"
        "metrics: no\n"
        "pprof: no\n"
        "playback: no\n"
        "pathDefaults:\n"
        "  source: publisher\n"
        "paths:\n"
        f"{paths}\n"
    )


def cleanup_processes(processes: Iterable[Any], grace_seconds: float = 2.0,
                      kill_grace_seconds: float = 0.5) -> bool:
    owned = list(processes)
    deadline = time.monotonic() + grace_seconds
    for process in owned:
        try:
            if process.poll() is None:
                process.terminate()
        except (OSError, ProcessLookupError):
            pass
    while time.monotonic() < deadline:
        if all(process.poll() is not None for process in owned):
            return True
        time.sleep(min(0.02, max(0.0, deadline - time.monotonic())))
    pending = []
    for process in owned:
        try:
            if process.poll() is None:
                pending.append(process)
        except (OSError, ProcessLookupError):
            pass
    for process in pending:
        try:
            process.kill()
        except (OSError, ProcessLookupError):
            pass
    kill_deadline = time.monotonic() + kill_grace_seconds
    while time.monotonic() < kill_deadline:
        if all(process.poll() is not None for process in pending):
            break
        time.sleep(min(0.02, max(0.0, kill_deadline - time.monotonic())))
    return all(process.poll() is not None for process in owned)


class ManagedTemporaryDirectory:
    """Keep a server config alive until all processes using it have exited."""

    def __init__(self, processes: list[Any], prefix: str, directory: Path,
                 grace_seconds: float, kill_grace_seconds: float):
        self.processes = processes
        self.prefix = prefix
        self.directory = directory
        self.grace_seconds = grace_seconds
        self.kill_grace_seconds = kill_grace_seconds
        self._temporary_directory: tempfile.TemporaryDirectory[str] | None = None
        self.cleanup_attempted = False
        self.cleanup_succeeded = False

    def __enter__(self) -> str:
        self._temporary_directory = tempfile.TemporaryDirectory(prefix=self.prefix, dir=self.directory)
        return self._temporary_directory.name

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        self.cleanup_attempted = True
        self.cleanup_succeeded = cleanup_processes(self.processes, self.grace_seconds, self.kill_grace_seconds)
        if self._temporary_directory is not None:
            self._temporary_directory.cleanup()


def summarize_states(states: list[dict[str, Any]]) -> dict[str, int]:
    return {
        "path_count": len(states),
        "probe_ok_count": sum(bool(item.get("probe_ok")) for item in states),
        "decoded_path_count": sum(int(item.get("decoded_frames", 0)) > 0 for item in states),
        "decoded_frame_count": sum(int(item.get("decoded_frames", 0)) for item in states),
    }


def reserve_loopback_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def run_command(args: list[str], timeout: float, *, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    if timeout <= 0:
        raise TimeoutError("command_started_after_work_deadline")
    return subprocess.run(args, capture_output=True, text=True, timeout=timeout, cwd=cwd, check=False)


def command_version(executable: str, timeout: float, version_flag: str = "-version") -> str:
    result = run_command([executable, version_flag], timeout)
    line = (result.stdout or result.stderr).splitlines()
    return line[0][:180] if line else f"exit_{result.returncode}"


def publisher_command(ffmpeg: str, url: str, role: str) -> list[str]:
    if role == "main":
        fixture, size, rate, keyint = "testsrc2", "640x360", "10", "10"
    else:
        fixture, size, rate, keyint = "smptebars", "320x180", "5", "5"
    source = f"{fixture}=size={size}:rate={rate}"
    return [
        ffmpeg, "-hide_banner", "-loglevel", "error", "-re", "-f", "lavfi", "-i", source,
        "-an", "-threads", "1", "-filter_threads", "1", "-c:v", "libx264", "-preset", "ultrafast", "-tune", "zerolatency",
        "-pix_fmt", "yuv420p", "-g", keyint, "-f", "rtsp", "-rtsp_transport", "tcp", url,
    ]


def probe_stream(ffprobe: str, url: str, timeout: float) -> dict[str, Any]:
    try:
        result = run_command([
            ffprobe, "-v", "error", "-analyzeduration", "1000000", "-probesize", "1000000",
            "-rtsp_transport", "tcp", "-select_streams", "v:0",
            "-show_entries", "stream=codec_name,width,height,avg_frame_rate", "-of", "json", url,
        ], timeout)
    except subprocess.TimeoutExpired:
        return {"probe_ok": False, "reason": "ffprobe_timeout", "timed_out": True}
    if result.returncode != 0:
        return {"probe_ok": False, "reason": "ffprobe_exit"}
    try:
        streams = json.loads(result.stdout).get("streams", [])
    except (json.JSONDecodeError, AttributeError):
        streams = []
    if len(streams) != 1:
        return {"probe_ok": False, "reason": "video_stream_missing"}
    stream = streams[0]
    return {
        "probe_ok": stream.get("codec_name") == "h264",
        "codec": stream.get("codec_name"),
        "width": stream.get("width"),
        "height": stream.get("height"),
        "avg_frame_rate": stream.get("avg_frame_rate"),
    }


def decode_frames(ffmpeg: str, url: str, frames: int, timeout: float) -> dict[str, Any]:
    try:
        result = run_command([
            ffmpeg, "-hide_banner", "-loglevel", "error", "-nostats", "-progress", "pipe:1",
            "-rtsp_transport", "tcp", "-timeout", "1500000", "-threads", "1", "-filter_threads", "1",
            "-analyzeduration", "1000000", "-probesize", "1000000", "-i", url,
            "-an", "-frames:v", str(frames), "-f", "null", "-",
        ], timeout)
    except subprocess.TimeoutExpired:
        return {"decoded_frames": 0, "exit_code": None, "timed_out": True}
    seen = re.findall(r"(?m)^frame=(\d+)\s*$", result.stdout or "")
    decoded = max((int(value) for value in seen), default=0)
    return {"decoded_frames": decoded, "exit_code": result.returncode, "timed_out": False}


def wait_until_ready(ffprobe: str, urls: list[tuple[str, str]], deadline: float) -> list[dict[str, Any]]:
    last: list[dict[str, Any]] = []
    while time.monotonic() < deadline:
        budget = min(5.0, deadline - time.monotonic())
        if budget <= 0:
            break
        with concurrent.futures.ThreadPoolExecutor(max_workers=len(urls)) as pool:
            futures = [pool.submit(probe_stream, ffprobe, url, budget) for _, url in urls]
            last = [future.result() for future in futures]
        if all(item.get("probe_ok") for item in last):
            return last
        time.sleep(min(0.25, max(0.0, deadline - time.monotonic())))
    return last


def read_all(ffmpeg: str, urls: list[tuple[str, str]], timeout: float) -> list[dict[str, Any]]:
    read_deadline = time.monotonic() + timeout
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(urls)) as pool:
        futures = [pool.submit(decode_frames, ffmpeg, url, MIN_DECODED_FRAMES, timeout) for _, url in urls]
        output = []
        for (path, _), future in zip(urls, futures):
            future_timeout = read_deadline - time.monotonic()
            try:
                item = future.result(timeout=max(0.0, future_timeout))
            except concurrent.futures.TimeoutError:
                item = {"decoded_frames": 0, "exit_code": None, "timed_out": True}
            item["path"] = path
            output.append(item)
    return output


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def rehearse(args: argparse.Namespace) -> dict[str, Any]:
    duration = validate_duration(args.duration)
    mediamtx = str(Path(args.mediamtx).resolve(strict=True))
    ffmpeg = str(Path(args.ffmpeg).resolve(strict=True))
    ffprobe = str(Path(args.ffprobe).resolve(strict=True))
    output = Path(args.output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    started_monotonic = time.monotonic()
    deadline = started_monotonic + duration
    cleanup_grace = min(2.0, duration * 0.4)
    kill_grace = min(0.5, cleanup_grace * 0.25)
    work_deadline = deadline - cleanup_grace - kill_grace - 0.1
    port = reserve_loopback_port()
    processes: list[subprocess.Popen[bytes]] = []
    process_records: list[dict[str, Any]] = []
    child_env = filtered_child_environment(dict(os.environ))
    states: list[dict[str, Any]] = []
    overall_ok = False
    failure = None
    mediamtx_version = "unknown"
    ffmpeg_version = "unknown"
    ffprobe_version = "unknown"
    outage: dict[str, Any] = {
        "detected": False,
        "new_consumer_after_source_recovery": False,
        "persistent_consumer_auto_reconnect_tested": False,
        "recovery_method": "restart_fixture_then_new_consumer_session",
        "other_paths_readable": 0,
    }
    cleanup_succeeded = False
    temp_workspace: ManagedTemporaryDirectory | None = None
    config_text = build_mediamtx_config(port)
    urls = [(f"ch{index:02d}_{role}", f"rtsp://127.0.0.1:{port}/ch{index:02d}_{role}")
            for index in range(1, CHANNELS + 1) for role in ("main", "sub")]

    def start_owned(label: str, command: list[str]) -> subprocess.Popen[bytes]:
        process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                   stderr=subprocess.DEVNULL, env=child_env)
        processes.append(process)
        process_records.append({"role": label, "pid": process.pid, "exit_code": None})
        return process

    def remaining(cap: float = 5.0) -> float:
        return require_time_remaining(work_deadline, cap=cap)

    try:
        mediamtx_version = command_version(mediamtx, remaining(4), "--version")
        ffmpeg_version = command_version(ffmpeg, remaining(4))
        ffprobe_version = command_version(ffprobe, remaining(4))
        temp_workspace = ManagedTemporaryDirectory(processes, "rtsp-rehearsal-", output, cleanup_grace, kill_grace)
        with temp_workspace as temp:
            config_path = Path(temp) / "mediamtx.yml"
            config_path.write_text(config_text, encoding="utf-8")
            server = start_owned("mediamtx", [mediamtx, str(config_path)])
            time.sleep(min(0.6, remaining()))
            if server.poll() is not None:
                raise RuntimeError("local_rtsp_server_exited_during_startup")
            producers: dict[str, subprocess.Popen[bytes]] = {}
            producer_commands: dict[str, list[str]] = {}
            for path, url in urls:
                remaining()
                role = path.rsplit("_", 1)[1]
                command = publisher_command(ffmpeg, url, role)
                producer_commands[path] = command
                producers[path] = start_owned(f"publisher:{path}", command)
            ready = wait_until_ready(ffprobe, urls, min(work_deadline, time.monotonic() + remaining(12)))
            for (path, _), probe in zip(urls, ready):
                item = {"path": path, **probe}
                states.append(item)
            if len(ready) != len(urls) or not all(item.get("probe_ok") for item in ready):
                raise RuntimeError("not_all_named_rtsp_paths_became_ready")
            for path, probe in zip((path for path, _ in urls), ready):
                role = path.rsplit("_", 1)[1]
                expected = (640, 360, "10/1") if role == "main" else (320, 180, "5/1")
                observed = (probe.get("width"), probe.get("height"), probe.get("avg_frame_rate"))
                if observed != expected:
                    raise RuntimeError(f"fixture_role_mismatch:{path}")

            first_reads = read_all(ffmpeg, urls, remaining(8))
            state_by_path = {item["path"]: item for item in states}
            for read in first_reads:
                state_by_path[read["path"]].update(read)
            if any(item.get("decoded_frames", 0) < MIN_DECODED_FRAMES or item.get("exit_code") != 0 for item in first_reads):
                raise RuntimeError("concurrent_decode_failed")

            target_path, target_url = urls[0]
            target_publisher = producers[target_path]
            target_publisher.terminate()
            while target_publisher.poll() is None and time.monotonic() < work_deadline:
                time.sleep(min(0.02, max(0.0, work_deadline - time.monotonic())))
            if target_publisher.poll() is None:
                raise TimeoutError("synthetic_publisher_stop_timeout")
            outage_read = decode_frames(ffmpeg, target_url, 15, min(4.0, remaining()))
            outage["detected"] = bool(outage_read["exit_code"] not in (0, None) or outage_read["decoded_frames"] < 15 or outage_read["timed_out"])
            outage["outage_consumer_decoded_frames"] = outage_read["decoded_frames"]
            other_urls = urls[1:]
            other_reads = read_all(ffmpeg, other_urls, remaining(8))
            outage["other_paths_readable"] = sum(
                item.get("decoded_frames", 0) >= MIN_DECODED_FRAMES and item.get("exit_code") == 0 for item in other_reads
            )
            if not outage["detected"]:
                raise RuntimeError("fresh_consumer_received_unexpected_frames_during_fixture_outage")
            if outage["other_paths_readable"] != len(other_urls):
                raise RuntimeError("uninterrupted_paths_not_readable_during_outage")
            producers[target_path] = start_owned(f"publisher_recovery:{target_path}", producer_commands[target_path])
            outage["new_consumer_after_source_recovery"] = True
            recovered_probe = wait_until_ready(ffprobe, [(target_path, target_url)], min(work_deadline, time.monotonic() + remaining(8)))
            recovery = decode_frames(ffmpeg, target_url, MIN_DECODED_FRAMES, remaining(8))
            outage["recovered_probe_ok"] = bool(recovered_probe and recovered_probe[0].get("probe_ok"))
            outage["recovered_decoded_frames"] = recovery["decoded_frames"]
            if not outage["recovered_probe_ok"] or recovery["decoded_frames"] < MIN_DECODED_FRAMES or recovery["exit_code"] != 0:
                raise RuntimeError("new_consumer_after_source_recovery_failed")
            overall_ok = True
    except Exception as exc:  # Keep a bounded failure artifact; do not expose process logs.
        failure = str(exc).split("\n", 1)[0][:120]
    finally:
        if temp_workspace is not None and temp_workspace.cleanup_attempted:
            cleanup_succeeded = temp_workspace.cleanup_succeeded
        else:
            cleanup_succeeded = cleanup_processes(processes, cleanup_grace, kill_grace)
        for record, process in zip(process_records, processes):
            record["exit_code"] = process.poll()

    all_processes_exited = all(record["exit_code"] is not None for record in process_records)
    elapsed_seconds = time.monotonic() - started_monotonic
    if not cleanup_succeeded:
        failure = "owned_process_cleanup_failed"
    elif elapsed_seconds > duration:
        failure = "total_duration_exceeded"
    report = {
        "scope": SCOPE,
        "result": "PASS" if is_success(overall_ok, cleanup_succeeded, all_processes_exited, elapsed_seconds, duration) else "FAIL",
        "hardware_benchmark": False,
        "action_accuracy_evidence": False,
        "field_acceptance_evidence": False,
        "real_camera_or_user_recording_used": False,
        "generated_monitoring_demo_or_ui": False,
        "model_invoked": False,
        "duration_limit_seconds": duration,
        "elapsed_seconds": round(elapsed_seconds, 3),
        "completed_at_utc": datetime.now(timezone.utc).isoformat(),
        "script_sha256": sha256_file(Path(__file__).resolve()),
        "platform": {"system": sys.platform, "machine": os.uname().machine if hasattr(os, "uname") else "unknown"},
        "tools": {"mediamtx": mediamtx_version, "ffmpeg": ffmpeg_version, "ffprobe": ffprobe_version,
                  "mediamtx_sha256": sha256_file(Path(mediamtx))},
        "server": {"bind": "127.0.0.1", "rtsp_transport": "tcp", "port": port,
                   "inherited_mtx_environment_overrides_removed": True,
                   "other_services_disabled": ["rtmp", "hls", "webrtc", "srt", "moq", "api", "metrics", "pprof", "playback"]},
        "fixture": {"channels": CHANNELS, "named_paths": len(urls), "publisher_processes_started": sum(r["role"].startswith("publisher:") for r in process_records),
                    "independent_real_devices": 0, "main": "testsrc2 640x360 10fps h264", "sub": "smptebars 320x180 5fps h264",
        "note": "Synthetic per-path encoders share one main-role fixture and one sub-role fixture across nine channel labels; no unique scene identity, independent camera devices, or hardware-load equivalence."},
        "counters": summarize_states(states),
        "paths": [{key: item.get(key) for key in ("path", "probe_ok", "codec", "width", "height", "avg_frame_rate", "decoded_frames", "exit_code")}
                  for item in states],
        "interruption_recovery": outage,
        "owned_subprocesses": process_records,
        "all_owned_subprocesses_exited": all(record["exit_code"] is not None for record in process_records),
        "cleanup_succeeded": cleanup_succeeded,
        "failure_reason": failure,
    }
    report_path = output / "interface-rehearsal-report.json"
    report_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    elapsed_seconds = time.monotonic() - started_monotonic
    report["elapsed_seconds"] = round(elapsed_seconds, 3)
    if elapsed_seconds > duration:
        report["result"] = "FAIL"
        report["failure_reason"] = "total_duration_exceeded"
    report["completed_at_utc"] = datetime.now(timezone.utc).isoformat()
    report_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return report


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mediamtx", required=True, help="path to the verified official standalone binary")
    parser.add_argument("--ffmpeg", required=True, help="path to FFmpeg")
    parser.add_argument("--ffprobe", required=True, help="path to ffprobe")
    parser.add_argument("--output", required=True, help="directory for the small synthetic JSON report")
    parser.add_argument("--duration", type=int, default=60, help="hard wall-clock budget in seconds (maximum 90)")
    args = parser.parse_args(argv)
    validate_duration(args.duration)
    return args


def main(argv: list[str] | None = None) -> int:
    try:
        args = parse_args(argv)
        report = rehearse(args)
    except (ValueError, OSError) as exc:
        print(f"configuration error: {exc}", file=sys.stderr)
        return 2
    print(f"{report['result']} {report['scope']} report={Path(args.output) / 'interface-rehearsal-report.json'}")
    return 0 if report["result"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
