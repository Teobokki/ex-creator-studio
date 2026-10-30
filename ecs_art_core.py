"""EX ART engine: layered RGBA document, numpy compositing with blend modes, brushes, selections, adjustments,
filters, transforms, history and the .exart project format.  (c)2026 EX Inc./HYPERION DIGITAL (c)2026"""
import io, json, math, zipfile, colorsys
import numpy as np
import cv2
from PIL import Image, ImageDraw, ImageFont, ImageOps

BLENDS = ["Normal", "Multiply", "Screen", "Overlay", "Add", "Darken", "Lighten"]
PRESETS = [("1920 \u00d7 1080 (Full HD)", 1920, 1080), ("1080 \u00d7 1080 (Square)", 1080, 1080),
           ("3840 \u00d7 2160 (4K UHD)", 3840, 2160), ("1280 \u00d7 720 (HD)", 1280, 720),
           ("1080 \u00d7 1920 (Portrait)", 1080, 1920), ("2480 \u00d7 3508 (A4 300 dpi)", 2480, 3508)]
MAX_SIDE = 8192
_uid = [0]


def new_id():
    _uid[0] += 1
    return _uid[0]


# ------------------------------------------------------------------------------------------- layers / document
class Layer:
    __slots__ = ("id", "name", "px", "mask", "visible", "locked", "opacity", "blend", "text")

    def __init__(self, name, px, **kw):
        self.id = kw.get("id") or new_id(); self.name = name; self.px = px
        self.mask = kw.get("mask"); self.visible = kw.get("visible", True); self.locked = kw.get("locked", False)
        self.opacity = float(kw.get("opacity", 1.0)); self.blend = kw.get("blend", "Normal"); self.text = kw.get("text")

    def clone(self, deep=False, name=None):
        return Layer(name or self.name, self.px.copy() if deep else self.px,
                     id=None if deep else self.id, mask=(self.mask.copy() if (deep and self.mask is not None) else self.mask),
                     visible=self.visible, locked=self.locked, opacity=self.opacity, blend=self.blend,
                     text=dict(self.text) if self.text else None)


def blank(w, h, rgba=(0, 0, 0, 0)):
    a = np.empty((h, w, 4), np.uint8); a[:] = rgba
    return a


class Doc:
    def __init__(self, w, h, bg=(255, 255, 255, 255)):
        self.w, self.h = int(w), int(h)
        self.layers = [Layer("Background", blank(self.w, self.h, bg))]
        self.active = 0
        self.sel = None            # uint8 HxW selection mask (0..255) or None = everything
        self.path = None; self.dirty = False

    @property
    def layer(self):
        return self.layers[self.active]

    # snapshots share pixel arrays (copy-on-write: edits always replace a layer's array, never mutate history)
    def snapshot(self):
        return {"w": self.w, "h": self.h, "layers": [l.clone() for l in self.layers], "active": self.active, "sel": self.sel}

    def restore(self, s):
        self.w, self.h = s["w"], s["h"]; self.layers = [l.clone() for l in s["layers"]]
        self.active = min(s["active"], len(self.layers) - 1); self.sel = s["sel"]


class History:
    def __init__(self, doc, limit=80):
        self.doc = doc; self.limit = limit; self.items = [("New canvas", doc.snapshot())]; self.pos = 0

    def push(self, label):
        del self.items[self.pos + 1:]
        self.items.append((label, self.doc.snapshot())); self.pos = len(self.items) - 1
        while len(self.items) > self.limit or (len(self.items) > 12 and self.mem() > 2.2e9):
            self.items.pop(0); self.pos -= 1
        self.doc.dirty = True

    def mem(self):
        seen = {}
        for _, s in self.items:
            for l in s["layers"]:
                seen[id(l.px)] = l.px.nbytes
                if l.mask is not None: seen[id(l.mask)] = l.mask.nbytes
        return sum(seen.values())

    def can_undo(self): return self.pos > 0
    def can_redo(self): return self.pos < len(self.items) - 1

    def goto(self, i):
        i = max(0, min(len(self.items) - 1, i))
        self.pos = i; self.doc.restore(self.items[i][1]); self.doc.dirty = True

    def undo(self):
        if self.can_undo(): self.goto(self.pos - 1); return True
        return False

    def redo(self):
        if self.can_redo(): self.goto(self.pos + 1); return True
        return False


# ------------------------------------------------------------------------------------------- compositing
def _blend(mode, cb, cs):
    if mode == "Multiply": return cb * cs
    if mode == "Screen": return cb + cs - cb * cs
    if mode == "Overlay": return np.where(cb <= 0.5, 2 * cb * cs, 1 - 2 * (1 - cb) * (1 - cs))
    if mode == "Add": return np.minimum(cb + cs, 1.0)
    if mode == "Darken": return np.minimum(cb, cs)
    if mode == "Lighten": return np.maximum(cb, cs)
    return cs


