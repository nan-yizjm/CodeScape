import { useEffect, useState } from 'react';
import { api } from './api';

type Receipt = {id: string; model: string; status: string; phase: string; created_at: string;
  usage: {total_tokens?: number} | null; cost: string | null; error_code: string | null};
type Usage = {request_count: number; known_total_tokens: number; total_tokens: number | null;
  unknown_usage_count: number; outcome_unknown_count: number; cost: string | null;
  records: Receipt[]; records_offset: number; next_offset: number | null};
const states: Record<string,string> = {prepared:'待发送', sent:'已发送，等待结果', success:'成功', failed:'失败', outcome_unknown:'远端结果未知'};
const phases: Record<string,string> = {connection:'无源码连接测试', architecture:'架构归纳', source_read:'源码补读', overview:'全局概览', explanation:'函数讲解', relation:'关系复核', repair:'结构修复'};

export function RequestUsage() {
  const [usage, setUsage] = useState<Usage | null>(null);
  const [offset, setOffset] = useState(0);
  const [error, setError] = useState('');
  useEffect(() => {
    let active = true;
    const load = () => api<Usage>(`/usage?offset=${offset}`).then(value => {
      if (active) { setUsage(value); setError(''); }
    }).catch(() => { if (active) setError('请求记录暂时无法读取，请稍后重试。'); });
    void load();
    const timer = setInterval(() => void load(), 3000);
    return () => { active = false; clearInterval(timer); };
  }, [offset]);
  return <details className="request-usage"><summary>AI 请求与用量{usage ? ` · ${usage.request_count} 次` : ''}</summary>
    {error && <p role="alert">{error}</p>}
    {usage && <>
      <div className="usage-stats"><span>Token 总量<strong>{usage.total_tokens === null ? '未知' : usage.total_tokens.toLocaleString()}</strong></span><span>已知 Token<strong>{usage.known_total_tokens.toLocaleString()}</strong></span><span>费用<strong>{usage.cost === null ? '未知' : usage.cost}</strong></span></div>
      <p className="usage-note">{usage.request_count ? `${usage.unknown_usage_count} 次用量未返回，${usage.outcome_unknown_count} 次远端结果不确定。` : '本版本启用账本后尚未发送请求。'}缺少服务用量或适用价格时显示未知；费用以服务账单为准。</p>
      {usage.records.map(record => <div className="receipt-row" key={record.id}><span><strong>{phases[record.phase] || 'AI 分析'}</strong><small>{record.model} · {new Date(record.created_at).toLocaleString('zh-CN')}</small></span><span className={record.status === 'outcome_unknown' ? 'uncertain' : ''}>{states[record.status] || record.status}<small>{record.usage?.total_tokens === undefined ? 'Token 未知' : `${record.usage.total_tokens.toLocaleString()} Tokens`} · 费用{record.cost ?? '未知'}</small></span></div>)}
      {(offset > 0 || usage.next_offset !== null) && <div className="receipt-pagination"><button className="text-button" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset-20))}>上一页</button><span>{Math.floor(offset/20)+1}</span><button className="text-button" disabled={usage.next_offset === null} onClick={() => usage.next_offset !== null && setOffset(usage.next_offset)}>下一页</button></div>}
    </>}
  </details>;
}
