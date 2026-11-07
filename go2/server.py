"""Stateful InternVLA-N1 inference service; this process never controls a robot."""

import argparse
import io
import json
import threading
from pathlib import Path

import numpy as np
from flask import Flask, jsonify, request
from PIL import Image, UnidentifiedImageError
from werkzeug.exceptions import BadRequest, RequestEntityTooLarge


def parse_intrinsic(value):
    """Accept a calibrated 3x3 or homogeneous 4x4 camera matrix."""
    if isinstance(value, str):
        value = json.loads(value)
    matrix = np.asarray(value, dtype=np.float64)
    if matrix.shape not in ((3, 3), (4, 4)) or not np.isfinite(matrix).all():
        raise ValueError('camera_intrinsic must be a finite 3x3 or 4x4 matrix')
    if matrix[0, 0] <= 0 or matrix[1, 1] <= 0 or not np.allclose(matrix[2, :3], [0, 0, 1]):
        raise ValueError('camera_intrinsic must contain positive focal lengths and last row [0,0,1]')
    if matrix.shape == (3, 3):
        result = np.eye(4)
        result[:3, :3] = matrix
        return result
    return matrix


def decode_observation(files):
    if 'image' not in files or 'depth' not in files:
        raise ValueError('multipart fields image and depth are required')
    try:
        with Image.open(files['image'].stream) as image:
            if image.width > 4096 or image.height > 4096:
                raise ValueError('image dimensions must not exceed 4096 pixels')
            rgb = np.asarray(image.convert('RGB'))
        encoded_depth = files['depth'].read()
        # PNG IHDR: byte 24 is sample bit depth, byte 25 is color type.
        if encoded_depth[:8] != b'\x89PNG\r\n\x1a\n' or encoded_depth[24:26] != bytes([16, 0]):
            raise ValueError('depth must be a single-channel 16-bit PNG, scaled by 10000 per metre')
        with Image.open(io.BytesIO(encoded_depth)) as image:
            if image.size != (rgb.shape[1], rgb.shape[0]):
                raise ValueError('RGB and aligned depth dimensions must match')
            depth = np.asarray(image).astype(np.float32) / 10000.0
        if depth.ndim != 2 or not np.isfinite(depth).all():
            raise ValueError('depth must be a finite single-channel image')
        if not np.any(depth > 0):
            raise ValueError('depth image has no valid measurements')
        return rgb, depth
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as exc:
        raise ValueError('could not decode RGB/depth image') from exc


def encode_output(output):
    if output.output_action is not None:
        actions = output.output_action
        if not isinstance(actions, (list, tuple)) or not actions:
            raise ValueError('model produced an empty action sequence')
        if any(type(action) is not int or action not in (0, 1, 2, 3, 5, 9) for action in actions):
            raise ValueError('model produced an unsupported discrete action')
        return {'discrete_action': list(actions)}
    if output.output_trajectory is not None:
        trajectory = np.asarray(output.output_trajectory, dtype=np.float64)
        if trajectory.ndim != 2 or trajectory.shape[1] != 2 or len(trajectory) < 2:
            raise ValueError('model produced an invalid trajectory shape')
        if not np.isfinite(trajectory).all():
            raise ValueError('model produced a non-finite trajectory')
        result = {'trajectory': trajectory.tolist()}
        if output.output_pixel is not None:
            pixel = np.asarray(output.output_pixel, dtype=np.float64)
            if pixel.shape != (2,) or not np.isfinite(pixel).all():
                raise ValueError('model produced an invalid pixel goal')
            result['pixel_goal'] = pixel.tolist()
        return result
    raise ValueError('model produced neither actions nor a trajectory')


