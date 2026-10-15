import { useEffect, useMemo, useRef, useState, type CSSProperties } from 'react';
import { ReactFlow, ReactFlowProvider, Background, Controls, Handle, Position, BaseEdge, EdgeLabelRenderer, MarkerType, Panel, useReactFlow,
  type Node, type NodeProps, type EdgeProps, type Viewport } from '@xyflow/react';
import ELK, { type ElkNode } from 'elkjs/lib/elk-api';
import { Info, Check, Circle, ArrowUpRight, AlertTriangle, Boxes, Braces } from 'lucide-react';
import type { Point, Projection, ProjectionNode, Relation } from './types';
import { edgePath, graphTheme, nodePalette, relationLabelPoint } from './graph';
import { layoutCompound, layoutHierarchy, orthogonalOptions, readingViewport, verticalFallback } from './layout';

type Data = { node: ProjectionNode; onInfo: (node: ProjectionNode) => void; onOpen: (node: ProjectionNode) => void; onInternal: (node: ProjectionNode) => void; onHover: (id: string | null) => void; dimmed: boolean } & Record<string, unknown>;
const elk = new ELK({ workerFactory: () => new Worker(new URL('elkjs/lib/elk-worker.min.js', import.meta.url)) });

function MapNode({ data }: NodeProps<Node<Data>>) {
  const node = data.node;
  const Icon = node.kind === 'module' ? Boxes : Braces;
  const palette = nodePalette(node);
  const stateLabel = { not_started: '未开始', in_progress: '学习中', mastered: '已掌握', not_applicable: '不适用' }[node.state];
  return <div onMouseEnter={() => node.kind === 'module' && data.onHover(node.id)} onMouseLeave={() => data.onHover(null)}
    onFocusCapture={() => node.kind === 'module' && data.onHover(node.id)} onBlurCapture={event => {if (!event.currentTarget.contains(event.relatedTarget)) data.onHover(null);}}
    className={`map-node ${node.kind} ${data.dimmed ? 'is-muted' : ''} ${node.container ? 'module-container' : ''} ${node.expanded ? 'is-expanded' : ''} ${node.external && node.kind === 'module' ? 'external-node' : ''} state-${node.state}`} style={{ width: node.width, height: node.height, '--node-bg': palette.background, '--node-border': palette.border, '--node-accent': palette.accent } as CSSProperties}>
    <button className="node-hit-area nodrag nopan" aria-label={node.kind === 'module' ? (node.canExpand ? `${node.expanded ? '收起' : '展开'}模块 ${node.label}` : `进入模块 ${node.label}`) : `阅读 ${node.label} 源码`} aria-expanded={node.canExpand ? node.expanded : undefined}
      onClick={event => { event.stopPropagation(); data.onOpen(node); }} />
    <Handle type="target" position={Position.Top} />
    <div className="node-top"><span className="node-symbol"><Icon size={16} /></span><span className="node-kind">{node.kind === 'module' ? node.sibling ? '同级模块' : node.external ? '关联模块' : node.id.startsWith('__abstract__:') ? '功能分组' : '逻辑模块' : '函数'}</span>
      <button className="node-info nodrag nopan" aria-label={`查看 ${node.label} 详情`} onPointerDown={event => event.stopPropagation()} onClick={event => { event.stopPropagation(); event.nativeEvent.stopImmediatePropagation?.(); data.onInfo(node); }}><Info size={16} /></button></div>
    <div className="node-title" title={node.label}>{node.label}</div>
    {node.kind === 'module' && <p title={node.description}>{node.description}</p>}
    <div className="node-footer"><span className="node-count">{node.kind === 'module' ? node.count === 0 ? node.state === 'not_applicable' ? '暂无可统计内容' : '模块学习内容' : node.shownCount !== undefined && node.shownCount < node.count ? `显示 ${node.shownCount} / ${node.count} 个函数` : `${node.count} 个函数` : node.external ? <><ArrowUpRight size={12} /> 跨模块</> : '真实源码实现'}</span>{node.kind === 'module' && node.internalRelationCount ? <button className="node-dependency nodrag nopan" aria-label={`查看 ${node.label} 内部依赖`} onClick={event => { event.stopPropagation(); data.onInternal(node); }}>内部依赖 {node.internalRelationCount}</button> : null}
      <span className="node-state" title={stateLabel}>{node.incomplete && <AlertTriangle size={12} />}{node.state === 'mastered' ? <Check size={12} /> : node.state === 'in_progress' ? <span className="learning-dot" /> : <Circle size={10} />}<span>{stateLabel}</span></span></div>
    <span className="node-action" aria-hidden="true">{node.kind === 'module' ? node.canExpand ? (node.expanded ? '收起子模块' : '展开子模块') : node.count ? '进入模块' : '查看模块内容' : '阅读源码'}<ArrowUpRight size={13} /></span>
    <Handle type="source" position={Position.Bottom} />
  </div>;
}

