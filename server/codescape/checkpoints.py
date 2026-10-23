"""应用写入的批次范围随检查点传递，不进入模型输出或公开架构契约。"""
from __future__ import annotations

from .models import Architecture


def dump_checkpoint(architecture: Architecture) -> dict:
    ranges = architecture._source_ranges
    if ranges is None:
        return architecture.model_dump()
    return {'checkpoint_format': 1, 'architecture': architecture.model_dump(),
            'source_ranges': {path: list(bounds) for path, bounds in ranges.items()}}


def load_checkpoint(value: dict) -> Architecture:
    if not isinstance(value, dict):
        raise ValueError('源码检查点格式无效。')
    if 'checkpoint_format' not in value:
        return Architecture.model_validate(value)
    if (type(value['checkpoint_format']) is not int or value['checkpoint_format'] != 1
            or set(value) != {'checkpoint_format', 'architecture', 'source_ranges'}):
        raise ValueError('源码检查点格式版本无效。')
    ranges = value['source_ranges']
    if not isinstance(ranges, dict) or not ranges:
        raise ValueError('源码检查点缺少批次范围。')
    parsed = {}
    for path, bounds in ranges.items():
        if (not isinstance(path, str) or not path or not isinstance(bounds, (list, tuple)) or len(bounds) != 2
                or any(type(n) is not int for n in bounds) or not 1 <= bounds[0] <= bounds[1]):
            raise ValueError('源码检查点行范围无效。')
        parsed[path] = tuple(bounds)
    architecture = Architecture.model_validate(value['architecture'])
    architecture._source_ranges = parsed
    return architecture
