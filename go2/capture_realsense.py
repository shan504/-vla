"""Capture one aligned RGB-D sample without ROS or any robot control interface."""

import argparse
import importlib
import json
import math
import platform
import sys
from datetime import datetime, timezone
from pathlib import Path

PNG_DEPTH_UNIT_M = 0.0001
PNG_DEPTH_MAX_M = 65535 / 10000


def runtime_description():
    return f'Python: {sys.executable} ({platform.python_version()}); architecture: {platform.machine()}'


def load_realsense():
    try:
        rs = importlib.import_module('pyrealsense2')
        if not all(hasattr(rs, name) for name in ('context', 'pipeline', 'config', 'align')):
            raise ImportError('module does not expose the RealSense Python SDK API')
        return rs
    except Exception as exc:
        raise RuntimeError(
            f'Cannot load pyrealsense2: {exc}\n{runtime_description()}\n'
            'First check USB detection with lsusb and the installed librealsense/Python binding '
            'for this Jetson/Python version. Do not install an x86_64 wheel on aarch64; '
            'this tool does not install or modify dependencies.'
        ) from exc


def device_information(device, rs):
    result = {}
    for field in ('name', 'serial_number', 'firmware_version', 'usb_type_descriptor'):
        try:
            key = getattr(rs.camera_info, field)
            result[field] = device.get_info(key) if device.supports(key) else None
        except (AttributeError, RuntimeError):
            result[field] = None
    return result


def discover_devices(rs):
    context = rs.context()
    devices = []
    for device in context.query_devices():
        streams = {profile.stream_type() for sensor in device.query_sensors()
                   for profile in sensor.get_stream_profiles()}
        devices.append({'device': device, 'info': device_information(device, rs),
                        'has_rgbd': rs.stream.color in streams and rs.stream.depth in streams})
    return devices


def select_device(devices, serial=None):
    candidates = [item for item in devices if item['has_rgbd']]
    if serial:
        candidates = [item for item in candidates if item['info']['serial_number'] == serial]
        if len(candidates) != 1:
            raise ValueError(f'No unique RGB-D device with serial {serial!r}; run --list first')
        return candidates[0]
    preferred = [item for item in candidates if 'd435i' in (item['info']['name'] or '').lower()]
    if preferred:
        candidates = preferred
    if not candidates:
        raise ValueError('No RGB-D RealSense device found; check USB cable, permissions and --list')
    if len(candidates) > 1:
        raise ValueError('Multiple RGB-D devices match; run --list and choose --serial explicitly')
    if not candidates[0]['info']['serial_number']:
        raise ValueError('Device serial is unavailable; cannot select the camera unambiguously')
    return candidates[0]