def composite(layers, x0, y0, x1, y1, bg=None, skip=None, override=None):
    """Composite a region. Premultiplied float32 accumulation (no divisions in the Normal path).
    Returns (rgb, a): rgb straight-alpha (bg None) or already over bg (HxWx3 float). override: {layer_id: (px, mask)}."""
    h, w = y1 - y0, x1 - x0
    P = np.zeros((h, w, 3), np.float32); a = np.zeros((h, w, 1), np.float32)
    inv = np.float32(1.0 / 255.0)
    for l in layers:
        if not l.visible or l.opacity <= 0 or (skip is not None and l.id in skip): continue
        px, mk = (override or {}).get(l.id, (l.px, l.mask))
        s = px[y0:y1, x0:x1]
        al = s[..., 3]
        if not al.any(): continue
        sa = al[..., None].astype(np.float32); sa *= inv * l.opacity
        if mk is not None: sa *= mk[y0:y1, x0:x1, None].astype(np.float32) * inv
        cs = s[..., :3].astype(np.float32); cs *= inv
        if l.blend == "Normal":
            P *= (1 - sa); P += cs * sa
        else:
            Cb = P / np.maximum(a, 1e-6)
            B = _blend(l.blend, Cb, cs)
            P *= (1 - sa); P += sa * ((1 - a) * cs + a * B)
        a += sa * (1 - a)
    if bg is not None:
        return P + bg * (1 - a), a
    return P / np.maximum(a, 1e-6), a


