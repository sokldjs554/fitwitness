import { lazy, Suspense, useEffect, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import {
  ArrowUpRight,
  ArrowRight,
  ChevronRight,
  ScanLine,
  Activity,
  RotateCcw,
  Box,
  FileText,
  AlertTriangle,
  Search,
  X,
  GitBranch,
  FlaskConical,
  PanelLeftClose,
  Maximize2,
  Plus,
  Minus,
  Play,
  Check,
  Command,
  FolderClosed,
  ArrowDownToLine,
} from "lucide-react";
import "@fontsource-variable/noto-sans-kr";
import "@fontsource-variable/dm-sans";
import "./style.css";
import {
  api,
  asset,
  fmt,
  labels,
  fields,
  eventNames,
  type Doc,
  type Run,
  type RunEvent,
} from "./types";
const Model = lazy(() =>
  import("./components/Model").then((m) => ({ default: m.Model })),
);
const ExperimentLab = lazy(() =>
  import("./components/ExperimentLab").then((m) => ({
    default: m.ExperimentLab,
  })),
);

function App() {
  const [ready, setReady] = useState(false),
    [docs, setDocs] = useState<Doc[]>([]),
    [query, setQuery] = useState("장비에 고정할 브래킷을 찾아줘"),
    [spacing, setSpacing] = useState("40"),
    [material, setMaterial] = useState("SUS304"),
    [run, setRun] = useState<Run | null>(null),
    [events, setEvents] = useState<RunEvent[]>([]),
    [selected, setSelected] = useState(""),
    [field, setField] = useState("hole_spacing"),
    [view, setView] = useState("2d"),
    [fault, setFault] = useState(false),
    [error, setError] = useState(""),
    [busy, setBusy] = useState(false),
    [timeline, setTimeline] = useState(false),
    [revised, setRevised] = useState(false),
    [tab, setTab] = useState<"work" | "eval">("work"),
    [filter, setFilter] = useState("all"),
    [zoom, setZoom] = useState(1),
    [eventSelection, setEventSelection] = useState<number | null>(null),
    [hiddenRail, setHiddenRail] = useState(false);
  const initialized = useRef(false);
  const queryRef = useRef<HTMLTextAreaElement>(null);
  const focusQueryOnMount = useRef(false);
  const reload = async () => {
    const d = await api<Doc[]>("/documents");
    setDocs(d);
    return d;
  };
  async function initialize() {
    setError("");
    try {
      await api("/demo-sessions", { method: "POST" });
      const d = await reload();
      setSelected(d.find((x) => x.active)?.id || "");
      setReady(true);
    } catch (e) {
      setError(String(e));
    }
  }
  useEffect(() => {
    if (!initialized.current) {
      initialized.current = true;
      void initialize();
    }
  }, []);
  useEffect(() => {
    const key = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        focusQueryOnMount.current = !queryRef.current;
        setTab("work");
        queryRef.current?.focus();
      }
    };
    window.addEventListener("keydown", key);
    return () => window.removeEventListener("keydown", key);
  }, []);
  useEffect(() => {
    if (tab === "work" && focusQueryOnMount.current) {
      queryRef.current?.focus();
      focusQueryOnMount.current = false;
    }
  }, [tab]);
  useEffect(() => {
    if (!run || !["queued", "running", "retry_wait"].includes(run.state))
      return;
    let stopped = false;
    const poll = async () => {
      try {
        const [r, e] = await Promise.all([
          api<Run>("/runs/" + run.id),
          api<RunEvent[]>("/runs/" + run.id + "/events"),
        ]);
        if (!stopped) {
          setRun(r);
          setEvents(e);
          if (r.error) setError(r.error);
        }
      } catch (e) {
        if (!stopped) setError(String(e));
      }
    };
    const t = setInterval(poll, 1000);
    void poll();
    return () => {
      stopped = true;
      clearInterval(t);
    };
  }, [run?.id, run?.state]);
  const running =
    busy || (!!run && ["queued", "running", "retry_wait"].includes(run.state));
  async function start() {
    setError("");
    setBusy(true);
    setEvents([]);
    setFilter("all");
    setEventSelection(null);
    try {
      const value = Number(spacing);
      if (!Number.isFinite(value) || value <= 0)
        throw Error("간격은 0보다 큰 숫자로 입력해 주세요.");
      const r = await api<Run>("/runs", {
        method: "POST",
        headers: {
          "Idempotency-Key": crypto.randomUUID(),
          ...(fault ? { "x-demo-fault": "1" } : {}),
        },
        body: JSON.stringify({
          search: {
            text: query + " 브래킷",
            requirements: [
              {
                field: "hole_spacing",
                value: { low: String(value), high: String(value) },
                unit: "mm",
              },
              { field: "material", value: material },
            ],
          },
          provider: "rules",
        }),
      });
      setRun(r);
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  }
  async function revise() {
    setBusy(true);
    setError("");
    try {
      const result = await api<{ revision: Doc }>("/demo/revision", {
        method: "POST",
      });
      setRevised(true);
      await reload();
      setSelected(result.revision.id);
      if (run) setRun(await api<Run>("/runs/" + run.id));
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  }
  const decisions = run?.decisions || [];
  const doc = docs.find((d) => d.id === selected);
  const decision = decisions.find((d) => d.revision_id === selected);
  const evidence = decision?.evidence.find((e) => e.field === field);
  const bbox = evidence?.source_refs[0]?.bbox;
  const active = docs.filter((d) => d.active);
  const state =
    run?.state === "completed"
      ? "검증 완료"
      : run?.state === "stale"
        ? "재검증 필요"
        : running
          ? "검증 실행 중"
          : run?.state === "failed"
            ? "실행 실패"
            : run?.state === "cancelled"
              ? "실행 취소"
              : "검증 대기";
  const candidates = (
    decisions.length
      ? decisions.map((d) => ({
          doc: docs.find((x) => x.id === d.revision_id),
          decision: d,
        }))
      : active.map((doc) => ({ doc, decision: undefined }))
  ).filter(
    (x) => x.doc && (filter === "all" || x.decision?.verdict === filter),
  );
  const inspectedEvent =
    events.find((e) => e.seq === eventSelection) || events.at(-1);
  const elapsed =
    events.length > 1
      ? (
          (Date.parse(events.at(-1)!.timestamp) -
            Date.parse(events[0].timestamp)) /
          1000
        ).toFixed(1)
      : "—";
  function choose(id: string) {
    setSelected(id);
    setField("hole_spacing");
    setView("2d");
    setZoom(1);
  }
  return (
    <div className={`app-shell ${hiddenRail ? "rail-collapsed" : ""}`}>
      <aside className="navigation">
        <a className="brand" href="/" aria-label="FitWitness 홈">
          <span className="brand-mark">
            fw<span>.</span>
          </span>
          <div>
            FitWitness<small>ENGINEERING LAB</small>
          </div>
        </a>
        <div className="project-switch">
          <span className="project-icon">T</span>
          <div>
            Tellus research<small>제조 도면 / 합성 데이터</small>
          </div>
          <ChevronRight size={13} />
        </div>
        <div className="nav-label">WORKSPACE</div>
        <nav>
          <button
            aria-label="검토대"
            className={tab === "work" ? "active" : ""}
            onClick={() => setTab("work")}
          >
            <ScanLine size={16} />
            <span>검토대</span>
            <kbd>01</kbd>
          </button>
          <button
            aria-label="실험실"
            className={tab === "eval" ? "active" : ""}
            onClick={() => setTab("eval")}
          >
            <FlaskConical size={16} />
            <span>실험실</span>
            <kbd>02</kbd>
          </button>
          <button
            onClick={() => {
              setTab("work");
              setTimeline(true);
            }}
          >
            <Activity size={16} />
            <span>실행 기록</span>
            {events.length > 0 && <b>{events.length}</b>}
          </button>
        </nav>
        <div className="nav-label collections-label">COLLECTION</div>
        <div className="collection-item">
          <FolderClosed size={15} />
          <span>Mounting brackets</span>
          <code>{active.length.toString().padStart(2, "0")}</code>
        </div>
        <div className="collection-details">
          <span>PDF · STEP · 개정 이력</span>
          <span className="connection">
            <i className={ready ? "connected" : ""} />
            {ready ? "PostgreSQL 연결됨" : "체험 공간 연결 중"}
          </span>
        </div>
        <div className="nav-bottom">
          <div className="workspace-note">
            <span>판정의 기준</span>
            <p>
              형상이 닮았어도
              <br />
              근거가 다르면 다른 부품.
            </p>
          </div>
          <a
            href="https://github.com/sokldjs554/fitwitness/tree/feat/fitwitness-foundation"
            target="_blank"
            rel="noreferrer"
          >
            소스와 재현 방법 <ArrowUpRight size={13} />
          </a>
          <small>FITWITNESS / RESEARCH PREVIEW</small>
        </div>
      </aside>
      <div className="main-area">
        <header className="topbar">
          <button
            className="icon-button rail-toggle"
            aria-label="탐색 메뉴 접기"
            onClick={() => setHiddenRail((v) => !v)}
          >
            <PanelLeftClose size={16} />
          </button>
          <div className="breadcrumb">
            Research <ChevronRight size={12} /> Manufacturing{" "}
            <ChevronRight size={12} />
            <strong>{tab === "work" ? "Drawing review" : "Experiments"}</strong>
          </div>
          <div className="topbar-right">
            <span className="live-tag">
              <i />
              {tab === "work" ? "LIVE WORKSPACE" : "RECORDED EVALUATION"}
            </span>
            <span className="avatar">FW</span>
          </div>
        </header>
        <main>
          {tab === "eval" ? (
            <Suspense
              fallback={
                <div className="loading-panel">실험실을 불러오는 중…</div>
              }
            >
              <ExperimentLab />
            </Suspense>
          ) : (
            <>
              <div className="page-heading work-heading">
                <div>
                  <span className="kicker">
                    MANUFACTURING / EVIDENCE REVIEW
                  </span>
                  <h1 aria-label="도면 검토대">
                    도면 검토대<span className="version-chip">v0.2</span>
                  </h1>
                  <p>조건을 지정하고, 후보의 원본 근거를 대조하세요.</p>
                </div>
                <div className="work-heading-meta">
                  <span>ACTIVE COLLECTION</span>
                  <strong>
                    {active.length.toString().padStart(2, "0")}{" "}
                    <small>DRAWINGS</small>
                  </strong>
                </div>
              </div>
              {error && (
                <div role="alert" className="notice danger">
                  <AlertTriangle size={16} />
                  <span>{error}</span>
                  {!ready && <button onClick={initialize}>다시 연결</button>}
                  <button
                    className="icon-button"
                    aria-label="오류 닫기"
                    onClick={() => setError("")}
                  >
                    <X size={14} />
                  </button>
                </div>
              )}
              <section className="request-bar" aria-label="검토 조건">
                <div className="query-control">
                  <label htmlFor="query">
                    <Search size={13} /> 찾는 부품{" "}
                    <kbd>
                      <Command size={9} /> K
                    </kbd>
                  </label>
                  <textarea
                    ref={queryRef}
                    id="query"
                    value={query}
                    onChange={(e) => setQuery(e.target.value)}
                    rows={1}
                  />
                </div>
                <div className="dimension-control">
                  <label htmlFor="spacing">구멍 중심 간격</label>
                  <div className="unit-input">
                    <input
                      id="spacing"
                      type="number"
                      min="0.01"
                      step="0.1"
                      value={spacing}
                      onChange={(e) => setSpacing(e.target.value)}
                    />
                    <span>mm</span>
                  </div>
                </div>
                <div className="material-control">
                  <label htmlFor="material">소재</label>
                  <select
                    id="material"
                    value={material}
                    onChange={(e) => setMaterial(e.target.value)}
                  >
                    <option>SUS304</option>
                    <option>AL6061</option>
                  </select>
                </div>
                <button
                  className="primary"
                  onClick={start}
                  disabled={!ready || running}
                >
                  <Play size={13} />
                  {running ? "검증 중…" : "조건 검증 시작"}
                  <ArrowRight size={14} />
                </button>
              </section>
              <div className="workspace-status">
                <div>
                  <span
                    className={`status-dot ${running ? "pulse" : ""} ${run?.state === "stale" ? "warning" : ""}`}
                  />
                  <strong>{state}</strong>
                  {run && <code>{run.id.slice(0, 8)}</code>}
                </div>
                <span>
                  규칙 기반 · 실시간 PDF 대조{" "}
                  <span className="status-separator">/</span> LLM 측정은
                  실험실에서
                </span>
              </div>
              {run?.state === "stale" && (
                <div className="notice stale">
                  <GitBranch size={17} />
                  <span>
                    <b>개정판이 도착했습니다.</b> 이전 판정은 더 이상 유효하지
                    않습니다.
                  </span>
                  <button onClick={start} disabled={running}>
                    바뀐 도면으로 재검증 <RotateCcw size={13} />
                  </button>
                </div>
              )}
              <div className="review-board">
                <section className="drawing-panel">
                  <div className="sheet-toolbar">
                    <div>
                      <FileText size={15} />
                      <strong>{doc?.drawing_number || "도면 선택"}</strong>
                      {doc && (
                        <span className="revision">
                          REV. {doc.revision_label}
                        </span>
                      )}
                    </div>
                    <div className="segmented">
                      <button
                        className={view === "2d" ? "on" : ""}
                        onClick={() => setView("2d")}
                      >
                        <FileText size={12} /> 2D 원본
                      </button>
                      <button
                        className={view === "3d" ? "on" : ""}
                        onClick={() => setView("3d")}
                        disabled={!doc}
                      >
                        <Box size={13} /> 3D 형상
                      </button>
                    </div>
                    {doc && (
                      <a
                        className="icon-button"
                        href={asset(doc.id, "pdf")}
                        target="_blank"
                        rel="noreferrer"
                        title="원본 PDF 열기"
                      >
                        <ArrowUpRight size={16} />
                      </a>
                    )}
                  </div>
                  <div
                    className={`drawing-stage ${view === "3d" ? "three-stage" : ""}`}
                  >
                    {doc ? (
                      view === "2d" ? (
                        <>
                          <div className="sheet-corner">
                            SOURCE DOCUMENT <span>01 / 01</span>
                          </div>
                          <div
                            className="paper"
                            style={{ width: `${Math.round(zoom * 88)}%` }}
                          >
                            <img
                              src={asset(doc.id, "png")}
                              alt="원본 도면과 근거 영역"
                            />
                            {bbox && (
                              <span
                                className={`bbox ${evidence?.verdict}`}
                                style={{
                                  left: bbox[0] * 100 + "%",
                                  top: bbox[1] * 100 + "%",
                                  width: (bbox[2] - bbox[0]) * 100 + "%",
                                  height: (bbox[3] - bbox[1]) * 100 + "%",
                                }}
                              />
                            )}
                          </div>
                          <div className="canvas-caption">
                            <span>원본 표기 · 추출 영역 표시</span>
                            <div className="zoom-control">
                              <button
                                aria-label="도면 축소"
                                onClick={() =>
                                  setZoom((v) => Math.max(0.65, v - 0.15))
                                }
                              >
                                <Minus size={13} />
                              </button>
                              <code>{Math.round(zoom * 100)}%</code>
                              <button
                                aria-label="도면 확대"
                                onClick={() =>
                                  setZoom((v) => Math.min(1.6, v + 0.15))
                                }
                              >
                                <Plus size={13} />
                              </button>
                              <button
                                aria-label="도면 크기 초기화"
                                onClick={() => setZoom(1)}
                              >
                                <Maximize2 size={12} />
                              </button>
                            </div>
                          </div>
                        </>
                      ) : (
                        <Suspense
                          fallback={
                            <div className="loading-panel">
                              3D 뷰어를 불러오는 중…
                            </div>
                          }
                        >
                          <Model id={doc.id} />
                        </Suspense>
                      )
                    ) : (
                      <div className="loading-panel">
                        <ScanLine size={30} />
                        <h2>도면을 불러오고 있습니다</h2>
                        <p>방문자별 독립된 검토 공간을 준비합니다.</p>
                      </div>
                    )}
                  </div>
                  <div className="sheet-properties">
                    <div>
                      <span>DRAWING</span>
                      <strong>{doc?.drawing_number || "—"}</strong>
                    </div>
                    <div>
                      <span>MATERIAL</span>
                      <strong>
                        {fmt(doc?.facts.find((f) => f.field === "material"))}
                      </strong>
                    </div>
                    <div>
                      <span>REVISION</span>
                      <strong>
                        {doc?.revision_label || "—"}{" "}
                        <small>{doc?.active ? "유효" : "이전"}</small>
                      </strong>
                    </div>
                    <div>
                      <span>SOURCE</span>
                      <code>{doc?.source_hash?.slice(0, 10) || "—"}</code>
                    </div>
                  </div>
                  <div className="sheet-footer">
                    <span>합성 CAD에서 생성한 실제 PDF / STEP</span>
                    {doc && (
                      <a
                        href={asset(doc.id, "step")}
                        download={`${doc.drawing_number}.step`}
                      >
                        <ArrowDownToLine size={12} /> STEP 원본
                      </a>
                    )}
                  </div>
                </section>
                <aside className="review-sidebar">
                  <section className="candidate-panel">
                    <div className="section-bar">
                      <h2>
                        후보 도면 <span>{active.length}</span>
                      </h2>
                      <div className="candidate-filters">
                        <button
                          aria-label="모든 후보 보기"
                          title="모든 후보 보기"
                          className={filter === "all" ? "selected" : ""}
                          onClick={() => setFilter("all")}
                        >
                          전체
                        </button>
                        <button
                          aria-label="불일치만 보기"
                          title="불일치만 보기"
                          className={filter === "mismatch" ? "selected" : ""}
                          onClick={() => setFilter("mismatch")}
                        >
                          불일치
                        </button>
                      </div>
                    </div>
                    <div className="candidate-columns">
                      <span>도번 / 개정</span>
                      <span>간격 · 판정</span>
                    </div>
                    <div className="candidates">
                      {candidates.map(({ doc: d, decision: c }) => (
                        <button
                          key={d!.id}
                          data-testid="candidate-card"
                          className={`candidate ${selected === d!.id ? "selected" : ""} ${run?.state === "stale" ? "outdated" : ""}`}
                          onClick={() => choose(d!.id)}
                        >
                          <div className="drawing-thumb">
                            <img
                              src={asset(d!.id, "png")}
                              alt={`${d!.drawing_number} 기술 도면`}
                            />
                          </div>
                          <div className="candidate-info">
                            <strong>
                              {d!.drawing_number}
                              <span className="revision">
                                {d!.revision_label}
                              </span>
                            </strong>
                            <small>
                              {fmt(
                                d!.facts.find((f) => f.field === "material"),
                              )}
                            </small>
                          </div>
                          <div className="candidate-result">
                            <code>
                              {fmt(
                                d!.facts.find(
                                  (f) => f.field === "hole_spacing",
                                ),
                              )}
                            </code>
                            <span
                              className={`verdict-text ${c?.verdict || ""}`}
                            >
                              {run?.state === "stale"
                                ? "이전 판정"
                                : c
                                  ? labels[c.verdict]
                                  : "검증 전"}
                            </span>
                          </div>
                        </button>
                      ))}
                      {!candidates.length && (
                        <p className="empty-note">
                          {ready
                            ? "해당 조건의 후보가 없습니다."
                            : "도면 로딩 중…"}
                        </p>
                      )}
                    </div>
                    {decisions.length > 0 && (
                      <div className="counts">
                        {["match", "mismatch", "unknown"].map((v) => (
                          <span className={v} key={v}>
                            <i />
                            {labels[v]}{" "}
                            <b>
                              {decisions.filter((d) => d.verdict === v).length}
                            </b>
                          </span>
                        ))}
                      </div>
                    )}
                  </section>
                  <section
                    className="evidence-panel"
                    data-testid="evidence-panel"
                  >
                    <div className="section-bar">
                      <h2>도면 근거</h2>
                      <span>{doc?.drawing_number || "선택 대기"}</span>
                    </div>
                    {decision ? (
                      <>
                        <div className="evidence-rows">
                          {decision.evidence.map((e) => (
                            <button
                              key={e.field}
                              className={`evidence-row ${field === e.field ? "chosen" : ""}`}
                              onClick={() => {
                                setField(e.field);
                                setView("2d");
                              }}
                            >
                              <div>
                                <span>{fields[e.field] || e.field}</span>
                                <strong>
                                  {fmt(
                                    doc?.facts.find((f) => f.field === e.field),
                                  )}
                                </strong>
                              </div>
                              <span
                                className={`evidence-symbol ${e.verdict}`}
                                aria-label={labels[e.verdict]}
                              >
                                {e.verdict === "match" ? (
                                  <Check size={14} />
                                ) : e.verdict === "mismatch" ? (
                                  <X size={14} />
                                ) : (
                                  <Minus size={14} />
                                )}
                              </span>
                            </button>
                          ))}
                        </div>
                        {evidence && (
                          <div className={`evidence-note ${evidence.verdict}`}>
                            <span className="note-rule" />
                            <div>
                              <strong>
                                {run?.state === "stale"
                                  ? "이전 개정판의 판단입니다"
                                  : evidence.summary}
                              </strong>
                              <p>
                                {evidence.source_refs.length
                                  ? "도면의 강조된 영역에서 원본 표기를 확인하세요."
                                  : "필수 조건을 확인할 표기가 없습니다. 추가 근거가 필요합니다."}
                              </p>
                            </div>
                          </div>
                        )}
                      </>
                    ) : (
                      <div className="evidence-empty">
                        <ScanLine size={20} />
                        <p>
                          검증하면 조건별 판정과
                          <br />
                          도면의 근거 위치가 연결됩니다.
                        </p>
                      </div>
                    )}
                    <div className="evidence-provenance">
                      <span>SNAPSHOT</span>
                      <code>
                        {run?.snapshot_id.slice(0, 14) || "검증 실행 전"}
                      </code>
                    </div>
                  </section>
                </aside>
              </div>
              <section className="revision-strip">
                <div className="revision-icon">
                  <GitBranch size={18} />
                </div>
                <div>
                  <strong>개정판 도착 시뮬레이션</strong>
                  <p>
                    FW-000-0 <span>A</span> <ArrowRight size={11} />{" "}
                    <span>B</span> · 구멍 간격 <b>40 → 42 mm</b>
                  </p>
                </div>
                <button
                  className="secondary"
                  disabled={!ready || running || revised}
                  onClick={revise}
                >
                  {revised ? "Rev. B 적용됨" : "개정판 적용"}
                  <ArrowUpRight size={13} />
                </button>
              </section>
              <section className={`run-panel ${timeline ? "expanded" : ""}`}>
                <div className="run-bar">
                  <button
                    className="run-toggle"
                    onClick={() => setTimeline((v) => !v)}
                  >
                    <Activity size={15} />
                    <b>실행 기록 보기</b>
                    <span>{events.length} events</span>
                    <ChevronRight
                      size={14}
                      className={timeline ? "rotated" : ""}
                    />
                  </button>
                  <div className="run-summary">
                    <span>
                      도구 <b>{run?.usage.tool_calls || 0}</b>
                    </span>
                    <span>
                      모델 <b>{run?.usage.model_calls || 0}</b>
                    </span>
                    <span>
                      경과 <b>{elapsed}s</b>
                    </span>
                  </div>
                  <label className="fault-toggle">
                    <input
                      type="checkbox"
                      checked={fault}
                      onChange={(e) => setFault(e.target.checked)}
                      disabled={running}
                    />
                    중간 중단 후 복구 체험
                  </label>
                  {run && running && (
                    <button
                      className="text-button"
                      onClick={() =>
                        api<Run>(`/runs/${run.id}/cancel`, { method: "POST" })
                          .then(setRun)
                          .catch((e) => setError(String(e)))
                      }
                    >
                      실행 취소
                    </button>
                  )}
                </div>
                {timeline && (
                  <div className="trace-layout">
                    <ol data-testid="run-timeline" className="timeline">
                      {events.map((e) => (
                        <li key={e.seq}>
                          <button
                            className={
                              inspectedEvent?.seq === e.seq ? "active" : ""
                            }
                            onClick={() => setEventSelection(e.seq)}
                          >
                            <code>{String(e.seq).padStart(2, "0")}</code>
                            <span className={`event-node ${e.kind}`} />
                            <strong>{eventNames[e.kind] || e.kind}</strong>
                            <time>
                              {new Date(e.timestamp).toLocaleTimeString(
                                "ko-KR",
                                { hour12: false },
                              )}
                            </time>
                          </button>
                        </li>
                      ))}
                      {!events.length && (
                        <p className="empty-note">
                          검증을 실행하면 실제 이벤트가 기록됩니다.
                        </p>
                      )}
                    </ol>
                    <div className="event-detail">
                      {inspectedEvent ? (
                        <>
                          <span className="inspector-label">
                            EVENT /{" "}
                            {String(inspectedEvent.seq).padStart(2, "0")}
                          </span>
                          <h3>
                            {eventNames[inspectedEvent.kind] ||
                              inspectedEvent.kind}
                          </h3>
                          <pre>
                            {JSON.stringify(inspectedEvent.payload, null, 2)}
                          </pre>
                        </>
                      ) : (
                        <p>
                          실행 단계의 입력과 관측 결과를 확인할 수 있습니다.
                        </p>
                      )}
                    </div>
                  </div>
                )}
              </section>
              <footer>
                <span>
                  FITWITNESS <i /> 근거가 있는 판단, 변경에도 추적 가능한 결과.
                </span>
                <button onClick={() => setTab("eval")}>
                  LLM 평가 결과 보기 <ArrowUpRight size={12} />
                </button>
              </footer>
            </>
          )}
        </main>
      </div>
    </div>
  );
}
createRoot(document.getElementById("root")!).render(<App />);
