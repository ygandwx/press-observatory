import { test, expect, type Page, type TestInfo } from "@playwright/test";
import { readFile } from "node:fs/promises";

const button = (p: Page, name: string) =>
  p.getByRole("button", { name, exact: true });
const link = (p: Page, name: string | RegExp) =>
  p.getByRole("link", { name, exact: typeof name === "string" });
const heading = (p: Page, name: string | RegExp) =>
  p.getByRole("heading", { name, exact: typeof name === "string" });
async function login(p: Page, role: string) {
  await p.context().clearCookies();
  await p.goto("/");
  await p.getByLabel("账号", { exact: true }).fill("验收" + role);
  await p
    .getByLabel("密码", { exact: true })
    .fill(process.env.ACCEPTANCE_PASSWORD!);
  await button(p, "登录").click();
  await expect(heading(p, "领导看板")).toBeVisible();
}
async function tab(p: Page, name: string) {
  await button(p, name).click();
  await expect(button(p, name)).toHaveAttribute("aria-pressed", "true");
}
async function autoPolicy(p: Page) {
  await login(p, "配置");
  await p.goto("/monitor?tab=自动入库设置");
  await p.getByLabel("启用 AI 自动入库").check();
  await p.getByLabel("放行门槛").selectOption("85");
  await button(p, "保存设置").click();
  await expect(p.getByText("已保存", { exact: true })).toBeVisible();
}
async function download(p: Page, action: () => Promise<void>) {
  const [file] = await Promise.all([p.waitForEvent("download"), action()]);
  expect(await file.failure()).toBeNull();
  return readFile((await file.path())!);
}
async function evidence(page: Page, info: TestInfo, name: string) {
  const target = info.outputPath(name + ".png");
  await page.screenshot({ path: target });
  await info.attach(name, { path: target, contentType: "image/png" });
}
test.beforeEach(async ({ context }) => {
  // The browser can reach only this suite's server, including public share views.
  await context.route(/^https?:\/\//, (route) =>
    new URL(route.request().url()).origin === process.env.ACCEPTANCE_BASE
      ? route.continue()
      : route.abort(),
  );
});
test.afterEach(async ({ page }) => {
  expect((await page.pageErrors()).map((e) => e.message)).toEqual([]);
});

test("B01 · 六个模块、两种详情和移动菜单在三档视口可用", async ({
  page,
}, info) => {
  await login(page, "编辑");
  for (const width of [375, 768, 1440]) {
    await page.setViewportSize({ width, height: 900 });
    for (const [path, title] of [
      ["/", "领导看板"],
      ["/news", "动态中心"],
      ["/archives", "媒体档案"],
      ["/monitor", "监测中心"],
      ["/summary", "纸媒业态日报"],
      ["/briefings", "每周简报"],
      ["/summary/1", "纸媒业态日报"],
      ["/briefings/2", "全国纸媒业态每周简报"],
    ]) {
      await page.goto(path);
      await expect(heading(page, new RegExp(title)).first()).toBeVisible();
      if (path === "/") await expect(link(page, /今日入库动态/)).toBeVisible();
      else if (path === "/monitor")
        await expect(heading(page, "最近采集记录")).toBeVisible();
      else if (path === "/news")
        await expect(heading(page, /已入库动态/)).toBeVisible();
      else if (path === "/archives")
        await expect(heading(page, /机构名册/)).toBeVisible();
      else if (path === "/summary" || path === "/briefings")
        await expect(page.locator(".pager")).toBeVisible();
      else await expect(page.locator(".paper footer")).toBeVisible();
      expect(
        await page.evaluate(() => document.documentElement.scrollWidth),
        path + " @" + width,
      ).toBeLessThanOrEqual(width + 1);
      if (path === "/") await evidence(page, info, "dashboard-" + width);
    }
    if (width === 375) {
      await button(page, "打开菜单").click();
      await link(page, /动态中心/).click();
      await expect(heading(page, "动态中心")).toBeVisible();
    }
  }
});