function RoutedEdge(props: EdgeProps) {
  const from = props.data?.from as ProjectionNode | undefined, to = props.data?.to as ProjectionNode | undefined;
  if (!from || !to) return null;
  const internal = from.id === to.id;
  const points = props.data?.points as Point[] | undefined;
  const path = edgePath(from, to, points);
  const labelPoint = relationLabelPoint(points);
  const dimmed = !!props.data?.dimmed;
  return <><path d={path} fill="none" opacity={dimmed ? .12 : 1} stroke={graphTheme.background} strokeWidth={6} strokeLinecap="round" strokeLinejoin="round" pointerEvents="none" />
    <BaseEdge id={props.id} path={path} markerEnd={props.markerEnd} style={{ stroke: internal ? graphTheme.internalEdge : graphTheme.edge, strokeWidth: internal ? 1.2 : 1.5, strokeLinecap: 'round', strokeLinejoin: 'round', strokeDasharray: internal ? '4 4' : undefined, opacity: dimmed ? .12 : internal ? .72 : .9 }} interactionWidth={12} />
    {labelPoint && from.kind === 'module' && to.kind === 'module' && <EdgeLabelRenderer><button
      className="edge-evidence-label nodrag nopan" aria-label={`查看 ${from.label} → ${to.label} 调用证据`}
      style={{opacity: dimmed ? .2 : 1, transform: `translate(-50%, -50%) translate(${labelPoint.x}px, ${labelPoint.y}px)`}}
      onClick={event => {event.stopPropagation(); (props.data?.open as (() => void))();}}>{String(props.data?.label || '调用协作')}</button></EdgeLabelRenderer>}</>;
}

const nodeTypes = { map: MapNode };
const edgeTypes = { routed: RoutedEdge };
const fitOptions = { padding: { top: '56px', bottom: '44px', left: '32px', right: '32px' }, minZoom:.001, maxZoom: 1 } as const;

