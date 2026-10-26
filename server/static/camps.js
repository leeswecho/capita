// Camps, shared by the player page (player/static/index.html) and the developer view
// (server/static/index.html); both servers serve this file as /camps.js. Each nation's people on a
// tile are drawn as a scatter of tepees, round huts if settled, or covered wagons for a settling
// party, more for more people (the server says which and how many: "camps", server/camps.py). Own
// camps are ochre, other nations' rust red. Each camp on a tile gets its own strip of the tile, and
// wagons are drawn smaller beside huts. They sit at fixed, well-spread places: for each tile, nation
// and look, a seeded best-candidate sequence of spots, of which a camp of n takes the first n, so one
// is added or removed only when the count changes.
import * as THREE from "three";

export const TEPEE_SIZE = 0.2, TEPEE_MAX = 10;   // size in tiles
const WAGON_SIZE = 0.24, WAGON_SMALL = 0.14;      // a wagon alone on a tile, and beside huts
const LOOKS = ["huts", "tepees", "wagons"];
const OWN = "#d9a441", OTHER = "#a4483a";   // ochre for the nation whose map it is, rust red for others
function tepeeTexture(hide) {
  // A tepee seen from above and to the south, as in an isometric view: a cone whose base is an
  // ellipse, lit from the upper left, with its shadow on the ground, a door and the poles' tips.
  const cv = document.createElement("canvas");
  cv.width = cv.height = 64;
  const c = cv.getContext("2d");
  const shade = k => { const n = parseInt(hide.slice(1), 16), f = v => Math.max(0, Math.min(255, Math.round(v * k)));
    return `rgb(${f(n >> 16)},${f((n >> 8) & 255)},${f(n & 255)})`; };
  const ax = 30, ay = 9, bx = 32, by = 50, rx = 21, ry = 7;            // apex; base ellipse
  c.beginPath(); c.ellipse(bx + 7, by + 3, rx + 2, ry + 1, 0, 0, Math.PI * 2);   // shadow, cast to the lower right
  c.fillStyle = "rgba(0, 0, 0, 0.35)"; c.fill();
  const cone = () => { c.beginPath(); c.moveTo(ax, ay); c.lineTo(bx - rx, by); c.ellipse(bx, by, rx, ry, 0, Math.PI, 0, true); c.closePath(); };
  const g = c.createLinearGradient(bx - rx, 0, bx + rx, 0);            // lit on the left, in shade on the right
  g.addColorStop(0, shade(1.25)); g.addColorStop(0.45, shade(1.0)); g.addColorStop(1, shade(0.55));
  cone(); c.fillStyle = g; c.fill();
  c.save(); cone(); c.clip();                                          // seams of the hide
  c.strokeStyle = "rgba(21, 18, 13, 0.35)"; c.lineWidth = 1.2;
  for (const x of [bx - 11, bx + 9]) { c.beginPath(); c.moveTo(ax, ay); c.lineTo(x, by + ry * 0.9); c.stroke(); }
  c.restore();
  c.beginPath(); c.moveTo(ax + 0.5, 31); c.lineTo(bx + 5, by + ry - 0.5); c.lineTo(bx - 4, by + ry - 0.3); c.closePath();   // the door
  c.fillStyle = "#1d1810"; c.fill();
  cone(); c.lineJoin = "round"; c.lineWidth = 2.5; c.strokeStyle = "#15120d"; c.stroke();
  c.beginPath(); c.moveTo(ax - 7, 1); c.lineTo(ax + 2, 13); c.moveTo(ax + 6, 0); c.lineTo(ax - 2, 13); c.moveTo(ax, 0); c.lineTo(ax, 12);   // pole tips
  c.lineWidth = 2; c.stroke();
  const tex = new THREE.CanvasTexture(cv);
  tex.colorSpace = THREE.SRGBColorSpace;
  tex.anisotropy = 4;
  return tex;
}
function shader(hex) {   // k -> the colour hex x k, for shading
  const n = parseInt(hex.slice(1), 16), f = (v, k) => Math.max(0, Math.min(255, Math.round(v * k)));
  return k => `rgb(${f(n >> 16, k)},${f((n >> 8) & 255, k)},${f(n & 255, k)})`;
}
function hutTexture(thatch) {
  // A round hut seen from above and to the south, like the tepees: a mud wall, a door, and a conical
  // thatched roof (in the nation's colour) that overhangs it, lit from the upper left.
  const cv = document.createElement("canvas");
  cv.width = cv.height = 64;
  const c = cv.getContext("2d"), wall = shader("#9c7b56"), roof = shader(thatch);
  const bx = 32, wy0 = 34, wy1 = 50, rx = 17, ry = 6;                    // wall: from the eaves down to the ground
  c.beginPath(); c.ellipse(bx + 7, wy1 + 3, rx + 4, ry + 1, 0, 0, Math.PI * 2);   // shadow
  c.fillStyle = "rgba(0, 0, 0, 0.35)"; c.fill();
  const body = () => { c.beginPath(); c.moveTo(bx - rx, wy0); c.lineTo(bx - rx, wy1); c.ellipse(bx, wy1, rx, ry, 0, Math.PI, 0, true); c.lineTo(bx + rx, wy0); c.closePath(); };
  const gw = c.createLinearGradient(bx - rx, 0, bx + rx, 0);
  gw.addColorStop(0, wall(1.2)); gw.addColorStop(0.45, wall(1.0)); gw.addColorStop(1, wall(0.55));
  body(); c.fillStyle = gw; c.fill();
  c.fillStyle = "#1d1810"; c.fillRect(bx - 1, wy0 + 6, 9, wy1 - wy0 - 1);   // the door, a little right of centre
  body(); c.lineJoin = "round"; c.lineWidth = 2.5; c.strokeStyle = "#15120d"; c.stroke();
  const ay = 8, ex = rx + 5, ey = wy0 + 1, ery = 7;                      // roof apex; eaves ellipse
  const cone = () => { c.beginPath(); c.moveTo(bx, ay); c.lineTo(bx - ex, ey); c.ellipse(bx, ey, ex, ery, 0, Math.PI, 0, true); c.closePath(); };
  const gr = c.createLinearGradient(bx - ex, 0, bx + ex, 0);
  gr.addColorStop(0, roof(1.25)); gr.addColorStop(0.45, roof(1.0)); gr.addColorStop(1, roof(0.55));
  cone(); c.fillStyle = gr; c.fill();
  c.save(); cone(); c.clip();                                            // the thatch's layers
  c.strokeStyle = "rgba(21, 18, 13, 0.3)"; c.lineWidth = 1.2;
  for (const k of [0.4, 0.7]) { c.beginPath(); c.ellipse(bx, ay + (ey - ay) * k, ex * k, ery * k, 0, 0, Math.PI); c.stroke(); }
  c.restore();
  cone(); c.lineWidth = 2.5; c.stroke();
  const tex = new THREE.CanvasTexture(cv);
  tex.colorSpace = THREE.SRGBColorSpace;
  tex.anisotropy = 4;
  return tex;
}
function wagonTexture(body) {
  // A covered wagon seen from above and to the south, facing left: a plank box in the nation's colour on
  // two spoked wheels, under a canvas hood on hoops, with its shadow cast to the lower right.
  const cv = document.createElement("canvas");
  cv.width = cv.height = 64;
  const c = cv.getContext("2d"), wood = shader(body), canvas = shader("#e8dfc4");
  c.beginPath(); c.ellipse(36, 52, 26, 5, 0, 0, Math.PI * 2);           // shadow
  c.fillStyle = "rgba(0, 0, 0, 0.35)"; c.fill();
  const x0 = 10, x1 = 54, by0 = 34, by1 = 44;                           // the box's side
  c.fillStyle = wood(0.6); c.fillRect(x0 + 2, by0 - 4, x1 - x0, 4);    // its top edge, seen from above
  const gb = c.createLinearGradient(0, by0, 0, by1);
  gb.addColorStop(0, wood(1.15)); gb.addColorStop(1, wood(0.75));
  c.fillStyle = gb; c.fillRect(x0, by0, x1 - x0, by1 - by0);
  c.strokeStyle = "rgba(21, 18, 13, 0.45)"; c.lineWidth = 1;
  c.beginPath(); c.moveTo(x0, by0 + 5); c.lineTo(x1, by0 + 5); c.stroke();   // a seam between planks
  const hood = () => { c.beginPath(); c.moveTo(x0 + 3, by0); c.bezierCurveTo(x0 + 1, 8, x1 + 1, 8, x1 - 1, by0); c.closePath(); };
  const gh = c.createLinearGradient(x0, 0, x1, 0);
  gh.addColorStop(0, canvas(1.08)); gh.addColorStop(0.5, canvas(1.0)); gh.addColorStop(1, canvas(0.7));
  hood(); c.fillStyle = gh; c.fill();
  c.save(); hood(); c.clip();                                            // the hoops under the canvas
  c.strokeStyle = "rgba(21, 18, 13, 0.3)"; c.lineWidth = 1.2;
  for (const x of [22, 32, 42]) { c.beginPath(); c.moveTo(x, by0); c.quadraticCurveTo(x + 1, 6, x + 2, 10); c.stroke(); }
  c.restore();
  c.beginPath(); c.ellipse(x0 + 4, by0 - 10, 3.5, 9, 0, 0, Math.PI * 2);   // the hood's open front, dark inside
  c.fillStyle = "#2a2219"; c.fill();
  hood(); c.lineJoin = "round"; c.lineWidth = 2.2; c.strokeStyle = "#15120d"; c.stroke();
  c.strokeRect(x0, by0, x1 - x0, by1 - by0);
  for (const wx of [18, 46]) {                                           // wheels
    c.beginPath(); c.arc(wx, by1 + 3, 7, 0, Math.PI * 2);
    c.fillStyle = "#5a4430"; c.fill(); c.lineWidth = 2; c.stroke();
    c.lineWidth = 1.2;
    for (let k = 0; k < 4; k++) {
      const a = k * Math.PI / 4;
      c.beginPath(); c.moveTo(wx - 6 * Math.cos(a), by1 + 3 - 6 * Math.sin(a)); c.lineTo(wx + 6 * Math.cos(a), by1 + 3 + 6 * Math.sin(a)); c.stroke();
    }
  }
  const tex = new THREE.CanvasTexture(cv);
  tex.colorSpace = THREE.SRGBColorSpace;
  tex.anisotropy = 4;
  return tex;
}

