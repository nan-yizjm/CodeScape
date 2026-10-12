"""Bounded Chat Completions SSE reception; partial output is never published."""
from __future__ import annotations

import codecs
import json

import httpx

MAX_RESPONSE_BYTES = 8 * 1024 * 1024


class StreamContractError(ValueError):
    def __init__(self, message, *, code='INVALID_EVENT'):
        super().__init__(message)
        # 仅固定类别进入账本；事件正文、远端错误及请求头不进入错误说明。
        self.code = code if code in {
            'RESPONSE_TOO_LARGE', 'DONE_WITHOUT_FINISH', 'INVALID_JSON', 'ERROR_EVENT',
            'INVALID_CHOICES', 'INVALID_CHOICE', 'INVALID_DELTA', 'INVALID_CONTENT',
            'CONTENT_AFTER_FINISH', 'INVALID_FINISH', 'DUPLICATE_FINISH', 'INVALID_UTF8',
        } else 'INVALID_EVENT'


async def read_json_response(response):
    chunks, size = [], 0
    async for chunk in response.aiter_bytes():
        size += len(chunk)
        if size > MAX_RESPONSE_BYTES:
            raise StreamContractError('模型响应超过接收保护上限。', code='RESPONSE_TOO_LARGE')
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
                raise StreamContractError('流式响应缺少完成原因。', code='DONE_WITHOUT_FINISH')
            done = True
            return
        try:
            value = json.loads(raw)
            if not isinstance(value, dict):
                raise StreamContractError('流式事件不是对象。')
            if 'error' in value:
                raise StreamContractError('流式服务返回错误事件。', code='ERROR_EVENT')
            on_usage(value.get('usage'))
            choices = value.get('choices', [])
            if not isinstance(choices, list):
                raise StreamContractError('流式选择项不是列表。', code='INVALID_CHOICES')
            for choice in choices:
                if not isinstance(choice, dict):
                    raise StreamContractError('流式选择项不是对象。', code='INVALID_CHOICE')
                if choice.get('index', 0) != 0:
                    continue
                delta = choice.get('delta', {})
                if not isinstance(delta, dict):
                    raise StreamContractError('流式增量不是对象。', code='INVALID_DELTA')
                content = delta.get('content')
                if content is not None:
                    if not isinstance(content, str):
                        raise StreamContractError('流式正文不是文本。', code='INVALID_CONTENT')
                    if finished is not None:
                        raise StreamContractError('流式选择项完成后继续输出。', code='CONTENT_AFTER_FINISH')
                    pieces.append(content)
                reason = choice.get('finish_reason')
                if reason is not None:
                    if not isinstance(reason, str):
                        raise StreamContractError('流式完成原因不是文本。', code='INVALID_FINISH')
                    if finished is not None:
                        raise StreamContractError('流式选择项重复完成。', code='DUPLICATE_FINISH')
                    finished = reason
        except StreamContractError:
            raise
        except (ValueError, TypeError):
            raise StreamContractError('流式响应不符合结构化契约。', code='INVALID_JSON') from None

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
                raise StreamContractError('模型响应超过接收保护上限。', code='RESPONSE_TOO_LARGE')
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
        raise StreamContractError('流式响应包含无效UTF-8。', code='INVALID_UTF8') from None
    # EOF is not a successful completion, even if some JSON happens to parse.
    raise httpx.ReadError('流式连接在完成标记前中断。')
