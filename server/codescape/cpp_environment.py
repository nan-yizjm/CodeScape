"""Snapshot-only compiler metadata and separately verified application-owned declarations."""
from __future__ import annotations

import hashlib
import json
import ntpath
import re
import shlex
import sys
import tempfile
from contextlib import contextmanager
from functools import lru_cache
from pathlib import Path

from .runtime import resource_root


@lru_cache(maxsize=4)
def _verify_headers(directory: str, manifest_time: int):
    root = Path(directory)
    document = json.loads((root / "headers-manifest.json").read_text(encoding="utf-8"))
    if document["compiler_major"] != 18:
        raise ValueError("系统声明与解析器版本不匹配。")
    for relative, expected in document["files"].items():
        path = root / relative
        if not path.resolve().is_relative_to(root.resolve()) or not path.is_file():
            raise ValueError("系统声明路径缺失或越界。")
        with path.open("rb") as value:
            if hashlib.file_digest(value, "sha256").hexdigest() != expected:
                raise ValueError("系统声明校验失败。")
    return tuple(root / relative for relative in document["include_roots"]), frozenset(document["files"])


def trusted_headers():
    root = resource_root() / "runtime/clang"
    if not getattr(sys, "frozen", False):
        root = resource_root() / ".runtime/packaging/resources/runtime/clang"
    manifest = root / "headers-manifest.json"
    if not manifest.is_file():
        return (), frozenset(), "可信系统声明未安装；相关文件仅保留可验证的解析结果。"
    try:
        directories, files = _verify_headers(str(root), manifest.stat().st_mtime_ns)
        return directories, files, None
    except (OSError, ValueError, KeyError, TypeError):
        return (), frozenset(), "可信系统声明校验未通过，禁止读取；请修复 CodeScape 安装。"


@contextmanager
def snapshot_overlay(root: Path, files, header_directories, header_files):
    tree = {}
    paths = [(root / relative, root / relative) for relative in files]
    if header_directories:
        sdk = header_directories[0].parents[2]
        paths.extend((sdk / relative, sdk / relative) for relative in sorted(header_files))
    for virtual, physical in paths:
        current = tree
        parts = virtual.absolute().parts
        for part in parts[:-1]:
            current = current.setdefault(part, {})
        current[parts[-1]] = str(physical.absolute())

    def entries(directory):
        return [{"type": "directory", "name": name, "contents": entries(value)} if isinstance(value, dict)
            else {"type": "file", "name": name, "external-contents": value}
            for name, value in directory.items()]

    with tempfile.TemporaryDirectory(prefix="codescape-parser-vfs-") as temporary:
        overlay = Path(temporary) / "snapshot-only.json"
        overlay.write_text(json.dumps({"version": 0, "case-sensitive": False, "fallthrough": False,
            "use-external-names": False, "roots": entries(tree)}), encoding="utf-8")
        yield overlay


def remap_path(value: str, root: Path, source_root: str | None, directory: str = "") -> Path | None:
    if not isinstance(value, str) or not value or any(item in value for item in ("\x00", "\r", "\n")):
        return None
    # Pure string normalization is intentional: never resolve or read the original repository.
    base = source_root or str(root)
    origin = ntpath.normpath(base)
    configured = ntpath.normpath(ntpath.join(origin, directory))
    full = ntpath.normpath(ntpath.join(configured, value))
    for permitted in (str(root), origin):
        permitted = ntpath.normpath(permitted)
        if ntpath.normcase(full) == ntpath.normcase(permitted):
            return root
        if ntpath.normcase(full).startswith(ntpath.normcase(permitted.rstrip("\\/") + "\\")):
            relative = ntpath.relpath(full, permitted).replace("\\", "/")
            result = root / relative
            if result.resolve().is_relative_to(root.resolve()):
                return result
    return None


def safe_arguments(arguments, root: Path, source_root: str | None, directory: str):
    allowed, includes, removed = [], [root], 0
    index = 1  # Compiler name is data, never a process to start.
    while index < len(arguments):
        option = arguments[index]
        index += 1
        if not isinstance(option, str):
            removed += 1
            continue
        if option in {"-I", "-isystem", "-iquote", "-D", "-U", "-std"}:
            value = arguments[index] if index < len(arguments) else ""
            index += 1
            kind = option
        else:
            match = re.fullmatch(r"(-I|-D|-U)(.+)|(-std)=(.+)", option)
            if not match:
                removed += 1
                continue
            kind, value = (match[1], match[2]) if match[1] else (match[3], match[4])
        if not isinstance(value, str) or len(value) > 1024 or any(item in value for item in ("\x00", "\r", "\n")):
            removed += 1
            continue
        if len(value) >= 2 and value[0] == value[-1] == '"':
            value = value[1:-1]
        if kind in {"-I", "-isystem", "-iquote"}:
            mapped = remap_path(value, root, source_root, directory)
            if mapped is None:
                removed += 1
                continue
            includes.append(mapped)
            allowed.extend([kind, str(mapped)])
        elif kind in {"-D", "-U"} and re.fullmatch(r"[A-Za-z_][A-Za-z_0-9]*(?:=.*)?", value):
            allowed.append(kind + value)
        elif kind == "-std" and re.fullmatch(r"(?:gnu|c)(?:89|90|99|11|17|18|2x|23)|(?:gnu\+\+|c\+\+)(?:98|03|11|14|17|20|23|2b)", value):
            allowed.append("-std=" + value)
        else:
            removed += 1
    return allowed, includes, removed


