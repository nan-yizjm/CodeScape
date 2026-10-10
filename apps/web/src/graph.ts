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
  return [...new Set([...module.entity_ids, ...modules.filter(child => child.parent_id === module.id).flatMap(child => moduleEntities(child, modules))])];
}

const ABSTRACT_PREFIX = '__abstract__:';
const ABSTRACT_THRESHOLD = 12;
const ABSTRACT_CHILD_LIMIT = 6;
const IGNORED_PATH_PARTS = new Set(['src', 'app', 'server']);
const ABSTRACT_LABELS: Record<string, string> = {
  apps: '应用服务', packages: '核心包', tests: '测试验证', scripts: '工具脚本', site: '站点基础',
  api: 'API 服务', web: 'Web 前端', worker: '后台任务', backend: '后端核心', contracts: '契约定义',
  publication: '内容发布', sources: '信源采集', events: '事件归并', editorial: '编辑分析', media: '媒体处理',
  providers: '模型与外部服务', admin: '管理后台', content: '内容处理', reports: '报告编排', jobs: '作业调度',
  operations: '运营工具', notify: '通知服务', lib: '基础库', routes: '路由集合',
  features: '功能域', components: '组件库', config: '配置',
};

function pathParts(module: Module): string[] {
  const path = module.code_paths[0] || '';
  return path.split('/').filter(Boolean).filter(part => !IGNORED_PATH_PARTS.has(part));
}

function abstractLabel(part: string, depth: number): string {
  if (ABSTRACT_LABELS[part]) return ABSTRACT_LABELS[part];
  const clean = part.replace(/\.(tsx?|jsx?|py|cpp|h)$/, '').replace(/[-_]+/g, ' ');
  return depth > 1 ? clean : `代码域 · ${clean}`;
}

function abstractId(parts: string[]): string { return `${ABSTRACT_PREFIX}${parts.join('/')}`; }

type AbstractBucket = { key: string; label: string; match: Set<string> };
const ABSTRACT_BUCKETS: AbstractBucket[] = [
  { key: 'runtime', label: '运行与入口', match: new Set(['server', 'entry.server', 'vite.config', 'config']) },
  { key: 'interface', label: '界面与基础', match: new Set(['components', 'modules', 'root', 'site-modules', 'site modules', 'lib']) },
  { key: 'product', label: '业务功能', match: new Set(['features', 'routes', 'pages', 'reports']) },
  { key: 'tests', label: '测试验证', match: new Set(['tests', 'test', '测试验证']) },
];

function abstractBucket(child: Module): AbstractBucket | undefined {
  const key = child.id.split('/').pop() || child.name.toLowerCase();
  return ABSTRACT_BUCKETS.find(bucket => bucket.match.has(key) || bucket.match.has(child.name.toLowerCase()));
}

/** Keep an expanded code domain readable by grouping crowded sibling domains. */
function compactAbstractChildren(modules: Module[]): Module[] {
  const result = [...modules];
  const parents = result.filter(module => module.id.startsWith(ABSTRACT_PREFIX));
  for (const parent of parents) {
    const children = result.filter(module => module.parent_id === parent.id);
    if (children.length <= ABSTRACT_CHILD_LIMIT) continue;
    const groups = new Map<string, { bucket?: AbstractBucket; children: Module[] }>();
    for (const child of children) {
      const bucket = abstractBucket(child);
      const key = bucket?.key || 'other';
      if (!groups.has(key)) groups.set(key, { bucket, children: [] });
      groups.get(key)!.children.push(child);
    }
    const assignments = new Map<string, string>();
    const synthetic: Module[] = [];
    for (const [key, group] of groups) {
      const chunks: Module[][] = [];
      for (let index = 0; index < group.children.length; index += ABSTRACT_CHILD_LIMIT) {
        chunks.push(group.children.slice(index, index + ABSTRACT_CHILD_LIMIT));
      }
      chunks.forEach((chunk, index) => {
        const suffix = chunks.length > 1 ? `-${index + 1}` : '';
        const id = `${parent.id}/_bucket/${key}${suffix}`;
        for (const child of chunk) assignments.set(child.id, id);
        const sample = chunk.slice(0, 2).map(child => child.name).join('、');
        const fallbackName = sample ? `相关模块 · ${sample}${chunk.length > 2 ? '等' : ''}` : `其他模块${chunks.length > 1 ? ` ${index + 1}` : ''}`;
        synthetic.push({
          id, parent_id: parent.id,
          name: group.bucket ? `${group.bucket.label}${chunks.length > 1 ? ` ${index + 1}` : ''}` : fallbackName,
          summary: group.bucket ? `按职责归纳 ${chunk.length} 个代码域，点击继续查看。` : `包含 ${chunk.length} 个相关代码域：${sample || '待进一步归类'}。`,
          entity_ids: [], code_paths: [`${parent.code_paths[0] || ''}_bucket/${key}${suffix}/`],
          incomplete: chunk.some(child => child.incomplete),
        });
      });
    }
    const childIds = new Set(children.map(child => child.id));
    for (const child of children) child.parent_id = assignments.get(child.id)!;
    const firstChild = result.findIndex(module => childIds.has(module.id));
    const withoutChildren = result.filter(module => !childIds.has(module.id));
    const parentIndex = withoutChildren.findIndex(module => module.id === parent.id);
    withoutChildren.splice(parentIndex >= 0 ? parentIndex + 1 : firstChild, 0, ...synthetic, ...children);
    result.splice(0, result.length, ...withoutChildren);
  }
  return result;
}

