const { spawn } = require("child_process");
const express = require("express");
const { createProxyMiddleware } = require("http-proxy-middleware");

const app = express();

const appPort = Number(process.env.PORT || 3000);
const streamlitPort = Number(process.env.STREAMLIT_PORT || 8501);
const pythonBin = process.env.PYTHON_BIN || "python3";

let streamlitReady = false;
let streamlitExited = false;
let streamlitExitCode = null;

const streamlitArgs = [
  "-m",
  "streamlit",
  "run",
  "app.py",
  "--server.port",
  String(streamlitPort),
  "--server.address",
  "127.0.0.1",
  "--server.headless",
  "true",
  "--browser.gatherUsageStats",
  "false",
];

const streamlitProc = spawn(pythonBin, streamlitArgs, {
  cwd: process.cwd(),
  env: process.env,
  stdio: ["ignore", "pipe", "pipe"],
});

streamlitProc.stdout.on("data", (data) => {
  const text = data.toString();
  process.stdout.write(text);

  if (text.includes("Network URL") || text.includes("Local URL")) {
    streamlitReady = true;
  }
});

streamlitProc.stderr.on("data", (data) => {
  const text = data.toString();
  process.stderr.write(text);

  if (text.toLowerCase().includes("streamlit") && !text.toLowerCase().includes("error")) {
    streamlitReady = true;
  }
});

streamlitProc.on("exit", (code) => {
  streamlitExited = true;
  streamlitExitCode = code;
  console.error(`Streamlit process exited with code ${code}`);
});

app.get("/health", (_req, res) => {
  if (streamlitReady && !streamlitExited) {
    return res.status(200).json({ ok: true, service: "streamlit" });
  }

  if (streamlitExited) {
    return res.status(500).json({
      ok: false,
      error: "streamlit_exited",
      exitCode: streamlitExitCode,
    });
  }

  return res.status(503).json({ ok: false, status: "starting" });
});

app.use(
  "/",
  createProxyMiddleware({
    target: `http://127.0.0.1:${streamlitPort}`,
    changeOrigin: true,
    ws: true,
    proxyTimeout: 60000,
  })
);

const server = app.listen(appPort, "0.0.0.0", () => {
  console.log(`Node gateway listening on port ${appPort}`);
});

const shutdown = () => {
  server.close(() => {
    if (!streamlitExited) {
      streamlitProc.kill("SIGTERM");
    }
    process.exit(0);
  });
};

process.on("SIGINT", shutdown);
process.on("SIGTERM", shutdown);
