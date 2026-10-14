"""独立应用进程禁止外部 DNS/TCP，验证已保存结果；不改变系统网络。"""
from __future__ import annotations

import errno
import ipaddress
import json
import socket
import threading
import uuid
from pathlib import Path

import uvicorn
from codescape.app import create_app
from codescape.database import Database, now
from codescape.models import Analysis, Architecture
from codescape.provider import Provider
from codescape.snapshots import create_snapshot

repository = Path(__file__).resolve().parents[1]
root = repository / '.runtime/offline-ui-validation' / str(uuid.uuid4())
source = root / 'owned-source'
source.mkdir(parents=True)
for name in ('input.py', 'engine.py', 'presentation.py'):
    (source/name).write_bytes((repository/'tests/fixtures/learning_project'/name).read_bytes())
saved = json.loads((repository/'docs/reports/p0/deepseek函数讲解验证.json').read_text(encoding='utf-8'))
analysis = Analysis.model_validate(saved['analysis'])
architecture = Architecture.model_validate(saved['architecture'])
architecture.overview.limitations.append('离线验证复用已保存样例；仅本应用进程禁止外部 DNS/TCP，不改变系统网络。')
project_id = str(uuid.uuid4())
manifest = create_snapshot(source, root/'projects'/project_id)
database = Database(root)
database.insert({'id':project_id,'name':'离线已保存样例验证','source':'已授权三个预置文件的历史响应；本轮零云端上传',
    'created_at':now(),'status':'ready','snapshot':manifest,'version_id':saved['version_id'],
    'result':analysis.model_dump(),'architecture':architecture.model_dump(),'demo':0})
database.engine.dispose()
# A disposable DPAPI credential makes the normal provider path attempt networking.
# It is not the user's key; even this fake credential never leaves the owned process.
Provider(root).configure('https://api.deepseek.com','deepseek-flash','offline-validation-only')
(root/'consents.json').write_text(json.dumps({project_id:{'base_url':'https://api.deepseek.com',
    'snapshot_hash':manifest['hash']}}),encoding='utf-8')
network = {'mode':'仅独立应用进程禁止外部 DNS/TCP','blocked':[], 'external_connections':0}
lock = threading.Lock()


def blocked(kind, address):
    with lock:
        network['blocked'].append({'kind':kind,'address':str(address)})
        (root/'network.json').write_text(json.dumps(network,ensure_ascii=False,indent=2),encoding='utf-8')
    raise OSError(errno.ENETUNREACH,'独立验证进程已禁止外部网络')


original_dns, original_connect, original_connect_ex = socket.getaddrinfo, socket.socket.connect, socket.socket.connect_ex


def local_address(address):
    try:
        return ipaddress.ip_address(address).is_loopback
    except ValueError:
        return address == 'localhost'


def offline_dns(host, *args, **kwargs):
    if host is not None and not local_address(host.decode() if isinstance(host,bytes) else host):
        blocked('DNS',host)
    return original_dns(host,*args,**kwargs)


def offline_connect(instance, address):
    if isinstance(address,tuple) and not local_address(address[0]):
        blocked('TCP',address[0])
    return original_connect(instance,address)


def offline_connect_ex(instance, address):
    if isinstance(address,tuple) and not local_address(address[0]):
        try:
            blocked('TCP',address[0])
        except OSError:
            return errno.ENETUNREACH
    return original_connect_ex(instance,address)


socket.getaddrinfo, socket.socket.connect, socket.socket.connect_ex = offline_dns, offline_connect, offline_connect_ex
(root/'network.json').write_text(json.dumps(network,ensure_ascii=False,indent=2),encoding='utf-8')
# The production default runs model work in another owned process. This UI verifier
# deliberately keeps that work in this blocked process; publication/ledger stay real.
application = create_app(root,'offline-ui-validation',architecture_runner=
    lambda provider,*args,**kwargs: provider.architecture(*args,**kwargs))
(repository/'.runtime/offline-ui-launch.json').write_text(json.dumps({'url':'http://127.0.0.1:8130/#nonce=offline-ui-validation',
    'root':str(root),'project_id':project_id,'saved_version_id':saved['version_id']},ensure_ascii=False),encoding='utf-8')
uvicorn.run(application,host='127.0.0.1',port=8130,log_level='warning')
