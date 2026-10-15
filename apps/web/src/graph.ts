import type { AggregateState, Module, Point, ProjectDetail, Projection, ProjectionNode, Relation } from './types';

export const modulePalette = [
  { background: '#eff4ff', border: '#c9d7f2', accent: '#385a98' },
  { background: '#f4f0ff', border: '#d9cdf0', accent: '#6b4b98' },
  { background: '#fff6e9', border: '#ecd5b4', accent: '#85571c' },
  { background: '#edf7f2', border: '#c2dfd2', accent: '#28634c' },
  { background: '#fff0f4', border: '#edccd7', accent: '#8f405e' },
  { background: '#eef7fa', border: '#c5dfe8', accent: '#2f6478' },
];

// 画布与独立 SVG 使用同一组展示色，不影响关系或几何信息。
export const graphTheme = {
  background: '#f8fafc', edge: '#64748b', internalEdge: '#94a3b8',
  ink: '#0f172a', muted: '#475569', nodeRadius: 16,
};

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
  publication: '内容发布', sources: '信源采集', ingest: '外部内容导入', root: '页面骨架', events: '事件归并', editorial: '编辑分析', media: '媒体处理',
  providers: '模型与外部服务', admin: '管理后台', content: '内容处理', reports: '报告编排', jobs: '作业调度',
  operations: '运营工具', notify: '通知服务', lib: '基础库', routes: '路由集合',
  features: '功能域', components: '组件库', config: '配置',
};
const ABSTRACT_SUMMARIES: Record<string, string> = {
  apps: '组织 API 接口、Web 页面与后台服务入口。', packages: '承载核心业务处理与共享契约，供应用层调用。',
  tests: '验证业务流程、模块协作与异常恢复。', scripts: '提供维护、检查与评测工具。', site: '组织站点文案、品牌信息与基础展示。',
  api: '接收外部请求，完成接口装配、校验与响应。', web: '组织页面路由、界面组件与浏览器交互。', worker: '运行后台任务与定时调度。',
  backend: '执行内容处理、业务编排与数据服务。', contracts: '定义模块之间共享的数据结构与接口约定。',
  ingest: '接收外部条目，校验来源、去重并交给后续处理。', root: '加载页面公共数据，组织页面骨架与通用界面。',
  publication: '管理内容公开发布、检索与读取投影。', sources: '接入信源，调度采集并维护来源健康。', events: '归并相关内容，维护事件关系与后续报道。',
  editorial: '分析材料，生成标题、摘要与翻译内容。', media: '获取、处理与缓存图片和分享素材。', providers: '封装模型调用与外部数据服务。',
  admin: '提供后台管理、权限与运营操作。', content: '管理内容身份、正文处理与修订。', reports: '编排报告、版次与发布调度。',
  jobs: '组织作业队列、任务执行与失败恢复。', operations: '维护备份、监控与运行可靠性。', notify: '组织通知、告警与内容投递。',
  lib: '提供共享工具、状态管理与运行支持。', routes: '组织请求分发、页面入口与接口处理。',
  features: '组织业务功能、数据加载与用户操作。', components: '提供可复用界面组件与交互元素。', config: '管理运行参数与配置读取。',
};

function pathParts(module: Module): string[] {
  const path = module.code_paths[0] || '';
  return path.split('/').filter(Boolean).filter(part => !IGNORED_PATH_PARTS.has(part));
}

