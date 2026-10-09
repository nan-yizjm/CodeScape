import type { Projection, ProjectionNode } from './types';

type Box = { id: string; width: number; height: number; x?: number; y?: number };
type Arrange = (boxes: Box[], edges: { id: string; from: string; to: string }[]) => Promise<Box[]>;

// Arrange siblings first, then size each parent around its complete subtree.
// All exported coordinates are absolute; React Flow receives relative coordinates.
export async function layoutHierarchy(projection: Projection, arrange: Arrange): Promise<Projection> {
  const byId = new Map(projection.nodes.map(node => [node.id, { ...node }]));
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
    const positions = await arrange(siblings.map(({ id, width, height }) => ({ id, width, height })), edges);
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
  return { ...projection, nodes: projection.nodes.map(node => position(node.id)) };
}

export const verticalFallback: Arrange = async boxes => {
  let y = 0;
  return boxes.map(box => { const positioned = { ...box, x: 0, y }; y += box.height + 64; return positioned; });
};
