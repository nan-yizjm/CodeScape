import type { Point, Projection } from './types';

const GAP = 18;
const STUB = 24;
type Segment = { a: Point; b: Point };
type Rectangle = { left: number; right: number; top: number; bottom: number };

function simplify(points: Point[]): Point[] {
  const result: Point[] = [];
  for (const point of points) {
    const previous = result.at(-1);
    if (previous && previous.x === point.x && previous.y === point.y) continue;
    while (result.length > 1) {
      const a = result.at(-2)!, b = result.at(-1)!;
      if ((a.x === b.x && b.x === point.x) || (a.y === b.y && b.y === point.y)) result.pop();
      else break;
    }
    result.push(point);
  }
  return result;
}

function blocks(a: Point, b: Point, rectangle: Rectangle): boolean {
  return a.x === b.x
    ? a.x > rectangle.left && a.x < rectangle.right && Math.max(a.y, b.y) > rectangle.top && Math.min(a.y, b.y) < rectangle.bottom
    : a.y > rectangle.top && a.y < rectangle.bottom && Math.max(a.x, b.x) > rectangle.left && Math.min(a.x, b.x) < rectangle.right;
}

function overlaps(a: Point, b: Point, segment: Segment): boolean {
  const c = segment.a, d = segment.b;
  return a.x === b.x && c.x === d.x ? Math.abs(a.x - c.x) < GAP && Math.min(Math.max(a.y, b.y), Math.max(c.y, d.y)) > Math.max(Math.min(a.y, b.y), Math.min(c.y, d.y))
    : a.y === b.y && c.y === d.y && Math.abs(a.y - c.y) < GAP && Math.min(Math.max(a.x, b.x), Math.max(c.x, d.x)) > Math.max(Math.min(a.x, b.x), Math.min(c.x, d.x));
}

function crosses(a: Point, b: Point, segment: Segment): boolean {
  const c = segment.a, d = segment.b;
  const horizontal = a.y === b.y;
  if (horizontal === (c.y === d.y)) return false;
  const h1 = horizontal ? a : c, h2 = horizontal ? b : d, v1 = horizontal ? c : a, v2 = horizontal ? d : b;
  return v1.x >= Math.min(h1.x, h2.x) && v1.x <= Math.max(h1.x, h2.x)
    && h1.y >= Math.min(v1.y, v2.y) && h1.y <= Math.max(v1.y, v2.y);
}

// A small heap keeps routing bounded even when several corridors are available.
class Queue {
  private items: { state: number; score: number; cost: number }[] = [];
  push(item: { state: number; score: number; cost: number }) {
    this.items.push(item);
    let index = this.items.length - 1;
    while (index > 0) {
      const parent = (index - 1) >> 1;
      if (this.items[parent].score <= item.score) break;
      this.items[index] = this.items[parent]; index = parent;
    }
    this.items[index] = item;
  }
  pop() {
    if (!this.items.length) return undefined;
    const first = this.items[0], last = this.items.pop()!;
    if (this.items.length) {
      let index = 0;
      while (index * 2 + 1 < this.items.length) {
        let child = index * 2 + 1;
        if (child + 1 < this.items.length && this.items[child + 1].score < this.items[child].score) child++;
        if (this.items[child].score >= last.score) break;
        this.items[index] = this.items[child]; index = child;
      }
      this.items[index] = last;
    }
    return first;
  }
}

/** Repair diagonal ELK hierarchy segments without moving their real ports. */
export function normalizeOrthogonal(points: Point[]): Point[] {
  return simplify(points.flatMap((point, index) => {
    const previous = points[index - 1];
    return previous && previous.x !== point.x && previous.y !== point.y ? [{ x: point.x, y: previous.y }, point] : [point];
  }));
}

