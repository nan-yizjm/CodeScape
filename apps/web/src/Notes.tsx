import {useCallback, useEffect, useRef, useState} from 'react';
import {api, ApiError, keepalive} from './api';
import type {LearningNote, ProjectDetail} from './types';

export function NoteEditor({project, kind, anchor}: {project:ProjectDetail;kind:'module'|'function';anchor:string}) {
  const [text,setText] = useState('');
  const [loaded,setLoaded] = useState(false);
  const [status,setStatus] = useState('正在读取笔记…');
  const [blocked,setBlocked] = useState(false);
  const [epoch,setEpoch] = useState(0);
  const note = useRef<LearningNote | null>(null);
  const draft = useRef(''), saved = useRef(''), busy = useRef(false), active = useRef(true);
  const key = `codescape-note-draft:${project.id}:${project.version_id}:${kind}:${anchor}`;
  const payload = useCallback((value:string) => ({id:note.current?.id, anchor_version:project.version_id,
    anchor_kind:kind, anchor_id:anchor, text:value, expected_revision:note.current?.revision || 0}),[project.id,project.version_id,kind,anchor]);
  useEffect(()=> {
    active.current = true;
    api<LearningNote[]>(`/projects/${project.id}/notes?version_id=${project.version_id}`).then(notes=> {
      if (!active.current) return;
      const existing = notes.find(n=>n.anchor_version === project.version_id && n.anchor_kind === kind && n.anchor_id === anchor && n.status === 'linked');
      note.current = existing || {id:crypto.randomUUID(),anchor_version:project.version_id,anchor_kind:kind,anchor_id:anchor,text:'',revision:0,status:'linked'};
      saved.current = existing?.text || '';
      const local = sessionStorage.getItem(key);
      draft.current = local ?? saved.current; setText(draft.current); setLoaded(true);
      setStatus(local !== null && local !== saved.current ? '已恢复未保存草稿' : existing?.implementation_changed ? '实现已变化，请复核笔记' : '笔记已保存');
    }).catch(error=> {if(active.current) {setStatus(error.message);setBlocked(true);}});
    return ()=> {active.current = false;};
  },[project.id,project.version_id,kind,anchor,key]);
  const save = useCallback(async () => {
    if (busy.current || !note.current || draft.current === saved.current || project.readonly) return;
    busy.current = true; const value = draft.current; setStatus('正在保存…');
    try {
      const result = await api<LearningNote>(`/projects/${project.id}/notes`,payload(value),'PUT');
      note.current = result; saved.current = value;
      if (draft.current === value) sessionStorage.removeItem(key);
      if(active.current) {setBlocked(false);setStatus(draft.current === value ? '笔记已保存' : '有修改待保存');}
    } catch(error) {
      if(active.current) {setBlocked(true);setStatus(error instanceof ApiError && error.code === 'WORKSPACE_CONFLICT'
        ? '笔记被另一页面更新。草稿已保留，请确认后重试。' : `保存失败，草稿已保留：${(error as Error).message}`);}
    } finally {busy.current = false;if(active.current)setEpoch(n=>n+1);}
  },[project.id,project.readonly,payload,key]);
  useEffect(()=> {
    if (!loaded || blocked || text === saved.current) return;
    const timer=setTimeout(()=>void save(),600);
    return ()=>clearTimeout(timer);
  },[text,loaded,blocked,epoch,save]);
  useEffect(()=> {
    const leave=()=> {
      if (note.current && !busy.current && !blocked && draft.current !== saved.current && !project.readonly)
        void keepalive(`/projects/${project.id}/notes`,payload(draft.current),'PUT').catch(()=>{});
    };
    window.addEventListener('pagehide',leave);
    return ()=> {leave();window.removeEventListener('pagehide',leave);};
  },[project.id,project.readonly,payload,blocked]);
  const retry=async()=> {
    try {
      const notes=await api<LearningNote[]>(`/projects/${project.id}/notes`);
      const current=notes.find(n=>n.id === note.current?.id) || notes.find(n=>n.anchor_version===project.version_id && n.anchor_kind===kind && n.anchor_id===anchor && n.status==='linked');
      if(!note.current) {
        const existing=notes.find(n=>n.anchor_version === project.version_id && n.anchor_kind === kind && n.anchor_id === anchor && n.status === 'linked');
        note.current=existing || {id:crypto.randomUUID(),anchor_version:project.version_id,anchor_kind:kind,anchor_id:anchor,text:'',revision:0,status:'linked'};
        saved.current=existing?.text || '';draft.current=sessionStorage.getItem(key) ?? saved.current;
        setText(draft.current);setLoaded(true);
      } else if(current) {
        if(current.text && draft.current!==current.text && !current.text.includes(draft.current)) {
          draft.current=[current.text,draft.current].filter(Boolean).join('\n\n');setText(draft.current);sessionStorage.setItem(key,draft.current);
        }
        note.current=current;saved.current=current.text;
      }
      setBlocked(false);await save();if(draft.current===saved.current)setStatus('笔记已保存');
    } catch(error) {setStatus((error as Error).message);}
  };
  return <section className="personal-note"><h3>我的学习笔记</h3><p className="tool-muted">纯文本，仅保存在本机。{project.readonly ? '历史版本只读。' : '停止输入后自动保存。'}</p>
    <textarea aria-label="学习笔记" maxLength={50000} rows={7} disabled={!loaded || !!project.readonly} value={text}
      onChange={event=> {draft.current=event.target.value;setText(draft.current);sessionStorage.setItem(key,draft.current);setStatus('有修改待保存');}}/>
    <div className="note-status" role="status">{status}</div>{blocked && !project.readonly && <button onClick={()=>void retry()}>合并最新内容并重试</button>}
  </section>;
}

