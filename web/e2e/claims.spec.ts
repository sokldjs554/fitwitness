import { test, expect, type Page } from "@playwright/test";

async function openDesk(page: Page) {
  await page.goto("/");
  // The workspace is seeded with 20 drawings and 8 claim cases on first load.
  await expect(page.getByRole("button", { name: "조건 검증 시작", exact: true })).toBeEnabled({ timeout: 60000 });
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

test("a claim above the auto-approve limit needs a reason and two different people", async ({ page }) => {
  test.setTimeout(120000);
  await openDesk(page);
  await page.getByTestId("claim-case").filter({ hasText: "자동승인 한도 초과" }).first().click();
  await page.getByRole("button", { name: "자동 심사 시작", exact: true }).click();
  await expect(page.getByTestId("claim-review")).toBeVisible({ timeout: 90000 });
  await expect(page.getByTestId("claim-state")).toHaveText("담당자 확인 대기");
  await expect(page.getByTestId("claim-tier")).toContainText("서로 다른 두 담당자");
  // no reason, no approval
  const approve = page.getByRole("button", { name: /1차 승인/ });
  await expect(approve).toBeDisabled();
  await page.getByLabel("검토 메모").fill("진단서와 영수증 확인");
  await approve.click();
  // nothing is paid on one signature; the second question names the first approver
  await expect(page.getByTestId("claim-review")).toContainText("2차 승인이 필요합니다", { timeout: 60000 });
  await expect(page.getByTestId("claim-state")).toHaveText("담당자 확인 대기");
  await expect(page.getByTestId("claims-ledger")).toContainText("0건");
  // the same name cannot approve twice
  await page.getByLabel("검토 메모").fill("같은 사람이 다시 승인");
  await expect(page.getByTestId("claim-same-person")).toBeVisible();
  await expect(page.getByRole("button", { name: /2차 승인/ })).toBeDisabled();
  await page.getByLabel("담당자 이름").fill("상급 검토자");
  await page.getByRole("button", { name: /2차 승인/ }).click();
  await expect(page.getByTestId("claim-state")).toHaveText("심사 완료", { timeout: 90000 });
  await expect(page.getByTestId("claim-decision")).toContainText("심사대 사용자 · 상급 검토자");
  await expect(page.getByTestId("claim-payout")).toHaveText("지급 완료");
  await expect(page.getByTestId("claims-ledger")).toContainText("1건");
});

test("a standard review is answered by one person", async ({ page }) => {
  test.setTimeout(120000);
  await openDesk(page);
  await page.getByTestId("claim-case").filter({ hasText: "서류 누락" }).first().click();
  await page.getByRole("button", { name: "자동 심사 시작", exact: true }).click();
  await expect(page.getByTestId("claim-review")).toBeVisible({ timeout: 90000 });
  await expect(page.getByTestId("claim-tier")).toHaveCount(0);
  await page.getByRole("button", { name: /부지급/ }).click();
  await expect(page.getByTestId("claim-state")).toHaveText("심사 완료", { timeout: 90000 });
  await expect(page.getByTestId("claim-decision")).toContainText("심사대 사용자");
});

test("the claims desk has its own address", async ({ page }) => {
  await page.goto("/#claims");
  await expect(page.getByRole("heading", { name: "청구 심사대", exact: true })).toBeVisible({ timeout: 60000 });
  await expect(page.getByTestId("claim-case").first()).toBeVisible({ timeout: 60000 });
  await page.getByRole("button", { name: "검토대", exact: true }).click();
  await expect(page).toHaveURL(/\/$|\/\?/);
  await page.getByRole("button", { name: "청구 심사", exact: true }).click();
  await expect(page).toHaveURL(/#claims$/);
});
