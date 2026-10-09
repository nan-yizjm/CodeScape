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
    expect(view.nodes.map(node => node.id)).toEqual(['f', 'g']);
    expect(view.relations).toHaveLength(2);
    expect(view.relations.find(edge => edge.to === 'g')?.calls).toHaveLength(2);
    expect(view.relations.some(edge => edge.from === 'f' && edge.to === 'f')).toBe(true);
  });
  it('模块内部调用不伪装为模块间关系', () => {
    expect(projectGraph(fixture(), null).relations).toHaveLength(0);
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
  it('多层模块完整显示，纵向降级布局仍把每层子模块包在父容器内', async () => {
    const project = fixture();
    const leaf = project.architecture!.modules[0];
    leaf.parent_id = 'middle';
    project.architecture!.modules.unshift({ ...leaf, id: 'root', parent_id: null, name: '父模块', entity_ids: [] },
      { ...leaf, id: 'middle', parent_id: 'root', name: '子模块', entity_ids: [] });
    const view = await layoutHierarchy(projectGraph(project, null), verticalFallback);
    expect(view.nodes.map(node => node.id)).toEqual(['root', 'middle', 'm']);
    for (const node of view.nodes.filter(node => node.parentId)) {
      const parent = view.nodes.find(item => item.id === node.parentId)!;
      expect(node.x).toBeGreaterThan(parent.x);
      expect(node.y).toBeGreaterThan(parent.y + 140);
      expect(node.x + node.width).toBeLessThan(parent.x + parent.width);
      expect(node.y + node.height).toBeLessThan(parent.y + parent.height);
    }
    expect(projectGraph(project, 'middle').nodes.map(node => node.id)).toEqual(['middle', 'm']);
    const svg = exportSvg(view, '嵌套');
    expect(svg.indexOf('父模块')).toBeLessThan(svg.indexOf('子模块'));
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
