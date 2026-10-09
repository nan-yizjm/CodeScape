import type { LearningState, Module, ProjectDetail, Projection, ProjectionNode, Relation } from './types';

export function moduleEntities(module: Module, modules: Module[]): string[] {
  return [...module.entity_ids, ...modules.filter(child => child.parent_id === module.id).flatMap(child => moduleEntities(child, modules))];
}

export function aggregateState(states: LearningState[]): LearningState {
  if (!states.length || states.every(state => state === 'not_started')) return 'not_started';
  if (states.every(state => state === 'mastered')) return 'mastered';
  return 'in_progress';
}

export function projectGraph(project: ProjectDetail, focus: string | null): Projection {
  const modules = project.architecture?.modules || [];
  const active = modules.find(module => module.id === focus);
  const children = modules.filter(module => module.parent_id === (focus || null));
  const functions = !!active && children.length === 0;
  const visible = project.analysis.entities.filter(entity => entity.visible);
  const nodes: ProjectionNode[] = functions ? visible.filter(entity => active.entity_ids.includes(entity.id)).map(entity => ({
    id: entity.id, label: visible.filter(item => item.name === entity.name).length > 1 ? `${entity.signature} · L${entity.location.start_line}` : entity.name, description: '', kind: 'function', entity, count: 0,
    state: project.learning[entity.id]?.state || 'not_started', incomplete: entity.unresolved_count > 0,
    external: project.analysis.calls.some(call => call.caller_scope_id === entity.scope_id && call.resolution === 'confirmed'
      && !active.entity_ids.includes(call.target_id || '')), x: 0, y: 0, width: 230, height: 84,
  })) : children.map(module => {
    const entityIds = moduleEntities(module, modules);
    return { id: module.id, label: module.name, description: module.summary, kind: 'module', module,
      count: entityIds.length, state: aggregateState(entityIds.map(id => project.learning[id]?.state || 'not_started')),
      incomplete: module.incomplete, external: false, x: 0, y: 0, width: 280, height: 160 };
  });
  const scopes = new Map(visible.map(entity => [entity.scope_id, entity.id]));
  const owner = new Map<string, string>();
  for (const node of nodes) {
    for (const id of node.kind === 'function' ? [node.id] : moduleEntities(node.module!, modules)) owner.set(id, node.id);
  }
  const grouped = new Map<string, Relation>();
  for (const call of project.analysis.calls) {
    if (call.resolution !== 'confirmed' || !call.target_id) continue;
    const fromEntity = scopes.get(call.caller_scope_id);
    const from = fromEntity && owner.get(fromEntity), to = owner.get(call.target_id);
    if (!from || !to || (!functions && from === to)) continue;
    const key = `${from}->${to}`;
    const label = functions ? '' : '调用协作';
    if (!grouped.has(key)) grouped.set(key, { id: key, from, to, label, calls: [] });
    grouped.get(key)!.calls.push(call);
  }
  return { nodes, relations: [...grouped.values()] };
}

export function escapeXml(value: string) {
  return value.replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('>', '&gt;').replaceAll('"', '&quot;').replaceAll("'", '&apos;');
}

export function edgePath(from: ProjectionNode, to: ProjectionNode) {
  const sx = from.x + from.width, sy = from.y + from.height / 2;
  if (from.id === to.id) return `M ${sx - 45} ${from.y} C ${sx - 45} ${from.y - 70}, ${sx + 55} ${from.y - 70}, ${sx + 35} ${sy} L ${sx + 4} ${sy}`;
  const tx = to.x, ty = to.y + to.height / 2;
  if (tx < sx) {
    const lane = Math.min(from.y, to.y) - 55;
    return `M ${sx} ${sy} C ${sx + 60} ${sy}, ${sx + 60} ${lane}, ${sx + 20} ${lane} L ${tx - 20} ${lane} C ${tx - 60} ${lane}, ${tx - 60} ${ty}, ${tx} ${ty}`;
  }
  const distance = Math.max(55, Math.abs(tx - sx) / 2);
  return `M ${sx} ${sy} C ${sx + distance} ${sy}, ${tx - distance} ${ty}, ${tx} ${ty}`;
}

export function exportSvg(projection: Projection, title: string): string {
  const nodes = projection.nodes;
  if (!nodes.length) throw new Error('当前视图没有可以导出的节点。');
  const left = Math.min(...nodes.map(node => node.x)) - 95;
  const top = Math.min(...nodes.map(node => node.y)) - 95;
  const right = Math.max(...nodes.map(node => node.x + node.width)) + 100;
  const bottom = Math.max(...nodes.map(node => node.y + node.height)) + 50;
  const byId = new Map(nodes.map(node => [node.id, node]));
  const edges = projection.relations.map(relation => {
    const from = byId.get(relation.from)!, to = byId.get(relation.to)!;
    return `<path d="${edgePath(from, to)}" fill="none" stroke="#83948c" stroke-width="1.8" marker-end="url(#arrow)"/>`;
  }).join('');
  const shapes = nodes.map(node => {
    const color = node.state === 'mastered' ? '#e8f0e6' : node.state === 'in_progress' ? '#fffdf0' : '#ffffff';
    const description = [...node.description].reduce<string[]>((lines, character) => {
      if (!lines.length || lines[lines.length - 1].length >= 19) lines.push('');
      lines[lines.length - 1] += character;
      return lines;
    }, []).slice(0, 3).map((line, index) => `<tspan x="${node.x + 20}" dy="${index ? 20 : 0}">${escapeXml(line)}</tspan>`).join('');
    return `<g><rect x="${node.x}" y="${node.y}" width="${node.width}" height="${node.height}" rx="14" fill="${color}" stroke="#cbd5cc"/>
      <text x="${node.x + 20}" y="${node.y + 35}" font-size="16" fill="#263d31">${escapeXml(node.label)}</text>
      <text x="${node.x + 20}" y="${node.y + 65}" font-size="12" fill="#63736a">${description}</text>
      ${node.incomplete ? `<text x="${node.x + node.width - 22}" y="${node.y + 24}" fill="#9c6b32">!</text>` : ''}</g>`;
  }).join('');
  return `<?xml version="1.0" encoding="UTF-8"?><svg xmlns="http://www.w3.org/2000/svg" viewBox="${left} ${top} ${right - left} ${bottom - top}" role="img"><title>${escapeXml(title)}</title><defs><marker id="arrow" markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto"><path d="M0 0 L8 4 L0 8 Z" fill="#83948c"/></marker></defs><rect x="${left}" y="${top}" width="${right - left}" height="${bottom - top}" fill="#f7f8f3"/><g font-family="Microsoft YaHei, PingFang SC, sans-serif">${edges}${shapes}</g></svg>`;
}
