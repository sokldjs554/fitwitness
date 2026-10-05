import {useEffect,useState} from 'react';
import {api} from '../types';
type Annotation={field:string;value:string|null;unit:string|null;visible_text:string;bbox:number[]};
type Trial={case_id:string;repeat:number;status:string;error:string|null;reading:{annotations:Annotation[]}|null;fields:Record<string,boolean>;events:{kind:string;payload:{provider_response_id?:string;raw_output?:string;parse_error?:string}}[];metadata:{provider_response_id:string;usage:{input_tokens:number;output_tokens:number}}|null};
type Report={status:string;protocol:{repeats:number;cases:{id:string;variant:string;image_data_url:string;expected:Record<string,string|null>}[]};rows:Trial[];metrics:{attempts:number;completed:number;field_accuracy:number;known_cost_usd:string;unresolved_reserved_usd:string;latency_p50_ms:number;latency_p95_ms:number};graph:{visual_calls:number;run:{state:string;decisions:{verdict:string}[]}};limitations:string[]};
const fields:Record<string,string>={width:'너비',material:'재질'};
const variants:Record<string,string>={original:'원본',missing_material:'재질 표기 누락',no_annotations:'주석 없는 형상'};

export function VisionTrials(){
 const [failedImage,setFailedImage]=useState('');
 const [report,setReport]=useState<Report|null>(null),[caseId,setCaseId]=useState(''),[repeat,setRepeat]=useState(1),[error,setError]=useState(false);
 useEffect(()=>{let alive=true;api<Report>('/evaluations/vision').then(r=>{if(alive)setReport(r)}).catch(()=>{if(alive)setError(true)});return()=>{alive=false}},[]);
 if(error)return <p role="status">이미지 읽기 기록을 불러오지 못했습니다.</p>;
 if(!report)return <p role="status">이미지 읽기 기록 확인 중</p>;
 if(report.status!=='measured')return <p role="status">아직 측정된 이미지 읽기 결과가 없습니다.</p>;
 const selected=report.protocol.cases.find(c=>c.id===caseId)||report.protocol.cases[0];
 if(!selected)return <section className="retrieval-trials" aria-label="도면 이미지 읽기"><h2>도면 이미지 읽기</h2><p role="status">기록된 이미지 읽기 사례가 없습니다.</p></section>;
 const row=report.rows.find(r=>r.case_id===selected.id&&r.repeat===repeat);
 const failed=row?.events.find(e=>e.kind==='model_schema_error')?.payload;
 const m=report.metrics;
 return <section className="retrieval-trials" aria-label="도면 이미지 읽기">
  <div className="section-heading"><div><span className="kicker">VISION / RECORDED</span><h2>그림에 없는 값도 읽었다고 할까?</h2><p>원본·재질 누락·주석 없는 이미지에서 실제 Claude의 관측을 비교합니다.</p></div><a className="secondary" href="/api/evaluations/vision" download>측정 기록 JSON</a></div>
  <div className="retrieval-meta"><span>Claude Haiku 4.5</span><span>PNG {report.protocol.cases.length}종 × {report.protocol.repeats}회</span><span>값·누락 정답률 {(m.field_accuracy*100).toFixed(1)}%</span><span>완료 {m.completed}/{m.attempts}</span><span>측정 비용 ${Number(m.known_cost_usd).toFixed(4)}</span></div>
  <p className="retrieval-note">저장된 실측입니다. 이미지에서 읽은 값은 검증 전 관측으로 남으며, 이것만으로 부품의 조건 일치를 확정하지 않습니다. p50 / p95 {(m.latency_p50_ms/1000).toFixed(2)} / {(m.latency_p95_ms/1000).toFixed(2)}초. 미확정 예약 ${Number(m.unresolved_reserved_usd).toFixed(4)}.</p>
  <label className="retrieval-filter">입력 선택 <select aria-label="이미지 읽기 사례" value={selected.id} onChange={e=>setCaseId(e.target.value)}>{report.protocol.cases.map(c=><option key={c.id} value={c.id}>{c.id.slice(0,7)} · {variants[c.variant]}</option>)}</select></label>
  <div className="vision-case">{failedImage===selected.image_data_url?<p role="status">입력 이미지를 불러오지 못했습니다.</p>:<img src={selected.image_data_url} alt="실제 VLM에 전달한 도면 이미지" onError={()=>setFailedImage(selected.image_data_url)}/>}<div>
   <label className="retrieval-filter">반복 <select aria-label="이미지 읽기 반복" value={repeat} onChange={e=>setRepeat(Number(e.target.value))}>{Array.from({length:report.protocol.repeats},(_,i)=><option key={i} value={i+1}>#{i+1}</option>)}</select></label>
   {!row?<p role="status">선택한 반복의 측정 기록이 없습니다.</p>:row.status==='error'?<p role="status">호출 실패: {row.error}</p>:<table><thead><tr><th>주석</th><th>정답</th><th>모델 관측 · 검증 전</th><th>대조</th></tr></thead><tbody>{Object.entries(selected.expected).map(([field,truth])=>{const annotations=row?.reading?.annotations.filter(a=>a.field===field&&a.value!==null)||[];return <tr key={field}><th>{fields[field]}</th><td>{truth??'표기 없음'}</td><td>{annotations.length?annotations.map(a=>`${a.value} ${a.unit||''}`).join(' / '):'읽은 값 없음'}</td><td>{row?.fields[field]?'정답과 일치':'오류'}</td></tr>})}</tbody></table>}
   <p className="retrieval-note">위 대조는 추출 평가입니다. 실제 업무 판정의 “조건 일치”와 다릅니다.</p>
   <details className="protocol"><summary>실제 응답과 관측값</summary><p>Provider 응답 ID <code>{row?.metadata?.provider_response_id||failed?.provider_response_id||'없음'}</code></p><pre>{row?.reading?JSON.stringify(row.reading,null,2):failed?.raw_output||row?.error||'응답 없음'}</pre></details>
  </div></div>
  <p className="retrieval-note">실제 Agent 진단: {report.graph.run.state} · 이미지 모델 호출 {report.graph.visual_calls}회 · 최종 {report.graph.run.decisions.map(d=>d.verdict==='unknown'?'확인 필요':d.verdict).join(', ')||'결과 없음'}. PDF 추출값을 지운 도면 1개의 진단입니다.</p>
  <details className="protocol"><summary>이미지 평가 범위와 한계</summary><ul>{report.limitations.map(t=><li key={t}>{t}</li>)}</ul></details>
 </section>
}