export function campSpots(seed, x0, x1, y0, y1) {   // up to TEPEE_MAX well-spread spots in a box, the same for the same seed
  let s = seed >>> 0 || 1;
  const rand = () => (s = Math.imul(s ^ (s >>> 15), 2246822519) + 0x9e3779b9 >>> 0) / 4294967296;
  const spots = [];
  for (let i = 0; i < TEPEE_MAX; i++) {
    let best = null, bestD = -1;
    for (let k = 0; k < 16; k++) {
      const p = [x0 + (x1 - x0) * rand(), y0 + (y1 - y0) * rand()];
      const d = spots.length ? Math.min(...spots.map(q => Math.hypot(p[0] - q[0], p[1] - q[1]))) : 1;
      if (d > bestD) { best = p; bestD = d; }
    }
    spots.push(best);
  }
  return spots;
}

const looks = new Map();   // depthTest -> {tepees: {own, other}, huts: ..., wagons: ...} materials
function look(depthTest) {
  if (!looks.has(depthTest)) {
    const material = tex => new THREE.MeshBasicMaterial({ map: tex, depthTest, depthWrite: false, transparent: true });
    const pair = draw => ({ own: material(draw(OWN)), other: material(draw(OTHER)) });
    looks.set(depthTest, { tepees: pair(tepeeTexture), huts: pair(hutTexture), wagons: pair(wagonTexture) });
  }
  return looks.get(depthTest);
}