/**
 * Large repositories often have hundreds of model modules at the same level.
 * Build a deterministic path-based navigation tree for the overview only;
 * the original modules and their evidence remain unchanged underneath it.
 */
function abstractModules(modules: Module[]): Module[] {
  const roots = modules.filter(module => module.parent_id === null);
  if (roots.length <= ABSTRACT_THRESHOLD) return modules;
  const paths = new Map(roots.map(module => [module.id, pathParts(module)]));
  const result: Module[] = [];
  const add = (bucket: Module[], prefix: string[], parentId: string | null) => {
    const next = new Map<string, Module[]>();
    for (const module of bucket) {
      const parts = paths.get(module.id) || [];
      const part = parts[prefix.length] || '';
      if (!part) continue;
      if (!next.has(part)) next.set(part, []);
      next.get(part)!.push(module);
    }
    // Skip an unlabeled path segment with a single branch so the extra layer
    // communicates a real subsystem boundary while preserving named domains.
    if (next.size === 1 && bucket.length > ABSTRACT_THRESHOLD && !ABSTRACT_LABELS[[...next.keys()][0]]) {
      const [part, branch] = [...next.entries()][0];
      add(branch, [...prefix, part], parentId);
      return;
    }
    for (const [part, branch] of next) {
      const current = [...prefix, part];
      const canGroup = branch.length > ABSTRACT_THRESHOLD || next.size > 1;
      if (!canGroup) {
        for (const module of branch) result.push({ ...module, parent_id: parentId });
        continue;
      }
      const group: Module = {
        id: abstractId(current), parent_id: parentId, name: abstractLabel(part, current.length),
        summary: `按源码路径归纳的高层代码域，包含 ${branch.length} 个逻辑模块。点击继续展开下一层。`,
        entity_ids: [], code_paths: [`${current.join('/')}/`], incomplete: branch.some(module => module.incomplete),
      };
      result.push(group);
      if (branch.length <= ABSTRACT_THRESHOLD) {
        for (const module of branch) result.push({ ...module, parent_id: group.id });
      } else add(branch, current, group.id);
    }
  };
  add(roots, [], null);
  // Preserve nested model modules and make them children of their original roots.
  const rootIds = new Set(roots.map(module => module.id));
  result.push(...modules.filter(module => !rootIds.has(module.id)));
  return compactAbstractChildren(result);
}

export function aggregateState(states: LearningState[]): LearningState {
  if (!states.length || states.every(state => state === 'not_started')) return 'not_started';
  if (states.every(state => state === 'mastered')) return 'mastered';
  return 'in_progress';
}

