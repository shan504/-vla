"""Send one saved, calibrated RGB-D observation to the navigation model service.

The capture format is rgb.png, depth.png (uint16, 10000 units per metre), and
camera_intrinsic.json. Every invocation starts a fresh model episode. This
client prints a prediction; it has no robot control interface.
"""

import argparse
import io
import ipaddress
import json
import math
import sys
import time
from pathlib import Path
from urllib.parse import urlsplit

import numpy as np
import requests
from PIL import Image, UnidentifiedImageError


def load_capture(directory):
    """Validate protocol inputs while retaining original image bytes and K."""
    directory = Path(directory)
    rgb = (directory / 'rgb.png').read_bytes()
    depth = (directory / 'depth.png').read_bytes()
    intrinsic = json.loads((directory / 'camera_intrinsic.json').read_text())
    matrix = np.asarray(intrinsic, dtype=np.float64)
    if matrix.shape not in ((3, 3), (4, 4)) or not np.isfinite(matrix).all():
        raise ValueError('camera_intrinsic.json must contain a finite 3x3 or 4x4 matrix')
    if matrix[0, 0] <= 0 or matrix[1, 1] <= 0 or not np.allclose(matrix[2, :3], [0, 0, 1]):
        raise ValueError('camera_intrinsic.json must contain positive focal lengths and row [0,0,1]')
    if len(depth) < 26 or depth[:8] != b'\x89PNG\r\n\x1a\n' or depth[24:26] != bytes([16, 0]):
        raise ValueError('depth.png must be a single-channel 16-bit PNG, scaled by 10000 per metre')
    try:
        with Image.open(io.BytesIO(rgb)) as image:
            if image.width > 4096 or image.height > 4096:
                raise ValueError('RGB dimensions must not exceed 4096 pixels')
            image.convert('RGB').load()
            size = image.size
        with Image.open(io.BytesIO(depth)) as image:
            if image.size != size:
                raise ValueError('RGB and aligned depth dimensions must match')
            values = np.asarray(image)
            if values.ndim != 2 or not np.any(values > 0):
                raise ValueError('depth.png has no valid depth measurements')
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as exc:
        raise ValueError('Could not decode captured RGB/depth images') from exc
    metadata_path = directory / 'metadata.json'
    if metadata_path.exists():
        metadata = json.loads(metadata_path.read_text())
        if not isinstance(metadata, dict):
            raise ValueError('metadata.json must contain an object')
        unit = metadata.get('depth_png_unit_m', 0.0001)
        if not math.isclose(float(unit), 0.0001, rel_tol=0, abs_tol=1e-12):
            raise ValueError('Capture depth unit is not 1/10000 metre; convert before inference')
        if metadata.get('depth_aligned_to', 'color') != 'color':
            raise ValueError('Captured depth must be aligned to color')
    if len(rgb) + len(depth) >= 20 * 1024 * 1024:
        raise ValueError('Capture exceeds the service 20 MiB request limit')
    return rgb, depth, intrinsic


def service_url(url):
    """Accept a base service URL or its /eval_dual endpoint."""
    parsed = urlsplit(url)
    if parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.query or parsed.fragment:
        raise ValueError('--url must be an HTTP(S) service URL without query or fragment')
    parsed.port  # Validate a malformed port before making any request.
    base = url.rstrip('/')
    if base.endswith('/eval_dual'):
        base = base[:-len('/eval_dual')]
    return base, parsed.hostname


def is_loopback(host):
    if host.lower().rstrip('.') == 'localhost':
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def predict_capture(directory, instruction, url='http://127.0.0.1:5801', timeout=180, session=None):
    if not isinstance(instruction, str) or not instruction.strip() or len(instruction.strip()) > 4096:
        raise ValueError('--instruction must contain 1..4096 non-whitespace characters')
    if not math.isfinite(timeout) or timeout <= 0:
        raise ValueError('--timeout must be a positive finite number of seconds')
    base, host = service_url(url)
    rgb, depth, intrinsic = load_capture(directory)
    owned_session = session is None
    session = session if session is not None else requests.Session()
    # The Slurm client talks to a loopback service on its allocated node. A
    # login-shell proxy must not send that request to a different machine.
    if is_loopback(host):
        session.trust_env = False
    try:
        health = session.get(base + '/healthz', timeout=(min(5, timeout), min(15, timeout)))
        if health.status_code != 200:
            raise RuntimeError('Service is not ready: HTTP {} {}'.format(health.status_code, health.text[:2000]))
        health_state = health.json()
        if not isinstance(health_state, dict) or health_state.get('status') != 'ready':
            raise RuntimeError('Service /healthz did not report status=ready')
        fields = {'instruction': instruction.strip(), 'reset': True, 'camera_intrinsic': intrinsic}
        start = time.perf_counter()
        response = session.post(
            base + '/eval_dual', data={'json': json.dumps(fields)},
            files={'image': ('rgb.png', rgb, 'image/png'), 'depth': ('depth.png', depth, 'image/png')},
            timeout=(min(5, timeout), timeout),
        )
        elapsed = time.perf_counter() - start
        try:
            output = response.json()
        except ValueError:
            output = response.text
        return {'http_status': response.status_code, 'elapsed_seconds': round(elapsed, 3), 'output': output}
    finally:
        if owned_session:
            session.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('capture_dir', type=Path)
    parser.add_argument('--instruction', required=True)
    parser.add_argument('--url', default='http://127.0.0.1:5801', help='service base URL or /eval_dual URL')
    parser.add_argument('--timeout', type=float, default=180, help='inference read timeout in seconds')
    args = parser.parse_args(argv)
    try:
        result = predict_capture(args.capture_dir, args.instruction, args.url, args.timeout)
    except (OSError, ValueError, TypeError, RuntimeError, requests.RequestException) as exc:
        print('Inference request failed: {}'.format(exc), file=sys.stderr)
        print('Check that the model job is running and inspect its logs/slurm/internnav-<JOBID>.out/.err.', file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2), flush=True)
    output = result['output']
    if result['http_status'] != 200 or not isinstance(output, dict) or not any(
        name in output for name in ('discrete_action', 'trajectory')
    ):
        print('Prediction failed; inspect the model server logs/slurm/internnav-<JOBID>.out/.err.', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
