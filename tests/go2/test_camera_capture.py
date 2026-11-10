"""Camera sample format tests; no USB devices, ROS, or robot control are used."""

import builtins
import importlib
import json
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
from PIL import Image

from go2 import capture_realsense as camera


def device(name='Intel RealSense D435I', serial='435', rgbd=True):
    return {'device': object(), 'info': {'name': name, 'serial_number': serial}, 'has_rgbd': rgbd}


def test_unique_d435i_is_preferred_to_other_cameras():
    preferred, other = device(), device('Intel RealSense D455', '455')
    assert camera.select_device([other, preferred]) is preferred
    assert camera.select_device([preferred, other], '455') is other


def test_ambiguous_and_missing_devices_require_explicit_selection():
    with pytest.raises(ValueError, match='Multiple'):
        camera.select_device([device(serial='one'), device(serial='two')])
    with pytest.raises(ValueError, match='Multiple'):
        camera.select_device([device('D455', 'one'), device('D415', 'two')])
    with pytest.raises(ValueError, match='No RGB-D'):
        camera.select_device([device(rgbd=False)])
    with pytest.raises(ValueError, match='No unique'):
        camera.select_device([device()], 'missing')


def test_depth_uses_actual_non_millimetre_sensor_scale():
    encoded, stats = camera.encode_depth_png(np.array([[4000, 10000]], np.uint16), 0.00025)
    np.testing.assert_array_equal(encoded, [[10000, 25000]])
    assert encoded.dtype == np.uint16
    assert stats['measured_min_m'] == 1.0
    assert stats['measured_max_m'] == 2.5
    assert stats['encoded_valid_fraction'] == 1.0


def test_invalid_and_unrepresentable_pixels_are_zero_not_saturated():
    raw_metres = np.array([[0.0, -1.0, np.nan, np.inf, 2.0, 6.55351, 6.55355, 100.0]])
    encoded, stats = camera.encode_depth_png(raw_metres, 1.0)
    np.testing.assert_array_equal(encoded, [[0, 0, 0, 0, 20000, 0, 0, 0]])
    assert stats['out_of_png_range_count'] == 3
    assert stats['measured_max_m'] == 100.0


def test_rounding_and_exact_uint16_boundary_do_not_wrap():
    encoded, stats = camera.encode_depth_png(np.array([[1, 3, 5]], np.uint16), 0.00025)
    np.testing.assert_array_equal(encoded, [[2, 8, 12]])  # nearest, ties to even
    encoded, _ = camera.encode_depth_png(np.array([[65535]], np.uint16), 0.0001)
    assert encoded[0, 0] == 65535
    encoded, stats = camera.encode_depth_png(np.array([[0.00001, 0.00006, 2.0]]), 1.0)
    np.testing.assert_array_equal(encoded, [[0, 1, 20000]])
    assert stats['below_png_resolution_count'] == 1


@pytest.mark.parametrize('raw,scale', [([[0]], 0.001), ([[65535]], 0.001),
    ([[1]], 0), ([[1]], float('nan')), ([1, 2], 0.001)])
def test_invalid_scale_shape_or_entirely_missing_depth_fails(raw, scale):
    with pytest.raises(ValueError):
        camera.encode_depth_png(np.asarray(raw), scale)


def test_color_depth_shape_and_encodings_are_validated():
    rgb = np.zeros((2, 3, 3), np.uint8)
    camera.validate_aligned_images(rgb, np.ones((2, 3), np.uint16))
    with pytest.raises(ValueError, match='aligned'):
        camera.validate_aligned_images(rgb, np.ones((3, 2), np.uint16))
    with pytest.raises(ValueError, match='RGB8'):
        camera.validate_aligned_images(rgb.astype(float), np.ones((2, 3), np.uint16))
    with pytest.raises(ValueError, match='Z16'):
        camera.validate_aligned_images(rgb, np.ones((2, 3), np.float32))


def test_list_path_does_not_import_numpy_or_pillow(monkeypatch, capsys):
    original_import = builtins.__import__
    def guarded_import(name, *args, **kwargs):
        if name.split('.')[0] in ('numpy', 'PIL'):
            raise AssertionError('listing hardware must not need capture/image dependencies')
        return original_import(name, *args, **kwargs)
    monkeypatch.setattr(builtins, '__import__', guarded_import)
    monkeypatch.setattr(camera, 'load_realsense', lambda: object())
    monkeypatch.setattr(camera, 'discover_devices', lambda rs: [device()])
    camera.main(['--list'])
    assert json.loads(capsys.readouterr().out)[0]['serial_number'] == '435'


def test_sdk_failure_reports_current_runtime_without_installing(monkeypatch):
    def missing(name):
        raise ImportError('missing SDK')
    monkeypatch.setattr(importlib, 'import_module', missing)
    with pytest.raises(RuntimeError) as caught:
        camera.load_realsense()
    message = str(caught.value)
    assert camera.sys.executable in message
    assert camera.platform.machine() in message
    assert 'lsusb' in message
    assert 'does not install' in message


