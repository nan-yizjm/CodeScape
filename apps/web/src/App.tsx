import { lazy, Suspense, useEffect, useMemo, useRef, useState } from 'react';
import { ArrowLeft, ArrowRight, BookOpen, Boxes, ChevronRight, Code2, Download, FolderOpen, GitBranch,
  Info, Layers3, Plus, Settings2, ShieldCheck, Sparkles, X, Check, AlertTriangle, Circle, FileCode2,
  Activity, Braces, ExternalLink, ArrowUpRight, Maximize2, Minimize2, ListOrdered, Pause, Play, Square, ArrowUp, ArrowDown } from 'lucide-react';
import type { Viewport } from '@xyflow/react';
import { api, bootstrap, keepalive } from './api';
import { OverviewPanel } from './OverviewPanel';
import { abstractModules, exportSvg, moduleEntities, moduleLearningState, projectGraph } from './graph';
import type { AnalysisTask, Entity, ImportPreview, LearningState, Panel, Project, ProjectDetail, Projection, ProjectionNode, SourceFile } from './types';

const Canvas = lazy(() => import('./Canvas').then(module => ({ default: module.Canvas })));
const SourceReader = lazy(() => import('./SourceReader').then(module => ({ default: module.SourceReader })));

const statuses: Record<string, string> = { ready: '可以探索', indexed: '静态索引就绪', indexing: '正在解析',
  queued: '等待解析', waiting: '等待主动启动', analyzing: 'AI 分析中', paused: '分析已暂停', failed: '解析失败', cancelled: '任务已取消' };
const taskLabels: Record<string, string> = { waiting: '等待启动', running: '运行中', paused: '已暂停',
  cancelled: '已取消', completed: '已完成', failed: '失败' };
const stateLabels = { not_started: '未开始', in_progress: '学习中', mastered: '已掌握', not_applicable: '不适用' };

