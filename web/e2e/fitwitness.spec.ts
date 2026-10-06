import { test, expect } from "@playwright/test";
test("conditions, evidence and revision revalidation use real API", async ({
  page,
}) => {
  await page.goto("/");
  await expect(
    page.getByRole("heading", { name: "도면 검토대", exact: false }),
  ).toBeVisible();
  const guide = page.getByRole("region", { name: "3분 데모 가이드" });
  await expect(guide).toContainText("조건 검증");
  await expect(guide).toContainText("자동 무효화");
  await expect(page.getByText("아직 실행 전 · 조건 검증 후 모델·도구 이벤트를 확인할 수 있습니다.")).toBeVisible();
  await guide.getByRole("button", { name: "3분 체험 시작" }).click();
  await expect(page.locator("#review-request")).toBeInViewport();
  await page
    .getByRole("button", { name: "조건 검증 시작", exact: true })
    .click();
  await expect(page.getByText("검증 완료", { exact: true })).toBeVisible({
    timeout: 30000,
  });
  await expect(page.getByTestId("candidate-card").first()).toBeVisible();
  await page.getByTestId("candidate-card").first().click();
  await expect(page.getByTestId("evidence-panel")).toContainText("도면 근거");
  await page.getByRole("button", { name: "개정판 적용", exact: true }).click();
  await expect(page.getByText("재검증 필요", { exact: true })).toBeVisible();
  await page
    .getByRole("button", { name: "바뀐 도면으로 재검증", exact: true })
    .click();
  await expect(page.getByText("검증 완료", { exact: true })).toBeVisible({
    timeout: 30000,
  });
  const overflow = await page.evaluate(
    () => document.documentElement.scrollWidth > window.innerWidth + 1,
  );
  expect(overflow).toBe(false);
});
test("worker crash recovery is visible and completes once", async ({
  page,
}) => {
  await page.goto("/");
  await page.getByRole("checkbox", { name: "중간 중단 후 복구 체험" }).check();
  await page
    .getByRole("button", { name: "조건 검증 시작", exact: true })
    .click();
  await expect(page.getByText("검증 완료", { exact: true })).toBeVisible({
    timeout: 30000,
  });
  await page.getByRole("button", { name: "실행 기록 보기" }).click();
  await expect(page.getByTestId("run-timeline")).toContainText(
    "저장된 지점에서 재개",
  );
});
test("review workbench filters candidates and exposes measured experiment comparison", async ({
  page,
}) => {
  await page.goto("/");
  await expect(
    page.getByRole("heading", { name: "도면 검토대", exact: true }),
  ).toBeVisible();
  await page
    .getByRole("button", { name: "조건 검증 시작", exact: true })
    .click();
  await expect(page.getByText("검증 완료", { exact: true })).toBeVisible({
    timeout: 60000,
  });
  await page
    .getByRole("button", { name: "불일치만 보기", exact: true })
    .click();
  await expect(page.getByTestId("candidate-card")).toHaveCount(2);
  await page
    .getByRole("button", { name: "모든 후보 보기", exact: true })
    .click();
  await expect(page.getByTestId("candidate-card")).toHaveCount(5);
  await page.getByRole("button", { name: "실험실", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "평가 결과 비교", exact: true }),
  ).toBeVisible();
  await expect(page.getByTestId("experiment-comparison")).toContainText("Qwen");
  await expect(
    page.getByRole("button", { name: "오류 사례만", exact: true }),
  ).toBeVisible();
  const overflow = await page.evaluate(
    () => document.documentElement.scrollWidth > window.innerWidth + 1,
  );
  expect(overflow).toBe(false);
  await page.keyboard.press("Control+k");
  await expect(page.getByRole("textbox")).toBeFocused();
});

test("published API measurement selection and real agent traces", async ({
  page,
  request,
}) => {
  const catalog = await (await request.get("/api/evaluations/catalog")).json();
  test.skip(
    !catalog.experiments.some((e: { id: string }) => e.id === "claude"),
    "Claude experiment has not been published on this commit",
  );
  await page.goto("/");
  await page.getByRole("button", { name: "실험실", exact: true }).click();
  await page
    .getByRole("combobox", { name: "측정 모델" })
    .selectOption("claude");
  await expect(page.getByTestId("experiment-comparison")).toContainText(
    "claude-haiku-4-5",
  );
  await expect(page.getByTestId("experiment-comparison")).toContainText(
    "API LLM",
  );
  await page.getByText("재현 정보와 측정 범위", { exact: false }).click();
  await expect(page.getByText("API seed 미지정 · 반복 실행")).toBeVisible();
  await expect(
    page.getByRole("heading", { name: "실제 Agent 실행 비교", exact: true }),
  ).toBeVisible();
  await page
    .getByRole("button", { name: "탐색 + 반례 검토 · 1", exact: true })
    .click();
  await expect(page.locator(".agent-events")).toContainText("planner");
  await page
    .getByRole("combobox", { name: "Agent 실행 버전" })
    .selectOption("v1");
  await expect(
    page.getByRole("region", { name: "실제 Agent 실행 비교" }),
  ).toContainText("failed");
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth > window.innerWidth + 1,
    ),
  ).toBe(false);
});

