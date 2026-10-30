import { useEffect, useState } from "react";
import { api } from "../types";

type Span = {
  name: string;
  kind: string;
  seq?: number;
  start_ms: number;
  duration_ms: number;
  attrs: Record<string, unknown>;
  children: Span[];
};
type Trace = { run_id: string; duration_ms: number; totals?: { model_ms: number; tool_ms: number; events: number }; spans: Span[] };

const ms = (x: number) => (x >= 1000 ? `${(x / 1000).toFixed(1)}초` : `${Math.round(x)} ms`);

function Node({ span, total }: { span: Span; total: number }) {
  const left = total > 0 ? Math.min(98, (span.start_ms / total) * 100) : 0;
  const width = total > 0 ? Math.max(1.5, Math.min(100 - left, (span.duration_ms / total) * 100)) : 100;
  const note = Object.entries(span.attrs)
    .filter(([k]) => ["role", "usage_input_tokens", "usage_output_tokens", "error", "reason"].includes(k))
    .map(([k, v]) => `${k.replace("usage_", "")} ${String(v).slice(0, 40)}`)
    .join(" · ");
  return (
    <li>
      <div className={`trace-row ${span.kind}`}>
        <span className="trace-name">{span.name}</span>
        <span className="trace-ms">{span.duration_ms > 0 ? ms(span.duration_ms) : ""}</span>
        <span className="trace-bar" aria-hidden="true"><i style={{ marginLeft: `${left}%`, width: `${width}%` }} /></span>
        {note && <small>{note}</small>}
      </div>
      {span.children.length > 0 && (
        <ul>{span.children.map((c, i) => <Node key={`${c.seq ?? c.name}-${i}`} span={c} total={total} />)}</ul>
      )}
    </li>
  );
}

/** The run as a tree of model and tool calls with their durations: the same data an OTLP export carries (/runs/{id}/trace). */
export function TraceTree({ runId, version }: { runId: string; version: number }) {
  const [trace, setTrace] = useState<Trace | null>(null);
  const [failed, setFailed] = useState(false);
  useEffect(() => {
    let alive = true;
    api<Trace>(`/runs/${runId}/trace`).then((t) => alive && (setTrace(t), setFailed(false))).catch(() => alive && setFailed(true));
    return () => { alive = false; };
  }, [runId, version]);
  if (failed) return <p role="alert" className="empty-note">호출 트리를 불러오지 못했습니다.</p>;
  if (!trace || !trace.spans.length) return null;
  return (
    <section className="trace-tree" aria-label="모델·도구 호출 트리" data-testid="trace-tree">
      <header>
        <strong>호출 트리</strong>
        <span>
          전체 {ms(trace.duration_ms)}
          {trace.totals ? ` · 모델 ${ms(trace.totals.model_ms)} · 도구 ${ms(trace.totals.tool_ms)} · 이벤트 ${trace.totals.events}개` : ""}
        </span>
      </header>
      <ul>{trace.spans.map((s, i) => <Node key={i} span={s} total={trace.duration_ms} />)}</ul>
    </section>
  );
}
