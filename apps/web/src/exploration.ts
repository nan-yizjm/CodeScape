import { abstractModules } from './graph';
import type {Location, Panel, ProjectDetail, ScrollPosition} from './types';
import type {Viewport} from '@xyflow/react';

export type SavedPanel = {kind: Panel['kind']; module_id?: string; entity_id?: string; relation_id?: string;
  from_id?: string; to_id?: string; call_ids?: string[]; location?: Location; scroll?: ScrollPosition};
export type SessionValue = {version_id: string; navigation_version?:number; focus_id: string | null; expanded_id?: string | null;
  show_other_modules?: boolean; dependency_mode?: 'primary' | 'all'; function_origin?: {focus: string | null; expanded: string | null; siblings: boolean} | null;
  viewport: Record<string,Viewport> | null; panels?: SavedPanel[]; expanded_evidence?: Record<string,boolean>; panel_width?: number; revision?: number};

export function panelKey(panel: Panel): string {
  return panel.kind === 'source' ? `source:${panel.location.path}:${panel.location.start_line}:${panel.location.end_line}`
    : panel.kind === 'module' ? `module:${panel.module.id}` : panel.kind === 'function' ? `function:${panel.entity.id}`
    : panel.kind === 'relation' ? `relation:${panel.relation.id}` : panel.kind;
}

export function serializePanels(panels: Panel[]): SavedPanel[] {
  return panels.slice(-20).map(panel => ({kind:panel.kind, ...(panel.scroll ? {scroll:panel.scroll} : {}),
    ...(panel.kind === 'module' ? {module_id:panel.module.id} : panel.kind === 'function' ? {entity_id:panel.entity.id}
      : panel.kind === 'source' ? {entity_id:panel.entity?.id, location:panel.location}
      : panel.kind === 'relation' ? {relation_id:panel.relation.id, from_id:panel.relation.from, to_id:panel.relation.to, call_ids:panel.relation.calls.map(call=>call.id)} : {})}));
}

export function restorePanels(saved: SavedPanel[], project: ProjectDetail): Panel[] {
  const modules = new Map(abstractModules(project.architecture?.modules || [],project.analysis).map(module=>[module.id,module]));
  const entities = new Map(project.analysis.entities.map(entity=>[entity.id,entity]));
  const calls = new Map(project.analysis.calls.filter(call=>call.resolution === 'confirmed').map(call=>[call.id,call]));
  return saved.slice(-20).flatMap((panel): Panel[] => {
    const scroll = panel.scroll || undefined;
    if (panel.kind === 'overview' || panel.kind === 'diagnostics') return [{kind:panel.kind,scroll}];
    if (panel.kind === 'module' && panel.module_id && modules.has(panel.module_id)) return [{kind:'module',module:modules.get(panel.module_id)!,scroll}];
    if (panel.kind === 'function' && panel.entity_id && entities.has(panel.entity_id)) return [{kind:'function',entity:entities.get(panel.entity_id)!,scroll}];
    if (panel.kind === 'source' && panel.location && project.file_ids[panel.location.path]) return [{kind:'source', location:panel.location, entity:panel.entity_id ? entities.get(panel.entity_id) : undefined,scroll}];
    if (panel.kind === 'relation' && panel.relation_id && panel.from_id && panel.to_id) {
      const found = (panel.call_ids || []).flatMap(id=>calls.has(id) ? [calls.get(id)!] : []);
      const primaryReasons = [...new Set((project.architecture?.primary_dependencies || []).filter(item => item.call_ids.some(id => found.some(call => call.id === id))).map(item => item.reason))];
      if (found.length && found.length === panel.call_ids?.length) return [{kind:'relation',relation:{id:panel.relation_id,from:panel.from_id,to:panel.to_id,label:'调用协作',calls:found,primaryReasons},scroll}];
    }
    return [];
  });
}