test("B02 · 看板下钻、筛选返回、收藏、分页、完整 CSV 与模型失败重试", async ({
  page,
}) => {
  await login(page, "阅读");
  await expect(button(page, "一键采集入库")).toHaveCount(0);
  for (const span of ["30 天", "90 天", "7 天"]) {
    await button(page, span).click();
    await expect(button(page, span)).toHaveAttribute("aria-pressed", "true");
  }
  await expect(page.locator(".graph canvas")).toHaveCount(4);
  await page.locator(".categories a").filter({ hasText: "改革类" }).click();
  await expect(page.getByLabel("事件类别")).toHaveValue("改革类");
  await expect(heading(page, "已入库动态 · 26")).toBeVisible();
  await page.goto("/");
  await link(page, /今日入库动态/).click();
  await expect(page.getByText(/正在查看生成时保存的列表/)).toBeVisible();
  await link(page, "查看实时列表").click();
  await page.getByLabel("标题或媒体").fill("合成分页事件");
  await button(page, "筛选").click();
  await expect(heading(page, "已入库动态 · 26")).toBeVisible();
  await button(page, "下一页").click();
  await expect(page.locator(".pager")).toContainText("第 2 / 2 页");
  await page.goBack();
  await expect(page.locator(".pager")).toContainText("第 1 / 2 页");
  const csv = await download(page, () => link(page, "导出 CSV").click());
  expect(csv.toString("utf8").trim().split(/\r?\n/)).toHaveLength(27);
  await link(page, "合成分页事件 25").click();
  await button(page, "☆ 收藏").click();
  await expect(button(page, "取消收藏")).toBeVisible();
  await page.reload();
  await expect(button(page, "取消收藏")).toBeVisible();
  await page.goto("/news");
  await page.getByLabel("仅看收藏").check();
  await button(page, "筛选").click();
  await expect(heading(page, "已入库动态 · 1")).toBeVisible();
  await link(page, "重置").click();
  await page.getByLabel("语义检索问题").fill("合成失败重试");
  await button(page, "AI 搜索").click();
  await expect(page.getByText(/检索未完成/)).toBeVisible();
  await button(page, "AI 搜索").click();
  await expect(page.getByText(/已检索全部/)).toBeVisible();
  await expect(link(page, "合成分页事件 00")).toBeVisible();
  await page.reload();
  await expect(heading(page, "已入库动态 · 1")).toBeVisible();
  await page.goto("/monitor");
  await expect(page.getByText(/此页面不可访问/)).toBeVisible();
  await button(page, "退出").click();
  await expect(heading(page, "登录工作台")).toBeVisible();
});

test("B03 · 名册预检后的对比、关注、五件套补全与取消", async ({ page }) => {
  await login(page, "阅读");
  await page.goto("/archives");
  for (const name of ["合成甲报", "合成乙报"])
    await page.getByLabel("对比 " + name).check();
  await button(page, "同业对比（2）").click();
  await expect(page.locator(".comparison h3")).toHaveCount(2);
  await button(page, "关闭对比（2）").click();
  await link(page, /合成甲报/).click();
  const url = page.url();
  await button(page, "关注机构").click();
  await expect(button(page, "取消关注")).toBeVisible();
  await page.reload();
  await expect(button(page, "取消关注")).toBeVisible();
  await page.goto(url + "?edit=1");
  await expect(heading(page, "机构资料")).toBeVisible();
  await expect(button(page, "保存档案")).toHaveCount(0);
  await page.goto("/archives");
  await page.getByLabel("仅看关注").check();
  await button(page, "筛选").click();
  await expect(heading(page, "机构名册 · 1")).toBeVisible();
  await login(page, "编辑");
  await page.goto(url);
  await button(page, "编辑与补全").click();
  await page
    .getByLabel("官网", { exact: true })
    .fill("https://acceptance.invalid/cancel");
  await button(page, "取消").click();
  await expect(page.getByText("https://acceptance.invalid/cancel")).toHaveCount(
    0,
  );
  await button(page, "编辑与补全").click();
  for (const [i, k] of [
    "官网",
    "客户端",
    "官方公众号",
    "视频号",
    "海外账号",
  ].entries())
    await page
      .getByLabel(k, { exact: true })
      .fill("https://acceptance.invalid/channel/" + i);
  await button(page, "保存档案").click();
  await expect(
    page.getByText("https://acceptance.invalid/channel/4"),
  ).toBeVisible();
  await page.reload();
  await expect(
    page.getByText("https://acceptance.invalid/channel/0"),
  ).toBeVisible();
  await page.goto("/monitor?tab=覆盖度面板");
  await expect(page.getByText(/已补全 5 \/ 10/)).toBeVisible();
});

