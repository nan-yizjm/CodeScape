"""独立包复核真实批次恢复；隔离根没有凭据，禁止产生模型请求，不执行目标代码。"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import subprocess
import uuid
from datetime import UTC, datetime
from pathlib import Path

repository = Path(__file__).resolve().parents[1]
reports = repository / 'docs/reports/p0'


def main():
    build = json.loads((repository / '.runtime/packaging/latest-build.json').read_text(encoding='utf-8'))
    executable = repository / '.runtime/packaging/dist/CodeScape/CodeScape.exe'
    expected = json.loads((reports / 'Windows声明与函数范围独立包验证.json').read_text(encoding='utf-8'))
    assert hashlib.sha256(executable.read_bytes()).hexdigest() == expected['executable_sha256']
    root = repository / 'output/frozen-public-prefix-validation' / str(uuid.uuid4())
    root.mkdir(parents=True)
    record = {'核对时间UTC': datetime.now(UTC).isoformat(), '构建号': build['build_id'],
              '程序sha256': expected['executable_sha256'], '模型请求': 0, '样本': {},
              '边界': '独立程序只复核已保存批次，到新请求前因没有凭据受控停止；不计真实新模型输出或语义验收。'}
    for sample in ('hiredis-c', 'spdlog-cpp', 'flask-python', 'zod-typescript'):
        receipt = json.loads((reports / f'{sample}-full真实模型验证.json').read_text(encoding='utf-8'))
        original = Path(receipt['数据目录'])
        checkpoint = original / 'projects' / receipt['project_id'] / 'architecture-checkpoint.json'
        raw = checkpoint.read_bytes()
        saved = json.loads(raw)
        with sqlite3.connect((original / 'app.db').as_uri() + '?mode=ro', uri=True) as connection:
            analysis = json.loads(connection.execute('SELECT result FROM prototype_projects WHERE id=?',
                                                     (receipt['project_id'],)).fetchone()[0])
        target = root / sample
        target.mkdir()
        assert not (target / 'credentials').exists()
        payload = {'root': str(target), 'project_id': receipt['project_id'], 'version_id': receipt['version_id'],
                   'job_id': str(uuid.uuid4()), 'request_config': {}, 'analysis': analysis,
                   'files': {name: (original / 'owned-source' / name).read_text(encoding='utf-8')
                             for name in receipt['实际文件']}, 'completed_batches': saved['batches']}
        completed = subprocess.run([str(executable), '--ai-worker'],
            input=(json.dumps(payload, ensure_ascii=False) + '\n').encode('utf-8'), capture_output=True,
            timeout=180, cwd=target, creationflags=subprocess.CREATE_NO_WINDOW)
        events = [json.loads(line) for line in completed.stdout.decode('utf-8').splitlines()]
        progress = next(item for item in events if item['event'] == 'progress')
        assert progress['completed'] == len(saved['batches'])
        assert completed.returncode == 1 and events[-1] == {
            'event': 'error', 'message': '请先配置 API Key 和模型。'}
        assert not any(item['event'] in {'batch', 'complete'} for item in events)
        with sqlite3.connect((target / 'app.db').as_uri() + '?mode=ro', uri=True) as connection:
            assert connection.execute('SELECT count(*) FROM ai_requests').fetchone()[0] == 0
        assert checkpoint.read_bytes() == raw
        record['样本'][sample] = {'检查点sha256': hashlib.sha256(raw).hexdigest(),
            '恢复批数': progress['completed'], '规划批数': progress['total'], '模型请求': 0,
            '真实原检查点未改写': True, '隔离根无凭据': True, '新请求前受控停止': True}
        print(json.dumps({sample: record['样本'][sample]}, ensure_ascii=False), flush=True)
    (reports / '独立包真实检查点恢复验证.json').write_text(
        json.dumps(record, ensure_ascii=False, indent=2), encoding='utf-8', newline='\n')


if __name__ == '__main__':
    main()
