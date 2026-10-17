import {useEffect, useRef, useState, type ReactNode} from 'react';
import {BookOpen, GitCompareArrows, History, NotebookPen, Search, SlidersHorizontal, X} from 'lucide-react';
import {api} from './api';
import {ArchitectureEditor} from './ArchitectureEditor';
import {NotesList} from './Notes';
import type {HistoryVersion, Project, ProjectDetail, SearchResult} from './types';

export function openComparison(left:string,right:string,leftVersion='',rightVersion='') {
  const params=new URLSearchParams({compare:'1',left,right});
  if(leftVersion)params.set('left_version',leftVersion);if(rightVersion)params.set('right_version',rightVersion);
  location.assign(`/?${params}`);
}

export function ToolDialog({title,onClose,children}: {title:string;onClose:()=>void;children:ReactNode}) {
  const ref=useRef<HTMLDivElement>(null), close=useRef(onClose);close.current=onClose;
  useEffect(()=> {
    const previous=document.activeElement;
    const items=()=>[...ref.current!.querySelectorAll<HTMLElement>('button:not(:disabled),input:not(:disabled),textarea:not(:disabled),select:not(:disabled),[tabindex="0"]')].filter(e=>e.getClientRects().length);
    const frame=requestAnimationFrame(()=>{const preferred=ref.current?.querySelector<HTMLElement>('[data-autofocus]');(preferred || items()[0])?.focus();});
    const key=(e:KeyboardEvent)=> {
      if(e.key==='Escape'){e.preventDefault();e.stopImmediatePropagation();close.current();}
      if(e.key!=='Tab')return;
      const all=items(),first=all[0],last=all.at(-1);if(!first){e.preventDefault();return;}
      if(e.shiftKey && (document.activeElement===first || !ref.current!.contains(document.activeElement))){e.preventDefault();last?.focus();}
      else if(!e.shiftKey && (document.activeElement===last || !ref.current!.contains(document.activeElement))){e.preventDefault();first.focus();}
    };
    document.addEventListener('keydown',key,true);
    return ()=>{cancelAnimationFrame(frame);document.removeEventListener('keydown',key,true);if(previous instanceof HTMLElement && previous.isConnected)previous.focus();};
  },[]);
  return <div className="tool-backdrop"><div className="tool-dialog" role="dialog" aria-modal="true" aria-label={title} ref={ref}>
    <header><div><span className="tool-eyebrow">学习工作区</span><h2>{title}</h2></div><button className="icon-button" aria-label={`关闭${title}`} onClick={onClose}><X size={20}/></button></header>
    <div className="tool-body">{children}</div></div></div>;
}

