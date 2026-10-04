import { AgentTrials } from "./AgentTrials";
import { useEffect, useState } from "react";
import {
  ArrowDownToLine,
  ArrowUpRight,
  FlaskConical,
  ChevronRight,
  Info,
} from "lucide-react";
import { api, fields, fmt, labels, type Fact } from "../types";

type Metrics = {
  attempts: number;
  completed: number;
  accuracy: number | null;
  accuracy_ci95: (number | null)[];
  false_acceptance_rate: number | null;
  false_acceptances: number;
  latency_p50_ms: number | null;
  latency_p95_ms: number | null;
  input_tokens: number;
  output_tokens: number;
  cost_usd: number | null;
};
type Row = {
  case_id: string;
  method: string;
  repeat: number;
  expected: string;
  predicted: string | null;
  status: string;
  latency_ms: number;
  input_tokens: number;
  output_tokens: number;
  raw_output?: string;
  error?: string;
  citations: string[];
};
type Case = {
  case_id: string;
  category: string;
  family_id: string;
  query: string;
  expected: string;
  facts: Fact[];
  drawing_number: string;
  revision: string;
  source_hash: string;
  requirements: {
    field: string;
    value: string | { low: string; high: string };
    unit?: string;
  }[];
};
type Report = {
  status: string;
  created_at: string;
  protocol: {
    case_count: number;
    families: string[];
    repeats: number;
    dataset_hash: string;
    prompt_hash: string;
    scope: string;
    seeds: number[] | null;
  };
  model: {
    model_id: string;
    model_revision?: string;
    provider?: string;
    device?: string;
    dtype?: string;
  };
  methods: { id: string; label: string; kind: string; metrics: Metrics }[];
  cases: Case[];
  predictions: Row[];
  blocked: { provider: string; reason: string }[];
  limitations: string[];
};
const categories: Record<string, string> = {
  match: "조건 일치",
  dimension: "치수 불일치",
  material: "소재 불일치",
  missing: "근거 누락",
  unit: "단위 변환",
  revision: "개정판",
};
const pct = (v: number | null | undefined) =>
  v == null ? "—" : `${(v * 100).toFixed(1)}%`;
const ms = (v: number | null) =>
  v == null ? "—" : v < 1 ? `${v.toFixed(2)} ms` : `${(v / 1000).toFixed(2)} s`;
