"""Durable, verified application-owned analysis output; never reads the source repository."""
from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path


def receiver_identity(config: dict) -> dict:
    """缓存只绑定公开接收端与模型身份，不保存密钥或其他配置字段。"""
    if not isinstance(config, dict) or any(not isinstance(config.get(key), str) for key in ('base_url', 'model')):
        raise ValueError('分析接收端配置无效。')
    return {key: config[key] for key in ('base_url', 'model')}


def artifact_path(root: Path, version_id: str) -> Path:
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", version_id):
        raise ValueError("分析版本标识无效。")
    return root / f"version-{version_id}.json"


def prepare_output(root: Path, version_id: str, snapshot_hash: str, pipeline: str,
                   result: dict, architecture: dict, usage: dict, *, provider_config: dict | None = None,
                   source_generation_versions: list[str] | None = None) -> dict:
    path = artifact_path(root, version_id)
    value = {"version_id": version_id, "snapshot_hash": snapshot_hash, "pipeline_version": pipeline,
             "result": result, "architecture": architecture, "usage": usage}
    if provider_config is not None:
        value['provider_config'] = receiver_identity(provider_config)
    if source_generation_versions is not None:
        value['source_generation_versions'] = sorted(set(source_generation_versions))
    data = json.dumps(value, ensure_ascii=False, sort_keys=True).encode("utf-8")
    temporary = path.with_suffix(".tmp")
    with temporary.open("wb") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())
    temporary.replace(path)
    descriptor = {"sha256": hashlib.sha256(data).hexdigest(), "snapshot_hash": snapshot_hash,
                  "pipeline_version": pipeline}
    if provider_config is not None:
        descriptor['provider_config'] = receiver_identity(provider_config)
    if source_generation_versions is not None:
        descriptor['source_generation_versions'] = value['source_generation_versions']
    load_output(root, version_id, descriptor)
    return descriptor


def load_output(root: Path, version_id: str, descriptor: dict) -> dict:
    try:
        data = artifact_path(root, version_id).read_bytes()
        if hashlib.sha256(data).hexdigest() != descriptor["sha256"]:
            raise ValueError
        value = json.loads(data)
        if (value["version_id"] != version_id or value["snapshot_hash"] != descriptor["snapshot_hash"]
                or value["pipeline_version"] != descriptor["pipeline_version"]
                or value.get('provider_config') != descriptor.get('provider_config')
                or value.get('source_generation_versions') != descriptor.get('source_generation_versions')):
            raise ValueError
        return value
    except (OSError, KeyError, TypeError, ValueError):
        raise ValueError("已保存的分析产物缺失或损坏，未发布，也未重新请求模型。") from None
