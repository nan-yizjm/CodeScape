"""Durable, verified application-owned analysis output; never reads the source repository."""
from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path


def artifact_path(root: Path, version_id: str) -> Path:
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", version_id):
        raise ValueError("分析版本标识无效。")
    return root / f"version-{version_id}.json"


def prepare_output(root: Path, version_id: str, snapshot_hash: str, pipeline: str,
                   result: dict, architecture: dict, usage: dict) -> dict:
    path = artifact_path(root, version_id)
    value = {"version_id": version_id, "snapshot_hash": snapshot_hash, "pipeline_version": pipeline,
             "result": result, "architecture": architecture, "usage": usage}
    data = json.dumps(value, ensure_ascii=False, sort_keys=True).encode("utf-8")
    temporary = path.with_suffix(".tmp")
    with temporary.open("wb") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())
    temporary.replace(path)
    descriptor = {"sha256": hashlib.sha256(data).hexdigest(), "snapshot_hash": snapshot_hash,
                  "pipeline_version": pipeline}
    load_output(root, version_id, descriptor)
    return descriptor


def load_output(root: Path, version_id: str, descriptor: dict) -> dict:
    try:
        data = artifact_path(root, version_id).read_bytes()
        if hashlib.sha256(data).hexdigest() != descriptor["sha256"]:
            raise ValueError
        value = json.loads(data)
        if (value["version_id"] != version_id or value["snapshot_hash"] != descriptor["snapshot_hash"]
                or value["pipeline_version"] != descriptor["pipeline_version"]):
            raise ValueError
        return value
    except (OSError, KeyError, TypeError, ValueError):
        raise ValueError("已保存的分析产物缺失或损坏，未发布，也未重新请求模型。") from None
