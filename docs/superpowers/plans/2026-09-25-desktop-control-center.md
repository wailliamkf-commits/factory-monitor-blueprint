# Desktop Control Center Implementation Plan

> **For agentic workers:** execute assigned modules with test-first verification; root integrates and one independent reviewer checks the complete change. User delegated architecture and execution choices; no repeated approval gate.

**Goal:** deliver a working desktop alert loop, local model adapter gateway, device recommendations and an honestly labeled recording.

**Architecture:** `desktop/` extends the frozen core without modifying it. Durable alert inbox and bounded protocol gateway are independent modules. Desktop integrates runtime events and preserves original recording/stop behavior.

**Tech Stack:** Python 3.12, PySide6, SQLite, httpx, existing native capture/recording core.

**Spec:** [Design](../specs/2026-09-25-desktop-control-center-design.md)

## Global Constraints

- Preserve all 129 frozen files and source manifest.
- Local-only models; no new cloud/production image uploads or native client clicking.
- At most one popup, no focus theft; desktop live display capture is blocked until capture/UI isolation is implemented.
- Failed model review stays unknown; field gate remains NOT_TESTED.
- Publish new artifacts with readback, preserve old Release and existing field installs.

## Review Focus

- Duplicated/late events and restart recovery → durable inbox tests.
- Live display overlay contamination → GUI guard tests.
- Deadline/backpressure/fallback failure → router and HTTP gateway tests.
- Missing/malicious evidence paths and untrusted text → GUI tests.
- Shutdown with dialogs, timers and model requests active → integration checks.

## Task 1 — Durable inbox

Files: `desktop/src/factory_monitor_desktop/alerts.py`, `desktop/tests/test_alerts.py`.
Interface exactly as spec. Test duplicate ID, late result after resolution, restart retention, snooze expiry, FIFO backlog, invalid actions and bounded queries. Run tests before implementation, then SQLite implementation and same tests. No copying or modifying core events table.

## Task 2 — Model and hardware adapters

Files: `desktop/src/factory_monitor_desktop/models.py`, `profiles.py`, `gateway.py`; matching tests.
Router accepts an Ollama-shaped request from the core and converts to either local protocol. Tests use real loopback HTTP fixture servers and cover malformed output, transport failure, finite total budget, concurrency saturation, redirect/public endpoint rejection, model trace and at most two attempts. Probe hardware read-only; test absent NVIDIA tool, invalid report and memory tiers. Root connects gateway launcher and docs after interfaces settle.

## Task 3 — Actual desktop window

Files: `desktop/src/factory_monitor_desktop/app.py`, `__main__.py`, package metadata, launchers and GUI tests.
Subclass `MainWindow`; observe merged events, keep persistent inbox, suppress legacy system bubble/beep and replace with bounded popup. Bind explicit event IDs for review/evidence. Test using real SQLite and Qt widgets. Candidate arrives before model review; no readiness claims from demo. Profile page displays hardware suggestions and routing configuration without silently editing core settings.

## Task 4 — Demonstration and release

Files: demo script, upgrade guide, benchmark template, README, CI extension.
Run frozen suite plus new suite, root validation, loopback gateway smoke and native GUI operation. Record only the test application's window using native capture if available; report real capture vs rendered fixture separately. Read back media frames and session log, show key image. Build v0.2 preview, independent review, dual-platform CI and GitHub artifact download verification. Move verified duplicate build/download artifacts to Trash, retain source and a canonical demonstration copy.

## Execution ledger

- Design complete; user has delegated product/architecture and action decisions. Use workers for independent deterministic modules, root for GUI/integration and final evidence.
- Existing `implementation/` remains unchanged; v0.1 resources are referenced and not re-downloaded or re-uploaded unnecessarily.
- Implemented durable alerts, local protocol gateway, conservative hardware profiles, native Qt window and local video playback. Core sources unchanged.
- Independent review found history-cache restart crash, per-I/O rather than total timeout, unbounded request reception, Windows CI exit masking and relative weight-path mismatch. Fixes and regression checks are required before publication.
- Application-run recording produced actual Qt paints with explicit synthetic labels; native ScreenCaptureKit attempts returned only four samples and remain failed. Recording readback now validates actual sample count as well as duration.
- Target Windows/Seetong/4060 remains unavailable. Field acceptance is NOT_TESTED; no resource purchase or throughput claim is made.
