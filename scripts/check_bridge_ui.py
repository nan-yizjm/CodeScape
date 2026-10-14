"""跨语言接口固定结果界面验证；不运行样例或请求模型。"""
import argparse
import json
import shutil
import uuid
from pathlib import Path

import uvicorn
from codescape.analyzers import analyze
from codescape.app import create_app
from codescape.database import Database, now
from codescape.models import Architecture, Module, Overview
from codescape.provider import Provider
from codescape.snapshots import create_snapshot, read_snapshot

repository = Path(__file__).resolve().parents[1]
arguments = argparse.ArgumentParser(description=__doc__)
arguments.add_argument('--resource-failure',action='store_true')
options = arguments.parse_args()
root = repository / '.runtime/bridge-ui-validation' / str(uuid.uuid4())
source = root / 'owned-source'
source.mkdir(parents=True)
files = {'bindings.cpp':'#include <pybind11/pybind11.h>\nint actual(int n){return n;}\nPYBIND11_MODULE(native, m){m.def("public_name", &actual);}\n',
    'consumer.py':'import native as bridge\ndef consume(): return bridge.public_name(2)\n'}
for path,text in files.items():
    (source/path).write_bytes(text.encode())
project_id,version_id = str(uuid.uuid4()),str(uuid.uuid4())
manifest = create_snapshot(source,root/'projects'/project_id)
snapshot = root/'projects'/project_id/'snapshots'/manifest['id']
analysis = analyze(snapshot,read_snapshot(snapshot,manifest),version_id)
assert len([call for call in analysis.calls if call.kind == 'interface']) == 1
arch = Architecture(modules=[Module(id=id,name=name,summary=summary,code_paths=[path],
    entity_ids=[entity.id for entity in analysis.entities if entity.visible and entity.location.path == path])
    for id,name,summary,path in [('consumer','Python 使用方','通过显式导入使用公开接口。','consumer.py'),
        ('native','C++ 绑定实现','将公开名称绑定到唯一 C++ 实现。','bindings.cpp')]],
    overview=Overview(introduction='人工预置的跨语言接口契约验证。',capabilities=['区分接口协作与直接调用'],
        principle='从 Python 使用、导入及 C++ 注册映射追溯唯一实现。',
        limitations=['没有编译、加载或执行样例；不代表运行时保证或真实 AI 质量。']))
db = Database(root)
db.insert({'id':project_id,'name':'跨语言接口人工验证','source':'应用自有固定结果 · 无模型请求',
    'created_at':now(),'status':'ready','snapshot':manifest,'version_id':version_id,'result':analysis.model_dump(),
    'architecture':arch.model_dump(),'demo':1})
db.engine.dispose()

async def forbidden(*args,**kwargs):
    raise AssertionError('接口界面验收禁止模型请求')

Provider.request = forbidden
nonce = 'bridge-ui-validation'
port = 8129 if options.resource_failure else 8128
if options.resource_failure:
    import codescape.app as application
    frontend_root = root/'isolated-frontend'
    shutil.copytree(repository/'apps/web/dist',frontend_root/'apps/web/dist')
    application.REPOSITORY = frontend_root
(repository/('.runtime/resource-ui-launch.json' if options.resource_failure else '.runtime/bridge-ui-launch.json')).write_text(json.dumps(
    {'url':f'http://127.0.0.1:{port}/#nonce={nonce}','root':str(root),'project_id':project_id}),encoding='utf-8')
uvicorn.run(create_app(root,nonce),host='127.0.0.1',port=port,log_level='warning')