def checker(h, w, x0=0, y0=0, s=10):
    yy, xx = np.mgrid[y0:y0 + h, x0:x0 + w]
    c = (((xx // s) + (yy // s)) % 2).astype(np.float32)
    v = 0.80 + 0.12 * c
    return np.repeat(v[..., None], 3, axis=2)


def flatten_rgba(doc):
    rgb, a = composite(doc.layers, 0, 0, doc.w, doc.h)
    return np.concatenate([(rgb * 255 + 0.5).clip(0, 255), (a * 255 + 0.5).clip(0, 255)], 2).astype(np.uint8)


def to_u8(rgb, a=None):
    out = (rgb * 255 + 0.5).clip(0, 255).astype(np.uint8)
    if a is not None:
        out = np.concatenate([out, (a * 255 + 0.5).clip(0, 255).astype(np.uint8)], 2)
    return out


# ------------------------------------------------------------------------------------------- brushes
_dab_cache = {}


def dab(size, hardness, hard_edge=False):
    """Round brush tip as float32 (d x d). hardness 0..1 (1 = crisp), hard_edge = pencil (no anti-aliasing)."""
    size = max(1.0, float(size)); key = (round(size, 1), round(hardness, 2), hard_edge)
    k = _dab_cache.get(key)
    if k is not None: return k
    r = size / 2.0; d = int(math.ceil(size)) + 2
    c = (d - 1) / 2.0
    yy, xx = np.mgrid[0:d, 0:d].astype(np.float32)
    dist = np.sqrt((xx - c) ** 2 + (yy - c) ** 2) / max(r, 0.5)
    if hard_edge:
        k = (dist <= 1.0).astype(np.float32)
    else:
        h = min(0.98, max(0.0, hardness))
        t = np.clip((dist - h) / max(1e-3, 1 - h), 0, 1)
        k = (1 - t * t * (3 - 2 * t)) * (dist <= 1.0 + 1.0 / max(r, 1))
        edge = np.clip((1.0 - dist) * r + 0.5, 0, 1)          # 1 px anti-aliased rim
        k = (k * edge).astype(np.float32)
    if len(_dab_cache) > 200: _dab_cache.clear()
    _dab_cache[key] = k
    return k


def dab_box(cx, cy, k, w, h):
    d = k.shape[0]; x0 = int(round(cx - d / 2.0)); y0 = int(round(cy - d / 2.0))
    X0, Y0, X1, Y1 = max(0, x0), max(0, y0), min(w, x0 + d), min(h, y0 + d)
    if X1 <= X0 or Y1 <= Y0: return None
    return X0, Y0, X1, Y1, k[Y0 - y0:Y1 - y0, X0 - x0:X1 - x0]


class Stroke:
    """One brush/pencil/eraser stroke. Coverage accumulates into a float buffer (flow) capped by opacity, and the
    layer is recomputed from its pre-stroke pixels, so strokes stay smooth and never build up past the opacity."""
    def __init__(self, doc, target, mode, color, size, hardness, opacity, flow, spacing=0.12, pencil=False):
        self.doc = doc; self.L = doc.layer; self.target = target; self.mode = mode
        self.color = np.array(color[:3], np.float32) / 255.0
        self.size = size; self.hard = hardness; self.op = opacity; self.flow = flow; self.pencil = pencil
        self.spacing = spacing; self.last = None; self.carry = 0.0
        src = self.L.px if target == "px" else self.L.mask
        self.before = src
        self.work = src.copy()
        if target == "px": self.L.px = self.work
        else: self.L.mask = self.work
        self.cov = np.zeros((doc.h, doc.w), np.float32)
        self.bbox = None

    def _grow(self, b):
        x0, y0, x1, y1 = b[:4]
        self.bbox = (x0, y0, x1, y1) if self.bbox is None else (min(self.bbox[0], x0), min(self.bbox[1], y0),
                                                                   max(self.bbox[2], x1), max(self.bbox[3], y1))

    def to(self, x, y, pressure=1.0):
        """Add dabs from the last point to (x, y). Returns the dirty rect (x0, y0, x1, y1) or None."""
        pts = []
        if self.last is None:
            pts.append((x, y, pressure))
        else:
            lx, ly, lp = self.last
            dist = math.hypot(x - lx, y - ly); step = max(0.6, self.size * self.spacing)
            if self.pencil: step = max(0.5, min(step, 1.0))
            t = self.carry
            while t + step <= dist:
                t += step; f = t / dist
                pts.append((lx + (x - lx) * f, ly + (y - ly) * f, lp + (pressure - lp) * f))
            self.carry = t - dist if dist > 0 else self.carry
            if not pts: self.last = (lx, ly, lp); self.carry = t - dist + 0 if dist > 0 else 0; return None
        self.last = (x, y, pressure)
        self.carry = 0.0
        dirty = None
        for px, py, pr in pts:
            sz = max(1.0, self.size * (0.25 + 0.75 * pr) if pr < 1 else self.size)
            k = dab(sz, self.hard, self.pencil)
            b = dab_box(px, py, k, self.doc.w, self.doc.h)
            if b is None: continue
            X0, Y0, X1, Y1, kk = b
            c = self.cov[Y0:Y1, X0:X1]
            fl = 1.0 if self.pencil else self.flow * (pr if pr < 1 else 1.0)
            cap = self.op
            self.cov[Y0:Y1, X0:X1] = np.maximum(c, np.minimum(cap, c + kk * fl * (cap - c) / max(cap, 1e-3) * cap + 0 * c)) \
                if False else np.minimum(cap, c + kk * fl * np.maximum(0, cap - c))
            dirty = (X0, Y0, X1, Y1) if dirty is None else (min(dirty[0], X0), min(dirty[1], Y0), max(dirty[2], X1), max(dirty[3], Y1))
        if dirty is None: return None
        self._grow(dirty); self._apply(dirty)
        return dirty

    def _apply(self, r):
        x0, y0, x1, y1 = r
        s = self.cov[y0:y1, x0:x1]
        if self.doc.sel is not None: s = s * (self.doc.sel[y0:y1, x0:x1].astype(np.float32) / 255.0)
        if self.target == "mask":
            b = self.before[y0:y1, x0:x1].astype(np.float32)
            v = 255.0 if self.mode == "erase" else float(np.dot(self.color, [0.299, 0.587, 0.114]) * 255)
            self.work[y0:y1, x0:x1] = (b + (v - b) * s + 0.5).clip(0, 255).astype(np.uint8)
            return
        b = self.before[y0:y1, x0:x1].astype(np.float32) / 255.0
        a = b[..., 3:4]; S = s[..., None]
        if self.mode == "erase":
            na = a * (1 - S); out = np.concatenate([b[..., :3], na], 2)
        else:
            na = a + S * (1 - a)
            rgb = (b[..., :3] * a * (1 - S) + self.color * S) / np.maximum(na, 1e-6)
            out = np.concatenate([rgb, na], 2)
        self.work[y0:y1, x0:x1] = (out * 255 + 0.5).clip(0, 255).astype(np.uint8)


class FxStroke:
    """Smudge / blur / sharpen / clone brushes (work straight on the layer copy)."""
    def __init__(self, doc, mode, size, hardness, strength, clone_off=None):
        self.doc = doc; self.L = doc.layer; self.mode = mode; self.size = size; self.hard = hardness
        self.st = strength; self.before = self.L.px; self.work = self.L.px.copy(); self.L.px = self.work
        self.last = None; self.carry = None; self.off = clone_off; self.bbox = None
        if mode in ("blur", "sharpen"):
            src = self.before.astype(np.float32)
            sig = max(1.0, size / 10.0)
            self.blur = cv2.GaussianBlur(src, (0, 0), sig) if False else None
            self.sig = sig

    def to(self, x, y, pressure=1.0):
        pts = []
        if self.last is None: pts = [(x, y)]
        else:
            lx, ly = self.last; dist = math.hypot(x - lx, y - ly); step = max(1.0, self.size * 0.15)
            n = int(dist // step)
            pts = [(lx + (x - lx) * (i + 1) / max(n, 1), ly + (y - ly) * (i + 1) / max(n, 1)) for i in range(n)]
            if not pts: return None
        dirty = None
        for px, py in pts:
            k = dab(self.size, self.hard); b = dab_box(px, py, k, self.doc.w, self.doc.h)
            if b is None: continue
            X0, Y0, X1, Y1, kk = b
            m = kk * self.st * (pressure if pressure < 1 else 1.0)
            if self.doc.sel is not None: m = m * (self.doc.sel[Y0:Y1, X0:X1].astype(np.float32) / 255.0)
            m = m[..., None]
            cur = self.work[Y0:Y1, X0:X1].astype(np.float32)
            if self.mode == "smudge":
                if self.carry is None or self.carry.shape != cur.shape: self.carry = cur.copy()
                new = cur + (self.carry - cur) * m
                self.carry = self.carry * 0.25 + new * 0.75 if False else new * 0.85 + self.carry * 0.15
            elif self.mode in ("blur", "sharpen"):
                pad = int(self.sig * 3) + 1
                a0, b0, a1, b1 = max(0, X0 - pad), max(0, Y0 - pad), min(self.doc.w, X1 + pad), min(self.doc.h, Y1 + pad)
                reg = self.work[b0:b1, a0:a1].astype(np.float32)
                bl = cv2.GaussianBlur(reg, (0, 0), self.sig)[Y0 - b0:Y1 - b0, X0 - a0:X1 - a0]
                tgt = bl if self.mode == "blur" else np.clip(cur + (cur - bl) * 1.2, 0, 255)
                new = cur + (tgt - cur) * m
            else:   # clone stamp
                ox, oy = self.off
                sx0, sy0 = X0 + ox, Y0 + oy
                if sx0 < 0 or sy0 < 0 or sx0 + (X1 - X0) > self.doc.w or sy0 + (Y1 - Y0) > self.doc.h: continue
                src = self.before[sy0:sy0 + (Y1 - Y0), sx0:sx0 + (X1 - X0)].astype(np.float32)
                new = cur + (src - cur) * m
            self.work[Y0:Y1, X0:X1] = (new + 0.5).clip(0, 255).astype(np.uint8)
            dirty = (X0, Y0, X1, Y1) if dirty is None else (min(dirty[0], X0), min(dirty[1], Y0), max(dirty[2], X1), max(dirty[3], Y1))
        self.last = (x, y)
        if dirty:
            self.bbox = dirty if self.bbox is None else (min(self.bbox[0], dirty[0]), min(self.bbox[1], dirty[1]),
                                                         max(self.bbox[2], dirty[2]), max(self.bbox[3], dirty[3]))
        return dirty


# ------------------------------------------------------------------------------------------- selections
def sel_shape(w, h, kind, pts, feather=0):
    im = Image.new("L", (w, h), 0); d = ImageDraw.Draw(im)
    if kind == "rect":
        (x0, y0), (x1, y1) = pts; d.rectangle([min(x0, x1), min(y0, y1), max(x0, x1) - 1, max(y0, y1) - 1], fill=255)
    elif kind == "ellipse":
        (x0, y0), (x1, y1) = pts
        S = 4; box = [min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1)]
        bw, bh = int(box[2] - box[0]) + 2, int(box[3] - box[1]) + 2
        if bw > 2 and bh > 2:
            big = Image.new("L", (bw * S, bh * S), 0)
            ImageDraw.Draw(big).ellipse([0, 0, (box[2] - box[0]) * S, (box[3] - box[1]) * S], fill=255)
            im.paste(big.resize((bw, bh), Image.LANCZOS), (int(box[0]), int(box[1])))
    else:
        if len(pts) >= 3: d.polygon([tuple(p) for p in pts], fill=255)
    m = np.asarray(im).copy()
    if feather > 0: m = cv2.GaussianBlur(m, (0, 0), feather)
    return m


def combine_sel(old, new, op):
    if old is None or op == "new": return new
    o = old.astype(np.int32); n = new.astype(np.int32)
    if op == "add": r = np.maximum(o, n)
    elif op == "sub": r = np.minimum(o, 255 - n)
    else: r = np.minimum(o, n)
    return r.astype(np.uint8)


def sel_bbox(sel):
    ys, xs = np.nonzero(sel > 8)
    if len(xs) == 0: return None
    return int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1


def sel_contours(sel, scale=1.0):
    m = (sel > 127).astype(np.uint8)
    if scale < 1.0:
        m = cv2.resize(m, (max(1, int(m.shape[1] * scale)), max(1, int(m.shape[0] * scale))), interpolation=cv2.INTER_NEAREST)
    cs, _ = cv2.findContours(m, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
    return [(c[:, 0, :].astype(np.float32) / scale) for c in cs if len(c) >= 2]


def flood_mask(img, x, y, tol, contiguous=True):
    """img HxWx4 uint8; returns uint8 mask of pixels within tol (0..255) of the seed."""
    h, w = img.shape[:2]
    if not (0 <= x < w and 0 <= y < h): return None
    if contiguous:
        src = np.ascontiguousarray(img[..., :4])
        m = np.zeros((h + 2, w + 2), np.uint8)
        im3 = np.ascontiguousarray(src[..., :3]); t = int(tol)
        # alpha participates: transparent pixels compare their alpha as well
        al = src[..., 3]
        if (al < 255).any():
            im3 = np.ascontiguousarray(np.concatenate([src[..., :3] // 2 + (255 - al[..., None]) // 2], 2).astype(np.uint8))
        cv2.floodFill(im3.copy(), m, (int(x), int(y)), 0, (t, t, t), (t, t, t), 4 | cv2.FLOODFILL_MASK_ONLY | (255 << 8) | cv2.FLOODFILL_FIXED_RANGE)
        return m[1:-1, 1:-1]
    seed = img[int(y), int(x)].astype(np.int32)
    d = np.abs(img.astype(np.int32) - seed).max(axis=2)
    return np.where(d <= tol, 255, 0).astype(np.uint8)


# ------------------------------------------------------------------------------------------- pixel ops
def paint_where(px, mask_f, color, opacity=1.0):
    """composite a flat colour over px using a float mask 0..1"""
    b = px.astype(np.float32) / 255.0; a = b[..., 3:4]; S = (mask_f * opacity)[..., None]
    col = np.array(color[:3], np.float32) / 255.0
    na = a + S * (1 - a); rgb = (b[..., :3] * a * (1 - S) + col * S) / np.maximum(na, 1e-6)
    return to_u8(rgb, na)


def over(px, src_rgba, mask_f=None):
    b = px.astype(np.float32) / 255.0; s = src_rgba.astype(np.float32) / 255.0
    sa = s[..., 3:4] * (mask_f[..., None] if mask_f is not None else 1.0); a = b[..., 3:4]
    na = sa + a * (1 - sa); rgb = (s[..., :3] * sa + b[..., :3] * a * (1 - sa)) / np.maximum(na, 1e-6)
    return to_u8(rgb, na)


def gradient(w, h, p0, p1, c0, c1, kind="linear"):
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    dx, dy = p1[0] - p0[0], p1[1] - p0[1]; L2 = dx * dx + dy * dy
    if L2 < 1: L2 = 1
    if kind == "radial":
        t = np.sqrt((xx - p0[0]) ** 2 + (yy - p0[1]) ** 2) / math.sqrt(L2)
    else:
        t = ((xx - p0[0]) * dx + (yy - p0[1]) * dy) / L2
    t = np.clip(t, 0, 1)[..., None]
    c0 = np.array(c0, np.float32); c1 = np.array(c1, np.float32)
    out = c0 + (c1 - c0) * t
    out += (np.random.default_rng(1).random(out.shape[:2], dtype=np.float32)[..., None] - 0.5) * 1.0   # dither (no banding)
    return out.clip(0, 255).astype(np.uint8)


def draw_shape(w, h, kind, p0, p1, color, stroke=0, fill=True, aa=4):
    """returns an RGBA uint8 layer patch (full size) with an anti-aliased shape"""
    x0, y0 = p0; x1, y1 = p1
    bx0 = int(max(0, min(x0, x1) - stroke - 3)); by0 = int(max(0, min(y0, y1) - stroke - 3))
    bx1 = int(min(w, max(x0, x1) + stroke + 3)); by1 = int(min(h, max(y0, y1) + stroke + 3))
    out = np.zeros((h, w, 4), np.uint8)
    if bx1 <= bx0 or by1 <= by0: return out
    S = aa; bw, bh = bx1 - bx0, by1 - by0
    im = Image.new("L", (bw * S, bh * S), 0); d = ImageDraw.Draw(im)
    q = lambda x, y: ((x - bx0) * S, (y - by0) * S)
    box = [q(min(x0, x1), min(y0, y1)), q(max(x0, x1), max(y0, y1))]
    sw = max(1, int(round(stroke * S)))
    if kind == "line":
        d.line([q(x0, y0), q(x1, y1)], fill=255, width=max(S, int(max(1, stroke) * S)))
        r = max(S, int(max(1, stroke) * S)) / 2
        for (xa, ya) in (q(x0, y0), q(x1, y1)): d.ellipse([xa - r, ya - r, xa + r, ya + r], fill=255)
    elif kind == "rect":
        if fill: d.rectangle(box, fill=255)
        else: d.rectangle(box, outline=255, width=sw)
    else:
        if fill: d.ellipse(box, fill=255)
        else: d.ellipse(box, outline=255, width=sw)
    m = np.asarray(im.resize((bw, bh), Image.LANCZOS))
    out[by0:by1, bx0:bx1, :3] = color[:3]; out[by0:by1, bx0:bx1, 3] = m
    return out


def system_fonts():
    import os, glob
    dirs = []
    if os.name == "nt":
        dirs += [os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts"),
                 os.path.join(os.environ.get("LOCALAPPDATA", ""), "Microsoft", "Windows", "Fonts")]
    else:
        dirs += ["/usr/share/fonts", os.path.expanduser("~/.fonts"), "/usr/local/share/fonts"]
    found = {}
    for d in dirs:
        for f in glob.glob(os.path.join(d, "**", "*.[tToO][tT][fF]"), recursive=True)[:3000]:
            try:
                fn = ImageFont.truetype(f, 12); fam, sty = fn.getname()
            except Exception:
                continue
            key = fam if sty in ("Regular", "Normal", "Book", "Roman") else "%s %s" % (fam, sty)
            found.setdefault(key, f)
    return dict(sorted(found.items(), key=lambda t: t[0].lower()))


def render_text(w, h, t):
    """t: dict(text, font_path, size, color, x, y, align) -> RGBA layer (full size)"""
    fnt = None
    for cand in (t.get("font_path"), "segoeui.ttf", "arial.ttf", "DejaVuSans.ttf"):
        try: fnt = ImageFont.truetype(cand, int(t["size"])); break
        except Exception: pass
    if fnt is None:
        try: fnt = ImageFont.load_default(size=int(t["size"]))
        except Exception: fnt = ImageFont.load_default()
    im = Image.new("RGBA", (w, h), tuple(t["color"][:3]) + (0,)); d = ImageDraw.Draw(im)
    d.multiline_text((t["x"], t["y"]), t["text"], font=fnt, fill=tuple(t["color"][:3]) + (255,),
                     align=t.get("align", "left"), spacing=int(t["size"] * 0.2))
    return np.asarray(im).copy()


# ------------------------------------------------------------------------------------------- adjustments / filters
def _rgb(px): return px[..., :3].astype(np.float32) / 255.0


def adjust(px, kind, p):
    """returns new RGBA uint8 for an adjustment/filter (alpha kept)."""
    a = px[..., 3:4]
    c = _rgb(px)
    if kind == "brightness":
        c = c + p.get("brightness", 0) / 100.0
        k = math.tan((p.get("contrast", 0) / 100.0 * 0.95 + 1) * math.pi / 4)
        c = (c - 0.5) * k + 0.5
    elif kind == "huesat":
        hsv = cv2.cvtColor(np.clip(c, 0, 1), cv2.COLOR_RGB2HSV)
        hsv[..., 0] = (hsv[..., 0] + p.get("hue", 0)) % 360
        hsv[..., 1] = np.clip(hsv[..., 1] * (1 + p.get("saturation", 0) / 100.0), 0, 1)
        c = cv2.cvtColor(hsv, cv2.COLOR_HSV2RGB)
        l = p.get("lightness", 0) / 100.0
        c = c + (1 - c) * l if l > 0 else c * (1 + l)
    elif kind == "levels":
        ib, iw, g = p.get("in_black", 0) / 255.0, p.get("in_white", 255) / 255.0, max(0.1, p.get("gamma", 1.0))
        ob, ow = p.get("out_black", 0) / 255.0, p.get("out_white", 255) / 255.0
        c = np.clip((c - ib) / max(1e-3, iw - ib), 0, 1) ** (1.0 / g)
        c = ob + c * (ow - ob)
    elif kind == "invert":
        c = 1 - c
    elif kind == "grayscale":
        l = c @ np.array([0.2126, 0.7152, 0.0722], np.float32); c = np.repeat(l[..., None], 3, 2)
    elif kind == "blur":
        r = max(0.1, p.get("radius", 4))
        pm = px.astype(np.float32); pm[..., :3] *= pm[..., 3:4] / 255.0      # premultiplied (no dark fringes)
        bl = cv2.GaussianBlur(pm, (0, 0), r)
        al = bl[..., 3:4]; rgb = np.where(al > 0, bl[..., :3] / np.maximum(al, 1e-3) * 255.0, 0)
        return np.concatenate([rgb, al], 2).clip(0, 255).astype(np.uint8) if False else \
            np.concatenate([(rgb + 0.5).clip(0, 255), (al + 0.5).clip(0, 255)], 2).astype(np.uint8)
    elif kind == "sharpen":
        r = max(0.3, p.get("radius", 1.5)); amt = p.get("amount", 80) / 100.0
        bl = cv2.GaussianBlur(c, (0, 0), r); c = c + (c - bl) * amt
    elif kind == "noise":
        amt = p.get("amount", 8) / 100.0; rng = np.random.default_rng(p.get("seed", 7))
        if p.get("mono", True): n = rng.standard_normal(c.shape[:2], dtype=np.float32)[..., None]
        else: n = rng.standard_normal(c.shape, dtype=np.float32)
        c = c + n * amt * 0.5
    elif kind == "cinematic":
        c = cinematic(c, p.get("preset", "Teal & Orange"), p.get("strength", 100) / 100.0)
    return np.concatenate([(np.clip(c, 0, 1) * 255 + 0.5).astype(np.uint8), a], 2)


CINEMATIC = ["Teal & Orange", "Noir", "Warm Film", "Cold Night", "Faded Matte", "Bleach Bypass", "Golden Hour"]


def cinematic(c, name, s=1.0):
    l = (c @ np.array([0.2126, 0.7152, 0.0722], np.float32))[..., None]
    o = c.copy()
    if name == "Teal & Orange":
        sh = np.array([-0.05, 0.03, 0.08], np.float32); hi = np.array([0.08, 0.03, -0.06], np.float32)
        o = c + sh * (1 - l) ** 2 + hi * l ** 2; o = (o - 0.5) * 1.08 + 0.5
    elif name == "Noir":
        g = l.repeat(3, 2); o = np.clip((g - 0.5) * 1.35 + 0.48, 0, 1) ** 1.1
    elif name == "Warm Film":
        o = c * np.array([1.06, 1.0, 0.9], np.float32) + 0.02; o = o * 0.94 + 0.04; o = o + (o - l) * 0.1
    elif name == "Cold Night":
        o = c * np.array([0.86, 0.96, 1.12], np.float32); o = (o - 0.5) * 1.1 + 0.46
    elif name == "Faded Matte":
        o = 0.08 + c * 0.84; o = l + (o - l) * 0.8
    elif name == "Bleach Bypass":
        g = l.repeat(3, 2); ov = np.where(g < 0.5, 2 * c * g, 1 - 2 * (1 - c) * (1 - g)); o = c * 0.4 + ov * 0.6
        o = l + (o - l) * 0.6
    elif name == "Golden Hour":
        o = c * np.array([1.10, 1.02, 0.84], np.float32) + np.array([0.03, 0.01, 0.0], np.float32)
        o = (o - 0.5) * 1.04 + 0.5
    return c + (np.clip(o, 0, 1) - c) * s


def apply_in_sel(old, new, sel):
    if sel is None: return new
    m = sel.astype(np.float32)[..., None] / 255.0
    return (old.astype(np.float32) * (1 - m) + new.astype(np.float32) * m + 0.5).astype(np.uint8)


# ------------------------------------------------------------------------------------------- transforms
def affine(px, M, w, h, interp=cv2.INTER_LINEAR):
    pm = px.astype(np.float32); pm[..., :3] *= pm[..., 3:4] / 255.0
    out = cv2.warpAffine(pm, M, (w, h), flags=interp, borderMode=cv2.BORDER_CONSTANT, borderValue=(0, 0, 0, 0))
    al = out[..., 3:4]; rgb = np.where(al > 0.5, out[..., :3] / np.maximum(al, 1e-3) * 255.0, 0)
    return np.concatenate([(rgb + 0.5).clip(0, 255), (al + 0.5).clip(0, 255)], 2).astype(np.uint8)


def content_bbox(px):
    a = px[..., 3]
    ys, xs = np.nonzero(a > 0)
    if len(xs) == 0: return None
    return int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1


def tf_matrix(cx, cy, sx, sy, ang, tx, ty):
    """scale (sx, sy) and rotate (deg) about the centre (cx, cy), then translate."""
    a = math.radians(ang); ca, sa = math.cos(a), math.sin(a)
    M = np.array([[ca * sx, -sa * sy, 0], [sa * sx, ca * sy, 0]], np.float64)
    M[:, 2] = np.array([cx + tx, cy + ty]) - M[:, :2] @ np.array([cx, cy])
    return M


# ------------------------------------------------------------------------------------------- files
def open_image(path):
    im = Image.open(path)
    try: im.seek(0)
    except Exception: pass
    im = ImageOps.exif_transpose(im).convert("RGBA")
    if max(im.size) > MAX_SIDE: im.thumbnail((MAX_SIDE, MAX_SIDE), Image.LANCZOS)
    return np.asarray(im).copy()


def save_exart(doc, path):
    meta = {"app": "EX CREATOR STUDIO / EX ART", "format": 1, "w": doc.w, "h": doc.h, "active": doc.active, "layers": []}
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        for i, l in enumerate(doc.layers):
            b = io.BytesIO(); Image.fromarray(l.px, "RGBA").save(b, "PNG", compress_level=3)
            z.writestr("layer_%03d.png" % i, b.getvalue())
            ent = {"name": l.name, "file": "layer_%03d.png" % i, "visible": l.visible, "locked": l.locked,
                   "opacity": l.opacity, "blend": l.blend, "text": l.text}
            if l.mask is not None:
                b = io.BytesIO(); Image.fromarray(l.mask, "L").save(b, "PNG"); z.writestr("mask_%03d.png" % i, b.getvalue())
                ent["mask"] = "mask_%03d.png" % i
            meta["layers"].append(ent)
        b = io.BytesIO(); im = Image.fromarray(flatten_rgba(doc), "RGBA"); im.thumbnail((512, 512)); im.save(b, "PNG")
        z.writestr("preview.png", b.getvalue())
        z.writestr("document.json", json.dumps(meta, indent=1))
    doc.path = path; doc.dirty = False


def load_exart(path):
    with zipfile.ZipFile(path) as z:
        meta = json.loads(z.read("document.json"))
        d = Doc(meta["w"], meta["h"]); d.layers = []
        for ent in meta["layers"]:
            px = np.asarray(Image.open(io.BytesIO(z.read(ent["file"]))).convert("RGBA")).copy()
            mk = np.asarray(Image.open(io.BytesIO(z.read(ent["mask"]))).convert("L")).copy() if ent.get("mask") else None
            d.layers.append(Layer(ent["name"], px, mask=mk, visible=ent.get("visible", True), locked=ent.get("locked", False),
                                  opacity=ent.get("opacity", 1.0), blend=ent.get("blend", "Normal"), text=ent.get("text")))
        d.active = min(meta.get("active", 0), len(d.layers) - 1); d.path = path
    return d


def export(doc, path, fmt, transparent=False, quality=95):
    rgba = flatten_rgba(doc)
    im = Image.fromarray(rgba, "RGBA")
    if fmt == "PNG" and transparent:
        im.save(path, "PNG", optimize=False)
    else:
        bg = Image.new("RGBA", im.size, (255, 255, 255, 255)); flat = Image.alpha_composite(bg, im).convert("RGB")
        if fmt == "JPG": flat.save(path, "JPEG", quality=quality, subsampling=0)
        else: flat.save(path, "PNG")


# ------------------------------------------------------------------------------------------- colour
def hex_to_rgb(s):
    s = s.strip().lstrip("#")
    if len(s) == 3: s = "".join(ch * 2 for ch in s)
    if len(s) != 6: raise ValueError(s)
    return tuple(int(s[i:i + 2], 16) for i in (0, 2, 4))


def rgb_to_hex(c): return "#%02X%02X%02X" % tuple(int(v) for v in c[:3])


def palette():
    """64 swatches: black, white, spectrum (2 rows), greys, skin tones, pastels, neons, earth tones."""
    out = [(0, 0, 0), (255, 255, 255)]                      # black and white first (easy to pick)
    for v, s in ((1.0, 1.0), (0.62, 1.0)):
        for i in range(12):
            r, g, b = colorsys.hsv_to_rgb(i / 12.0, s, v); out.append((int(r * 255), int(g * 255), int(b * 255)))
    out += [(int(255 * t), int(255 * t), int(255 * t)) for t in (.06, .13, .2, .3, .4, .5, .6, .7, .8, .88)]
    out += [(255, 224, 196), (241, 194, 160), (224, 172, 138), (198, 134, 96), (161, 102, 68), (122, 74, 48),
            (88, 54, 36), (60, 38, 26)]
    out += [(255, 179, 186), (255, 223, 186), (255, 255, 186), (186, 255, 201), (186, 225, 255), (213, 196, 255),
            (255, 196, 236), (196, 255, 244)]
    out += [(255, 0, 110), (0, 255, 159), (0, 184, 255), (190, 0, 255), (255, 240, 31), (255, 95, 31),
            (57, 255, 20), (255, 7, 58)]
    out += [(110, 76, 48), (150, 120, 80), (86, 98, 58), (180, 150, 110), (60, 70, 90), (140, 30, 40), (30, 60, 50), (200, 170, 60)]
    return out
