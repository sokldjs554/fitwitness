import { useEffect, useState } from "react";
import { ArrowUpRight } from "lucide-react";
import { api } from "../types";

type Card = {
  id: string;
  title: string;
  posting: string;
  question: string;
  model: string | null;
  measured: string;
  cost_usd: number | null;
  headline: { label: string; value: string; note?: string }[];
  table: { caption: string; columns: string[]; rows: string[][] } | null;
  path?: {
    caption: string; tools: string[]; planner_tools: number; challenger_tools: number;
    model_calls: number; stop: string | null; verdicts: string;
  };
  fixes?: string[];
  quote?: string;
  caveats: string[];
  links: { label: string; href: string }[];
};
type Report = { status: string; what: string; spent_usd: number; cards: Card[] };

export function ModelRecords() {
  const [report, setReport] = useState<Report | null>(null);
  const [error, setError] = useState(false);
  const [selected, setSelected] = useState("");
  useEffect(() => {
    let alive = true;
    api<Report>("/evaluations/models").then(r => alive && setReport(r)).catch(() => alive && setError(true));
    return () => { alive = false; };
  }, []);
  const card = report?.cards?.find(c => c.id === selected) || report?.cards?.[0];
  return <section className="retrieval-trials model-records" aria-label="최신 모델 측정">
    <div className="section-heading"><div><span className="kicker">CLAUDE HAIKU 4.5 / RECORDED / 2026-10-22 ~ 10-25</span><h2>최신 모델 측정</h2>
      <p>실제 모델로 잰 경로·추출·감사·질의 읽기·명령 검색·표제란, 그리고 사람에게 쓰게 해 본 결과입니다. 이 화면은 모델을 부르지 않고 커밋된 기록을 보여 줍니다.</p></div>
      <a className="secondary" href="/api/evaluations/models" download="fitwitness-model-records.json">요약 JSON</a></div>
    {error ? <p role="alert">최신 측정 기록을 불러오지 못했습니다.</p> :
      !report ? <p role="status">최신 측정 기록 확인 중</p> :
      report.status !== "measured" || !card ? <p role="status">아직 측정된 결과가 없습니다.</p> : <>
      <div className="record-picker" role="group" aria-label="측정 고르기">
        {report.cards.map(c => <button key={c.id} type="button" className={c.id === card.id ? "selected" : ""}
          aria-pressed={c.id === card.id} onClick={() => setSelected(c.id)}>{c.title}</button>)}
      </div>
      <article className="record-card" aria-label={card.title}>
        <div className="record-head">
          <span className="record-posting">{card.posting}</span>
          <h3>{card.title}</h3>
          <p>{card.question}</p>
          <div className="retrieval-meta">
            {card.model && <span>{card.model}</span>}
            <span>{card.measured}</span>
            {card.cost_usd != null && <span>${card.cost_usd.toFixed(4)}</span>}
          </div>
        </div>
        <div className="record-headline">
          {card.headline.map(h => <div key={h.label}><strong>{h.value}</strong><span>{h.label}</span>{h.note && <small>{h.note}</small>}</div>)}
        </div>
        {card.quote && <blockquote className="record-quote">“{card.quote}”<span>생산기술 현업 5년, 사용자 테스트</span></blockquote>}
        {card.table && <div className="retrieval-table-wrap" tabIndex={0} role="region" aria-label="표 (좌우로 밀어 볼 수 있습니다)"><table aria-label={card.table.caption}>
          <caption>{card.table.caption}</caption>
          <thead><tr>{card.table.columns.map(c => <th key={c}>{c}</th>)}</tr></thead>
          <tbody>{card.table.rows.map((r, i) => <tr key={i}>{r.map((v, j) => j === 0 ? <th key={j}>{v}</th> : <td key={j}>{v}</td>)}</tr>)}</tbody>
        </table></div>}
        {card.path && <div className="record-path">
          <span>{card.path.caption}</span>
          <ol>
            <li><b>planner</b> 도구 {card.path.planner_tools}번 {card.path.tools.map(t => <code key={t}>{t}</code>)}</li>
            <li><b>challenger</b> 도구 {card.path.challenger_tools}번, 반례 검토</li>
            <li>멈춤 <code>{card.path.stop || "—"}</code> · 모델 호출 {card.path.model_calls}번 · 판정 {card.path.verdicts}</li>
          </ol>
        </div>}
        {card.fixes && card.fixes.length > 0 && <details className="protocol"><summary>실패를 보고 고친 것 <span>{card.fixes.length}</span></summary>
          <ul>{card.fixes.map(f => <li key={f}>{f}</li>)}</ul></details>}
        <ul className="record-caveats">{card.caveats.map(c => <li key={c}>{c}</li>)}</ul>
        <div className="record-links">{card.links.map(l => <a key={l.href} href={l.href} target="_blank" rel="noreferrer">{l.label} <ArrowUpRight size={12} /></a>)}</div>
      </article>
      <p className="retrieval-note">{report.what} 이 화면에 모은 측정의 API 비용은 합계 ${report.spent_usd.toFixed(2)}입니다.</p>
      </>}
  </section>;
}
