"""Run one RGB-only S2 prediction, save its result, and exit.

This path does not invent depth or camera calibration, run S1, or control a robot.
"""

import argparse
import json
import math
import sys
import time
from datetime import datetime
from pathlib import Path

from PIL import Image, ImageDraw


def check_inputs(args):
    if not args.instruction.strip() or len(args.instruction) > 4096:
        raise ValueError('instruction must contain 1..4096 non-whitespace characters')
    if min(args.resize_w, args.resize_h) < 1:
        raise ValueError('resize dimensions must be positive')
    with Image.open(args.image) as image:
        if max(image.size) > 4096:
            raise ValueError('image dimensions must not exceed 4096 pixels')
        rgb = image.convert('RGB')
        rgb.load()
    config = json.loads((args.model_path / 'config.json').read_text())
    if 'nextdit' not in config.get('system1', ''):
        raise ValueError('expected the InternVLA-N1-DualVLN nextdit checkpoint')
    index = args.model_path / 'model.safetensors.index.json'
    names = set(json.loads(index.read_text())['weight_map'].values()) if index.exists() else {'model.safetensors'}
    for name in names:
        if not (args.model_path / name).is_file():
            raise ValueError('missing model shard: ' + name)
    depth = Path('checkpoints/depth_anything_v2_metric_hypersim_vits.pth')
    if not depth.is_file():
        raise ValueError('missing auxiliary RGB-backbone checkpoint: ' + str(depth))
    return rgb


def prediction(actions, pixel, source_size, resized_size):
    if actions is not None:
        if not isinstance(actions, (list, tuple)) or not actions or any(
            type(action) is not int or action not in (0, 1, 2, 3, 5, 9) for action in actions
        ):
            raise ValueError('model returned an empty or unsupported discrete action sequence')
        return {'discrete_actions': list(actions)}
    if pixel is None or len(pixel) != 2:
        raise ValueError('model returned neither actions nor an image waypoint')
    x, y = float(pixel[0]), float(pixel[1])
    if not (math.isfinite(x) and math.isfinite(y) and 0 <= x < resized_size[0] and 0 <= y < resized_size[1]):
        raise ValueError('model waypoint is outside the resized image: ' + str(pixel))
    return {'model_pixel_xy': [x, y],
            'source_pixel_xy': [x * source_size[0] / resized_size[0], y * source_size[1] / resized_size[1]]}


def infer(args, rgb, loader, synchronize, clock=time.perf_counter):
    """Inject loading/synchronization in tests; production uses the actual GPU agent."""
    import numpy as np

    output = args.output_dir
    output.mkdir(parents=True, exist_ok=False)
    start = clock()
    agent = loader(args)
    synchronize()
    load_seconds = clock() - start
    agent.reset()
    synchronize()
    start = clock()
    actions, _latent, pixel = agent.step_s2(
        np.asarray(rgb), None, np.eye(4), args.instruction.strip(), intrinsic=None,
    )
    synchronize()
    inference_seconds = clock() - start
    result = {
        'mode': 'rgb_s2_only', 'image': str(args.image.resolve()),
        'instruction': args.instruction.strip(), 'source_size_wh': list(rgb.size),
        'resized_size_wh': [args.resize_w, args.resize_h],
        'model_load_seconds': round(load_seconds, 3), 'inference_seconds': round(inference_seconds, 3),
        'raw_text': agent.llm_output,
        **prediction(actions, pixel, rgb.size, (args.resize_w, args.resize_h)),
    }
    preview = rgb.copy()
    draw = ImageDraw.Draw(preview)
    if 'source_pixel_xy' in result:
        x, y = result['source_pixel_xy']
        radius = max(8, min(rgb.size) // 40)
        draw.ellipse((x - radius, y - radius, x + radius, y + radius), outline='red', width=4)
        draw.line((x - radius, y, x + radius, y), fill='red', width=3)
        draw.line((x, y - radius, x, y + radius), fill='red', width=3)
    else:
        label = 'STOP' if result['discrete_actions'] == [0] else 'Actions: ' + str(result['discrete_actions'])
        draw.text((16, 16), label, fill='red')
    preview.save(output / 'preview.png')
    result['preview'] = str((output / 'preview.png').resolve())
    (output / 'result.json').write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image', type=Path, required=True)
    parser.add_argument('--instruction', required=True)
    parser.add_argument('--model-path', type=Path, default=Path('checkpoints/InternVLA-N1-DualVLN'))
    parser.add_argument('--device', default='cuda:0')
    parser.add_argument('--attention', choices=['sdpa', 'eager', 'flash_attention_2'], default='sdpa')
    parser.add_argument('--resize-w', type=int, default=384)
    parser.add_argument('--resize-h', type=int, default=384)
    parser.add_argument('--output-dir', type=Path, default=Path('output') / ('rgb-once-' + datetime.now().strftime('%Y%m%d-%H%M%S-%f')))
    parser.add_argument('--check-input', action='store_true', help='check inputs on CPU, without importing Torch or loading the model')
    args = parser.parse_args(argv)
    args.num_history, args.plan_step_gap = 8, 4
    try:
        rgb = check_inputs(args)
        if args.check_input:
            print(json.dumps({'status': 'input_ok', 'source_size_wh': list(rgb.size), 'mode': 'rgb_s2_only'}))
            return 0
        # All GPU/model imports stay below the CPU preflight exit.
        import torch
        from go2.server import load_agent
        result = infer(args, rgb, load_agent, lambda: torch.cuda.synchronize(args.device))
        print(json.dumps(result, ensure_ascii=False, indent=2), flush=True)
        print('Result directory:', args.output_dir.resolve(), flush=True)
    except Exception as exc:
        import traceback
        traceback.print_exc()
        print('RGB prediction failed: ' + str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
