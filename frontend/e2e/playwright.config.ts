import { defineConfig } from "@playwright/test";
import path from "node:path";

const baseURL = process.env.ACCEPTANCE_BASE;
if (
  !baseURL ||
  !/^http:\/\/127\.0\.0\.1:\d+$/.test(baseURL) ||
  !process.env.ACCEPTANCE_PASSWORD ||
  !process.env.ACCEPTANCE_OUTPUT
)
  throw Error("Start the isolated suite with python scripts/acceptance/run.py");
const output = process.env.ACCEPTANCE_OUTPUT;
export default defineConfig({
  testDir: ".",
  testMatch: "*.spec.ts",
  workers: 1,
  retries: 0,
  timeout: 65000,
  expect: { timeout: 8000 },
  outputDir: path.join(output, "browser"),
  reporter: [
    ["list"],
    ["json", { outputFile: path.join(output, "results.json") }],
  ],
  use: {
    baseURL,
    browserName: "chromium",
    actionTimeout: 10000,
    viewport: { width: 1440, height: 1000 },
    screenshot: "only-on-failure",
    trace: "retain-on-failure",
  },
});
