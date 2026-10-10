import type { Point, Projection, ProjectionNode } from './types';
import type { ElkNode } from 'elkjs/lib/elk-api';
import { routeDependencies } from './routing';

export const orthogonalOptions = {
  'elk.algorithm': 'layered', 'elk.direction': 'DOWN', 'elk.edgeRouting': 'ORTHOGONAL',
  'elk.spacing.nodeNode': '64', 'elk.spacing.edgeEdge': '18', 'elk.layered.spacing.edgeEdgeBetweenLayers': '18',
  'elk.layered.spacing.nodeNodeBetweenLayers': '96', 'elk.layered.mergeEdges': 'false',
  'elk.layered.mergeHierarchyEdges': 'false',
};

/** Route cross-container edges with their real endpoints in one compound graph. */
export async function layoutCompound(projection: Projection, arrange: (graph: ElkNode) => Promise<ElkNode>): Promise<Projection> {
  const byId = new Map(projection.nodes.map(node => [node.id, node]));
  const edges = projection.relations.filter(edge => edge.from !== edge.to);
  function children(parentId?: string): ElkNode[] {
    return projection.nodes.filter(node => node.parentId === parentId).map(node => ({
      id: node.id, width: node.width, height: node.height,
      layoutOptions: { ...orthogonalOptions, 'elk.portConstraints': 'FIXED_SIDE',
        'elk.padding': node.container ? '[top=164,left=32,bottom=32,right=32]' : '[top=0,left=0,bottom=0,right=0]' },
      ports: edges.flatMap(edge => [
        ...(edge.from === node.id ? [{ id: `${edge.id}:out`, width: 0, height: 0, layoutOptions: { 'elk.port.side': 'SOUTH' } }] : []),
        ...(edge.to === node.id ? [{ id: `${edge.id}:in`, width: 0, height: 0, layoutOptions: { 'elk.port.side': 'NORTH' } }] : []),
      ]),
      ...(node.container ? { children: children(node.id) } : {}),
    }));
  }
  const result = await arrange({ id: '__layout_root__', layoutOptions: { ...orthogonalOptions,
    'elk.hierarchyHandling': 'INCLUDE_CHILDREN', 'elk.padding': '[top=60,left=0,bottom=0,right=0]' },
    children: children(), edges: edges.map(edge => ({ id: edge.id, sources: [`${edge.id}:out`], targets: [`${edge.id}:in`] })) });
  const positioned = new Map<string, ProjectionNode>();
  const routed = new Map<string, { container: string; points: Point[] }>();
  function visit(graph: ElkNode, offset: Point) {
    for (const child of graph.children || []) {
      const original = byId.get(child.id)!;
      const node = { ...original, x: offset.x + (child.x || 0), y: offset.y + (child.y || 0),
        width: child.width || original.width, height: child.height || original.height };
      positioned.set(child.id, node); visit(child, node);
    }
    for (const edge of graph.edges || []) routed.set(edge.id, { container: edge.container || graph.id,
      points: edge.sections?.flatMap(section => [section.startPoint, ...(section.bendPoints || []), section.endPoint]) || [] });
  }
  visit(result, { x: 0, y: 0 });
  return routeDependencies({ ...projection, nodes: projection.nodes.map(node => positioned.get(node.id)!), relations: projection.relations.map(edge => {
    const route = routed.get(edge.id), container = route && positioned.get(route.container);
    return { ...edge, points: route?.points.map(point => ({ x: point.x + (container?.x || 0), y: point.y + (container?.y || 0) })) };
  }) });
}

type Box = { id: string; width: number; height: number; x?: number; y?: number };
type Arrange = (boxes: Box[], edges: { id: string; from: string; to: string }[]) => Promise<Box[] | { boxes: Box[]; routes: { id: string; points: Point[] }[] }>;

// Arrange siblings first, then size each parent around its complete subtree.
// All exported coordinates are absolute; React Flow receives relative coordinates.
export async function layoutHierarchy(projection: Projection, arrange: Arrange): Promise<Projection> {
  const byId = new Map(projection.nodes.map(node => [node.id, { ...node }]));
  const routes = new Map<string, { parentId?: string; points: Point[] }>();
  async function level(parentId?: string): Promise<ProjectionNode[]> {
    const siblings = [...byId.values()].filter(node => node.parentId === parentId);
    for (const node of siblings) if (node.container) {
      const children = await level(node.id);
      node.width = Math.max(364, ...children.map(child => child.x + child.width + 32));
      node.height = Math.max(204, ...children.map(child => child.y + child.height + 32));
    }
    function ancestor(id: string) {
      let node = byId.get(id);
      while (node?.parentId !== parentId && node?.parentId) node = byId.get(node.parentId);
      return node && node.parentId === parentId ? node.id : undefined;
    }
    const edges = projection.relations.flatMap(edge => {
      const from = ancestor(edge.from), to = ancestor(edge.to);
      return from && to && from !== to ? [{ id: edge.id, from, to }] : [];
    });
    const arranged = await arrange(siblings.map(({ id, width, height }) => ({ id, width, height })), edges);
    const positions = Array.isArray(arranged) ? arranged : arranged.boxes;
    if (!Array.isArray(arranged)) for (const route of arranged.routes) {
      const edge = projection.relations.find(item => item.id === route.id);
      // Routes between concrete siblings have the correct ports. Ancestor
      // layout edges only arrange containers and must not replace leaf paths.
      if (edge && siblings.some(node => node.id === edge.from) && siblings.some(node => node.id === edge.to)) {
        routes.set(route.id, { parentId, points: route.points.map(point => ({ x: point.x + (parentId ? 32 : 0), y: point.y + (parentId ? 164 : 60) })) });
      }
    }
    for (const node of siblings) {
      const position = positions.find(item => item.id === node.id);
      node.x = (position?.x || 0) + (parentId ? 32 : 0);
      node.y = (position?.y || 0) + (parentId ? 164 : 60);
    }
    return siblings;
  }
  await level();
  const absolute = new Map<string, ProjectionNode>();
  function position(id: string): ProjectionNode {
    const cached = absolute.get(id);
    if (cached) return cached;
    const node = byId.get(id)!;
    const parent = node.parentId ? position(node.parentId) : null;
    const result = { ...node, x: node.x + (parent?.x || 0), y: node.y + (parent?.y || 0) };
    absolute.set(id, result);
    return result;
  }
  const nodes = projection.nodes.map(node => position(node.id));
  return { ...projection, nodes, relations: projection.relations.map(relation => {
    const route = routes.get(relation.id), parent = route?.parentId ? position(route.parentId) : null;
    return { ...relation, points: route?.points.map(point => ({ x: point.x + (parent?.x || 0), y: point.y + (parent?.y || 0) })) };
  }) };
}

export const verticalFallback: Arrange = async boxes => {
  let y = 0;
  return boxes.map(box => { const positioned = { ...box, x: 0, y }; y += box.height + 64; return positioned; });
};