def create_app(agent, camera_intrinsic=None):
    """Inject an already loaded agent; tests use a fake, production loads the model."""
    app = Flask(__name__)
    app.config['MAX_CONTENT_LENGTH'] = 20 * 1024 * 1024
    default_intrinsic = parse_intrinsic(camera_intrinsic) if camera_intrinsic is not None else None
    lock = threading.Lock()
    state = {'instruction': None, 'needs_reset': True}

    @app.errorhandler(RequestEntityTooLarge)
    def too_large(error):
        return jsonify(error='request exceeds 20 MiB'), 413

    @app.get('/healthz')
    def health():
        return jsonify(status='ready', model=type(agent).__name__, protocol='internnav-go2-v1')

    @app.post('/eval_dual')
    def evaluate():
        try:
            data = json.loads(request.form.get('json', '{}'))
            if not isinstance(data, dict):
                raise ValueError('json must be an object')
            instruction = data.get('instruction')
            if not isinstance(instruction, str) or not instruction.strip() or len(instruction) > 4096:
                raise ValueError('instruction must be a nonempty string of at most 4096 characters')
            instruction = instruction.strip()
            reset = data.get('reset', False)
            if type(reset) is not bool:
                raise ValueError('reset must be a boolean')
            intrinsic = parse_intrinsic(data['camera_intrinsic']) if 'camera_intrinsic' in data else default_intrinsic
            if intrinsic is None:
                raise ValueError('camera_intrinsic is required (request or server option)')
            rgb, depth = decode_observation(request.files)
        except (ValueError, TypeError, KeyError, BadRequest) as exc:
            return jsonify(error=str(exc)), 400

        # One process serves one robot/episode. Keep reset and inference atomic.
        with lock:
            if state['instruction'] is not None and instruction != state['instruction'] and not reset:
                return jsonify(error='instruction changed; send reset=true to start a new episode'), 400
            if state['needs_reset'] and not reset:
                return jsonify(error='send reset=true to start or recover an episode'), 400
            try:
                if reset:
                    agent.reset()
                    state.update(instruction=instruction, needs_reset=False)
                output = agent.step(rgb, depth, np.eye(4), instruction, intrinsic=intrinsic)
                return jsonify(encode_output(output))
            except Exception:
                # Do not reuse latent/action state after a failed request.
                state.update(instruction=None, needs_reset=True)
                try:
                    agent.reset()
                except Exception:
                    app.logger.exception('Failed to reset agent after inference error')
                app.logger.exception('Model inference failed; a fresh episode is required')
                return jsonify(error='model inference failed; stop motion and retry with reset=true'), 503

    return app


def load_agent(args):
    import torch

    if not args.device.startswith('cuda') or not torch.cuda.is_available():
        raise RuntimeError('InternVLA-N1 inference requires an NVIDIA CUDA GPU; run this server on your GPU workstation')
    device = torch.device(args.device)
    torch.cuda.get_device_properties(device)
    with torch.cuda.device(device):
        if not torch.cuda.is_bf16_supported():
            raise RuntimeError('This model runs in bfloat16; use a GPU with bfloat16 support')
    model_path = Path(args.model_path).expanduser().resolve()
    if not (model_path / 'config.json').is_file():
        raise RuntimeError(f'model checkpoint config.json not found: {model_path}')
    config = json.loads((model_path / 'config.json').read_text())
    if 'nextdit' not in config.get('system1', ''):
        raise RuntimeError('This Go2 configuration expects the InternVLA-N1-DualVLN checkpoint (nextdit system1)')
    depth_encoder = Path('checkpoints/depth_anything_v2_metric_hypersim_vits.pth')
    if not depth_encoder.is_file():
        raise RuntimeError(f'missing {depth_encoder}; run from the repository root after downloading models')

    from internnav.agent.internvla_n1_agent_realworld import InternVLAN1AsyncAgent

    args.model_path = str(model_path)
    return InternVLAN1AsyncAgent(args)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--model-path', required=True)
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--port', type=int, default=5801)
    parser.add_argument('--device', default='cuda:0')
    parser.add_argument('--plan-step-gap', type=int, default=4)
    parser.add_argument('--attention', choices=['sdpa', 'flash_attention_2', 'eager'], default='sdpa')
    parser.add_argument('--camera-intrinsic', help='calibrated 3x3/4x4 matrix as JSON; alternatively send it per request')
    parser.add_argument('--resize-w', type=int, default=384)
    parser.add_argument('--resize-h', type=int, default=384)
    parser.add_argument('--num-history', type=int, default=8)
    parser.add_argument('--output-dir', default='test_data')
    args = parser.parse_args()
    if not 1 <= args.port <= 65535 or args.plan_step_gap < 0 or args.num_history < 1:
        parser.error('port must be 1..65535, plan-step-gap >= 0, num-history >= 1')
    if min(args.resize_w, args.resize_h) < 1:
        parser.error('resize dimensions must be positive')
    try:
        intrinsic = parse_intrinsic(args.camera_intrinsic) if args.camera_intrinsic else None
        agent = load_agent(args)
    except (ValueError, RuntimeError) as exc:
        parser.exit(1, f'{exc}\n')
    app = create_app(agent, camera_intrinsic=intrinsic)
    app.run(host=args.host, port=args.port, threaded=False, use_reloader=False)


if __name__ == '__main__':
    main()
