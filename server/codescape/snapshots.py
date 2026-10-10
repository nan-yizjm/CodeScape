from __future__ import annotations

import json
import os
import re
import stat
import uuid
from pathlib import Path

from .analyzers import digest

SOURCE_EXTENSIONS = {".py", ".c", ".h", ".cpp", ".cc", ".cxx", ".hpp", ".hh", ".ts", ".tsx"}
EXCLUDED_DIRECTORIES = {".git", ".venv", "node_modules", "__pycache__", "dist", "build", ".cache", ".runtime"}
SECRET_EXTENSIONS = {".pem", ".key", ".p12", ".pfx"}
OPTIONAL_DIRECTORIES = {"tests", "test", "examples", "example", "vendor", "third_party"}


def matches_scope(path: str, patterns: list[str]) -> bool:
    """Root-relative glob: * stays in one segment; **/ also matches zero directories."""
    for pattern in patterns:
        pattern = pattern.strip().replace("\\", "/").removeprefix("./")
        if not pattern:
            continue
        if pattern.startswith("/") or ".." in pattern.split("/"):
            raise ValueError("范围规则必须相对项目根目录，不能包含上级路径。")
        if pattern.endswith("/"):
            pattern += "**"
        tokens, index = [], 0
        while index < len(pattern):
            if pattern[index:index + 3] == "**/":
                tokens.append("(?:.*/)?")
                index += 3
            elif pattern[index:index + 2] == "**":
                tokens.append(".*")
                index += 2
            elif pattern[index] == "*":
                tokens.append("[^/]*")
                index += 1
            elif pattern[index] == "?":
                tokens.append("[^/]")
                index += 1
            else:
                tokens.append(re.escape(pattern[index]))
                index += 1
        expression = "".join(tokens)
        if re.fullmatch(expression, path) or (not any(c in pattern for c in "*?") and path.startswith(pattern + "/")):
            return True
    return False


def is_link(path: Path) -> bool:
    info = path.lstat()
    return stat.S_ISLNK(info.st_mode) or bool(getattr(info, "st_file_attributes", 0) & 0x400)


def create_snapshot(
    source: Path, project_root: Path, excludes: list[str] | None = None, includes: list[str] | None = None
) -> dict:
    excludes, includes = excludes or [], includes or []
    # Validate rules even for empty repositories.
    for pattern in excludes + includes:
        matches_scope("", [pattern])
    source = source.resolve(strict=True)
    if not source.is_dir():
        raise ValueError("请选择源码目录。")
    snapshot_id = str(uuid.uuid4())
    target = project_root / "snapshots" / snapshot_id
    target.mkdir(parents=True, exist_ok=False)
    manifest = {"id": snapshot_id, "files": {}, "excluded": [], "source": str(source),
                "scope": {"excludes": excludes, "includes": includes, "matcher_version": 1}}
    for directory, dirs, names in os.walk(source, followlinks=False):
        parent = Path(directory)
        kept = []
        for name in sorted(dirs):
            candidate = parent / name
            relative = candidate.relative_to(source).as_posix()
            if (
                name in EXCLUDED_DIRECTORIES
                or is_link(candidate)
                or project_root.resolve().is_relative_to(candidate.resolve())
            ):
                manifest["excluded"].append({"path": relative, "reason": "默认排除目录或链接"})
            else:
                kept.append(name)
        dirs[:] = kept
        for name in sorted(names):
            file = parent / name
            relative = file.relative_to(source).as_posix()
            if is_link(file) or name.startswith(".env") or file.suffix.lower() in SECRET_EXTENSIONS:
                manifest["excluded"].append({"path": relative, "reason": "链接或敏感文件"})
                continue
            if matches_scope(relative, excludes):
                manifest["excluded"].append({"path": relative, "reason": "用户排除"})
                continue
            if file.suffix.lower() not in SOURCE_EXTENSIONS:
                continue
            if any(part in OPTIONAL_DIRECTORIES for part in file.relative_to(source).parts[:-1]) and not matches_scope(relative, includes):
                manifest["excluded"].append({"path": relative, "reason": "默认排除测试、示例或第三方源码，可明确包含"})
                continue
            before = file.stat()
            raw = file.read_bytes()
            after = file.stat()
            if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
                raise ValueError("复制时源码发生变化，请在源码稳定后重试。")
            try:
                text = raw.decode("utf-8")
            except UnicodeDecodeError:
                manifest["excluded"].append({"path": relative, "reason": "原型暂仅支持 UTF-8 源码"})
                continue
            if text.startswith("\ufeff"):
                manifest["excluded"].append({"path": relative, "reason": "原型暂不支持带 BOM 的字节定位"})
                continue
            destination = target / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(raw)
            manifest["files"][relative] = {"hash": digest(raw), "size": len(raw), "text": text}
    manifest["hash"] = digest(
        json.dumps({key: value["hash"] for key, value in manifest["files"].items()}, sort_keys=True)
    )
    manifest["scope_hash"] = digest(json.dumps(manifest["scope"], sort_keys=True))
    for relative, information in manifest["files"].items():
        original = source / relative
        if is_link(original) or digest(original.read_bytes()) != information["hash"]:
            raise ValueError("创建快照期间源码发生变化，请在源码稳定后重试。")
    (target / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return manifest


def read_snapshot(root: Path, manifest: dict) -> dict[str, str]:
    files = {}
    for relative, information in manifest["files"].items():
        resolved = (root / relative).resolve()
        if not resolved.is_relative_to(root.resolve()):
            raise ValueError("快照路径越界。")
        raw = resolved.read_bytes()
        if digest(raw) != information["hash"]:
            raise ValueError("快照文件校验失败。")
        files[relative] = raw.decode("utf-8")
    return files
