"""Load optional baseline encoders only when requested."""
from importlib import import_module

_EXPORTS = {
    'PositionalEncoding': 'bert_backbone',
    'DistanceNetwork': 'distance_encoder',
    'ImageEncoder': 'image_clip_encoder',
    'InstructionEncoder': 'instruction_encoder',
    'InstructionLongCLIPEncoder': 'instruction_longCLIP_encoder',
    'LanguageEncoder': 'instruction_roberta_encoder',
    'VisionLanguageEncoder': 'vision_language_encoder',
}
__all__ = list(_EXPORTS)


def __getattr__(name):
    if name not in _EXPORTS:
        raise AttributeError(f'module {__name__!r} has no attribute {name!r}')
    value = getattr(import_module(f'.{_EXPORTS[name]}', __name__), name)
    globals()[name] = value
    return value
