"""固定应用自有 spdlog 快照上的静态超时回退验证；不上传、不执行目标。"""
import asyncio
import json
import time
from pathlib import Path

from codescape.index_process import index_snapshot
from codescape.snapshots import read_snapshot

repository=Path(__file__).resolve().parents[1]
report=json.loads((repository/'docs/reports/p0/spdlog-cpp带依赖静态测量.json').read_text(encoding='utf-8'))
root=Path(report['root']).resolve(strict=True)
if not root.is_relative_to((repository/'output/representative-index').resolve()):
    raise ValueError('只能使用本轮应用自有测量目录。')
manifest=json.loads((root/'manifest.json').read_text(encoding='utf-8'))
snapshot=root/'project/snapshots'/manifest['id']
files=read_snapshot(snapshot,manifest)
started=time.perf_counter()
value=asyncio.run(index_snapshot(snapshot,manifest,'index-fallback-validation'))
assert any(item.category == 'native_index_incomplete' for item in value.diagnostics)
assert sum(entity.visible for entity in value.entities) == report['visible_functions']
assert not any(call.resolution == 'confirmed' for call in value.calls if call.kind == 'direct')
assert files == read_snapshot(snapshot,manifest)
result={'日期':'2026-10-11','样本Commit':manifest['origin']['commit'],'snapshot_hash':manifest['hash'],
    '文件':len(files),'可见函数':sum(entity.visible for entity in value.entities),'隐藏结构':sum(not entity.visible for entity in value.entities),
    '正常解析期限秒':120,'总耗时秒':round(time.perf_counter()-started,3),'原生语义回退':True,
    '语法实体未丢失':True,'C++直接调用未假确认':True,'源码字节不变':True,'模型请求':0,
    '工具':value.tools,'边界':'仅验证固定快照的原生超时回退，不代表此结果完成了 C++ 工程语义或 AI 架构验收。'}
(repository/'docs/reports/p0/静态超时回退验证.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(result,ensure_ascii=False))
