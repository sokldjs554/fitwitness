import { test, expect, type Page } from "@playwright/test";

async function openDesk(page: Page) {
  await page.goto("/");
  await expect(page.getByRole("button", { name: "조건 검증 시작", exact: true })).toBeEnabled();
  await page.getByRole("button", { name: "청구 심사", exact: true }).click();
  await expect(page.getByRole("heading", { name: "청구 심사대", exact: true })).toBeVisible();
  await expect(page.getByTestId("claim-case").first()).toBeVisible();
}

test("a clean claim is adjudicated, explained and paid exactly once", async ({ page }) => {
  test.setTimeout(120000);
  await openDesk(page);
  await page.getByTestId("claim-case").filter({ hasText: "정상 청구" }).first().click();
  await page.getByRole("button", { name: "자동 심사 시작", exact: true }).click();
  await expect(page.getByTestId("claim-state")).toHaveText("심사 완료", { timeout: 90000 });
  await expect(page.getByTestId("claim-decision")).toContainText("지급");
  await expect(page.getByTestId("claim-payout")).toHaveText("지급 완료");
  await expect(page.getByTestId("claim-rules")).toContainText("R-");
  await expect(page.getByTestId("claim-fields").locator("tr").first()).toBeVisible();
  await expect(page.getByTestId("claim-explain")).toContainText("지급합니다");
  await expect(page.getByTestId("claims-ledger")).toContainText("1건");
});

test("a claim above the auto-approve limit waits for a reviewer and follows the answer", async ({ page }) => {
  test.setTimeout(120000);
  await openDesk(page);
  await page.getByTestId("claim-case").filter({ hasText: "자동승인 한도 초과" }).first().click();
  await page.getByRole("button", { name: "자동 심사 시작", exact: true }).click();
  await expect(page.getByTestId("claim-review")).toBeVisible({ timeout: 90000 });
  await expect(page.getByTestId("claim-state")).toHaveText("담당자 확인 대기");
  await page.getByLabel("검토 메모").fill("진단서와 영수증 확인");
  await page.getByRole("button", { name: /승인 · 지급/ }).click();
  await expect(page.getByTestId("claim-state")).toHaveText("심사 완료", { timeout: 90000 });
  await expect(page.getByTestId("claim-decision")).toContainText("심사대 사용자");
  await expect(page.getByTestId("claim-payout")).toHaveText("지급 완료");
});
