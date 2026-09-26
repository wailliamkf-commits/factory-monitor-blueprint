"""Bounded local vision-model routing for desktop reviews."""

from __future__ import annotations

import base64
import binascii
import asyncio
import ipaddress
import json
import math
import threading
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

import httpx
from .resource_budget import BudgetRefused, MODEL_NAME


MAX_DEADLINE_SECONDS = 15.0
MAX_IMAGE_BYTES = 4 * 1024 * 1024
MAX_RESPONSE_BYTES = 64 * 1024
MAX_PROMPT_CHARS = 16_000
MAX_OUTPUT_TOKENS = 512
_DECISIONS = {"supported", "dismissed", "uncertain"}
_MATERIAL_TYPES = {"cable", "wire", "bundle", "coil", "none", "unclear"}


class ModelConfigurationError(ValueError):
    """The local model route is not safe or internally consistent."""


class ModelRouterError(RuntimeError):
    """A model request failed, timed out, or returned unusable evidence."""

    def __init__(self, message: str, *, status_code: int = 502, trace: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.trace = trace


class ModelBusyError(ModelRouterError):
    """The single configured local inference slot is already occupied."""

    def __init__(self, trace: dict[str, Any] | None = None) -> None:
        super().__init__("local model gateway is busy (429)", status_code=429, trace=trace)


class _UncertainRequestError(ModelRouterError):
    """The request may have reached a provider that can still be processing it."""

    def __init__(self) -> None:
        super().__init__("local model request timed out; backend state is uncertain", status_code=504)


@dataclass(frozen=True)
class _Provider:
    name: str
    protocol: str
    endpoint: str
    model: str
    max_images: int
    timeout_seconds: float

    @property
    def request_url(self) -> str:
        if self.protocol == "ollama":
            return f"{self.endpoint}/api/chat"
        path = urlparse(self.endpoint).path.rstrip("/")
        suffix = "/chat/completions" if path == "/v1" else "/v1/chat/completions"
        return f"{self.endpoint}{suffix}"


def _validate_endpoint(value: Any, protocol: str) -> str:
    if not isinstance(value, str):
        raise ModelConfigurationError("provider endpoint must be a loopback HTTP URL")
    parsed = urlparse(value)
    if parsed.scheme != "http" or not parsed.hostname or parsed.username or parsed.password:
        raise ModelConfigurationError("provider endpoint must be loopback HTTP")
    if parsed.query or parsed.fragment:
        raise ModelConfigurationError("provider endpoint cannot include query or fragment")
    hostname = parsed.hostname.lower().rstrip(".")
    loopback = hostname == "localhost"
    try:
        loopback = loopback or ipaddress.ip_address(hostname).is_loopback
    except ValueError:
        pass
    if not loopback:
        raise ModelConfigurationError("provider endpoint must use a loopback host")
    if protocol == "ollama" and parsed.path not in {"", "/"}:
        raise ModelConfigurationError("Ollama endpoint must be a base URL without a path")
    if protocol == "openai" and parsed.path.rstrip("/") not in {"", "/v1"}:
        raise ModelConfigurationError("OpenAI-compatible endpoint path must be empty or /v1")
    try:
        port = parsed.port
    except ValueError as exc:
        raise ModelConfigurationError("provider endpoint has an invalid port") from exc
    if port is not None and not 1 <= port <= 65535:
        raise ModelConfigurationError("provider endpoint has an invalid port")
    return f"http://{parsed.netloc}{parsed.path.rstrip('/')}".rstrip("/")


def _finite_number(value: Any, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ModelConfigurationError(f"{name} must be a finite number")
    return float(value)


def _clean_providers(config: dict[str, Any]) -> tuple[_Provider, ...]:
    values = config.get("providers")
    if not isinstance(values, list) or not 1 <= len(values) <= 2:
        raise ModelConfigurationError("providers must contain one primary and at most one fallback")
    providers: list[_Provider] = []
    names: set[str] = set()
    for index, value in enumerate(values):
        if not isinstance(value, dict):
            raise ModelConfigurationError("each provider must be an object")
        name = value.get("name", "primary" if index == 0 else "fallback")
        protocol = value.get("protocol")
        model = value.get("model")
        if not isinstance(name, str) or not name.strip() or name in names:
            raise ModelConfigurationError("provider names must be unique non-empty strings")
        if protocol not in {"ollama", "openai"}:
            raise ModelConfigurationError("provider protocol must be ollama or openai")
        if not isinstance(model, str) or not model.strip() or len(model) > 200:
            raise ModelConfigurationError("provider model must be a non-empty string up to 200 characters")
        max_images = value.get("max_images")
        if isinstance(max_images, bool) or not isinstance(max_images, int) or not 1 <= max_images <= 6:
            raise ModelConfigurationError("max_images must be an integer from 1 to 6")
        timeout_seconds = _finite_number(value.get("timeout_seconds"), "timeout_seconds")
        if not 0 < timeout_seconds < MAX_DEADLINE_SECONDS:
            raise ModelConfigurationError("timeout_seconds must be greater than 0 and below 15")
        providers.append(
            _Provider(
                name=name,
                protocol=protocol,
                endpoint=_validate_endpoint(value.get("endpoint"), protocol),
                model=model.strip(),
                max_images=max_images,
                timeout_seconds=timeout_seconds,
            )
        )
        names.add(name)
    return tuple(providers)


def _decode_request(payload: Any, max_images: int) -> tuple[list[dict[str, Any]], dict[str, Any], bool]:
    if not isinstance(payload, dict):
        raise ModelRouterError("chat request must be a JSON object", status_code=400)
    messages = payload.get("messages")
    if not isinstance(messages, list) or not messages:
        raise ModelRouterError("chat request must contain messages", status_code=400)
    clean_messages: list[dict[str, Any]] = []
    total_image_bytes = 0
    image_count = 0
    material = False
    for message in messages:
        if not isinstance(message, dict) or message.get("role") not in {"system", "user", "assistant"}:
            raise ModelRouterError("chat messages have an invalid shape", status_code=400)
        content = message.get("content", "")
        if not isinstance(content, str) or len(content) > MAX_PROMPT_CHARS:
            raise ModelRouterError("chat text exceeds the local review limit", status_code=400)
        images = message.get("images", [])
        if images is None:
            images = []
        if not isinstance(images, list):
            raise ModelRouterError("chat images must be a list", status_code=400)
        image_count += len(images)
        decoded_images: list[str] = []
        for image in images:
            if not isinstance(image, str) or len(image) > ((MAX_IMAGE_BYTES + 2) // 3) * 4:
                raise ModelRouterError("image exceeds the local review size limit", status_code=413)
            try:
                decoded = base64.b64decode(image, validate=True)
            except (binascii.Error, ValueError) as exc:
                raise ModelRouterError("chat image is not valid base64", status_code=400) from exc
            if not decoded or len(decoded) > MAX_IMAGE_BYTES:
                raise ModelRouterError("image exceeds the local review size limit", status_code=413)
            total_image_bytes += len(decoded)
            decoded_images.append(image)
        if total_image_bytes > 16 * 1024 * 1024:
            raise ModelRouterError("combined images exceed the local review size limit", status_code=413)
        format_spec = payload.get("format")
        if isinstance(format_spec, dict):
            properties = format_spec.get("properties", {})
            required = format_spec.get("required", [])
            if isinstance(properties, dict) and {"target_visible", "target_type"}.issubset(properties):
                material = True
            if isinstance(required, list) and {"target_visible", "target_type"}.issubset(required):
                material = True
        if "material_candidate" in content:
            material = True
        clean_messages.append({"role": message["role"], "content": content, "images": decoded_images})
    if not 1 <= image_count <= max_images:
        raise ModelRouterError(f"request must contain 1 to {max_images} images", status_code=400)
    if payload.get("stream", False) is not False:
        raise ModelRouterError("streaming responses are not supported by the desktop gateway", status_code=400)
    return clean_messages, payload.get("format", "json"), material


def _validate_result(value: Any, material: bool) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ModelRouterError("local model returned an invalid review schema")
    expected_keys = {"decision", "reason", "visible_evidence"}
    if material:
        expected_keys.update({"target_visible", "target_type"})
    if set(value) != expected_keys:
        raise ModelRouterError("local model returned an invalid review schema")
    decision = value.get("decision")
    reason = value.get("reason")
    visible = value.get("visible_evidence")
    if (
        decision not in _DECISIONS
        or not isinstance(reason, str)
        or not reason.strip()
        or len(reason) > 180
        or not isinstance(visible, list)
        or len(visible) > 3
        or any(not isinstance(item, str) or not item.strip() or len(item) > 100 for item in visible)
        or (decision == "supported" and not visible)
    ):
        raise ModelRouterError("local model returned an invalid review schema")
    if material:
        target_visible = value.get("target_visible")
        target_type = value.get("target_type")
        if not isinstance(target_visible, bool) or target_type not in _MATERIAL_TYPES:
            raise ModelRouterError("local model returned an invalid material schema")
        if decision == "supported" and (not target_visible or target_type not in {"cable", "wire", "bundle", "coil"}):
            raise ModelRouterError("local model returned an invalid material schema")
    return value


def _decode_content(content: Any) -> dict[str, Any]:
    if isinstance(content, str):
        try:
            result = json.loads(content)
        except json.JSONDecodeError as exc:
            raise ModelRouterError("local model returned invalid JSON") from exc
    else:
        result = content
    return result


class ModelRouter:
    """Route local reviews; an ambiguous request timeout disables routes until restart."""

    def __init__(self, config: dict[str, Any], *, resource_policy=None) -> None:
        if not isinstance(config, dict):
            raise ModelConfigurationError("model router config must be an object")
        deadline = _finite_number(config.get("deadline_seconds"), "deadline_seconds")
        if not 0 < deadline < MAX_DEADLINE_SECONDS:
            raise ModelConfigurationError("deadline_seconds must be greater than 0 and below the core 15-second budget")
        inflight = config.get("max_inflight", 1)
        if isinstance(inflight, bool) or inflight != 1:
            raise ModelConfigurationError("max_inflight is fixed at 1 until separately benchmarked")
        self.deadline_seconds = deadline
        self.providers = _clean_providers(config)
        self.resource_policy = resource_policy
        if resource_policy is not None and any(
            p.protocol != 'ollama' or p.model != MODEL_NAME for p in self.providers
        ):
            raise ModelConfigurationError('vram8gb requires the pinned local Ollama model on every route')
        self.max_inflight = 1
        self._inflight = threading.BoundedSemaphore(1)
        self._backend_uncertain = False
        self.last_trace: dict[str, Any] | None = None
        self._last_admission = None

    def chat(self, payload: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
        messages, format_spec, material = _decode_request(payload, max(p.max_images for p in self.providers))
        image_count = sum(len(message["images"]) for message in messages)
        if not self._inflight.acquire(blocking=False):
            trace = {"provider": None, "model": None, "status": "busy", "attempts": 0, "duration_ms": 0, "attempt_log": []}
            self.last_trace = trace
            raise ModelBusyError(trace)
        started = time.monotonic()
        deadline = started + self.deadline_seconds
        attempts: list[dict[str, Any]] = []
        image_metadata = None
        self._last_admission = None
        try:
            if self._backend_uncertain:
                trace = {
                    "provider": None,
                    "model": None,
                    "status": "uncertain",
                    "backend_state": "uncertain",
                    "attempts": 0,
                    "duration_ms": 0,
                    "attempt_log": [],
                }
                self.last_trace = trace
                raise ModelRouterError(
                    "local model backend state is uncertain; verify the backend has stopped, then restart the application",
                    status_code=503,
                    trace=trace,
                )
            if self.resource_policy is not None:
                messages, image_metadata = self.resource_policy.prepare_images(messages)
            for provider in self.providers:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                attempt_started = time.monotonic()
                if image_count > provider.max_images:
                    attempts.append(
                        {
                            "provider": provider.name,
                            "model": provider.model,
                            "status": "skipped",
                            "duration_ms": 0,
                            "failure": "image_limit",
                        }
                    )
                    continue
                try:
                    value = self._call(provider, messages, format_spec, remaining, deadline)
                    result = _validate_result(value, material)
                    elapsed = round((time.monotonic() - attempt_started) * 1000)
                    attempts.append({"provider": provider.name, "model": provider.model, "status": "completed", "duration_ms": elapsed})
                    trace = {
                        "provider": provider.name,
                        "model": provider.model,
                        "status": "completed",
                        "attempts": len(attempts),
                        "duration_ms": round((time.monotonic() - started) * 1000),
                        "attempt_log": attempts,
                    }
                    if image_metadata is not None:
                        trace['resource_profile'] = 'vram8gb'
                        trace['image_preparation'] = image_metadata
                        trace['resource_admission'] = self._last_admission
                    self.last_trace = trace
                    return {
                        "model": provider.model,
                        "message": {"role": "assistant", "content": json.dumps(result, ensure_ascii=False, separators=(",", ":"))},
                        "done": True,
                        "done_reason": "stop",
                    }, trace
                except ModelBusyError as exc:
                    elapsed = round((time.monotonic() - attempt_started) * 1000)
                    attempts.append(
                        {
                            "provider": provider.name,
                            "model": provider.model,
                            "status": "busy",
                            "duration_ms": elapsed,
                            "failure_reason": str(exc),
                        }
                    )
                    trace = {
                        "provider": provider.name,
                        "model": provider.model,
                        "status": "busy",
                        "attempts": len(attempts),
                        "duration_ms": round((time.monotonic() - started) * 1000),
                        "attempt_log": attempts,
                    }
                    self.last_trace = trace
                    exc.trace = trace
                    raise
                except _UncertainRequestError as exc:
                    self._backend_uncertain = True
                    elapsed = round((time.monotonic() - attempt_started) * 1000)
                    attempts.append(
                        {
                            "provider": provider.name,
                            "model": provider.model,
                            "status": "uncertain",
                            "duration_ms": elapsed,
                            "failure_reason": str(exc),
                        }
                    )
                    trace = {
                        "provider": provider.name,
                        "model": provider.model,
                        "status": "uncertain",
                        "backend_state": "uncertain",
                        "attempts": len(attempts),
                        "duration_ms": round((time.monotonic() - started) * 1000),
                        "attempt_log": attempts,
                    }
                    self.last_trace = trace
                    raise ModelRouterError(
                        "local model review timed out; verify the backend has stopped, then restart the application; no fallback or retry was started",
                        status_code=504,
                        trace=trace,
                    ) from exc
                except ModelRouterError as exc:
                    elapsed = round((time.monotonic() - attempt_started) * 1000)
                    attempts.append({"provider": provider.name, "model": provider.model, "status": "failed", "duration_ms": elapsed, "failure_reason": str(exc)})
                    if exc.status_code == 429:
                        raise
                except (httpx.HTTPError, TimeoutError, ValueError) as exc:
                    elapsed = round((time.monotonic() - attempt_started) * 1000)
                    reason = "local model request timed out" if isinstance(exc, TimeoutError) else "local model transport failed"
                    attempts.append({"provider": provider.name, "model": provider.model, "status": "failed", "duration_ms": elapsed, "failure_reason": reason})
            status = "timeout" if time.monotonic() >= deadline else "error"
            trace = {
                "provider": attempts[-1]["provider"] if attempts else None,
                "model": attempts[-1]["model"] if attempts else None,
                "status": status,
                "attempts": len(attempts),
                "duration_ms": round((time.monotonic() - started) * 1000),
                "attempt_log": attempts,
            }
            self.last_trace = trace
            code = 504 if status == "timeout" else 502
            message = "local model review exceeded its total deadline" if status == "timeout" else "local model review failed"
            raise ModelRouterError(message, status_code=code, trace=trace)
        except BudgetRefused as exc:
            trace = {'provider': None, 'model': None, 'status': 'resource_blocked',
                     'resource_profile': 'vram8gb', 'resource_reason': exc.reason,
                     'attempts': len(attempts), 'attempt_log': attempts,
                     'duration_ms': round((time.monotonic() - started) * 1000)}
            self.last_trace = trace
            raise ModelRouterError('local review withheld: ' + exc.reason,
                                   status_code=503, trace=trace) from exc
        finally:
            self._inflight.release()

    def _call(
        self,
        provider: _Provider,
        messages: list[dict[str, Any]],
        format_spec: Any,
        remaining: float,
        deadline: float,
    ) -> dict[str, Any]:
        timeout_budget = min(provider.timeout_seconds, remaining, deadline - time.monotonic())
        if timeout_budget <= 0:
            raise _UncertainRequestError()
        if provider.protocol == "ollama":
            request_body = {
                "model": provider.model,
                "stream": False,
                "format": format_spec,
                "messages": [{"role": item["role"], "content": item["content"], "images": item["images"]} for item in messages],
                "options": {"temperature": 0, "num_predict": MAX_OUTPUT_TOKENS},
            }
            if self.resource_policy is not None:
                request_body['options'].update(num_ctx=4096, num_predict=256)
                request_body['keep_alive'] = -1
        else:
            converted: list[dict[str, Any]] = []
            for item in messages:
                content: list[dict[str, Any]] = []
                if item["content"]:
                    content.append({"type": "text", "text": item["content"]})
                for image in item["images"]:
                    content.append({"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{image}"}})
                converted.append({"role": item["role"], "content": content})
            schema = format_spec if isinstance(format_spec, dict) else None
            response_format = (
                {"type": "json_schema", "json_schema": {"name": "monitor_review", "strict": True, "schema": schema}}
                if schema is not None
                else {"type": "json_object"}
            )
            request_body = {
                "model": provider.model,
                "stream": False,
                "messages": converted,
                "response_format": response_format,
                "temperature": 0,
                "max_tokens": MAX_OUTPUT_TOKENS,
            }

        async def request() -> bytes:
            timeout = httpx.Timeout(max(0.001, provider.timeout_seconds))

            async def read_limited(response: httpx.Response) -> bytes:
                raw = bytearray()
                async for chunk in response.aiter_bytes():
                    raw.extend(chunk)
                    if len(raw) > MAX_RESPONSE_BYTES:
                        raise _UncertainRequestError()
                return bytes(raw)

            async with asyncio.timeout(timeout_budget):
                async with httpx.AsyncClient(trust_env=False, follow_redirects=False, timeout=timeout) as client:
                    if self.resource_policy is not None:
                        self._last_admission = await self.resource_policy.admit(client, provider.endpoint, provider.model)
                    async with client.stream("POST", provider.request_url, json=request_body) as response:
                        if response.status_code == 429:
                            raise ModelBusyError()
                        if response.is_redirect:
                            await read_limited(response)
                            raise ModelRouterError("local model redirects are disabled")
                        if response.status_code >= 400:
                            await read_limited(response)
                            raise ModelRouterError(
                                f"local model returned HTTP {response.status_code}", status_code=response.status_code
                            )
                        return await read_limited(response)

        try:
            raw = asyncio.run(request())
        except TimeoutError as exc:
            raise _UncertainRequestError() from exc
        except httpx.ConnectTimeout as exc:
            raise ModelRouterError("local model connection timed out") from exc
        except httpx.TimeoutException as exc:
            raise _UncertainRequestError() from exc
        except httpx.ConnectError as exc:
            raise ModelRouterError("local model connection failed") from exc
        except httpx.HTTPError as exc:
            raise _UncertainRequestError() from exc
        try:
            outer = json.loads(raw)
            if provider.protocol == "ollama":
                content = outer["message"]["content"]
            else:
                content = outer["choices"][0]["message"]["content"]
        except (json.JSONDecodeError, KeyError, IndexError, TypeError) as exc:
            raise ModelRouterError("local model returned a malformed response") from exc
        return _decode_content(content)