def encode_depth_png(raw_depth, depth_scale):
    """Preserve measured distances; unrepresentable pixels become invalid, never saturated."""
    import numpy as np

    if not math.isfinite(depth_scale) or depth_scale <= 0:
        raise ValueError('depth_scale must be positive and finite (metres per raw unit)')
    raw = np.asarray(raw_depth)
    if raw.ndim != 2 or not raw.size or not np.issubdtype(raw.dtype, np.number):
        raise ValueError('aligned raw depth must be a nonempty two-dimensional numeric array')
    metres = raw.astype(np.float64) * depth_scale
    measured = np.isfinite(metres) & (metres > 0)
    # Permit one floating-point ULP at the exact 65535-unit boundary.
    above_range = measured & (metres > np.nextafter(PNG_DEPTH_MAX_M, np.inf))
    representable = measured & ~above_range
    rounded = np.zeros(raw.shape, dtype=np.float64)
    rounded[representable] = np.rint(metres[representable] / PNG_DEPTH_UNIT_M)
    below_resolution = representable & (rounded < 1)
    valid = representable & ~below_resolution & (rounded <= 65535)
    encoded = np.zeros(raw.shape, dtype=np.uint16)
    encoded[valid] = rounded[valid].astype(np.uint16)
    if not np.any(valid):
        raise ValueError('No valid depth remains in the PNG range (0, 6.5535] metres; check the camera/scene')
    centre = (raw.shape[0] // 2, raw.shape[1] // 2)
    stats = {
        'pixel_count': int(raw.size),
        'measured_count': int(np.count_nonzero(measured)),
        'encoded_valid_count': int(np.count_nonzero(valid)),
        'encoded_valid_fraction': float(np.count_nonzero(valid) / raw.size),
        'out_of_png_range_count': int(np.count_nonzero(above_range)),
        'below_png_resolution_count': int(np.count_nonzero(below_resolution)),
        'measured_min_m': float(np.min(metres[measured])),
        'measured_max_m': float(np.max(metres[measured])),
        'centre_depth_m': float(metres[centre]) if measured[centre] else None,
        'centre_png_depth_m': float(encoded[centre]) * PNG_DEPTH_UNIT_M if valid[centre] else None,
    }
    return encoded, stats


def validate_aligned_images(rgb, raw_depth):
    import numpy as np

    if rgb.ndim != 3 or rgb.shape[2] != 3 or rgb.dtype != np.uint8:
        raise ValueError('color image must be RGB8 with shape (height, width, 3)')
    if raw_depth.ndim != 2 or raw_depth.dtype != np.uint16:
        raise ValueError('raw depth image must be Z16 with shape (height, width)')
    if rgb.shape[:2] != raw_depth.shape:
        raise ValueError('depth must be aligned to the color image with matching dimensions')


def frame_metadata(frame):
    return {
        'frame_number': int(frame.get_frame_number()),
        'timestamp_ms': float(frame.get_timestamp()),
        'timestamp_domain': str(frame.get_frame_timestamp_domain()),
    }


def capture(rs, selected, args):
    try:
        import numpy as np
        from PIL import Image
    except ImportError as exc:
        raise RuntimeError(f'Capture needs numpy and Pillow: {exc}\n{runtime_description()}\n'
                           'Check which of these two modules is missing before installing anything.') from exc
    output = args.output.expanduser()
    if output.exists():
        raise ValueError(f'Output already exists; choose a new --output to preserve existing files: {output}')
    pipeline, config = rs.pipeline(), rs.config()
    config.enable_device(selected['info']['serial_number'])
    config.enable_stream(rs.stream.color, args.width, args.height, rs.format.rgb8, args.fps)
    config.enable_stream(rs.stream.depth, args.width, args.height, rs.format.z16, args.fps)
    started = False
    try:
        profile = pipeline.start(config)
        started = True
        depth_scale = float(profile.get_device().first_depth_sensor().get_depth_scale())
        align = rs.align(rs.stream.color)
        for _ in range(args.warmup):
            pipeline.wait_for_frames(timeout_ms=10000)
        frames = align.process(pipeline.wait_for_frames(timeout_ms=10000))
        color_frame, depth_frame = frames.get_color_frame(), frames.get_depth_frame()
        if not color_frame or not depth_frame:
            raise RuntimeError('The aligned frameset does not contain both color and depth')
        rgb = np.asanyarray(color_frame.get_data()).copy()
        raw_depth = np.asanyarray(depth_frame.get_data()).copy()
        validate_aligned_images(rgb, raw_depth)
        encoded, stats = encode_depth_png(raw_depth, depth_scale)
        color_profile = color_frame.get_profile().as_video_stream_profile()
        intrinsics = color_profile.get_intrinsics()
        intrinsic = [[float(intrinsics.fx), 0.0, float(intrinsics.ppx)],
                     [0.0, float(intrinsics.fy), float(intrinsics.ppy)], [0.0, 0.0, 1.0]]
        if not all(math.isfinite(v) for row in intrinsic for v in row) or min(intrinsics.fx, intrinsics.fy) <= 0:
            raise RuntimeError('Camera returned invalid color intrinsics')
        if (intrinsics.width, intrinsics.height) != (rgb.shape[1], rgb.shape[0]):
            raise RuntimeError('Color calibration resolution differs from the aligned images')
        metadata = {
            'captured_at_utc': datetime.now(timezone.utc).isoformat(),
            'device': device_information(profile.get_device(), rs),
            'width': int(rgb.shape[1]), 'height': int(rgb.shape[0]), 'fps': int(color_profile.fps()),
            'rgb_encoding': 'RGB8', 'aligned_depth_encoding': 'Z16', 'depth_aligned_to': 'color',
            'depth_raw_unit_m': depth_scale,
            'depth_png_unit_m': PNG_DEPTH_UNIT_M, 'depth_png_invalid_value': 0,
            'depth_png_max_m': PNG_DEPTH_MAX_M,
            'depth_png_rounding': 'nearest integer, ties to even',
            'depth_png_out_of_range_policy': 'invalid zero; original aligned values retained in depth_raw.npy',
            'camera_intrinsic': intrinsic,
            'distortion_model': str(intrinsics.model),
            'distortion_coefficients': [float(v) for v in intrinsics.coeffs],
            'color_frame': frame_metadata(color_frame), 'depth_frame': frame_metadata(depth_frame),
            'depth_statistics': stats,
            'files': ['rgb.png', 'depth.png', 'camera_intrinsic.json', 'metadata.json', 'depth_raw.npy'],
        }
        output.mkdir(parents=True, exist_ok=False)
        Image.fromarray(rgb).save(output / 'rgb.png')
        Image.fromarray(encoded).save(output / 'depth.png')
        np.save(output / 'depth_raw.npy', raw_depth, allow_pickle=False)
        (output / 'camera_intrinsic.json').write_text(json.dumps(intrinsic, indent=2) + '\n', encoding='utf-8')
        (output / 'metadata.json').write_text(json.dumps(metadata, indent=2, allow_nan=False) + '\n', encoding='utf-8')
        return output, metadata
    finally:
        if started:
            try:
                pipeline.stop()
            except RuntimeError as exc:
                print(f'Camera cleanup warning: {exc}', file=sys.stderr)


def positive_int(value):
    result = int(value)
    if result <= 0:
        raise argparse.ArgumentTypeError('must be a positive integer')
    return result


def build_parser():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path,
                        default=Path('captures') / ('d435i-' + datetime.now().strftime('%Y%m%d-%H%M%S-%f')))
    parser.add_argument('--serial', help='select a specific camera by its device serial')
    parser.add_argument('--width', type=positive_int, default=640)
    parser.add_argument('--height', type=positive_int, default=480)
    parser.add_argument('--fps', type=positive_int, default=30)
    parser.add_argument('--warmup', type=int, default=30, help='frames to discard before capture (default: 30)')
    parser.add_argument('--list', action='store_true', help='list RealSense devices without starting streams')
    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.warmup < 0:
        parser.error('--warmup must not be negative')
    try:
        rs = load_realsense()
        devices = discover_devices(rs)
        if args.list:
            print(json.dumps([dict(item['info'], has_rgbd=item['has_rgbd']) for item in devices], indent=2))
            return
        selected = select_device(devices, args.serial)
        output, metadata = capture(rs, selected, args)
        print(json.dumps({'output': str(output.resolve()), **metadata}, indent=2, allow_nan=False))
    except (RuntimeError, ValueError, OSError) as exc:
        parser.exit(1, f'{exc}\n')


if __name__ == '__main__':
    main()
