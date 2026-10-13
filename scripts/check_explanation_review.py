"""在已授权固定样本的隔离数据中验证短委托讲解复核，不发布、不执行目标源码。"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import shutil
import sqlite3
import uuid
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

import explanation_review as review_module
from check_public_model import implementation_hash, saved_snapshot
from codescape.database import Database, now
from codescape.ledger import ai_scope
from codescape.models import Analysis, Architecture
from codescape.provider import (
    MAX_CONTEXT_CHARS,
    PIPELINE_VERSION,
    Provider,
    ProviderError,
    compatible_source_checkpoint,
)
from codescape.snapshots import matches_scope, read_snapshot

repository = Path(__file__).resolve().parents[1]
draft_path = Path(review_module.__file__)


def save_record(path, record):
    temporary = path.with_suffix('.json.tmp')
    temporary.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding='utf-8')
    temporary.replace(path)


async def main(args):
    reports = repository / 'docs/reports/p0'
    origin = reports / f'{args.id}-narrow真实模型验证.json'
    prior_raw = origin.read_bytes()
    prior = json.loads(prior_raw)
    approved = next(item for item in json.loads((repository / 'docs/benchmark_sources.json').read_text(
        encoding='utf-8'))['repositories'] if item['id'] == args.id)
    assert prior['Commit'] == approved['commit'] and prior['状态'] == '结构检查通过，待源码质量复核'
    assert compatible_source_checkpoint({'pipeline_version': prior['流水线']})
    original = Path(prior['数据目录']).resolve(strict=True)
    original.relative_to(repository / '.runtime/public-model-validation')
    index_path = Path(prior['静态索引制品']['path']).resolve(strict=True)
    index_path.relative_to(original / 'validation-artifacts')
    raw = index_path.read_bytes()
    assert hashlib.sha256(raw).hexdigest() == prior['静态索引制品']['sha256']
    analysis = Analysis.model_validate_json(raw)
    architecture = Architecture.model_validate(prior['architecture'])
    source = (original / 'owned-source').resolve(strict=True)
    baseline, manifest = saved_snapshot(approved)
    fixed_files = read_snapshot(baseline, manifest)
    catalogue = json.loads((repository / 'docs/benchmark_sources.json').read_text(encoding='utf-8'))
    files = {}
    for name in prior['实际文件']:
        assert (name in fixed_files and matches_scope(name, approved['initial_includes'])
                and not matches_scope(name, approved['initial_excludes'] + catalogue['common_excludes']))
        path = (source / name).resolve(strict=True)
        path.relative_to(source)
        assert path.read_bytes() == (baseline / name).read_bytes(), '来源字节与固定授权快照不一致'
        files[name] = fixed_files[name]
    selected = {entity.id for entity in analysis.entities if entity.name in args.functions} if args.targeted else None
    context, ranges, expected = review_module.review_context(architecture, analysis, files, selected_entity_ids=selected)
    if args.functions:
        allowed = {entity.id for entity in analysis.entities if entity.name in args.functions}
        payload = json.loads(context)
        payload['functions'] = [card for card in payload['functions'] if card['entity_id'] in allowed]
        context = json.dumps(payload, ensure_ascii=False, separators=(',', ':'))
        expected &= allowed
        ranges = {}
        for card in payload['functions']:
            for ref in [card['implementation'], *card['provided_evidence']]:
                ranges.setdefault(ref['path'], []).append((ref['start_line'], ref['end_line']))
    if not expected:
        raise ValueError('指定范围中没有可复核的短委托函数，请使用静态索引中的完整函数名。')
    if args.functions and len(context) > MAX_CONTEXT_CHARS:
        raise ValueError('指定函数复核材料超过保护上限，请缩小范围。')
    batches = [(context, ranges, expected)] if args.functions else review_module.review_batches(
        architecture, analysis, files)
    completed_plans = []
    resume = None
    if args.resume_review:
        resume_path = args.resume_review.resolve(strict=True)
        resume_path.relative_to(reports.resolve())
        resume = json.loads(resume_path.read_text(encoding='utf-8'))
        for key in ('样本', 'Commit', '接收端', '模型', 'project_id', 'version_id', '实际快照hash'):
            if resume.get(key) != prior[key]:
                raise ValueError('旧审校的工程、快照、接收端或模型不一致，不能复用。')
        if (args.functions or resume.get('指定函数筛选') or resume.get('指定完整实现复核')
                or resume.get('原报告sha256') != hashlib.sha256(prior_raw).hexdigest()
                or resume.get('候选复核实现sha256') != hashlib.sha256(draft_path.read_bytes()).hexdigest()
                or resume.get('分批复核材料sha256') != [hashlib.sha256(batch[0].encode('utf-8')).hexdigest()
                                                        for batch in batches]):
            raise ValueError('旧审校的实现、原结果或分批材料不一致，不能复用。')
        checkpoint = resume.get('复核检查点') or {}
        completed_plans = checkpoint.get('复核决定', {}).get('batches', [])
        if not completed_plans or checkpoint.get('completed') != len(completed_plans):
            raise ValueError('旧审校没有有效的已保存批次。')
        # 在任何新模型请求之前重建候选；不读取旧架构作为可信结果。
        rebuilt = architecture
        if len(completed_plans) > len(batches):
            raise ValueError('旧审校批次数超过当前范围。')
        for plan, (_, supplied, identifiers) in zip(completed_plans, batches):
            rebuilt = review_module.apply_review(rebuilt, review_module.ExplanationReview.model_validate(plan),
                                                analysis, files, supplied, identifiers)
    if args.preflight:
        print(json.dumps({'样本': args.id, 'Commit': approved['commit'], '短委托候选函数': len(expected),
                          '复核批次数': len(batches), '最大上下文字符': max(len(batch[0]) for batch in batches),
                          '可复用批数': len(completed_plans), '剩余请求批数': len(batches) - len(completed_plans),
                          '固定授权源码字节一致': True, '模型请求': 0, '执行目标代码': False}, ensure_ascii=False))
        return
    root = repository / '.runtime/public-model-validation' / str(uuid.uuid4())
    root.mkdir()
    with sqlite3.connect((original / 'app.db').as_uri() + '?mode=ro', uri=True) as connection:
        with sqlite3.connect(root / 'app.db') as destination:
            connection.backup(destination)
    shutil.copyfile(original / 'consents.json', root / 'consents.json')
    actual = Path(os.environ['LOCALAPPDATA']) / 'CodeScape'
    config = Provider(actual).settings()
    assert config['base_url'] == prior['接收端'] == 'https://www.cctq.ai/v1'
    assert config['model'] == prior['模型'] == args.model
    shutil.copyfile(actual / 'provider.json', root / 'provider.json')
    (root / 'credentials').mkdir()
    shutil.copyfile(actual / 'credentials/provider.dpapi', root / 'credentials/provider.dpapi')
    database = Database(root)
    provider = Provider(root, database)
    job_id = str(uuid.uuid4())
    database.insert_job({'id': job_id, 'project_id': prior['project_id'], 'version_id': prior['version_id'],
        'kind': 'analysis', 'status': 'running', 'position': 0, 'created_at': now(), 'updated_at': now(),
        'detail': '隔离短委托讲解复核，不发布或更改原结果'})
    record = {key: prior[key] for key in ('样本', 'Commit', '接收端', '模型', '实际文件', 'project_id', 'version_id')}
    record.update({'数据目录': str(root), '流水线': PIPELINE_VERSION, '原报告流水线': prior['流水线'],
        '核对时间UTC': datetime.now(UTC).isoformat(), '原报告sha256': hashlib.sha256(prior_raw).hexdigest(),
        '新建job_id': job_id, '实施代码起始sha256': implementation_hash(), '执行目标代码': False,
        '候选复核实现sha256': hashlib.sha256(draft_path.read_bytes()).hexdigest(),
        '实际快照hash': prior['实际快照hash'], '发送实体': sorted(expected),
        '指定函数筛选': args.functions,
        '指定完整实现复核': args.targeted,
        '复核材料sha256': hashlib.sha256(context.encode('utf-8')).hexdigest(),
        '分批复核材料sha256': [hashlib.sha256(batch[0].encode('utf-8')).hexdigest() for batch in batches],
        '性质': '候选实现的真实讲解复核，仅已授权窄范围；未集成产品、未发布，不是完整工程验收',
        '状态': '候选复核进行中', '上传前已核对固定授权源码字节': True})
    path = reports / f'{args.id}-explanation-review-narrow真实模型验证-{uuid.uuid4()}.json'
    # 请求前登记目录；进度汇总和崩溃核对可以读取实际账本，不遗漏在途用量。
    save_record(path, record)

    def saved_batch(result, decisions, total):
        record['复核检查点'] = {'completed': len(decisions), 'total': total,
            '复核决定': {'batches': decisions}, 'architecture': result.model_dump(),
            '状态': '仅已校验批次的隔离候选；其余仍为原讲解，未发布、未计全体通过'}
        save_record(path, record)

    if resume is not None:
        record['恢复来源'] = {'报告': resume_path.name, '报告sha256': hashlib.sha256(resume_path.read_bytes()).hexdigest(),
                            '复用已校验批数': len(completed_plans)}
        saved_batch(rebuilt, completed_plans, len(batches))

    try:
        with ai_scope(project_id=prior['project_id'], version_id=prior['version_id'], job_id=job_id,
                      request_config=config, phase='explanation_review',
                      source_chars=sum(len(card['implementation']['text']) for card in json.loads(context)['functions'])):
            async with asyncio.timeout(args.max_seconds):
                def checked(document):
                    review = review_module.ExplanationReview.model_validate(document)
                    result = review_module.apply_review(architecture, review, analysis, files, ranges, expected)
                    return result, review

                if args.functions:
                    (result, review), _ = await provider.structured(
                        review_module.review_prompt(review_module.ExplanationReview.model_json_schema()), context,
                        checked, max_tokens=8192)
                    decisions = [review.model_dump()]
                    saved_batch(result, decisions, 1)
                else:
                    result, _ = await review_module.review_explanations(
                        provider, architecture, analysis, files, on_batch=saved_batch, completed_plans=completed_plans)
                    decisions = provider._explanation_review_plans
        record.update({'状态': '候选讲解复核校验通过，未发布', '复核决定': {'batches': decisions},
                       'architecture': result.model_dump(), '原模块不变': result.modules == architecture.modules,
                       '原概览不变': result.overview == architecture.overview,
                       '原调用判断不变': result.call_judgments == architecture.call_judgments,
                       '原主要依赖依据不变': result.primary_dependencies == architecture.primary_dependencies,
                       '原函数集合不变': [note.entity_id for note in result.explanations]
                                        == [note.entity_id for note in architecture.explanations]})
    except TimeoutError:
        record['状态'] = '候选复核达到时限，未发布；在途结果可能未知'
    except ProviderError as error:
        record.update({'状态': '候选复核未完成，原结果保持', '错误': str(error)})
    except OSError:
        record.update({'状态': '候选复核保存失败，原结果保持', '错误': '隔离报告无法保存，后续请求已停止。'})
    finally:
        complete = record['状态'] == '候选讲解复核校验通过，未发布'
        database.update_job(job_id, status='completed' if complete else 'paused', detail='隔离讲解复核结束，未发布')
        with database.engine.connect() as connection:
            from sqlalchemy import select
            rows = [dict(row) for row in connection.execute(select(database.requests).where(
                database.requests.c.job_id == job_id)).mappings()]
        record.update({'请求账本': rows, '请求状态': dict(Counter(row['status'] for row in rows)),
            '已知total_tokens': sum((row['usage'] or {}).get('total_tokens', 0) for row in rows),
            '未知用量请求': sum('total_tokens' not in (row['usage'] or {}) for row in rows), '费用': None,
            '实施代码结束sha256': implementation_hash()})
        record['评测期间实施代码一致'] = record['实施代码起始sha256'] == record['实施代码结束sha256']
        assert origin.read_bytes() == prior_raw
        record['原报告未变'] = True
        try:
            save_record(path, record)
        finally:
            database.engine.dispose()
        print(json.dumps({key: record[key] for key in ('状态', '请求状态', '已知total_tokens', '未知用量请求')}, ensure_ascii=False))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--id', choices=['hiredis-c', 'spdlog-cpp', 'flask-python', 'zod-typescript'], required=True)
    parser.add_argument('--model', required=True)
    parser.add_argument('--max-seconds', type=int, default=600)
    parser.add_argument('--function', dest='functions', action='append', help='仅验证指定实际函数名的短委托，不扩展源码范围')
    parser.add_argument('--preflight', action='store_true', help='只读核对固定授权字节与批次，不调用模型、不创建评测数据')
    parser.add_argument('--targeted', action='store_true', help='复核指定完整函数实现，不限于短委托形态，必须同时指定--function')
    parser.add_argument('--resume-review', type=Path, help='显式复用同实现同材料的独立分批审校前缀，不替换原发布结果')
    args = parser.parse_args()
    if not 1 <= args.max_seconds <= 1800:
        parser.error('开发评测等待上限需在1至1800秒之间。')
    if args.targeted and not args.functions:
        parser.error('指定实现复核必须通过--function明确选择函数。')
    asyncio.run(main(args))
