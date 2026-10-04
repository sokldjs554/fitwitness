import { test, expect } from "@playwright/test";
test("conditions, evidence and revision revalidation use real API", async ({
  page,
}) => {
  await page.goto("/");
  await expect(
    page.getByRole("heading", { name: "도면 검토대", exact: false }),
  ).toBeVisible();
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
