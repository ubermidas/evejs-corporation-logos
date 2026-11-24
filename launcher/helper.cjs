"use strict";

// EVEJS Launcher lifecycle helper for the Corporation Logos mod.
//
// The launcher runs:  node helper.cjs --request <request.json> --result <result.json>
// with a request such as
//   {"protocol":"evejs_launcher_mod_v1","requestId":"...","action":"install",
//    "profile":null,"runtime":{"backend":"native","evejsRoot":"C:\\EVE\\evejs"},
//    "mod":{"id":"evejs-corporation-logos"}}
// and reads the result file this writes. Actions: install, prepare_disable,
// prepare_remove and recover. Game must be stopped; the logos themselves are
// never touched, only the three server files the mod changes.

const fs = require("node:fs");
const net = require("node:net");
const path = require("node:path");
const { randomUUID } = require("node:crypto");
const patch = require("./lib/patch.cjs");

const PROTOCOL = "evejs_launcher_mod_v1";
const MOD_ID = "evejs-corporation-logos";
const ACTIONS = ["install", "prepare_disable", "prepare_remove", "recover"];
const STATE_FILE = "_local/corporation-logos/state.json";
const BACKUP_ROOT = "_local/corporation-logos/backups";
const VERSION = JSON.parse(fs.readFileSync(path.join(__dirname, "evejs-launcher.mod.json"), "utf8")).version;

function argument(name) {
  const index = process.argv.indexOf(name);
  if (index < 2 || !process.argv[index + 1]) throw new Error(`Missing ${name}.`);
  return process.argv[index + 1];
}

function readJson(file) {
  return JSON.parse(fs.readFileSync(file, "utf8").replace(/^\uFEFF/, ""));
}

function writeAtomic(file, text) {
  fs.mkdirSync(path.dirname(file), { recursive: true });
  const temp = `${file}.${randomUUID()}.tmp`;
  try {
    fs.writeFileSync(temp, text, { flag: "wx" });
    fs.renameSync(temp, file);
  } finally {
    fs.rmSync(temp, { force: true });
  }
}

// Game must not be running: reserve its socket for a moment.
function assertGameStopped(root) {
  let network = {};
  const config = path.join(root, "config", "server.json");
  if (fs.existsSync(config)) network = readJson(config).network || {};
  const port = Number(network.serverPort ?? 26000);
  const host = network.gameServerBindHost || "127.0.0.1";
  if (!Number.isInteger(port) || port < 1 || port > 65535) {
    return Promise.reject(new Error("Set a valid Game port before changing Corporation Logos."));
  }
  return new Promise((resolve, reject) => {
    const probe = net.createServer();
    probe.once("error", (error) => {
      reject(new Error(`Stop Game before changing Corporation Logos. Game's address ${host}:${port} is in use (${error.code}).`));
    });
    probe.listen({ port, host, exclusive: true }, () => probe.close(resolve));
  });
}

function fullyInstalled(current) {
  return current.handshakePatched && current.deliveryCurrent &&
    (current.imageServer === "patched" || current.imageServer === "hand-edited");
}

function anyInstalled(current) {
  return current.handshakePatched || current.deliveryPresent || current.imageServer === "patched";
}

async function run(request) {
  if (!request || request.protocol !== PROTOCOL || !ACTIONS.includes(request.action) ||
      request.mod?.id !== MOD_ID || request.profile !== null || request.runtime?.backend !== "native") {
    throw new Error("Expected a native EVEJS Launcher lifecycle request for Corporation Logos.");
  }
  if (typeof request.runtime.evejsRoot !== "string" || !path.isAbsolute(request.runtime.evejsRoot)) {
    throw new Error("The launcher did not give the EveJS folder.");
  }
  const root = fs.realpathSync(request.runtime.evejsRoot);
  for (const relative of [patch.HANDSHAKE, patch.RESOLVER]) {
    if (!fs.existsSync(path.join(root, relative))) {
      throw new Error(`${root} is not an EveJS folder this mod supports (${relative} is missing).`);
    }
  }
  const stateFile = path.join(root, STATE_FILE);
  const saved = fs.existsSync(stateFile) ? readJson(stateFile) : null;
  if (saved && (saved.schema !== 1 || !["active", "stock", "installing", "restoring"].includes(saved.status))) {
    throw new Error(`Unrecognised Corporation Logos state in ${STATE_FILE}. Review it before retrying.`);
  }
  const enable = request.action === "install" ||
    (request.action === "recover" && ["active", "installing"].includes(saved?.status));
  const current = patch.state(root, __dirname);

  if (enable && fullyInstalled(current)) {
    if (saved?.status !== "active") writeAtomic(stateFile, `${JSON.stringify({ schema: 1, version: VERSION, status: "active" })}\n`);
    return "Corporation Logos is already installed.";
  }
  if (!enable && !anyInstalled(current)) {
    if (saved && saved.status !== "stock") writeAtomic(stateFile, `${JSON.stringify({ schema: 1, version: VERSION, status: "stock" })}\n`);
    return "Corporation Logos is already disabled.";
  }

  await assertGameStopped(root);
  const record = (status) => writeAtomic(stateFile, `${JSON.stringify({ schema: 1, version: VERSION, status })}\n`);
  const stamp = new Date().toISOString().replace(/[-:]/g, "").replace(/\.\d+Z$/, "Z");
  const backups = path.join(root, BACKUP_ROOT, stamp);
  const wasInstalled = fullyInstalled(current);
  record(enable ? "installing" : "restoring");
  try {
    if (enable) patch.install(root, __dirname, backups);
    else patch.remove(root, __dirname, backups);
    record(enable ? "active" : "stock");
  } catch (error) {
    try {
      if (wasInstalled) patch.install(root, __dirname, backups);
      else patch.remove(root, __dirname, backups);
      record(wasInstalled ? "active" : "stock");
    } catch (rollbackError) {
      throw new Error(`${error.message} Undoing it also failed: ${rollbackError.message} The original files are in ${backups}.`);
    }
    throw error;
  }
  return enable
    ? "Corporation Logos installed. Start Game; players see corporations with a PNG logo after they log in."
    : "Corporation Logos removed from the EveJS source. The logo images were kept.";
}

async function main() {
  const request = readJson(argument("--request"));
  let reply;
  try {
    reply = { success: true, state: "ready", message: await run(request) };
  } catch (error) {
    reply = { success: false, state: "failed", message: String(error && error.message || error).slice(0, 4096) };
  }
  fs.writeFileSync(argument("--result"), JSON.stringify({
    protocol: PROTOCOL,
    requestId: request && request.requestId,
    ...reply,
    restartRequired: [],
    contributions: [],
    environment: {},
    arguments: [],
  }), { flag: "wx" });
}

if (require.main === module) {
  void main().catch((error) => {
    console.error(error.message);
    process.exitCode = 1;
  });
}

module.exports = { run };
