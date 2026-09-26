# Local RTSP interface rehearsal

This rehearsal is scoped to **`LOCAL_RTSP_TRANSPORT_ONLY`**. It checks RTSP/TCP routing, stream metadata, actual H.264 decoding, a publisher interruption, continued reads from other paths, and recovery reads through a newly created consumer session. It does not test persistent-client reconnect, camera hardware, 4060 performance, model behavior, action accuracy, deployment on Windows, or field acceptance. Windows behavior remains unverified.

## Evidence

The machine-readable result is [interface-rehearsal-report.json](interface-rehearsal-report.json). It records the exact script SHA256, tool versions, per-path codec/resolution/rate, decoded frame counts, interruption results, and exit status for every owned process.

The separate empty deployment configuration smoke test is documented in [empty-config-smoke.json](empty-config-smoke.json): MediaMTX listened only at `127.0.0.1:18554/TCP`, returned `200 OK` to RTSP OPTIONS and rejected an unconfigured path with `400 Bad Request`. That test had no source, recording, or model.

## Reproduction

Prerequisites are Python 3, FFmpeg with `libx264`, `ffprobe`, and a locally extracted MediaMTX standalone binary. The rehearsal takes explicit executable paths and writes only the JSON report to the selected output directory. This run used the following command on macOS arm64:

```bash
python3 scripts/rehearse_rtsp_interfaces.py \
  --mediamtx evidence/local/interface-rehearsal-tools/mediamtx \
  --ffmpeg /opt/homebrew/bin/ffmpeg \
  --ffprobe /opt/homebrew/bin/ffprobe \
  --output evidence/interface-rehearsal \
  --duration 60
```

The script binds the server to a dynamically selected `127.0.0.1` port and enables RTSP/TCP only. It disables RTMP, HLS, WebRTC, SRT, MoQ, API, metrics, pprof, and playback. It starts one synthetic FFmpeg publisher for each of 18 paths (`ch01_main` through `ch09_sub`) and concurrently probes and decodes five frames from every path. Main uses `testsrc2` at 640×360/10 fps; sub uses `smptebars` at 320×180/5 fps. Those two role fixtures are reused across the nine channel labels: the paths are not nine unique scenes or nine camera devices, and this is not a hardware-load comparison.

For interruption recovery, the script stops its own `ch01_main` publisher, starts a consumer during the outage and confirms zero decoded frames, reads the other 17 paths, restarts the synthetic publisher, then starts a separate consumer session and confirms five decoded frames. This demonstrates source-interruption detection and a new-session recovery read; it does not test an already running application client's reconnect behavior.

The wall-clock budget is capped at 90 seconds (60 seconds for the recorded run), with a shared work deadline that reserves time for one bounded terminate-then-kill cleanup pass. Inherited `MTX_*` environment overrides are removed from child processes so an ambient MediaMTX setting cannot change the loopback-only configuration. An expired work budget, a failed decode, a missing path, or a process that cannot be confirmed exited makes the result `FAIL`. No port scan or external address is used. The process records in the report are the readback evidence for process cleanup.

## Recorded run

- MediaMTX `v1.21.1`, FFmpeg and ffprobe `9.0.1`; macOS arm64. The script SHA256 is recorded in the report.
- The official standalone asset `mediamtx_v1.21.1_darwin_arm64.tar.gz` came from the [MediaMTX v1.21.1 release](https://github.com/bluenviron/mediamtx/releases/tag/v1.21.1). Its GitHub Release API digest was `sha256:25e20ed41611f1f3103b8359585210b29b11b69fa0d9e11bd11b92f7bbcb42ef`; the downloaded archive matched before extraction. The [official standalone installation guide](https://mediamtx.org/docs/kickoff/install) documents this binary route. MediaMTX's [RTSP camera/server guide](https://mediamtx.org/docs/publish/rtsp-cameras-and-servers) describes connecting streams through the server.
- The local test passed in 9.869 seconds: all 18 paths probed and decoded five frames (90 total); a new consumer during the source interruption decoded zero frames; the other 17 paths remained readable; after the test publisher restarted, a separate new consumer decoded five frames.
- All 20 owned processes (server and publishers, including the restarted publisher) exited and cleanup succeeded. No source videos, credentials, camera URLs, process logs, or model outputs are included.
- The focused test command `python3 -m unittest scripts.test_rehearse_rtsp_interfaces` passed 10 tests, including budget expiry, configuration restrictions, process cleanup ordering, and success-state accounting.

MediaMTX supports proxying requests from a local RTSP path to a confirmed upstream RTSP source; see its [official proxy documentation](https://mediamtx.org/docs/features/proxy). This rehearsal did not configure an upstream source or validate a real NVR address.

Cleanup receipt (2026-09-26): after the final run and root review, Finder moved `mediamtx_v1.21.1_darwin_arm64.tar.gz`, the extracted `mediamtx` executable, and the two one-path diagnostic configs generated during this run to the user's Trash; Trash was not emptied. The release archive also contains `LICENSE` and `mediamtx.yml`, and the extraction command wrote both names into the ignored tools directory. There was no pre-extraction directory inventory, so whether same-name files existed beforehand cannot be established. The two files were restored from Trash and remain in the ignored directory. The source script and synthetic JSON report remain the reproducible record.