@pytest.fixture
def fake_capture():
    calls = []
    rgb = np.zeros((2, 3, 3), np.uint8)
    rgb[0, 0] = [120, 35, 7]
    depth = np.array([[4000, 8000, 30000], [4000, 0, 4000]], np.uint16)
    intrinsics = SimpleNamespace(width=3, height=2, fx=100.0, fy=101.0, ppx=1.25, ppy=0.75,
                                 model='brown_conrady', coeffs=[0.1, 0, 0, 0, 0])
    video = SimpleNamespace(get_intrinsics=lambda: intrinsics, fps=lambda: 30)
    def frame(data, number):
        return SimpleNamespace(get_data=lambda: data, get_profile=lambda: SimpleNamespace(as_video_stream_profile=lambda: video),
            get_frame_number=lambda: number, get_timestamp=lambda: 2000.0,
            get_frame_timestamp_domain=lambda: 'hardware_clock')
    color_frame, depth_frame = frame(rgb, 10), frame(depth, 11)
    frames = SimpleNamespace(get_color_frame=lambda: color_frame, get_depth_frame=lambda: depth_frame)
    info = {'name': 'Intel RealSense D435I', 'serial_number': '435', 'firmware_version': 'x', 'usb_type_descriptor': '3.2'}
    hardware = SimpleNamespace(supports=lambda key: True, get_info=lambda key: info[key],
                               first_depth_sensor=lambda: SimpleNamespace(get_depth_scale=lambda: 0.00025))
    profile = SimpleNamespace(get_device=lambda: hardware)
    def wait(**kwargs):
        calls.append('frame')
        return frames
    pipeline = SimpleNamespace(start=lambda config: profile, stop=lambda: calls.append('stop'), wait_for_frames=wait)
    config = SimpleNamespace(enable_device=lambda serial: calls.append(('serial', serial)),
                             enable_stream=lambda *args: calls.append(('stream', args)))
    rs = SimpleNamespace(pipeline=lambda: pipeline, config=lambda: config,
        align=lambda stream: SimpleNamespace(process=lambda frameset: frameset),
        stream=SimpleNamespace(color='color', depth='depth'), format=SimpleNamespace(rgb8='rgb8', z16='z16'),
        camera_info=SimpleNamespace(**{key: key for key in info}))
    return rs, device(), calls, rgb, depth


def test_capture_saves_http_inputs_calibration_and_unmodified_raw_depth(tmp_path, fake_capture):
    rs, selected, calls, rgb, raw = fake_capture
    args = SimpleNamespace(output=tmp_path / 'sample', width=3, height=2, fps=30, warmup=2)
    output, metadata = camera.capture(rs, selected, args)
    assert calls.count('frame') == 3
    assert calls[-1] == 'stop'
    assert {path.name for path in output.iterdir()} == {
        'rgb.png', 'depth.png', 'metadata.json', 'camera_intrinsic.json', 'depth_raw.npy'}
    np.testing.assert_array_equal(np.asarray(Image.open(output / 'rgb.png')), rgb)
    np.testing.assert_array_equal(np.load(output / 'depth_raw.npy', allow_pickle=False), raw)
    encoded_png = (output / 'depth.png').read_bytes()
    assert encoded_png[24:26] == bytes([16, 0])
    saved = np.asarray(Image.open(output / 'depth.png'))
    assert saved[0, 0] == 10000
    assert saved[0, 1] == 20000
    assert saved[0, 2] == 0  # 7.5m cannot be represented; raw value is preserved separately
    intrinsic = json.loads((output / 'camera_intrinsic.json').read_text())
    assert intrinsic == [[100.0, 0, 1.25], [0, 101.0, 0.75], [0, 0, 1]]
    assert metadata['depth_raw_unit_m'] == 0.00025
    assert metadata['color_frame']['frame_number'] == 10
    assert metadata['depth_frame']['frame_number'] == 11
    assert json.loads((output / 'metadata.json').read_text())['depth_aligned_to'] == 'color'


def test_capture_releases_pipeline_after_failure(tmp_path, fake_capture, monkeypatch):
    rs, selected, calls, _, _ = fake_capture
    args = SimpleNamespace(output=tmp_path / 'sample', width=3, height=2, fps=30, warmup=0)
    def bad_depth(*args):
        raise ValueError('invalid depth')
    monkeypatch.setattr(camera, 'encode_depth_png', bad_depth)
    with pytest.raises(ValueError, match='invalid depth'):
        camera.capture(rs, selected, args)
    assert calls[-1] == 'stop'
    assert not args.output.exists()


def test_existing_output_is_never_overwritten(tmp_path, fake_capture):
    rs, selected, calls, _, _ = fake_capture
    args = SimpleNamespace(output=tmp_path, width=3, height=2, fps=30, warmup=0)
    with pytest.raises(ValueError, match='already exists'):
        camera.capture(rs, selected, args)
    assert not calls
