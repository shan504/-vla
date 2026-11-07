"""Agent exports, loaded on demand so inference does not import every baseline."""

from importlib import import_module

_EXPORTS = {
    'Agent': ('base', 'Agent'),
    'DialogAgent': ('dialog_agent', 'DialogAgent'),
    'CmaAgent': ('cma_agent', 'CmaAgent'),
    'RdpAgent': ('rdp_agent', 'RdpAgent'),
    'Seq2SeqAgent': ('seq2seq_agent', 'Seq2SeqAgent'),
    'InternVLAN1Agent': ('internvla_n1_agent', 'InternVLAN1Agent'),
}
_AGENT_EXPORTS = {
    'dialog': 'DialogAgent',
    'cma': 'CmaAgent',
    'rdp': 'RdpAgent',
    'seq2seq': 'Seq2SeqAgent',
    'internvla_n1': 'InternVLAN1Agent',
}
__all__ = list(_EXPORTS)


def __getattr__(name):
    if name not in _EXPORTS:
        raise AttributeError(f'module {__name__!r} has no attribute {name!r}')
    module, symbol = _EXPORTS[name]
    value = getattr(import_module(f'{__name__}.{module}'), symbol)
    globals()[name] = value
    return value


def _load_registered_agent(name):
    if name in _AGENT_EXPORTS:
        __getattr__(_AGENT_EXPORTS[name])
