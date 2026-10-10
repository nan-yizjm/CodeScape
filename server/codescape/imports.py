from __future__ import annotations

import ipaddress
import json
import os
import re
import shutil
import socket
import subprocess
import time
import uuid
from pathlib import Path, PurePosixPath
from urllib.parse import urlsplit

from .models import ImportRequest
from .snapshots import SOURCE_EXTENSIONS, create_snapshot, read_snapshot

PUBLIC_GIT_HOSTS = {"github.com", "gitlab.com", "bitbucket.org", "codeberg.org"}
PROXY_DNS_NETWORKS = (ipaddress.ip_network("198.18.0.0/15"), ipaddress.ip_network("2001:2::/48"))
WINDOWS_RESERVED_NAMES = {"con", "prn", "aux", "nul", *(f"com{i}" for i in range(1, 10)),
                          *(f"lpt{i}" for i in range(1, 10))}


def remove_staging(path: Path, boundary: Path) -> None:
    if not path.resolve().is_relative_to(boundary.resolve()) or path.resolve() == boundary.resolve():
        raise ValueError("临时目录清理范围错误。")

    def writable_retry(function, target, error):
        if not isinstance(error, PermissionError):
            raise error
        os.chmod(target, 0o700)
        function(target)

    shutil.rmtree(path, onexc=writable_retry)


def validate_git_url(url: str) -> str:
    parsed = urlsplit(url)
    if (parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username
            or parsed.password or parsed.query or parsed.fragment or parsed.port not in {None, 80, 443}
            or not parsed.path.strip("/") or any(c.isspace() or ord(c) < 32 for c in url)):
        raise ValueError("请输入不含账号、密码或查询参数的公开 HTTP/HTTPS Git 仓库地址。")
    try:
        addresses = socket.getaddrinfo(parsed.hostname, parsed.port or 443, type=socket.SOCK_STREAM)
    except OSError:
        raise ValueError("无法解析 Git 仓库地址，请检查网络和仓库 URL。") from None
    def public_route(address: str) -> bool:
        ip = ipaddress.ip_address(address)
        # Some VPNs synthesize benchmark-range DNS. Only known HTTPS Git hosts
        # may use that route; TLS hostname verification remains mandatory.
        return ip.is_global or (parsed.scheme == "https" and parsed.hostname in PUBLIC_GIT_HOSTS
                                and any(ip in network for network in PROXY_DNS_NETWORKS if ip.version == network.version))

    if not addresses or any(not public_route(item[4][0]) for item in addresses):
        raise ValueError("远程 Git 导入仅允许公开网络地址，本地仓库请使用目录导入。")
    return url.rstrip("/")


def git_environment() -> dict[str, str]:
    environment = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    environment.update({"GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull,
                        "GIT_TERMINAL_PROMPT": "0", "GIT_ASKPASS": "", "GIT_PROTOCOL_FROM_USER": "0",
                        "GIT_ALLOW_PROTOCOL": "http:https", "GCM_INTERACTIVE": "never"})
    return environment


def git_command(*arguments: str) -> list[str]:
    bundled = Path(__file__).resolve().parents[2] / "runtime/git/cmd/git.exe"
    executable = str(bundled) if bundled.is_file() else shutil.which("git")
    if not executable:
        raise ValueError("Git 运行组件不可用，请修复 CodeScape 安装。")
    return [executable, "-c", "credential.helper=", "-c", "core.hooksPath=",
            "-c", "http.followRedirects=false", "-c", "http.sslVerify=true", "-c", "http.proxy=", "-c", "http.cookieFile=",
            "-c", "protocol.file.allow=never", "-c", "protocol.ext.allow=never", *arguments]


def run_git(*arguments: str, timeout: int = 120) -> bytes:
    try:
        result = subprocess.run(git_command(*arguments), env=git_environment(), capture_output=True,
                                timeout=timeout, check=False)
    except subprocess.TimeoutExpired:
        raise ValueError("Git 获取超时，请检查网络后重试。") from None
    if result.returncode:
        # Git stderr may include authentication/proxy details; never echo it to the UI/log.
        raise ValueError("无法读取公开 Git 仓库。请核对地址、网络和公开权限；私有仓库可先克隆到本地。")
    return result.stdout