export function LearningTools({project,projects,onLocate,onRefresh,embedded=false}: {project:ProjectDetail;projects:Project[];
  onLocate:(target:SearchResult)=>void;onRefresh:()=>void;embedded?:boolean}) {
  const [tool,setTool]=useState<'search'|'guide'|'notes'|'edit'|'history'|'compare'|null>(null);
  const [query,setQuery]=useState(''),[offset,setOffset]=useState(0);
  const [results,setResults]=useState<{items:SearchResult[];total:number}>({items:[],total:0});
  const [guide,setGuide]=useState<{items:SearchResult[];message:string}>({items:[],message:''});
  const [error,setError]=useState(''),[loading,setLoading]=useState(false),[other,setOther]=useState('');
  const [change,setChange]=useState<{status:string;message:string}|null>(null);
  const base=`/projects/${project.id}/versions/${project.version_id}`;
  const selected=useRef(tool);selected.current=tool;
  useEffect(()=> {
    const key=(e:KeyboardEvent)=> {if((e.ctrlKey || e.metaKey) && e.key.toLowerCase()==='k' && !document.querySelector('[role="dialog"]')){e.preventDefault();setTool('search');}};
    window.addEventListener('keydown',key);return ()=>window.removeEventListener('keydown',key);
  },[]);
  useEffect(()=> {
    if(tool!=='search')return;let current=true;setLoading(true);setError('');
    const timer=setTimeout(()=>api<{items:SearchResult[];total:number}>(`${base}/search?q=${encodeURIComponent(query)}&offset=${offset}`)
      .then(r=>{if(current)setResults(r);}).catch(e=>{if(current)setError(e.message);}).finally(()=>{if(current)setLoading(false);}),200);
    return ()=>{current=false;clearTimeout(timer);};
  },[tool,query,offset,base]);
  useEffect(()=> {
    if(tool!=='guide')return;setError('');setLoading(true);
    api<{items:SearchResult[];message:string}>(base+'/guide').then(setGuide).catch(e=>setError(e.message)).finally(()=>setLoading(false));
  },[tool,base]);
  useEffect(()=> {
    if(project.readonly)return;
    let current=true;
    const check=()=>{if(document.visibilityState==='visible')void api<{status:string;message:string}>(`/projects/${project.id}/changes`).then(value=>{if(current)setChange(value);}).catch(()=>{});};
    check();const timer=setInterval(check,60000);document.addEventListener('visibilitychange',check);
    return ()=>{current=false;clearInterval(timer);document.removeEventListener('visibilitychange',check);};
  },[project.id,project.version_id,project.readonly]);
  const locate=(item:SearchResult)=> {if(item.kind==='note' && item.status==='unlinked'){setTool('notes');return;}setTool(null);onLocate(item);};
  const titles={search:'搜索与快速定位',guide:'建议从哪里开始看',notes:'我的笔记',edit:'调整项目架构',history:'历史版本与变化',compare:'双项目对比学习'};
  return <>
    <div className="learning-tools" aria-label="学习辅助工具"><button onClick={()=>setTool('search')}><Search size={15}/>搜索<span className="shortcut-hint">Ctrl K</span></button>
      <button disabled={!project.architecture} onClick={()=>setTool('guide')}><BookOpen size={15}/>阅读指引</button>
      <button onClick={()=>setTool('notes')}><NotebookPen size={15}/>笔记</button>
      <button disabled={!project.architecture || !!project.readonly} onClick={()=>setTool('edit')}><SlidersHorizontal size={15}/>调整架构{project.manual_edit?.modified && <span className="manual-badge">已调整</span>}</button>
      <button onClick={()=>setTool('history')}><History size={15}/>版本与变化</button>
      {!embedded && <button disabled={!project.architecture} onClick={()=>setTool('compare')}><GitCompareArrows size={15}/>对比学习</button>}
    </div>
    {project.readonly && <div className="workspace-update">正在阅读历史快照 · {project.version_id.slice(0,8)} · 学习状态与架构只读 <button onClick={()=>location.assign(`/?project=${project.id}${embedded ? '&pane=1&slot='+new URLSearchParams(location.search).get('slot') : ''}`)}>返回当前版本</button></div>}
    {change && change.status!=='unchanged' && <div className={`workspace-update ${change.status==='changed' ? 'has-change' : ''}`}><span>{change.message}</span>
      {change.status==='manual' && <button onClick={()=>void api<{status:string;message:string}>(`/projects/${project.id}/changes?remote=true`).then(setChange).catch(e=>setChange({status:'error',message:e.message}))}>检查远程更新</button>}</div>}
    {project.manual_edit?.conflicts.length ? <div className="workspace-update has-change">人工修改有待复核项，原版本仍保留。<button onClick={()=>setTool('edit')}>查看并调整</button></div> : null}
    {tool && <ToolDialog title={titles[tool]} onClose={()=>setTool(null)}>
      {error && <p className="inline-error" role="alert">{error}</p>}
      {tool==='search' && <><input data-autofocus aria-label="项目内搜索" className="search-input" placeholder="搜索模块、函数、路径、讲解或笔记…" value={query} maxLength={200} onChange={e=>{setQuery(e.target.value);setOffset(0);}}/>
        <p className="tool-muted">在当前快照与已有内容中查找，不请求模型。</p>{loading ? <p role="status">正在搜索…</p> : <><p>{query ? `${results.total} 项匹配` : '输入关键词开始搜索'}</p>
          <div className="search-results">{results.items.map(item=><button key={`${item.kind}:${item.id}`} onClick={()=>locate(item)}><span className="tool-eyebrow">{{module:'模块',function:'函数',file:'文件',note:'笔记',overview:'概览'}[item.kind]}</span><strong>{item.title}</strong><small>{item.location?.path}</small><p>{item.text}</p></button>)}</div>
          <div className="tool-row"><button disabled={!offset} onClick={()=>setOffset(Math.max(0,offset-20))}>上一页</button><span>{offset+Math.min(1,results.total)}–{Math.min(offset+20,results.total)} / {results.total}</span><button disabled={offset+20>=results.total} onClick={()=>setOffset(offset+20)}>下一页</button></div></>}
      </>}
      {tool==='guide' && <><p className="tool-muted">{guide.message}</p>{loading ? <p>正在整理阅读建议…</p> : <ol className="guide-list">{guide.items.map((item,i)=><li key={item.id || i}><button onClick={()=>locate(item)}><strong>{item.title}</strong><p>{item.reason}</p><small>{item.location ? `${item.location.path}:${item.location.start_line}` : '项目全局视角'}</small></button></li>)}</ol>}</>}
      {tool==='notes' && <NotesList project={project} onLocate={(kind,id)=>locate({kind,id,title:''})}/>}
      {tool==='edit' && <ArchitectureEditor key={`${project.version_id}:${project.manual_edit?.revision}`} project={project} onSaved={()=>{setTool(null);onRefresh();}}/>}
      {tool==='history' && <HistoryTools project={project}/>}
      {tool==='compare' && <><p>两侧可以独立深入架构、阅读源码和保存学习进度。</p><label>右侧项目<select data-autofocus value={other} onChange={e=>setOther(e.target.value)}><option value="">选择另一个已完成分析的项目</option>{projects.filter(p=>p.id!==project.id && p.has_architecture).map(p=><option key={p.id} value={p.id}>{p.name}</option>)}</select></label><button className="primary" disabled={!other} onClick={()=>openComparison(project.id,other)}>开始并排学习</button></>}
    </ToolDialog>}
  </>;
}

