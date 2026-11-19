import { expect, test, type Page } from "@playwright/test";

async function checkWidths(page: Page) {
  for (const width of [375, 768, 1440]) {
    await page.setViewportSize({ width, height: 900 });
    expect(
      await page.evaluate(() => document.documentElement.scrollWidth),
    ).toBeLessThanOrEqual(width + 1);
  }
}

async function signIn(page: Page, fixtureRole: string) {
  await page.context().clearCookies();
  await page.goto("/");
  await page.getByLabel("账号", { exact: true }).fill("验收" + fixtureRole);
  await page
    .getByLabel("密码", { exact: true })
    .fill(process.env.ACCEPTANCE_PASSWORD!);
  await page.getByRole("button", { name: "登录", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "领导看板", exact: true }),
  ).toBeVisible();
}

test("G01 · 专题与常规事件共用日期、机构、分类和原文，日报首次生成三秒内可阅读", async ({
  page,
}, info) => {
  await signIn(page, "编辑");
  await page.goto("/geo");
  await expect(page.getByRole("heading", { name: "GEO 专题" })).toBeVisible();
  const event = page.getByRole("link", { name: "合成周报样本", exact: true });
  await expect(event).toBeVisible();
  const target = await event.getAttribute("href");
  const row = event.locator("xpath=ancestor::article");
  const occurrence = (await row.locator("small").innerText()).slice(0, 10);
  await expect(row).toContainText("AI可见度");
  await checkWidths(page);
  await page.getByLabel("媒体机构").selectOption({ label: "合成甲报" });
  await page.getByLabel("事件类别").selectOption("技术类");
  await page.getByLabel("搜索专题").fill("生成式引擎优化");
  await expect(
    page.getByRole("heading", { name: "专题时间线 · 1 件" }),
  ).toBeVisible();
  await page.getByLabel("事件类别").selectOption("政策类");
  await expect(page.getByText("当前条件下无已核实的 GEO 事件")).toBeVisible();
  await page.getByLabel("事件类别").selectOption("技术类");
  await event.click();
  await expect(page).toHaveURL(new RegExp(target + "$"));
  await expect(
    page.getByRole("heading", { name: "合成周报样本", exact: true }),
  ).toBeVisible();
  await page.goto("/summary");
  await page.getByLabel("业务日期").fill(occurrence);
  const prior = await page.request.get(
    "/api/v1/reports?kind=daily&day=" + occurrence,
  );
  expect((await prior.json()).count).toBe(0);
  const start = Date.now();
  await page.getByRole("button", { name: "生成或打开当期" }).click();
  await expect(page.locator(".paper footer")).toBeVisible();
  await expect(page.locator(".paper .reference")).toContainText("合成周报样本");
  const milliseconds = Date.now() - start;
  await info.attach("daily-first-render-ms", {
    body: String(milliseconds),
    contentType: "text/plain",
  });
  expect(milliseconds).toBeLessThanOrEqual(3000);
});

test("G02 · 周报两种只读链接与导出独立，浏览器回读已签本周 GEO", async ({
  page,
  browser,
}) => {
  await signIn(page, "签发");
  await page.goto("/briefings");
  await page
    .getByRole("link", { name: "业务详版", exact: true })
    .first()
    .click();
  await expect(page).toHaveURL(/view=detail/);
  await expect(
    page.getByRole("heading", { name: "本周 GEO 进展" }),
  ).toBeVisible();
  if (await page.getByRole("button", { name: "签发", exact: true }).count())
    await page.getByRole("button", { name: "签发", exact: true }).click();
  const create = page.getByRole("button", {
    name: "生成只读链接",
    exact: true,
  });
  if (await create.count()) await create.click();
  const leader = await page
    .getByRole("link", { name: /领导参阅版只读链接/ })
    .getAttribute("href");
  const detail = await page
    .getByRole("link", { name: /业务详版只读链接/ })
    .getAttribute("href");
  expect(leader).not.toBe(detail);
  const anonymous = await browser.newContext({
    baseURL: process.env.ACCEPTANCE_BASE,
  });
  try {
    const tab = await anonymous.newPage();
    for (const [href, label] of [
      [leader!, "领导参阅版"],
      [detail!, "业务详版"],
    ]) {
      await tab.goto(href);
      await expect(tab.locator(".paperhead")).toContainText(label);
      await expect(
        tab.getByRole("heading", { name: "本周 GEO 进展" }),
      ).toBeVisible();
      await expect(
        tab.getByRole("button", { name: "签发", exact: true }),
      ).toHaveCount(0);
      await expect(
        tab.getByRole("button", { name: "业务详版", exact: true }),
      ).toHaveCount(0);
      const download = tab.waitForEvent("download");
      await checkWidths(tab);
      await tab.getByRole("button", { name: "导出 PDF", exact: true }).click();
      expect((await download).suggestedFilename()).toContain(label);
    }
    await page
      .getByLabel("签发版本", { exact: true })
      .selectOption({ index: 1 });
    await page.getByRole("button", { name: "撤销本周报全部分享" }).click();
    await expect(
      page.getByRole("button", { name: "启用此签发版本的只读分享" }),
    ).toBeVisible();
    for (const href of [leader!, detail!]) {
      const response = await anonymous.request.get(
        href.replace("/share/", "/api/v1/shared/"),
      );
      expect(response.status()).toBe(404);
    }
  } finally {
    await anonymous.close();
  }
});
