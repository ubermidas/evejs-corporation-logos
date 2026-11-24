"use strict";

// The server-side changes of the corporation logo add-on, for Node.js.
// install.py does the same in Python; tests/test_addon.py checks that both
// produce identical files.
//
//   server/src/services/corporation/corporationLogoClientDelivery.js  (new)
//   server/src/network/tcp/handshake.js        one block before the GM Server Health block
//   server/src/_secondary/image/imageRequestResolver.js
//                                              a PNG lookup, unless the file already has one

const fs = require("node:fs");
const path = require("node:path");
const { randomUUID } = require("node:crypto");

const DELIVERY = "server/src/services/corporation/corporationLogoClientDelivery.js";
const HANDSHAKE = "server/src/network/tcp/handshake.js";
const RESOLVER = "server/src/_secondary/image/imageRequestResolver.js";
const ANCHOR = "  // Last, in its own try block: the GM Server Health window";
const BLOCK_LINES = [
  "  // Custom corporation logos: draw player corps that have a PNG in the image store",
  "  // through photo.GetCorporationLogo instead of the three emblem layers.",
  "  const corporationLogoDelivery = require(path.join(",
  "    __dirname,",
  '    "../../services/corporation/corporationLogoClientDelivery",',
  "  ));",
  "  source.push.apply(source, corporationLogoDelivery.buildCorporationLogoSignedFuncSource());",
  "",
  "",
];

const MARK = "// corp-logo-addon";
const RESOLVER_REQUIRE_ANCHOR = 'const path = require("path");';
const RESOLVER_FUNCTION_ANCHOR = "function resolveCorporationLogoPath(corporationID, size) {";
const RESOLVER_REQUIRES = [
  'const corpLogoAddonFs = require("fs"); // corp-logo-addon',
  'const { resolveRuntimeImagesDir: corpLogoAddonImagesDir } = require("../../gameStore/storeRoot"); // corp-logo-addon',
];
const RESOLVER_FUNCTION = [
  "// corp-logo-addon: begin",
  "// A corporation's own PNG from <runtime images>/Corporation/<id>_<size>.png,",
  "// the requested size first. Added by the corporation logo add-on.",
  "function corpLogoAddonFind(corporationID, size) {",
  "  const id = toPositiveInteger(corporationID, null);",
  "  if (!id) return null;",
  '  const root = path.join(corpLogoAddonImagesDir(), "Corporation");',
  "  const sizes = [size, 32, 64, 128, 256, 512, 1024].filter((value, index, all) => value && all.indexOf(value) === index);",
  "  for (const candidate of sizes) {",
  "    const filePath = path.join(root, `${id}_${candidate}.png`);",
  "    if (corpLogoAddonFs.existsSync(filePath)) return filePath;",
  "  }",
  "  return null;",
  "}",
  "// corp-logo-addon: end",
  "",
];
const RESOLVER_CALL = [
  "  const corpLogoAddonPath = corpLogoAddonFind(corporationID, size); // corp-logo-addon",
  "  if (corpLogoAddonPath) return corpLogoAddonPath; // corp-logo-addon",
];

// Files are handled as latin1 strings so every byte, including mixed line
// endings, survives unchanged.
function read(root, relative) {
  return fs.readFileSync(path.join(root, relative)).toString("latin1");
}

function newlineOf(text) {
  return text.includes("\r\n") ? "\r\n" : "\n";
}

function blockFor(text) {
  return BLOCK_LINES.join(newlineOf(text));
}

function count(text, needle) {
  return text.split(needle).length - 1;
}

// Lines with their endings, like Python's splitlines(keepends=True) for \n and \r\n.
function lines(text) {
  return text.match(/[^\n]*\n|[^\n]+$/g) || [];
}

function body(line) {
  return line.replace(/\r?\n$/, "");
}

function resolverState(text) {
  if (text.includes(MARK)) return "patched";
  if (text.includes("findCustomCorporationLogoPath")) return "hand-edited";
  if (count(text, RESOLVER_REQUIRE_ANCHOR) === 1 && count(text, RESOLVER_FUNCTION_ANCHOR) === 1) return "stock";
  return "unsupported";
}

function patchResolver(text) {
  let out = "";
  for (const line of lines(text)) {
    const content = body(line);
    const ending = line.slice(content.length) || newlineOf(text);
    if (content === RESOLVER_FUNCTION_ANCHOR) {
      out += RESOLVER_FUNCTION.map((part) => part + ending).join("");
      out += line;
      out += RESOLVER_CALL.map((part) => part + ending).join("");
      continue;
    }
    out += line;
    if (content === RESOLVER_REQUIRE_ANCHOR) out += RESOLVER_REQUIRES.map((part) => part + ending).join("");
  }
  return out;
}

