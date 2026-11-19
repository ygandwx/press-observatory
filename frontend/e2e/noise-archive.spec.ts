import { expect, test, type Page } from "@playwright/test";
import { readFile } from "node:fs/promises";

const path = "/monitor?tab=" + encodeURIComponent("周度噪音报告");
const endpoint = "/api/v1/monitor/noise-weeks";

async function login(page: Page, role: string) {
  await page.goto("/");
  await page.getByLabel("账号", { exact: true }).fill("验收" + role);
  await page
    .getByLabel("密码", { exact: true })
    .fill(process.env.ACCEPTANCE_PASSWORD!);
  await page.getByRole("button", { name: "登录", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "领导看板", exact: true }),
  ).toBeVisible();
}
async function csv(page: Page, name: string) {
  const result = page.waitForEvent("download");
  await page.getByRole("link", { name, exact: true }).click();
  const dl = await result;
  expect(await dl.failure()).toBeNull();
  return {
    name: dl.suggestedFilename(),
    data: await readFile((await dl.path())!, "utf8"),
  };
}

test("N01 · 运营按归档周和版本核查负样本、撤回记录及两份CSV", async ({
  page,
}, info) => {
  await login(page, "编辑");
  const list = await page.request.get(endpoint);
  expect(list.status()).toBe(200);
  const { items } = await list.json();
  const revised = items.find((row: { version: number }) => row.version > 1);
  expect(revised).toBeTruthy();

  await page.goto(path);
  await page.getByLabel("归档周", { exact: true }).selectOption(revised.week);
  await expect(page.getByLabel("归档版本")).toHaveValue(
    String(revised.version),
  );
  await expect(
    page.getByRole("heading", { name: "高噪音信源排行" }),
  ).toBeVisible();
  await expect(
    page.getByRole("heading", { name: "噪音原因排行" }),
  ).toBeVisible();
  await expect(
    page
      .locator("li")
      .filter({ hasText: "合成官网 · 噪音 10 / 判定 12 篇 · 83.3%" }),
  ).toBeVisible();
  await expect(
    page.locator("li").filter({ hasText: "招聘 · 9 篇 · 90%" }),
  ).toBeVisible();
  await page.getByLabel("筛选信源").selectOption({ label: "合成官网" });
  const search = page.getByLabel("查找标题、原文或原因");
  await search.fill("合成归档采购样本");
  await expect(page.getByText("匹配 1 篇", { exact: true })).toBeVisible();
  await page.getByText("查看判定记录", { exact: true }).click();
  await expect(page.getByText(/初始判定.*隔离归档采集员/)).toBeVisible();
  await expect(
    page.getByRole("link", { name: /合成归档采购样本/ }),
  ).toHaveAttribute("href", /quality\/procurement$/);
  await search.fill("");
  await page.getByLabel("样本分类").selectOption("returned");
  await expect(page.getByText("匹配 1 篇", { exact: true })).toBeVisible();
  await expect(
    page.getByRole("link", { name: /合成归档待核样本/ }).first(),
  ).toBeVisible();
  await page.getByLabel("样本分类").selectOption("");
  await search.fill("合成归档纠正样本");
  await expect(page.getByText("没有符合条件的负样本")).toBeVisible();
  await expect(page.getByText(/撤回负样本.*隔离归档审核员/)).toBeVisible();
  const current = await csv(page, "导出负样本 CSV");
  expect(current.name).toBe(
    "noise-samples-" + revised.week + "-v" + revised.version + ".csv",
  );
  expect(current.data).toContain("合成归档采购样本");
  expect(current.data).not.toContain("合成归档纠正样本");
  expect(current.data).toContain("普通退回待校准");
  const report = await csv(page, "导出周报 CSV");
  expect(report.name).toBe(
    "noise-report-" + revised.week + "-v" + revised.version + ".csv",
  );
  expect(report.data).toContain("人工复核");
  expect(report.data).toContain("招标采购");

  await page.getByLabel("归档版本").selectOption("1");
  await expect(page).toHaveURL(/version=1/);
  await expect(
    page.getByRole("link", { name: /合成归档纠正样本/ }),
  ).toBeVisible();
  await page.getByRole("button", { name: "周度噪音报告", exact: true }).click();
  await expect(page).toHaveURL(/version=1/);
  await page.reload();
  await expect(page.getByLabel("归档版本")).toHaveValue("1");
  const first = await csv(page, "导出负样本 CSV");
  expect(first.name.endsWith("-v1.csv")).toBeTruthy();
  expect(first.data).toContain("合成归档纠正样本");
  const other = items.find(
    (r: { week: string; total: number }) =>
      r.week !== revised.week && r.total === 0,
  );
  expect(other).toBeTruthy();
  await page.getByLabel("归档周").selectOption(other.week);
  await expect(page.getByText("本周未记录信源判定")).toBeVisible();

  for (const width of [375, 768, 1440]) {
    await page.setViewportSize({ width, height: 900 });
    expect(
      await page.evaluate(() => document.documentElement.scrollWidth),
    ).toBeLessThanOrEqual(width + 1);
  }
  await page.goto(path + "&week=" + revised.week + "&version=999999");
  await expect(page.getByRole("button", { name: "重试" })).toBeVisible();
  await page.getByRole("button", { name: "查看最新版本" }).click();
  await expect(page.getByLabel("归档版本")).toHaveValue(
    String(revised.version),
  );
  await page.setViewportSize({ width: 375, height: 900 });
  expect(
    await page.evaluate(() => document.documentElement.scrollWidth),
  ).toBeLessThanOrEqual(376);
  await info.attach("周度噪音报告手机视图", {
    body: await page.screenshot(),
    contentType: "image/png",
  });
  expect(await page.pageErrors()).toEqual([]);
});

test("N02 · 社领导不能查看运营噪音归档或导出", async ({ page }) => {
  await login(page, "阅读");
  await page.goto(path);
  await expect(
    page.getByRole("heading", { name: "周度噪音报告", exact: true }),
  ).toHaveCount(0);
  await expect(page.getByRole("link", { name: /导出负样本/ })).toHaveCount(0);
  for (const suffix of [
    "",
    "/2024-01-01",
    "/2024-01-01?version=1&export=samples",
    "/2024-01-01?version=1&export=report",
  ]) {
    expect((await page.request.get(endpoint + suffix)).status()).toBe(403);
  }
});
