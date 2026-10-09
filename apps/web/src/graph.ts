import type { LearningState, Module, ProjectDetail, Projection, ProjectionNode, Relation } from './types';

export const modulePalette = [
  { background: '#edf2ff', border: '#cbd7f5', accent: '#405ea9' },
  { background: '#f1edff', border: '#d9cff5', accent: '#6f519f' },
  { background: '#fff4e6', border: '#efd8b6', accent: '#96621f' },
  { background: '#eaf6f1', border: '#c7e4d7', accent: '#326a53' },
  { background: '#fceef2', border: '#efcfd9', accent: '#944d66' },
  { background: '#eaf5f8', border: '#c7e1e8', accent: '#356677' },
];

export function nodePalette(node: ProjectionNode) {
  return modulePalette[(node.tone ?? 0) % modulePalette.length];
}

export function moduleEntities(module: Module, modules: Module[]): string[] {
  return [...module.entity_ids, ...modules.filter(child => child.parent_id === module.id).flatMap(child => moduleEntities(child, modules))];
}

export function aggregateState(states: LearningState[]): LearningState {
  if (!states.length || states.every(state => state === 'not_started')) return 'not_started';
  if (states.every(state => state === 'mastered')) return 'mastered';
  return 'in_progress';
}

export function projectGraph(project: ProjectDetail, focus: string | null, options: { expandedModuleId?: string | null; showOtherModules?: boolean } = {}): Projection {
  const modules = project.architecture?.modules || [];
  const active = modules.find(module => module.id === focus);
  const children = modules.filter(module => module.parent_id === (focus || null));
  const functions = !!active && children.length === 0;
  const visible = project.analysis.entities.filter(entity => entity.visible);
  const scopes = new Map(visible.map(entity => [entity.scope_id, entity.id]));
  const included = new Set(active?.entity_ids || []);
  if (functions) for (const call of project.analysis.calls) {
    const caller = scopes.get(call.caller_scope_id);
    if (call.resolution !== 'confirmed' || !call.target_id || !caller) continue;
    if (active.entity_ids.includes(caller)) included.add(call.target_id);
    if (active.entity_ids.includes(call.target_id)) included.add(caller);
  }
  const displayed: Module[] = [];
  function descend(parentId: string | null, maxDepth = Infinity, depth = 0) {
    if (depth >= maxDepth) return;
    for (const module of modules.filter(item => item.parent_id === parentId)) {
      displayed.push(module);
      descend(module.id, maxDepth, depth + 1);
    }
  }
  let hiddenModuleCount = 0;
  if (!functions) {
    if (focus) {
      if (active) displayed.push(active);
      descend(focus);
    } else {
      const roots = modules.filter(module => module.parent_id === null);
      const expandedId = options.expandedModuleId || null;
      const expanded = modules.find(module => module.id === expandedId);
      let anchorRoot = expanded;
      while (anchorRoot?.parent_id) anchorRoot = modules.find(module => module.id === anchorRoot!.parent_id);
      const visibleRoots = expanded && !options.showOtherModules ? roots.filter(module => module.id === anchorRoot?.id) : roots;
      hiddenModuleCount = roots.length - visibleRoots.length;
      for (const root of visibleRoots) {
        displayed.push(root);
        if (!expanded) continue;
        let onPath = root.id === anchorRoot?.id;
        let cursor: Module | undefined = expanded;
        while (cursor?.parent_id && !onPath) {
          onPath = cursor.parent_id === root.id;
          cursor = modules.find(module => module.id === cursor!.parent_id);
        }
        if (onPath) {
          const addExpanded = (parentId: string, depth: number) => {
            if (depth > 2) return;
            for (const child of modules.filter(item => item.parent_id === parentId)) {
              displayed.push(child);
              addExpanded(child.id, depth + 1);
            }
          };
          if (expanded.id === root.id) addExpanded(root.id, 1);
          else {
            const ancestors: Module[] = [];
            let current: Module | undefined = expanded;
            while (current && current.id !== root.id) { ancestors.unshift(current); current = current.parent_id ? modules.find(item => item.id === current!.parent_id) : undefined; }
            for (const ancestor of ancestors) {
              if (!displayed.some(item => item.id === ancestor.id)) displayed.push(ancestor);
            }
            addExpanded(expanded.id, 1);
          }
        }
      }
    }
  }
  const functionNodes: ProjectionNode[] = functions ? visible.filter(entity => included.has(entity.id)).map(entity => ({
    id: entity.id, label: visible.filter(item => item.name === entity.name).length > 1 ? `${entity.signature} · L${entity.location.start_line}` : entity.name, description: '', kind: 'function', entity, count: 0,
    state: project.learning[entity.id]?.state || 'not_started', incomplete: entity.unresolved_count > 0,
    external: !active.entity_ids.includes(entity.id), tone: modules.findIndex(module => module.entity_ids.includes(entity.id)),
    parentId: modules.find(module => module.entity_ids.includes(entity.id))?.id, x: 0, y: 0, width: 248, height: 116,
  })) : [];
  if (functions && functionNodes.length) {
    displayed.push(active, ...modules.filter(module => module.id !== active.id && functionNodes.some(node => module.entity_ids.includes(node.id))));
  }
  const moduleNodes: ProjectionNode[] = displayed.map(module => {
    const entityIds = moduleEntities(module, modules);
    return { id: module.id, label: module.name, description: module.summary, kind: 'module', module,
      count: entityIds.length, state: aggregateState(entityIds.map(id => project.learning[id]?.state || 'not_started')),
      incomplete: module.incomplete, external: functions && module.id !== active.id, tone: modules.indexOf(module),
      parentId: !functions && module.parent_id && displayed.some(item => item.id === module.parent_id) ? module.parent_id : undefined,
      container: functions || displayed.some(item => item.parent_id === module.id),
      canExpand: !functions && modules.some(item => item.parent_id === module.id),
      expanded: !functions && options.expandedModuleId === module.id,
      shownCount: functions ? functionNodes.filter(node => node.parentId === module.id).length : undefined,
      x: 0, y: 0, width: 300, height: 204 };
  });
  const nodes = [...moduleNodes, ...functionNodes];
  const owner = new Map<string, string>();
  for (const node of nodes) {
    if (functions && node.kind !== 'function') continue;
    for (const id of node.kind === 'function' ? [node.id] : moduleEntities(node.module!, modules)) owner.set(id, node.id);
  }
  const grouped = new Map<string, Relation>();
  for (const call of project.analysis.calls) {
    if (call.resolution !== 'confirmed' || !call.target_id) continue;
    const fromEntity = scopes.get(call.caller_scope_id);
    const from = fromEntity && owner.get(fromEntity), to = owner.get(call.target_id);
    if (!from || !to || (!functions && from === to)) continue;
    if (functions && !active.entity_ids.includes(from) && !active.entity_ids.includes(to)) continue;
    const key = `${from}->${to}`;
    const label = functions ? '' : '调用协作';
    if (!grouped.has(key)) grouped.set(key, { id: key, from, to, label, calls: [] });
    grouped.get(key)!.calls.push(call);
  }
  return { nodes, relations: [...grouped.values()], hiddenModuleCount };
}