function HistoryTools({project}: {project:ProjectDetail}) {
  const [versions,setVersions]=useState<HistoryVersion[]>([]),[error,setError]=useState('');
  const [before,setBefore]=useState(''),[after,setAfter]=useState(project.version_id);
  const [difference,setDifference]=useState<unknown>(null),[deleting,setDeleting]=useState<string|null>(null);
  const refresh=()=>api<HistoryVersion[]>(`/projects/${project.id}/versions`).then(items=>{setVersions(items);setBefore(current=>current || items.find(v=>v.id!==project.version_id)?.id || '');}).catch(e=>setError(e.message));
  useEffect(()=>{void refresh();},[project.id,project.version_id]);
  const pin=async(version:HistoryVersion)=> {try{await api(`/projects/${project.id}/versions/${version.id}/pin`,{pinned:!version.pinned,expected_revision:version.revision},'PUT');await refresh();}catch(e){setError((e as Error).message);}};
  const remove=async(version:HistoryVersion)=> {try{await api(`/projects/${project.id}/versions/${version.id}?expected_revision=${version.revision}`,undefined,'DELETE');setDeleting(null);await refresh();}catch(e){setError((e as Error).message);await refresh();}};
  const view=(id:string)=> {const params=new URLSearchParams(location.search);params.set('project',project.id);params.set('version',id);location.assign('/?'+params);};
  const diff=async()=>{try{setDifference(await api(`/projects/${project.id}/compare?before=${before}&after=${after}`));setError('');}catch(e){setError((e as Error).message);}};
  const name=(version:HistoryVersion)=>`${version.current ? '当前' : new Date(version.created_at).toLocaleString('zh-CN')} · ${version.id.slice(0,8)}`;
  return <div className="history-tools"><p className="tool-muted">最近五个成功版本（含当前），固定及含待复核修改的版本额外保留。历史快照只读，清理不删除笔记正文。空间按源码与版本记录估算，不含数据库页开销；共享数据可能重复计入。</p>
    {error && <p className="inline-error" role="alert">{error}</p>}
    <div className="history-list">{versions.map(version=><article key={version.id}><div><strong>{name(version)}</strong><p>{version.file_count} 个文件 · 约 {(version.bytes/1024/1024).toFixed(2)} MB{version.manual ? ' · 有人工调整记录' : ''}{version.pinned ? ' · 已固定' : ''}</p></div><div className="tool-row">
      <button disabled={!version.available} onClick={()=>view(version.id)}>查看</button><button onClick={()=>void pin(version)}>{version.pinned ? '取消固定' : '固定'}</button>
      {version.protected_reason && <span className="tool-muted">{version.protected_reason}</span>}{!version.current && <button disabled={version.pinned || !!version.protected_reason} onClick={()=>setDeleting(version.id)}>删除</button>}</div>
      {deleting===version.id && <div className="delete-history-confirm"><p>删除这版分析及其历史源码快照？笔记正文保留，原始项目不受影响。</p><button onClick={()=>void remove(version)}>确认删除历史版本</button><button onClick={()=>setDeleting(null)}>取消</button></div>}
    </article>)}</div>
    <h3>比较前后变化</h3><div className="tool-row"><label>之前<select value={before} onChange={e=>{setBefore(e.target.value);setDifference(null);}}><option value="">选择版本</option>{versions.map(v=><option key={v.id} value={v.id}>{name(v)}</option>)}</select></label>
      <label>之后<select value={after} onChange={e=>{setAfter(e.target.value);setDifference(null);}}>{versions.map(v=><option key={v.id} value={v.id}>{name(v)}</option>)}</select></label></div>
    <div className="tool-row"><button disabled={!before || before===after} onClick={()=>void diff()}>查看变化摘要</button><button disabled={!before || before===after} onClick={()=>openComparison(project.id,project.id,before,after)}>并排阅读这两版</button></div>
    {difference !== null && <Difference value={difference}/>}
  </div>;
}

