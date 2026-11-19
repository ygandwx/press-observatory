import { test, expect, type Page } from "@playwright/test";
import { readFile } from "node:fs/promises";

async function authenticate(page: Page, role: string) {
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

test("看板四卡、三种分布的快照、Excel与窄屏档案", async ({ page }) => {
  await authenticate(page, "阅读");
  await expect(page.locator(".metrics > *")).toHaveCount(4);
  await expect(
    page.getByRole("link", { name: /AI 自动入库占比/ }),
  ).toBeVisible();
  for (const title of ["事件类型分布", "品类分布", "地区分布"]) {
    const chart = page
      .locator(".charts section")
      .filter({ has: page.getByRole("heading", { name: title, exact: true }) });
    await chart.locator("summary").click();
    const chosen = chart.locator(".graph-drills a").first();
    const label = await chosen.innerText();
    const n = Number(label.match(/· (\d+) 件/)![1]);
    await chosen.click();
    await expect(page).toHaveURL(/snapshot=/);
    await expect(
      page.getByRole("heading", { name: "已入库动态 · " + n, exact: true }),
    ).toBeVisible();
    await page.goto("/");
  }
  await page.goto("/news");
  await page.getByLabel("趋势标签").fill("这个趋势标签没有任何事件");
  await page.getByRole("button", { name: "筛选", exact: true }).click();
  await expect(page.getByText("没有符合条件的动态")).toBeVisible();
  const waitDownload = page.waitForEvent("download");
  await page.getByRole("link", { name: "导出 Excel" }).click();
  const file = await waitDownload;
  const data = await readFile((await file.path())!, "utf8");
  expect(data).toContain("urn:schemas-microsoft-com:office:spreadsheet");
  expect(data.match(/<Row>/g)?.length).toBe(1);
  await page.setViewportSize({ width: 768, height: 900 });
  await page.goto("/archives");
  await expect(page.locator(".directory-table tbody tr").first()).toHaveCSS(
    "display",
    "block",
  );
  await expect(page.getByRole("button", { name: "打开菜单" })).toBeVisible();
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
});

test("三家同类机构按共同期末对照具名指标和出处", async ({ page }) => {
  await authenticate(page, "编辑");
  await page.goto("/archives");
  await page.evaluate(async () => {
    const session = await fetch("/api/v1/session").then((r) => r.json());
    const options = {
      method: "POST",
      headers: {
        "X-CSRFToken": session.csrf,
        "Content-Type": "application/json",
      },
    };
    for (const name of ["指标验收一报", "指标验收二报", "指标验收三报"]) {
      const created = await fetch("/api/v1/media", {
        ...options,
        body: JSON.stringify({
          name,
          category: "地方党媒体系",
          subcategory: "省级党委机关报",
        }),
      });
      if (!created.ok) throw Error(await created.text());
      const media = await created.json();
      if (name !== "指标验收三报") {
        const result = await fetch("/api/v1/media/" + media.id, {
          ...options,
          body: JSON.stringify({
            action: "metric",
            kind: "营收",
            value: 12.5,
            unit: "万元",
            day: "2020-12-31",
            source_url: "https://acceptance.invalid/entry",
          }),
        });
        if (!result.ok) throw Error(await result.text());
      }
    }
  });
  await page.reload();
  for (const n of ["一", "二", "三"])
    await page.getByLabel("对比 指标验收" + n + "报").check();
  await page.getByRole("button", { name: "同业对比（3）" }).click();
  await page.getByLabel("统一统计期末").fill("2020-12-31");
  await expect(page.locator(".comparison h3")).toHaveCount(3);
  await expect(
    page.locator(".comparison").getByRole("link", { name: "原文 ↗" }),
  ).toHaveCount(2);
  await expect(
    page.locator(".comparison").getByText("暂无已核实数据", { exact: true }),
  ).toHaveCount(7);
});
