import { useEffect, useMemo, useState, type CSSProperties } from 'react';
import { ReactFlow, ReactFlowProvider, Background, Controls, Handle, Position, BaseEdge, MarkerType,
  type Node, type NodeProps, type EdgeProps, type Viewport } from '@xyflow/react';
import ELK from 'elkjs/lib/elk-api';
import { Info, Check, Circle, ArrowUpRight, AlertTriangle, Boxes, Braces } from 'lucide-react';
import type { Projection, ProjectionNode, Relation } from './types';
import { edgePath, nodePalette } from './graph';

type Data = { node: ProjectionNode; onInfo: (node: ProjectionNode) => void } & Record<string, unknown>;
const elk = new ELK({ workerFactory: () => new Worker(new URL('elkjs/lib/elk-worker.min.js', import.meta.url)) });

function MapNode({ data }: NodeProps<Node<Data>>) {
  const node = data.node;
  const Icon = node.kind === 'module' ? Boxes : Braces;
  const palette = nodePalette(node);
  const stateLabel = { not_started: '未开始', in_progress: '学习中', mastered: '已掌握' }[node.state];
  return <div className={`map-node ${node.kind} state-${node.state}`} style={{ '--node-bg': palette.background, '--node-border': palette.border, '--node-accent': palette.accent } as CSSProperties}>
    <Handle type="target" position={Position.Left} />
    <div className="node-top"><span className="node-symbol"><Icon size={16} /></span><span className="node-kind">{node.kind === 'module' ? '逻辑模块' : '函数'}</span>
      <button className="node-info nodrag nopan" aria-label={`查看 ${node.label} 详情`} onClick={event => { event.stopPropagation(); data.onInfo(node); }}><Info size={16} /></button></div>
    <div className="node-title" title={node.label}>{node.label}</div>
    {node.kind === 'module' && <p>{node.description}</p>}
    <div className="node-footer"><span>{node.kind === 'module' ? `${node.count} 个函数` : node.external ? <><ArrowUpRight size={12} /> 跨模块</> : '真实源码实现'}</span>
      <span className="node-state" title={stateLabel}>{node.incomplete && <AlertTriangle size={12} />}{node.state === 'mastered' ? <Check size={12} /> : node.state === 'in_progress' ? <span className="learning-dot" /> : <Circle size={10} />}<span>{stateLabel}</span></span></div>
    <Handle type="source" position={Position.Right} />
  </div>;
}

function RoutedEdge(props: EdgeProps) {
  const from = props.data?.from as ProjectionNode | undefined, to = props.data?.to as ProjectionNode | undefined;
  if (!from || !to) return null;
  return <BaseEdge id={props.id} path={edgePath(from, to)} markerEnd={props.markerEnd} style={{ stroke: '#999999', strokeWidth: 1.5 }} interactionWidth={20} />;
}

const nodeTypes = { map: MapNode };
const edgeTypes = { routed: RoutedEdge };

export function Canvas({ projection, onNode, onInfo, onRelation, onLayout, onViewport, viewport }: {
  projection: Projection; onNode: (node: ProjectionNode) => void; onInfo: (node: ProjectionNode) => void;
  onRelation: (relation: Relation) => void; onLayout: (projection: Projection) => void;
  onViewport: (viewport: Viewport) => void; viewport?: Viewport;
}) {
  const [layout, setLayout] = useState<Projection | null>(null);
  const [warning, setWarning] = useState('');
  const structure = useMemo(() => JSON.stringify(projection.nodes.map(node => [node.id, node.width, node.height])) + JSON.stringify(projection.relations.map(edge => edge.id)), [projection]);
  useEffect(() => {
    let active = true;
    setLayout(null);
    setWarning('');
    let timeout: ReturnType<typeof setTimeout>;
    Promise.race([elk.layout({ id: 'root', layoutOptions: { 'elk.algorithm': 'layered', 'elk.direction': 'RIGHT',
      'elk.spacing.nodeNode': '55', 'elk.layered.spacing.nodeNodeBetweenLayers': '115' },
      children: projection.nodes.map(node => ({ id: node.id, width: node.width, height: node.height })),
      edges: projection.relations.filter(edge => edge.from !== edge.to).map(edge => ({ id: edge.id, sources: [edge.from], targets: [edge.to] })),
    }), new Promise<never>((_, reject) => { timeout = setTimeout(() => reject(new Error('布局超时')), 30000); })]).then(result => {
      clearTimeout(timeout);
      if (!active) return;
      const positioned = { ...projection, nodes: projection.nodes.map(node => {
        const match = result.children?.find(child => child.id === node.id);
        return { ...node, x: match?.x || 0, y: (match?.y || 0) + 90 };
      }) };
      setLayout(positioned); onLayout(positioned);
    }).catch(() => {
      clearTimeout(timeout);
      if (!active) return;
      const fallback = { ...projection, nodes: projection.nodes.map((node, index) => ({ ...node, x: index % 3 * 380, y: Math.floor(index / 3) * 230 + 90 })) };
      setWarning('布局降级为网格，全部节点仍保留。'); setLayout(fallback); onLayout(fallback);
    });
    return () => { active = false; clearTimeout(timeout); };
  // Only topology changes should re-layout; learning updates preserve the viewport.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [structure]);
  const positioned = useMemo(() => (layout?.nodes || []).map(node => ({ ...node, ...projection.nodes.find(current => current.id === node.id), x: node.x, y: node.y })), [layout, projection]);
  const nodes: Node<Data>[] = positioned.map(node => ({ id: node.id, type: 'map', position: { x: node.x, y: node.y }, data: { node, onInfo }, width: node.width, height: node.height }));
  const edges = useMemo(() => {
    const byId = new Map(positioned.map(node => [node.id, node]));
    return projection.relations.map(relation => ({ id: relation.id, source: relation.from, target: relation.to,
      type: 'routed', markerEnd: { type: MarkerType.ArrowClosed, color: '#999999' }, data: { from: byId.get(relation.from), to: byId.get(relation.to) } }));
  }, [positioned, projection]);
  if (!layout) return <div className="canvas-loading"><span className="spinner" />正在组织架构布局…</div>;
  return <ReactFlowProvider><ReactFlow key={structure} nodes={nodes} edges={edges} nodeTypes={nodeTypes} edgeTypes={edgeTypes}
    onNodeClick={(_, node) => onNode(node.data.node)} onEdgeClick={(_, edge) => { const relation = projection.relations.find(item => item.id === edge.id); if (relation) onRelation(relation); }}
    fitView={!viewport} defaultViewport={viewport} fitViewOptions={{ padding: .27, maxZoom: 1.1 }}
    nodesDraggable={false} nodesConnectable={false} elementsSelectable={false} minZoom={.2} maxZoom={1.8}
    onMoveEnd={(_, value) => onViewport(value)} deleteKeyCode={null} colorMode="light">
    <Background color="#dedede" gap={22} size={.8} /><Controls showInteractive={false} />
    <div className="canvas-label"><span className="canvas-label-dot" />{projection.nodes[0]?.kind === 'function' ? '函数调用图' : '语义模块图'}</div>
    <div className="canvas-legend"><span><Circle size={9} />未开始</span><span><span className="learning-dot" />学习中</span><span><Check size={12} />已掌握</span></div>
    {warning && <div className="layout-warning">{warning}</div>}
  </ReactFlow></ReactFlowProvider>;
}
