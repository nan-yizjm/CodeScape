import { describe, expect, it } from 'vitest';
import { abstractModules, aggregateState, edgePath, exportSvg, moduleLearningState, projectGraph, relationLabelPoint } from './graph';
import { layoutCompound, layoutHierarchy, verticalFallback } from './layout';
import ELK from 'elkjs/lib/elk.bundled.js';
import type { ProjectDetail, ProjectionNode } from './types';

function fixture(): ProjectDetail {
  const location = { path: 'a.py', start_line: 1, end_line: 2, start_byte: 0, end_byte: 10 };
  const entities = ['f', 'g', 'hidden'].map(id => ({ id, name: id, qualified_name: id, signature: `${id}()`,
    language: 'python', location, scope_id: `${id}-scope`, visible: id !== 'hidden', hidden_reason: null,
    explanation: null, condition: null, unresolved_count: 0 }));
  const calls = [ ['f', 'g'], ['f', 'g'], ['f', 'f'], ['hidden', 'g'] ].map(([from, to], index) => ({
    id: `c${index}`, caller_scope_id: `${from}-scope`, target_id: to, expression: `${to}()`, location,
    resolution: 'confirmed', origin: 'static', reason: '已确认', target_name: to,
  }));
  return { id: 'p', name: '项目', source: '测试', created_at: '', status: 'ready', version_id: 'v', demo: true,
    error: null, function_count: 2, file_count: 1, progress: { mastered: 0, total: 2 }, languages: ['python'],
    diagnostic_count: 0, has_architecture: true, file_ids: { 'a.py': 'a' }, learning: {},
    scope: { excluded: [], snapshot_id: 's' }, analysis: { entities, calls, scopes: [], diagnostics: [], tools: {} },
    architecture: { modules: [{ id: 'm', parent_id: null, name: '模块', summary: '职责', entity_ids: ['f', 'g'],
      code_paths: ['a.py'], incomplete: false }], overview: { introduction: '', capabilities: [], principle: '', limitations: [] } } };
}

