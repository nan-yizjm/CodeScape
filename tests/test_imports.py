import json
import subprocess
import time
from pathlib import Path

import pytest
from codescape import imports as importing
from codescape.imports import ImportService, export_git, git_command, git_environment, validate_git_url
from codescape.models import ImportRequest
from codescape.snapshots import create_snapshot, matches_scope, read_snapshot


@pytest.mark.parametrize(("path", "pattern", "expected"), [
    ("a.py", "**/*.py", True), ("one/two/a.py", "**/*.py", True),
    ("one/two/a.py", "*/*.py", False), ("tests/a.py", "tests/", True),
    ("a/tests/x.py", "tests/**", False), ("tests/x.py", "**/tests/**", True),
    ("one/tests/two/x.py", "**/tests/**", True), ("abc.py", "a?c.py", True),
])
def test_scope_glob_has_path_segment_semantics(path, pattern, expected):
    assert matches_scope(path, [pattern]) is expected


def test_scope_include_overrides_optional_default_but_not_exclusion_or_secret(tmp_path):
    source = tmp_path / "source"
    for relative in ["main.py", "tests/a.py", "tests/nested/b.py", "vendor/lib.py", ".env.py"]:
        path = source / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("raise RuntimeError('不得执行')", encoding="utf-8")
    plain = create_snapshot(source, tmp_path / "plain")
    assert set(plain["files"]) == {"main.py"}
    expanded = create_snapshot(source, tmp_path / "expanded", ["**/nested/**"], ["tests/**", ".env.py"])
    assert set(expanded["files"]) == {"main.py", "tests/a.py"}
    assert expanded["scope_hash"] != plain["scope_hash"]
    with pytest.raises(ValueError, match="相对"):
        create_snapshot(source, tmp_path / "bad", ["**", "../other/**"])


def test_preview_is_immutable_one_use_and_root_confined(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    (source / "main.py").write_text("def old(): pass", encoding="utf-8")
    service = ImportService(tmp_path / "app")
    preview = service.preview(ImportRequest(path=str(source)))
    (source / "main.py").write_text("def changed(): pass", encoding="utf-8")
    project_id = "12345678-1234-1234-1234-123456789012"
    value = service.consume(preview["preview_id"], project_id)
    root = tmp_path / "app/projects" / project_id / "snapshots" / value["manifest"]["id"]
    assert read_snapshot(root, value["manifest"])["main.py"] == "def old(): pass"
    with pytest.raises(ValueError, match="重新扫描"):
        service.consume(preview["preview_id"], project_id)
    with pytest.raises(ValueError, match="不存在"):
        service.consume("../projects", project_id)


def test_preview_expiration_and_hash_integrity(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    (source / "x.py").write_text("pass", encoding="utf-8")
    service = ImportService(tmp_path / "app")
    preview = service.preview(ImportRequest(path=str(source)))
    metadata = service.staging / preview["preview_id"] / "preview.json"
    value = json.loads(metadata.read_text(encoding="utf-8"))
    value["created_at"] = time.time() - 1801
    metadata.write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises(ValueError, match="过期"):
        service.consume(preview["preview_id"], "12345678-1234-1234-1234-123456789012")
    service.cleanup()
    assert not metadata.parent.exists()
    assert source.exists()


def test_cleanup_retains_current_preview_and_unowned_directory(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    (source / "x.py").write_text("pass", encoding="utf-8")
    service = ImportService(tmp_path / "app")
    preview = service.preview(ImportRequest(path=str(source)))
    unrelated = service.staging / "unowned"
    unrelated.mkdir()
    service.cleanup()
    assert unrelated.is_dir()
    assert (service.staging / preview["preview_id"] / "preview.json").exists()


@pytest.mark.parametrize("url", ["file:///C:/repo", "ssh://host/repo", "https://user:password@host/repo",
                                  "https://host/repo?token=x", "https://host/repo#ref", "https://host:8125/repo"])
def test_remote_url_rejects_credentials_and_unapproved_protocols(url):
    with pytest.raises(ValueError):
        validate_git_url(url)


def test_remote_url_rejects_private_network(monkeypatch):
    monkeypatch.setattr(importing.socket, "getaddrinfo", lambda *a, **k: [(2, 1, 6, "", ("127.0.0.1", 443))])
    with pytest.raises(ValueError, match="公开网络"):
        validate_git_url("https://internal.invalid/repo")


def test_proxy_dns_is_limited_to_known_https_hosts(monkeypatch):
    monkeypatch.setattr(importing.socket, "getaddrinfo", lambda *a, **k: [(2, 1, 6, "", ("198.18.1.50", 443))])
    assert validate_git_url("https://github.com/redis/hiredis").startswith("https://github.com/")
    for url in ("http://github.com/redis/hiredis", "https://private.invalid/repo"):
        with pytest.raises(ValueError, match="公开网络"):
            validate_git_url(url)
    command = git_command("version")
    assert "http.sslVerify=true" in command and "http.followRedirects=false" in command


def test_bare_object_export_does_not_checkout_follow_links_or_fetch_lfs(tmp_path, monkeypatch):
    repository = tmp_path / "fixture.git"
    subprocess.run(git_command("init", "--bare", "--quiet", str(repository)), check=True,
                   capture_output=True, env=git_environment())
    data = b"raise RuntimeError('must never execute')\n"
    lfs = b"version https://git-lfs.github.com/spec/v1\noid sha256:example\nsize 100\n"
    stream = b"commit refs/heads/main\ncommitter Test <test@example.invalid> 1 +0000\ndata 4\nTEST\n"
    for mode, name, raw in [("100644", "main.py", data), ("120000", "link.py", b"../../private.py"),
                            ("100644", "lfs.ts", lfs)]:
        stream += f"M {mode} inline {name}\ndata {len(raw)}\n".encode() + raw + b"\n"
    stream += b"\ndone\n"
    subprocess.run(git_command("--git-dir", str(repository), "fast-import", "--quiet"),
                   input=stream, check=True, capture_output=True, env=git_environment())
    real = importing.run_git
    real("--git-dir", str(repository), "symbolic-ref", "HEAD", "refs/heads/main")
    commit = real("--git-dir", str(repository), "rev-parse", "HEAD").decode().strip()

    def network_stub(*arguments, **kwargs):
        if arguments[0] == "ls-remote":
            return f"ref: refs/heads/main\tHEAD\n{commit}\tHEAD\n".encode()
        if arguments[0] == "clone":
            importing.shutil.copytree(repository, Path(arguments[-1]))
            return b""
        return real(*arguments, **kwargs)

    monkeypatch.setattr(importing, "run_git", network_stub)
    monkeypatch.setattr(importing, "validate_git_url", lambda url: url)
    staging = tmp_path / "staging"
    staging.mkdir()
    source, origin = export_git("https://public.invalid/repo", staging)
    assert (source / "main.py").read_bytes() == data
    assert not (source / "link.py").exists() and not (source / "lfs.ts").exists()
    assert origin["commit"] == commit and origin["default_branch"] == "main"
    assert {item["path"] for item in origin["excluded"]} == {"link.py", "lfs.ts"}
    assert not (source / ".git").exists()
