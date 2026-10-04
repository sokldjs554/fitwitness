import {useEffect,useState} from 'react';
import {api} from '../types';
type Trial={provider:string;mode:string;repeat:number;state:string;latency_ms:number;error:string|null;metrics:{correct:number;total:number;accuracy:number};result:{usage:{model_calls:number;tool_calls:number;cost_usd:string|null;reserved_cost_usd:string}};events:{seq:number;kind:string;payload:Record<string,unknown>}[]};
type Report={status:string;runs:Trial[];limitations:string[];protocol:{scope:string;model:string}};
export function AgentTrials(){
 const [report,setReport]=useState<Report|null>(null),[selected,setSelected]=useState(0);
 useEffect(()=>{let live=true;api<Report>('/evaluations/agent').then(r=>live&&setReport(r)).catch(()=>{});return()=>{live=false;};},[]);
 if(report?.status!=='measured')return null;
 const trial=report.runs[selected];
 return <section className="comparison agent-trials" aria-label="실제 Agent 실행 비교">
  <div className="section-bar"><h2>실제 Agent 실행 비교</h2><a href="/api/evaluations/agent" download="agent-trials.json">실행 기록 JSON ↓</a></div>
  <p className="agent-caption">검색 → 도구 선택 → 근거 조회 → 조건 검증. 행을 선택하면 실제 호출 기록을 볼 수 있습니다.</p>
  <div className="table-scroll"><table><thead><tr><th>구조 / 반복</th><th>판정 일치</th><th>상태</th><th>모델 / 도구 호출</th><th>총 지연</th><th>API 비용</th></tr></thead><tbody>{report.runs.map((r,i)=><tr key={i} aria-selected={selected===i}><td><button className="text-button" onClick={()=>setSelected(i)}>{r.provider==='rules'?'규칙 기준선':r.mode} · {r.repeat+1}</button></td><td>{r.metrics.correct}/{r.metrics.total}</td><td>{r.state}</td><td>{r.result.usage.model_calls} / {r.result.usage.tool_calls}</td><td>{(r.latency_ms/1000).toFixed(2)} s</td><td>{r.result.usage.cost_usd==null?'—':`$${Number(r.result.usage.cost_usd).toFixed(4)}`}</td></tr>)}</tbody></table></div>
  {trial&&<details key={selected} open><summary>{trial.provider} / {trial.mode} · 관측 기록</summary>{trial.error&&<p role="alert">{trial.error}</p>}<div className="agent-events">{trial.events.filter(e=>['model','tool','agent_plan','retry_wait','model_error','verified'].includes(e.kind)).map(e=><details key={e.seq}><summary><code>{e.seq.toString().padStart(2,'0')}</code> {e.kind} {String(e.payload.role||e.payload.name||'')}{e.payload.latency_ms!=null?` · ${(Number(e.payload.latency_ms)/1000).toFixed(2)}s`:''}</summary><pre>{JSON.stringify(e.payload,null,2)}</pre></details>)}</div></details>}
  <div className="agent-caption">{report.limitations.map(x=><p key={x}>{x}</p>)}</div>
 </section>;
}