def export_git(url: str, staging: Path) -> tuple[Path, dict]:
    """Fetch bare objects and copy blobs. No checkout, hooks, smudge filters, LFS or submodule execution."""
    url = validate_git_url(url)
    refs = run_git("ls-remote", "--symref", url, "HEAD").decode("utf-8")
    branch_match = re.search(r"^ref: refs/heads/(.+)\tHEAD$", refs, re.MULTILINE)
    if not branch_match:
        raise ValueError("仓库没有可读取的默认分支。")
    branch = branch_match[1]
    repository, source = staging / "objects.git", staging / "source"
    run_git("clone", "--bare", "--template=", "--depth=1", "--single-branch", "--branch", branch, "--", url, str(repository))
    commit = run_git("--git-dir", str(repository), "rev-parse", "HEAD").decode().strip()
    entries = run_git("--git-dir", str(repository), "ls-tree", "-r", "-z", "HEAD").split(b"\0")
    excluded = []
    source.mkdir()
    total = 0
    process = subprocess.Popen(git_command("--git-dir", str(repository), "cat-file", "--batch"),
                               env=git_environment(), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                               stderr=subprocess.DEVNULL)
    try:
        assert process.stdin and process.stdout
        for entry in entries:
            if not entry:
                continue
            meta, raw_path = entry.split(b"\t", 1)
            mode, kind, object_id = meta.split(b" ")
            try:
                relative = raw_path.decode("utf-8")
            except UnicodeDecodeError:
                excluded.append({"path": "无法解码的 Git 路径", "reason": "文件名不是 UTF-8"})
                continue
            parts = PurePosixPath(relative).parts
            if (not parts or PurePosixPath(relative).is_absolute() or ".." in parts
                    or any(":" in part or "\\" in part or part.rstrip(". ") != part
                           or part.split('.')[0].casefold() in WINDOWS_RESERVED_NAMES
                           or any(ord(character) < 32 for character in part) for part in parts)):
                raise ValueError("仓库包含不安全或不兼容 Windows 的文件路径。")
            if mode not in {b"100644", b"100755"} or kind != b"blob":
                excluded.append({"path": relative, "reason": "未读取符号链接或 Git 子模块"})
                continue
            if PurePosixPath(relative).suffix.lower() not in SOURCE_EXTENSIONS:
                continue
            destination = source.joinpath(*parts)
            if not destination.resolve().is_relative_to(source.resolve()):
                raise ValueError("Git 文件路径越界。")
            process.stdin.write(object_id + b"\n")
            process.stdin.flush()
            header = process.stdout.readline().split()
            if len(header) != 3 or header[1] != b"blob":
                raise ValueError("Git 对象读取失败。")
            size = int(header[2])
            total += size
            if size > 16 * 1024 * 1024 or total > 512 * 1024 * 1024:
                raise ValueError("仓库源码范围过大，请先克隆到本地并缩小扫描范围。")
            raw = process.stdout.read(size)
            if len(raw) != size or process.stdout.read(1) != b"\n":
                raise ValueError("Git 对象内容不完整。")
            if raw.startswith(b"version https://git-lfs.github.com/spec/v1\n"):
                excluded.append({"path": relative, "reason": "Git LFS 指针，未下载对象"})
                continue
            destination.parent.mkdir(parents=True, exist_ok=True)
            if destination.exists():
                raise ValueError("仓库存在 Windows 文件名冲突，不能可靠创建快照。")
            destination.write_bytes(raw)
        process.stdin.close()
        if process.wait(timeout=10):
            raise ValueError("Git 对象读取失败。")
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()
    return source, {"kind": "git", "url": url, "default_branch": branch, "commit": commit,
                    "excluded": excluded}


class ImportService:
    def __init__(self, root: Path):
        self.root = root.resolve()
        self.staging = self.root / "imports"

    def cleanup(self) -> None:
        if not self.staging.is_dir():
            return
        for directory in self.staging.iterdir():
            if not directory.is_dir() or directory.is_symlink():
                continue
            try:
                if self._directory(directory.name) != directory:
                    continue
            except ValueError:
                continue
            try:
                value = json.loads((directory / "preview.json").read_text(encoding="utf-8"))
                expired = time.time() - value["created_at"] > 1800
            except (ValueError, OSError, KeyError, TypeError):
                # An interrupted scan without a completed manifest is retained for
                # one day so concurrent scans cannot be collected prematurely.
                expired = time.time() - directory.stat().st_mtime > 86400
            if expired:
                remove_staging(directory, self.staging)

    def _directory(self, preview_id: str) -> Path:
        try:
            canonical = str(uuid.UUID(preview_id))
        except (ValueError, TypeError):
            raise ValueError("扫描预览不存在，请重新扫描。") from None
        if canonical != preview_id:
            raise ValueError("扫描预览不存在，请重新扫描。")
        return self.staging / canonical

    def preview(self, body: ImportRequest) -> dict:
        if bool(body.path) == bool(body.url):
            raise ValueError("请选择本地目录或公开 Git URL 中的一种来源。")
        preview_id = str(uuid.uuid4())
        directory = self._directory(preview_id)
        directory.mkdir(parents=True)
        try:
            if body.url:
                source, origin = export_git(body.url, directory)
                display_source = origin["url"]
                name = urlsplit(display_source).path.rstrip("/").split("/")[-1].removesuffix(".git")
            else:
                source = Path(body.path or "")
                if not source.is_absolute() or not source.is_dir():
                    raise ValueError("请输入存在的本地源码目录绝对路径。")
                origin = {"kind": "local", "path": str(source)}
                display_source, name = str(source), source.name
            manifest = create_snapshot(source, directory, body.excludes, body.includes)
            manifest["source"] = display_source
            manifest["origin"] = origin
            manifest["excluded"].extend(origin.get("excluded", []))
            if not manifest["files"]:
                raise ValueError("当前范围没有支持的源码文件，请调整包含或排除规则。")
            value = {"preview_id": preview_id, "name": name, "source": display_source,
                     "created_at": time.time(), "manifest": manifest}
            (directory / "preview.json").write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
            snapshot = directory / "snapshots" / manifest["id"]
            (snapshot / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
            for disposable in (directory / "objects.git", directory / "source"):
                if disposable.exists():
                    remove_staging(disposable, directory)
            return {"preview_id": preview_id, "name": name, "source": display_source,
                    "file_count": len(manifest["files"]),
                    "size": sum(item["size"] for item in manifest["files"].values()),
                    "files": list(manifest["files"]), "excluded": manifest["excluded"], "origin": origin}
        except Exception:
            remove_staging(directory, self.staging)
            raise

    def consume(self, preview_id: str, project_id: str) -> dict:
        directory = self._directory(preview_id)
        try:
            value = json.loads((directory / "preview.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            raise ValueError("扫描预览不存在，请重新扫描。") from None
        if time.time() - value["created_at"] > 1800:
            raise ValueError("扫描预览已过期，请重新扫描。")
        read_snapshot(directory / "snapshots" / value["manifest"]["id"], value["manifest"])
        target = self.root / "projects" / str(uuid.UUID(project_id))
        target.parent.mkdir(parents=True, exist_ok=True)
        directory.replace(target)
        (target / "preview.json").unlink()
        return value
