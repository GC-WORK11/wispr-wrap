#!/usr/bin/env node
/* wrap — one npm-bin entry for every platform.
 *
 * POSIX with bash: execs the battle-tested bin/wrap unchanged.
 * Windows (or any POSIX without bash): execs the Python port,
 * server/wrap_cli.py, which implements the same commands.
 *
 * npm generates wrap.cmd on Windows pointing at this file, so node —
 * which npm itself already requires — is the only hard dependency here.
 */
const { spawnSync, execFileSync } = require("child_process");
const fs = require("fs");
const path = require("path");

const ROOT = path.dirname(__dirname);

function have(cmd, arg) {
  try {
    execFileSync(cmd, [arg || "--version"], { stdio: "ignore" });
    return true;
  } catch (e) {
    return false;
  }
}

// first interpreter that actually answers — Windows python.org gives
// `python`, MS Store + *nix give `python3`, the launcher gives `py -3`.
function findPython() {
  if (have("python")) return ["python", []];
  if (have("python3")) return ["python3", []];
  try {
    execFileSync("py", ["-3", "--version"], { stdio: "ignore" });
    return ["py", ["-3"]];
  } catch (e) {
    return null;
  }
}

const isWin = process.platform === "win32";
const bash = isWin ? null : (have("bash") ? "bash" : (have("sh") ? "sh" : null));

let argv;
if (bash && fs.existsSync(path.join(ROOT, "bin", "wrap"))) {
  argv = [bash, [path.join(ROOT, "bin", "wrap")]];
} else {
  const py = findPython();
  if (!py) {
    console.error("wrap: python not found on PATH (need python 3.7+)");
    process.exit(1);
  }
  argv = [py[0], [...py[1], path.join(ROOT, "server", "wrap_cli.py")]];
}

const r = spawnSync(argv[0], [...argv[1], ...process.argv.slice(2)], {
  stdio: "inherit",
  cwd: ROOT,
});
process.exit(r.status === null ? 1 : r.status);