test("B04 · 信源与关键词启停、取消编辑、阈值和只读配置", async ({ page }) => {
  await login(page, "配置");
  await page.goto("/monitor?tab=信源配置");
  await page.getByLabel("信源名称").fill("合成暂停源");
  await page.getByLabel("来源网址").fill("https://acceptance.invalid/disabled");
  await page.getByLabel("启用", { exact: true }).uncheck();
  await button(page, "添加").click();
  await expect(
    page.locator(".row").filter({ hasText: "合成暂停源" }),
  ).toContainText("已暂停");
  const sourceRow = page.locator(".row").filter({ hasText: "合成暂停源" });
  await sourceRow.getByRole("button", { name: "编辑" }).click();
  await page.getByLabel("启用", { exact: true }).check();
  await button(page, "保存修改").click();
  await expect(sourceRow).not.toContainText("已暂停");
  await sourceRow.getByRole("button", { name: "编辑" }).click();
  await page.getByLabel("启用", { exact: true }).uncheck();
  await button(page, "保存修改").click();
  await expect(sourceRow).toContainText("已暂停");
  await tab(page, "关键词管理");
  await page.getByLabel("关键词", { exact: true }).fill("合成关键词");
  await button(page, "添加").click();
  const row = page.locator(".row").filter({ hasText: "合成关键词" });
  await row.getByRole("button", { name: "编辑" }).click();
  await page.getByLabel("启用", { exact: true }).uncheck();
  await button(page, "取消").click();
  await expect(row).toContainText("启用");
  await row.getByRole("button", { name: "编辑" }).click();
  await page.getByLabel("启用", { exact: true }).uncheck();
  await button(page, "保存修改").click();
  await expect(row).toContainText("暂停");
  await page.reload();
  await expect(row).toContainText("暂停");
  await tab(page, "自动入库设置");
  await page.getByLabel("放行门槛").selectOption("70");
  await page.getByLabel("低于此分过滤").fill("71");
  await button(page, "保存设置").click();
  await expect(page.getByText(/数值须在/)).toBeVisible();
  await page.getByLabel("低于此分过滤").fill("40");
  await button(page, "保存设置").click();
  await expect(page.getByText("已保存", { exact: true })).toBeVisible();
  await page.reload();
  await expect(page.getByLabel("放行门槛")).toHaveValue("70");
  await login(page, "审核");
  await page.goto("/monitor?tab=自动入库设置");
  await expect(button(page, "保存设置")).toBeDisabled();
});

test("B05 · 真实 worker 消费、去重、巡检重试与记录结论", async ({
  page,
}, info) => {
  await autoPolicy(page);
  await login(page, "编辑");
  await button(page, "生成研判").click();
  await expect(page.getByText(/隔离研判/)).toBeVisible();
  await button(page, "一键采集入库").click();
  const dialog = page.getByRole("dialog");
  await expect(dialog.getByText(/采集已结束/)).toBeVisible({ timeout: 20000 });
  await expect(dialog.getByText(/需授权数据源或人工巡检/)).toBeVisible();
  await evidence(page, info, "collection");
  await link(page, /查看本次新入库动态 · 1 件/).click();
  await expect(link(page, "合成日报上线移动采编工具")).toBeVisible();
  await page.goto("/monitor");
  await page.getByLabel("选择采集信源").selectOption({ label: "合成官网" });
  await button(page, "一键采集入库").click();
  await expect(dialog.getByText("本次暂无新入库动态")).toBeVisible({
    timeout: 20000,
  });
  await button(page, "关闭").click();
  const manual = page.locator(".row").filter({ hasText: "合成人工渠道" });
  await manual.getByRole("button", { name: "重试" }).click();
  await expect(
    page.locator(".row").filter({ hasText: "人工重试" }),
  ).toContainText("人工巡检", { timeout: 22000 });
  await tab(page, "人工巡检");
  await page.getByLabel("巡检结论").fill("隔离样本已核对");
  await button(page, "记录结论").first().click();
  await expect(page.getByText("隔离样本已核对")).toBeVisible();
});

