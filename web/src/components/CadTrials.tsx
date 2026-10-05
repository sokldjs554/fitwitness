import { useEffect, useState } from "react";
import { api } from "../types";

type CadReport = {
  status: string;
  created_at: string;
  protocol: { paired_case_count: number; source_sha256: string; code_sha: string; weights: Record<string, number> };
  metrics: {
    paired_cases: number; selected_files_parsed: number; parse_success_rate: number;
    top1_accuracy: number; top3_accuracy: number; mrr: number;
    paired_distance_p50: number; paired_distance_p95: number;
    parse_latency_p50_ms: number; parse_latency_p95_ms: number;
  };
  cases: { case_id: string; rank: number; distance: number }[];
  evidence: { workflow_run_id: number; artifact_id: number; artifact_sha256: string; raw_sha256: string };
  limitations: string[];
};
const pct = (n: number) => (n * 100).toFixed(1) + "%";

export function CadTrials() {
  const [report, setReport] = useState<CadReport | null>(null);
  const [error, setError] = useState(false);
  useEffect(() => {
    let alive = true;
    api<CadReport>("/evaluations/cad").then(r => alive && setReport(r)).catch(() => alive && setError(true));
    return () => { alive = false; };
  }, []);
  return <section className="retrieval-trials" aria-label="외부 CAD 형상 일반화">
    <div className="section-heading"><div><span className="kicker">CAD / EXTERNAL / RECORDED</span><h2>외부 CAD 형상 일반화</h2>
      <p>같은 설계를 다른 STEP 표현으로 바꿔도 다시 찾는지 실제 NIST 모델로 확인했습니다.</p></div>
      <a className="secondary" href="/api/evaluations/cad" download="fitwitness-nist-cad.json">측정 기록 JSON</a></div>
    {error ? <p role="alert">외부 CAD 측정 기록을 불러오지 못했습니다.</p> :
      !report ? <p role="status">외부 CAD 측정 기록 확인 중</p> :
      report.status !== "measured" ? <p role="status">아직 측정된 외부 CAD 결과가 없습니다.</p> : <>
      <div className="retrieval-meta"><span>NIST CTC/FTC {report.metrics.paired_cases}쌍</span><span>STEP {report.metrics.selected_files_parsed}개 파싱</span><span>AP203 → AP242</span><span>GitHub Actions 실측</span></div>
      <p className="retrieval-note">생산 현장 도면 성능을 주장하지 않습니다. 외부 engineering benchmark에서 geometry extraction과 cross-format nearest-shape retrieval만 측정했습니다.</p>
      <div className="retrieval-table-wrap"><table aria-label="외부 CAD 형상 거리 실측"><thead><tr><th>파싱</th><th>Top-1</th><th>Top-3</th><th>MRR</th><th>거리 p50 / p95</th></tr></thead><tbody><tr>
        <td>{report.metrics.selected_files_parsed} / {report.metrics.selected_files_parsed}</td>
        <td>{report.metrics.paired_cases} / {report.metrics.paired_cases} · {pct(report.metrics.top1_accuracy)}</td>
        <td>{pct(report.metrics.top3_accuracy)}</td><td>{report.metrics.mrr.toFixed(3)}</td>
        <td>{report.metrics.paired_distance_p50.toFixed(5)} / {report.metrics.paired_distance_p95.toFixed(5)}</td>
      </tr></tbody></table></div>
      <div className="retrieval-table-wrap"><table aria-label="외부 CAD 사례별 순위"><thead><tr><th>NIST case</th><th>정답 순위</th><th>AP203 ↔ AP242 거리</th></tr></thead>
        <tbody>{report.cases.map(c => <tr key={c.case_id}><th>{c.case_id.toUpperCase()}</th><td>{c.rank}</td><td>{c.distance.toExponential(4)}</td></tr>)}</tbody></table></div>
      <details className="protocol"><summary>외부 CAD 측정 범위와 재현 근거</summary><ul>{report.limitations.map(x => <li key={x}>{x}</li>)}</ul>
        <p>source SHA <code>{report.protocol.source_sha256}</code></p><p>raw SHA <code>{report.evidence.raw_sha256}</code></p>
        <p>workflow <code>{report.evidence.workflow_run_id}</code> · artifact <code>{report.evidence.artifact_id}</code></p></details>
      </>}
  </section>;
}
