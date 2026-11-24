"use strict";

// Stores, lists and removes corporation logo PNGs in EveJS's runtime image
// folder: <runtime images>/Corporation/<corporationID>_<size>.png.
//
// corp_logo.py runs this inside the EveJS server container (Docker) or in
// the EveJS folder (native), passing it with `node -e`, so nothing has to be
// installed or rebuilt to set a logo. Commands come in as JSON on stdin:
//   {"command":"set","corporationID":98000002,"images":{"32":"<base64>",...}}
//   {"command":"remove","corporationID":98000002}
//   {"command":"list"}
// It prints one JSON line with the result.

const fs = require("node:fs");
const path = require("node:path");

const SIZES = [32, 64, 128, 256, 512, 1024];
const MAX_BYTES = 2 * 1024 * 1024;
const FIRST_PLAYER_CORPORATION_ID = 2000000;
const PNG_MAGIC = Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]);

function logoRoot() {
  const storeRoot = require(path.resolve("server/src/gameStore/storeRoot"));
  return path.join(storeRoot.resolveRuntimeImagesDir(), "Corporation");
}

function corporationIDOf(value) {
  const id = Math.trunc(Number(value));
  if (!Number.isSafeInteger(id) || id < FIRST_PLAYER_CORPORATION_ID) {
    throw new Error("Give a player or bot corporation ID (NPC corporations keep their game logos).");
  }
  return id;
}

function pngSize(bytes) {
  if (bytes.length < 33 || !bytes.subarray(0, 8).equals(PNG_MAGIC) || bytes.toString("latin1", 12, 16) !== "IHDR") return null;
  return { width: bytes.readUInt32BE(16), height: bytes.readUInt32BE(20) };
}

function writeAtomic(filePath, bytes) {
  const temp = `${filePath}.${process.pid}.tmp`;
  fs.writeFileSync(temp, bytes);
  fs.renameSync(temp, filePath);
}

function set(request) {
  const corporationID = corporationIDOf(request.corporationID);
  const images = request.images || {};
  const decoded = SIZES.map((size) => {
    const bytes = Buffer.from(String(images[String(size)] || ""), "base64");
    const dimensions = pngSize(bytes);
    if (!dimensions) throw new Error(`The ${size} px image is missing or not a PNG.`);
    if (bytes.length > MAX_BYTES) throw new Error(`The ${size} px image is larger than 2 MB.`);
    return { size, bytes };
  });
  const root = logoRoot();
  fs.mkdirSync(root, { recursive: true });
  for (const { size, bytes } of decoded) writeAtomic(path.join(root, `${corporationID}_${size}.png`), bytes);
  return { ok: true, corporationID, sizes: SIZES, folder: root };
}

function remove(request) {
  const corporationID = corporationIDOf(request.corporationID);
  const root = logoRoot();
  const removed = [];
  for (const size of SIZES) {
    try {
      fs.unlinkSync(path.join(root, `${corporationID}_${size}.png`));
      removed.push(size);
    } catch (error) {
      if (error.code !== "ENOENT") throw error;
    }
  }
  return { ok: true, corporationID, removed, folder: root };
}

function list() {
  const root = logoRoot();
  const logos = {};
  let names = [];
  try {
    names = fs.readdirSync(root);
  } catch {
    names = [];
  }
  for (const name of names) {
    const match = /^(\d+)_(\d+)\.png$/i.exec(name);
    if (!match || Number(match[1]) < FIRST_PLAYER_CORPORATION_ID) continue;
    (logos[match[1]] ||= []).push(Number(match[2]));
  }
  for (const sizes of Object.values(logos)) sizes.sort((a, b) => a - b);
  return { ok: true, folder: root, logos };
}

async function main() {
  const chunks = [];
  for await (const chunk of process.stdin) chunks.push(chunk);
  const request = JSON.parse(Buffer.concat(chunks).toString("utf8") || "{}");
  const commands = { set, remove, list };
  const command = commands[request.command];
  if (!command) throw new Error(`Unknown command: ${request.command}`);
  return command(request);
}

main().then(
  (result) => { process.stdout.write(`${JSON.stringify(result)}\n`); },
  (error) => {
    process.stdout.write(`${JSON.stringify({ ok: false, error: String(error && error.message || error) })}\n`);
    process.exitCode = 1;
  },
);