test("B06 · AI 正文与链接录入、失败重试、核验后审核及队列批量操作", async ({
  page,
}) => {
  await login(page, "编辑");
  await page.goto("/monitor?tab=动态录入");
  await page.getByLabel("原文片段").fill("合成提取重试样本");
  await button(page, "AI 提取").click();
  await expect(page.getByText(/模型调用失败/)).toBeVisible();
  await button(page, "AI 提取").click();
  await expect(page.getByLabel("标题", { exact: true })).toHaveValue(
    "合成晚报改版手记",
  );
  await page.getByLabel("原文片段").fill("");
  await page
    .getByLabel("真实原文链接")
    .fill("https://acceptance.invalid/entry");
  await button(page, "AI 提取").click();
  await expect(page.getByText("已提取，请核对后提交")).toBeVisible();
  await button(page, "保存至待审核队列").click();
  await expect(heading(page, "合成晚报改版手记")).toBeVisible();
  const url = page.url();
  await expect(button(page, "批准入库")).toHaveCount(0);
  await login(page, "审核");
  await page.goto(url);
  await button(page, "批准入库").click();
  await expect(
    page.getByText("请先核验至少一个可访问的真实原文。"),
  ).toBeVisible();
  await expect(button(page, "批准入库")).toBeVisible();
  await button(page, "核验链接").click();
  await expect(page.locator(".tag", { hasText: "已核验" })).toBeVisible();
  await button(page, "批准入库").click();
  await expect(button(page, "归并至该事件")).toBeVisible();
  await page.goto("/monitor?tab=待审核队列");
  await page.getByLabel("全选本页").check();
  await expect(button(page, "批量退回")).toBeDisabled();
  await page.getByLabel("退回原因").fill("隔离批量复核");
  await button(page, "批量退回").click();
  await expect(heading(page, "待审核动态 · 0")).toBeVisible();
});

