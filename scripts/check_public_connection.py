"""隔离数据中的一次无源码连接检查，最多等待60秒，不改主工作区。"""
import asyncio
import json
import os
import shutil
import uuid
from datetime import UTC, datetime
from pathlib import Path

from codescape.database import Database
from codescape.ledger import RequestLedger, ai_scope
from codescape.provider import PIPELINE_VERSION, Provider, ProviderError

ERROR_CODES = frozenset({'server_error', 'internal_error', 'internal_server_error', 'upstream_error', 'api_error',
    'rate_limit_exceeded', 'rate_limit_error', 'insufficient_quota', 'authentication_error', 'invalid_api_key',
    'invalid_request_error', 'context_length_exceeded', 'invalid_parameter', 'model_not_found',
    'model_not_supported', 'overloaded_error', 'timeout', 'timeout_error', 'upstream_timeout', 'bad_gateway'})


def safe_remote_error(error):
    """不保存message或任意服务字段；仅保留有限错误类别。"""
    if not isinstance(error, dict):
        return {'类型': '非对象错误事件'}
    result = {}
    for key in ('code', 'type', 'status'):
        value = error.get(key)
        result[key] = value if ((isinstance(value, str) and value in ERROR_CODES)
            or (type(value) is int and value in {400, 401, 402, 403, 408, 413, 422, 429, 500, 502, 503, 504})) else '未提供或未识别'
    return result


async def main():
    repository = Path(__file__).resolve().parents[1]
    actual = Path(os.environ['LOCALAPPDATA']) / 'CodeScape'
    settings = Provider(actual).settings()
    assert settings['base_url'] == 'https://www.cctq.ai/v1' and settings['model'] == 'gpt-6-luna'
    root = repository / '.runtime/public-model-validation' / str(uuid.uuid4())
    (root / 'credentials').mkdir(parents=True)
    shutil.copyfile(actual / 'credentials/provider.dpapi', root / 'credentials/provider.dpapi')
    shutil.copyfile(actual / 'provider.json', root / 'provider.json')
    database = Database(root)
    provider = Provider(root, database)
    record = {'日期': datetime.now().date().isoformat(), '核对时间UTC': datetime.now(UTC).isoformat(),
        '性质': '真实模型无源码连接检查，不替代架构与质量验收', '数据目录': str(root),
        '接收端': settings['base_url'], '模型': settings['model'], '流水线': PIPELINE_VERSION,
        '流式接收': settings['stream_response'], '实际文件': [], '源码发送': False,
        '最多请求': 1, '等待上限秒': 60, '执行目标代码': False, '服务错误事件白名单': []}
    original_loads = json.loads
    def observed_loads(value, *args, **kwargs):
        result = original_loads(value, *args, **kwargs)
        if isinstance(result, dict) and 'error' in result:
            record['服务错误事件白名单'].append(safe_remote_error(result['error']))
        return result
    try:
        json.loads = observed_loads
        with ai_scope(phase='connection', source_chars=0):
            result, _ = await asyncio.wait_for(provider.request(
                '返回 JSON 对象 {"connected": true}。这是不含源码的连接测试。',
                '请只返回 json: {"connected": true}', max_tokens=256, max_attempts=1), 60)
        record['状态'] = '连接通过' if result.get('connected') is True else '响应未返回预期连接状态'
    except TimeoutError:
        record['状态'] = '60秒连接检查截止，远端结果未知'
    except ProviderError as error:
        record.update({'状态': '连接失败', '错误': str(error)})
    finally:
        json.loads = original_loads
        summary = RequestLedger(database).summary()
        assert summary['request_count'] <= 1
        record.update({'请求账本': summary['records'], '已知total_tokens': summary['known_total_tokens'],
                       '未知用量请求': sum(row['usage'] is None for row in summary['records'])})
        path = repository / 'docs/reports/p0' / f'zod-typescript-connection-bounded真实模型验证-{uuid.uuid4()}.json'
        path.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding='utf-8')
        database.engine.dispose()
        print(json.dumps({key: record[key] for key in ('状态', '已知total_tokens', '未知用量请求', '服务错误事件白名单')}, ensure_ascii=False))


if __name__ == '__main__':
    asyncio.run(main())
