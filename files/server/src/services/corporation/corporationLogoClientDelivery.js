"use strict";

// Custom corporation logo delivery.
//
// The stock client never asks the image server for a player corporation's logo:
// eveIcon.CorpIcon.DoLayout always builds it from the three emblem layers
// (shape1-3 / color1-3) in cfg.corptickernames. Only alliances go through
// photo.GetAllianceLogo. Every player-corp logo in the client (info window,
// corp banner, search results, menus, OwnerIcon, GetLogoIcon) goes through
// CorpIcon, so this patches CorpIcon.DoLayout at runtime, in the login
// handshake's signedFunc, instead of editing code.ccp.
//
// Corporations with a custom PNG in <runtime images>/Corporation/<id>_<size>.png
// are drawn from that PNG through the client's own photo.GetCorporationLogo
// (which requests <imageserverurl>Corporation/<id>_<size>.png and caches it in
// cache:/Pictures/Corporations). All other corporations keep the stock emblem.

const fs = require("node:fs");
const path = require("node:path");
const { resolveRuntimeImagesDir } = require(path.join(
  __dirname,
  "../../gameStore/storeRoot",
));

const CUSTOM_LOGO_FILE = /^(\d+)_(\d+)\.png$/i;
const FIRST_NON_NPC_CORPORATION_ID = 2000000;
const MAX_DELIVERED_IDS = 20000;

function listCustomCorporationLogoIDs(dir = path.join(resolveRuntimeImagesDir(), "Corporation")) {
  let names;
  try {
    names = fs.readdirSync(dir);
  } catch (_err) {
    return [];
  }
  const ids = new Set();
  for (const name of names) {
    const match = CUSTOM_LOGO_FILE.exec(name);
    if (!match) continue;
    const id = Number(match[1]);
    if (Number.isSafeInteger(id) && id >= FIRST_NON_NPC_CORPORATION_ID) ids.add(id);
  }
  return Array.from(ids).sort((a, b) => a - b).slice(0, MAX_DELIVERED_IDS);
}

// Python 2.7 lines, one-space indents, single quotes only: the handshake joins them
// with \n inside a double-quoted compile() string.
function buildCorporationLogoSignedFuncSource(ids = listCustomCorporationLogoIDs()) {
  const idList = ids.map((id) => String(Math.trunc(id))).join(", ");
  return [
    "try:",
    " import trinity as _evejs_trinity",
    " import eve.client.script.ui.control.eveIcon as _evejs_eveIcon",
    ` _evejs_eveIcon._evejsCustomCorpLogoIDs = set([${idList}])`,
    " if not getattr(_evejs_eveIcon, '_evejsCorpLogoPatchInstalled', False):",
    "  _evejs_eveIcon._evejs_original_corp_do_layout = _evejs_eveIcon.CorpIcon.DoLayout",
    "  def _evejs_corp_do_layout(self, corpID, attributes, _orig=_evejs_eveIcon._evejs_original_corp_do_layout, _mod=_evejs_eveIcon, _trinity=_evejs_trinity):",
    "   try:",
    "    layers = list(self.children) if not self.destroyed else []",
    "    if layers and corpID and corpID in _mod._evejsCustomCorpLogoIDs:",
    "     for _layer in layers:",
    "      _layer.SetRGBA(1.0, 1.0, 1.0, 0.0)",
    "     _png = layers[0]",
    "     _png.SetSecondaryTexturePath(None)",
    "     _png.spriteEffect = _trinity.TR2_SFX_COPY",
    "     _png.SetRGBA(1.0, 1.0, 1.0, 1.0)",
    "     _size = 256 if attributes.get('big', False) else 128",
    "     sm.GetService('photo').GetCorporationLogo(corpID, _size, _png)",
    "     self._evejsCustomLogo = True",
    "     return",
    "    if layers and getattr(self, '_evejsCustomLogo', False):",
    "     for _layer in layers:",
    "      _layer.SetRGBA(1.0, 1.0, 1.0, 0.0)",
    "     self._evejsCustomLogo = False",
    "   except:",
    "    import traceback",
    "    traceback.print_exc()",
    "   return _orig(self, corpID, attributes)",
    "  _evejs_eveIcon.CorpIcon.DoLayout = _evejs_corp_do_layout",
    "  _evejs_eveIcon._evejsCorpLogoPatchInstalled = True",
    " print 'CORP_LOGO_PNG_PATCH:OK:%d' % len(_evejs_eveIcon._evejsCustomCorpLogoIDs)",
    "except:",
    " import traceback",
    " traceback.print_exc()",
  ];
}

module.exports = {
  buildCorporationLogoSignedFuncSource,
  listCustomCorporationLogoIDs,
};