function unpatchResolver(text) {
  let out = "";
  let inBlock = false;
  let afterBlock = false;
  for (const line of lines(text)) {
    const content = body(line);
    if (afterBlock) {
      afterBlock = false;
      if (!content) continue;
    }
    if (content === "// corp-logo-addon: begin") {
      inBlock = true;
      continue;
    }
    if (inBlock) {
      if (content === "// corp-logo-addon: end") {
        inBlock = false;
        afterBlock = true;
      }
      continue;
    }
    if (content.includes(MARK)) continue;
    out += line;
  }
  return out;
}

function deliverySource(packageRoot) {
  return fs.readFileSync(path.join(packageRoot, "files", DELIVERY));
}

function state(root, packageRoot) {
  const handshake = read(root, HANDSHAKE);
  const deliveryPath = path.join(root, DELIVERY);
  const deliveryPresent = fs.existsSync(deliveryPath);
  return {
    handshakeAnchor: count(handshake, ANCHOR) === 1,
    handshakePatched: handshake.includes(blockFor(handshake)),
    deliveryPresent,
    deliveryCurrent: deliveryPresent && fs.readFileSync(deliveryPath).equals(deliverySource(packageRoot)),
    imageServer: resolverState(read(root, RESOLVER)),
  };
}

function writeAtomic(file, bytes) {
  fs.mkdirSync(path.dirname(file), { recursive: true });
  const temp = `${file}.${randomUUID()}.tmp`;
  try {
    fs.writeFileSync(temp, bytes, { flag: "wx" });
    fs.renameSync(temp, file);
  } finally {
    fs.rmSync(temp, { force: true });
  }
}

function backup(root, relative, folder) {
  const source = path.join(root, relative);
  if (!fs.existsSync(source)) return;
  const target = path.join(folder, relative);
  fs.mkdirSync(path.dirname(target), { recursive: true });
  fs.copyFileSync(source, target);
}

// Returns the list of changes made. Throws before changing anything when the
// EveJS files are not as expected.
function install(root, packageRoot, backupFolder) {
  const current = state(root, packageRoot);
  if (!current.handshakePatched && !current.handshakeAnchor) {
    throw new Error(`${HANDSHAKE} does not have the expected anchor; this EveJS version is not supported.`);
  }
  if (current.imageServer === "unsupported") {
    throw new Error(`${RESOLVER} does not look as expected; this EveJS version is not supported.`);
  }
  const done = [];
  if (current.deliveryPresent && !current.deliveryCurrent) backup(root, DELIVERY, backupFolder);
  if (!current.deliveryCurrent) {
    writeAtomic(path.join(root, DELIVERY), deliverySource(packageRoot));
    done.push(`installed ${DELIVERY}`);
  }
  if (!current.handshakePatched) {
    backup(root, HANDSHAKE, backupFolder);
    const text = read(root, HANDSHAKE);
    writeAtomic(path.join(root, HANDSHAKE), Buffer.from(text.replace(ANCHOR, blockFor(text) + ANCHOR), "latin1"));
    done.push(`patched ${HANDSHAKE}`);
  }
  if (current.imageServer === "stock") {
    backup(root, RESOLVER, backupFolder);
    writeAtomic(path.join(root, RESOLVER), Buffer.from(patchResolver(read(root, RESOLVER)), "latin1"));
    done.push(`patched ${RESOLVER}`);
  } else if (current.imageServer === "hand-edited") {
    done.push(`left ${RESOLVER} as it is: it already serves corporation PNGs`);
  }
  return done;
}

function remove(root, packageRoot, backupFolder) {
  const current = state(root, packageRoot);
  const done = [];
  if (current.handshakePatched) {
    backup(root, HANDSHAKE, backupFolder);
    const text = read(root, HANDSHAKE);
    writeAtomic(path.join(root, HANDSHAKE), Buffer.from(text.replace(blockFor(text), ""), "latin1"));
    done.push(`unpatched ${HANDSHAKE}`);
  }
  if (current.imageServer === "patched") {
    backup(root, RESOLVER, backupFolder);
    writeAtomic(path.join(root, RESOLVER), Buffer.from(unpatchResolver(read(root, RESOLVER)), "latin1"));
    done.push(`unpatched ${RESOLVER}`);
  }
  if (current.deliveryPresent) {
    backup(root, DELIVERY, backupFolder);
    fs.rmSync(path.join(root, DELIVERY), { force: true });
    done.push(`removed ${DELIVERY}`);
  }
  return done;
}

module.exports = {
  DELIVERY,
  HANDSHAKE,
  RESOLVER,
  install,
  patchResolver,
  remove,
  state,
  unpatchResolver,
};
