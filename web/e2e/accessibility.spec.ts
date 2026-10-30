import AxeBuilder from "@axe-core/playwright";
import { test, expect, type Page } from "@playwright/test";

// Automatic checks for what a machine can judge: names, roles, contrast, targets. They do not replace a person using a
// screen reader; they keep the failures a machine finds from coming back. Serious and critical findings fail the test.
async function scan(page: Page) {
  const result = await new AxeBuilder({ page })
    .withTags(["wcag2a", "wcag2aa", "wcag21aa", "wcag22aa"])
    .exclude("canvas") // the CAD view is a drawing; its text alternative is checked on the page that holds it
    // Boxes drawn over a page picture sit as close as the printed lines do, so some are smaller than 24 px or touch their
    // neighbours. Each field in the table beside the picture is a full-size target that does the same thing (WCAG 2.5.8, equivalent).
    .exclude(".scan-box")
    .exclude(".evidence-box")
    .analyze();
  const bad = result.violations.filter((v) => v.impact === "serious" || v.impact === "critical");
  return bad.map((v) => `${v.id} (${v.impact}): ${v.nodes.length} nodes, e.g. ${v.nodes[0]?.target.join(" ")}`);
}

async function ready(page: Page) {
  await page.goto("/");
  await expect(page.getByRole("button", { name: "조건 검증 시작", exact: true })).toBeEnabled({ timeout: 60000 });
}

test("the review desk has no serious accessibility violations", async ({ page }) => {
  await ready(page);
  expect(await scan(page)).toEqual([]);
});

test("the laboratory has no serious accessibility violations", async ({ page }) => {
  await ready(page);
  await page.getByRole("button", { name: "실험실", exact: true }).click();
  await expect(page.getByRole("navigation", { name: "실험실 빠른 이동" })).toBeVisible();
  expect(await scan(page)).toEqual([]);
});

test("the claims desk has no serious accessibility violations", async ({ page }) => {
  await ready(page);
  await page.getByRole("button", { name: "청구 심사", exact: true }).click();
  await expect(page.getByRole("heading", { name: "청구 심사대", exact: true })).toBeVisible();
  await expect(page.getByTestId("claim-case").first()).toBeVisible();
  expect(await scan(page)).toEqual([]);
});