export function projectGraph(project: ProjectDetail, focus: string | null, options: { expandedModuleId?: string | null; showOtherModules?: boolean } = {}): Projection {
  const sourceModules = project.architecture?.modules || [];
  const modules = focus ? sourceModules : abstractModules(sourceModules);
  const abstracted = modules !== sourceModules;
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
  let otherModuleCount = 0;
  if (!functions) {
    if (focus) {
      if (active) {
        displayed.push(active);
        const siblings = modules.filter(module => module.parent_id === active.parent_id && module.id !== active.id);
        const isExpanded = options.expandedModuleId === active.id;
        otherModuleCount = isExpanded ? siblings.length : 0;
        hiddenModuleCount = isExpanded && !options.showOtherModules ? siblings.length : 0;
        if (options.expandedModuleId === active.id) descend(active.id, 1);
        if (options.showOtherModules || !isExpanded) displayed.push(...siblings);
      }
    } else {
      const roots = modules.filter(module => module.parent_id === null);
      const expandedId = options.expandedModuleId || null;
      const expanded = modules.find(module => module.id === expandedId);
      if (!expanded) displayed.push(...roots);
      else {
        const parent = expanded.parent_id ? modules.find(module => module.id === expanded.parent_id) : undefined;
        const siblings = parent ? modules.filter(module => module.parent_id === parent.id && module.id !== expanded.id) : roots.filter(module => module.id !== expanded.id);
        otherModuleCount = siblings.length;
        hiddenModuleCount = options.showOtherModules ? 0 : siblings.length;
        displayed.push(expanded);
        const addExpanded = (parentId: string) => displayed.push(...modules.filter(item => item.parent_id === parentId));
        addExpanded(expanded.id);
        if (options.showOtherModules) displayed.push(...siblings);
      }
    }
  }
  const functionExpanded = functions && options.expandedModuleId !== null &&
    (options.expandedModuleId === undefined || options.expandedModuleId === active.id);
  const functionNodes: ProjectionNode[] = functionExpanded ? visible.filter(entity => included.has(entity.id) &&
    (options.showOtherModules !== false || active.entity_ids.includes(entity.id))).map(entity => ({
    id: entity.id, label: visible.filter(item => item.name === entity.name).length > 1 ? `${entity.signature} · L${entity.location.start_line}` : entity.name, description: '', kind: 'function', entity, count: 0,
    state: project.learning[entity.id]?.state || 'not_started', incomplete: entity.unresolved_count > 0,
    external: !active.entity_ids.includes(entity.id), tone: modules.findIndex(module => module.entity_ids.includes(entity.id)),
    parentId: modules.find(module => module.entity_ids.includes(entity.id))?.id, x: 0, y: 0, width: 248, height: 116,
  })) : [];
  if (functions) {
    const related = modules.filter(module => module.id !== active.id && functionNodes.some(node => module.entity_ids.includes(node.id)));
    displayed.push(active, ...related);
  }
  const moduleNodes: ProjectionNode[] = displayed.map(module => {
    const entityIds = moduleEntities(module, modules);
    return { id: module.id, label: module.name, description: module.summary, kind: 'module', module,
      count: entityIds.length, state: aggregateState(entityIds.map(id => project.learning[id]?.state || 'not_started')),
      incomplete: module.incomplete, external: functions && module.id !== active.id, tone: modules.indexOf(module),
      parentId: !functions && module.parent_id && displayed.some(item => item.id === module.parent_id) ? module.parent_id : undefined,
      container: functionNodes.some(node => node.parentId === module.id) || displayed.some(item => item.parent_id === module.id),
      canExpand: functions ? module.id === active.id : modules.some(item => item.parent_id === module.id),
      expanded: functions ? module.id === active.id && functionExpanded : module.id === options.expandedModuleId,
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
  const internalRelationCounts = new Map<string, number>();
  for (const call of project.analysis.calls) {
    if (call.resolution !== 'confirmed' || !call.target_id) continue;
    const fromEntity = scopes.get(call.caller_scope_id);
    const from = fromEntity && owner.get(fromEntity), to = owner.get(call.target_id);
    // In a compact source-domain overview, retain a self-loop as an honest
    // signal that the visible domain contains internal dependencies. The
    // concrete module/function view continues to hide same-module calls.
    if (!from || !to || (!functions && from === to && !abstracted)) continue;
    if (functions && !active.entity_ids.includes(from) && !active.entity_ids.includes(to)) continue;
    const key = `${from}->${to}`;
    const label = functions ? '' : from === to ? '域内依赖' : '调用协作';
    if (!grouped.has(key)) grouped.set(key, { id: key, from, to, label, calls: [] });
    grouped.get(key)!.calls.push(call);
    if (from === to) internalRelationCounts.set(from, (internalRelationCounts.get(from) || 0) + 1);
  }
  return { nodes: nodes.map(node => ({ ...node, internalRelationCount: internalRelationCounts.get(node.id) || 0 })), relations: [...grouped.values()], hiddenModuleCount, otherModuleCount, abstraction: abstracted };
}

export function escapeXml(value: string) {
  return value.replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('>', '&gt;').replaceAll('"', '&quot;').replaceAll("'", '&apos;');
}

export function edgePath(from: ProjectionNode, to: ProjectionNode) {
  const sx = from.x + from.width / 2, sy = from.y + from.height;
  const tx = to.x + to.width / 2, ty = to.y;
  if (from.id === to.id) {
    const right = from.x + from.width, top = from.y + 34, bottom = from.y + from.height - 34, loop = right + 24;
    return `M ${right - 48} ${top} C ${loop} ${top}, ${loop} ${bottom}, ${right - 48} ${bottom}`;
  }
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