function abstractLabel(part: string, depth: number): string {
  if (ABSTRACT_LABELS[part]) return ABSTRACT_LABELS[part];
  const clean = part.replace(/\.(tsx?|jsx?|py|cpp|h)$/, '').replace(/[-_]+/g, ' ');
  if (ABSTRACT_LABELS[clean]) return ABSTRACT_LABELS[clean];
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

const FUNCTION_DOMAINS = [
  { key: 'collection', label: '采集与信源', pattern: /collect|source|crawl|fetch|rss|feed|采集|抓取|信源|来源/, summary: '负责信源接入、内容采集与抓取恢复。' },
  { key: 'management', label: '管理与权限', pattern: /admin|auth|session|cookie|audit|feedback|管理|权限|会话|审计|反馈/, summary: '负责管理操作、身份权限与反馈处理。' },
  { key: 'intelligence', label: '内容分析与评测', pattern: /llm|model|bench|eval|embed|digest|translate|grouping|relation|模型|评测|翻译|摘要|分组|关系/, summary: '负责模型调用、内容理解与效果评测。' },
  { key: 'publication', label: '发布与检索', pattern: /publish|publication|edition|report|search|topic|reading|story|发布|版次|报告|搜索|主题|阅读|故事/, summary: '负责内容发布、检索与阅读数据组织。' },
  { key: 'presentation', label: '界面与交互', pattern: /web|render|ui|component|navigation|media|image|界面|渲染|组件|导航|媒体|图片/, summary: '负责页面呈现、用户交互与媒体展示。' },
  { key: 'infrastructure', label: '运行与可靠性', pattern: /backup|migration|queue|worker|watchdog|budget|runtime|http|db|cache|备份|迁移|队列|预算|运行|缓存|恢复/, summary: '负责运行环境、数据维护与可靠性保障。' },
];

const DOMAIN_PATHS: Record<string, string> = {
  sources: 'collection', ingest: 'collection', collect: 'collection',
  admin: 'management', auth: 'management', audit: 'management', feedback: 'management',
  editorial: 'intelligence', events: 'intelligence', providers: 'intelligence', content: 'intelligence',
  publication: 'publication', reports: 'publication',
  media: 'presentation', site: 'presentation', components: 'presentation',
  jobs: 'infrastructure', operations: 'infrastructure', config: 'infrastructure', db: 'infrastructure', lib: 'infrastructure', modules: 'infrastructure', notify: 'infrastructure',
};

function scopeNames(children: Module[], modules: Module[]): string[] {
  const names = children.flatMap(child => child.id.startsWith(ABSTRACT_PREFIX)
    ? modules.filter(item => item.parent_id === child.id).map(item => item.name)
    : [child.name]);
  return [...new Set(names.length ? names : children.map(child => child.name))];
}

function scopeSummary(children: Module[], modules: Module[]): string {
  const names = scopeNames(children, modules);
  return `涵盖${names.slice(0, 3).join('、')}${names.length > 3 ? '等职责' : ''}。`;
}

function scopeLabel(children: Module[], modules: Module[]): string {
  // 相同描述不代表相同实现：用各自真实职责区别范围，不再编号复制同一句话。
  const names = scopeNames(children, modules);
  return names.slice(0, 2).map(name => name.length > 16 ? `${name.slice(0, 16)}…` : name).join(' · ');
}

function functionalBucket(child: Module, testing: boolean): AbstractBucket {
  const pathKey = (child.id.startsWith(ABSTRACT_PREFIX) ? child.id.split('/').pop() : child.code_paths[0]?.split('/').pop())?.replace(/\.(tsx?|test|spec)\b/g, '') || '';
  const direct = DOMAIN_PATHS[pathKey];
  const text = `${child.name} ${pathKey}`.toLowerCase();
  // 仅比较自身的名称/范围，避免在全部下游说明中匹配一个常见词便覆盖本职。
  const domain = FUNCTION_DOMAINS.find(item => item.key === direct) || FUNCTION_DOMAINS.find(item => item.pattern.test(text)) || FUNCTION_DOMAINS.at(-1)!;
  return { key: domain.key, label: `${domain.label}${testing ? '验证' : ''}`, match: new Set() };
}

function abstractBucket(child: Module): AbstractBucket | undefined {
  const key = (child.id.split('/').pop() || child.name.toLowerCase()).replace(/\.(tsx?|jsx?)$/, '');
  return ABSTRACT_BUCKETS.find(bucket => bucket.match.has(key) || bucket.match.has(child.name.toLowerCase()));
}

/** Keep an expanded code domain readable by grouping crowded sibling domains. */
function compactAbstractChildren(modules: Module[]): Module[] {
  const result = modules.map(module => ({ ...module }));
  const parents = result.filter(module => module.id.startsWith(ABSTRACT_PREFIX));
  for (const parent of parents) {
    const children = result.filter(module => module.parent_id === parent.id);
    if (children.length <= ABSTRACT_CHILD_LIMIT) continue;
    const groups = new Map<string, { bucket?: AbstractBucket; children: Module[] }>();
    const testing = /tests|测试/.test(`${parent.id} ${parent.name}`);
    for (const child of children) {
      const bucket = /\/web$/.test(parent.id) ? abstractBucket(child) || functionalBucket(child, testing) : functionalBucket(child, testing);
      const key = bucket?.key || 'other';
      if (!groups.has(key)) groups.set(key, { bucket, children: [] });
      groups.get(key)!.children.push(child);
    }
    const assignments = new Map<string, string>();
    const synthetic: Module[] = [];
    for (const [key, group] of groups) {
      const domainId = `${parent.id}/_bucket/${key}`;
      const addGroup = (items: Module[], id: string, parentId: string, name: string) => {
        if (items.length === 1) { assignments.set(items[0].id, parentId); return; }
        synthetic.push({ id, parent_id: parentId, name, summary: scopeSummary(items, result), entity_ids: [],
          code_paths: [...new Set(items.flatMap(item => item.code_paths))], incomplete: items.some(item => item.incomplete) });
        if (items.length <= ABSTRACT_CHILD_LIMIT) {
          for (const item of items) assignments.set(item.id, id);
          return;
        }
        const ordered = [...items].sort((a, b) => a.id.localeCompare(b.id));
        const size = Math.ceil(ordered.length / ABSTRACT_CHILD_LIMIT);
        for (let offset = 0; offset < ordered.length; offset += size) {
          const part = ordered.slice(offset, offset + size);
          addGroup(part, `${id}/scope-${offset}`, id, scopeLabel(part, result));
        }
      };
      // 只有一个同职责集合时，父级已经表达该范围，省去完全重复的一层。
      if (groups.size === 1) {
        const ordered = [...group.children].sort((a, b) => a.id.localeCompare(b.id));
        const size = Math.ceil(ordered.length / ABSTRACT_CHILD_LIMIT);
        for (let offset = 0; offset < ordered.length; offset += size) {
          const part = ordered.slice(offset, offset + size);
          addGroup(part, `${domainId}/scope-${offset}`, parent.id, scopeLabel(part, result));
        }
      } else addGroup(group.children, domainId, parent.id, group.bucket!.label);
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
export function abstractModules(modules: Module[]): Module[] {
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
      // 已有职责模块表达单分支范围；已知业务目录仍保留其导航分组语义。
      const singleFile = branch.length === 1 && /\.(tsx?|jsx?|py|cpp|h)$/.test(part);
      const namedDomain = !!ABSTRACT_LABELS[part] || ABSTRACT_BUCKETS.some(bucket => bucket.match.has(part));
      const canGroup = !singleFile && (branch.length > 1 || namedDomain)
        && (branch.length > ABSTRACT_THRESHOLD || next.size > 1);
      if (!canGroup) {
        for (const module of branch) result.push({ ...module, parent_id: parentId });
        continue;
      }
      const group: Module = {
        id: abstractId(current), parent_id: parentId,
        name: branch.length <= 2 && !ABSTRACT_LABELS[part]
          ? scopeLabel(branch, modules) : abstractLabel(part, current.length),
        summary: ABSTRACT_SUMMARIES[part] || ABSTRACT_SUMMARIES[part.replace(/\.(tsx?|jsx?|py|cpp|h)$/, '')] || [...new Set(branch.map(module => module.summary.split(/[。；]/)[0]).filter(Boolean))].slice(0, 2).join('；') + '。',
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

export function aggregateState(values: AggregateState[]): AggregateState {
  const states = values.filter(state => state !== 'not_applicable');
  if (!states.length) return 'not_applicable';
  if (states.every(state => state === 'not_started')) return 'not_started';
  if (states.every(state => state === 'mastered')) return 'mastered';
  return 'in_progress';
}

export function moduleLearningIds(module: Module, project: ProjectDetail, modules = project.architecture?.modules || []): string[] {
  const children = modules.filter(child => child.parent_id === module.id);
  if (children.length) return [...new Set(children.flatMap(child => moduleLearningIds(child, project, modules)))];
  const visible = new Set(project.analysis.entities.filter(entity => entity.visible).map(entity => entity.id));
  const functions = module.entity_ids.filter(id => visible.has(id));
  if (functions.length) return functions;
  const id = project.learning_entities?.find(unit => unit.kind === 'module' && unit.source_id === module.id)?.id;
  return id ? [id] : [];
}

export function moduleLearningState(module: Module, project: ProjectDetail, modules = project.architecture?.modules || []): AggregateState {
  return aggregateState(moduleLearningIds(module, project, modules).map(id => project.learning[id]?.state || 'not_started'));
}

export function projectGraph(project: ProjectDetail, focus: string | null, options: { expandedModuleId?: string | null; showOtherModules?: boolean } = {}): Projection {
  const sourceModules = project.architecture?.modules || [];
  // 函数层的同级关系沿用导航树，不能退回数百个原始根模块。
  const modules = abstractModules(sourceModules);
  const abstracted = modules !== sourceModules;
  const active = modules.find(module => module.id === focus);
  const children = modules.filter(module => module.parent_id === (focus || null));
  const functions = !!active && children.length === 0;
  const visible = project.analysis.entities.filter(entity => entity.visible);
  const scopes = new Map(visible.map(entity => [entity.scope_id, entity.id]));
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
  const siblings = functions ? modules.filter(module => module.id !== active.id && module.parent_id === active.parent_id) : [];
  const contextModules = siblings;
  // 函数层始终只展示当前模块的实现，跨模块调用使用存在性标记。
  const crossModuleCallers = new Set(project.analysis.calls.filter(call => call.kind !== 'interface' && call.resolution === 'confirmed' &&
    call.target_id && !active?.entity_ids.includes(call.target_id) &&
    sourceModules.some(module => module.entity_ids.includes(call.target_id!))).map(call => call.caller_scope_id));
  const functionNodes: ProjectionNode[] = functionExpanded ? visible.filter(entity => active.entity_ids.includes(entity.id)).map(entity => ({
    id: entity.id, label: visible.filter(item => item.name === entity.name).length > 1 ? `${entity.signature} · L${entity.location.start_line}` : entity.name, description: '', kind: 'function', entity, count: 0,
    state: project.learning[entity.id]?.state || 'not_started', incomplete: entity.unresolved_count > 0,
    external: crossModuleCallers.has(entity.scope_id), tone: modules.findIndex(module => module.entity_ids.includes(entity.id)),
    parentId: modules.find(module => module.entity_ids.includes(entity.id))?.id, x: 0, y: 0, width: 248, height: 116,
  })) : [];
  if (functions) {
    otherModuleCount = functionExpanded ? contextModules.length : 0;
    hiddenModuleCount = functionExpanded && !options.showOtherModules ? contextModules.length : 0;
    displayed.push(active, ...(options.showOtherModules ? contextModules : []));
  }
  const moduleNodes: ProjectionNode[] = displayed.map(module => {
    const entityIds = moduleEntities(module, modules);
    return { id: module.id, label: module.name, description: module.summary, kind: 'module', module,
      count: entityIds.length, state: moduleLearningState(module, project, modules),
      incomplete: module.incomplete, sibling: functions && siblings.some(item => item.id === module.id),
      external: functions && module.id !== active.id && !siblings.some(item => item.id === module.id), tone: modules.indexOf(module),
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
    if (functions && node.kind === 'module' && node.id === active.id) continue;
    for (const id of node.kind === 'function' ? [node.id] : moduleEntities(node.module!, modules)) owner.set(id, node.id);
  }
  // 隐藏作用域只聚合到模块边界，绝不提升成外层函数的直接调用。
  // 先沿真实作用域找到明确实体归属，再以唯一可见文件范围作为保守后备。
  const scopeRecords = new Map(project.analysis.scopes.map(scope => [scope.id, scope]));
  const allScopeEntities = new Map(project.analysis.entities.map(entity => [entity.scope_id, entity]));
  const allEntities = new Map(project.analysis.entities.map(entity => [entity.id, entity]));
  const pathOwners = new Map<string, string | null>();
  for (const node of moduleNodes) {
    const paths = new Set<string>();
    // 无函数叶模块同样保留代码路径；抽象域用其所有后代真实路径。
    const collectPaths = (id: string) => {
      const module = modules.find(item => item.id === id);
      module?.code_paths.filter(path => !path.endsWith('/')).forEach(path => paths.add(path));
      modules.filter(item => item.parent_id === id).forEach(item => collectPaths(item.id));
    };
    collectPaths(node.id);
    for (const path of paths) {
      const previous = pathOwners.get(path);
      if (previous === undefined || (previous && node.parentId === previous))
        pathOwners.set(path, node.id);
      else if (previous !== node.id) pathOwners.set(path, null);
    }
  }
  const scopeOwner = (scopeId: string): string | undefined => {
    let current: string | null | undefined = scopeId;
    const visited = new Set<string>();
    while (current && !visited.has(current)) {
      visited.add(current);
      const entity = allScopeEntities.get(current);
      const explicit = entity && owner.get(entity.id);
      if (explicit) return explicit;
      current = scopeRecords.get(current)?.parent_id;
    }
    const path = scopeRecords.get(scopeId)?.location.path || allScopeEntities.get(scopeId)?.location.path;
    return path ? pathOwners.get(path) || undefined : undefined;
  };
  const grouped = new Map<string, Relation>();
  const nodeRecords = new Map(nodes.map(node=>[node.id,node]));
  const moduleBoundary = (id: string | undefined) => {
    const node = id ? nodeRecords.get(id) : undefined;
    return node?.kind === 'function' ? node.parentId : id;
  };
  const internalRelationCounts = new Map<string, number>();
  for (const call of project.analysis.calls) {
    if (call.resolution !== 'confirmed' || !call.target_id) continue;
    const fromEntity = scopes.get(call.caller_scope_id);
    const from = functions && call.kind !== 'interface' ? fromEntity && owner.get(fromEntity) :
      call.kind === 'interface' ? moduleBoundary(scopeOwner(call.caller_scope_id)) : scopeOwner(call.caller_scope_id);
    const target = allEntities.get(call.target_id);
    const concreteTarget = owner.get(call.target_id) || (!functions && target ? scopeOwner(target.scope_id) : undefined);
    const to = call.kind === 'interface' ? moduleBoundary(concreteTarget) : concreteTarget;
    if (functions && call.kind === 'interface' && from === to) continue;
    // In a compact source-domain overview, retain a self-loop as an honest
    // signal that the visible domain contains internal dependencies. The
    // concrete module/function view continues to hide same-module calls.
    if (!from || !to || (!functions && from === to && !abstracted)) continue;
    if (functions && call.kind !== 'interface' && !active.entity_ids.includes(fromEntity || '') && !active.entity_ids.includes(call.target_id)) continue;
    const key = `${from}->${to}`;
    const label = call.kind === 'interface' && from !== to ? '接口协作' : functions ? '' : from === to ? '域内依赖' : '调用协作';
    if (!grouped.has(key)) grouped.set(key, { id: key, from, to, label, calls: [] });
    grouped.get(key)!.calls.push(call);
    if (grouped.get(key)!.calls.some(item=>item.kind==='interface') && grouped.get(key)!.calls.some(item=>item.kind!=='interface')) grouped.get(key)!.label='协作关系';
    if (from === to) internalRelationCounts.set(from, (internalRelationCounts.get(from) || 0) + 1);
  }
  const selections = project.architecture?.primary_dependencies || [];
  const relations = [...grouped.values()].map(relation => ({...relation,
    primaryReasons: [...new Set(selections.filter(item => item.call_ids.some(id => relation.calls.some(call => call.id === id))).map(item => item.reason))]}));
  return { nodes: nodes.map(node => ({ ...node, internalRelationCount: internalRelationCounts.get(node.id) || 0 })), relations, hiddenModuleCount, otherModuleCount, abstraction: abstracted };
}

/** Filter only module collaboration lines; retain full evidence and every node. */
export function dependencyProjection(projection: Projection, mode: 'primary' | 'all'): Projection {
  if (projection.nodes.some(node => node.kind === 'function') || mode === 'all') return {...projection, hiddenRelationCount: 0};
  const cross = projection.relations.filter(relation => relation.from !== relation.to);
  const selected = cross.filter(relation => relation.primaryReasons?.length);
  if (!selected.length) return {...projection, hiddenRelationCount: 0, primaryFallback: cross.length > 0};
  return {...projection, relations: projection.relations.filter(relation => relation.from === relation.to || relation.primaryReasons?.length),
    hiddenRelationCount: cross.length - selected.length};
}

export function escapeXml(value: string) {
  return value.replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('>', '&gt;').replaceAll('"', '&quot;').replaceAll("'", '&apos;');
}

export function relationLabelPoint(points?: Point[]): Point | undefined {
  const segments = points?.slice(1).map((point, index) => ({a: points[index], b: point,
    length: Math.hypot(point.x - points[index].x, point.y - points[index].y)})) || [];
  let remaining = segments.reduce((sum, segment) => sum + segment.length, 0) / 2;
  for (const segment of segments) {
    if (remaining <= segment.length && segment.length) return {
      x: segment.a.x + (segment.b.x - segment.a.x) * remaining / segment.length,
      y: segment.a.y + (segment.b.y - segment.a.y) * remaining / segment.length,
    };
    remaining -= segment.length;
  }
}

export function edgePath(from: ProjectionNode, to: ProjectionNode, points?: Point[]) {
  if (points && points.length >= 2) {
    let path = `M ${points[0].x} ${points[0].y}`;
    for (let index = 1; index < points.length - 1; index++) {
      const previous = points[index - 1], current = points[index], next = points[index + 1];
      const incoming = Math.hypot(current.x - previous.x, current.y - previous.y);
      const outgoing = Math.hypot(next.x - current.x, next.y - current.y);
      const radius = Math.min(8, incoming / 2, outgoing / 2);
      if (!incoming || !outgoing) continue;
      const before = { x: current.x + (previous.x - current.x) * radius / incoming, y: current.y + (previous.y - current.y) * radius / incoming };
      const after = { x: current.x + (next.x - current.x) * radius / outgoing, y: current.y + (next.y - current.y) * radius / outgoing };
      path += ` L ${before.x} ${before.y} Q ${current.x} ${current.y} ${after.x} ${after.y}`;
    }
    return `${path} L ${points.at(-1)!.x} ${points.at(-1)!.y}`;
  }
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
  const routePoints = projection.relations.flatMap(relation => relation.points || []);
  const left = Math.min(...nodes.map(node => node.x), ...routePoints.map(point => point.x)) - 95;
  const top = Math.min(...nodes.map(node => node.y), ...routePoints.map(point => point.y)) - 95;
  const right = Math.max(...nodes.map(node => node.x + node.width), ...routePoints.map(point => point.x)) + 100;
  const bottom = Math.max(...nodes.map(node => node.y + node.height), ...routePoints.map(point => point.y)) + 50;
  const byId = new Map(nodes.map(node => [node.id, node]));
  const edges = projection.relations.filter(relation => relation.from !== relation.to || byId.get(relation.from)?.kind === 'function').map(relation => {
    const from = byId.get(relation.from)!, to = byId.get(relation.to)!;
    const path = edgePath(from, to, relation.points);
    const label = from.kind === 'module' && to.kind === 'module' ? relationLabelPoint(relation.points) : undefined;
    const internal = from.id === to.id;
    return `<path d="${path}" fill="none" stroke="${graphTheme.background}" stroke-width="6" stroke-linecap="round" stroke-linejoin="round"/><path data-relation-id="${escapeXml(relation.id)}" d="${path}" fill="none" stroke="${internal ? graphTheme.internalEdge : graphTheme.edge}" stroke-width="${internal ? 1.2 : 1.5}" stroke-dasharray="${internal ? '4 4' : 'none'}" opacity="${internal ? .72 : .9}" stroke-linecap="round" stroke-linejoin="round" marker-end="url(#arrow)"/>${label ? `<rect x="${label.x - 27}" y="${label.y - 10}" width="54" height="20" rx="10" fill="#ffffff" stroke="#e2e8f0"/><text x="${label.x}" y="${label.y + 4}" text-anchor="middle" font-size="10" fill="${graphTheme.muted}">${escapeXml(relation.label || '调用协作')}</text>` : ''}`;
  }).join('');
  const textLines = (text: string, width: number, fontSize: number, limit: number): string[] => {
    const lines: string[] = []; let line = '', used = 0;
    for (const character of text.replace(/\s+/g, ' ').trim()) {
      const advance = /[^\u0000-\u00ff]/.test(character) ? fontSize : fontSize * .62;
      if (line && used + advance > width) { lines.push(line); line = ''; used = 0; }
      line += character; used += advance;
    }
    if (line) lines.push(line);
    if (lines.length > limit) lines[limit - 1] = [...lines[limit - 1]].slice(0, -1).join('') + '…';
    return lines.slice(0, limit);
  };
  const shapes = nodes.map((node, index) => {
    const palette = nodePalette(node);
    const color = node.kind === 'module' ? palette.background : '#ffffff';
    const labelLines = textLines(node.label, node.width - 40, 16, 2);
    const descriptionY = 80 + Math.max(0, labelLines.length - 1) * 20;
    const textBottom = node.container ? 105 : node.height - 35;
    const description = textLines(node.description, node.width - 40, 13, Math.max(1, Math.floor((textBottom - descriptionY) / 20) + 1))
      .map((line, lineIndex) => `<tspan x="${node.x + 20}" dy="${lineIndex ? 20 : 0}">${escapeXml(line)}</tspan>`).join('');
    const label = labelLines.map((line, lineIndex) => `<tspan x="${node.x + 20}" dy="${lineIndex ? 20 : 0}">${escapeXml(line)}</tspan>`).join('');
    return `<g data-node-id="${escapeXml(node.id)}"><title>${escapeXml(node.label)}</title><desc>${escapeXml(node.description)}</desc><defs><clipPath id="node-text-${index}"><rect x="${node.x + 20}" y="${node.y + 30}" width="${node.width - 40}" height="${textBottom - 25}"/></clipPath></defs><rect x="${node.x}" y="${node.y}" width="${node.width}" height="${node.height}" rx="${graphTheme.nodeRadius}" fill="${color}" stroke="${palette.border}"/>
      <rect x="${node.x + 20}" y="${node.y + 19}" width="18" height="3" rx="1.5" fill="${palette.accent}"/>
      <text x="${node.x + 20}" y="${node.y + 52}" clip-path="url(#node-text-${index})" font-size="16" font-weight="600" fill="${graphTheme.ink}">${label}</text>
      <text x="${node.x + 20}" y="${node.y + descriptionY}" clip-path="url(#node-text-${index})" font-size="13" fill="${graphTheme.muted}">${description}</text>
      ${node.kind === 'module' && node.internalRelationCount ? `<text x="${node.x + 20}" y="${node.y + 132}" font-size="10" fill="${palette.accent}">内部依赖 ${node.internalRelationCount}</text>` : ''}
      ${node.kind === 'function' && node.external ? `<text x="${node.x + 20}" y="${node.y + node.height - 15}" font-size="10" fill="#626b78">↗ 跨模块</text>` : ''}
      <text x="${node.x + node.width - 20}" y="${node.y + (node.container ? 115 : node.height - 15)}" text-anchor="end" font-size="10" fill="#555555">${node.state === 'mastered' ? '已掌握' : node.state === 'in_progress' ? '学习中' : node.state === 'not_applicable' ? '不适用' : '未开始'}</text>
      ${node.incomplete ? `<text x="${node.x + node.width - 22}" y="${node.y + 24}" fill="#9c6b32">!</text>` : ''}</g>`;
  }).join('');
  return `<?xml version="1.0" encoding="UTF-8"?><svg xmlns="http://www.w3.org/2000/svg" viewBox="${left} ${top} ${right - left} ${bottom - top}" role="img"><title>${escapeXml(title)}</title><defs><marker id="arrow" markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto"><path d="M0 0 L8 4 L0 8 Z" fill="${graphTheme.edge}"/></marker></defs><rect x="${left}" y="${top}" width="${right - left}" height="${bottom - top}" fill="${graphTheme.background}"/><g font-family="Microsoft YaHei, PingFang SC, sans-serif">${shapes}${edges}</g></svg>`;
}
