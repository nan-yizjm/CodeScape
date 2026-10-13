"""只读严格核对固定公开样本的混合批次恢复，不调用模型、不执行目标源码。"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from codescape.models import Analysis, Architecture
from codescape.provider import Provider, checkpoint_source_generations, compatible_source_checkpoint

repository = Path(__file__).resolve().parents[1]
reports = repository / 'docs/reports/p0'


def main():
    proof = {'核对时间UTC': datetime.now(UTC).isoformat(), '模型请求': 0,
             '性质': '只读核对已保存源码批次的恢复；不计全局发布或语义质量通过', '样本': {}}
    for sample in ('hiredis-c', 'spdlog-cpp', 'flask-python', 'zod-typescript'):
        receipt = reports / f'{sample}-full真实模型验证.json'
        record = json.loads(receipt.read_text(encoding='utf-8'))
        root = Path(record['数据目录']).resolve(strict=True)
        root.relative_to(repository / '.runtime/public-model-validation')
        checkpoint = root / 'projects' / record['project_id'] / 'architecture-checkpoint.json'
        raw = checkpoint.read_bytes()
        saved = json.loads(raw)
        assert compatible_source_checkpoint(saved)
        with sqlite3.connect((root / 'app.db').as_uri() + '?mode=ro', uri=True) as connection:
            row = connection.execute('SELECT result,version_id,snapshot FROM prototype_projects WHERE id=?',
                                     (record['project_id'],)).fetchone()
        assert row and row[1] == record['version_id']
        snapshot = json.loads(row[2])
        assert snapshot['hash'] == record['实际快照hash']
        analysis = Analysis.model_validate_json(row[0])
        files = {name: (root / 'owned-source' / name).read_text(encoding='utf-8')
                 for name in record['实际文件']}
        assert set(files) == set(snapshot['files'])
        completed = [Architecture.model_validate(batch) for batch in saved['batches']]
        planner = Provider.__new__(Provider)
        planned, reused = planner._execution_batches(analysis, files, completed)
        assert reused == completed, f'{sample}：已保存{len(completed)}批，恢复{len(reused)}批；未全部进入连续恢复前缀。'
        # 活跃任务可能在只读核对期间原子保存新的批次；记录所读版本，绝不改写它。
        proof['样本'][sample] = {
            'Commit': record['Commit'], '快照hash': snapshot['hash'],
            '检查点sha256': hashlib.sha256(raw).hexdigest(), '已保存批数': len(completed),
            '恢复规划批数': len(planned), '严格实体引用关系校验': '通过',
            '全部前缀复用': True, '检查点读取期间稳定': checkpoint.read_bytes() == raw,
            '源码生成版本集合': sorted(checkpoint_source_generations(saved)),
            '讲解数': sum(len(batch.explanations) for batch in completed),
        }
    destination = reports / '精简批次真实检查点恢复核对.json'
    if destination.exists():
        raw = destination.read_bytes()
        archive = destination.with_name(destination.stem + '-' + hashlib.sha256(raw).hexdigest()[:16] + '.json')
        if archive.exists():
            assert archive.read_bytes() == raw
        else:
            archive.write_bytes(raw)
    destination.write_text(json.dumps(proof, ensure_ascii=False, indent=2), encoding='utf-8', newline='\n')
    print(json.dumps(proof['样本'], ensure_ascii=False))


if __name__ == '__main__':
    main()