function ReadingScale({ nodes, initialReading }: { nodes: ProjectionNode[]; initialReading: boolean }) {
  const { setViewport, getViewport, viewportInitialized } = useReactFlow();
  const button = useRef<HTMLButtonElement>(null);
  const initialized = useRef(false);
  useEffect(() => {
    const frame=button.current?.closest('.react-flow')?.getBoundingClientRect();
    if(!viewportInitialized || !frame || initialized.current) return;
    initialized.current=true;
    const value=initialReading && readingViewport(nodes,frame.width);
    if(value) void setViewport(value,{duration:0});
  },[viewportInitialized,initialReading,nodes,setViewport]);
  useEffect(() => {
    const frame = button.current?.closest('.react-flow');
    if (!frame || !viewportInitialized || !nodes.length) return;
    let previous: { width: number; height: number } | undefined;
    let pending = 0;
    const observer = new ResizeObserver(([entry]) => {
      const { width, height } = entry.contentRect;
      const viewport = getViewport();
      const left = Math.min(...nodes.map(node => node.x)) * viewport.zoom + viewport.x;
      const right = Math.max(...nodes.map(node => node.x + node.width)) * viewport.zoom + viewport.x;
      const top = Math.min(...nodes.map(node => node.y)) * viewport.zoom + viewport.y;
      const bottom = Math.max(...nodes.map(node => node.y + node.height)) * viewport.zoom + viewport.y;
      const resized = previous && (Math.abs(width - previous.width) > 20 || Math.abs(height - previous.height) > 40);
      const outside = right < 0 || left > width || bottom < 0 || top > height;
      const before = previous;
      previous = { width, height };
      if (!width || !height || (!resized && !outside)) return;
      cancelAnimationFrame(pending);
      pending = requestAnimationFrame(() => {
        if(nodes.length>200) {
          const target=outside ? readingViewport(nodes,width) : before && {...viewport,x:viewport.x+(width-before.width)/2,y:viewport.y+(height-before.height)/2};
          if(target) void setViewport(target,{duration:0});
          return;
        }
        const minX = Math.min(...nodes.map(node => node.x)), maxX = Math.max(...nodes.map(node => node.x + node.width));
        const minY = Math.min(...nodes.map(node => node.y)), maxY = Math.max(...nodes.map(node => node.y + node.height));
        const zoom = Math.max(.001, Math.min(1, Math.max(1, width - 64) / (maxX - minX), Math.max(1, height - 100) / (maxY - minY)));
        void setViewport({ x: width / 2 - (minX + maxX) / 2 * zoom, y: height / 2 + 6 - (minY + maxY) / 2 * zoom, zoom },
          { duration: resized && !matchMedia('(prefers-reduced-motion: reduce)').matches ? 250 : 0 });
      });
    });
    observer.observe(frame);
    return () => { observer.disconnect(); cancelAnimationFrame(pending); };
  }, [viewportInitialized, nodes, setViewport, getViewport]);
  return <Panel position="top-right"><button ref={button} className="reading-scale" onClick={event => {
    const frame = event.currentTarget.closest('.react-flow')?.getBoundingClientRect();
    if (!frame || !nodes.length) return;
    const value=readingViewport(nodes,frame.width);
    if(!value) return;
    void setViewport(value,
      { duration: matchMedia('(prefers-reduced-motion: reduce)').matches ? 0 : 350 });
  }}>阅读尺寸<ArrowUpRight size={13} /></button></Panel>;
}