export function escapeXml(value: string) {
  return value.replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('>', '&gt;').replaceAll('"', '&quot;').replaceAll("'", '&apos;');
}

export function edgePath(from: ProjectionNode, to: ProjectionNode) {
  const sx = from.x + from.width / 2, sy = from.y + from.height;
  const tx = to.x + to.width / 2, ty = to.y;
  if (from.id === to.id) return `M ${sx} ${sy} C ${sx} ${sy + 50}, ${from.x + from.width + 60} ${sy + 50}, ${from.x + from.width + 60} ${from.y - 50} C ${from.x + from.width + 60} ${from.y - 50}, ${tx} ${from.y - 50}, ${tx} ${ty}`;
  if (ty < sy) {
    const lane = Math.max(from.x + from.width, to.x + to.width) + 50;
    return `M ${sx} ${sy} C ${sx} ${sy + 40}, ${lane} ${sy + 40}, ${lane} ${sy} L ${lane} ${ty - 40} C ${lane} ${ty - 60}, ${tx} ${ty - 60}, ${tx} ${ty}`;
  }
  const distance = Math.max(40, Math.abs(ty - sy) / 2);
  return `M ${sx} ${sy} C ${sx} ${sy + distance}, ${tx} ${ty - distance}, ${tx} ${ty}`;
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
    return `<path d="${edgePath(from, to)}" fill="none" stroke="#929292" stroke-width="1.6" marker-end="url(#arrow)"/>`;
  }).join('');
  const shapes = nodes.map(node => {
    const palette = nodePalette(node);
    const color = node.kind === 'module' ? palette.background : '#ffffff';
    const description = [...node.description].reduce<string[]>((lines, character) => {
      if (!lines.length || lines[lines.length - 1].length >= 19) lines.push('');
      lines[lines.length - 1] += character;
      return lines;
    }, []).slice(0, 3).map((line, index) => `<tspan x="${node.x + 20}" dy="${index ? 20 : 0}">${escapeXml(line)}</tspan>`).join('');
    return `<g><rect x="${node.x}" y="${node.y}" width="${node.width}" height="${node.height}" rx="24" fill="${color}" stroke="${palette.border}"/>
      <rect x="${node.x + 20}" y="${node.y + 19}" width="18" height="3" rx="1.5" fill="${palette.accent}"/>
      <text x="${node.x + 20}" y="${node.y + 52}" font-size="16" font-weight="600" fill="#202020">${escapeXml(node.label)}</text>
      <text x="${node.x + 20}" y="${node.y + 80}" font-size="13" fill="#666666">${description}</text>
      <text x="${node.x + node.width - 20}" y="${node.y + (node.container ? 115 : node.height - 15)}" text-anchor="end" font-size="10" fill="#555555">${node.state === 'mastered' ? '已掌握' : node.state === 'in_progress' ? '学习中' : '未开始'}</text>
      ${node.incomplete ? `<text x="${node.x + node.width - 22}" y="${node.y + 24}" fill="#9c6b32">!</text>` : ''}</g>`;
  }).join('');
  return `<?xml version="1.0" encoding="UTF-8"?><svg xmlns="http://www.w3.org/2000/svg" viewBox="${left} ${top} ${right - left} ${bottom - top}" role="img"><title>${escapeXml(title)}</title><defs><marker id="arrow" markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto"><path d="M0 0 L8 4 L0 8 Z" fill="#929292"/></marker></defs><rect x="${left}" y="${top}" width="${right - left}" height="${bottom - top}" fill="#fafafa"/><g font-family="Microsoft YaHei, PingFang SC, sans-serif">${shapes}${edges}</g></svg>`;
}
