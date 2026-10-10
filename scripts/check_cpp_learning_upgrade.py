"""只读核对已经保存的 C++ 解析升级结果，不执行或重新解析目标代码。"""
from __future__ import annotations

import json
from pathlib import Path

from codescape.migration import learning_mapping
from codescape.models import Analysis

repository = Path(__file__).resolve().parents[1]
root = repository/'output/representative-index/8723120e-46b6-4701-bd65-15427f7aa5de'
old = Analysis.model_validate_json((root/'static-analysis.json').read_text(encoding='utf-8'))
new = Analysis.model_validate_json((root/'static-analysis-cpp-config6.json').read_text(encoding='utf-8'))
assert old.file_hashes == new.file_hashes
assert {item.id for item in old.entities} == {item.id for item in new.entities}
before,_ = learning_mapping(old,new)
after,report = learning_mapping(old,new,same_snapshot=True)
removed = [item for item in old.entities if item.visible and item.id not in after]
assert len(before) == 273
assert len(after) == 1853
assert sum(item.visible for item in new.entities) == 1857
assert len(removed) == 5
corrected = next(item for item in new.entities if item.name == 'static_format_result' and not item.visible
    and item.id in {entity.id for entity in removed})
previous_constructor = next(item for item in removed if item.id == corrected.id)
unmatched = [item for item in new.entities if item.visible and item.id not in after.values()]
assert len(unmatched) == 4
assert not corrected.visible and corrected.hidden_reason == '构造或析构'
assert corrected.name == 'static_format_result'
result = {'日期':'2026-10-11','方式':'已经保存的固定 spdlog 同一快照解析结果，只读复核',
    '旧工具':old.tools,'新工具':new.tools,'文件哈希完全一致':True,'全部实体ID保持':len(new.entities),
    '仅旧身份策略可迁移':len(before),'同快照唯一声明策略可迁移':len(after),
    '解析名称修正迁移':sum('解析名称修正' in item['reason'] for item in report['mapping']),
    '可见性修正':[{'旧名称':previous_constructor.name,'新名称':corrected.name,
        '原因':corrected.hidden_reason,'位置':corrected.location.model_dump()}],
    '歧义不迁移':[{'名称':item.qualified_name,'位置':item.location.model_dump()} for item in unmatched],
    '固定响应集成检查':'test_versions.py 验证候选发布后 mastered 保持；不同快照、指纹、范围和歧义反例不迁移',
    '真实模型请求':0,'执行目标代码':False,'边界':'只覆盖同一快照及唯一范围/声明/实现指纹；不按名称猜测任意改名迁移'}
destination = repository/'docs/reports/p0/C++解析升级学习迁移验证.json'
destination.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps({'可迁移':len(after),'可见性修正':1,'歧义不迁移':len(unmatched),'真实模型请求':0},ensure_ascii=False))
