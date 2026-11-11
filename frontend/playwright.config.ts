import { defineConfig, devices } from "@playwright/test";

// Runs the real FastAPI backend serving the built static UI (run `npm run build` first).
// E2E_REAL=1 uses faster-whisper instead of the deterministic test engine.
const PY = process.env.E2E_PYTHON ?? "python";
const real = process.env.E2E_REAL === "1";
const port = 8799;

export default defineConfig({
  testDir: "./e2e",
  timeout: real ? 300_000 : 60_000,
  fullyParallel: false,
  workers: 1,
  reporter: [["list"]],
  use: { baseURL: `http://127.0.0.1:${port}`, trace: "retain-on-failure" },
  projects: [
    { name: "desktop", use: { ...devices["Desktop Chrome"], viewport: { width: 1366, height: 900 } } },
    { name: "mobile", use: { ...devices["Pixel 7"] } },
  ],
  webServer: {
    command: `${PY} -m uvicorn app.main:app --app-dir ../backend --host 127.0.0.1 --port ${port}`,
    url: `http://127.0.0.1:${port}/api/health`,
    reuseExistingServer: false,
    timeout: 60_000,
    env: {
      TRANSCRIBLY_DATA_DIR: process.env.E2E_DATA_DIR ?? "../.e2e-data",
      TRANSCRIBLY_FRONTEND_DIST: "out",
      TRANSCRIBLY_JOBS_PER_IP_PER_HOUR: "0",
      TRANSCRIBLY_MAX_UPLOAD_MB: "5",
      ...(real
        ? {
            TRANSCRIBLY_WHISPER_MODEL: process.env.E2E_MODEL ?? "base",
            ...(process.env.TRANSCRIBLY_MODEL_DIR ? { TRANSCRIBLY_MODEL_DIR: process.env.TRANSCRIBLY_MODEL_DIR } : {}),
          }
        : { TRANSCRIBLY_TEST_MODE: "true", TRANSCRIBLY_TRANSCRIPTION_ENGINE: "mock" }),
    },
  },
});
