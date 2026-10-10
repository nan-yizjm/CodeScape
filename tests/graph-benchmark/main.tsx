import React, {useCallback, useMemo, useRef, useState} from 'react';
import {createRoot} from 'react-dom/client';
import {Canvas} from '../../apps/web/src/Canvas';
import {exportSvg} from '../../apps/web/src/graph';
import type {Projection, ProjectionNode} from '../../apps/web/src/types';
import '@xyflow/react/dist/style.css';
import '../../apps/web/src/style.css';

const tasks: number[] = [];
declare const __BASELINE__: boolean;
new PerformanceObserver(list => tasks.push(...list.getEntries().map(entry => entry.duration))).observe({type:'longtask', buffered:true});
function fixture(count: number, dense: boolean): Projection {
  const nodes: ProjectionNode[] = [{id:'parent', label:'人工预置图规模验证', description:'仅验证 CodeScape 布局和导出，不代表 AI 分析结果。',
    kind:'module', count, state:'not_started', incomplete:false, external:false, container:true, expanded:true, x:0,y:0,width:364,height:204}];
  for (let index=0;index<count;index++) nodes.push({id:`function-${index}`,
    label:index % 31 === 0 ? `长中文函数名称用于验证布局导出与完整名称保留_${index}_function` : `function_${index}`,
    description:'',kind:'function',count:0,state:'not_started',incomplete:false,external:false,parentId:'parent',x:0,y:0,width:300,height:126});
  const pairs = new Map<string,{from:string;to:string}>();
  const add=(from:number,to:number)=>{if(to<count)pairs.set(`${from}:${to}`,{from:`function-${from}`,to:`function-${to}`});};
  for(let i=0;i<count;i++) {
    for(let offset=1;offset<=(dense?5:1);offset++) add(i,i+offset);
    if(i%50===0)add(i,i);
    if(i%20===0&&i+1<count)add(i+1,i);
  }
  return {nodes, relations:[...pairs.entries()].map(([id,pair])=>({id,...pair,label:'人工确认关系夹具',calls:[]}))};
}
function download(name:string,content:string,type:string) {
  const url=URL.createObjectURL(new Blob([content],{type})), link=document.createElement('a');
  link.href=url;link.download=name;link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
}
function App() {
  const [selected,setSelected]=useState('50-sparse'),[generation,setGeneration]=useState(0),[positioned,setPositioned]=useState<Projection|null>(null);
  const [report,setReport]=useState<Record<string,unknown>>({}),[clicked,setClicked]=useState(''),[viewport,setViewport]=useState({x:0,y:0,zoom:1});
  const began=useRef(performance.now()), interact=useRef(0), taskStart=useRef(0);
  const projection=useMemo(()=>fixture(Number(selected.split('-')[0]),selected.endsWith('dense')),[selected,generation]);
  const laidOut=useCallback((value:Projection)=>{
    const layoutMs=performance.now()-began.current;setPositioned(value);
    const waitForEdges=()=>{
      const drawn=document.querySelectorAll('.react-flow__edge').length;
      const ready=value.nodes.length>200 ? drawn>0 : drawn===value.relations.length;
      if(!ready&&performance.now()-began.current<35000){requestAnimationFrame(waitForEdges);return;}
      const longTasks=tasks.slice(taskStart.current);
      setReport({scenario:selected, expected_functions:Number(selected.split('-')[0]),nodes:value.nodes.length,edges:value.relations.length,
        layout_ms:Math.round(layoutMs),first_paint_ms:Math.round(performance.now()-began.current),
        rendered_nodes:document.querySelectorAll('.react-flow__node').length, rendered_edges:document.querySelectorAll('.react-flow__edge').length,
        render_mode:!__BASELINE__&&value.nodes.length>200?'仅挂载视口元素；数据与导出保留全量':'全量挂载',
        baseline:__BASELINE__,
        max_main_thread_task_ms:Math.round(Math.max(0,...longTasks)), long_task_count:longTasks.length,
        fallback:document.querySelector('.layout-warning')?.textContent||null,model_requests:0,
        boundary:'人工投影夹具；不代表真实模型判断质量。'});
    };
    requestAnimationFrame(waitForEdges);
  },[selected,generation]);
  const run=(value:string)=>{began.current=performance.now();taskStart.current=tasks.length;setPositioned(null);setReport({});setClicked('');setSelected(value);setGeneration(value=>value+1);};
  const open=(node:ProjectionNode)=>{interact.current=performance.now();setClicked(node.label);requestAnimationFrame(()=>setReport(value=>({...value,click_paint_ms:Math.round(performance.now()-interact.current)})));};
  return <div style={{height:'100vh',display:'grid',gridTemplateRows:'110px 1fr 170px'}}>
    <header style={{padding:16,display:'flex',alignItems:'center',gap:12,flexWrap:'wrap',background:'#fff'}}><strong>人工图规模验证 · 零模型请求</strong>
      <select aria-label="规模场景" value={selected} onChange={event=>run(event.target.value)}>{[50,200,1000].flatMap(count=>['sparse','dense'].map(mode=><option key={`${count}-${mode}`} value={`${count}-${mode}`}>{count} 个函数 · {mode==='sparse'?'稀疏':'密集'} · 自环和双向</option>))}</select>
      <button onClick={()=>run(selected)}>重新测量</button><button disabled={!positioned} onClick={()=>{
        const start=performance.now(),svg=exportSvg(positioned!,selected);setReport(value=>({...value,svg_ms:Math.round(performance.now()-start),svg_bytes:new Blob([svg]).size}));
        download(`人工图-${selected}.svg`,svg,'image/svg+xml');}}>导出全量 SVG</button>
      <button onClick={()=>download(`图规模-${selected}.json`,JSON.stringify({...report,viewport,clicked},null,2),'application/json')}>下载测量记录</button></header>
    <div className="graph-container" style={{minHeight:0}}><Canvas key={generation} projection={projection} onNode={open} onInfo={open} onRelation={()=>setClicked('关系证据夹具')} onLayout={laidOut} onViewport={setViewport} dependencyMode="all" onDependencyMode={()=>{}} /></div>
    <footer style={{padding:'10px 20px',overflow:'auto',background:'#fff'}}><output aria-label="测量记录">{JSON.stringify({...report,viewport,clicked})}</output></footer>
  </div>;
}
createRoot(document.getElementById('root')!).render(<App/>);