describe('基于证据生成图形', () => {
  it('合并多条同向调用，保留自递归，隐藏作用域不提升到外层', () => {
    const view = projectGraph(fixture(), 'm');
    expect(view.nodes.filter(node => node.kind === 'function').map(node => node.id)).toEqual(['f', 'g']);
    expect(view.relations).toHaveLength(2);
    expect(view.relations.find(edge => edge.to === 'g')?.calls).toHaveLength(2);
    expect(view.relations.some(edge => edge.from === 'f' && edge.to === 'f')).toBe(true);
  });
  it('模块内部调用不伪装为模块间关系', () => {
    expect(projectGraph(fixture(), null).relations).toHaveLength(0);
  });
  it('函数视图只展示当前实现，同级模块收起，跨模块调用仅标记存在性', async () => {
    const project = fixture();
    project.architecture!.modules[0].entity_ids = ['f'];
    project.architecture!.modules.push({ ...project.architecture!.modules[0], id: 'other', name: '同级模块', entity_ids: ['g'] });
    const view = await layoutHierarchy(projectGraph(project, 'm', {showOtherModules: true}), verticalFallback);
    expect(view.nodes.map(node => node.id)).toEqual(['m', 'other', 'f']);
    const node = view.nodes.find(item => item.id === 'f')!, parent = view.nodes.find(item => item.id === 'm')!;
    expect(node.parentId).toBe('m');
    expect(parent.container).toBe(true);
    expect(node.x).toBeGreaterThan(parent.x);
    expect(node.y).toBeGreaterThan(parent.y + 140);
    expect(node.x + node.width).toBeLessThan(parent.x + parent.width);
    expect(node.y + node.height).toBeLessThan(parent.y + parent.height);
    expect(node.external).toBe(true);
    expect(view.nodes.find(item => item.id === 'other')?.sibling).toBe(true);
    expect(view.nodes.find(item => item.id === 'other')?.container).toBe(false);
    expect(view.relations.find(edge => edge.from === 'f' && edge.to === 'other')?.calls).toHaveLength(2);
    const svg = exportSvg(view, '函数与所属模块');
    expect(svg).toContain('同级模块');
    expect(svg).toContain('跨模块');
    expect(svg.indexOf('>模块</text>')).toBeLessThan(svg.indexOf('>f</text>'));
    const isolated = projectGraph(project, 'm');
    expect(isolated.nodes.map(item => item.id)).toEqual(['m', 'f']);
    expect(isolated.relations.every(edge => edge.from === 'f' && edge.to === 'f')).toBe(true);
    const incoming = projectGraph(project, 'other');
    expect(incoming.nodes.map(item => item.id)).toEqual(['other', 'g']);
    expect(incoming.nodes.find(item => item.id === 'g')?.external).toBe(false);
    expect(incoming.relations).toHaveLength(0);
  });
  it('协作标签位于走线长度中点，SVG 与画布共享位置', () => {
    const points = [{x:0,y:0},{x:0,y:20},{x:100,y:20}];
    expect(relationLabelPoint(points)).toEqual({x:40,y:20});
    expect(relationLabelPoint([{x:0,y:0},{x:0,y:0}])).toBeUndefined();
    const project = fixture();
    project.architecture!.modules[0].entity_ids = ['f'];
    project.architecture!.modules.push({...project.architecture!.modules[0], id: 'other',entity_ids:['g']});
    const view = projectGraph(project,null);
    view.relations[0].points = points;
    expect(exportSvg(view,'协作')).toContain('x="40" y="24"');
    expect(exportSvg(view,'协作')).toContain('调用协作');
  });
  it('隐藏与顶层作用域可聚合唯一模块范围，函数图仍不提升匿名调用', () => {
    const project = fixture();
    project.analysis.entities.find(item => item.id === 'g')!.location = {...project.analysis.entities[0].location, path: 'b.py'};
    project.architecture!.modules[0].entity_ids = ['f'];
    project.architecture!.modules.push({...project.architecture!.modules[0], id: 'other', entity_ids: ['g'], code_paths: ['b.py']});
    project.analysis.scopes = [{id: 'top', name: 'a.py', kind: 'module', location: project.analysis.entities[0].location}];
    project.analysis.calls.push({...project.analysis.calls[0], id: 'top-call', caller_scope_id: 'top'});
    const view = projectGraph(project, null);
    expect(view.relations).toHaveLength(1);
    expect(view.relations[0].from).toBe('m');
    expect(view.relations[0].to).toBe('other');
    expect(view.relations[0].calls.map(call => call.caller_scope_id)).toEqual(['f-scope','f-scope','hidden-scope','top']);
    const functions = projectGraph(project, 'm', {showOtherModules: true});
    expect(functions.relations.find(edge => edge.to === 'other')?.calls).toHaveLength(2);
    // 同文件被多个独立模块覆盖时，不根据文件名猜测匿名逻辑的职责。
    project.architecture!.modules.push({...project.architecture!.modules[0], id: 'ambiguous', entity_ids: []});
    expect(projectGraph(project, null).relations[0].calls).toHaveLength(2);
    // 匿名作用域在真实具名实现内时，只聚合其模块，证据仍保留原作用域。
    project.analysis.scopes.push({id:'hidden-scope',name:'匿名',kind:'lambda',location:project.analysis.entities[0].location,parent_id:'f-scope'});
    expect(projectGraph(project, null).relations[0].calls).toHaveLength(3);
  });
  it('函数视图默认保留函数节点和调用连线，显式收起才隐藏函数', () => {
    const project = fixture();
    const expanded = projectGraph(project, 'm');
    expect(expanded.nodes.filter(node => node.kind === 'function').map(node => node.id)).toEqual(['f', 'g']);
    expect(expanded.relations).toHaveLength(2);
    const collapsed = projectGraph(project, 'm', { expandedModuleId: null });
    expect(collapsed.nodes.filter(node => node.kind === 'function')).toHaveLength(0);
    expect(collapsed.relations).toHaveLength(0);
  });
  it('多层模块完整显示，纵向降级布局仍把每层子模块包在父容器内', async () => {
    const project = fixture();
    const leaf = project.architecture!.modules[0];
    leaf.parent_id = 'middle';
    project.architecture!.modules.unshift({ ...leaf, id: 'root', parent_id: null, name: '父模块', entity_ids: [] },
      { ...leaf, id: 'middle', parent_id: 'root', name: '子模块', entity_ids: [] });
    const collapsed = projectGraph(project, null);
    expect(collapsed.nodes.map(node => node.id)).toEqual(['root']);
    expect(collapsed.hiddenModuleCount).toBe(0);
    const view = await layoutHierarchy(projectGraph(project, null, { expandedModuleId: 'root' }), verticalFallback);
    expect(view.nodes.map(node => node.id)).toEqual(['root', 'middle']);
    for (const node of view.nodes.filter(node => node.parentId)) {
      const parent = view.nodes.find(item => item.id === node.parentId)!;
      expect(node.x).toBeGreaterThan(parent.x);
      expect(node.y).toBeGreaterThan(parent.y + 140);
      expect(node.x + node.width).toBeLessThan(parent.x + parent.width);
      expect(node.y + node.height).toBeLessThan(parent.y + parent.height);
    }
    expect(projectGraph(project, 'middle', { expandedModuleId: 'middle' }).nodes.map(node => node.id)).toEqual(['middle', 'm']);
    const childView = projectGraph(project, null, { expandedModuleId: 'middle' });
    expect(childView.nodes.map(node => node.id)).toEqual(['middle', 'm']);
    const svg = exportSvg(view, '嵌套');
    expect(svg.indexOf('父模块')).toBeLessThan(svg.indexOf('子模块'));
  });
  it('父模块默认收起并隐藏同层模块，展开后最多显示两层且可恢复同层模块', () => {
    const project = fixture();
    const leaf = project.architecture!.modules[0];
    leaf.parent_id = 'middle';
    project.architecture!.modules.unshift(
      { ...leaf, id: 'root', parent_id: null, name: '父模块', entity_ids: [] },
      { ...leaf, id: 'middle', parent_id: 'root', name: '子模块', entity_ids: [] },
      { ...leaf, id: 'sibling', parent_id: null, name: '同层模块', entity_ids: [] },
    );
    expect(projectGraph(project, null).nodes.map(node => node.id)).toEqual(['root', 'sibling']);
    const expanded = projectGraph(project, null, { expandedModuleId: 'root' });
    expect(expanded.nodes.map(node => node.id)).toEqual(['root', 'middle']);
    expect(expanded.hiddenModuleCount).toBe(1);
    expect(expanded.otherModuleCount).toBe(1);
    expect(projectGraph(project, null, { expandedModuleId: 'root', showOtherModules: true }).nodes.map(node => node.id))
      .toEqual(['root', 'middle', 'sibling']);
  });
  it('大型项目概览先展示源码域，再逐层进入逻辑模块', () => {
    const project = fixture();
    const base = project.architecture!.modules[0];
    project.architecture!.modules = Array.from({ length: 30 }, (_, index) => ({
      ...base, id: `large-${index}`, name: `模块 ${index}`, entity_ids: [],
      code_paths: [index < 15 ? `apps/web/features/feature-${index}.tsx` : `packages/backend/src/publication/file-${index}.ts`],
    }));
    const overview = projectGraph(project, null);
    expect(overview.nodes.map(node => node.id)).toEqual(['__abstract__:apps', '__abstract__:packages']);
    const apps = projectGraph(project, null, { expandedModuleId: '__abstract__:apps' });
    expect(apps.nodes[0].id).toBe('__abstract__:apps');
    expect(apps.nodes.some(node => node.id === '__abstract__:apps/web')).toBe(true);
    const web = projectGraph(project, null, { expandedModuleId: '__abstract__:apps/web' });
    const featureGroup = web.nodes.find(node => node.id === '__abstract__:apps/web/features');
    expect(featureGroup).toBeDefined();
    expect(web.nodes.some(node => node.id === 'large-0')).toBe(false);
    const featureFiles = projectGraph(project, null, { expandedModuleId: featureGroup!.id });
    expect(featureFiles.nodes.some(node => node.id === '__abstract__:apps/web')).toBe(false);
    expect(featureFiles.nodes.find(node => node.id === featureGroup!.id)?.parentId).toBeUndefined();
    const navigation = abstractModules(project.architecture!.modules);
    const ancestors: string[] = [];
    let current = navigation.find(module => module.id === 'large-0')!;
    while (current.parent_id) {
      ancestors.unshift(current.parent_id);
      current = navigation.find(module => module.id === current.parent_id)!;
    }
    for (const ancestor of ancestors) {
      const level = projectGraph(project, null, { expandedModuleId: ancestor });
      expect(level.nodes.every(node => !node.parentId || node.parentId === ancestor)).toBe(true);
      expect(level.nodes.filter(node => node.parentId === ancestor).length).toBeLessThanOrEqual(6);
    }
    const feature = projectGraph(project, null, { expandedModuleId: ancestors.at(-1) });
    expect(feature.nodes.some(node => node.id === 'large-0')).toBe(true);
  });
  it('展开代码域时将拥挤的同级职责归并为少量子域', () => {
    const project = fixture();
    const base = project.architecture!.modules[0];
    const parts = ['server', 'entry.server', 'vite.config', 'components', 'modules', 'features', 'routes', 'lib', 'tests'];
    project.architecture!.modules = parts.concat(['features', 'routes', 'lib', 'components']).map((part, index) => ({
      ...base, id: `crowded-${index}`, name: `${part} ${index}`, entity_ids: [], code_paths: [`apps/web/${part}/file-${index}.tsx`],
    }));
    const expanded = projectGraph(project, null, { expandedModuleId: '__abstract__:apps/web' });
    const parents = expanded.nodes.filter(node => node.parentId === '__abstract__:apps/web');
    expect(parents.length).toBeLessThanOrEqual(6);
    expect(parents.map(node => node.label)).toEqual(expect.arrayContaining(['运行与入口', '界面与基础', '业务功能', '测试验证']));
  });
  it('抽象总览仍聚合跨代码域依赖', () => {
    const project = fixture();
    const base = project.architecture!.modules[0];
    project.architecture!.modules = Array.from({ length: 13 }, (_, index) => ({
      ...base, id: `domain-${index}`, name: `域模块 ${index}`, entity_ids: index === 0 ? ['f'] : index === 1 ? ['g'] : [],
      code_paths: [index === 0 ? 'apps/api/src/entry.ts' : `packages/backend/src/core-${index}.ts`],
    }));
    const overview = projectGraph(project, null);
    expect(overview.relations).toHaveLength(2);
    const cross = overview.relations.find(relation => relation.from !== relation.to)!;
    expect(new Set(cross.calls.map(call => call.target_id))).toEqual(new Set(['g']));
  });
  it('测试代码域按功能概括，保留真实模块与可回退的直属父级', () => {
    const project = fixture(), base = project.architecture!.modules[0];
    const paths = ['backup-files', 'collection-detail-budget', 'admin-session', 'model-budget', 'report-edition', 'web-render'];
    project.architecture!.modules = Array.from({ length: 30 }, (_, index) => ({ ...base,
      id: `test-${index}`, name: paths[index % paths.length], summary: '', entity_ids: [], code_paths: [`tests/${paths[index % paths.length]}-${index}.test.ts`] }));
    const modules = abstractModules(project.architecture!.modules);
    const view = projectGraph(project, null, { expandedModuleId: '__abstract__:tests' });
    const children = view.nodes.filter(node => node.parentId === '__abstract__:tests');
    expect(children.length).toBeLessThanOrEqual(6);
    expect(children.map(node => node.label)).toContain('采集与信源验证');
    expect(children.every(node => !node.label.includes('.test') && !node.description.includes('.test'))).toBe(true);
    const leaf = modules.find(module => module.id === 'test-0')!;
    const parentView = projectGraph(project, null, { expandedModuleId: leaf.parent_id });
    expect(parentView.nodes.some(node => node.id === leaf.id)).toBe(true);
    expect(parentView.nodes.some(node => node.expanded && node.id === leaf.parent_id)).toBe(true);
  });
  it('嵌套连线路径坐标随父容器平移，画布和导出使用相同圆角走线', async () => {
    const nodes = projectGraph(fixture(), 'm').nodes;
    const view = await layoutHierarchy({ nodes, relations: [{ id: 'f-g', from: 'f', to: 'g', label: '', calls: [] }] }, async (boxes, edges) => ({
      boxes: boxes.map((box, index) => ({ ...box, x: index * 400, y: 0 })),
      routes: edges.map(edge => ({ id: edge.id, points: [{ x: 10, y: 116 }, { x: 10, y: 140 }, { x: 410, y: 140 }, { x: 410, y: 0 }] })),
    }));
    const parent = view.nodes.find(node => node.id === 'm')!;
    const route = view.relations[0].points!;
    expect(route[0]).toEqual({ x: parent.x + 42, y: parent.y + 280 });
    const path = edgePath(view.nodes.find(node => node.id === 'f')!, view.nodes.find(node => node.id === 'g')!, route);
    expect(path).toContain(' Q ');
    expect(exportSvg(view, '路径')).toContain(`d="${path}"`);
  });
  it('跨容器依赖由真实端点正交布线，不穿过无关节点', async () => {
    const nodes = projectGraph(fixture(), 'm').nodes;
    nodes.push({ ...nodes[0], id: 'external', container: false, canExpand: false, expanded: false });
    const view = await layoutCompound({ nodes, relations: [
      { id: 'out', from: 'f', to: 'external', label: '', calls: [] },
      { id: 'back', from: 'external', to: 'g', label: '', calls: [] },
    ] }, graph => new ELK().layout(graph));
    expect(view.nodes).toHaveLength(nodes.length);
    for (const edge of view.relations) {
      expect(edge.points!.length).toBeGreaterThanOrEqual(2);
      const unrelated = view.nodes.filter(node => !node.container && node.id !== edge.from && node.id !== edge.to);
      for (let index = 1; index < edge.points!.length; index++) {
        const a = edge.points![index - 1], b = edge.points![index];
        expect(a.x === b.x || a.y === b.y).toBe(true);
        for (const node of unrelated) {
          const intersects = a.x === b.x ? a.x > node.x && a.x < node.x + node.width && Math.max(a.y, b.y) > node.y && Math.min(a.y, b.y) < node.y + node.height
            : a.y > node.y && a.y < node.y + node.height && Math.max(a.x, b.x) > node.x && Math.min(a.x, b.x) < node.x + node.width;
          expect(intersects).toBe(false);
        }
      }
      const source = view.nodes.find(node => node.id === edge.from)!;
      expect(edge.points![0].y).toBe(source.y + source.height);
      const target = view.nodes.find(node => node.id === edge.to)!;
      expect(edge.points!.at(-1)!.y).toBe(target.y);
    }
  });
  it('多路跨层汇入和扇出分别走线，没有斜线、共线重叠或穿卡片', async () => {
    const base = projectGraph(fixture(), 'm').nodes[0];
    const box = (id: string, parentId?: string): ProjectionNode => ({ ...base, id, parentId, container: id === 'routes', width: 300, height: 204 });
    const nodes = [box('routes'), ...['management', 'collection', 'publication', 'presentation', 'intelligence'].map(id => box(id, 'routes')), box('http'), box('app')];
    const pairs = [['publication', 'collection'], ['collection', 'management'], ['publication', 'http'], ['collection', 'http'], ['intelligence', 'http'],
      ['app', 'publication'], ['app', 'management'], ['app', 'collection'], ['app', 'presentation'], ['app', 'intelligence']];
    const relations = pairs.map(([from, to]) => ({ id: `${from}->${to}`, from, to, label: '', calls: [] }));
    const elk = new ELK();
    const view = await layoutCompound({ nodes, relations }, graph => elk.layout(graph));
    expect(view.relations.map(edge => edge.id)).toEqual(relations.map(edge => edge.id));
    const segments = view.relations.flatMap(edge => edge.points!.slice(1).map((b, index) => ({ a: edge.points![index], b, edge })));
    for (const { a, b, edge } of segments) {
      expect(a.x === b.x || a.y === b.y).toBe(true);
      for (const node of view.nodes.filter(node => !node.container)) {
        const inside = a.x === b.x ? a.x > node.x && a.x < node.x + node.width && Math.max(a.y, b.y) > node.y && Math.min(a.y, b.y) < node.y + node.height
          : a.y > node.y && a.y < node.y + node.height && Math.max(a.x, b.x) > node.x && Math.min(a.x, b.x) < node.x + node.width;
        expect(inside, `${edge.id} 穿过 ${node.id}`).toBe(false);
      }
      for (const other of segments.filter(item => item.edge.id !== edge.id)) {
        const sameVertical = a.x === b.x && other.a.x === other.b.x && a.x === other.a.x;
        const sameHorizontal = a.y === b.y && other.a.y === other.b.y && a.y === other.a.y;
        const overlap = sameVertical ? Math.min(Math.max(a.y, b.y), Math.max(other.a.y, other.b.y)) > Math.max(Math.min(a.y, b.y), Math.min(other.a.y, other.b.y))
          : sameHorizontal && Math.min(Math.max(a.x, b.x), Math.max(other.a.x, other.b.x)) > Math.max(Math.min(a.x, b.x), Math.min(other.a.x, other.b.x));
        expect(overlap, `${edge.id} 与 ${other.edge.id} 重叠`).toBe(false);
        if (a.x === b.x && other.a.x === other.b.x && Math.min(Math.max(a.y, b.y), Math.max(other.a.y, other.b.y)) > Math.max(Math.min(a.y, b.y), Math.min(other.a.y, other.b.y))) {
          expect(Math.abs(a.x - other.a.x), `${edge.id} 与 ${other.edge.id}: ${JSON.stringify({ a, b, other })}`).toBeGreaterThanOrEqual(18);
        }
        if (a.y === b.y && other.a.y === other.b.y && Math.min(Math.max(a.x, b.x), Math.max(other.a.x, other.b.x)) > Math.max(Math.min(a.x, b.x), Math.min(other.a.x, other.b.x))) {
          expect(Math.abs(a.y - other.a.y), `${edge.id} 与 ${other.edge.id}: ${JSON.stringify({ a, b, other })}`).toBeGreaterThanOrEqual(18);
        }
      }
    }
    const again = await layoutCompound({ nodes, relations }, graph => elk.layout(graph));
    expect(again.relations.map(edge => edge.points)).toEqual(view.relations.map(edge => edge.points));
    for (const edge of view.relations) expect(exportSvg(view, '多路连线')).toContain(edgePath(view.nodes.find(node => node.id === edge.from)!, view.nodes.find(node => node.id === edge.to)!, edge.points));
  });
  it('抽象总览保留内部依赖计数及原始证据', () => {
    const project = fixture();
    const base = project.architecture!.modules[0];
    project.architecture!.modules = Array.from({ length: 13 }, (_, index) => ({
      ...base, id: `same-domain-${index}`, name: `同域模块 ${index}`, entity_ids: index === 0 ? ['f', 'g'] : [],
      code_paths: [`packages/backend/src/core-${index}.ts`],
    }));
    const overview = projectGraph(project, null);
    expect(overview.relations).toHaveLength(1);
    expect(overview.relations[0].from).toBe(overview.relations[0].to);
    expect(overview.relations[0].label).toBe('域内依赖');
    expect(overview.nodes[0].internalRelationCount).toBe(3);
    expect(exportSvg(overview, '内部依赖')).not.toContain('<path d="M ');
  });
  it('函数层也能显示同级模块，其他模块保持收起并连接真实调用证据', () => {
    const project = fixture();
    project.architecture!.modules[0].entity_ids = ['f'];
    project.architecture!.modules.push({ ...project.architecture!.modules[0], id: 'other', name: '同层模块', entity_ids: ['g'] });
    project.architecture!.modules.push({ ...project.architecture!.modules[0], id: 'unrelated', name: '无关模块', entity_ids: [] });
    const options = { expandedModuleId: 'm', showOtherModules: false };
    const expanded = projectGraph(project, 'm', options);
    expect(expanded.nodes.map(node => node.id)).toEqual(['m', 'f']);
    expect(expanded.nodes[0].expanded).toBe(true);
    expect(expanded.otherModuleCount).toBe(2);
    expect(expanded.hiddenModuleCount).toBe(2);
    const all = projectGraph(project, 'm', { ...options, showOtherModules: true });
    expect(all.nodes.map(node => node.id)).toEqual(['m', 'other', 'unrelated', 'f']);
    expect(all.otherModuleCount).toBe(2);
    expect(all.nodes.filter(node => node.sibling).every(node => !node.expanded && !node.container)).toBe(true);
    expect(all.relations.find(edge => edge.from === 'f' && edge.to === 'other')?.calls).toHaveLength(2);
    expect(all.relations.some(edge => edge.from === 'unrelated' || edge.to === 'unrelated')).toBe(false);
    expect(all.hiddenModuleCount).toBe(0);
    const collapsed = projectGraph(project, 'm', { ...options, expandedModuleId: null });
    expect(collapsed.nodes.map(node => node.id)).toEqual(['m']);
    expect(collapsed.otherModuleCount).toBe(0);
    expect(collapsed.nodes[0].container).toBe(false);
    expect(collapsed.nodes[0].canExpand).toBe(true);
  });
  it('只恢复当前父级的同级模块，不把其他源码域的关联模块当成同级', () => {
    const project = fixture(), base = project.architecture!.modules[0];
    project.architecture!.modules = [
      { ...base, id: 'parent', entity_ids: [] },
      { ...base, parent_id: 'parent', entity_ids: ['f'] },
      { ...base, id: 'peer', parent_id: 'parent', entity_ids: [] },
      { ...base, id: 'outside', entity_ids: ['g'] },
    ];
    const view = projectGraph(project, 'm', { expandedModuleId: 'm', showOtherModules: true });
    expect(view.otherModuleCount).toBe(1);
    expect(view.nodes.map(node => node.id)).toEqual(['m', 'peer', 'f']);
  });
  it('分组依据自身源码范围，保持原始归属，去掉相同覆盖范围的重复层', () => {
    const project = fixture(), base = project.architecture!.modules[0];
    const parts = ['sources', 'ingest', 'admin', 'content', 'events', 'providers', 'reports', 'jobs', 'media', 'operations', 'notify', 'lib', 'publication'];
    project.architecture!.modules = parts.map((part, index) => ({ ...base,
      id: `role-${index}`, name: `${part}职责`, summary: '会使用来源、抓取结果与模型进行发布', entity_ids: [], code_paths: [`packages/backend/src/${part}/file.ts`] }));
    const before = JSON.stringify(project.architecture!.modules);
    const grouped = abstractModules(project.architecture!.modules);
    const source = grouped.find(module => module.id === '__abstract__:packages/backend/sources')!;
    const admin = grouped.find(module => module.id === '__abstract__:packages/backend/admin')!;
    expect(source.parent_id).toContain('/collection');
    expect(admin.parent_id).toBe('__abstract__:packages/backend');
    expect(grouped.some(module => module.id === '__abstract__:packages/backend/_bucket/management')).toBe(false);
    const domains = grouped.filter(module => module.parent_id === '__abstract__:packages/backend');
    expect(new Set(domains.map(module => module.summary)).size).toBe(domains.length);
    expect(grouped.filter(module => !module.id.startsWith('__abstract__:')).map(module => module.id).sort())
      .toEqual(project.architecture!.modules.map(module => module.id).sort());
    expect(JSON.stringify(project.architecture!.modules)).toBe(before);
    expect(grouped.every(module => !/采集与信源\s*\d+$/.test(module.name))).toBe(true);
  });
  it('聚合状态涵盖空模块和混合掌握情况', () => {
    expect(aggregateState([])).toBe('not_applicable');
    expect(aggregateState(['not_applicable', 'mastered'])).toBe('mastered');
    expect(aggregateState(['mastered', 'not_started'])).toBe('in_progress');
    expect(aggregateState(['mastered', 'mastered'])).toBe('mastered');
  });
  it('无函数可信叶模块可学习，父级排除占位和空集合', () => {
    const project = fixture();
    project.architecture!.modules = [
      {id: 'parent', parent_id: null, name: '配置域', summary: '管理配置', entity_ids: [], code_paths: ['a.py'], incomplete: false},
      {id: 'configuration', parent_id: 'parent', name: '配置', summary: '加载配置', entity_ids: [], code_paths: ['a.py'], incomplete: false},
      {id: 'unknown', parent_id: 'parent', name: '未知', summary: '待复核', entity_ids: [], code_paths: ['a.py'], incomplete: true},
    ];
    project.learning_entities = [{id: 'module:configuration', kind: 'module', source_id: 'configuration'}];
    project.learning['module:configuration'] = {state: 'mastered', revision: 1};
    expect(moduleLearningState(project.architecture!.modules[0], project)).toBe('mastered');
    expect(moduleLearningState(project.architecture!.modules[2], project)).toBe('not_applicable');
    expect(projectGraph(project, null).nodes[0].state).toBe('mastered');
    project.learning_entities = [];
    expect(moduleLearningState(project.architecture!.modules[0], project)).toBe('not_applicable');
  });
  it('导出包含画布外节点并转义不可信文本', () => {
    const nodes: ProjectionNode[] = projectGraph(fixture(), 'm').nodes.map((node, index) => ({ ...node,
      label: index ? '<script>alert(1)</script>' : '入口', x: index * 6000, y: 100 }));
    const svg = exportSvg({ nodes, relations: [] }, '项目 & 标题');
    expect(svg).toContain('x="6000"');
    expect(svg).toContain('&lt;script&gt;');
    expect(svg).not.toContain('<script>');
    expect(svg).toContain('项目 &amp; 标题');
  });
});
