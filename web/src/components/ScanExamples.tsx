import { useEffect, useRef, useState } from "react";
import { api, docKindLabels } from "../types";

type Field = {
  field: string; label: string; decisive: boolean; truth: string | null; value: string | null; correct: boolean; flagged: boolean;
  doc_id?: string; doc_kind?: string; bbox?: [number, number, number, number];
  first_read: string | null; first_conf?: number | null; read: string | null; read_conf?: number | null; how: string[];
};
type Example = {
  id: string; title: string; picked_by: string; note: string; case_id: string; level: string;
  page: { doc_id: string; doc_kind: string; image: string; width: number; height: number };
  fields: Field[]; confidence: number; outcome: "APPROVE" | "DENY" | "REVIEW"; amount: number; rules: string[];
  reasons: { rule_id: string; severity: string; message: string }[]; gold: string; gold_amount: number;
};
type Report = { status: string; what: string; caveats: string[]; corpus: { claims: number; level: string }; examples: Example[] };

const OUTCOME: Record<string, string> = { APPROVE: "지급", DENY: "부지급", REVIEW: "담당자 확인" };
const LEVEL: Record<string, string> = { clean: "깨끗함", light: "약함", medium: "보통", heavy: "심함" };
const HOW: Record<string, string> = {
  second_read: "확대해 다시 읽음", second_read_confirmed: "다시 읽어 확인", repaired: "글자 보정", sum_checked: "영수증 두 줄의 합이 맞아 확정",
};
const won = (n: number) => `${n.toLocaleString("ko-KR")}원`;
const show = (f: Field, v: string | null) => v == null || v === "" ? "—" : f.field === "total_amount" && /^\d+$/.test(v) ? won(Number(v)) : v;

// One value's verdict in words: right or wrong, and whether it sends the claim to a person.
function verdict(f: Field): { cls: "ok" | "flag" | "wrong"; text: string } {
  if (f.correct && !f.flagged) return { cls: "ok", text: "맞게 읽음" };
  if (f.correct) return { cls: "flag", text: "맞지만 신뢰도가 낮아 담당자 확인" };
  if (f.flagged) return { cls: "flag", text: f.value == null ? "읽지 못해 담당자 확인" : "틀리게 읽었지만 담당자 확인" };
  return f.decisive ? { cls: "wrong", text: "틀렸는데 확인 없이 통과" } : { cls: "wrong", text: "틀림 (지급에 쓰는 값은 아님)" };
}