test("retrieval comparison reveals modality limits and ranked evidence", async ({page,request}) => {
  const report=await (await request.get('/api/evaluations/retrieval')).json();
  test.skip(report.status!=='measured','retrieval measurement not published');
  await page.goto('/');
  await page.getByRole('button',{name:'실험실',exact:true}).click();
  const region=page.getByRole('region',{name:'도면 검색 방식 비교',exact:true});
  await expect(region).toBeVisible();
  await expect(region).toContainText('E5');
  await expect(region).toContainText('OpenCLIP');
  await region.getByRole('combobox',{name:'검색 질문 유형'}).selectOption('image');
  await expect(region.getByRole('img',{name:'실제 검색에 사용한 변형 도면'})).toBeVisible();
  await expect(region).toContainText('관련 도면');
  await expect(region.getByRole('table',{name:'검색 방식별 측정 지표'})).toContainText('0.0%');
  expect(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth+1)).toBe(false);
});

// Published experiments are required here: absence must fail, never skip.
test('reranking and visual success/error evidence are inspectable',async({page,request})=>{
  const ranking=await(await request.get('/api/evaluations/retrieval?experiment=reranking')).json();
  const vision=await(await request.get('/api/evaluations/vision')).json();
  expect(ranking.status).toBe('measured');expect(vision.status).toBe('measured');
  await page.goto('/');await page.getByRole('button',{name:'실험실',exact:true}).click();
  const region=page.getByRole('region',{name:'도면 검색 방식 비교',exact:true});
  await region.getByRole('combobox',{name:'검색 비교 실험'}).selectOption('reranking');
  await expect(region).toContainText('복합 + BGE 재정렬');
  await expect(region).toContainText('복합 + 조건 근거 정렬');
  await region.getByRole('combobox',{name:'검색 질문 유형'}).selectOption('paraphrase');
  await expect(region).toContainText('관련 도면');
  const v=page.getByRole('region',{name:'도면 이미지 읽기',exact:true});
  await v.getByRole('combobox',{name:'이미지 읽기 사례'}).selectOption('FW-F001-no_annotations');
  await expect(v.getByRole('img',{name:'실제 VLM에 전달한 도면 이미지'})).toBeVisible();
  await expect(v.getByRole('table')).toContainText('표기 없음');
  await v.getByRole('combobox',{name:'이미지 읽기 반복'}).selectOption('2');
  await v.getByText('실제 응답과 관측값',{exact:true}).click();
  await expect(v).toContainText('msg_');await expect(v).toContainText('검증 전');
  await v.getByRole('combobox',{name:'이미지 읽기 사례'}).selectOption('FW-F001-original');
  await v.getByRole('combobox',{name:'이미지 읽기 반복'}).selectOption('1');
  await expect(v.getByRole('status')).toContainText('실패 사례 · 평가에 포함');
  await expect(v.locator('pre')).toContainText('ImageReading');
  await expect(v).toContainText('msg_');
  expect(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth+1)).toBe(false);
});
test("a run that asks a reviewer pauses, takes verdicts and completes", async ({
  page,
}) => {
  await page.goto("/");
  await page
    .getByRole("checkbox", { name: "확인 필요 시 담당자에게 묻기" })
    .check();
  await page
    .getByRole("button", { name: "조건 검증 시작", exact: true })
    .click();
  const notice = page.getByTestId("review-notice");
  await expect(notice).toBeVisible({ timeout: 30000 });
  await expect(
    page.getByText("담당자 확인 대기", { exact: true }).first(),
  ).toBeVisible();
  const resume = notice.getByRole("button", { name: "답변 전송 후 재개" });
  await expect(resume).toBeDisabled();
  const selects = notice.getByRole("combobox");
  const pending = await selects.count();
  expect(pending).toBeGreaterThan(0);
  for (let i = 0; i < pending; i++) {
    await selects.nth(i).selectOption("mismatch");
  }
  await resume.click();
  await expect(page.getByText("검증 완료", { exact: true })).toBeVisible({
    timeout: 30000,
  });
  await page.getByRole("button", { name: "실행 기록 보기" }).click();
  await expect(page.getByTestId("run-timeline")).toContainText(
    "담당자 답변으로 재개",
  );
});
test("other part kinds are in the workspace and retrieval excludes them", async ({
  page,
}) => {
  await page.goto("/");
  await expect(
    page.getByRole("button", { name: "조건 검증 시작", exact: true }),
  ).toBeEnabled();
  await expect(page.getByTestId("candidate-card")).toHaveCount(20);
  await expect(page.locator(".collection-details")).toContainText("플랜지 5");
  await page.getByLabel("찾는 부품").fill("플랜지 연결판");
  await page
    .getByRole("button", { name: "조건 검증 시작", exact: true })
    .click();
  await expect(page.getByText("검증 완료", { exact: true })).toBeVisible({
    timeout: 60000,
  });
  const cards = page.getByTestId("candidate-card");
  await expect(cards).toHaveCount(5);
  await expect(cards.first()).toContainText("FW-001-");
  await page.getByRole("button", { name: /검색 제외 15개 보기/ }).click();
  await expect(page.getByTestId("excluded-card")).toHaveCount(15);
  await page.getByLabel("찾는 부품").fill("존재하지 않는 부품 이름");
  await page
    .getByRole("button", { name: "조건 검증 시작", exact: true })
    .click();
  await expect(page.getByTestId("empty-result")).toBeVisible({
    timeout: 60000,
  });
  await expect(page.getByTestId("candidate-card")).toHaveCount(0);
  await expect(page.getByRole("button", { name: /검색 제외 20개 보기/ })).toBeVisible();
});
