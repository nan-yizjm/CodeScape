import json

from codescape.analyzers import python_analysis
from codescape.provider import Provider


def test_context_preserves_hidden_and_top_level_call_scope(tmp_path):
    files = {"main.py": "def target(): return 1\ncallback = lambda: target()\nvalue = target()\n"}
    analysis = python_analysis(files, "v")
    confirmed = [call for call in analysis.calls if call.resolution == "confirmed"]
    assert len(confirmed) == 2
    text, ids = Provider(tmp_path)._context(analysis, files)
    payload = json.loads(text)
    assert {call["caller_scope_id"] for call in payload["calls"]} == {call.caller_scope_id for call in confirmed}
    assert len(ids) == 1
    assert all(entity["visible"] for entity in payload["entities"])
    # 模型可阅读真实范围与调用位置，但不把匿名/顶层伪装成目标函数的调用。
    target = next(entity for entity in analysis.entities if entity.visible)
    assert all(call["caller_scope_id"] != target.scope_id for call in payload["calls"])