export function ExperimentLab() {
  const [experiment, setExperiment] = useState("qwen");
  const [catalog, setCatalog] = useState<{id:string;label:string}[]>([]);
  useEffect(() => { api<{experiments:{id:string;label:string}[]}>("/evaluations/catalog").then(r=>setCatalog(r.experiments)).catch(()=>{}); }, []);
  const [report, setReport] = useState<Report | null>(null),
    [error, setError] = useState(""),
    [onlyErrors, setOnlyErrors] = useState(false),
    [selected, setSelected] = useState(""),
    [trial, setTrial] = useState(0);
  useEffect(() => {
    let live = true;
    setError(""); setReport(null); setOnlyErrors(false); setTrial(0);
    api<Report>(`/evaluations?experiment=${experiment}`)
      .then((r) => {
        if (live) {
          setReport(r);
          setSelected(
            r.cases?.find((c) =>
              r.predictions.some(
                (p) =>
                  p.case_id === c.case_id &&
                  p.method !== "rules" &&
                  (p.status !== "ok" || p.predicted !== c.expected),
              ),
            )?.case_id ||
              r.cases?.[0]?.case_id ||
              "",
          );
        }
      })
      .catch((e) => live && setError(String(e)));
    return () => {
      live = false;
    };
  }, [experiment]);
  if (error)
    return (
      <div role="alert" className="notice danger">
        평가 결과를 불러오지 못했습니다. {error}
      </div>
    );
  if (!report || report.status !== "measured")
    return (
      <section className="loading-panel">
        <FlaskConical size={28} />
        <h2>실측 결과 확인 중</h2>
        <p>측정이 완료된 실험만 비교 화면에 표시합니다.</p>
      </section>
    );
  const llm = report.methods.find((m) => m.kind === "llm");
  const caseRows = report.cases.filter(
    (c) =>
      !onlyErrors ||
      report.predictions.some(
        (p) =>
          p.case_id === c.case_id &&
          p.method !== "rules" &&
          (p.status !== "ok" || p.predicted !== c.expected),
      ),
  );
  const chosen = report.cases.find((c) => c.case_id === selected);
  const modelRow = report.predictions.find(
    (p) => p.case_id === selected && p.method !== "rules" && p.repeat === trial,
  );
  const ruleRow = report.predictions.find(
    (p) => p.case_id === selected && p.method === "rules" && p.repeat === trial,
  );
  const badCases = report.cases.filter((c) =>
    report.predictions.some(
      (p) =>
        p.case_id === c.case_id &&
        p.method !== "rules" &&
        (p.status !== "ok" || p.predicted !== c.expected),
    ),
  ).length;
  return (
    <div className="lab">
      <div className="page-heading">
        <div>
          <span className="kicker">EVIDENCE / BENCHMARK</span>
          <h1>평가 결과 비교</h1>
          <p>같은 PDF 근거, 다른 판단. 모델이 틀린 지점까지 확인합니다.</p>
        </div>
        <a
          className="secondary"
          href={`/api/evaluations?experiment=${experiment}`}
          download="fitwitness-evaluation.json"
        >
          <ArrowDownToLine size={14} /> 원시 결과 JSON
        </a>
      </div>
      <div className="experiment-meta">
        <label>측정 모델 <select aria-label="측정 모델" value={experiment} onChange={e=>setExperiment(e.target.value)}>{catalog.map(x=><option key={x.id} value={x.id}>{x.label}</option>)}</select></label>
        <span className="live-tag recorded">실측 기록</span>
        <span>{report.protocol.case_count} cases</span>
        <span>{report.protocol.families.length} families</span>
        <span>{report.protocol.repeats} trials</span>
        <span>{report.model.provider ? `${report.model.provider} · API` : `CPU · ${report.model.dtype}`}</span>
        <time>{new Date(report.created_at).toLocaleDateString("ko-KR")}</time>
      </div>
      <section className="comparison" data-testid="experiment-comparison">
        <div className="section-bar">
          <h2>동일 입력 비교</h2>
          <span>실패한 호출도 정확도 분모에 포함</span>
        </div>
        <div className="table-scroll">
          <table>
            <thead>
              <tr>
                <th>실험</th>
                <th>
                  판정 정확도 <small>95% CI</small>
                </th>
                <th>
                  잘못된 일치 <small>비일치 정답 기준</small>
                </th>
                <th>
                  지연 <small>p50 / p95</small>
                </th>
                <th>실제 토큰</th>
                <th>호출 결과</th>
              </tr>
            </thead>
            <tbody>
              {report.methods.map((m) => (
                <tr key={m.id}>
                  <td>
                    <span className={`method-mark ${m.kind}`} />
                    <strong>{m.label}</strong>
                    <small>
                      {m.kind === "rules"
                        ? "BASELINE · 운영 검증기"
                        : report.model.provider ? "API LLM · 실제 호출" : "LOCAL LLM · 실제 추론"}
                    </small>
                  </td>
                  <td>
                    <strong className="number">
                      {pct(m.metrics.accuracy)}
                    </strong>
                    <small>
                      {m.metrics.accuracy_ci95.map((v) => pct(v)).join(" – ")}
                    </small>
                  </td>
                  <td className={m.metrics.false_acceptances ? "negative" : ""}>
                    <strong className="number">
                      {pct(m.metrics.false_acceptance_rate)}
                    </strong>
                    <small>{m.metrics.false_acceptances}회</small>
                  </td>
                  <td>
                    <strong>{ms(m.metrics.latency_p50_ms)}</strong>
                    <small>{ms(m.metrics.latency_p95_ms)}</small>
                  </td>
                  <td>
                    <strong>
                      {(
                        m.metrics.input_tokens + m.metrics.output_tokens
                      ).toLocaleString()}
                    </strong>
                    <small>
                      입력 {m.metrics.input_tokens.toLocaleString()} / 출력{" "}
                      {m.metrics.output_tokens.toLocaleString()}
                    </small>
                  </td>
                  <td>
                    <strong>
                      {m.metrics.completed} / {m.metrics.attempts}
                    </strong>
                    <small>스키마 검증 통과</small>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>
      <div className="evaluation-note">
        <Info size={15} />
        <span>
          이 점수는 <b>근거 기반 판정</b>의 소규모 합성 데이터 평가입니다.
          검색·OCR·VLM·전체 Agent 성능은 포함하지 않습니다.
        </span>
      </div>
      <section className="case-explorer">
        <div className="case-list">
          <div className="section-bar">
            <h2>
              사례 탐색 <span>{caseRows.length}</span>
            </h2>
            <button
              className={`filter ${onlyErrors ? "selected" : ""}`}
              onClick={() => setOnlyErrors((v) => !v)}
              aria-pressed={onlyErrors}
              aria-label="오류 사례만"
            >
              오류 사례만 <b>{badCases}</b>
            </button>
          </div>
          <div className="case-table">
            <div className="case-table-head">
              <span>사례 / 정답</span>
              <span>LLM · 3회 반복</span>
            </div>
            {caseRows.map((c) => {
              const rows = report.predictions.filter(
                (p) => p.case_id === c.case_id && p.method !== "rules",
              );
              return (
                <button
                  className={`case-row ${selected === c.case_id ? "active" : ""}`}
                  key={c.case_id}
                  onClick={() => {
                    setSelected(c.case_id);
                    setTrial(0);
                  }}
                >
                  <div>
                    <strong>{categories[c.category]}</strong>
                    <small>
                      {c.family_id} · {labels[c.expected]}
                    </small>
                  </div>
                  <div className="trial-dots">
                    {rows.map((r) => (
                      <span
                        key={r.repeat}
                        className={
                          r.status === "ok" && r.predicted === c.expected
                            ? "correct"
                            : "incorrect"
                        }
                        title={`반복 ${r.repeat + 1}: ${labels[r.predicted || ""] || "오류"}`}
                      >
                        {r.repeat + 1}
                      </span>
                    ))}
                    <ChevronRight size={13} />
                  </div>
                </button>
              );
            })}
            {caseRows.length === 0 && (
              <p className="empty-note">
                선택한 조건에 해당하는 오류가 없습니다.
              </p>
            )}
          </div>
        </div>
        <aside className="case-inspector">
          {chosen && (
            <>
              <div className="section-bar">
                <h2>
                  {chosen.drawing_number} <span>REV.{chosen.revision}</span>
                </h2>
                <code>{chosen.family_id}</code>
              </div>
              <div className="inspector-body">
                <div className="inspector-label">요구 조건</div>
                <p className="case-query">
                  {chosen.requirements
                    .map(
                      (r) =>
                        `${fields[r.field] || r.field} ${typeof r.value === "string" ? r.value : r.value.low} ${r.unit || ""}`,
                    )
                    .join(" · ")}
                </p>
                <div className="side-by-side">
                  <div>
                    <span>정답 / 검증기</span>
                    <strong className={chosen.expected}>
                      {labels[chosen.expected]}
                    </strong>
                    <small>
                      검증기: {labels[ruleRow?.predicted || ""] || "오류"}
                    </small>
                  </div>
                  <div>
                    <span>{llm?.label.split("/").pop()}</span>
                    <strong className={modelRow?.predicted || "unknown"}>
                      {labels[modelRow?.predicted || ""] || "출력 오류"}
                    </strong>
                    <small>{ms(modelRow?.latency_ms ?? null)}</small>
                  </div>
                </div>
                <div className="section-line">
                  <span className="inspector-label">실제 모델 출력</span>
                  <div className="segmented" aria-label="평가 반복 선택">
                    {Array.from({ length: report.protocol.repeats }, (_, i) => (
                      <button
                        key={i}
                        className={trial === i ? "on" : ""}
                        onClick={() => setTrial(i)}
                      >
                        #{i + 1}
                      </button>
                    ))}
                  </div>
                </div>
                <pre className="output-code">
                  {modelRow?.raw_output || modelRow?.error || "기록 없음"}
                </pre>
                {modelRow &&
                  (modelRow.status !== "ok" ||
                    modelRow.predicted !== chosen.expected) && (
                    <div className="failure-note">
                      {modelRow.status !== "ok"
                        ? "출력 형식 또는 근거 인용 검증에 실패했습니다."
                        : "모델의 판정이 독립 정답과 다릅니다."}{" "}
                      {chosen.category === "missing"
                        ? "PDF에 소재가 없으므로 일치를 확정할 수 없습니다."
                        : ""}
                    </div>
                  )}
                <div className="inspector-label">모델에 제공한 PDF 근거</div>
                <div className="facts-table">
                  {chosen.facts.map((f) => (
                    <div key={f.id}>
                      <code>{f.id}</code>
                      <span>{fields[f.field] || f.field}</span>
                      <strong>{fmt(f)}</strong>
                    </div>
                  ))}
                </div>
                <p className="source-hash">
                  PDF SHA256 <code>{chosen.source_hash.slice(0, 24)}…</code>
                </p>
              </div>
            </>
          )}
        </aside>
      </section>
      <AgentTrials />
      <details className="protocol">
        <summary>
          재현 정보와 측정 범위{" "}
          <span>dataset {report.protocol.dataset_hash.slice(0, 12)}</span>
        </summary>
        <div className="protocol-body">
          <dl>
            <dt>모델 revision</dt>
            <dd>{report.model.model_revision || report.model.model_id}</dd>
            <dt>프롬프트 hash</dt>
            <dd>{report.protocol.prompt_hash}</dd>
            <dt>반복 seed</dt>
            <dd>{report.protocol.seeds?.join(", ") || "API seed 미지정 · 반복 실행"}</dd>
          </dl>
          <ul>
            {report.limitations.map((x) => (
              <li key={x}>{x}</li>
            ))}
          </ul>
          <p>
            {report.blocked
              .map(
                (x) =>
                  `${x.provider === "anthropic" ? "Claude" : "OpenAI"}: ${x.reason} · 미측정`,
              )
              .join(" / ")}
          </p>
          <a
            href="https://github.com/sokldjs554/fitwitness/tree/feat/fitwitness-foundation/docs/evaluation"
            target="_blank"
            rel="noreferrer"
          >
            평가 프로토콜과 재현 코드 <ArrowUpRight size={12} />
          </a>
        </div>
      </details>
    </div>
  );
}