export default function App() {
  const [authorized, setAuthorized] = useState(false);
  const [projects, setProjects] = useState<Project[]>([]);
  const [analysisTasks, setAnalysisTasks] = useState<AnalysisTask[]>([]);
  const [project, setProject] = useState<ProjectDetail | null>(null);
  const [focus, setFocus] = useState<string | null>(null);
  const [panels, setPanels] = useState<Panel[]>([]);
  const [source, setSource] = useState<SourceFile | null>(null);
  const [viewports, setViewports] = useState<Record<string, Viewport>>({});
  const [modal, setModal] = useState<'import' | 'settings' | 'consent' | 'tasks' | null>(null);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [busy, setBusy] = useState(false);
  const [importPath, setImportPath] = useState('');
  const [importKind, setImportKind] = useState<'local' | 'git'>('local');
  const [importPreview, setImportPreview] = useState<ImportPreview | null>(null);
  const [reanalysisProject, setReanalysisProject] = useState<string | null>(null);
  const [includeText, setIncludeText] = useState('');
  const [excludeText, setExcludeText] = useState('');
  const [settings, setSettings] = useState({ base_url: 'https://api.deepseek.com', model: 'deepseek-flash', has_key: false });
  const [key, setKey] = useState('');
  const [testMessage, setTestMessage] = useState('');
  const [width, setWidth] = useState(420);
  const [fullscreen, setFullscreen] = useState(false);
  const [expandedModule, setExpandedModule] = useState<string | null>(null);
  const [showOtherModules, setShowOtherModules] = useState(false);
  const shellRef = useRef<HTMLDivElement>(null);
  const modalRef = useRef<HTMLElement>(null);
  const modalBusy = useRef(false);
  modalBusy.current = busy;
  const layout = useRef<Projection | null>(null);
  const functionOrigin = useRef<{ focus: string | null; expanded: string | null; siblings: boolean } | null>(null);
  const saveTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const panel = panels.at(-1);
  const modules = project?.architecture?.modules || [];
  const activeModule = modules.find(module => module.id === focus);
  const projection = useMemo(() => project ? projectGraph(project, focus, { expandedModuleId: expandedModule, showOtherModules }) : { nodes: [], relations: [] }, [project, focus, expandedModule, showOtherModules]);
  const isFunctions = !!activeModule && !modules.some(module => module.parent_id === focus);
  const viewportKey = `${isFunctions ? 'grouped-functions' : 'vertical'}:${focus || 'root'}:${expandedModule || 'collapsed'}:${showOtherModules ? 'all' : 'focused'}:v4`;
  const confirmedCount = project?.analysis.calls.filter(call => call.resolution === 'confirmed').length || 0;

  async function refresh() {
    const [nextProjects, nextTasks] = await Promise.all([api<Project[]>('/projects'), api<AnalysisTask[]>('/tasks')]);
    setProjects(nextProjects); setAnalysisTasks(nextTasks);
  }
  async function taskAction(task: AnalysisTask, action: 'start' | 'pause' | 'cancel') {
    setBusy(true); setError('');
    try {
      await api(`/tasks/${task.id}/${action}`, {});
      await refresh();
      if (project?.id === task.project_id) setProject(await api<ProjectDetail>(`/projects/${project.id}`));
      setNotice(action === 'start' ? '任务已启动，完成后不会自动启动下一项。' : action === 'pause' ? '任务已暂停，有效检查点已保留。' : '任务已取消，已有成功结果保持可读。');
    } catch (problem) {
      const message = (problem as Error).message;
      if (action === 'start' && message.includes('授权')) {
        await openProject(task.project_id); setModal('consent');
      } else setError(message);
    }
    finally { setBusy(false); }
  }
  async function moveTask(task: AnalysisTask, direction: number) {
    const waiting = analysisTasks.filter(item => item.status === 'waiting');
    const index = waiting.findIndex(item => item.id === task.id), target = index + direction;
    if (target < 0 || target >= waiting.length) return;
    [waiting[index], waiting[target]] = [waiting[target], waiting[index]];
    setBusy(true); setError('');
    try { setAnalysisTasks(await api<AnalysisTask[]>('/tasks/order', { ids: waiting.map(item => item.id) }, 'PUT')); }
    catch (problem) { setError((problem as Error).message); }
    finally { setBusy(false); }
  }
  async function retryIndex() {
    if (!project) return;
    setError(''); setBusy(true);
    try {
      await api(`/projects/${project.id}/index`, {});
      setProject(await api<ProjectDetail>(`/projects/${project.id}`));
      await refresh();
    } catch (problem) { setError((problem as Error).message); }
    finally { setBusy(false); }
  }
  async function toggleFullscreen() {
    if (fullscreen) {
      if (document.fullscreenElement) await document.exitFullscreen();
      setFullscreen(false);
    } else {
      try { await shellRef.current?.requestFullscreen(); }
      catch { /* Embedded browsers can still expand the workspace to their full viewport. */ }
      setFullscreen(true);
    }
  }
  useEffect(() => {
    const changed = () => setFullscreen(!!document.fullscreenElement);
    const escape = (event: KeyboardEvent) => {
      if (event.key !== 'Escape' || modal) return;
      if (document.fullscreenElement) void document.exitFullscreen().catch(() => {});
      setFullscreen(false);
    };
    document.addEventListener('fullscreenchange', changed);
    document.addEventListener('keydown', escape);
    return () => { document.removeEventListener('fullscreenchange', changed); document.removeEventListener('keydown', escape); };
  }, [modal]);
  useEffect(() => {
    if (project) return;
    if (document.fullscreenElement) void document.exitFullscreen().catch(() => {});
    setFullscreen(false);
  }, [project?.id]);
  async function openSettings() {
    setError(''); setTestMessage(''); setKey('');
    try { setSettings(await api('/settings/ai')); setModal('settings'); }
    catch (problem) { setError((problem as Error).message); }
  }
  useEffect(() => {
    bootstrap().then(async () => { setAuthorized(true); await refresh(); setSettings(await api('/settings/ai')); })
      .catch(problem => setError(problem.message));
  }, []);
  useEffect(() => {
    if (!authorized) return;
    const clientId = crypto.randomUUID();
    let connection: EventSource | undefined;
    let stopped = false;
    const heartbeat = () => api('/clients/heartbeat', { client_id: clientId }).catch(() => {});
    void heartbeat().then(() => {
      if (!stopped) connection = new EventSource(`/api/v1/clients/${clientId}/events`);
    });
    const timer = setInterval(() => void heartbeat(), 20000);
    const disconnect = () => { connection?.close(); void keepalive('/clients/disconnect', { client_id: clientId }).catch(() => {}); };
    const restore = () => { void heartbeat(); if (!connection || connection.readyState === EventSource.CLOSED) connection = new EventSource(`/api/v1/clients/${clientId}/events`); };
    window.addEventListener('pagehide', disconnect);
    window.addEventListener('pageshow', restore);
    return () => {
      stopped = true; clearInterval(timer); disconnect();
      window.removeEventListener('pagehide', disconnect); window.removeEventListener('pageshow', restore);
    };
  }, [authorized]);
  useEffect(() => {
    if (!authorized) return;
    const timer = setInterval(() => {
      api<Project[]>('/projects').then(next => {
        setProjects(next);
        if (project && next.some(item => item.id === project.id && item.version_id !== project.version_id)) {
          void openProject(project.id); setNotice('新版本已发布，已切换到对应图与源码。');
        }
      }).catch(() => {});
      api<AnalysisTask[]>('/tasks').then(setAnalysisTasks).catch(() => {});
      if (project && ['indexing', 'analyzing', 'queued', 'waiting'].includes(project.status)) {
        api<ProjectDetail>(`/projects/${project.id}`).then(next => {
          setProject(next);
          if (next.status === 'ready' && project.status !== 'ready') {
            if (next.version_id !== project.version_id) void openProject(next.id);
            else { setFocus(null); setPanels([{ kind: 'overview' }]); }
            setNotice('架构分析已完成，可以开始探索。');
          }
        }).catch(problem => setError(problem.message));
      }
    }, 2000);
    return () => clearInterval(timer);
  }, [authorized, project?.id, project?.status, project?.version_id]);
  useEffect(() => {
    if (!notice) return;
    const timer = setTimeout(() => setNotice(''), 6000);
    return () => clearTimeout(timer);
  }, [notice]);
  useEffect(() => {
    if (!modal) return;
    const dialog = modalRef.current;
    const previous = document.activeElement;
    if (!dialog) return;
    const focusable = () => [...dialog.querySelectorAll<HTMLElement>('button:not(:disabled), input:not(:disabled), textarea:not(:disabled), [tabindex="0"]')]
      .filter(element => element.getClientRects().length > 0);
    const frame = requestAnimationFrame(() => {
      if (!dialog.contains(document.activeElement)) focusable()[0]?.focus();
    });
    const keyboard = (event: KeyboardEvent) => {
      if (event.key === 'Escape' && !modalBusy.current) { event.preventDefault(); setModal(null); }
      if (event.key !== 'Tab') return;
      const items = focusable(), first = items[0], last = items.at(-1);
      if (!first) { event.preventDefault(); return; }
      if (event.shiftKey && (document.activeElement === first || !dialog.contains(document.activeElement))) {
        event.preventDefault(); last?.focus();
      } else if (!event.shiftKey && (document.activeElement === last || !dialog.contains(document.activeElement))) {
        event.preventDefault(); first.focus();
      }
    };
    document.addEventListener('keydown', keyboard);
    return () => {
      cancelAnimationFrame(frame); document.removeEventListener('keydown', keyboard);
      if (previous instanceof HTMLElement && previous.isConnected) previous.focus();
    };
  }, [modal]);
  useEffect(() => {
    if (!project || panel?.kind !== 'source') return;
    let active = true;
    setSource(null);
    const fileId = project.file_ids[panel.location.path];
    if (!fileId) { setError(`找不到快照文件：${panel.location.path}`); return; }
    api<SourceFile>(`/projects/${project.id}/files/${fileId}?version_id=${project.version_id}`)
      .then(value => { if (active) setSource(value); })
      .catch(problem => { if (active) setError(problem.message); });
    return () => { active = false; };
  }, [project?.id, project?.version_id, panel]);

  async function openProject(id: string) {
    if (saveTimer.current) clearTimeout(saveTimer.current);
    functionOrigin.current = null;
    setError(''); setSource(null); setPanels([]); setViewports({}); setExpandedModule(null); setShowOtherModules(false); layout.current = null;
    try {
      const detail = await api<ProjectDetail>(`/projects/${id}`);
      const saved = await api<{ focus_id: string | null; viewport: Record<string, Viewport> | null } | null>(`/projects/${id}/session`);
      const savedFocus = saved?.focus_id && detail.architecture?.modules.some(module => module.id === saved.focus_id) ? saved.focus_id : null;
      setProject(detail);
      setFocus(savedFocus); setExpandedModule(savedFocus);
      setViewports(saved?.viewport || {});
    } catch (problem) { setError((problem as Error).message); }
  }

  function saveSession(nextFocus: string | null, nextViewports = viewports) {
    if (!project) return;
    if (saveTimer.current) clearTimeout(saveTimer.current);
    const projectId = project.id, versionId = project.version_id;
    saveTimer.current = setTimeout(() => {
      api(`/projects/${projectId}/session`, { version_id: versionId, focus_id: nextFocus,
        viewport: nextViewports, panel: null }, 'PUT').catch(problem => setError(problem.message));
    }, 500);
  }

  function changeFocus(next: string | null) {
    if (next && !modules.some(module => module.id === next) && abstractModules(modules).some(module => module.id === next)) {
      functionOrigin.current = null;
      setPanels([]); setFocus(null); setExpandedModule(next); setShowOtherModules(false);
      layout.current = null; saveSession(null);
      return;
    }
    if (next !== focus) {
      const isLeaf = !!next && !modules.some(module => module.parent_id === next);
      const parentId = isLeaf ? abstractModules(modules).find(module => module.id === next)?.parent_id || null : null;
      const previousOrigin = isFunctions && abstractModules(modules).find(module => module.id === focus)?.parent_id === parentId ? functionOrigin.current : null;
      functionOrigin.current = isLeaf ? previousOrigin || { focus: projection.nodes.some(node => node.id === next) ? focus : null,
        expanded: projection.nodes.some(node => node.id === next) ? expandedModule || parentId : parentId,
        siblings: projection.nodes.some(node => node.id === next) && showOtherModules } : null;
    }
    setPanels([]);
    setExpandedModule(null); setShowOtherModules(false);
    if (next === focus) return;
    setFocus(next); setExpandedModule(next); layout.current = null; saveSession(next);
  }
  function pushPanel(next: Panel) { setPanels(current => [...current.slice(-19), next]); }
  async function sourcePanel(entity: Entity, location = entity.location) {
    if (!project) return;
    setSource(null); pushPanel({ kind: 'source', entity, location });
  }

  function filePanel(path: string) {
    const scope = project?.analysis.scopes.find(item => item.kind === 'module' && item.location.path === path);
    setSource(null); pushPanel({kind: 'source', location: scope?.location || {path, start_line: 1, end_line: 1, start_byte: 0, end_byte: 0}});
  }

  function onNode(node: ProjectionNode) {
    if (node.kind === 'module') {
      if (node.canExpand) {
        if (node.expanded && focus && isFunctions) {
          const origin = functionOrigin.current;
          const navigationModules = abstractModules(modules);
          const parentId = navigationModules.find(module => module.id === node.id)?.parent_id || null;
          setFocus(origin?.focus || null);
          setExpandedModule(origin ? origin.expanded : parentId);
          setShowOtherModules(origin?.siblings || false);
          setPanels([]); layout.current = null; saveSession(origin?.focus || null);
          functionOrigin.current = null;
          return;
        }
        const currentExpanded = expandedModule ? modules.find(module => module.id === expandedModule) : undefined;
        const isOuterParent = !!currentExpanded && currentExpanded.parent_id === node.module?.id;
        setExpandedModule(isOuterParent ? (node.module?.parent_id || null) : node.expanded ? (node.module?.parent_id || null) : node.id);
        setShowOtherModules(false);
        setPanels([]);
      } else if (node.module && !moduleEntities(node.module, modules).length) pushPanel({kind: 'module', module: node.module});
      else changeFocus(node.id);
    }
    else if (node.entity) void sourcePanel(node.entity);
  }

  async function learning(entity: Pick<Entity, 'id'>, state: LearningState) {
    if (!project) return;
    try {
      const value = await api<{ state: LearningState; revision: number }>(`/projects/${project.id}/learning/${entity.id}`,
        { version_id: project.version_id, state, expected_revision: project.learning[entity.id]?.revision || 0 }, 'PUT');
      setProject(current => current ? { ...current, learning: { ...current.learning, [entity.id]: value },
        progress: { ...current.progress, mastered: (current.learning_entities || current.analysis.entities.filter(item => item.visible))
          .filter(item => (item.id === entity.id ? state : current.learning[item.id]?.state) === 'mastered').length } } : current);
      setNotice('学习状态已保存到本地。'); await refresh();
    } catch (problem) { setError((problem as Error).message); }
  }

  async function importProject() {
    setBusy(true); setError('');
    try {
      const rules = (text: string) => text.split('\n').map(value => value.trim()).filter(Boolean);
      if (!importPreview) {
        setImportPreview(await api<ImportPreview>(reanalysisProject ? `/projects/${reanalysisProject}/reanalysis/preview` : '/imports/preview', {
          ...(!reanalysisProject ? (importKind === 'git' ? { url: importPath } : { path: importPath }) : {}),
          excludes: rules(excludeText), includes: rules(includeText),
        }));
        return;
      }
      if (reanalysisProject) {
        await api(`/projects/${reanalysisProject}/reanalysis`, { preview_id: importPreview.preview_id });
        setProject(await api<ProjectDetail>(`/projects/${reanalysisProject}`));
        setReanalysisProject(null); setImportPreview(null); setModal('consent'); await refresh();
        return;
      }
      const created = await api<Project>('/imports', { preview_id: importPreview.preview_id });
      setModal(null); setImportPath(''); setImportPreview(null); await refresh(); await openProject(created.id);
      setNotice(created.status === 'queued' ? '源码快照已创建，解析任务等待主动启动。' : '源码快照已创建，正在进行静态解析。');
    } catch (problem) { setError((problem as Error).message); }
    finally { setBusy(false); }
  }

  function beginImport(reanalyze = false) {
    setReanalysisProject(reanalyze && project ? project.id : null);
    setImportPreview(null); setError('');
    setIncludeText(reanalyze ? project?.scope.includes?.join('\n') || '' : '');
    setExcludeText(reanalyze ? project?.scope.excludes?.join('\n') || '' : '');
    setModal('import');
  }

  async function saveSettings(test = false) {
    setBusy(true); setTestMessage('');
    try {
      const configured = await api<typeof settings>('/settings/ai', { base_url: settings.base_url, model: settings.model, key: key || null }, 'PUT');
      setSettings(configured); setKey('');
      if (test) {
        const result = await api<{ model: string; usage: { total_tokens?: number } }>('/settings/ai/test', {});
        setTestMessage(`连接成功 · ${result.model} · ${result.usage.total_tokens ?? '未知'} Tokens · 未发送源码`);
      } else setTestMessage('设置已保存，密钥使用 Windows DPAPI 加密。');
    } catch (problem) { setTestMessage((problem as Error).message); }
    finally { setBusy(false); }
  }

  async function startAI() {
    if (!project) return;
    setBusy(true);
    try {
      await api(`/projects/${project.id}/consent`, { accepted: true, version_id: project.candidate?.version_id || project.version_id });
      const started = await api<{status: string}>(`/projects/${project.id}/analyze`, {});
      setProject(await api(`/projects/${project.id}`)); setModal(null);
      await refresh();
      setNotice(started.status === 'waiting' ? '已进入等待队列，请在空闲后主动启动。' : 'AI 分析已开始，已有结果保持可读。');
    } catch (problem) { setError((problem as Error).message); }
    finally { setBusy(false); }
  }

  async function retryAI() {
    if (!project) return;
    const pausedIndex = analysisTasks.find(task => task.project_id === project.id && task.kind === 'index' && task.status === 'paused');
    if (pausedIndex) { await taskAction(pausedIndex, 'start'); return; }
    setBusy(true); setError('');
    try {
      const started = await api<{status: string}>(`/projects/${project.id}/analyze`, {});
      setProject(await api(`/projects/${project.id}`));
      await refresh();
      setNotice(started.status === 'waiting' ? '已进入等待队列，请在空闲后主动启动。' : '已恢复分批分析，有效检查点将继续使用。');
    } catch (problem) {
      const message = (problem as Error).message;
      if (message.includes('授权')) setModal('consent');
      else setError(message);
    } finally { setBusy(false); }
  }

  function download() {
    if (!layout.current || !project) return;
    const contents = exportSvg({ ...layout.current, nodes: layout.current.nodes.map(node => ({ ...node,
      state: projection.nodes.find(current => current.id === node.id)?.state || node.state })) }, project.name);
    const url = URL.createObjectURL(new Blob([contents], { type: 'image/svg+xml;charset=utf-8' }));
    const link = document.createElement('a'); link.href = url; link.download = `${project.name}-${activeModule?.name || '项目架构'}.svg`;
    link.click(); setTimeout(() => URL.revokeObjectURL(url), 1000); setNotice('当前层级完整图形已导出为 SVG。');
  }

  const breadcrumb = (() => {
    const chain = [];
    let current = activeModule;
    while (current) { chain.unshift(current); current = modules.find(module => module.id === current!.parent_id); }
    return chain;
  })();

  const moduleOrder = (() => {
    const ordered: { id: string; name: string; depth: number }[] = [];
    function visit(parent: string | null, depth: number) {
      for (const module of modules.filter(item => item.parent_id === parent)) {
        ordered.push({ id: module.id, name: module.name, depth }); visit(module.id, depth + 1);
      }
    }
    visit(null, 0); return ordered;
  })();
  const batchProgress = project?.analysis_progress;
  const analysisHint = project?.status === 'analyzing'
    ? batchProgress?.detail || '正在规划源码批次…'
    : batchProgress?.phase === 'paused' ? '本轮分批分析已暂停，可重新开始。' : '';

  return <div ref={shellRef} className={`app-shell ${fullscreen ? 'is-fullscreen' : ''}`}>
    <header className="app-header">
      <button className="brand" onClick={() => { setProject(null); setPanels([]); }} aria-label="返回项目学习库"><span className="brand-symbol"><Layers3 size={22} /></span><span>CodeScape<span className="brand-cn">源境</span></span></button>
      <div className="header-center"><span className="status-dot" />本地工作空间<span className="header-divider" />M0 开发预览</div>
      <div className="header-actions"><span className="local-badge"><ShieldCheck size={13} /> 数据本地保存</span><button className="icon-button task-trigger" aria-label="分析任务" title="分析任务" onClick={() => { setError(''); setModal('tasks'); }}><ListOrdered size={19} />{analysisTasks.some(task => task.status === 'running') && <span className="task-active-dot" />}</button><button className="icon-button" aria-label="AI 服务设置" onClick={() => void openSettings()}><Settings2 size={19} /></button></div>
    </header>
    <div className="app-body">
      <aside className="sidebar">
        <div className="sidebar-heading"><span>学习空间</span><span className="mini-label">工作空间</span></div>
        <button aria-label="项目学习库" title="项目学习库" className={`sidebar-nav ${!project ? 'active' : ''}`} onClick={() => { setProject(null); setPanels([]); }}><BookOpen size={18} />项目学习库<span className="nav-count">{projects.length}</span></button>
        <div className="project-list-label">最近的项目<button className="tiny-icon" aria-label="导入项目" onClick={() => beginImport()}><Plus size={15} /></button></div>
        <div className="sidebar-projects">{projects.map(item => <button key={item.id} aria-label={`打开项目 ${item.name}`} title={item.name} className={`sidebar-project ${project?.id === item.id ? 'selected' : ''}`} onClick={() => void openProject(item.id)}><span className="small-project-icon"><Code2 size={15} /></span><span><strong>{item.name}</strong><small>{item.status === 'analyzing' && item.analysis_progress?.total ? `AI 分析 ${item.analysis_progress.completed}/${item.analysis_progress.total} 批` : item.demo ? '预置验证样例' : item.languages.join(' / ') || statuses[item.status]}</small></span>{item.status === 'analyzing' || item.status === 'indexing' ? <span className="spinner small" /> : <ChevronRight size={13} />}</button>)}</div>
        <div className="sidebar-bottom"><div className="quiet-note"><Braces size={16} /><strong>从全景，深入源码</strong><p>每条关系都有出处。每次探索都能继续。</p></div><div className="connection"><span className="status-dot" />{settings.has_key ? 'DeepSeek 已配置' : 'AI 服务待配置'}<span>v0.1.0</span></div></div>
      </aside>
      <main className={`main ${project ? 'workspace-main' : ''}`}>
        {error && !modal && <div className="error-banner" role="alert"><AlertTriangle size={16} />{error}<button aria-label="关闭错误" onClick={() => setError('')}><X size={14} /></button></div>}
        {!project ? <div className="library">
          <div className="library-heading"><div className="library-intro"><div className="eyebrow"><span className="eyebrow-dot" />你的代码学习空间</div><h1>看见结构，<br />读懂代码<span className="heading-period">。</span></h1><p>从项目全景到一行源码，让每一次探索都有迹可循。</p><div className="hero-actions"><button className="primary-button" onClick={() => beginImport()} disabled={!authorized}><Plus size={17} />导入项目<ArrowUpRight size={17} /></button><span>本地优先 · 自由探索</span></div></div><LibraryArtwork /></div>
          <div className="library-summary"><div className="summary-metric"><strong>{String(projects.length).padStart(2, '0')}</strong><span>学习项目<small>本地项目</small></span></div><div className="summary-metric"><strong>{projects.reduce((sum, item) => sum + item.file_count, 0)}</strong><span>源码文件<small>静态快照</small></span></div><div className="summary-metric"><strong>{projects.reduce((sum, item) => sum + item.function_count, 0)}</strong><span>已识别函数<small>可探索实体</small></span></div><span className="summary-end"><ShieldCheck size={15} />数据在本机，关系有出处</span></div>
          <div className="section-heading"><div><h2>你的项目</h2><span className="section-count">{String(projects.length).padStart(2, '0')}</span></div><span>按最近导入排序</span></div>
          <div className="project-grid">{projects.map((item, index) => <button className="project-card" key={item.id} onClick={() => void openProject(item.id)}><div className="card-top"><span className="card-number">{String(index + 1).padStart(2, '0')}<span> / 项目</span></span><span className={`pill ${item.status === 'ready' ? 'ready' : ''}`}><span />{statuses[item.status] || item.status}</span></div><span className="project-icon"><GitBranch size={24} /></span><h3>{item.name}</h3><p className="card-source" title={item.source}>{item.source}</p><div className="language-chips">{item.languages.map(language => <span key={language}>{language === 'python' ? 'Python' : language === 'typescript' ? 'TypeScript' : language === 'cpp' ? 'C++' : 'C'}</span>)}{item.demo && <span className="sample-chip">预置样例</span>}</div><div className="card-stats"><span><FileCode2 size={14} />{item.file_count} 个文件</span><span><Braces size={14} />{item.function_count} 个函数</span></div><div className="learning-progress"><span style={{ width: `${item.progress.total ? item.progress.mastered / item.progress.total * 100 : 0}%` }} /></div><div className="card-footer"><span>{item.progress.total ? <><strong>{item.progress.mastered}</strong> / {item.progress.total} 已掌握</> : '暂无可统计学习内容'}</span><span className="card-open"><ArrowUpRight size={17} /></span></div></button>)}
            <button aria-label="添加项目" className="add-project-card" onClick={() => beginImport()}><span><Plus size={24} /></span><strong>下一个项目，<br />新的视野。</strong><p>导入本地源码，开始你的探索</p><span className="add-project-action">添加项目<ArrowUpRight size={15} /></span></button>
          </div>
          <div className="prototype-note"><Info size={17} /><div><strong>关于这个开发预览</strong><p>预置模块由人工划分，调用来自静态解析。导入自己的项目后，可授权 AI 生成架构。</p></div><span>M0 开发预览</span></div><div className="library-footer"><span>复杂的代码，也可以有清晰的轮廓。</span><span>CODESCAPE / 源境</span></div>
        </div> : <>
          <div className="workspace-toolbar"><div className="breadcrumbs"><button onClick={() => { setProject(null); setPanels([]); }}>学习库</button><ChevronRight size={14} /><button className={!focus ? 'current' : ''} onClick={() => changeFocus(null)}>{project.name}</button>{breadcrumb.map(module => <span className="breadcrumb-item" key={module.id}><ChevronRight size={14} /><button className={focus === module.id ? 'current' : ''} onClick={() => changeFocus(module.id)}>{module.name}</button></span>)}</div><div className="toolbar-actions"><button onClick={() => pushPanel({ kind: 'overview' })} disabled={!project.architecture}><BookOpen size={15} />项目概览</button><button onClick={download} disabled={!project.architecture}><Download size={15} />导出 SVG</button>{project.status === 'paused' && project.error && <button className="secondary-button" onClick={() => void retryAI()} disabled={busy}><Sparkles size={14} />重新开始分批分析</button>}<button className="ai-button" onClick={() => { if (project.architecture && !project.candidate) beginImport(true); else { setError(''); setModal('consent'); } }} disabled={!project.file_count || ['indexing', 'queued', 'failed', 'analyzing'].includes(project.status)}><Sparkles size={14} />{project.status === 'analyzing' ? 'AI 分析中' : 'AI 架构分析'}</button></div></div>
          <div className="module-navigation"><div className="module-selector"><Layers3 size={15} /><label htmlFor="module-switch">切换模块</label><select id="module-switch" value={focus || ''} onChange={event => changeFocus(event.target.value || null)} disabled={!modules.length}><option value="">全部模块 · 架构全景</option>{moduleOrder.map(module => <option key={module.id} value={module.id}>{'　'.repeat(module.depth)}{module.depth ? '↳ ' : ''}{module.name}</option>)}</select></div><span className="architecture-hint">{isFunctions ? '当前模块展开函数 · 可显示同级模块' : expandedModule ? '当前层级已展开 · 再次点击即可收起' : projection.abstraction ? '点击代码域逐层展开 · 信息按钮查看职责' : '点击模块展开子模块 · 信息按钮查看职责'}</span>{projection.otherModuleCount ? <button className="secondary-button compact-button" onClick={() => setShowOtherModules(current => !current)}>{showOtherModules ? '隐藏同层模块' : `显示其他模块（${projection.otherModuleCount}）`}</button> : null}<button className="fullscreen-button" onClick={() => void toggleFullscreen()} aria-label={fullscreen ? '退出全屏' : '全屏查看架构'}>{fullscreen ? <Minimize2 size={15} /> : <Maximize2 size={15} />}{fullscreen ? '退出全屏' : '全屏查看'}<kbd>{fullscreen ? 'Esc' : '↗'}</kbd></button></div>
          <div className="workspace-heading" key={focus || 'root'}><div><div className="eyebrow">{isFunctions ? '函数结构 · 深入源码' : '语义架构 · 全景探索'}</div><h1>{activeModule?.name || '项目全景'}<span className="view-tag">{isFunctions ? '函数视图' : '模块视图'}</span></h1><p>{activeModule?.summary || '理解主要模块的分工，以及它们如何协作。'}</p></div><div className="workspace-stats"><span><strong>{project.function_count}</strong>函数</span><span><strong>{confirmedCount}</strong>已识别调用</span><span><strong>{project.progress.mastered}<small> / {project.progress.total}</small></strong>已掌握</span></div></div>
          <div className="version-controls"><button className="text-button" disabled={busy || analysisTasks.some(task => task.project_id === project.id && ['running', 'waiting', 'paused'].includes(task.status))} onClick={() => beginImport(true)}>重新扫描并分析</button>{project.candidate && <span>独立候选已就绪 · 旧结果保持可读</span>}{project.migration_report && <span>学习迁移：保留 {project.migration_report.kept_count} · 重置 {project.migration_report.reset_count} · 新增 {project.migration_report.added_count} · 移除 {project.migration_report.removed_count}</span>}</div>
          <div className="graph-meta"><div>{project.demo && <span className="sample-label"><Circle size={9} />预置验证样例</span>}<span><ShieldCheck size={12} />绑定只读源码快照</span><span>{project.status === 'analyzing' ? <><span className="spinner small" />{analysisHint}{batchProgress?.total ? `（${batchProgress.completed}/${batchProgress.total} 批）` : ''}</> : project.demo && project.architecture?.overview.limitations.some(value => value.includes('人工预置')) ? '模块人工预置 · 调用静态提取' : statuses[project.status]}</span></div><button onClick={() => pushPanel({ kind: 'diagnostics' })}><AlertTriangle size={12} />范围与局限{project.diagnostic_count > 0 && <b>{project.diagnostic_count}</b>}</button></div>
          <div className="exploration">
            <div className="graph-container">
              {project.architecture ? projection.nodes.length ? <Suspense fallback={<div className="canvas-loading"><span className="spinner" />正在准备探索画布…</div>}><Canvas key={`${project.id}:${focus || 'root'}:${expandedModule || 'collapsed'}:${showOtherModules ? 'all' : 'focused'}`} projection={projection} onNode={onNode}
                onInfo={node => pushPanel(node.kind === 'module' ? { kind: 'module', module: node.module! } : { kind: 'function', entity: node.entity! })}
                onRelation={relation => pushPanel({ kind: 'relation', relation })} onLayout={value => { layout.current = value; }}
                viewport={viewports[viewportKey]} onViewport={value => { const next = { ...viewports, [viewportKey]: value }; setViewports(next); saveSession(focus, next); }} /></Suspense>
                : <div className="empty-state"><Boxes size={32} /><h3>此模块没有可展示函数</h3><p>实现可能位于顶层代码或其他隐藏作用域，详情中保留源码依据。</p><button className="secondary-button" onClick={() => activeModule && pushPanel({ kind: 'module', module: activeModule })}>查看模块详情</button></div>
                : <div className="empty-state"><span className="large-empty-icon"><Sparkles size={30} /></span><h3>{['indexing', 'queued'].includes(project.status) ? '正在建立静态源码索引' : project.status === 'failed' ? '静态索引未完成' : project.status === 'analyzing' ? '正在分批理解项目架构' : '源码已就绪，下一步理解架构'}</h3><p>{project.status === 'analyzing' ? `${analysisHint}${batchProgress?.total ? ` · 已完成 ${batchProgress.completed}/${batchProgress.total} 批` : ''}` : '静态索引不会被当作语义架构。授权后，AI 将基于当前快照归纳模块职责与整体工作原理；超大项目会自动拆成受控批次。'}</p>{project.status === 'indexed' && <button className="primary-button" onClick={() => { setError(''); setModal('consent'); }}><Sparkles size={15} />开始 AI 架构分析</button>}{project.status === 'failed' && <button className="primary-button" disabled={busy} onClick={() => void retryIndex()}>重试静态索引</button>}{project.status === 'paused' && <button className="primary-button" disabled={busy} onClick={() => void retryAI()}>{busy ? <span className="spinner" /> : <Sparkles size={15} />}重新开始分批分析</button>}{project.error && <p className="inline-error">{project.error}</p>}</div>}
              <div className="canvas-caption"><span>{isFunctions ? '点击函数阅读源码 · 点击连线查看证据' : projection.abstraction ? '点击代码域逐层展开 · 再次点击收起 · 信息按钮查看职责' : '点击父模块展开 · 再次点击收起 · 信息按钮查看职责'}</span><span>{projection.nodes.length} 节点 <span className="caption-dot">·</span> {projection.relations.filter(relation => relation.from !== relation.to).length} 条协作线{projection.relations.some(relation => relation.from === relation.to) ? ` · ${projection.relations.filter(relation => relation.from === relation.to).length} 个内部依赖` : ''}</span></div>
            </div>
            {panel && <><div className="panel-resizer" onPointerDown={event => {
              event.currentTarget.setPointerCapture(event.pointerId);
              const begin = event.clientX, initial = width;
              const move = (next: PointerEvent) => setWidth(Math.max(320, Math.min(650, initial + begin - next.clientX)));
              const finish = () => { document.removeEventListener('pointermove', move); document.removeEventListener('pointerup', finish); };
              document.addEventListener('pointermove', move); document.addEventListener('pointerup', finish);
            }} /><aside className="detail-panel" style={{ width }}>
              <div className="panel-header"><div>{panel.kind === 'source' ? <FileCode2 size={17} /> : panel.kind === 'relation' ? <GitBranch size={17} /> : <Info size={17} />}<strong>{{ overview: '项目概览', module: '模块详情', function: '函数详情', relation: '关系证据', source: '源码阅读', diagnostics: '范围与局限' }[panel.kind]}</strong></div><div>{panels.length > 1 && <button className="tiny-icon" aria-label="返回之前的面板" onClick={() => setPanels(current => current.slice(0, -1))}><ArrowLeft size={16} /></button>}<button className="tiny-icon" aria-label="关闭辅助面板" onClick={() => setPanels([])}><X size={17} /></button></div></div>
              {panel.kind === 'source' ? <><div className="source-file-heading"><span>{panel.location.path}</span><small>L{panel.location.start_line}–{panel.location.end_line} · 只读快照</small></div>{source ? <Suspense fallback={<div className="canvas-loading">正在准备源码阅读器…</div>}><SourceReader file={source} location={panel.location} /></Suspense> : <div className="canvas-loading">正在读取匹配版本的源码…</div>}<div className="source-bottom"><ShieldCheck size={12} />原始仓库不会被修改</div></> : <div className="panel-content">
                {panel.kind === 'overview' && project.architecture && <OverviewPanel key={project.id} project={project} onUpdate={updated => setProject(current => current?.id === updated.id ? updated : current)} onExplore={changeFocus} />}
                {panel.kind === 'module' && <><div className="panel-kicker">逻辑模块</div><h2>{panel.module.name}</h2><p className="lead">{panel.module.summary}</p><div className="detail-stat-row"><span>{moduleEntities(panel.module, modules).length} 个函数</span><span>{stateLabels[moduleLearningState(panel.module, project, modules)]}</span></div><h3>真实代码范围</h3>{panel.module.code_paths.map(path => <button className="source-reference" key={path} onClick={() => filePanel(path)}><FileCode2 size={14} /><span>{path}</span><ExternalLink size={14} /></button>)}{project.learning_entities?.some(unit => unit.kind === 'module' && unit.source_id === panel.module.id) && <><h3>学习状态</h3><p>本模块以可读源码和职责作为学习内容。</p><div className="learning-buttons">{(['not_started', 'in_progress', 'mastered'] as const).map(state => { const id = project.learning_entities!.find(unit => unit.kind === 'module' && unit.source_id === panel.module.id)!.id; return <button key={state} className={(project.learning[id]?.state || 'not_started') === state ? 'selected' : ''} onClick={() => void learning({id}, state)}>{stateLabels[state]}</button>; })}</div></>}{moduleEntities(panel.module, modules).length > 0 && <h3>模块中的函数</h3>}{project.analysis.entities.filter(entity => moduleEntities(panel.module, modules).includes(entity.id)).map(entity => <button className="entity-row" key={entity.id} onClick={() => void sourcePanel(entity)}><Braces size={14} /><span>{entity.name}</span><ExternalLink size={12} /></button>)}{moduleEntities(panel.module, modules).length > 0 && <button className="secondary-button wide" onClick={() => changeFocus(panel.module.id)}>进入模块<ArrowRight size={15} /></button>}</>}
                {panel.kind === 'function' && <><div className="panel-kicker">源码实体</div><h2 className="code-title">{panel.entity.name}</h2><pre className="signature">{panel.entity.signature}</pre><h3>源码位置</h3><button className="source-reference" onClick={() => void sourcePanel(panel.entity)}><FileCode2 size={15} /><span>{panel.entity.location.path}<small>第 {panel.entity.location.start_line}–{panel.entity.location.end_line} 行</small></span><ExternalLink size={14} /></button><h3>学习状态</h3><div className="learning-buttons">{(['not_started', 'in_progress', 'mastered'] as const).map(state => <button key={state} className={project.learning[panel.entity.id]?.state === state || !project.learning[panel.entity.id] && state === 'not_started' ? 'selected' : ''} onClick={() => void learning(panel.entity, state)}>{stateLabels[state]}</button>)}</div>{panel.entity.unresolved_count > 0 && <div className="diagnostic-box"><AlertTriangle size={16} /><p>此函数有 {panel.entity.unresolved_count} 处未解析或间接调用；画布没有绘制猜测边。</p></div>}<h3>相关解析信息</h3><p>语言：{panel.entity.language}</p><p>限定名：<code>{panel.entity.qualified_name}</code></p>{panel.entity.condition && <p>条件：{panel.entity.condition}</p>}</>}
                {panel.kind === 'relation' && <><div className="panel-kicker">可追溯关系</div><h2>{panel.relation.from === panel.relation.to ? '代码域内部依赖' : '源码中的直接协作'}</h2><p className="lead">{panel.relation.from === panel.relation.to ? `当前代码域内聚合了 ${panel.relation.calls.length} 处已确认调用，展开后可查看具体模块与函数。` : `该关系由 ${panel.relation.calls.length} 处已识别调用聚合。每条证据都与当前只读快照绑定。`}</p><div className="evidence-list">{panel.relation.calls.map((call, index) => {
                  const caller = project.analysis.entities.find(entity => entity.scope_id === call.caller_scope_id);
                  return <div className="evidence-card" key={call.id}><div className="evidence-card-top"><span>证据 {String(index + 1).padStart(2, '0')}</span><span className="origin-badge">{call.origin === 'static' ? '静态分析确认' : 'AI 基于源码判断'}</span></div><code>{caller?.name || '真实作用域'} → {call.target_name}</code><p>{call.reason}</p><button onClick={() => caller && void sourcePanel(caller, call.location)}><FileCode2 size={13} />{call.location.path}:{call.location.start_line}<ArrowUpRightIcon /></button></div>;
                })}</div></>}
                {panel.kind === 'diagnostics' && <><div className="panel-kicker">范围与局限</div><h2>认识分析的边界</h2><p className="lead">图中没有连线，只表示当前没有确认或显示该关系。</p><div className="detail-stat-row"><span>{project.file_count} 个纳入文件</span><span>{project.scope.excluded.length} 个排除项</span></div><h3>当前能力</h3><p>Python 名称绑定、TypeScript Compiler API、Tree-sitter 与 libclang 静态解析已接入；复杂宏配置、动态分派和跨语言接口仍需完善。</p><h3>解析诊断</h3>{project.analysis.diagnostics.length ? project.analysis.diagnostics.map((item, index) => <div className="diagnostic-entry" key={index}><strong>{item.category}</strong><p>{item.message}</p><small>{item.path}{item.line ? `:${item.line}` : ''}</small></div>) : <p>没有记录到解析器诊断，不代表覆盖所有运行时行为。</p>}<h3>未解析调用</h3>{project.analysis.calls.filter(call => ['unresolved', 'excluded_indirect'].includes(call.resolution)).map(call => <div className="diagnostic-entry" key={call.id}><code>{call.expression}</code><p>{call.reason}</p><small>{call.location.path}:{call.location.start_line}</small></div>)}<h3>排除项</h3>{project.scope.excluded.slice(0, 30).map((item, index) => <p className="excluded-entry" key={index}>{item.path}<small>{item.reason}</small></p>)}</>}
              </div>}
            </aside></>}
          </div>
        </>}
      </main>
    </div>
    {notice && <div className="toast" role="status"><Check size={16} />{notice}</div>}
    {modal && <div className="modal-backdrop" onClick={event => { if (event.target === event.currentTarget && !busy) setModal(null); }}><section ref={modalRef} className={`modal ${modal === 'tasks' ? 'task-modal' : ''}`} role="dialog" aria-modal="true" aria-label={{ import: '导入项目', settings: 'AI 服务设置', consent: '源码上传授权', tasks: '分析任务' }[modal]}><div className="modal-heading"><span className="modal-icon">{modal === 'import' ? <FolderOpen size={22} /> : modal === 'settings' ? <Settings2 size={22} /> : modal === 'tasks' ? <ListOrdered size={22} /> : <ShieldCheck size={22} />}</span><button className="tiny-icon" aria-label="关闭对话框" onClick={() => !busy && setModal(null)}><X size={19} /></button></div>{error && <div className="modal-error" role="alert"><AlertTriangle size={15} />{error}</div>}
      {modal === 'tasks' && <><h2>安排你的分析</h2><p>同时运行一个任务。完成后留在当前结果，下一项由你主动启动。</p>
        <div className="task-list">{analysisTasks.filter(task => ['waiting', 'running', 'paused'].includes(task.status)).map(task => {
          const waiting = analysisTasks.filter(item => item.status === 'waiting');
          const position = waiting.findIndex(item => item.id === task.id);
          return <article className={`task-card ${task.status}`} key={task.id} aria-label={`${task.project_name} ${taskLabels[task.status]}`}>
            <div className="task-card-heading"><strong>{task.project_name}</strong><span>{task.status === 'running' && <span className="spinner small" />}{taskLabels[task.status]}</span></div>
            <p>{task.kind === 'index' ? '建立静态索引' : 'AI 架构分析'}{task.status === 'running' && task.progress?.total ? ` · ${task.progress.completed}/${task.progress.total} 批` : ''}</p>
            <small>{task.status === 'running' ? task.progress?.detail || task.detail : task.detail}</small>
            <div className="task-card-actions">
              {task.status === 'running' ? <button className="secondary-button" disabled={busy} onClick={() => void taskAction(task, 'pause')}><Pause size={14} />暂停</button> : <button className="primary-button" disabled={busy || analysisTasks.some(item => item.status === 'running')} onClick={() => void taskAction(task, 'start')}><Play size={14} />{task.status === 'paused' ? '恢复' : '启动'}</button>}
              <button className="text-button" disabled={busy} onClick={() => void taskAction(task, 'cancel')}><Square size={12} />{task.status === 'waiting' ? '移出队列' : '取消任务'}</button>
              {task.status === 'waiting' && <span className="task-order-actions"><button className="tiny-icon" aria-label={`上移 ${task.project_name}`} disabled={busy || position === 0} onClick={() => void moveTask(task, -1)}><ArrowUp size={14} /></button><button className="tiny-icon" aria-label={`下移 ${task.project_name}`} disabled={busy || position === waiting.length - 1} onClick={() => void moveTask(task, 1)}><ArrowDown size={14} /></button></span>}
            </div>
          </article>;
        })}</div>
        {!analysisTasks.some(task => ['waiting', 'running', 'paused'].includes(task.status)) && <div className="task-empty"><Check size={22} /><strong>当前没有待执行任务</strong><span>从项目中启动分析，任务会出现在这里。</span></div>}
        {analysisTasks.some(task => !['waiting', 'running', 'paused'].includes(task.status)) && <details className="task-history"><summary>已结束任务</summary>{analysisTasks.filter(task => !['waiting', 'running', 'paused'].includes(task.status)).map(task => <div key={task.id}><span>{task.project_name}<small>{task.kind === 'index' ? '静态索引' : task.kind === 'reanalysis' ? '重新分析' : 'AI 架构'}</small></span><span>{taskLabels[task.status]}</span></div>)}</details>}
        <div className="privacy-note"><Info size={15} />暂停保留有效检查点；取消结束本轮任务。已发送的云端请求仍可能产生用量。</div>
      </>}
      {modal === 'import' && <form onSubmit={event => { event.preventDefault(); void importProject(); }}>
        <h2>{reanalysisProject ? '重新扫描项目' : '开始新的代码探索'}</h2><p>{reanalysisProject ? '从原来源读取最新源码，预览新的范围。旧结果保持可读；创建候选后再次确认上传，新结果成功才发布。' : '读取源码并创建独立快照。确认范围后建立静态索引，AI 分析需单独授权。'}</p>
        {!reanalysisProject && <div className="import-source-options" role="group" aria-label="源码来源">
          <button type="button" className={`secondary-button ${importKind === 'local' ? 'selected' : ''}`} aria-pressed={importKind === 'local'} disabled={busy} onClick={() => { setImportKind('local'); setImportPreview(null); setImportPath(''); }}>本地目录</button>
          <button type="button" className={`secondary-button ${importKind === 'git' ? 'selected' : ''}`} aria-pressed={importKind === 'git'} disabled={busy} onClick={() => { setImportKind('git'); setImportPreview(null); setImportPath(''); }}>公开 Git 仓库</button>
        </div>}
        {!reanalysisProject && <label>{importKind === 'git' ? '公开 Git URL' : '本地源码目录'}<input autoFocus disabled={busy} value={importPath} onChange={event => { setImportPath(event.target.value); setImportPreview(null); }} placeholder={importKind === 'git' ? 'https://github.com/owner/repository' : 'C:/Projects/my-project'} /></label>}
        {!reanalysisProject && importKind === 'git' && <p className="import-hint">读取默认分支，保存实际 Commit；不运行仓库脚本，不拉取子模块或 LFS 对象。</p>}
        <label>额外排除规则<span>每行一条；* 匹配一层，** 匹配任意层</span><textarea disabled={busy} rows={2} value={excludeText} onChange={event => { setExcludeText(event.target.value); setImportPreview(null); }} placeholder={'generated/**\n**/legacy/**'} /></label>
        <label>明确包含规则<span>测试、示例和第三方源码默认排除；可在此包含</span><textarea disabled={busy} rows={2} value={includeText} onChange={event => { setIncludeText(event.target.value); setImportPreview(null); }} placeholder={'tests/**\nexamples/**'} /></label>
        {importPreview && <section className="import-preview" aria-label="扫描范围预览"><div className="import-preview-summary"><strong>{importPreview.name}</strong><span>{importPreview.file_count} 个源码文件 · {(importPreview.size / 1024).toFixed(1)} KB</span></div>
          {importPreview.origin.commit && <p>默认分支 {importPreview.origin.default_branch}<br /><code title={importPreview.origin.commit}>Commit {importPreview.origin.commit.slice(0, 12)}</code></p>}
          {importPreview.file_count > 200 && <p className="import-hint">源码规模较大，AI 分析将分批进行；可以调整规则缩小范围。</p>}
          <details><summary>纳入文件（{importPreview.file_count}）</summary><ul>{importPreview.files.map(path => <li key={path}>{path}</li>)}</ul></details>
          <details><summary>排除项（{importPreview.excluded.length}）</summary><ul>{importPreview.excluded.map((item, index) => <li key={index}><code>{item.path}</code><span>{item.reason}</span></li>)}</ul></details>
          <p className="import-hint">导入使用本次扫描的快照。原目录之后发生变化不会替换它；改变范围后请重新扫描。</p>
        </section>}
        <div className="privacy-note"><ShieldCheck size={15} />只读取源码，不执行原项目。云端上传需确认接收端和范围。</div>
        <button type="submit" className="primary-button wide" disabled={busy || (!reanalysisProject && !importPath.trim())}>{busy ? <span className="spinner" /> : <FolderOpen size={16} />}{busy ? '正在处理源码…' : importPreview ? reanalysisProject ? '确认候选范围，继续上传授权' : '确认导入并建立静态索引' : '扫描并预览范围'}</button>
      </form>}
      {modal === 'settings' && <form onSubmit={event => event.preventDefault()}><h2>连接你的 AI 服务</h2><p>以 DeepSeek 为主要适配服务，支持兼容接口。密钥只在本机加密保存。</p><label>服务地址<input value={settings.base_url} onChange={event => setSettings({ ...settings, base_url: event.target.value })} /></label><label>模型名称<input value={settings.model} onChange={event => setSettings({ ...settings, model: event.target.value })} placeholder="deepseek-flash" /></label><label>API Key<span>{settings.has_key ? '已加密保存 · 留空保留现有密钥' : '尚未配置'}</span><input type="password" autoComplete="off" value={key} onChange={event => setKey(event.target.value)} placeholder={settings.has_key ? '••••••••••••••••' : '输入 API Key'} /></label>{testMessage && <div className="test-message" role="status">{testMessage}</div>}<div className="modal-actions"><button type="button" className="secondary-button" disabled={busy} onClick={() => void saveSettings(true)}>{busy ? <span className="spinner" /> : <Activity size={15} />}测试连接</button><button type="button" className="primary-button" disabled={busy} onClick={() => void saveSettings()}>保存设置<Check size={15} /></button></div><div className="privacy-note"><ShieldCheck size={14} />连接测试不发送源码，可能产生少量 Token 用量。</div></form>}
      {modal === 'consent' && project && <><h2>让 AI 理解这个项目</h2><p>将当前快照范围内的源码、实体索引与已识别调用发送到你配置的服务，生成中文项目概览和语义模块架构。</p><div className="consent-summary"><span>项目<strong>{project.name}</strong></span><span>接收端<strong>{settings.base_url}</strong></span><span>模型<strong>{settings.model || '尚未配置'}</strong></span><span>范围<strong>{project.candidate ? `${project.candidate.file_count} 个候选源码文件 · 新快照静态索引将在授权后建立` : `${project.file_count} 个源码文件 · ${project.function_count} 个函数`}</strong></span></div><div className="privacy-note"><Info size={15} />只使用所选模型。已有结果保持可读，费用按实际用量记录。超出单批上下文时会自动拆分为受控批次，并逐批校验函数归属。</div><button className="primary-button wide" disabled={busy || !settings.has_key || !settings.model} onClick={() => void startAI()}>{busy ? <span className="spinner" /> : <Sparkles size={16} />}同意上传当前源码并开始分批分析</button>{!settings.has_key && <button className="text-button" onClick={() => void openSettings()}>先配置 AI 服务</button>}</>}
    </section></div>}
  </div>;
}

