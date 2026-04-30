/**
 * Run uvicorn with the monorepo root .venv (Windows/macOS/Linux), not system Python.
 */
const { spawn } = require("child_process");
const path = require("path");
const fs = require("fs");

const root = path.join(__dirname, "..");
const isWin = process.platform === "win32";
const py = path.join(root, isWin ? ".venv/Scripts/python.exe" : ".venv/bin/python");
const backend = path.join(root, "backend");

if (!fs.existsSync(py)) {
  console.error(
    "No Python venv at:\n  " + py + "\n" +
    "At repo root run:  python -m venv .venv\n" +
    "Then:  " + (isWin ? ".venv\\Scripts\\pip" : ".venv/bin/pip") + " install -r backend/requirements.txt"
  );
  process.exit(1);
}
if (!fs.existsSync(path.join(backend, "main.py"))) {
  console.error("backend/main.py not found (wrong cwd?)", backend);
  process.exit(1);
}

/** Default when 8000 is taken by another app (common on Windows). Override: MDHHS_BACKEND_PORT=8000 */
const port = (process.env.MDHHS_BACKEND_PORT || "8001").trim();
const child = spawn(
  py,
  ["-m", "uvicorn", "main:app", "--reload", "--host", "127.0.0.1", "--port", port],
  { cwd: backend, stdio: "inherit", env: { ...process.env, PYTHONUNBUFFERED: "1" } }
);
child.on("exit", (code, sig) => {
  process.exit(code ?? (sig ? 1 : 0));
});
