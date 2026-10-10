import type {LearningState, ProjectDetail} from './types';

export type LearningValue = {state: LearningState; revision: number};
type Entry = {confirmed: LearningValue; pending?: LearningState; desired?: LearningState; running: boolean};
type Scope = {id: string; version: string};
export class LearningWrites {
  private entries = new Map<string, Entry>();
  constructor(private put: (scope:Scope,id:string,state:LearningState,revision:number)=>Promise<LearningValue>,
    private read: (scope:Scope,id:string)=>Promise<LearningValue>,
    private change: (scope:Scope,id:string,value:LearningValue)=>void,
    private report: (scope:Scope,message:string,error?:unknown)=>void) {}

  private key(scope:Scope,id:string) {return `${scope.id}:${scope.version}:${id}`;}
  choose(scope:Scope,id:string,state:LearningState,current?:LearningValue) {
    const key = this.key(scope,id);
    let entry = this.entries.get(key);
    if (!entry) {entry = {confirmed:current || {state:'not_started',revision:0},running:false};this.entries.set(key,entry);}
    if (!entry.running && current && current.revision > entry.confirmed.revision) entry.confirmed = current;
    entry.desired = state; entry.pending = state;
    this.change(scope,id,{state,revision:entry.confirmed.revision});
    if (!entry.running) void this.send(scope,id,entry);
  }
  overlay(scope:Scope,states:Record<string,LearningValue>) {
    const result = {...states};
    for (const [key,entry] of this.entries) {
      const prefix = `${scope.id}:${scope.version}:`;
      if (!key.startsWith(prefix)) continue;
      const id = key.slice(prefix.length), remote = states[id] || {state:'not_started' as const,revision:0};
      if (!entry.running && remote.revision > entry.confirmed.revision) entry.confirmed = remote;
      result[id] = entry.running && entry.desired ? {state:entry.desired,revision:entry.confirmed.revision}
        : remote.revision >= entry.confirmed.revision ? remote : entry.confirmed;
    }
    return result;
  }
  private async send(scope:Scope,id:string,entry:Entry) {
    entry.running = true;
    try {
      while (entry.pending !== undefined) {
        const desired = entry.pending; entry.pending = undefined;
        entry.confirmed = await this.put(scope,id,desired,entry.confirmed.revision);
      }
      entry.desired = undefined;
      this.change(scope,id,entry.confirmed);
      this.report(scope,'学习状态已保存到本地。');
    } catch(error) {
      // Never silently retry a conflicting or uncertain write with a newer revision.
      entry.pending = undefined; entry.desired = undefined;
      try {entry.confirmed = await this.read(scope,id);} catch { /* Roll back to the last acknowledged value. */ }
      entry.pending = undefined; entry.desired = undefined;
      this.change(scope,id,entry.confirmed);
      this.report(scope,'学习状态保存未完成，已恢复确认状态；请检查后重新选择。',error);
    } finally {entry.running = false;}
  }
}

export function withLearning(project:ProjectDetail,learning:Record<string,LearningValue>):ProjectDetail {
  const units = project.learning_entities || project.analysis.entities.filter(item=>item.visible);
  return {...project,learning,progress:{total:units.length,mastered:units.filter(item=>learning[item.id]?.state === 'mastered').length}};
}