function Difference({value}: {value:unknown}) {
  const result=value as {functions:{added:{name:string}[];removed:{name:string}[];changed:{after:{name:string}}[];ambiguous:{name:string}[]};modules:{added_names:string[];removed_names:string[];membership_changes:{name:string;before:string;after:string}[]};dependencies:{from:string;to:string;before:number;after:number}[];manual:{before:boolean;after:boolean};limitations:string[]};
  const groups=[['新增函数',result.functions.added.map(e=>e.name)],['删除函数',result.functions.removed.map(e=>e.name)],['实现变化',result.functions.changed.map(e=>e.after.name)],['身份有歧义',result.functions.ambiguous.map(e=>e.name)],['新增模块名称',result.modules.added_names],['移除模块名称',result.modules.removed_names],['归属变化',result.modules.membership_changes.map(e=>`${e.name}：${e.before} → ${e.after}`)],['确认调用变化',result.dependencies.map(e=>`${e.from} → ${e.to}：${e.before} → ${e.after} 处`)]] as [string,string[]][];
  return <div className="difference"><p>{result.manual.before || result.manual.after ? '此对比包含人工架构调整，模块变化不全由源码变化导致。' : '按源码身份与已确认关系比较。'}</p>{groups.map(([title,items])=><details key={title} open={items.length>0}><summary>{title} · {items.length}</summary>{items.length ? <ul>{items.map((item,i)=><li key={i}>{item}</li>)}</ul> : <p className="tool-muted">未发现</p>}</details>)}{result.limitations.map(text=><p className="tool-muted" key={text}>{text}</p>)}</div>;
}