class CompilerConfigurations(dict):
    def __init__(self):
        super().__init__()
        self.incomplete: set[str] = set()


def compiler_configurations(root: Path, files: dict[str, str], source_root: str | None = None):
    configurations, notes = CompilerConfigurations(), []
    for path, text in files.items():
        if Path(path).name != "compile_commands.json":
            continue
        try:
            if len(text) > 2 * 1024**2:
                raise ValueError("编译数据库过大")
            records = json.loads(text)
            if not isinstance(records, list):
                raise ValueError("编译数据库不是数组")
            for record in records:
                if not isinstance(record, dict):
                    continue
                directory = record.get("directory", str(Path(path).parent))
                if not isinstance(directory, str):
                    continue
                target = remap_path(record.get("file"), root, source_root, directory)
                if target is None or target.relative_to(root).as_posix() not in files:
                    notes.append((path, "编译配置的文件不在当前快照内，已忽略。"))
                    continue
                arguments = record.get("arguments")
                if arguments is None and isinstance(record.get("command"), str):
                    arguments = [item[1:-1] if len(item) >= 2 and item[0] == item[-1] == '"' else item
                        for item in shlex.split(record["command"], posix=False)]
                if not isinstance(arguments, list) or len(arguments) > 4096:
                    notes.append((path, "编译配置参数无效，已忽略。"))
                    continue
                value = safe_arguments(arguments, root, source_root, directory)
                key = target.relative_to(root).as_posix()
                bucket = configurations.setdefault(key, [])
                if value not in bucket and len(bucket) < 8:
                    bucket.append(value)
                elif value not in bucket:
                    if key not in configurations.incomplete:
                        notes.append((key, "编译配置超过八个，未覆盖的配置保留为缺口；相关调用不能认定全部配置一致。"))
                    configurations.incomplete.add(key)
                if value[2]:
                    notes.append((key, f"已移除 {value[2]} 项非白名单参数；没有执行编译命令。"))
        except (ValueError, TypeError, KeyError):
            notes.append((path, "无法读取编译数据库，使用保守默认配置；没有执行编译命令。"))
    return configurations, notes


def preprocessing_lines(text: str):
    # Match the preprocessing phases that can hide an include behind comments or continuations.
    text = text.replace("??/", "\\").replace("\\\r\n", "").replace("\\\n", "")
    token = re.compile(r'/\*.*?\*/|//[^\r\n]*|"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'', re.S)
    text = token.sub(lambda match: re.sub(r"[^\r\n]", " ", match[0]) if match[0].startswith(("/*", "//")) else match[0], text)
    return text.splitlines()


def verify_includes(root: Path, files, path: str, include_directories, header_directories, header_files):
    dependencies, problems = [], []
    sdk = header_directories[0].parents[2] if header_directories else None
    for line in preprocessing_lines(files[path]):
        directive = re.match(r"\s*(?:#|%:)\s*(include(?:_next)?|import|embed)\b(.*)", line)
        if not directive:
            continue
        literal = re.fullmatch(r'\s*(["<])([^">]+)[">]\s*', directive[2])
        if not literal or directive[1] != "include":
            problems.append("包含指令使用动态名称或不受支持形式，未进行该单元的原生解析。")
            continue
        name = literal[2]
        if ntpath.isabs(name) or ":" in name:
            problems.append("包含路径越界，未读取外部声明。")
            continue
        candidates = ([root / Path(path).parent] if literal[1] == '"' else []) + list(include_directories)
        found = False
        for directory in candidates:
            candidate = (directory / name).resolve()
            if candidate.is_relative_to(root.resolve()):
                relative = candidate.relative_to(root.resolve()).as_posix()
                if relative in files:
                    dependencies.append(relative)
                    found = True
                    break
        if not found and sdk:
            for directory in header_directories:
                candidate = (directory / name).resolve()
                if candidate.is_relative_to(sdk.resolve()) and candidate.relative_to(sdk.resolve()).as_posix() in header_files:
                    found = True
                    break
        if not found:
            if ".." in name.replace("\\", "/").split("/"):
                problems.append("包含路径越界，未读取外部声明。")
            else:
                problems.append(f"包含文件 {name} 不在快照或可信系统声明中。")
    return dependencies, problems
