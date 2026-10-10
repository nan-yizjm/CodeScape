import { describe, expect, it } from 'vitest';
import { aggregateState, exportSvg, projectGraph } from './graph';
import { layoutHierarchy, verticalFallback } from './layout';
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
  it('函数视图保留所属模块容器，跨模块函数只嵌入真实所属模块，导出与画布一致', async () => {
    const project = fixture();
    project.architecture!.modules[0].entity_ids = ['f'];
    project.architecture!.modules.push({ ...project.architecture!.modules[0], id: 'other', name: '关联模块', entity_ids: ['g'] });
    const view = await layoutHierarchy(projectGraph(project, 'm'), verticalFallback);
    expect(view.nodes.map(node => node.id)).toEqual(['m', 'other', 'f', 'g']);
    for (const [id, parentId] of [['f', 'm'], ['g', 'other']]) {
      const node = view.nodes.find(item => item.id === id)!;
      const parent = view.nodes.find(item => item.id === parentId)!;
      expect(node.parentId).toBe(parentId);
      expect(parent.container).toBe(true);
      expect(node.x).toBeGreaterThan(parent.x);
      expect(node.y).toBeGreaterThan(parent.y + 140);
      expect(node.x + node.width).toBeLessThan(parent.x + parent.width);
      expect(node.y + node.height).toBeLessThan(parent.y + parent.height);
    }
    expect(view.nodes.find(node => node.id === 'm')?.external).toBe(false);
    expect(view.nodes.find(node => node.id === 'other')?.external).toBe(true);
    expect(view.relations.find(edge => edge.from === 'f' && edge.to === 'g')?.calls).toHaveLength(2);
    const svg = exportSvg(view, '函数与所属模块');
    expect(svg).toContain('关联模块');
    expect(svg.indexOf('>模块</text>')).toBeLessThan(svg.indexOf('>f</text>'));
  });
  it('跨模块调用显示直接关联函数，仍保留多条证据和真实归属', () => {
    const project = fixture();
    project.architecture!.modules[0].entity_ids = ['f'];
    project.architecture!.modules.push({ ...project.architecture!.modules[0], id: 'other', name: '其他模块', entity_ids: ['g'] });
    const view = projectGraph(project, 'm');
    expect(view.nodes.find(node => node.id === 'g')?.external).toBe(true);
    expect(view.nodes.find(node => node.id === 'f')?.external).toBe(false);
    expect(view.relations.find(edge => edge.to === 'g')?.calls).toHaveLength(2);
    const incoming = projectGraph(project, 'other');
    expect(incoming.nodes.find(node => node.id === 'f')?.external).toBe(true);
    expect(incoming.nodes.find(node => node.id === 'g')?.external).toBe(false);
    expect(incoming.relations).toHaveLength(1);
    expect(incoming.relations[0].calls).toHaveLength(2);
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
    expect(view.nodes.map(node => node.id)).toEqual(['root', 'middle', 'm']);
    for (const node of view.nodes.filter(node => node.parentId)) {
      const parent = view.nodes.find(item => item.id === node.parentId)!;
      expect(node.x).toBeGreaterThan(parent.x);
      expect(node.y).toBeGreaterThan(parent.y + 140);
      expect(node.x + node.width).toBeLessThan(parent.x + parent.width);
      expect(node.y + node.height).toBeLessThan(parent.y + parent.height);
    }
    expect(projectGraph(project, 'middle', { expandedModuleId: 'middle' }).nodes.map(node => node.id)).toEqual(['middle', 'm']);
    const childView = projectGraph(project, null, { expandedModuleId: 'middle' });
    expect(childView.nodes.map(node => node.id)).toEqual(['root', 'middle', 'm']);
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
    expect(expanded.nodes.map(node => node.id)).toEqual(['root', 'middle', 'm']);
    expect(expanded.hiddenModuleCount).toBe(1);
    expect(expanded.otherModuleCount).toBe(1);
    expect(projectGraph(project, null, { expandedModuleId: 'root', showOtherModules: true }).nodes.map(node => node.id))
      .toEqual(['root', 'middle', 'm', 'sibling']);
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
    const featureGroup = apps.nodes.find(node => node.id === '__abstract__:apps/web/features/feature-0.tsx');
    expect(featureGroup).toBeDefined();
    expect(apps.nodes.some(node => node.id === 'large-0')).toBe(false);
    const feature = projectGraph(project, null, { expandedModuleId: featureGroup!.id });
    expect(feature.nodes.some(node => node.id === 'large-0')).toBe(true);
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
  it('抽象总览保留代码域内部依赖的自环提示', () => {
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
  });
  it('函数视图只保留真实关联模块，模块视图才提供同层模块开关', () => {
    const project = fixture();
    project.architecture!.modules[0].entity_ids = ['f'];
    project.architecture!.modules.push({ ...project.architecture!.modules[0], id: 'other', name: '同层模块', entity_ids: ['g'] });
    project.architecture!.modules.push({ ...project.architecture!.modules[0], id: 'unrelated', name: '无关模块', entity_ids: [] });
    const options = { expandedModuleId: 'm', showOtherModules: false };
    const expanded = projectGraph(project, 'm', options);
    expect(expanded.nodes.map(node => node.id)).toEqual(['m', 'f']);
    expect(expanded.nodes[0].expanded).toBe(true);
    expect(expanded.otherModuleCount).toBe(0);
    const all = projectGraph(project, 'm', { ...options, showOtherModules: true });
    expect(all.nodes.map(node => node.id)).toEqual(['m', 'other', 'f', 'g']);
    expect(all.otherModuleCount).toBe(0);
    expect(all.hiddenModuleCount).toBe(0);
    const collapsed = projectGraph(project, 'm', { ...options, expandedModuleId: null });
    expect(collapsed.nodes.map(node => node.id)).toEqual(['m']);
    expect(collapsed.otherModuleCount).toBe(0);
    expect(collapsed.nodes[0].container).toBe(false);
    expect(collapsed.nodes[0].canExpand).toBe(true);
  });
  it('聚合状态涵盖空模块和混合掌握情况', () => {
    expect(aggregateState([])).toBe('not_started');
    expect(aggregateState(['mastered', 'not_started'])).toBe('in_progress');
    expect(aggregateState(['mastered', 'mastered'])).toBe('mastered');
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
