"""只读核对既定中转站模型目录；不发起生成、不发送源码、不输出响应正文。"""
import argparse
import asyncio
import json
import os
from datetime import UTC, datetime
from pathlib import Path

import httpx
from codescape.provider import Provider
from codescape.streaming import StreamContractError, read_json_response


async def main(expected_model):
    repository = Path(__file__).resolve().parents[1]
    provider = Provider(Path(os.environ['LOCALAPPDATA']) / 'CodeScape')
    settings = provider.settings()
    assert settings['base_url'] == 'https://www.cctq.ai/v1' and settings['model'] == expected_model
    record = {'日期': datetime.now().date().isoformat(), '核对时间UTC': datetime.now(UTC).isoformat(),
        '接收端': settings['base_url'], '模型': settings['model'], '接口': '/models', '方法': 'GET',
        '源码发送': False, '模型生成请求数': 0, 'HTTP状态': None, '目录包含指定模型': None,
        '边界': '目录展示不证明模型实际上游可调用；不推断账户余额、价格、服务内部原因或架构质量。'}
    try:
        async with asyncio.timeout(20), httpx.AsyncClient(timeout=10, trust_env=False, follow_redirects=False) as client:
            async with client.stream('GET', settings['base_url'] + '/models',
                                     headers={'Authorization': 'Bearer ' + provider.credentials.read()}) as response:
                record['HTTP状态'] = response.status_code
                if response.status_code == 200:
                    document = await read_json_response(response)
                    models = document.get('data') if isinstance(document, dict) else None
                    if isinstance(models, list):
                        record['目录包含指定模型'] = any(isinstance(item, dict) and item.get('id') == settings['model']
                                                   for item in models)
                        record['模型条目数'] = len(models)
                    else:
                        record['状态'] = '目录结构不符合预期，未保存正文'
                else:
                    record['状态'] = '模型目录请求失败，未保存正文'
    except (httpx.HTTPError, TimeoutError):
        record['状态'] = '模型目录传输失败，未保存异常正文'
    except (ValueError, StreamContractError):
        record['状态'] = '模型目录响应不符合接收契约，未保存正文'
    path = repository / 'docs/reports/p0/中转站模型目录只读核对.json'
    if path.exists():
        path.with_name(path.stem + '-' + datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ') + '.json').write_bytes(path.read_bytes())
    path.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(record, ensure_ascii=False))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='只读核对明确指定的模型是否出现在目录中')
    parser.add_argument('--model', required=True, help='已授权接收端的准确模型ID')
    asyncio.run(main(parser.parse_args().model))