// Add the camps' meshes to `group`. camps: [{tile: tile id, nation: {id} or null, own, look: "tepees",
// "huts" or "wagons", count, small: drawn smaller (wagons beside huts)}].
// The page supplies its grid helpers and quad(lat, lon, size), a flat square `size` degrees of latitude
// across at its marker height; depthTest false draws camps over everything (the player's flat map),
// true lets the globe hide those on its far side (the developer view). Returns the meshes added.
export function drawCamps(group, camps, { tile, parseTileId, tileIndex, quad, depthTest = false, renderOrder = 7 }) {
  const byTile = new Map(), added = [];
  for (const c of camps) (byTile.get(c.tile) || byTile.set(c.tile, []).get(c.tile)).push(c);
  const mats = look(depthTest);
  for (const [tid, list] of byTile) {
    const { lat, lon } = parseTileId(tid);
    list.forEach((c, j) => {   // strip j of the tile's width, in tile units from its south-west corner
      const size = c.look === "wagons" ? (c.small ? WAGON_SMALL : WAGON_SIZE) : TEPEE_SIZE;
      const m = size / 2 + 0.04;   // keep whole pictures inside the tile
      const x0 = j / list.length, x1 = (j + 1) / list.length;
      const seed = tileIndex(tid) * 7919 + (c.nation?.id || 0) * 104729 + LOOKS.indexOf(c.look) * 15485863;
      const spots = campSpots(seed, x0 + m, Math.max(x0 + m, x1 - m), m, 1 - m)
        .slice(0, c.count).sort((a, b) => b[1] - a[1]);   // northern ones first, so nearer ones overlap them
      const kind = mats[c.look];
      for (const [x, y] of spots) {
        const mesh = new THREE.Mesh(quad(lat + y * tile, lon + x * tile, tile * size), c.own ? kind.own : kind.other);
        mesh.renderOrder = renderOrder + (1 - y) * 0.5;   // drawn north to south (Three would otherwise sort these by distance)
        group.add(mesh);
        added.push(mesh);
      }
    });
  }
  return added;
}
