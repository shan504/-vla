"""Saved RGB-D requests cross the real multipart/Flask protocol, without a model."""

import json
from types import SimpleNamespace
from urllib.parse import urlsplit

import numpy as np
import pytest
import requests
from PIL import Image
from requests.adapters import BaseAdapter
from requests.structures import CaseInsensitiveDict

from go2 import predict_capture as client
from go2.server import create_app


K = [[611.25, 0, 2.1], [0, 610.75, 1.8], [0, 0, 1]]


class Agent:
    def __init__(self):
        self.calls = []
        self.resets = 0
        self.fail = False

    def reset(self):
        self.resets += 1

    def step(self, rgb, depth, pose, instruction, intrinsic):
        self.calls.append((rgb, depth, pose, instruction, intrinsic))
        if self.fail:
            raise RuntimeError('test inference failed')
        return SimpleNamespace(output_action=[0], output_trajectory=None, output_pixel=None)


class FlaskAdapter(BaseAdapter):
    """Run actual requests multipart encoding through Flask's request decoder."""

    def __init__(self, app):
        self.client = app.test_client()
        self.requests = []
        self.health_override = None

    def send(self, request, **kwargs):
        self.requests.append((request, kwargs))
        path = urlsplit(request.url).path
        if path == '/healthz' and self.health_override is not None:
            status, payload = self.health_override
            response = requests.Response()
            response.status_code = status
            response._content = json.dumps(payload).encode()
        else:
            result = self.client.open(path, method=request.method, data=request.body, headers=dict(request.headers))
            response = requests.Response()
            response.status_code = result.status_code
            response._content = result.data
            response.headers = CaseInsensitiveDict(result.headers)
        response.request = request
        response.url = request.url
        return response

    def close(self):
        pass


@pytest.fixture
def capture(tmp_path):
    pixels = np.zeros((4, 5, 3), dtype=np.uint8)
    pixels[:, :, 0] = 213
    Image.fromarray(pixels).save(tmp_path / 'rgb.png')
    depth = np.full((4, 5), 32345, dtype=np.uint16)
    depth[0, 0] = 0
    Image.fromarray(depth).save(tmp_path / 'depth.png')
    (tmp_path / 'camera_intrinsic.json').write_text(json.dumps(K))
    (tmp_path / 'metadata.json').write_text(json.dumps({
        'depth_png_unit_m': 0.0001, 'depth_aligned_to': 'color',
    }))
    return tmp_path


@pytest.fixture
def service():
    agent = Agent()
    adapter = FlaskAdapter(create_app(agent))
    session = requests.Session()
    session.mount('http://', adapter)
    session.mount('https://', adapter)
    return agent, adapter, session


def test_calibrated_capture_roundtrip_preserves_units_intrinsic_and_resets(capture, service):
    agent, adapter, session = service
    result = client.predict_capture(capture, '  Walk to the doorway.  ', session=session)
    assert result['http_status'] == 200
    assert result['output'] == {'discrete_action': [0]}
    assert result['elapsed_seconds'] >= 0
    rgb, depth, pose, instruction, intrinsic = agent.calls[0]
    np.testing.assert_array_equal(rgb[:, :, 0], np.full((4, 5), 213))
    np.testing.assert_allclose(depth[1:], 3.2345)
    assert depth[0, 0] == 0
    np.testing.assert_array_equal(intrinsic[:3, :3], K)
    np.testing.assert_array_equal(pose, np.eye(4))
    assert instruction == 'Walk to the doorway.'
    assert (capture / 'rgb.png').read_bytes() in adapter.requests[1][0].body
    assert (capture / 'depth.png').read_bytes() in adapter.requests[1][0].body
    # A changed scene/instruction begins a fresh episode even on a reused session.
    client.predict_capture(capture, 'Stop.', session=session)
    assert agent.resets == 2


@pytest.mark.parametrize('corruption', ['missing', 'uint8', 'empty', 'unaligned', 'intrinsic', 'unit', 'alignment'])
def test_bad_capture_is_rejected_before_http(capture, service, corruption):
    _, adapter, session = service
    if corruption == 'missing':
        (capture / 'rgb.png').unlink()
    elif corruption == 'uint8':
        Image.fromarray(np.full((4, 5), 20, dtype=np.uint8)).save(capture / 'depth.png')
    elif corruption == 'empty':
        Image.fromarray(np.zeros((4, 5), dtype=np.uint16)).save(capture / 'depth.png')
    elif corruption == 'unaligned':
        Image.fromarray(np.ones((5, 5), dtype=np.uint16)).save(capture / 'depth.png')
    elif corruption == 'intrinsic':
        (capture / 'camera_intrinsic.json').write_text(json.dumps([[0, 0, 2], [0, 600, 2], [0, 0, 1]]))
    else:
        metadata = {'depth_png_unit_m': 0.001} if corruption == 'unit' else {'depth_aligned_to': 'depth'}
        (capture / 'metadata.json').write_text(json.dumps(metadata))
    with pytest.raises((ValueError, OSError)):
        client.predict_capture(capture, 'Stop.', session=session)
    assert not adapter.requests


@pytest.mark.parametrize('timeout', [0, -1, float('nan'), float('inf')])
def test_invalid_timeout_is_rejected_before_http(capture, service, timeout):
    _, adapter, session = service
    with pytest.raises(ValueError, match='timeout'):
        client.predict_capture(capture, 'Stop.', timeout=timeout, session=session)
    assert not adapter.requests


def test_health_failure_does_not_submit_observation(capture, service):
    agent, adapter, session = service
    adapter.health_override = (503, {'status': 'loading'})
    with pytest.raises(RuntimeError, match='HTTP 503'):
        client.predict_capture(capture, 'Stop.', session=session)
    assert len(adapter.requests) == 1
    assert not agent.calls


@pytest.mark.parametrize('url,uses_proxy', [
    ('http://127.0.0.1:5801', False),
    ('http://localhost:5801/eval_dual', False),
    ('http://[::1]:5801', False),
    ('http://192.168.1.230:5801', True),
])
def test_only_loopback_bypasses_inherited_proxy(capture, service, monkeypatch, url, uses_proxy):
    _, adapter, session = service
    monkeypatch.setenv('http_proxy', 'http://unused-proxy.example:7890')
    monkeypatch.setenv('HTTP_PROXY', 'http://unused-proxy.example:7890')
    monkeypatch.setenv('no_proxy', '')
    monkeypatch.setenv('NO_PROXY', '')
    result = client.predict_capture(capture, 'Stop.', url=url, session=session)
    assert result['http_status'] == 200
    assert session.trust_env is uses_proxy
    assert bool(adapter.requests[0][1]['proxies']) is uses_proxy


def test_cli_reports_model_failure_and_logs_hint(capture, service, monkeypatch, capsys):
    agent, _, session = service
    agent.fail = True
    monkeypatch.setattr(client.requests, 'Session', lambda: session)
    status = client.main([str(capture), '--instruction', 'Stop.'])
    assert status == 1
    output = capsys.readouterr()
    report = json.loads(output.out)
    assert report['http_status'] == 503
    assert 'model inference failed' in report['output']['error']
    assert 'logs/slurm/' in output.err


def test_cli_reports_success_and_timing(capture, service, monkeypatch, capsys):
    _, _, session = service
    monkeypatch.setattr(client.requests, 'Session', lambda: session)
    assert client.main([str(capture), '--instruction', 'Stop.']) == 0
    report = json.loads(capsys.readouterr().out)
    assert report['output'] == {'discrete_action': [0]}
    assert report['elapsed_seconds'] >= 0