export function NotesList({project,onLocate}: {project:ProjectDetail;onLocate:(kind:'module'|'function',id:string)=>void}) {
  const [notes,setNotes]=useState<LearningNote[]>([]), [error,setError]=useState('');
  const [targets,setTargets]=useState<Record<string,string>>({});
  const readDrafts=()=> {
    const prefix=`codescape-note-draft:${project.id}:`;
    const values:{key:string;text:string}[]=[];
    for(let index=0;index<sessionStorage.length;index++) {
      const key=sessionStorage.key(index);if(key?.startsWith(prefix)) {
        const text=sessionStorage.getItem(key);if(text)values.push({key,text});
      }
    }
    return values;
  };
  const [drafts,setDrafts]=useState(readDrafts),[discarding,setDiscarding]=useState<string|null>(null);
  const refresh=()=>api<LearningNote[]>(`/projects/${project.id}/notes`).then(setNotes).catch(e=>setError(e.message));
  useEffect(()=>{void refresh();},[project.id]);
  const choices=[...(project.architecture?.modules || []).map(m=>({value:`module:${m.id}`,label:`模块 · ${m.name}`})),
    ...project.analysis.entities.filter(e=>e.visible).map(e=>({value:`function:${e.id}`,label:`函数 · ${e.qualified_name}`}))];
  const relink=async(note:LearningNote)=> {
    const target=targets[note.id];if(!target)return;
    const index=target.indexOf(':');
    const existing=notes.find(n=>n.id!==note.id && n.status==='linked' && n.anchor_version===project.version_id && `${n.anchor_kind}:${n.anchor_id}`===target);
    try {
      await api(`/projects/${project.id}/notes`,{id:note.id,anchor_version:project.version_id,
        anchor_kind:target.slice(0,index),anchor_id:target.slice(index+1),text:note.text,expected_revision:note.revision,
        ...(existing ? {merge_target_id:existing.id,merge_target_revision:existing.revision} : {})},'PUT');
      await refresh();
    } catch(e) {setError((e as Error).message);}
  };
  const recover=async(key:string,text:string)=> {
    const target=targets[key];if(!target)return;
    const index=target.indexOf(':'),kind=target.slice(0,index),anchor=target.slice(index+1);
    const existing=notes.find(n=>n.anchor_version===project.version_id && n.anchor_kind===kind && n.anchor_id===anchor && n.status==='linked');
    const combined=existing?.text && !existing.text.includes(text) ? existing.text+'\n\n'+text : existing?.text || text;
    if(combined.length>50000){setError('合并后的笔记超过50000字，请先在详情中整理现有笔记。草稿仍保留。');return;}
    try {
      await api(`/projects/${project.id}/notes`,{id:existing?.id || crypto.randomUUID(),anchor_version:project.version_id,
        anchor_kind:kind,anchor_id:anchor,text:combined,expected_revision:existing?.revision || 0},'PUT');
      sessionStorage.removeItem(key);setDrafts(readDrafts());await refresh();
    } catch(e){setError((e as Error).message);}
  };
  return <div className="tool-list"><p>点击模块或函数详情可以编写笔记。未能匹配的旧笔记保留在这里。</p>
    {error && <p className="inline-error" role="alert">{error}</p>}{notes.filter(n=>n.text).length === 0 && <p className="tool-muted">还没有笔记。</p>}
    {!!drafts.length && <section><h3>浏览器保留的未确认草稿</h3><p className="tool-muted">保存失败或版本切换前的文字仍在这里；保存时与目标现有笔记合并。浏览器关闭前请处理。</p>
      {drafts.map(draft=><article className="note-card" key={draft.key}><p className="note-text">{draft.text}</p>{!project.readonly && <><div className="tool-row"><select aria-label="草稿保存目标" value={targets[draft.key] || ''} onChange={e=>setTargets({...targets,[draft.key]:e.target.value})}><option value="">选择当前版本中的目标</option>{choices.map(c=><option key={c.value} value={c.value}>{c.label}</option>)}</select><button disabled={!targets[draft.key]} onClick={()=>void recover(draft.key,draft.text)}>合并并保存草稿</button></div>
        {discarding===draft.key ? <div className="tool-row"><span>确认放弃这份浏览器草稿？服务器笔记不受影响。</span><button onClick={()=>{sessionStorage.removeItem(draft.key);setDrafts(readDrafts());setDiscarding(null);}}>确认放弃草稿</button><button onClick={()=>setDiscarding(null)}>取消</button></div> : <button onClick={()=>setDiscarding(draft.key)}>放弃这份草稿</button>}</>}
      </article>)}</section>}
    {notes.filter(n=>n.text).map(note=><article key={note.id} className="note-card"><span className="tool-muted">{note.status === 'unlinked' ? '待重新关联' : note.anchor_version === project.version_id ? '当前版本' : '历史版本'}{note.implementation_changed ? ' · 实现已变化' : ''}</span>
      <p className="note-text">{note.text}</p>{note.status === 'linked' && note.anchor_version === project.version_id
        ? <button onClick={()=>onLocate(note.anchor_kind,note.anchor_id)}>定位并编辑</button>
        : !project.readonly && <div className="tool-row"><select aria-label="笔记新关联目标" value={targets[note.id] || ''} onChange={e=>setTargets({...targets,[note.id]:e.target.value})}>
          <option value="">选择当前版本中的目标</option>{choices.map(c=><option key={c.value} value={c.value}>{c.label}</option>)}</select><button disabled={!targets[note.id]} onClick={()=>void relink(note)}>{notes.some(n=>n.id!==note.id && n.status==='linked' && n.anchor_version===project.version_id && `${n.anchor_kind}:${n.anchor_id}`===targets[note.id]) ? '合并正文并重新关联' : '重新关联'}</button></div>}
    </article>)}
  </div>;
}
