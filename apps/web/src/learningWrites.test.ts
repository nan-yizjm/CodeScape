import {describe,expect,it} from 'vitest';
import {LearningWrites, type LearningValue} from './learningWrites';

const scope={id:'project',version:'v'};
function deferred<T>() {let resolve!:(value:T)=>void;let reject!:(error:unknown)=>void; const promise=new Promise<T>((yes,no)=>{resolve=yes;reject=no;});return {promise,resolve,reject};}
const settle=async()=>{await Promise.resolve();await Promise.resolve();await Promise.resolve();};
describe('学习状态并发',()=>{
  it('立即反馈，合并连续选择并使用上一成功修订串行提交',async()=>{
    const first=deferred<LearningValue>(),second=deferred<LearningValue>();
    const writes:{state:string;revision:number}[]=[],changes:LearningValue[]=[],messages:string[]=[];
    const client=new LearningWrites(async(_,__,state,revision)=>{writes.push({state,revision});return writes.length===1 ? first.promise:second.promise;},
      async()=>({state:'not_started',revision:0}),(_,__,value)=>changes.push(value),(_,message)=>messages.push(message));
    client.choose(scope,'f','in_progress');client.choose(scope,'f','not_started');client.choose(scope,'f','mastered');
    expect(changes.at(-1)?.state).toBe('mastered');expect(writes).toEqual([{state:'in_progress',revision:0}]);
    expect(client.overlay(scope,{f:{state:'not_started',revision:0}}).f.state).toBe('mastered');
    first.resolve({state:'in_progress',revision:1});await settle();
    expect(writes).toEqual([{state:'in_progress',revision:0},{state:'mastered',revision:1}]);
    second.resolve({state:'mastered',revision:2});await settle();
    expect(changes.at(-1)).toEqual({state:'mastered',revision:2});expect(messages).toHaveLength(1);
  });
  it('冲突读取另一页面的确认状态，丢弃等待选择且不自动覆盖',async()=>{
    const first=deferred<LearningValue>(),changes:LearningValue[]=[];let calls=0;
    const client=new LearningWrites(async()=>{calls++;return first.promise;},async()=>({state:'mastered',revision:4}),
      (_,__,value)=>changes.push(value),()=>{});
    client.choose(scope,'f','in_progress');client.choose(scope,'f','not_started');
    first.reject(new Error('冲突'));await settle();
    expect(calls).toBe(1);expect(changes.at(-1)).toEqual({state:'mastered',revision:4});
    client.choose(scope,'f','in_progress',{state:'mastered',revision:4});expect(calls).toBe(2);
  });
  it('离线失败回滚，其他项目同名实体不受影响，旧轮询不抹掉已确认值',async()=>{
    const first=deferred<LearningValue>(),changes:{project:string;value:LearningValue}[]=[];
    const client=new LearningWrites(async()=>first.promise,async()=>{throw new Error('离线');},
      (where,_,value)=>changes.push({project:where.id,value}),()=>{});
    client.choose(scope,'f','mastered',{state:'in_progress',revision:2});
    expect(client.overlay({id:'another',version:'v'},{f:{state:'not_started',revision:0}}).f.state).toBe('not_started');
    first.reject(new Error('断网'));await settle();
    expect(changes.at(-1)?.value).toEqual({state:'in_progress',revision:2});
    expect(client.overlay(scope,{f:{state:'not_started',revision:0}}).f.revision).toBe(2);
  });
  it('冲突读取进行时的选择也回滚，必须看到新值后主动重试',async()=>{
    const recovery=deferred<LearningValue>();let calls=0;const changes:LearningValue[]=[];
    const client=new LearningWrites(async()=>{calls++;throw new Error('修订冲突');},async()=>recovery.promise,
      (_,__,value)=>changes.push(value),()=>{});
    client.choose(scope,'f','mastered');await settle();
    client.choose(scope,'f','not_started');recovery.resolve({state:'in_progress',revision:8});await settle();
    expect(calls).toBe(1);expect(changes.at(-1)).toEqual({state:'in_progress',revision:8});
    expect(client.overlay(scope,{f:{state:'in_progress',revision:8}}).f.state).toBe('in_progress');
  });
});
