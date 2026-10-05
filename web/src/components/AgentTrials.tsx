import { useEffect, useState } from "react";
import { api } from "../types";
type Trial = {
  provider: string;
  mode: string;
  repeat: number;
  state: string;
  latency_ms: number;
  error: string | null;
  metrics: { correct: number; total: number; accuracy: number };
  result: {
    usage: {
      model_calls: number;
      tool_calls: number;
      cost_usd: string | null;
      reserved_cost_usd: string;
    };
  };
  events: { seq: number; kind: string; payload: Record<string, unknown> }[];
};
type Report = {
  status: string;
  runs: Trial[];
  limitations: string[];
  protocol: { scope: string; model: string };
};
export function AgentTrials() {
  const [version, setVersion] = useState("latest");
  const [report, setReport] = useState<Report | null>(null),
    [selected, setSelected] = useState(0),
    [error, setError] = useState(false);
  useEffect(() => {
    let live = true;
    setSelected(0);
    setReport(null);
    setError(false);
    api<Report>(`/evaluations/agent?version=${version}`)
      .then((r) => live && setReport(r))
      .catch(() => live && setError(true));
    return () => {
      live = false;
    };
  }, [version]);
  const trial = report?.runs?.[selected];
  return (
    <section
      className="comparison agent-trials"
      aria-label="실제 Agent 실행 비교"
    >
      <div className="section-bar">
        <h2>실제 Agent 실행 비교</h2>
        <a
          href={`/api/evaluations/agent?version=${version}`}
          download="agent-trials.json"
        >
          실행 기록 JSON ↓
        </a>
      </div>
      <div className="agent-caption">
        <label>
          실행 버전{" "}
          <select
            aria-label="Agent 실행 버전"
            value={version}
            onChange={(e) => setVersion(e.target.value)}
          >
            <option value="latest">수정 후 · 진단 재실행</option>
            <option value="v1">개선 전 · 실패 포함</option>
          </select>
        </label>
      </div>
      {error ? <p role="alert">Agent 실행 기록을 불러오지 못했습니다. 다른 실행 버전을 선택해 주세요.</p> : !report ? <p role="status">Agent 실행 기록 확인 중</p> : report.status !== "measured" ? <p role="status">아직 측정된 Agent 결과가 없습니다.</p> : <>
      {report.runs.length === 0 && <p role="status">기록된 실행 사례가 없습니다.</p>}
      <p className="agent-caption">
        검색 → 도구 선택 → 근거 조회 → 조건 검증. 행을 선택하면 실제 호출 기록을
        볼 수 있습니다. 단일 계획은 한 번 계획한 뒤 도구를 실행하고, 반복 계획은
        필요한 경우 다시 계획하며, 탐색 + 반례 검토는 별도 challenger가 결과를 재검토합니다.
      </p>
      <div className="table-scroll">
        <table>
          <thead>
            <tr>
              <th>구조 / 반복</th>
              <th>판정 일치</th>
              <th>상태</th>
              <th>모델 호출 / 도구 호출</th>
              <th>총 지연</th>
              <th>API 비용</th>
            </tr>
          </thead>
          <tbody>
            {report.runs.map((r, i) => (
              <tr key={i} aria-selected={selected === i}>
                <td>
                  <button
                    className="text-button"
                    onClick={() => setSelected(i)}
                  >
                    {r.provider === "rules"
                      ? "규칙 기준선"
                      : r.mode === "fixed"
                        ? "단일 계획"
                        : r.mode === "react"
                          ? "반복 계획"
                          : "탐색 + 반례 검토"}{" "}
                    · {r.repeat + 1}
                  </button>
                </td>
                <td>
                  {r.metrics.correct}/{r.metrics.total}
                </td>
                <td>{r.state}</td>
                <td>
                  모델 {r.result.usage.model_calls} · 도구 {r.result.usage.tool_calls}
                </td>
                <td>{(r.latency_ms / 1000).toFixed(2)} s</td>
                <td>
                  {r.result.usage.cost_usd == null
                    ? "—"
                    : `$${Number(r.result.usage.cost_usd).toFixed(4)}`}
                  {Number(r.result.usage.reserved_cost_usd) > 0 && (
                    <small>
                      미확정 예약 $
                      {Number(r.result.usage.reserved_cost_usd).toFixed(4)}
                    </small>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {trial && (
        <details key={selected} open>
          <summary>
            {trial.provider} / {trial.mode} · 관측 기록
          </summary>
          {trial.error && <p role="alert">{trial.error}</p>}
          <div className="agent-events">
            {trial.events
              .filter((e) =>
                [
                  "model",
                  "tool",
                  "agent_plan",
                  "retry_wait",
                  "model_error",
                  "model_schema_error",
                  "tool_skipped",
                  "evidence_exhausted",
                  "verified",
                  "budget_stop",
                ].includes(e.kind),
              )
              .map((e) => (
                <details key={e.seq}>
                  <summary>
                    <code>{e.seq.toString().padStart(2, "0")}</code> {e.kind}{" "}
                    {String(e.payload.role || e.payload.name || "")}
                    {e.payload.latency_ms != null
                      ? ` · ${(Number(e.payload.latency_ms) / 1000).toFixed(2)}s`
                      : ""}
                  </summary>
                  <pre>{JSON.stringify(e.payload, null, 2)}</pre>
                </details>
              ))}
          </div>
        </details>
      )}
      <div className="agent-caption">
        {report.limitations.map((x) => (
          <p key={x}>{x}</p>
        ))}
      </div>
      </>}
    </section>
  );
}
