"""请求账本浏览器夹具：全部 HTTP 响应在进程内固定，不访问模型。"""
import asyncio
import uuid
from pathlib import Path

import httpx
import uvicorn
from codescape.app import create_app
from codescape.database import Database
from codescape.ledger import RequestLedger
from codescape.provider import Provider, ProviderError

root = Path(__file__).resolve().parents[1] / ".runtime" / "usage-ui-validation" / str(uuid.uuid4())
database = Database(root)
provider = Provider(root, database)
provider.configure("https://example.invalid", "固定响应请求账本", "local-ui-validation-key")
original_client = httpx.AsyncClient
counter = 0


def response(request):
    global counter
    counter += 1
    if counter == 2:
        return httpx.Response(401)
    content = "not-json" if counter == 3 else '{"connected":true}'
    result = {"choices": [{"message": {"content": content}}]}
    if counter != 4:
        result["usage"] = {"total_tokens": 14}
    return httpx.Response(200, json=result)


async def populate():
    httpx.AsyncClient = lambda **kwargs: original_client(transport=httpx.MockTransport(response), **kwargs)
    try:
        for _ in range(23):
            try:
                await provider.test()
            except ProviderError:
                pass
    finally:
        httpx.AsyncClient = original_client


asyncio.run(populate())
ledger = RequestLedger(database)
identifier = ledger.prepare(provider.settings(), {"not_saved": "无源码夹具"}, "interrupted", 1)
ledger.sent(identifier)
database.engine.dispose()
uvicorn.run(create_app(root, "usage-ui-test"), host="127.0.0.1", port=8126, log_level="warning")
