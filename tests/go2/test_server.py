"""Protocol tests with a fake agent; these do not validate pretrained inference."""

import io
import json
from types import SimpleNamespace

import numpy as np
import pytest
from PIL import Image

from go2.server import create_app, parse_intrinsic


K = [[400, 0, 2], [0, 400, 2], [0, 0, 1]]


class FakeAgent:
    def __init__(self):
        self.calls = []
        self.resets = 0
        self.fail = False
        self.output = SimpleNamespace(output_action=[1], output_trajectory=None, output_pixel=None)

    def reset(self):
        self.resets += 1

    def step(self, rgb, depth, pose, instruction, intrinsic):
        self.calls.append((rgb, depth, pose, instruction, intrinsic))
        if self.fail:
            raise RuntimeError('simulated inference failure')
        return self.output


def encoded(array, kind):
    stream = io.BytesIO()
    Image.fromarray(array).save(stream, format=kind)
    stream.seek(0)
    return stream


def payload(**changes):
    fields = {'instruction': 'Go to the door', 'reset': True, 'camera_intrinsic': K}
    fields.update(changes)
    return {
        'json': json.dumps(fields),
        'image': (encoded(np.zeros((4, 5, 3), dtype=np.uint8), 'JPEG'), 'rgb.jpg'),
        'depth': (encoded(np.full((4, 5), 20000, dtype=np.uint16), 'PNG'), 'depth.png'),
    }


@pytest.fixture
def service():
    agent = FakeAgent()
    app = create_app(agent)
    app.testing = True
    return app.test_client(), agent


def test_health_and_image_depth_instruction_roundtrip(service):
    client, agent = service
    assert client.get('/healthz').json['model'] == 'FakeAgent'
    response = client.post('/eval_dual', data=payload())
    assert response.status_code == 200
    assert response.json == {'discrete_action': [1]}
    assert agent.resets == 1
    rgb, depth, pose, instruction, intrinsic = agent.calls[0]
    assert rgb.shape == (4, 5, 3)
    np.testing.assert_array_equal(depth, np.full((4, 5), 2.0))
    np.testing.assert_array_equal(pose, np.eye(4))
    np.testing.assert_array_equal(intrinsic[:3, :3], K)
    assert instruction == 'Go to the door'


def test_continuation_and_explicit_instruction_change(service):
    client, agent = service
    assert client.post('/eval_dual', data=payload(reset=False)).status_code == 400
    assert client.post('/eval_dual', data=payload()).status_code == 200
    assert client.post('/eval_dual', data=payload(reset=False)).status_code == 200
    assert agent.resets == 1
    response = client.post('/eval_dual', data=payload(instruction='Turn left', reset=False))
    assert response.status_code == 400
    assert len(agent.calls) == 2
    assert client.post('/eval_dual', data=payload(instruction='Turn left')).status_code == 200
    assert agent.calls[-1][3] == 'Turn left'
    assert agent.resets == 2


@pytest.mark.parametrize('changes', [
    {'instruction': ''}, {'instruction': None}, {'instruction': 42},
    {'reset': 'true'}, {'camera_intrinsic': []},
    {'camera_intrinsic': [[0, 0, 2], [0, 400, 2], [0, 0, 1]]},
])
def test_invalid_metadata_is_rejected_before_model_call(service, changes):
    client, agent = service
    assert client.post('/eval_dual', data=payload(**changes)).status_code == 400
    assert not agent.calls


@pytest.mark.parametrize('mutation', ['missing_rgb', 'missing_depth', 'invalid_json', 'corrupt_rgb', 'uint8_depth', 'unaligned_depth', 'zero_depth'])
def test_invalid_image_payload_is_rejected(service, mutation):
    client, agent = service
    data = payload()
    if mutation == 'missing_rgb':
        del data['image']
    elif mutation == 'missing_depth':
        del data['depth']
    elif mutation == 'invalid_json':
        data['json'] = '{'
    elif mutation == 'corrupt_rgb':
        data['image'] = (io.BytesIO(b'not an image'), 'bad.jpg')
    elif mutation == 'uint8_depth':
        data['depth'] = (encoded(np.ones((4, 5), dtype=np.uint8), 'PNG'), 'depth.png')
    elif mutation == 'unaligned_depth':
        data['depth'] = (encoded(np.ones((3, 5), dtype=np.uint16), 'PNG'), 'depth.png')
    elif mutation == 'zero_depth':
        data['depth'] = (encoded(np.zeros((4, 5), dtype=np.uint16), 'PNG'), 'depth.png')
    response = client.post('/eval_dual', data=data)
    assert response.status_code == 400
    assert not agent.calls


def test_validated_trajectory_response(service):
    client, agent = service
    agent.output = SimpleNamespace(output_action=None, output_trajectory=np.array([[0., 0.], [1., .2]]), output_pixel=[20, 30])
    response = client.post('/eval_dual', data=payload())
    assert response.status_code == 200
    assert response.json == {'trajectory': [[0.0, 0.0], [1.0, 0.2]], 'pixel_goal': [20.0, 30.0]}


@pytest.mark.parametrize('output', [
    SimpleNamespace(output_action=[], output_trajectory=None, output_pixel=None),
    SimpleNamespace(output_action=[99], output_trajectory=None, output_pixel=None),
    SimpleNamespace(output_action=None, output_trajectory=[[0, 0], [float('nan'), 0]], output_pixel=None),
    SimpleNamespace(output_action=None, output_trajectory=None, output_pixel=None),
])
def test_invalid_model_outputs_fail_closed(service, output):
    client, agent = service
    agent.output = output
    response = client.post('/eval_dual', data=payload())
    assert response.status_code == 503
    assert 'discrete_action' not in response.json
    assert 'trajectory' not in response.json
    assert client.post('/eval_dual', data=payload(reset=False)).status_code == 400


def test_failure_clears_episode_and_requires_reset(service):
    client, agent = service
    assert client.post('/eval_dual', data=payload()).status_code == 200
    agent.fail = True
    response = client.post('/eval_dual', data=payload(reset=False))
    assert response.status_code == 503
    assert response.json.keys() == {'error'}
    agent.fail = False
    assert client.post('/eval_dual', data=payload(reset=False)).status_code == 400
    assert client.post('/eval_dual', data=payload()).status_code == 200


def test_server_intrinsic_default():
    agent = FakeAgent()
    client = create_app(agent, camera_intrinsic=K).test_client()
    data = payload()
    data['json'] = json.dumps({'instruction': 'Turn left', 'reset': True})
    assert client.post('/eval_dual', data=data).status_code == 200
    assert parse_intrinsic(json.dumps(K)).shape == (4, 4)
