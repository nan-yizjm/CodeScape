import { useCallback, useEffect, useRef, useState } from 'react';
import { api, ApiError, keepalive } from './api';
import type {SessionValue} from './exploration';

export function useSessionSaver(project: {id: string; version_id: string} | null, value: Omit<SessionValue,'version_id'>,
  onVersion: (id: string)=>void, slot = 'single') {
  const [status,setStatus] = useState<'saved'|'pending'|'saving'|'conflict'|'error'>('saved');
  const [epoch,setEpoch] = useState(0);
  const state = useRef<{key:string; id:string; version:string; revision:number; signature:string; first:boolean; blocked:boolean} | null>(null);
  const pending = useRef<{key:string; id:string; value:SessionValue; signature:string} | null>(null);
  const serial = useRef<Promise<void>>(Promise.resolve());
  const inFlight = useRef(false);
  const timer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  const onVersionRef = useRef(onVersion); onVersionRef.current = onVersion;
  const signature = JSON.stringify(value);
  const initialize = useCallback((id:string,version:string,revision:number) => {
    state.current = {key:`${id}:${version}`,id,version,revision,signature:'',first:true,blocked:false};
    pending.current = null; setStatus('saved'); setEpoch(value=>value+1);
  },[]);
  const flush = useCallback(async () => {
    clearTimeout(timer.current);
    const item = pending.current; pending.current = null;
    if (!item) return serial.current;
    serial.current = serial.current.then(async () => {
      const current = state.current;
      if (!current || current.key !== item.key || current.blocked) return;
      inFlight.current = true; setStatus('saving');
      try {
        const saved = await api<SessionValue>(`/projects/${item.id}/versions/${item.value.version_id}/workspaces/${slot}`, {...item.value,expected_revision:current.revision}, 'PUT');
        if (state.current?.key === item.key) {
          current.revision = saved.revision || 0; current.signature = item.signature;
          setStatus(pending.current ? 'pending' : 'saved');
        }
      } catch(problem) {
        if (state.current?.key !== item.key) return;
        current.blocked = true;
        if (problem instanceof ApiError && problem.code === 'VERSION_CHANGED') onVersionRef.current(item.id);
        else setStatus(problem instanceof ApiError && problem.code === 'SESSION_CONFLICT' ? 'conflict' : 'error');
      } finally {inFlight.current = false;}
    });
    return serial.current;
  },[]);
  useEffect(() => {
    const current = state.current;
    if (!project || !current || current.key !== `${project.id}:${project.version_id}`) return;
    if (current.first) {current.first = false; current.signature = signature; return;}
    if (current.blocked) return;
    if (!inFlight.current && signature === current.signature) {pending.current = null; setStatus('saved'); return;}
    pending.current = {key:current.key,id:project.id,value:{...JSON.parse(signature),version_id:project.version_id},signature};
    setStatus('pending'); timer.current = setTimeout(()=>void flush(),500);
    return ()=>clearTimeout(timer.current);
  },[project?.id,project?.version_id,signature,epoch,flush]);
  useEffect(() => {
    const leave = () => {
      const item = pending.current, current = state.current;
      if (item && current && !inFlight.current && !current.blocked && item.key === current.key)
        void keepalive(`/projects/${item.id}/versions/${item.value.version_id}/workspaces/${slot}`,{...item.value,expected_revision:current.revision},'PUT').catch(()=>{});
    };
    window.addEventListener('pagehide',leave);
    return ()=>window.removeEventListener('pagehide',leave);
  },[]);
  return {initialize,flush,status,epoch};
}
