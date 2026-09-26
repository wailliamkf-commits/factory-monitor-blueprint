"""Explicitly preload an already-installed fixed local model, then verify admission.

No model download, automatic retry, unload, process termination, or field test is
performed. A timed-out HTTP request does not prove the backend stopped loading.
"""

from __future__ import annotations

import argparse
import asyncio
from contextlib import AsyncExitStack
import json
from pathlib import Path
import sys
import time
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "desktop/src"), str(ROOT / "implementation/src")]

import httpx

from factory_monitor_desktop.resource_budget import (
    BudgetRefused, MAX_STATUS_BYTES, MODEL_DIGEST, MODEL_NAME, ResourceSampler, Vram8gbPolicy,
)


async def _bounded_json(client: httpx.AsyncClient, method: str, url: str,
                        body: dict[str, Any] | None = None) -> dict[str, Any]:
    raw = bytearray()
    async with client.stream(method, url, json=body, follow_redirects=False) as response:
        if response.status_code != 200:
            raise BudgetRefused("local_service_request_failed")
        async for chunk in response.aiter_bytes(chunk_size=MAX_STATUS_BYTES):
            raw.extend(chunk)
            if len(raw) > MAX_STATUS_BYTES:
                raise BudgetRefused("local_service_response_too_large")
    try:
        result = json.loads(raw)
    except (ValueError, UnicodeError) as exc:
        raise BudgetRefused("local_service_response_invalid") from exc
    if not isinstance(result, dict):
        raise BudgetRefused("local_service_response_invalid")
    return result


async def _wait_first_sample(sampler: ResourceSampler) -> None:
    deadline = time.monotonic() + 3
    while sampler.snapshot().get("monotonic") is None:
        if time.monotonic() >= deadline:
            raise BudgetRefused("sensor_unavailable")
        await asyncio.sleep(.02)


def _write_report(stream, result: dict[str, Any]) -> None:
    # Only this invocation's exclusively-created report is ever rewritten.
    stream.seek(0)
    stream.write(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + "\n")
    stream.truncate()
    stream.flush()


