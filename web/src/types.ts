export type Fact = {
  id: string;
  field: string;
  value: string | { low: string; high: string };
  unit?: string;
  source: { bbox?: number[]; source_hash?: string; page?: number };
};
export type Doc = {
  id: string;
  drawing_number: string;
  revision_label: string;
  title: string;
  active: boolean;
  facts: Fact[];
  supersedes?: string;
  source_hash: string;
  kind?: string;
};
export type Evidence = {
  field: string;
  verdict: string;
  summary: string;
  source_refs: { bbox?: number[] }[];
};
export type Decision = {
  revision_id: string;
  verdict: string;
  evidence: Evidence[];
  reviewed_by?: string | null;
  review_note?: string | null;
};
export type Run = {
  id: string;
  state: string;
  decisions: Decision[];
  snapshot_id: string;
  error?: string;
  question?: string | null;
  attempts?: number;
  next_attempt_at?: string | null;
  usage: {
    tool_calls: number;
    model_calls: number;
    input_tokens: number;
    output_tokens: number;
  };
};
export type RunEvent = {
  seq: number;
  kind: string;
  timestamp: string;
  payload: Record<string, unknown>;
};
export const labels: Record<string, string> = {
  match: "조건 일치",
  mismatch: "조건 불일치",
  unknown: "확인 필요",
};
export const kindLabels: Record<string, string> = {
  bracket: "브래킷",
  flange: "플랜지",
  shaft: "샤프트",
  housing: "하우징",
};
export const fields: Record<string, string> = {
  hole_spacing: "구멍 간격",
  material: "소재",
  width: "너비",
  kind: "부품 종류",
  height: "높이",
  thickness: "두께",
};
export const eventNames: Record<string, string> = {
  queued: "요청 접수",
  started: "Worker 시작",
  intent: "요구 조건 추출",
  retrieved: "후보 검색",
  tool: "근거 조회",
  verified: "조건 판정",
  completed: "결과 저장",
  interrupted: "Worker 중단",
  resumed: "저장된 지점에서 재개",
  stale: "개정 감지",
  model: "모델 응답",
  failed: "실행 실패",
  cancelled: "취소",
  waiting_input: "담당자 확인 대기",
  resumed_by_human: "담당자 답변으로 재개",
  human_review: "담당자 판정 반영",
  retry_scheduled: "일시 오류 · 재시도 예약",
  dead_lettered: "재시도 소진 · 보류함 이동",
  retry_wait: "모델 응답 대기",
  model_error: "모델 호출 오류",
  model_schema_error: "모델 응답 형식 오류",
  agent_plan: "에이전트 계획",
  tool_skipped: "도구 호출 생략",
  budget_stop: "예산 한도 도달",
  evidence_exhausted: "추가 근거 없음",
};
export type ReviewPending = {
  revision_id: string;
  drawing_number: string;
  unknown_fields: string[];
};
export async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const r = await fetch("/api" + path, {
    ...init,
    headers: { "Content-Type": "application/json", ...init?.headers },
  });
  if (!r.ok) {
    const x = await r.json().catch(() => ({ detail: r.statusText }));
    throw Error(
      typeof x.detail === "string" ? x.detail : JSON.stringify(x.detail),
    );
  }
  return r.json();
}
export const asset = (id: string, type: string) =>
  `/api/documents/${id}/assets/${type}`;
export const fmt = (f?: Fact) =>
  !f
    ? "표기 없음"
    : typeof f.value === "string"
      ? f.value
      : `${f.value.low}${f.value.low === f.value.high ? "" : `–${f.value.high}`} ${f.unit || ""}`;
