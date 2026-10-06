import { useEffect, useState } from "react";
import { ArrowRight, FileText, Play, RotateCcw, ScanLine, ShieldCheck } from "lucide-react";
import {
  api,
  claimLabels,
  docKindLabels,
  eventNames,
  type ClaimCase,
  type Run,
  type RunEvent,
} from "../types";

const won = (n: number) => `${n.toLocaleString("ko-KR")}원`;
const ACTIVE = ["queued", "running", "retry_wait"];

export function ClaimsDesk({ ready }: { ready: boolean }) {
  const [cases, setCases] = useState<ClaimCase[]>([]);
  const [selected, setSelected] = useState("");
  const [run, setRun] = useState<Run | null>(null);
  const [events, setEvents] = useState<RunEvent[]>([]);
  const [doc, setDoc] = useState("");
  const [field, setField] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [reviewNote, setReviewNote] = useState("");
  const [crash, setCrash] = useState(false);
  const [alwaysReview, setAlwaysReview] = useState(false);
  const [ledger, setLedger] = useState<{ claim_id: string; amount: number; paid_at: string }[]>([]);

  const loadCases = async () => {
    const list = await api<ClaimCase[]>("/claims/cases");
    setCases(list);
    if (!selected && list[0]) setSelected(list[0].case_id);
    return list;
  };
  const loadLedger = () => api<typeof ledger>("/claims/ledger").then(setLedger).catch(() => {});
  useEffect(() => {
    if (!ready) return;
    loadCases().catch((e) => setError(String(e)));
    void loadLedger();
  }, [ready]);
  const current = cases.find((c) => c.case_id === selected);
  useEffect(() => {
    // Opening a case shows its latest run, if any.
    setRun(null);
    setEvents([]);
    setField("");
    setDoc(current?.documents[0]?.id || "");
    const last = current?.latest_run;
    if (last) {
      Promise.all([api<Run>("/runs/" + last.run_id), api<RunEvent[]>(`/runs/${last.run_id}/events`)])
        .then(([r, e]) => { setRun(r); setEvents(e); })
        .catch((e) => setError(String(e)));
    }
  }, [selected]);
  useEffect(() => {
    if (!run || !ACTIVE.includes(run.state)) return;
    let stopped = false;
    const poll = async () => {
      try {
        const [r, e] = await Promise.all([api<Run>("/runs/" + run.id), api<RunEvent[]>(`/runs/${run.id}/events`)]);
        if (!stopped) {
          setRun(r);
          setEvents(e);
          if (!ACTIVE.includes(r.state)) { void loadLedger(); void loadCases(); }
        }
      } catch (e) {
        if (!stopped) setError(String(e));
      }
    };
    const t = setInterval(poll, 800);
    void poll();
    return () => { stopped = true; clearInterval(t); };
  }, [run?.id, run?.state]);

  async function start() {
    if (!current) return;
    setBusy(true);
    setError("");
    try {
      const r = await api<Run>(`/claims/cases/${current.case_id}/run`, {
        method: "POST",
        headers: {
          "Idempotency-Key": crypto.randomUUID(),
          ...(crash ? { "x-demo-fault": "1" } : {}),
          ...(alwaysReview ? { "x-claim-review": "always" } : {}),
        },
      });
      setRun(r);
      setEvents([]);
      setField("");
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  }
  async function decide(outcome: "APPROVE" | "DENY") {
    if (!run) return;
    setBusy(true);
    setError("");
    try {
      const r = await api<Run>(`/runs/${run.id}/resume`, {
        method: "POST",
        body: JSON.stringify({ outcome, reviewer: "심사대 사용자", note: reviewNote }),
      });
      setRun(r);
      setReviewNote("");
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  }

  const pending = run?.state === "waiting_input"
    ? ([...events].reverse().find((e) => e.kind === "waiting_input")?.payload as
        | { proposed: string; total_amount: number; reasons: { rule_id: string; message: string }[]; line_items: { basis: string; amount: number }[] }
        | undefined)
    : undefined;
  const outcome = run?.claim ?? null;
  const extraction = outcome?.extraction;
  const evidence = extraction?.evidence ?? {};
  const docEvidence = Object.entries(evidence).filter(([, ref]) => ref.doc_id === doc);
  const highlighted = field && evidence[field]?.doc_id === doc ? evidence[field] : null;
  const running = busy || (!!run && ACTIVE.includes(run.state));
  const stateLabel = !run ? "심사 전" : run.state === "completed" ? "심사 완료" : run.state === "waiting_input" ? "담당자 확인 대기"
    : run.state === "retry_wait" ? "일시 오류 · 재시도 대기" : run.state === "failed" ? "실행 실패" : run.state === "cancelled" ? "취소" : "심사 중";
  const fieldRows = extraction
    ? Object.keys(claimLabels).filter((k) => k in extraction && k !== "evidence" && k !== "confidence" && extraction[k] !== null && extraction[k] !== undefined)
    : [];

  return (
    <div className="claims" data-testid="claims-desk">
      <header className="claims-head">
        <div>
          <p className="eyebrow">INSURANCE / CLAIM ADJUDICATION</p>
          <h1>청구 심사대</h1>
          <p className="subtitle">서류에서 필요한 값만 위치와 함께 읽고, 사내 지급 기준을 적용해 지급·부지급·담당자 확인으로 나눕니다. 모든 서류와 계약은 합성 데이터입니다.</p>
        </div>
        <div className="claims-ledger" data-testid="claims-ledger">
          <span>지급 원장</span>
          <strong>{ledger.length}건 · {won(ledger.reduce((a, p) => a + p.amount, 0))}</strong>
          <small>같은 청구는 한 번만 기록됩니다</small>
        </div>
      </header>
      {error && (
        <div role="alert" className="notice danger">
          <span>{error}</span>
          <button onClick={() => setError("")}>닫기</button>
        </div>
      )}
      <div className="claims-layout">
        <aside className="claims-cases">
          <div className="section-bar"><h2>청구 건 <span>{cases.length}</span></h2></div>
          <ul>
            {cases.map((c) => (
              <li key={c.case_id}>
                <button data-testid="claim-case" aria-pressed={c.case_id === selected} className={c.case_id === selected ? "selected" : ""} onClick={() => setSelected(c.case_id)}>
                  <strong>{c.claim_id}</strong>
                  <span className="scenario">{c.scenario_label}</span>
                  <small>{c.requested.map((r) => claimLabels[r] || r).join(" · ")} · 서류 {c.documents.length}</small>
                  {c.latest_run && <em className={`state ${c.latest_run.state}`}>{c.latest_run.state === "completed" ? "완료" : c.latest_run.state === "waiting_input" ? "확인 대기" : c.latest_run.state}</em>}
                </button>
              </li>
            ))}
            {!cases.length && <li className="empty-note">{ready ? "불러온 청구 건이 없습니다. 서버에 합성 청구 코퍼스가 생성되어 있어야 합니다." : "체험 공간 연결 중…"}</li>}
          </ul>
        </aside>
        <section className="claims-main">
          {current && (
            <>
              <div className="claims-request">
                <div>
                  <h2>{current.claim_id} <span className="scenario">{current.scenario_label}</span></h2>
                  <p>
                    {current.policy.product_id === "CANCER-B" ? "암진단 플러스 B형" : "종합건강보험 A형"} · 계약 {current.policy.policy_id} ({current.policy.effective_from} ~ {current.policy.effective_to}
                    {current.policy.status !== "active" ? ` · ${current.policy.status}` : ""}) · 피보험자 {current.insured_name} · 청구 담보 {current.requested.map((r) => claimLabels[r] || r).join(", ")}
                  </p>
                </div>
                <div className="claims-actions">
                  <label className="fault-toggle"><input type="checkbox" checked={alwaysReview} onChange={(e) => setAlwaysReview(e.target.checked)} disabled={running} />항상 담당자 확인</label>
                  <label className="fault-toggle"><input type="checkbox" checked={crash} onChange={(e) => setCrash(e.target.checked)} disabled={running} />지급 기록 직후 중단 체험</label>
                  <button className="primary" onClick={start} disabled={!ready || running}>
                    <Play size={13} /> {running ? "심사 중…" : "자동 심사 시작"} <ArrowRight size={14} />
                  </button>
                </div>
              </div>
              <div className="workspace-status">
                <div><span className={`status-dot ${running ? "pulse" : ""}`} /><strong data-testid="claim-state">{stateLabel}</strong>{run && <code>{run.id.slice(0, 8)}</code>}</div>
                <span>규칙 기반 추출·심사 · 모델 추출은 운영자 경로</span>
              </div>
              {run?.state === "waiting_input" && pending && (
                <div className="notice review" data-testid="claim-review">
                  <span>
                    <b>담당자 확인이 필요합니다.</b> 자동 심사 제안: {claimLabels[pending.proposed] || pending.proposed}, 지급 가능액 {won(pending.total_amount)}.
                    <ul className="review-list">
                      {pending.reasons.map((r) => <li key={r.rule_id + r.message}><code>{r.rule_id}</code><small>{r.message}</small></li>)}
                    </ul>
                    <input className="review-note" aria-label="검토 메모" placeholder="검토 메모 (선택)" value={reviewNote} onChange={(e) => setReviewNote(e.target.value)} />
                  </span>
                  <div className="review-buttons">
                    <button onClick={() => decide("APPROVE")} disabled={busy}>승인 · 지급 <ArrowRight size={13} /></button>
                    <button onClick={() => decide("DENY")} disabled={busy}>부지급</button>
                  </div>
                </div>
              )}
              {run?.state === "retry_wait" && (
                <div className="notice stale" data-testid="claim-retry"><RotateCcw size={15} /><span><b>지급 기록 직후 worker가 중단됐습니다.</b> {run.attempts ?? 1}회 시도 후 재시도를 예약했습니다. 저장된 지점부터 다시 이어지며, 원장에 이미 있는 지급은 다시 기록되지 않습니다.</span></div>
              )}
              {outcome && (
                <div className={`claims-decision ${outcome.decision.outcome}`} data-testid="claim-decision">
                  <div>
                    <span className="eyebrow">결정</span>
                    <strong>{claimLabels[outcome.decision.outcome]}</strong>
                    <b>{won(outcome.decision.total_amount)}</b>
                  </div>
                  <div>
                    <span className="eyebrow">지급 원장</span>
                    <strong data-testid="claim-payout">{claimLabels[outcome.payout.status] || outcome.payout.status}</strong>
                    <small>{outcome.payout.paid_at ? new Date(outcome.payout.paid_at).toLocaleString("ko-KR") : "기록 없음"}</small>
                  </div>
                  <div>
                    <span className="eyebrow">사람 개입</span>
                    <strong>{outcome.human ? `${outcome.human.reviewer} · ${claimLabels[outcome.human.outcome]}` : "없음 · 자동 처리"}</strong>
                    <small>추출 신뢰도 {Math.round(outcome.extraction.confidence * 100)}%{outcome.flags.length ? ` · 정합성 플래그 ${outcome.flags.length}` : ""}</small>
                  </div>
                </div>
              )}
              <div className="claims-board">
                <section className="claims-doc">
                  <div className="section-bar">
                    <h2>서류 원문</h2>
                    <div className="doc-tabs">
                      {current.documents.map((d) => (
                        <button key={d.id} className={d.id === doc ? "selected" : ""} onClick={() => { setDoc(d.id); setField(""); }}>
                          <FileText size={11} /> {docKindLabels[d.kind] || d.kind}
                        </button>
                      ))}
                    </div>
                  </div>
                  <div className="doc-sheet">
                    {doc && (
                      <div className="doc-image">
                        <img src={`/api/claims/cases/${current.case_id}/documents/${doc}/png`} alt="청구 서류 원문" />
                        {docEvidence.map(([f, ref]) => (
                          <button
                            key={f}
                            className={`evidence-box ${f === field ? "chosen" : ""}`}
                            title={claimLabels[f] || f}
                            aria-label={`${claimLabels[f] || f} 근거 위치`}
                            style={{ left: `${ref.bbox[0] * 100}%`, top: `${ref.bbox[1] * 100}%`, width: `${(ref.bbox[2] - ref.bbox[0]) * 100}%`, height: `${(ref.bbox[3] - ref.bbox[1]) * 100}%` }}
                            onClick={() => setField(f)}
                          />
                        ))}
                      </div>
                    )}
                    {highlighted && <p className="doc-snippet"><ScanLine size={12} /> <code>{highlighted.snippet}</code></p>}
                    {doc && <a className="text-button" href={`/api/claims/cases/${current.case_id}/documents/${doc}/pdf`} target="_blank" rel="noreferrer">원본 PDF 열기</a>}
                  </div>
                </section>
                <aside className="claims-side">
                  <section>
                    <div className="section-bar"><h2>추출 결과</h2><span>{extraction ? `${fieldRows.length}개 필드` : "심사 전"}</span></div>
                    {extraction ? (
                      <table className="claims-table" data-testid="claim-fields">
                        <tbody>
                          {fieldRows.map((k) => {
                            const ref = evidence[k];
                            const v = extraction[k];
                            return (
                              <tr key={k} className={k === field ? "chosen" : ""} onClick={() => { if (ref) { setDoc(ref.doc_id); setField(k); } }}>
                                <th>{claimLabels[k] || k}</th>
                                <td>{typeof v === "number" ? (k === "total_amount" ? won(v) : String(v)) : String(v)}</td>
                                <td className="where">{ref ? `${docKindLabels[current.documents.find((d) => d.id === ref.doc_id)?.kind || ""] || ref.doc_id} p.${ref.page}` : "근거 없음"}</td>
                              </tr>
                            );
                          })}
                        </tbody>
                      </table>
                    ) : <p className="empty-note">자동 심사를 시작하면 서류별 추출 값과 위치가 여기에 연결됩니다.</p>}
                  </section>
                  <section>
                    <div className="section-bar"><h2>적용 기준</h2><span>{outcome ? `${outcome.decision.line_items.length + outcome.decision.reasons.length}개 조항` : ""}</span></div>
                    {outcome ? (
                      <ul className="claims-rules" data-testid="claim-rules">
                        {outcome.decision.line_items.map((li) => (
                          <li key={li.rule_id + li.basis} className="pay"><ShieldCheck size={12} /><code>{li.rule_id}</code><span>{li.basis}</span><b>{won(li.amount)}</b></li>
                        ))}
                        {outcome.decision.reasons.map((r) => (
                          <li key={r.rule_id + r.message} className={r.severity}><code>{r.rule_id}</code><span>{r.message}</span></li>
                        ))}
                      </ul>
                    ) : <p className="empty-note">지급 기준표의 어느 조항이 적용됐는지 규칙 ID와 함께 표시됩니다.</p>}
                    {outcome && <pre className="claims-explain" data-testid="claim-explain">{outcome.explanation}</pre>}
                  </section>
                  <section>
                    <div className="section-bar"><h2>실행 기록</h2><span>{events.length} events</span></div>
                    <ol className="claims-timeline" data-testid="claim-timeline">
                      {events.map((e) => (
                        <li key={e.seq}><span>{String(e.seq).padStart(2, "0")}</span>{eventNames[e.kind] || e.kind}<small>{new Date(e.timestamp).toLocaleTimeString("ko-KR")}</small></li>
                      ))}
                    </ol>
                  </section>
                </aside>
              </div>
            </>
          )}
        </section>
      </div>
    </div>
  );
}
