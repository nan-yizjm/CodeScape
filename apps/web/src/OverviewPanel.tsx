import { useMemo, useRef, useState } from 'react';
import { ArrowUpRight, Check, Info, Sparkles } from 'lucide-react';
import { api } from './api';
import { Disclosure } from './InteractionPrimitives';
import { abstractModules } from './graph';
import type { ProjectDetail } from './types';

export function OverviewPanel({ project, onUpdate, onExplore }: {
  project: ProjectDetail; onUpdate: (project: ProjectDetail) => void; onExplore: (id: string) => void;
}) {
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState('');
  const content = useRef<HTMLDivElement>(null);
  const overview = project.architecture!.overview;
  const roots = useMemo(() => abstractModules(project.architecture!.modules,project.analysis).filter(module => !module.parent_id), [project.architecture]);
  const incomplete = project.architecture!.modules.filter(module => module.incomplete).length;
  const legacyLong = !overview.synthesized && (overview.introduction.length > 160 || overview.principle.length > 240 || overview.capabilities.length > 5);

  async function refresh() {
    setRefreshing(true); setError('');
    try {
      onUpdate(await api<ProjectDetail>(`/projects/${project.id}/overview`, {}));
      content.current?.parentElement?.scrollTo({ top: 0, behavior: 'smooth' });
    }
    catch (value) { setError((value as Error).message); }
    finally { setRefreshing(false); }
  }

  return <div className="project-overview" ref={content}>
    <div className="panel-kicker">了解这个项目</div><h2>{project.name}</h2>
    {legacyLong ? <div className="overview-legacy"><p>旧版概览由分批说明组成，尚未归纳为全局介绍。</p><button className="secondary-button" onClick={() => void refresh()} disabled={refreshing}><Sparkles size={14} />{refreshing ? '正在归纳全局概览…' : '生成简明概览'}</button></div>
      : <><p className="lead">{overview.introduction}</p><section><h3>它能做什么</h3><ul className="capability-list">{overview.capabilities.map(value => <li key={value}><Check size={14} /><span>{value}</span></li>)}</ul></section><section><h3>它如何工作</h3><p>{overview.principle}</p></section></>}
    <section><h3>主要部分</h3><div className="overview-modules">{roots.slice(0, 6).map(module => <button className="overview-module" key={module.id} onClick={() => onExplore(module.id)}><span><strong>{module.name}</strong><small>{module.summary}</small></span><ArrowUpRight size={14} /></button>)}</div>{roots.length > 6 && <p className="overview-note">其余 {roots.length - 6} 个模块可在架构图中继续探索。</p>}</section>
    <footer className="overview-scope"><p><Info size={13} /><span>{project.demo ? '预置验证样例；说明来源见下方。' : '基于当前源码快照归纳，未执行项目。'}{incomplete > 0 && ` ${incomplete} 个模块的归属需复核。`}{project.diagnostic_count > 0 && ` 有 ${project.diagnostic_count} 条解析诊断。`}</span></p><Disclosure title="分析范围与说明">{overview.limitations.map(value => <p key={value}>{value}</p>)}{!!overview.analysis_notes?.length && <Disclosure title={<>原始分批记录（{overview.analysis_notes.length} 条）</>}><p>这些记录描述各批次当时的证据范围，不等同于全局缺口。</p>{overview.analysis_notes.map(value => <p key={value}>{value}</p>)}</Disclosure>}</Disclosure></footer>
    {!legacyLong && !project.demo && <button className="overview-refresh" onClick={() => void refresh()} disabled={refreshing}><Sparkles size={12} />{refreshing ? '正在归纳全局概览…' : '重新归纳概览'}</button>}
    {error && <p className="inline-error" role="alert">{error}</p>}
  </div>;
}
