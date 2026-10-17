import {useEffect, useState} from 'react';
import {api, bootstrap} from './api';
import type {Project} from './types';

export function CompareWorkspace() {
  const params=new URLSearchParams(location.search);
  const [projects,setProjects]=useState<Project[]>([]),[ready,setReady]=useState(false),[error,setError]=useState('');
  const [left,setLeft]=useState(params.get('left') || ''),[right,setRight]=useState(params.get('right') || '');
  const [leftVersion,setLeftVersion]=useState(params.get('left_version') || ''),[rightVersion,setRightVersion]=useState(params.get('right_version') || '');
  const [ratio,setRatio]=useState(50),[mobile,setMobile]=useState<'left'|'right'>('left');
  const widthKey=`codescape-compare-width:${left}:${right}`;
  useEffect(()=> {const saved=Number(sessionStorage.getItem(widthKey));setRatio(saved>=30 && saved<=70 ? saved : 50);},[widthKey]);
  useEffect(()=>{bootstrap().then(()=>api<Project[]>('/projects')).then(items=>{setProjects(items.filter(p=>p.has_architecture));setReady(true);}).catch(e=>setError(e.message));},[]);
  const source=(id:string,version:string,slot:string)=>'/?'+new URLSearchParams({pane:'1',project:id,slot,...(version ? {version} : {})});
  return <div className="compare-shell"><header className="compare-header"><button onClick={()=>location.assign('/?project='+left)}>← 返回单项目</button><strong>对比学习</strong><span className="tool-muted">两侧独立探索 · 不自动推定模块对应关系</span></header>
    {error && <p className="inline-error">{error}</p>}
    <div className="compare-controls"><label>左侧<select aria-label="左侧项目" value={left} onChange={e=>{setLeft(e.target.value);setLeftVersion('');}}><option value="">选择项目</option>{projects.map(p=><option key={p.id} value={p.id}>{p.name}</option>)}</select>{leftVersion && <small>历史快照 {leftVersion.slice(0,8)}</small>}</label>
      <label className="compare-ratio">区域宽度<input aria-label="对比区域宽度" type="range" min={30} max={70} value={ratio} onChange={e=>{setRatio(Number(e.target.value));sessionStorage.setItem(widthKey,e.target.value);}}/></label>
      <label>右侧<select aria-label="右侧项目" value={right} onChange={e=>{setRight(e.target.value);setRightVersion('');}}><option value="">选择项目</option>{projects.map(p=><option key={p.id} value={p.id}>{p.name}</option>)}</select>{rightVersion && <small>历史快照 {rightVersion.slice(0,8)}</small>}</label></div>
    <div className="compare-mobile-tabs" role="tablist" aria-label="选择对比侧"><button role="tab" aria-selected={mobile==='left'} onClick={()=>setMobile('left')}>左侧 · {projects.find(p=>p.id===left)?.name}</button><button role="tab" aria-selected={mobile==='right'} onClick={()=>setMobile('right')}>右侧 · {projects.find(p=>p.id===right)?.name}</button></div>
    {ready && <div className="compare-panes" style={{gridTemplateColumns:`minmax(0,${ratio}fr) minmax(0,${100-ratio}fr)`}}>
      <section className={`compare-pane ${mobile==='left' ? 'mobile-active' : ''}`} aria-label="左侧学习工作区">{left ? <iframe key={`${left}:${leftVersion}`} title="左侧学习工作区" src={source(left,leftVersion,'compare-left')} allowFullScreen/> : <p>请选择左侧项目</p>}</section>
      <section className={`compare-pane ${mobile==='right' ? 'mobile-active' : ''}`} aria-label="右侧学习工作区">{right ? <iframe key={`${right}:${rightVersion}`} title="右侧学习工作区" src={source(right,rightVersion,'compare-right')} allowFullScreen/> : <p>请选择右侧项目</p>}</section>
    </div>}
  </div>;
}