test("B07 · 日报取消、保存、签发、导出、匿名分享撤销与历史更正", async ({
  page,
  browser,
}) => {
  await login(page, "编辑");
  await page.goto("/summary");
  await button(page, "生成或打开当期").click();
  await expect(page).toHaveURL(/\/summary\/\d+$/);
  const path = new URL(page.url()).pathname;
  await button(page, "重新生成").click();
  await expect(page.getByText("已保存", { exact: true })).toBeVisible();
  await button(page, "编辑").click();
  await page.getByLabel("一句话结论").fill("合成取消内容");
  await button(page, "取消").click();
  await expect(page.locator(".paper blockquote")).not.toHaveText(
    "合成取消内容",
  );
  await button(page, "编辑").click();
  await page.getByLabel("一句话结论").fill("合成签发结论");
  await button(page, "保存").click();
  await expect(page.locator(".paper blockquote")).toHaveText("合成签发结论");
  await page.reload();
  await expect(page.locator(".paper blockquote")).toHaveText("合成签发结论");
  await expect(button(page, "签发")).toHaveCount(0);
  await button(page, "提交签发").click();
  await expect(button(page, "编辑")).toHaveCount(0);
  await login(page, "签发");
  await page.goto(path);
  await button(page, "签发").click();
  const share = button(page, "生成只读链接");
  await expect(share).toBeVisible();
  const pendingShare = Promise.withResolvers<void>();
  await page.route("**/api/v1/reports/*", async (route) => {
    const req = route.request();
    if (req.method() === "POST" && req.postDataJSON()?.action === "share")
      await pendingShare.promise;
    await route.continue();
  });
  try {
    await share.click();
    await expect(button(page, "复制全文")).toBeDisabled();
  } finally {
    pendingShare.resolve();
  }
  await expect(link(page, /打开只读链接/)).toBeVisible();
  await page.context().grantPermissions(["clipboard-read", "clipboard-write"], {
    origin: process.env.ACCEPTANCE_BASE,
  });
  await button(page, "复制全文").click();
  await expect(page.getByText("已复制全文", { exact: true })).toBeVisible();
  expect(await page.evaluate(() => navigator.clipboard.readText())).toContain(
    "https://acceptance.invalid/article/",
  );
  const publicURL = await link(page, /打开只读链接/).getAttribute("href");
  expect(publicURL).toBeTruthy();
  const anon = await browser.newContext({
    baseURL: process.env.ACCEPTANCE_BASE,
  });
  try {
    const publicPage = await anon.newPage();
    await publicPage.goto(publicURL!);
    await expect(publicPage.locator("blockquote")).toHaveText("合成签发结论");
    await expect(button(publicPage, "编辑")).toHaveCount(0);
    const pdf = await download(page, () => button(page, "导出 PDF").click());
    expect(pdf.subarray(0, 5).toString()).toBe("%PDF-");
    const png = await download(page, () => button(page, "导出长图").click());
    expect(png.subarray(0, 8).toString("hex")).toBe("89504e470d0a1a0a");
    await button(page, "撤销分享").click();
    await expect(link(page, /打开只读链接/)).toHaveCount(0);
    await publicPage.reload();
    await expect(publicPage.getByText(/记录不存在/)).toBeVisible();
  } finally {
    await anon.close();
  }
  await button(page, "创建更正稿").click();
  await expect(page.locator(".paperhead")).toContainText("草稿");
  await login(page, "编辑");
  await page.goto(path);
  await button(page, "编辑").click();
  await expect(button(page, "重新生成")).toHaveCount(0);
  await page.getByLabel("一句话结论").fill("合成未签更正");
  await button(page, "保存").click();
  await expect(page.locator("blockquote")).toHaveText("合成未签更正");
  await page.getByLabel("签发版本").selectOption({ index: 1 });
  await expect(page.locator("blockquote")).toHaveText("合成签发结论");
  await expect(button(page, "编辑")).toHaveCount(0);
  await login(page, "阅读");
  await page.goto(path);
  await expect(page.locator("blockquote")).toHaveText("合成签发结论");
});

test("B08 · 周报已结束周、历史筛选、双版和签发后的阅读", async ({ page }) => {
  await login(page, "编辑");
  await page.goto("/briefings");
  await button(page, "生成该周简报").click();
  await expect(page.getByText(/请选择已结束的自然周/)).toBeVisible();
  await page
    .getByRole("combobox", { name: "状态", exact: true })
    .selectOption("published");
  await expect(page.getByText(/暂无周报/)).toBeVisible();
  await page
    .getByRole("combobox", { name: "状态", exact: true })
    .selectOption("draft");
  await page.getByLabel("月份").fill("2000-01");
  await expect(page.getByText(/暂无周报/)).toBeVisible();
  await page.getByLabel("月份").fill("");
  await page.getByLabel("搜索结论").fill("本期入库");
  await link(page, /全国纸媒业态每周简报 · 第/).click();
  const path = new URL(page.url()).pathname;
  await expect(heading(page, "入库变化")).toBeVisible();
  await expect(page.getByText(/本周 1 件，上周 0 件/)).toBeVisible();
  await button(page, "业务详版").click();
  await expect(heading(page, "事件明细与原文")).toBeVisible();
  await expect(page.locator(".reference")).toContainText("合成周报样本");
  await login(page, "签发");
  await page.goto(path);
  await button(page, "签发").click();
  await expect(button(page, "归档")).toBeVisible();
  await login(page, "阅读");
  await page.goto("/briefings");
  await expect(heading(page, "最新发布")).toBeVisible();
  await login(page, "签发");
  await page.goto(path);
  await button(page, "归档").click();
  await expect(page.locator(".paperhead")).toContainText("验收签发");
  await page.goto("/briefings");
  await page
    .getByRole("combobox", { name: "状态", exact: true })
    .selectOption("archived");
  await expect(link(page, /全国纸媒业态每周简报 · 第/)).toBeVisible();
});
