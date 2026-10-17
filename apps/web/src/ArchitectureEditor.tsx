import {useState} from 'react';
import {api} from './api';
import {abstractModules} from './graph';
import type {Module, ProjectDetail} from './types';

export function ArchitectureEditor({project,onSaved}: {project:ProjectDetail;onSaved:()=>void}) {
  const [draft,setDraft]=useState<Module[]>(()=>structuredClone(abstractModules(project.architecture?.modules || [],project.analysis)).map(m=>({...m,
    code_paths:m.code_paths.filter(p=>!!project.file_ids[p])})));
  const [selected,setSelected]=useState(draft[0]?.id || ''), [members,setMembers]=useState<string[]>([]);
  const [splitName,setSplitName]=useState(''),[remainingName,setRemainingName]=useState('');
  const [mergeIds,setMergeIds]=useState<string[]>([]),[mergeName,setMergeName]=useState('');
  const [preview,setPreview]=useState<{module_count:number;function_count:number}|null>(null);
  const [error,setError]=useState(''),[busy,setBusy]=useState(false);
  const current=draft.find(m=>m.id === selected), edit=project.manual_edit;
  const leafs=draft.filter(m=>!draft.some(n=>n.parent_id === m.id));
  const change=(next:Module[])=>{setDraft(next);setPreview(null);setError('');};
  const update=(values:Partial<Module>)=>change(draft.map(m=>m.id === selected ? {...m,...values} : m));
  const run=async(action:'apply'|'undo'|'redo'|'restore',save=false)=> {
    setBusy(true);setError('');
    try {
      const result=await api<{module_count:number;function_count:number}>(`/projects/${project.id}/versions/${project.version_id}/architecture${save ? '' : '/preview'}`,
        {action,modules:draft,expected_revision:edit?.revision || 0},save ? 'PUT' : 'POST');
      if(save)onSaved();else setPreview(result);
    } catch(e) {setError((e as Error).message);}finally{setBusy(false);}
  };
  const split=()=> {
    if(!current || !splitName.trim() || !remainingName.trim() || !members.length || members.length === current.entity_ids.length)return;
    const left={...current,id:crypto.randomUUID(),parent_id:current.id,name:splitName.trim(),entity_ids:members};
    const right={...current,id:crypto.randomUUID(),parent_id:current.id,name:remainingName.trim(),entity_ids:current.entity_ids.filter(id=>!members.includes(id))};
    const filePaths=(ids:string[])=>[...new Set(project.analysis.entities.filter(e=>ids.includes(e.id)).map(e=>e.location.path))];
    left.code_paths=filePaths(left.entity_ids);right.code_paths=filePaths(right.entity_ids);
    const describe=(ids:string[])=>project.analysis.entities.filter(e=>ids.includes(e.id)).slice(0,3)
      .map(e=>e.explanation || `包含函数 ${e.qualified_name}`).join('；').slice(0,600);
    left.summary=describe(left.entity_ids);right.summary=describe(right.entity_ids);
    change([...draft.map(m=>m.id === current.id ? {...m,entity_ids:[]} : m),left,right]);setMembers([]);
  };
  const merge=()=> {
    const selectedModules=leafs.filter(m=>mergeIds.includes(m.id));
    if(selectedModules.length<2 || !mergeName.trim() || new Set(selectedModules.map(m=>m.parent_id)).size !== 1){setError('请选择至少两个同级叶模块并填写合并后的名称。');return;}
    const combined:Module={id:crypto.randomUUID(),parent_id:selectedModules[0].parent_id,name:mergeName.trim(),
      summary:selectedModules.map(m=>m.summary).join('；').slice(0,600),entity_ids:selectedModules.flatMap(m=>m.entity_ids),
      code_paths:[...new Set(selectedModules.flatMap(m=>m.code_paths))],incomplete:selectedModules.some(m=>m.incomplete)};
    const parent=draft.find(m=>m.id===combined.parent_id);
    const collapse=parent && !parent.entity_ids.length && draft.filter(m=>m.parent_id===parent.id).every(m=>mergeIds.includes(m.id));
    if(collapse && parent){combined.parent_id=parent.parent_id;combined.code_paths=[...new Set([...combined.code_paths,...parent.code_paths])];combined.incomplete ||= parent.incomplete;}
    change([...draft.filter(m=>!mergeIds.includes(m.id) && !(collapse && m.id===parent?.id)),combined]);setSelected(combined.id);setMergeIds([]);
  };
  return <div className="architecture-editor"><p className="tool-muted">调整只改变模块组织，原始 AI 架构和源码证据保留。先预览，再保存。</p>
    {edit?.conflicts.map((c,i)=><div className="inline-error" key={i}><p>{c.message}</p><button onClick={()=>{const params=new URLSearchParams(location.search);params.set('project',project.id);params.set('version',c.from_version_id);location.assign('/?'+params);}}>查看原修改版本</button></div>)}
    {!!edit?.conflicts.length && <p>保存时以当前草稿为准，确认处理上述待复核项；也可明确放弃人工修改。</p>}
    <div className="tool-row"><button disabled={busy || !edit?.can_undo} onClick={()=>void run('undo',true)}>撤销</button>
      <button disabled={busy || !edit?.can_redo} onClick={()=>void run('redo',true)}>重做</button>
      <button disabled={busy || (!edit?.modified && !edit?.conflicts.length)} onClick={()=>void run('restore',true)}>{edit?.conflicts.length ? '放弃待复核修改并恢复原始架构' : '恢复原始 AI 架构'}</button></div>
    <label>选择模块<select value={selected} onChange={e=>{setSelected(e.target.value);setMembers([]);}}>{draft.map(m=><option key={m.id} value={m.id}>{m.name}</option>)}</select></label>
    {current && <><label>模块名称<input maxLength={80} value={current.name} onChange={e=>update({name:e.target.value})}/></label>
      <label>职责说明<textarea maxLength={600} rows={3} value={current.summary} onChange={e=>update({summary:e.target.value})}/></label>
      <label>所属父模块<select value={current.parent_id || ''} onChange={e=>update({parent_id:e.target.value || null})}><option value="">项目顶层</option>{draft.filter(m=>m.id!==current.id && (!m.entity_ids.length || m.parent_id === current.id)).map(m=><option key={m.id} value={m.id}>{m.name}</option>)}</select></label>
      {current.entity_ids.length>1 && <details><summary>拆分这个模块</summary><p>选择第一组函数，未选函数归入第二组。</p><div className="member-list">{current.entity_ids.map(id=><label key={id}><input type="checkbox" checked={members.includes(id)} onChange={e=>setMembers(e.target.checked ? [...members,id] : members.filter(n=>n!==id))}/>{project.analysis.entities.find(e=>e.id===id)?.qualified_name}</label>)}</div>
        <label>第一组职责名称<input value={splitName} maxLength={80} onChange={e=>setSplitName(e.target.value)}/></label><label>第二组职责名称<input value={remainingName} maxLength={80} onChange={e=>setRemainingName(e.target.value)}/></label>
        <button disabled={!members.length || members.length===current.entity_ids.length || !splitName.trim() || !remainingName.trim()} onClick={split}>加入拆分草稿</button></details>}
    </>}
    <details><summary>合并同级模块</summary><div className="member-list">{leafs.map(m=><label key={m.id}><input type="checkbox" checked={mergeIds.includes(m.id)} onChange={e=>setMergeIds(e.target.checked ? [...mergeIds,m.id] : mergeIds.filter(id=>id!==m.id))}/>{m.name}</label>)}</div>
      <label>合并后职责名称<input maxLength={80} value={mergeName} onChange={e=>setMergeName(e.target.value)}/></label><button disabled={mergeIds.length<2 || !mergeName.trim()} onClick={merge}>加入合并草稿</button></details>
    {error && <p className="inline-error" role="alert">{error}</p>}{preview && <div className="tool-success" role="status">预览校验通过：{preview.module_count} 个模块、{preview.function_count} 个函数；所有函数归属完整，保存后依赖按源码重新聚合。</div>}
    <div className="tool-actions"><button disabled={busy} onClick={()=>void run('apply')}>预览修改</button><button className="primary" disabled={busy || !preview} onClick={()=>void run('apply',true)}>保存架构调整</button></div>
  </div>;
}
