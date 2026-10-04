import { useEffect, useState } from 'react';
import { api } from '../types';

type Metrics={attempts:number;completed:number;recall_at_5:number;ndcg_at_10:number;latency_p50_ms:number;latency_p95_ms:number};
type Case={id:string;family_id:string;category:string;text:string;image_data_url?:string;relevance:Record<string,number>};
type Row={case_id:string;method:string;status:string;ranked:{revision_id:string;scores:Record<string,number>}[]};
type Report={status:string;corpus_size:number;protocol:{repeats:number;dataset_hash:string};
  cases:Case[];methods:{id:string;label:string;metrics:Metrics;categories:Record<string,Metrics>}[];
  rows:Row[];documents:Record<string,{drawing_number:string;kind:string}>;limitations:string[];encoder_fingerprint:string;graph_state:string};
const categories:Record<string,string>={all:'전체 질문',exact:'도번을 아는 질문',paraphrase:'다른 말로 설명한 질문',image:'이미지만 있는 질문',mixed:'설명과 이미지가 있는 질문'};
const pct=(n:number)=>(n*100).toFixed(1)+'%';

export function RetrievalTrials(){
  const [report,setReport]=useState<Report|null>(null),[error,setError]=useState(false);
  const [category,setCategory]=useState('all'),[caseId,setCaseId]=useState('');
  useEffect(()=>{let alive=true;api<Report>('/api/evaluations/retrieval').then(r=>{if(alive)setReport(r);}).catch(()=>{if(alive)setError(true);});return()=>{alive=false;};},[]);
  if(error)return <p role="status">검색 측정 기록을 불러오지 못했습니다. 화면을 새로고침해 주세요.</p>;
  if(!report||report.status!=='measured')return null;
  const cases=report.cases.filter(c=>category==='all'||c.category===category);
  const selected=cases.find(c=>c.id===caseId)||cases[0];
  return <section className="retrieval-trials" aria-label="도면 검색 방식 비교">
    <div className="section-heading"><div><span className="kicker">RETRIEVAL / RECORDED</span><h2>도면 검색 방식 비교</h2>
      <p>이름을 몰라도 찾을 수 있을까? 같은 질문에서 검색 방식만 바꿨습니다.</p></div>
      <a className="secondary" href="/api/evaluations/retrieval" download="fitwitness-retrieval.json">측정 기록 JSON</a></div>
    <div className="retrieval-meta"><span>합성 도면 {report.corpus_size}개</span><span>질문 {report.cases.length}개 × {report.protocol.repeats}회</span><span>실제 E5 · OpenCLIP / CPU</span><span>저장된 결과</span></div>
    <p className="retrieval-note">공개 체험의 검색 엔진은 도번·키워드 방식입니다. 아래는 별도 작업 환경에서 실제 모델과 PostgreSQL로 측정한 결과입니다.</p>
    <label className="retrieval-filter">검색 질문 유형 <select aria-label="검색 질문 유형" value={category} onChange={e=>{setCategory(e.target.value);setCaseId('');}}>{Object.entries(categories).map(([id,label])=><option value={id} key={id}>{label}</option>)}</select></label>
    <div className="retrieval-table-wrap"><table aria-label="검색 방식별 측정 지표"><thead><tr><th>검색 방식</th><th>Recall@5</th><th>nDCG@10</th><th>p50 / p95</th><th>실행 완료</th></tr></thead><tbody>
      {report.methods.map(m=>{const s=category==='all'?m.metrics:m.categories[category];return <tr key={m.id}><th>{m.label}</th><td>{pct(s.recall_at_5)}</td><td>{s.ndcg_at_10.toFixed(3)}</td><td>{s.latency_p50_ms.toFixed(0)} / {s.latency_p95_ms.toFixed(0)} ms</td><td>{s.completed}/{s.attempts}</td></tr>;})}
    </tbody></table></div>
    <p className="retrieval-note">Recall@5는 관련 도면 중 상위 5개에서 찾은 비율, nDCG@10은 상위 순위의 관련도를 봅니다. 이미지만 있는 질문의 키워드 검색처럼 입력 모달리티가 없으면 빈 결과로 집계합니다.</p>
    <div className="retrieval-case"><label>질문 선택 <select aria-label="검색 평가 질문" value={selected.id} onChange={e=>setCaseId(e.target.value)}>{cases.map(c=><option value={c.id} key={c.id}>{c.family_id} · {categories[c.category]}</option>)}</select></label>
      <div className="retrieval-query">{selected.image_data_url&&<img src={selected.image_data_url} alt="실제 검색에 사용한 변형 도면"/>}<div><span className="kicker">ACTUAL QUERY</span><p>{selected.text||'텍스트 없이 변형된 도면 이미지만 입력'}</p><small>관련 도면: {Object.keys(selected.relevance).map(id=>report.documents[id]?.drawing_number||id).join(' · ')}</small></div></div>
      <div className="retrieval-rankings">{report.methods.map(m=>{const row=report.rows.find(r=>r.case_id===selected.id&&r.method===m.id);return <div key={m.id}><h3>{m.label}</h3>{!row?.ranked.length?<p className="muted">검색 결과 없음</p>:<ol>{row.ranked.slice(0,5).map(r=><li key={r.revision_id} className={selected.relevance[r.revision_id]?'is-relevant':''}><span>{report.documents[r.revision_id]?.drawing_number||r.revision_id}</span><small>{selected.relevance[r.revision_id]?'관련':'비관련'}</small><span className="rank-channels">{Object.entries(r.scores).map(([k,v])=>`${k} ${v.toFixed(3)}`).join(' / ')}</span></li>)}</ol>}</div>;})}</div>
      <p className="retrieval-note">순위는 반복 1의 원시 결과입니다. 합성 정답은 런타임 검색에 전달하지 않았으며, 치수·형상 관련도는 재질이나 조립 적합성 판정과 다릅니다.</p>
    </div>
    <details className="protocol"><summary>검색 측정 범위와 재현 정보</summary><ul>{report.limitations.map(t=><li key={t}>{t}</li>)}</ul><p>데이터 해시 <code>{report.protocol.dataset_hash}</code></p><p>모델 해시 <code>{report.encoder_fingerprint}</code></p><p>실제 복합 검색 → LangGraph 실행 상태: {report.graph_state}</p></details>
  </section>;
}
