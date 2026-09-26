import base64
import io
import json
import time

import httpx
from PIL import Image
import pytest

from factory_monitor_desktop.models import ModelConfigurationError, ModelRouter, ModelRouterError
from factory_monitor_desktop.resource_budget import MODEL_DIGEST, MODEL_NAME, Vram8gbPolicy


class Sampler:
    def __init__(self):
        self.error = None

    def snapshot(self):
        return dict(monotonic=time.monotonic(), total_mib=8192, used_mib=3500,
                    free_mib=4692, ram_available_mib=8000, gpu_count=1,
                    error=self.error, latched=False)


def setup_router(monkeypatch):
    sampler = Sampler()
    calls = []
    model = dict(name=MODEL_NAME, model=MODEL_NAME, digest=MODEL_DIGEST,
                 details={'quantization_level': 'Q4_K_M'}, context_length=4096,
                 size_vram=3000 * 1024**2)

    def respond(request):
        calls.append(request)
        if request.url.path == '/api/ps':
            return httpx.Response(200, json={'models': [model]})
        return httpx.Response(200, json={'message': {'content': json.dumps(
            dict(decision='uncertain', reason='detail insufficient', visible_evidence=[]))}})

    original = httpx.AsyncClient
    monkeypatch.setattr('factory_monitor_desktop.models.httpx.AsyncClient',
                        lambda **kwargs: original(transport=httpx.MockTransport(respond), **kwargs))
    provider = dict(name='primary', protocol='ollama', endpoint='http://127.0.0.1:11435',
                    model=MODEL_NAME, max_images=6, timeout_seconds=1)
    config = dict(deadline_seconds=2, max_inflight=1, providers=[provider])
    return ModelRouter(config, resource_policy=Vram8gbPolicy(sampler)), sampler, calls, model, config


def payload():
    stream = io.BytesIO()
    Image.new('RGB', (1280, 720), 'gray').save(stream, format='JPEG')
    encoded = base64.b64encode(stream.getvalue()).decode()
    return {'stream': False, 'messages': [{'role': 'user', 'content': 'ordered frames t=0..5',
                                         'images': [encoded] * 6}]}


def test_8gb_router_verifies_warm_model_then_posts_bounded_frames(monkeypatch):
    router, _, calls, _, _ = setup_router(monkeypatch)
    _, trace = router.chat(payload())
    assert [r.url.path for r in calls] == ['/api/ps', '/api/chat']
    body = json.loads(calls[1].content)
    assert body['options'] == dict(temperature=0, num_ctx=4096, num_predict=256)
    assert body['keep_alive'] == -1
    assert body['messages'][0]['content'] == 'ordered frames t=0..5'
    assert len(body['messages'][0]['images']) == 6
    for encoded in body['messages'][0]['images']:
        with Image.open(io.BytesIO(base64.b64decode(encoded))) as image:
            assert image.size == (448, 252)
    assert trace['resource_admission']['digest'] == MODEL_DIGEST
    assert trace['image_preparation']['image_count'] == 6
    assert 'images' not in json.dumps(trace['resource_admission'])


def test_sensor_unknown_cannot_send_or_fallback(monkeypatch):
    router, sampler, calls, _, config = setup_router(monkeypatch)
    config['providers'].append(dict(config['providers'][0], name='backup'))
    router = ModelRouter(config, resource_policy=router.resource_policy)
    sampler.error = 'probe_failed'
    with pytest.raises(ModelRouterError) as caught:
        router.chat(payload())
    assert caught.value.status_code == 503
    assert caught.value.trace['resource_reason'] == 'sensor_unavailable'
    assert calls == []


def test_wrong_loaded_model_cannot_cold_load_or_fallback(monkeypatch):
    router, _, calls, model, config = setup_router(monkeypatch)
    config['providers'].append(dict(config['providers'][0], name='backup'))
    router = ModelRouter(config, resource_policy=router.resource_policy)
    model['digest'] = 'unapproved'
    with pytest.raises(ModelRouterError) as caught:
        router.chat(payload())
    assert caught.value.trace['resource_reason'] == 'loaded_digest_mismatch'
    assert [r.url.path for r in calls] == ['/api/ps']


def test_8gb_rejects_unprotected_backup_protocol(monkeypatch):
    router, _, _, _, config = setup_router(monkeypatch)
    config['providers'].append(dict(config['providers'][0], name='backup', protocol='openai'))
    with pytest.raises(ModelConfigurationError, match='pinned'):
        ModelRouter(config, resource_policy=router.resource_policy)


def test_response_disconnect_fences_backend_without_a_second_request(monkeypatch):
    router, _, calls, _, _ = setup_router(monkeypatch)
    # A POST that reached a service but lost its response cannot safely free its GPU slot.
    original_client = httpx.AsyncClient

    def broken_client(**kwargs):
        client = original_client(**kwargs)
        original_stream = client.stream

        def stream(method, *args, **stream_kwargs):
            if method == 'POST':
                raise httpx.ReadError('response lost after upload')
            return original_stream(method, *args, **stream_kwargs)

        client.stream = stream
        return client

    monkeypatch.setattr('factory_monitor_desktop.models.httpx.AsyncClient', broken_client)
    with pytest.raises(ModelRouterError) as first:
        router.chat(payload())
    assert first.value.status_code == 504
    assert first.value.trace['backend_state'] == 'uncertain'
    count = len(calls)
    with pytest.raises(ModelRouterError) as second:
        router.chat(payload())
    assert second.value.status_code == 503
    assert len(calls) == count