export function Canvas({ projection, onNode, onInfo, onRelation, onLayout, onViewport, viewport, dependencyMode, onDependencyMode }: {
  projection: Projection; onNode: (node: ProjectionNode) => void; onInfo: (node: ProjectionNode) => void;
  onRelation: (relation: Relation) => void; onLayout: (projection: Projection) => void;
  onViewport: (viewport: Viewport) => void; viewport?: Viewport;
  dependencyMode: 'primary' | 'all'; onDependencyMode: (mode: 'primary' | 'all') => void;
}) {
  const [layout, setLayout] = useState<Projection | null>(null);
  const [layoutStructure, setLayoutStructure] = useState('');
  const [warning, setWarning] = useState('');
  const [hovered, setHovered] = useState<string | null>(null);
  const preview = projection.nodes.find(node => node.id === hovered && node.kind === 'module');
  const neighbors = new Set<string>();
  if (preview) {
    neighbors.add(preview.id);
    for (const relation of projection.relations) if (relation.from === preview.id || relation.to === preview.id) {
      neighbors.add(relation.from); neighbors.add(relation.to);
    }
    // Keep surrounding containers legible, without treating ancestry as a dependency.
    for (const id of [...neighbors]) {
      let node = projection.nodes.find(item => item.id === id);
      while (node?.parentId) { neighbors.add(node.parentId); node = projection.nodes.find(item => item.id === node!.parentId); }
    }
  }
  const structure = useMemo(() => JSON.stringify(projection.nodes.map(node => [node.id, node.width, node.height, node.parentId, node.container])) + JSON.stringify(projection.relations.map(edge => edge.id)), [projection]);
  useEffect(() => {
    let active = true;
    setLayout(null);
    setWarning('');
    let timeout: ReturnType<typeof setTimeout>;
    const parents = new Map(projection.nodes.map(node => [node.id, node.parentId]));
    const crossContainer = projection.relations.some(edge => edge.from !== edge.to && parents.get(edge.from) !== parents.get(edge.to));
    const organized = crossContainer ? layoutCompound(projection, graph => elk.layout<ElkNode>(graph)) : layoutHierarchy(projection, async (boxes, edges) => {
      if (!boxes.length) return [];
      if (!edges.length) {
        const columns = Math.min(2, boxes.length);
        const columnWidth = Math.max(...boxes.map(box => box.width)) + 48;
        let y = 0;
        return boxes.map((box, index) => {
          if (index && index % columns === 0) y += Math.max(...boxes.slice(index - columns, index).map(item => item.height)) + 72;
          return { ...box, x: index % columns * columnWidth, y };
        });
      }
      const result = await elk.layout<ElkNode>({ id: 'root', layoutOptions: { ...orthogonalOptions,
        // Small overviews keep independent modules beside the flow, rather than
        // adding a separate packed row above its first dependency layer.
        'elk.separateConnectedComponents': boxes.length <= 8 ? 'false' : 'true',
        'elk.padding': '[top=0,left=0,bottom=0,right=0]' },
        children: boxes.map(box => ({ ...box, layoutOptions: { 'elk.portConstraints': 'FIXED_SIDE' }, ports: edges.flatMap(edge => [
          ...(edge.from === box.id ? [{ id: `${edge.id}:out`, width: 0, height: 0, layoutOptions: { 'elk.port.side': 'SOUTH' } }] : []),
          ...(edge.to === box.id ? [{ id: `${edge.id}:in`, width: 0, height: 0, layoutOptions: { 'elk.port.side': 'NORTH' } }] : []),
        ]) })), edges: edges.map(edge => ({ id: edge.id, sources: [`${edge.id}:out`], targets: [`${edge.id}:in`] })) });
      return { boxes: (result.children || []).map(box => ({ id: box.id, width: box.width!, height: box.height!, x: box.x, y: box.y })), routes: (result.edges || []).map(edge => ({ id: edge.id, points: edge.sections?.flatMap(section => [section.startPoint, ...(section.bendPoints || []), section.endPoint]) || [] })) };
    });
    Promise.race([organized, new Promise<never>((_, reject) => { timeout = setTimeout(() => reject(new Error('布局超时')), 30000); })]).then(positioned => {
      clearTimeout(timeout);
      if (!active) return;
      setLayout(positioned); setLayoutStructure(structure); onLayout(positioned);
    }).catch(async () => {
      clearTimeout(timeout);
      if (!active) return;
      const fallback = await layoutHierarchy(projection, verticalFallback);
      if (!active) return;
      setWarning('布局降级为纵向排列，模块层级与全部节点仍保留。'); setLayout(fallback); setLayoutStructure(structure); onLayout(fallback);
    });
    return () => { active = false; clearTimeout(timeout); };
  // Only topology changes should re-layout; learning updates preserve the viewport.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [structure]);
  const positioned = useMemo(() => {
    const current = new Map(projection.nodes.map(node => [node.id, node]));
    return (layout?.nodes || []).map(node => ({ ...node, ...current.get(node.id), x: node.x, y: node.y, width: node.width, height: node.height }));
  }, [layout, projection]);
  const positionedById = new Map(positioned.map(node => [node.id, node]));
  const nodes: Node<Data>[] = positioned.map(node => {
    const parent = node.parentId ? positionedById.get(node.parentId) : undefined;
    return { id: node.id, type: 'map', parentId: node.parentId, extent: node.parentId ? 'parent' : undefined,
      position: { x: node.x - (parent?.x || 0), y: node.y - (parent?.y || 0) }, data: { node, onInfo, onOpen: onNode, onHover: setHovered, dimmed: !!preview && !neighbors.has(node.id), onInternal: node => {
        const relation = projection.relations.find(item => item.from === node.id && item.to === node.id); if (relation) onRelation(relation);
      } },
      // Controlled nodes are rebuilt after viewport/panel changes. Preserve the
      // measured size so React Flow retains its handle bounds on those updates.
      width: node.width, height: node.height, measured: { width: node.width, height: node.height },
      // Offscreen nodes retain known ports, so visible edges do not depend on
      // first mounting both endpoints. Layout and export still contain every node.
      handles: [{type:'target', position:Position.Top, x:node.width/2, y:0, width:0,height:0},
        {type:'source', position:Position.Bottom, x:node.width/2, y:node.height, width:0,height:0}],
      style: { zIndex: node.container ? 0 : 1 } };
  });
  const edges = useMemo(() => {
    const byId = new Map(positioned.map(node => [node.id, node]));
    return (layout?.relations || []).filter(relation => relation.from !== relation.to || byId.get(relation.from)?.kind === 'function').map(relation => ({ id: relation.id, source: relation.from, target: relation.to,
      ariaLabel: `${byId.get(relation.from)?.label} → ${byId.get(relation.to)?.label}，查看调用证据`,
      type: 'routed', markerEnd: { type: MarkerType.ArrowClosed, color: graphTheme.edge }, data: { from: byId.get(relation.from), to: byId.get(relation.to), points: relation.points, label: projection.relations.find(item=>item.id===relation.id)?.label || relation.label, dimmed: !!preview && relation.from !== preview.id && relation.to !== preview.id,
        open: () => onRelation(projection.relations.find(item => item.id === relation.id) || relation) } }));
  }, [positioned, layout, projection, onRelation, preview?.id]);
  if (!layout || layoutStructure !== structure) return <div className="canvas-loading"><span className="spinner" />正在组织架构布局…</div>;
  return <ReactFlowProvider><ReactFlow key={structure} nodes={nodes} edges={edges} nodeTypes={nodeTypes} edgeTypes={edgeTypes}
    onNodeClick={(_, node) => onNode(node.data.node)} onEdgeClick={(_, edge) => { const relation = projection.relations.find(item => item.id === edge.id); if (relation) onRelation(relation); }}
    fitView={!viewport && nodes.length <= 200} defaultViewport={viewport} fitViewOptions={fitOptions}
    onlyRenderVisibleElements={nodes.length > 200}
    nodesDraggable={false} nodesConnectable={false} nodesFocusable={false} elementsSelectable={false} minZoom={.001} maxZoom={2}
    onMoveEnd={(_, value) => onViewport(value)} deleteKeyCode={null} colorMode="light" ariaLabelConfig={{ 'controls.ariaLabel': '画布控制', 'controls.zoomIn.ariaLabel': '放大', 'controls.zoomOut.ariaLabel': '缩小', 'controls.fitView.ariaLabel': '适应画布' }}>
    <Background color="#d7dee8" gap={22} size={.8} /><Controls showInteractive={false} fitViewOptions={fitOptions} />
    <ReadingScale nodes={positioned} initialReading={!viewport && nodes.length>200} />
    <Panel position="top-left"><div className="graph-view-options">{projection.nodes.some(node => node.kind === 'function') ? <span>{projection.relations.some(relation => relation.label === '接口协作' || relation.label === '协作关系') ? '函数与接口关系' : '函数调用图'} · 全部确认关系</span> : <>
      <label htmlFor="dependency-mode">依赖</label><select id="dependency-mode" value={dependencyMode} onChange={event => onDependencyMode(event.target.value as 'primary' | 'all')}><option value="primary">主要协作</option><option value="all">全部确认依赖</option></select>
      <span>{projection.hiddenRelationCount ? `隐藏 ${projection.hiddenRelationCount} 条` : projection.primaryFallback ? '未筛选 · 全部显示' : '全部可见'}</span></>}</div></Panel>
    {preview && <div className="module-hover-preview" role="status"><strong>{preview.label}</strong><p>{preview.description}</p><small>直接关联 {new Set(projection.relations.filter(item => item.from !== item.to && (item.from === preview.id || item.to === preview.id)).map(item => item.from === preview.id ? item.to : item.from)).size} 个模块{projection.hiddenRelationCount ? ' · 基于当前筛选' : ''}</small></div>}
    <div className="canvas-legend"><span><Circle size={9} />未开始</span><span><span className="learning-dot" />学习中</span><span><Check size={12} />已掌握</span></div>
    {warning && <div className="layout-warning">{warning}</div>}
  </ReactFlow></ReactFlowProvider>;
}
