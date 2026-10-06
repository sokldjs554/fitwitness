import { test, expect, type Page } from "@playwright/test";
import { projectMesh } from "../src/components/meshProjection";

test("mesh projection hides back faces so rear triangles cannot cover the front surface", () => {
  const triangles = projectMesh({
    vertices: [[0, 0, 0], [1, 0, 0], [0, 1, 0]],
    faces: [[0, 1, 2], [0, 2, 1]],
  });
  expect(triangles.filter((triangle) => triangle.frontFacing)).toHaveLength(1);
});

async function disableWebGL(page: Page) {
  await page.addInitScript(() => {
    const original = HTMLCanvasElement.prototype.getContext;
    HTMLCanvasElement.prototype.getContext = function (type: string, ...args: unknown[]) {
      if (type.includes("webgl")) return null;
      return original.apply(this, [type, ...args] as Parameters<typeof original>);
    } as typeof original;
  });
}

async function openModel(page: Page) {
  await page.goto("/");
  await expect(page.getByTestId("candidate-card")).toHaveCount(20);
  await page.getByRole("button", { name: "3D 형상", exact: true }).click();
}

test("all five drawings show their actual mesh projection when WebGL is unavailable", async ({ page }, testInfo) => {
  await disableWebGL(page);
  await openModel(page);
  const cards = page.getByTestId("candidate-card");
  for (let index = 0; index < 5; index++) {
    const card = cards.nth(index);
    const source = await card.locator("img").getAttribute("src");
    const meshURL = source!.replace(/\/png$/, "/mesh");
    const meshResponse = await page.request.get(meshURL);
    expect(meshResponse.ok()).toBe(true);
    const mesh = await meshResponse.json();
    await card.click();
    await page.getByRole("button", { name: "3D 형상", exact: true }).click();
    const projection = page.getByRole("img", { name: "실제 메시의 2D 투영" });
    await expect(projection).toHaveAttribute("data-document-id", meshURL.split("/")[3]);
    await expect(projection.locator("polygon")).toHaveCount(mesh.faces.length);
    const points = await projection.locator("polygon").first().getAttribute("points");
    expect(points).not.toMatch(/NaN|Infinity/);
    expect(points?.split(" ")).toHaveLength(3);
    await expect(page.locator(".model canvas")).toHaveCount(0);
    await expect(page.locator(".model [role=alert]")).toHaveCount(0);
    await expect(page.locator(".model")).toContainText("WebGL");
    await expect(page.locator(".model")).not.toContainText("드래그하여 회전");
  }
  await page.locator(".model").screenshot({ path: testInfo.outputPath("mesh-projection.png") });
});

test("failed mesh request can be retried without leaving a stale error", async ({ page }) => {
  await disableWebGL(page);
  let fail = true;
  await page.route("**/assets/mesh", async (route) => {
    if (fail) await route.fulfill({ status: 503, body: "temporarily unavailable" });
    else await route.continue();
  });
  await openModel(page);
  await expect(page.locator(".model [role=alert]")).toBeVisible();
  await expect(page.locator(".model canvas, .model svg")).toHaveCount(0);
  fail = false;
  await page.getByRole("button", { name: "형상 다시 불러오기" }).click();
  await expect(page.getByRole("img", { name: "실제 메시의 2D 투영" })).toBeVisible();
  await expect(page.locator(".model [role=alert]")).toHaveCount(0);
});

test("switching drawings discards an older pending mesh failure", async ({ page }) => {
  await disableWebGL(page);
  let release!: () => void;
  const pending = new Promise<void>((resolve) => { release = resolve; });
  let first = true;
  let started!: () => void;
  const requested = new Promise<void>((resolve) => { started = resolve; });
  let firstURL = "";
  const failedRequests: string[] = [];
  page.on("requestfailed", (request) => failedRequests.push(request.url()));
  await page.route("**/assets/mesh", async (route) => {
    if (!first) return route.continue();
    first = false;
    firstURL = route.request().url();
    started();
    await pending;
    await route.fulfill({ status: 503, body: "old request failed" }).catch(() => {});
  });
  await openModel(page);
  await requested;
  await expect(page.locator(".model [role=status]")).toContainText("불러오는 중");
  const card = page.getByTestId("candidate-card").nth(1);
  const source = await card.locator("img").getAttribute("src");
  await card.click();
  await page.getByRole("button", { name: "3D 형상", exact: true }).click();
  const projection = page.getByRole("img", { name: "실제 메시의 2D 투영" });
  await expect(projection).toHaveAttribute("data-document-id", source!.split("/")[3]);
  await expect.poll(() => failedRequests).toContain(firstURL);
  release();
  await expect(projection).toBeVisible();
  await expect(page.locator(".model [role=alert]")).toHaveCount(0);
});
