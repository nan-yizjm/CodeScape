"""独立 Windows 程序验证；只读应用自有样例，不上传源码、不执行样例。"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import subprocess
import time
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx

repository = Path(__file__).resolve().parents[1]


def check_declarations(client, root):
    source = root / 'owned-declarations'
    source.mkdir()
    files = {
        'owned.py': 'from typing import overload, Protocol\nfrom abc import abstractmethod\n'
                    '@overload\ndef parse(value: int): ...\n'
                    '@overload\ndef parse(value: str): pass\n'
                    'def parse(value): return value\n'
                    'class Port(Protocol):\n def declaration(self): ...\n def default(self): return 1\n'
                    'class Base:\n @abstractmethod\n def declaration(self): pass\n'
                    ' @abstractmethod\n def default(self): return 2\n'
                    'def noop(): pass\n',
        'macros.hpp': '#define API __declspec(dllexport)\n#define BEGIN namespace owned {\n#define END }\n'
                      '#if NO_EXCEPTIONS\n#define TRY\n#define CATCH(x)\n#else\n'
                      '#define TRY try\n#define CATCH(x) catch (...) { report(x); }\n#endif\n',
        'owned.cpp': '#include "macros.hpp"\nBEGIN\nclass API Reader {\npublic:\n'
                     ' Reader() {}\n int value() const { return 1; }\n void send() {\n'
                     '  TRY { target(); }\n  CATCH(location())\n'
                     '  if (should_flush()) { flush(); }\n }\n};\nEND\n',
    }
    for name, text in files.items():
        (source/name).write_text(text, encoding='utf-8', newline='')
    response = client.post('/api/v1/imports', json={'path':str(source)})
    assert response.status_code in {200,202}
    url = '/api/v1/projects/' + response.json()['data']['id']
    for _ in range(600):
        project = client.get(url).json()['data']
        if project['status'] in {'indexed','paused','failed'}:
            break
        time.sleep(0.1)
    assert project['status']=='indexed',project['error']
    entities = project['analysis']['entities']
    python = [entity for entity in entities if entity['language']=='python']
    assert len(python)==8 and sum(entity['visible'] for entity in python)==4
    assert sorted(entity['hidden_reason'] for entity in python if not entity['visible']) == [
        '抽象方法声明','接口方法声明','类型重载声明','类型重载声明']
    assert any(entity['name']=='noop' and entity['visible'] for entity in python)
    cpp = [entity for entity in entities if entity['language']=='cpp']
    assert sorted(entity['name'].split('::')[-1] for entity in cpp if entity['visible'])==['send','value']
    constructor = next(entity for entity in cpp if entity['name'].split('::')[-1]=='Reader')
    assert not constructor['visible']
    send = next(entity for entity in cpp if entity['name'].split('::')[-1]=='send')
    assert (send['location']['start_line'],send['location']['end_line'])==(7,11)
    calls = [call for call in project['analysis']['calls'] if call['caller_scope_id']==send['scope_id']]
    assert any(call['expression']=='flush' and call['location']['start_line']==10 for call in calls)
    assert any(call['expression']=='location' and call['location']['start_line']==9 for call in calls)
    assert any(note['category']=='syntax_recovery' for note in project['analysis']['diagnostics'])
    assert project['function_count']==6 and not project['has_architecture']
    assert not project['upload_consent']['accepted']
    assert all((source/name).read_bytes()==text.encode() for name,text in files.items())
    assert client.get('/api/v1/usage').json()['data']['records']==[]
    return {'python_visible':4,'python_hidden_declarations':4,'cpp_visible_methods':2,
            'cpp_constructor_hidden':True,'whole_body_and_tail_scope':True,
            'macro_argument_scope_retained':True,'source_bytes_unchanged':True,
            'no_ai_architecture':True,'model_requests':0,'tools':project['analysis']['tools']}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--executable", type=Path,
        default=repository / ".runtime/packaging/dist/CodeScape/CodeScape.exe")
    parser.add_argument("--remote", action="store_true", help="额外验证公开 Hiredis 默认分支导入；不请求模型")
    parser.add_argument('--declarations', action='store_true', help='额外验证独立包声明过滤与宏包装完整函数范围；不请求模型')
    args = parser.parse_args()
    executable = args.executable.resolve(strict=True)
    root = repository / "output/windows-bundle-validation" / str(uuid.uuid4())
    source = root / "owned-source"
    source.mkdir(parents=True)
    files = {
        "owned.py": 'raise RuntimeError("仅用于静态读取，不得执行")\ndef target(): return 1\ndef caller(): return target()\n',
        "owned.ts": 'throw new Error("仅用于静态读取，不得执行");\nexport function tsTarget(){return 1;}\nexport function tsCaller(){return tsTarget();}\n',
        "owned.c": '#include <stddef.h>\nint cTarget(void){return 1;}\nint cCaller(void){return cTarget();}\n',
        "owned.cpp": '#include <vector>\n#define API inline\nnamespace owned { namespace kind { enum Value { one=1 }; } struct Reader { static kind::Value cppTarget(); }; API kind::Value Reader::cppTarget(){return kind::one;} int cppCaller(){std::vector<int> v{1};return Reader::cppTarget()+v[0];} }\n',
    }
    for path, text in files.items():
        (source / path).write_text(text, encoding="utf-8", newline="")
    environment = {key: value for key, value in os.environ.items() if key not in {"PYTHONPATH", "PYTHONHOME"}}
    environment.update(CODESCAPE_DATA_DIR=str(root), PATH=os.path.join(os.environ["SystemRoot"], "System32"))
    log = (root / "service.log").open("wb")
    process = subprocess.Popen([str(executable), "--no-browser"], cwd=root, env=environment,
        stdout=log, stderr=log, creationflags=subprocess.CREATE_NO_WINDOW)
    log.close()
    client = None
    try:
        for _ in range(300):
            launch_path = root / "run/launch.json"
            if launch_path.is_file():
                launch = json.loads(launch_path.read_text(encoding="utf-8"))
                client = httpx.Client(base_url=launch["url"].split("/#")[0], trust_env=False, timeout=20)
                try:
                    response = client.post("/api/v1/session/exchange", json={"nonce": launch["url"].split("#nonce=")[1]})
                    if response.status_code == 200:
                        client.headers["x-csrf-token"] = response.json()["data"]["csrf_token"]
                        break
                except httpx.NetworkError:
                    pass
                client.close()
                client = None
            if process.poll() is not None:
                raise RuntimeError(f"独立程序启动失败，查看 {root / 'service.log'}")
            time.sleep(0.1)
        else:
            raise RuntimeError("独立程序启动超时")
        assert client.get("/").status_code == 200
        assert "<script" in client.get("/").text
        samples = client.get("/api/v1/projects").json()["data"]
        assert len(samples) == 1 and samples[0]["demo"] and samples[0]["has_architecture"]
        second = subprocess.run([str(executable), "--no-browser"], cwd=root, env=environment,
            capture_output=True, timeout=20, creationflags=subprocess.CREATE_NO_WINDOW)
        assert second.returncode == 0
        assert "已复用" in second.stdout.decode("utf-8", errors="replace")
        reactivated = json.loads((root / "run/launch.json").read_text(encoding="utf-8"))
        assert reactivated["port"] == launch["port"]
        response = client.post("/api/v1/imports", json={"path": str(source)})
        assert response.status_code in {200, 202}, response.text
        project_id = response.json()["data"]["id"]
        url = f"/api/v1/projects/{project_id}"
        for _ in range(300):
            project = client.get(url).json()["data"]
            if project["status"] in {"indexed", "failed"}:
                break
            time.sleep(0.1)
        assert project["status"] == "indexed", project["error"]
        assert project["function_count"] == 8
        assert not project["has_architecture"] and not project["upload_consent"]["accepted"]
        confirmed = [call for call in project["analysis"]["calls"] if call["resolution"] == "confirmed"]
        by_language = {entity["id"]: entity["language"] for entity in project["analysis"]["entities"]}
        confirmed_languages = {by_language[call["target_id"]] for call in confirmed}
        assert confirmed_languages == {"python", "typescript", "c", "cpp"}, confirmed_languages
        cpp_target = next(entity for entity in project["analysis"]["entities"] if entity["qualified_name"] == "owned::Reader::cppTarget")
        assert "kind::Value" not in cpp_target["name"]
        assert any(call["target_id"] == cpp_target["id"] for call in confirmed)
        original = files["owned.py"]
        (source / "owned.py").write_text("已修改的原目录，不应该影响快照", encoding="utf-8")
        text = client.get(f"{url}/files/{project['file_ids']['owned.py']}?version_id={project['version_id']}").json()["data"]
        assert text["text"] == original
        assert client.get("/api/v1/usage").json()["data"]["records"] == []
        declarations = check_declarations(client,root) if args.declarations else None
        remote = None
        if args.remote:
            response = client.post("/api/v1/imports", json={"url": "https://github.com/redis/hiredis.git"}, timeout=60)
            assert response.status_code in {200, 202}, response.text
            remote_id = response.json()["data"]["id"]
            for _ in range(600):
                remote = client.get(f"/api/v1/projects/{remote_id}").json()["data"]
                if remote["status"] in {"indexed", "failed"}:
                    break
                time.sleep(0.1)
            assert remote["status"] == "indexed", remote["error"]
            assert remote["origin"]["kind"] == "git" and len(remote["origin"]["commit"]) == 40
            assert remote["file_count"] > 0 and remote["function_count"] > 0
            assert not remote["has_architecture"] and not remote["upload_consent"]["accepted"]
            assert client.get("/api/v1/usage").json()["data"]["records"] == []
        resources = executable.parent / "_internal"
        versions = {}
        for name, command in {
            "node": [str(resources / "runtime/node/node.exe"), "--version"],
            "git": [str(resources / "runtime/git/cmd/git.exe"), "--version"],
        }.items():
            checked = subprocess.run(command, capture_output=True, env=environment, timeout=10,
                creationflags=subprocess.CREATE_NO_WINDOW)
            assert checked.returncode == 0
            versions[name] = checked.stdout.decode().strip()
        # Workers must retain pipe streams after freezing. Invalid input must be a controlled protocol error.
        worker = subprocess.run([str(executable), "--ai-worker"], input=b'{}\n', capture_output=True,
            env=environment, cwd=root, timeout=20, creationflags=subprocess.CREATE_NO_WINDOW)
        assert worker.returncode == 1
        assert json.loads(worker.stdout)["event"] == "error"
        stopped = subprocess.run([str(executable), "--prepare-update", "--no-browser"], env=environment,
            cwd=root, capture_output=True, timeout=65, creationflags=subprocess.CREATE_NO_WINDOW)
        assert stopped.returncode == 0
        process.wait(timeout=5)
        assert process.returncode == 0
        result = {"date": (datetime.now(UTC) + timedelta(hours=8)).date().isoformat(), "platform": platform.platform(), "architecture": platform.machine(),
            "executable": str(executable), "executable_sha256": hashlib.sha256(executable.read_bytes()).hexdigest(),
            "data_root": str(root), "developer_path_removed": True, "duplicate_launch_reused": True,
            "static_sample_functions": 8, "confirmed_call_languages": sorted(confirmed_languages),
            "macro_qualified_return_name": "owned::Reader::cppTarget；确认调用仍定位该唯一实现",
            "original_modification_does_not_change_snapshot": True, "ai_worker_protocol": "受控错误响应",
            "runtime_versions": versions, "model_requests": 0,
            "declaration_and_macro_boundary": declarations,
            "public_git_import": ({"repository": "https://github.com/redis/hiredis.git", "origin": remote["origin"],
                "file_count": remote["file_count"], "function_count": remote["function_count"],
                "diagnostic_count": remote["diagnostic_count"], "ai_analysis": False} if remote else "本次未验证"),
            "update_saves_and_exits_normally": True,
            "boundary": "开发机 Windows 独立运行验证；不代替干净 Windows 10/11 虚拟机验收；不代表真实 AI 分析质量。"}
        report = repository / ('docs/reports/p0/Windows声明与函数范围独立包验证.json'
                               if args.declarations else 'docs/reports/p0/Windows独立运行验证.json')
        report.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(result, ensure_ascii=False, indent=2))
    finally:
        if client:
            client.close()
        if process.poll() is None:
            process.terminate()
        process.wait(timeout=15)


if __name__ == "__main__":
    main()
