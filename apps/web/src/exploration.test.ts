import {expect,it} from 'vitest';
import {panelKey, restorePanels, serializePanels} from './exploration';
import type {Panel,ProjectDetail} from './types';

it('保存面板引用和阅读位置，恢复时不混入失效实体与证据',()=> {
  const location={path:'a.py',start_line:1,end_line:2,start_byte:0,end_byte:10};
  const entity={id:'f',name:'f',qualified_name:'f',signature:'private-signature-marker',language:'python',location,
    scope_id:'s',visible:true,hidden_reason:null,explanation:null,condition:null,unresolved_count:0};
  const module={id:'m',parent_id:null,name:'模块',summary:'职责',entity_ids:['f'],code_paths:['a.py'],incomplete:false};
  const call={id:'c',caller_scope_id:'s',target_id:'f',target_name:'f',expression:'f()',location,resolution:'confirmed',origin:'static',reason:'证据'};
  const project={analysis:{entities:[entity],scopes:[],calls:[call],diagnostics:[],tools:{}},
    architecture:{modules:[module]},file_ids:{'a.py':'file'}} as unknown as ProjectDetail;
  const panels:Panel[]=[{kind:'overview',scroll:{top:14,left:0}},{kind:'module',module},{kind:'function',entity},
    {kind:'relation',relation:{id:'r',from:'m',to:'m',label:'协作',calls:[call]},scroll:{top:60,left:0}},
    {kind:'source',entity,location,scroll:{top:123,left:22,line:4}}];
  const saved=serializePanels(panels);
  expect(JSON.stringify(saved)).not.toContain('private-signature-marker');
  const restored=restorePanels(saved,project);
  expect(restored.map(panelKey)).toEqual(panels.map(panelKey));
  expect(restored[4].scroll).toEqual({top:123,left:22,line:4});
  expect(restorePanels([{kind:'function',entity_id:'missing'},{kind:'relation',relation_id:'bad',from_id:'m',to_id:'m',call_ids:['missing']},
    {kind:'source',location:{...location,path:'outside.py'}}],project)).toEqual([]);
});

it('面板返回栈最多保存20项',()=> {
  expect(serializePanels(Array.from({length:25},():Panel=>({kind:'overview'})))).toHaveLength(20);
});
