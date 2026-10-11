"""独立阅读固定源码后编写预期；仅运行 CodeScape 静态分析，不执行目标代码。"""
from __future__ import annotations

import json
from pathlib import Path

from codescape.analyzers import typescript_analysis
from codescape.models import Analysis
from codescape.snapshots import read_snapshot

repository = Path(__file__).resolve().parents[1]
reports = repository/'docs/reports/p0'


def check(name,cases):
    record = json.loads((reports/f'{name}静态测量.json').read_text(encoding='utf-8'))
    root = Path(record['root']).resolve(strict=True)
    root.relative_to(repository/'output/representative-index')
    manifest = json.loads((root/'manifest.json').read_text(encoding='utf-8'))
    snapshot = root/'project/snapshots'/manifest['id']
    before = read_snapshot(snapshot,manifest)
    if name=='zod-typescript':
        analysis = typescript_analysis(snapshot,before,'representative-index')
        assert not analysis.diagnostics
        (root/'static-analysis-ts-bindings2.json').write_text(analysis.model_dump_json(),encoding='utf-8')
    else:
        analysis = Analysis.model_validate_json((root/'static-analysis.json').read_text(encoding='utf-8'))
    scopes = {item.id:item for item in analysis.scopes}
    entities = {item.id:item for item in analysis.entities}
    checked = []
    for path,line,expression,resolution,target_name,scope_kind in cases:
        selected = [call for call in analysis.calls if call.location.path==path
            and call.location.start_line==line and call.expression==expression]
        assert len(selected)==1,(path,line,expression)
        call = selected[0]
        assert call.resolution==resolution,(expression,call.resolution)
        assert (entities[call.target_id].qualified_name if call.target_id else None)==target_name
        assert scopes[call.caller_scope_id].kind==scope_kind
        raw = (snapshot/path).read_bytes()
        assert expression in raw.splitlines()[line-1].decode('utf-8')
        assert call.location.start_byte < call.location.end_byte <= len(raw)
        checked.append({'文件':path,'行':line,'表达式':expression,'分类':resolution,
            '目标':target_name,'真实作用域':scopes[call.caller_scope_id].model_dump(),
            '字节证据':raw[call.location.start_byte:call.location.end_byte].decode('utf-8')})
    assert before==read_snapshot(snapshot,manifest)
    return {'Commit':record['origin']['commit'],'hash':manifest['hash'],'工具':analysis.tools,
        '源码字节未变':True,'调用基线':checked}


def main():
    result = {'日期':'2026-10-11','方式':'先读源码建立预期，不从解析结果自动生成预期',
        'Flask':check('flask-python',[
            ('src/flask/helpers.py',529,'_prepare_send_file_kwargs','confirmed','_prepare_send_file_kwargs','function'),
            ('src/flask/app.py',1016,'self.dispatch_request','excluded_indirect',None,'function'),
            ('src/flask/app.py',1019,'self.finalize_request','excluded_indirect',None,'function'),
            ('src/flask/app.py',1039,'self.make_response','excluded_indirect',None,'function')]),
        'Zod':check('zod-typescript',[
            ('packages/zod/src/v4/classic/parse.ts',13,'core._parse','excluded_indirect',None,'module'),
            ('packages/zod/src/v4/classic/parse.ts',20,'core._parseAsync','excluded_indirect',None,'module'),
            ('packages/zod/src/v4/classic/parse.ts',27,'core._safeParse','excluded_indirect',None,'module'),
            ('packages/zod/src/v4/core/parse.ts',25,'schema._zod.run','unresolved',None,'lambda'),
            ('packages/zod/src/v4/core/parse.ts',30,'util.finalizeIssue','confirmed','finalizeIssue','lambda'),
            ('packages/zod/src/v4/core/parse.ts',78,'failure','confirmed','failure','lambda'),
            ('packages/zod/src/v4/core/parse.ts',89,'util.finalizeIssue','confirmed','finalizeIssue','lambda')]),
        '模型请求':0,'执行目标代码':False,'边界':'仅这些调用/作用域抽样，不外推整体准确率或 AI 质量。'}
    (reports/'Python与TypeScript源码抽样基线.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print('Flask 4 项、Zod 7 项源码调用/作用域基线通过；模型请求 0。')


if __name__=='__main__':
    main()