async def prepare_model(port: int, report: Path, *, sampler: ResourceSampler | None = None,
                        client: httpx.AsyncClient | None = None, timeout_seconds: float = 120) -> int:
    """Return zero only after warm-model readback passes Vram8gbPolicy.

    Injected sampler/client are for isolated tests. This function starts and
    closes its sampler; an injected HTTP client remains owned by its caller.
    """
    if type(port) is not int or not 1 <= port <= 65535:
        raise ValueError("port must be between 1 and 65535")
    if not isinstance(timeout_seconds, (int, float)) or isinstance(timeout_seconds, bool) or not 0 < timeout_seconds <= 120:
        raise ValueError("timeout must be positive and at most 120 seconds")
    report = Path(report)
    report.parent.mkdir(parents=True, exist_ok=True)
    endpoint = f"http://127.0.0.1:{port}"
    log_path = report.with_name(report.name + ".resources.ndjson")
    started = time.monotonic()
    result: dict[str, Any] = {
        "gate": "RUNNING", "scope": "explicit local model preparation; no field validation",
        "field_gate": "NOT_TESTED", "model": MODEL_NAME, "digest": MODEL_DIGEST,
        "endpoint": endpoint, "load_timeout_seconds": timeout_seconds,
        "model_load_requested": False, "download_requested": False,
        "automatic_retry": False, "unload_requested": False, "kill_requested": False,
        "backend_stop_verified": False, "backend_may_still_be_running": False,
        "resource_log": str(log_path.resolve()),
    }
    # Reserve before probing or issuing HTTP. Existing reports are never touched.
    with report.open("x", encoding="utf-8") as stream:
        _write_report(stream, result)
        try:
            with log_path.open("x", encoding="utf-8"):
                pass
            sampler = sampler or ResourceSampler(log_path=log_path)
            sampler.start()
            policy = Vram8gbPolicy(sampler)
            async with AsyncExitStack() as stack:
                if client is None:
                    client = await stack.enter_async_context(httpx.AsyncClient(
                        trust_env=False, follow_redirects=False, timeout=httpx.Timeout(120)))
                async with asyncio.timeout(timeout_seconds):
                    await _wait_first_sample(sampler)
                    # No NVIDIA or an invalid/stale/pressured sample means no HTTP.
                    result["before"] = policy._admit_sample()
                    result["backend_may_still_be_running"] = True
                    status = await _bounded_json(client, "GET", endpoint + "/api/ps")
                    models = status.get("models")
                    if not isinstance(models, list):
                        raise BudgetRefused("local_service_response_invalid")
                    if models:
                        result["path"] = "verify_existing_warm_model"
                        result["admission"] = await policy.admit(client, endpoint, MODEL_NAME)
                    else:
                        result["path"] = "load_installed_cold_model"
                        cold_sample = policy._admit_sample()
                        if cold_sample["free_mib"] < 6144:
                            raise BudgetRefused("cold_load_vram_unavailable")
                        tags = await _bounded_json(client, "GET", endpoint + "/api/tags")
                        installed = tags.get("models")
                        if not isinstance(installed, list):
                            raise BudgetRefused("local_service_response_invalid")
                        matching = [item for item in installed if isinstance(item, dict)
                                    and item.get("name") == MODEL_NAME]
                        if (len(matching) != 1 or matching[0].get("digest") != MODEL_DIGEST
                                or matching[0].get("model", MODEL_NAME) != MODEL_NAME):
                            raise BudgetRefused("installed_model_digest_unverified")
                        # Recheck resources and zero resident models immediately before POST.
                        cold_sample = policy._admit_sample()
                        if cold_sample["free_mib"] < 6144:
                            raise BudgetRefused("cold_load_vram_unavailable")
                        recheck = await _bounded_json(client, "GET", endpoint + "/api/ps")
                        if recheck.get("models") != []:
                            raise BudgetRefused("cold_load_requires_no_resident_model")
                        if policy._admit_sample()["free_mib"] < 6144:
                            raise BudgetRefused("cold_load_vram_unavailable")
                        result["model_load_requested"] = True
                        _write_report(stream, result)
                        loaded = await _bounded_json(client, "POST", endpoint + "/api/generate", {
                            "model": MODEL_NAME, "prompt": "", "stream": False,
                            "options": {"num_ctx": 4096}, "keep_alive": -1,
                        })
                        if loaded.get("done") is not True:
                            raise BudgetRefused("model_load_completion_unverified")
                        # Demand a post-load hardware sample, not the cached cold sample.
                        load_completed_at = time.monotonic()
                        refresh_deadline = time.monotonic() + 5
                        while (sampler.snapshot().get("monotonic") or 0) <= load_completed_at:
                            if time.monotonic() >= refresh_deadline:
                                raise BudgetRefused("post_load_sensor_unavailable")
                            await asyncio.sleep(.02)
                        result["admission"] = await policy.admit(client, endpoint, MODEL_NAME)
                    result["gate"] = "PASS"
                    result["reason"] = "warm_model_and_resource_readback_passed"
        except (TimeoutError, httpx.TimeoutException):
            result.update(gate="FAIL", reason="preparation_timeout",
                          backend_stop_verified=False)
        except BudgetRefused as exc:
            result.update(gate="FAIL", reason=exc.reason)
        except FileExistsError:
            result.update(gate="FAIL", reason="resource_log_exists")
        except httpx.HTTPError:
            result.update(gate="FAIL", reason="local_service_transport_failed")
        except (OSError, ValueError, TypeError, RuntimeError):
            result.update(gate="FAIL", reason="preparation_failed")
        finally:
            if sampler is not None:
                sampler.close()
                result["after"] = sampler.snapshot()
            result["elapsed_seconds"] = round(time.monotonic() - started, 3)
            if result["gate"] != "PASS":
                result["recovery"] = "Inspect local service and resource state manually; no retry, unload, or kill was attempted."
            _write_report(stream, result)
    return 0 if result["gate"] == "PASS" else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=11435, help="Loopback Ollama port (default: 11435)")
    parser.add_argument("--report", type=Path, required=True, help="New report path; must not already exist")
    args = parser.parse_args(argv)
    if not 1 <= args.port <= 65535:
        parser.error("--port must be between 1 and 65535")
    try:
        code = asyncio.run(prepare_model(args.port, args.report))
    except FileExistsError:
        print("Report already exists; nothing was loaded or overwritten.", file=sys.stderr)
        return 2
    except (OSError, ValueError):
        print("Preparation could not create its report; inspect the chosen local path.", file=sys.stderr)
        return 2
    print(f"{'PASS' if code == 0 else 'FAIL'}: {args.report}")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
