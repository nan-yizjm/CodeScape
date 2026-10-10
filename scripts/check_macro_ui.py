"""应用自有宏定位界面验收；解析源码但不执行样例，禁止模型请求。"""
import json
import uuid
from pathlib import Path

import uvicorn
from codescape.analyzers import cpp_analysis
from codescape.app import create_app
from codescape.database import Database, now
from codescape.models import Architecture, Module, Overview
from codescape.provider import Provider
from codescape.snapshots import create_snapshot, read_snapshot

repository = Path(__file__).resolve().parents[1]
root = repository / ".runtime/macro-ui-validation" / str(uuid.uuid4())
source = root / "owned-source"
source.mkdir(parents=True)
(source / "owned.c").write_bytes('// 中文 😀\r\n#define CALL(v) target(v)\r\nint target(int v){return v;}\r\nint caller(){\r\n\treturn CALL(2);\r\n}\r\n'.encode())
project_id, version_id = str(uuid.uuid4()), str(uuid.uuid4())
manifest = create_snapshot(source, root / "projects" / project_id)
snapshot = root / "projects" / project_id / "snapshots" / manifest["id"]
analysis = cpp_analysis(snapshot, read_snapshot(snapshot, manifest), version_id)
assert len([call for call in analysis.calls if call.resolution == 'confirmed']) == 1
arch = Architecture(modules=[Module(id='macro',name='宏定位验证',summary='预置调用位置与宏定义定位验证。',
    entity_ids=[entity.id for entity in analysis.entities],code_paths=['owned.c'])],
    overview=Overview(introduction='人工预置宏定位验证项目。',capabilities=['验证宏定义与展开位置'],
        principle='CodeScape 只读取和解析测试源码，未运行测试源码。',limitations=['不是实际模型分析。']))
db = Database(root)
db.insert({'id':project_id,'name':'宏源码定位人工验证','source':'应用自有固定结果 · 无模型请求',
    'created_at':now(),'status':'ready','snapshot':manifest,'version_id':version_id,
    'result':analysis.model_dump(),'architecture':arch.model_dump(),'demo':1})
db.engine.dispose()

async def forbidden(*args, **kwargs):
    raise AssertionError('宏界面验收禁止模型请求')

Provider.request = forbidden
nonce = 'macro-ui-validation'
(repository / '.runtime/macro-ui-launch.json').write_text(json.dumps(
    {'url':f'http://127.0.0.1:8127/#nonce={nonce}','root':str(root),'project_id':project_id}),encoding='utf-8')
uvicorn.run(create_app(root,nonce),host='127.0.0.1',port=8127,log_level='warning')