/** Route compact compound views through separate lanes, outside cards and headers. */
export function routeDependencies(projection: Projection): Projection {
  const relations = projection.relations.map(edge => ({ ...edge, points: edge.points && normalizeOrthogonal(edge.points) }));
  const visible = relations.filter(edge => edge.from !== edge.to && edge.points && edge.points.length > 1);
  // Large function maps retain the worker paths; avoid an unbounded main-thread grid.
  if (!visible.length || projection.nodes.length > 40 || visible.length > 80) return { ...projection, relations };
  const rectangles: Rectangle[] = projection.nodes.map(node => node.container
    ? { left: node.x + 8, right: node.x + node.width - 8, top: node.y - 10, bottom: node.y + 144 }
    : { left: node.x - 10, right: node.x + node.width + 10, top: node.y - 10, bottom: node.y + node.height + 10 });
  const endpoints = visible.map(edge => ({ edge, source: edge.points![0], target: edge.points!.at(-1)! }));
  const xs = new Set<number>(), ys = new Set<number>();
  for (const rectangle of rectangles) for (const distance of [GAP, GAP * 2, GAP * 3]) {
    xs.add(rectangle.left - distance); xs.add(rectangle.right + distance);
    ys.add(rectangle.top - distance); ys.add(rectangle.bottom + distance);
  }
  for (const { source, target } of endpoints) {
    xs.add(source.x); xs.add(target.x); ys.add(source.y + STUB); ys.add(target.y - STUB);
  }
  const x = [...xs].sort((a, b) => a - b), y = [...ys].sort((a, b) => a - b);
  if (x.length * y.length > 16000) return { ...projection, relations };
  const point = (id: number): Point => ({ x: x[id % x.length], y: y[Math.floor(id / x.length)] });
  const id = (point: Point) => y.indexOf(point.y) * x.length + x.indexOf(point.x);
  const adjacency = new Map<number, { id: number; direction: number; length: number }[]>();
  for (let row = 0; row < y.length; row++) for (let column = 0; column < x.length; column++) {
    const from = row * x.length + column;
    for (const to of [column + 1 < x.length ? from + 1 : -1, row + 1 < y.length ? from + x.length : -1]) {
      if (to < 0) continue;
      const a = point(from), b = point(to);
      if (rectangles.some(rectangle => blocks(a, b, rectangle))) continue;
      const direction = a.y === b.y ? 1 : 2, length = Math.abs(a.x - b.x) + Math.abs(a.y - b.y);
      if (!adjacency.has(from)) adjacency.set(from, []);
      if (!adjacency.has(to)) adjacency.set(to, []);
      adjacency.get(from)!.push({ id: to, direction, length }); adjacency.get(to)!.push({ id: from, direction, length });
    }
  }
  const occupied: Segment[] = endpoints.flatMap(({ source, target }) => [
    { a: source, b: { x: source.x, y: source.y + STUB } }, { a: { x: target.x, y: target.y - STUB }, b: target },
  ]);
  // Keep a source's fan-out together and follow its optimized port order.
  const groupDistance = new Map<string, number>();
  for (const { edge, source, target } of endpoints) groupDistance.set(edge.from, Math.min(groupDistance.get(edge.from) ?? Infinity, Math.abs(source.x - target.x) + Math.abs(source.y - target.y)));
  endpoints.sort((a, b) => groupDistance.get(a.edge.from)! - groupDistance.get(b.edge.from)! || a.edge.from.localeCompare(b.edge.from)
    || a.source.x - b.source.x || a.edge.id.localeCompare(b.edge.id));
  for (const { edge, source, target } of endpoints) {
    const start = { x: source.x, y: source.y + STUB }, end = { x: target.x, y: target.y - STUB };
    const startId = id(start), endId = id(end), queue = new Queue();
    const distances = new Map<number, number>(), previous = new Map<number, number>();
    const heuristic = (node: number) => { const p = point(node); return Math.abs(p.x - end.x) + Math.abs(p.y - end.y); };
    const initial = startId * 3 + 2;
    distances.set(initial, 0); queue.push({ state: initial, cost: 0, score: heuristic(startId) });
    let completed: number | undefined;
    while (true) {
      const current = queue.pop();
      if (!current) break;
      if (current.cost !== distances.get(current.state)) continue;
      const currentId = Math.floor(current.state / 3), direction = current.state % 3;
      if (currentId === endId) { completed = current.state; break; }
      for (const next of adjacency.get(currentId) || []) {
        const a = point(currentId), b = point(next.id);
        if (occupied.some(segment => overlaps(a, b, segment))) continue;
        const cost = current.cost + next.length + (direction !== next.direction ? 28 : 0) + occupied.filter(segment => crosses(a, b, segment)).length * 120;
        const state = next.id * 3 + next.direction;
        if (cost >= (distances.get(state) ?? Infinity)) continue;
        distances.set(state, cost); previous.set(state, current.state);
        queue.push({ state, cost, score: cost + heuristic(next.id) });
      }
    }
    if (completed === undefined) continue;
    const path: Point[] = [];
    for (let state: number | undefined = completed; state !== undefined; state = previous.get(state)) path.unshift(point(Math.floor(state / 3)));
    edge.points = simplify([source, ...path, target]);
    for (let index = 1; index < edge.points.length; index++) occupied.push({ a: edge.points[index - 1], b: edge.points[index] });
  }
  return { ...projection, relations };
}