export function ScanExamples() {
  const [report, setReport] = useState<Report | null>(null);
  const [error, setError] = useState(false);
  const [selected, setSelected] = useState("");
  const [field, setField] = useState("");
  const section = useRef<HTMLElement>(null);
  useEffect(() => {
    let alive = true;
    api<Report>("/evaluations/scan-examples").then((r) => alive && setReport(r)).catch(() => alive && setError(true));
    return () => { alive = false; };
  }, []);
  // opened from "#claims-scan" (a link into this section): the browser's own scroll ran before the page was tall enough
  useEffect(() => { if (report && window.location.hash === "#claims-scan") section.current?.scrollIntoView(); }, [report]);
  const ex = report?.examples?.find((e) => e.id === selected) || report?.examples?.[0];
  const pick = (id: string) => { setSelected(id); setField(""); };
  const onPage = ex ? ex.fields.filter((f) => f.bbox && f.doc_id === ex.page.doc_id) : [];
  const chosen = ex?.fields.find((f) => f.field === field);
  return (
    <section ref={section} className="retrieval-trials scan-examples" id="claims-scan" aria-label="스캔 읽기 예시" data-testid="scan-examples">
      <div className="section-heading">
        <div>
          <span className="kicker">SCANNED PAGES / PRE-RENDERED</span>
          <h2>스캔한 서류는 이렇게 읽습니다</h2>
          <p>합성 서류를 스캔처럼 망가뜨려 읽은 결과를 미리 계산해 둔 예시입니다. 이 화면은 아무것도 읽지 않습니다. 공개 서버에는 글자 인식 엔진이 없습니다.</p>
        </div>
        <a className="secondary" href="/api/evaluations/scan-examples" download="fitwitness-scan-examples.json">예시 JSON</a>
      </div>
      {error ? <p role="alert">스캔 읽기 예시를 불러오지 못했습니다.</p> :
        !report ? <p role="status">스캔 읽기 예시 확인 중</p> :
        report.status !== "measured" || !ex ? <p role="status">아직 만들어 둔 예시가 없습니다.</p> : <>
        <div className="record-picker" role="group" aria-label="예시 고르기">
          {report.examples.map((e) => (
            <button key={e.id} type="button" className={e.id === ex.id ? "selected" : ""} aria-pressed={e.id === ex.id} onClick={() => pick(e.id)}>{e.title}</button>
          ))}
        </div>
        <article className="scan-example" aria-label={ex.title} data-testid="scan-example">
          <div className="scan-meta">
            <span>{ex.case_id}</span>
            <span>{docKindLabels[ex.page.doc_kind] || ex.page.doc_kind} 쪽</span>
            <span>스캔 상태 {LEVEL[ex.level] || ex.level}</span>
          </div>
          <p className="scan-note">{ex.note}</p>
          <div className="scan-board">
            <div className="scan-page">
              <div className="doc-image">
                <img src={`/api/evaluations/scan-examples/${ex.page.image.split("/").pop()}`} alt={`스캔한 ${docKindLabels[ex.page.doc_kind] || ex.page.doc_kind} 페이지`} width={ex.page.width} height={ex.page.height} />
                {onPage.map((f) => (
                  <button key={f.field} type="button" className={`scan-box ${verdict(f).cls} ${f.field === field ? "chosen" : ""}`} aria-label={`${f.label} 읽은 위치`}
                    onClick={() => setField(f.field)}
                    style={{ left: `${f.bbox![0] * 100}%`, top: `${f.bbox![1] * 100}%`, width: `${(f.bbox![2] - f.bbox![0]) * 100}%`, height: `${(f.bbox![3] - f.bbox![1]) * 100}%` }} />
                ))}
              </div>
              <p className="scan-legend"><i className="ok" /> 맞게 읽음 <i className="flag" /> 담당자 확인 <i className="wrong" /> 틀림</p>
            </div>
            <div className="scan-side">
              <div className="retrieval-table-wrap" tabIndex={0} role="region" aria-label="표 (좌우로 밀어 볼 수 있습니다)">
                <table className="scan-fields" aria-label="읽은 값과 정답" data-testid="scan-fields">
                  <thead><tr><th>항목</th><th>읽은 값</th><th>정답</th><th>판정</th></tr></thead>
                  <tbody>
                    {ex.fields.map((f) => {
                      const v = verdict(f);
                      const changed = f.first_read != null && f.read != null && f.first_read !== f.read;
                      return (
                        <tr key={f.field} className={`${v.cls} ${f.field === field ? "chosen" : ""}`} onClick={() => setField(f.field)} data-field={f.field}>
                          <th scope="row">{f.label}{f.decisive && <small title="지급 결정에 쓰는 값">지급에 쓰는 값</small>}{f.doc_kind && f.doc_id !== ex.page.doc_id && <small>{docKindLabels[f.doc_kind] || f.doc_kind}</small>}</th>
                          <td>
                            <b>{show(f, f.value)}</b>
                            {f.value == null && f.read != null && <small className="scan-first">인식한 글자 <code>{f.read}</code></small>}
                            {changed && <small className="scan-first">처음 읽은 값 <code>{f.first_read}</code></small>}
                            {f.read_conf != null && <small className="scan-conf">신뢰도 {f.read_conf.toFixed(2)}</small>}
                            {f.how.map((h) => <small key={h} className="scan-how">{HOW[h] || h}</small>)}
                          </td>
                          <td>{show(f, f.truth)}</td>
                          <td className={`scan-verdict ${v.cls}`}>{v.text}</td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
              {chosen?.read != null && <p className="doc-snippet">이 칸의 글자 인식 결과 <code>{chosen.read}</code></p>}
              <div className={`scan-result ${ex.outcome}`} data-testid="scan-result">
                <div><span className="eyebrow">결과</span><strong>{OUTCOME[ex.outcome]}</strong>{ex.outcome === "APPROVE" && <b>{won(ex.amount)}</b>}</div>
                <div><span className="eyebrow">정답</span><strong>{OUTCOME[ex.gold] || ex.gold}</strong>{ex.gold === "APPROVE" && <b>{won(ex.gold_amount)}</b>}</div>
                <div><span className="eyebrow">추출 신뢰도</span><strong>{ex.confidence.toFixed(2)}</strong><small>0.7 미만이면 사람에게</small></div>
              </div>
              {ex.reasons.length > 0 && (
                <ul className="claims-rules scan-reasons">
                  {ex.reasons.map((r) => <li key={r.rule_id + r.message} className={r.severity}><code>{r.rule_id}</code><span>{r.message}</span></li>)}
                </ul>
              )}
              <p className="scan-picked">이 예시를 고른 기준: {ex.picked_by}</p>
            </div>
          </div>
        </article>
        <ul className="record-caveats">{report.caveats.map((c) => <li key={c}>{c}</li>)}</ul>
      </>}
    </section>
  );
}