function ArrowUpRightIcon() { return <ExternalLink size={12} />; }

function LibraryArtwork() {
  return <div className="library-artwork" aria-hidden="true">
    <span className="artwork-coordinate top">图 01 / 代码的轮廓</span>
    <svg viewBox="0 0 400 280" fill="none">
      <defs><pattern id="atlas-grid" width="20" height="20" patternUnits="userSpaceOnUse"><circle cx="1" cy="1" r=".6" fill="#d0d0d0" /></pattern></defs>
      <rect x="30" y="25" width="340" height="230" fill="url(#atlas-grid)" />
      <path d="M30 40V25h15m310 0h15v15M30 240v15h15m310 0h15v-15" stroke="#b8b8b8" />
      <path d="M127 58 274 91 314 184 233 228 86 195 46 102Z" stroke="#dedede" />
      <path d="M147 78 254 102 287 175 229 207 107 179 74 109Z" stroke="#e6e6e6" />
      <path className="artwork-route" d="M100 138H153M222 122H277V76h33M222 146H310M222 170H277v48h33" stroke="#696969" strokeWidth="1.2" />
      <g className="artwork-layer top">
        <path d="m145 99 57-22 58 22-58 22-57-22Z" fill="#f1edff" stroke="#d9cff5" />
        <path d="m169 99 33-12 34 12-34 12-33-12Z" stroke="#6f519f" />
      </g>
      <g className="artwork-layer bottom">
        <path d="m145 180 57-22 58 22-58 22-57-22Z" fill="#edf2ff" stroke="#cbd7f5" />
        <path d="m176 181 26 9 26-9" stroke="#405ea9" />
      </g>
      <path d="m145 117 57-22 58 22v44l-58 22-57-22v-44Z" fill="#191919" />
      <path d="m145 117 57 22 58-22m-58 22v44" stroke="#555" />
      <path d="m171 143 13 5m-13 3 24 9m23-12 17-7m-17 15 11-4" stroke="white" strokeWidth="1.5" strokeLinecap="round" />
      <rect x="47" y="112" width="53" height="53" rx="7" fill="white" stroke="#d6d6d6" />
      <path d="m61 127-5 5 5 5m23-10 5 5-5 5m-18 10h17m-17 6h10" stroke="#737373" strokeWidth="1.2" />
      <rect x="310" y="63" width="40" height="27" rx="6" fill="#f1edff" stroke="#d9cff5" />
      <path d="M321 74h18m-18 6h11" stroke="#6f519f" />
      <rect x="310" y="133" width="40" height="27" rx="6" fill="#fff4e6" stroke="#efd8b6" />
      <path d="m322 142 8 4-8 4m12 0h5" stroke="#96621f" />
      <rect x="310" y="205" width="40" height="27" rx="6" fill="#edf2ff" stroke="#cbd7f5" />
      <path d="M321 214h18m-18 6h11" stroke="#405ea9" />
      <circle cx="127" cy="58" r="3" fill="#fafafa" stroke="#aaa" />
      <circle cx="86" cy="195" r="3" fill="#fafafa" stroke="#aaa" />
      <circle cx="274" cy="91" r="2" fill="#aaa" />
      <circle cx="233" cy="228" r="2" fill="#aaa" />
      <path d="M46 208v7m-3-3h6M356 107v7m-3-3h6" stroke="#999" />
    </svg>
    <span className="artwork-coordinate bottom">从一行源码，展开整个世界。</span>
  </div>;
}
