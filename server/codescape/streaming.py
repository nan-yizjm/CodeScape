"""Bounded Chat Completions SSE reception; partial output is never published."""
from __future__ import annotations

import codecs
import json

import httpx

MAX_RESPONSE_BYTES = 8 * 1024 * 1024


class StreamContractError(ValueError):
    pass


async def read_json_response(response):
    chunks, size = [], 0
    async for chunk in response.aiter_bytes():
        size += len(chunk)
        if size > MAX_RESPONSE_BYTES:
            raise StreamContractError('模型响应超过接收保护上限。')
        chunks.append(chunk)
    return json.loads(b''.join(chunks))


async def read_chat_stream(response, on_usage):
    decoder = codecs.getincrementaldecoder('utf-8-sig')('strict')
    buffer, data, pieces = [], [], []
    size, finished, done, skip_lf = 0, None, False, False

    def event():
        nonlocal finished, done
        if not data:
            return
        raw = '\n'.join(data)
        data.clear()
        if raw == '[DONE]':
            if finished is None:
                raise StreamContractError('流式响应缺少完成原因。')
            done = True
            return
        try:
            value = json.loads(raw)
            if not isinstance(value, dict) or 'error' in value:
                raise ValueError
            on_usage(value.get('usage'))
            choices = value.get('choices', [])
            if not isinstance(choices, list):
                raise ValueError
            for choice in choices:
                if not isinstance(choice, dict):
                    raise ValueError
                if choice.get('index', 0) != 0:
                    continue
                delta = choice.get('delta', {})
                if not isinstance(delta, dict):
                    raise ValueError
                content = delta.get('content')
                if content is not None:
                    if not isinstance(content, str) or finished is not None:
                        raise ValueError
                    pieces.append(content)
                reason = choice.get('finish_reason')
                if reason is not None:
                    if not isinstance(reason, str) or finished is not None:
                        raise ValueError
                    finished = reason
        except (ValueError, TypeError):
            raise StreamContractError('流式响应不符合结构化契约。') from None

    def line(value):
        if value == '':
            event()
        elif value.startswith('data:'):
            field = value[5:]
            data.append(field[1:] if field.startswith(' ') else field)

    try:
        async for chunk in response.aiter_bytes():
            size += len(chunk)
            if size > MAX_RESPONSE_BYTES:
                raise StreamContractError('模型响应超过接收保护上限。')
            text = decoder.decode(chunk)
            # CR, LF and CRLF may be split across arbitrary transport chunks.
            for char in text:
                if skip_lf:
                    skip_lf = False
                    if char == '\n':
                        continue
                if char in '\r\n':
                    line(''.join(buffer))
                    buffer.clear()
                    skip_lf = char == '\r'
                    if done:
                        return {'choices': [{'message': {'content': ''.join(pieces)}, 'finish_reason': finished}]}
                else:
                    buffer.append(char)
        decoder.decode(b'', final=True)
    except UnicodeError:
        raise StreamContractError('流式响应包含无效UTF-8。') from None
    # EOF is not a successful completion, even if some JSON happens to parse.
    raise httpx.ReadError('流式连接在完成标记前中断。')
