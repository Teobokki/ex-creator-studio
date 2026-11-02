"""EX CREATOR STUDIO - lip sync engine (face/mouth detection, audio envelope, mouth rendering, MP4 export).
(c)2026 EX Inc./HYPERION DIGITAL (c)2026"""
import hashlib, json, math, os, subprocess, sys, threading, time, wave
import numpy as np
import cv2

FPS = 30
NO_WINDOW = 0x08000000 if os.name == "nt" else 0


def res_path(*n):
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, *n)


# ----------------------------------------------------------------------------- detection
_CASCADES = {}


def _cascade(name):
    if name not in _CASCADES:
        c = cv2.CascadeClassifier()
        p = res_path("assets", name)
        # cv2 cannot open non-ASCII paths on Windows; read via memory-safe fallback
        ok = c.load(p)
        if not ok:
            try:
                fs = cv2.FileStorage(np.fromfile(p, np.uint8).tobytes().decode("utf-8", "ignore"),
                                     cv2.FILE_STORAGE_READ | cv2.FILE_STORAGE_MEMORY)
                ok = c.read(fs.getFirstTopLevelNode())
            except Exception:
                ok = False
        _CASCADES[name] = c if ok else None
    return _CASCADES[name]


def _lip_line(gray, cx, cy, mw, search):
    """Refine the mouth's vertical position to the darkest horizontal line near the estimate."""
    h, w = gray.shape
    x0, x1 = int(max(0, cx - mw * 0.35)), int(min(w, cx + mw * 0.35))
    y0, y1 = int(max(0, cy - search)), int(min(h, cy + search))
    if x1 - x0 < 3 or y1 - y0 < 5:
        return cy
    rows = gray[y0:y1, x0:x1].astype(np.float32).mean(axis=1)
    rows = np.convolve(rows, np.ones(3) / 3, mode="same")
    # prefer the estimate a little (penalise distance)
    dist = np.abs(np.arange(y0, y1) - cy) / max(1.0, search)
    score = rows + dist * (rows.max() - rows.min() + 1) * 0.35
    yy = int(np.argmin(score)) + y0
    if rows.max() - rows.min() < 12:  # flat area - no visible lip line
        return cy
    return yy


def _mouth_blob(small, face, kind):
    """Find the mouth as a dark/red blob inside the lower face (works for photos and drawings).
    Returns (cx, cy, w) in `small` coords or None."""
    x, y, fw, fh = face
    H, W = small.shape[:2]
    rx0, rx1 = int(max(0, x + fw * 0.22)), int(min(W, x + fw * 0.78))
    ry0, ry1 = int(max(0, y + fh * (0.60 if kind == "anime" else 0.50))), int(min(H, y + fh * 0.97))
    if rx1 - rx0 < 8 or ry1 - ry0 < 8:
        return None
    reg = small[ry0:ry1, rx0:rx1].astype(np.float32)
    gray = reg.mean(axis=2)
    red = reg[..., 0] - reg[..., 1]
    skin_g = np.median(gray); skin_r = np.median(red)
    score = (skin_g - gray) + 0.6 * np.maximum(0, red - skin_r)
    thr = max(22.0, np.percentile(score, 93))
    m = (score > thr).astype(np.uint8)
    m = cv2.morphologyEx(m, cv2.MORPH_CLOSE, np.ones((3, 5), np.uint8))
    masks = [m]
    if kind == "anime":  # drawn open mouth: a red/pink shape (dark hair can push the darkness threshold too high)
        m2 = ((red - skin_r > 35) & (gray < np.percentile(gray, 90) - 8)).astype(np.uint8)
        masks.append(cv2.morphologyEx(m2, cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8)))
    best, bs = None, 1e9
    for m in masks:
        best, bs = _blob_pick(m, rx0, ry0, fw, fh, y, kind, best, bs)
    return best


def _blob_pick(m, rx0, ry0, fw, fh, y, kind, best, bs):
    n, lab, st, cen = cv2.connectedComponentsWithStats(m, 8)
    rh, rw = m.shape
    exp_y = (fh * (0.775 if kind == "face" else 0.72)) - (ry0 - y)
    for i in range(1, n):
        bx, by, bw, bh, area = st[i]
        if area < max(6, rw * rh * 0.002) or bw < rw * 0.10:
            continue
        if bx <= 0 or bx + bw >= rw:  # touches the side: chin/jaw outline, hair, collar
            continue
        if bw < bh * (0.7 if kind == "anime" else 1.2) or bw > rw * 0.95:  # anime mouths are often drawn open (round)
            continue
        ccx, ccy = cen[i]
        # a twin blob mirrored across the face centre at the same height = a pair of eyes, not a mouth
        twin = False
        for j in range(1, n):
            if j != i and st[j][4] > area * 0.4 and st[j][4] < area * 2.5 and abs(cen[j][1] - ccy) < max(bh, 4) \
                    and abs((cen[j][0] + ccx) / 2 - rw / 2) < rw * 0.08 and abs(cen[j][0] - ccx) > bw:
                twin = True; break
        if twin:
            continue
        # a long thin dark curve low in the face is the chin/jaw line, not the mouth
        thin = 0.6 if (kind == "anime" and bh < max(3, bw * 0.12) and ccy > exp_y) else 0.0
        cost = thin + abs(ccx - rw / 2) / rw * 2.5 + abs(ccy - exp_y) / rh * 1.5 - min(1.0, area / (rw * rh * 0.05)) * 0.2
        if cost < bs:
            best, bs = (rx0 + bx + bw / 2.0, ry0 + by + bh * (0.4 if kind == "anime" else 0.5), float(bw)), cost
    return best, bs


_YUNET = [None, False]


def _yunet():
    """OpenCV YuNet face detector (5 landmarks incl. both mouth corners). Loaded from memory so
    non-ASCII install paths work."""
    if not _YUNET[1]:
        _YUNET[1] = True
        try:
            buf = np.fromfile(res_path("assets", "face_detection_yunet_2023mar.onnx"), np.uint8)
            _YUNET[0] = cv2.FaceDetectorYN.create("onnx", buf, np.array([], np.uint8), (320, 320), 0.8, 0.3, 50)
        except Exception:
            _YUNET[0] = None
    return _YUNET[0]


def _pad(img, frac):
    """Pad with the median border colour so faces cut off by the image edge can still be found."""
    h, w = img.shape[:2]
    p = int(max(h, w) * frac)
    if p < 2:
        return img, 0
    edge = np.concatenate([img[0], img[-1], img[:, 0], img[:, -1]]).reshape(-1, img.shape[2] if img.ndim == 3 else 1)
    col = [int(v) for v in np.median(edge, axis=0)]
    return cv2.copyMakeBorder(img, p, p, p, p, cv2.BORDER_CONSTANT, value=col if img.ndim == 3 else col[0]), p


def _yunet_faces(img):
    """All YuNet faces in an RGB image as 14-float landmark rows (box, eyes, nose, mouth corners) + score."""
    det = _yunet()
    if det is None:
        return []
    h, w = img.shape[:2]
    k = min(1.0, 640.0 / max(h, w))
    q = cv2.resize(img, (max(1, int(w * k)), max(1, int(h * k))), interpolation=cv2.INTER_AREA) if k < 1 else img
    try:
        det.setInputSize((q.shape[1], q.shape[0]))
        _, faces = det.detect(cv2.cvtColor(np.ascontiguousarray(q), cv2.COLOR_RGB2BGR))
    except Exception:
        faces = None
    out = []
    for f in faces if faces is not None else []:
        f = f.astype(np.float64)
        v = f[:14] / k
        out.append((v, float(f[14])))
    return out


def _roll(v):
    """Head roll (degrees, clockwise on screen = positive) from the eye line, steadied by the mouth corners."""
    (ex1, ey1), (ex2, ey2), (lx, ly), (rx, ry) = v[4:6], v[6:8], v[10:12], v[12:14]
    if ex1 > ex2:
        ex1, ey1, ex2, ey2 = ex2, ey2, ex1, ey1
    if lx > rx:
        lx, ly, rx, ry = rx, ry, lx, ly
    a_eye = math.degrees(math.atan2(ey2 - ey1, ex2 - ex1))
    a_mouth = math.degrees(math.atan2(ry - ly, rx - lx))
    return a_eye if abs(a_eye - a_mouth) > 20 else (a_eye * 0.6 + a_mouth * 0.4)


def _detect_yunet(small):
    """Best face -> (face_rect, mouth centre, corner width, head roll angle in degrees)."""
    best = None
    for frac in (0.0, 0.25):
        img, p = _pad(small, frac)
        for v, sc in _yunet_faces(img):
            v = v.copy(); v[0:13:2] -= p; v[1:14:2] -= p
            if v[2] < 12 or v[3] < 12:
                continue
            if best is None or sc * v[2] * v[3] > best[0]:
                best = (sc * v[2] * v[3], v)
        if best is not None:
            break
    if best is None:
        return None
    v = best[1]
    # refine: straighten the head and enlarge/shrink the face to ~200 px, then run the landmarks again.
    # Tilted and small faces get much more accurate eyes and mouth corners this way.
    for _ in range(2):
        fx, fy, fw, fh = v[:4]
        fcx, fcy = fx + fw / 2.0, fy + fh / 2.0
        ang0 = _roll(v)
        up = float(np.clip(200.0 / max(fw, fh, 1.0), 0.25, 8.0))
        S = int(max(fw, fh) * 3.0 * up)
        M = cv2.getRotationMatrix2D((fcx, fcy), ang0, up)
        M[0, 2] += S / 2.0 - fcx; M[1, 2] += S / 2.0 - fcy
        patch = cv2.warpAffine(small, M, (S, S), flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REPLICATE)
        Mi = cv2.invertAffineTransform(M)
        cands = []
        for v2, sc in _yunet_faces(patch):
            c = (v2[0] + v2[2] / 2 - S / 2.0, v2[1] + v2[3] / 2 - S / 2.0)
            if math.hypot(*c) < S * 0.2:
                cands.append((math.hypot(*c), v2))
        if not cands:
            break
        v2 = min(cands, key=lambda c: c[0])[1].copy()
        pts = np.stack([v2[4:14:2], v2[5:14:2], np.ones(5)], 1) @ Mi.T  # landmarks back to picture coords
        nv = v.copy()
        nv[4:14:2], nv[5:14:2] = pts[:, 0], pts[:, 1]
        cen = Mi @ np.array([v2[0] + v2[2] / 2, v2[1] + v2[3] / 2, 1.0])
        nv[2], nv[3] = v2[2] / up, v2[3] / up
        nv[0], nv[1] = cen[0] - nv[2] / 2, cen[1] - nv[3] / 2
        # only trust the second look if it agrees with the first (same mouth, similar size)
        def mouth_of(q):
            return (q[10] + q[12]) / 2, (q[11] + q[13]) / 2, math.hypot(q[12] - q[10], q[13] - q[11])
        (ax_, ay_, aw), (bx_, by_, bw) = mouth_of(v), mouth_of(nv)
        if math.hypot(bx_ - ax_, by_ - ay_) > max(aw, fw * 0.25) * 0.45 or not (0.7 < bw / max(aw, 1e-3) < 1.45):
            break
        v = nv
        if abs(_roll(v) - ang0) < 3:
            break
    fx, fy, fw, fh = v[:4]
    (lx, ly), (rx, ry) = v[10:12], v[12:14]
    (ex1, ey1), (ex2, ey2) = v[4:6], v[6:8]
    if ex1 > ex2:
        ex1, ey1, ex2, ey2 = ex2, ey2, ex1, ey1
    if lx > rx:
        lx, ly, rx, ry = rx, ry, lx, ly
    cx, cy = (lx + rx) / 2, (ly + ry) / 2
    cw = math.hypot(rx - lx, ry - ly)
    eye_d = math.hypot(ex2 - ex1, ey2 - ey1)
    ang = _roll(v)
    # sanity: corners must be a plausible mouth for this face, otherwise use face proportions
    r = math.radians(ang)
    rel = ((cx - (fx + fw / 2)) * -math.sin(r) + (cy - (fy + fh / 2)) * math.cos(r)) / max(fh, 1) + 0.5
    if not (eye_d * 0.45 < cw < eye_d * 1.6) or not (0.4 < rel < 1.1):
        cx, cy, cw = fx + fw / 2, fy + fh * 0.78, fw * 0.38
    return (fx, fy, fw, fh), (cx, cy), cw, ang


def _lip_gap(gray, cx, cy, cw, ang):
    """Snap the mouth centre to the gap between the lips. Works in a de-rotated, enlarged strip under/over the
    corner line: picks the dark run (closed-lip line or open mouth) nearest the corner line, searching further
    down than up - landmark corners sit high on open mouths and on heads tilted forward. Returns new (cx, cy)."""
    U = max(1.0, 80.0 / max(cw, 1.0))
    Wd, Hd = int(cw * 0.6 * U), int(cw * 1.0 * U)
    top = cw * 0.25  # strip spans cy - 0.25 cw .. cy + 0.75 cw (in mouth-aligned coords)
    if Wd < 6 or Hd < 10:
        return cx, cy
    M = cv2.getRotationMatrix2D((cx, cy), ang, U)
    M[0, 2] += Wd / 2.0 - cx; M[1, 2] += top * U - cy
    strip = cv2.warpAffine(gray, M, (Wd, Hd), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE).astype(np.float32)
    prof = strip[:, int(Wd * 0.2):int(Wd * 0.8) + 1].mean(axis=1)
    prof = cv2.GaussianBlur(prof.reshape(-1, 1), (1, 0), max(0.6, U * 0.6)).ravel() if len(prof) > 3 else prof
    ref, lo = float(np.percentile(prof, 75)), float(prof.min())
    if ref - lo < 14:
        return cx, cy  # no visible gap
    thr = ref - max(10.0, 0.4 * (ref - lo))
    dark = prof < thr
    runs, i = [], 0
    while i < len(dark):
        if dark[i]:
            j = i
            while j + 1 < len(dark) and dark[j + 1]:
                j += 1
            runs.append((i, j)); i = j + 1
        else:
            i += 1
    if not runs:
        return cx, cy
    c0 = top * U
    def cost(r):
        a, b = r
        d = (a - c0) / (cw * U)
        depth = (thr - prof[a:b + 1].min()) / max(1.0, ref - lo)
        return (abs(d) * (2.5 if d < 0 else 1.2)) - depth * 0.35 + (5 if a <= 0 or d > 0.55 else 0)
    a, b = min(runs, key=cost)
    if cost((a, b)) >= 5:
        return cx, cy
    yy = (a + b) / 2.0  # closed: the line between the lips; open: the middle of the opening
    dy = yy / U - top
    r = math.radians(ang)  # back to image coords along the face's vertical axis
    return cx - dy * math.sin(r), cy + dy * math.cos(r)


def _fill_holes(m):
    cs, _ = cv2.findContours(m, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    out = np.zeros_like(m)
    cv2.drawContours(out, cs, -1, 1, -1)
    return out


def drawn_face(rgb):
    """Drawn faces (stickman, emoji, simple cartoons): two eye blobs side by side and the mouth mark under
    them. Returns (face_rect, (cx, cy, mw, mh)) in rgb coords or None."""
    h, w = rgb.shape[:2]
    s = min(1.0, 600.0 / max(h, w))
    sm = cv2.resize(rgb, (max(1, int(w * s)), max(1, int(h * s))), interpolation=cv2.INTER_AREA) if s < 1 else rgb
    g = cv2.cvtColor(sm, cv2.COLOR_RGB2GRAY)
    sh, sw = g.shape
    A = float(sh * sw)
    k = int(min(sh, sw) * 0.2) | 1
    bg = cv2.medianBlur(g, max(5, min(k, 255)))
    gf, bf = g.astype(np.float32), bg.astype(np.float32)
    ink = (((bf - gf) > 40) | ((gf < 70) & (bf > 100))).astype(np.uint8)
    ink = _fill_holes(ink)
    n, lab, st, cen = cv2.connectedComponentsWithStats(ink, 8)
    eyes = []
    for i in range(1, n):
        bx, by, bw, bh, ar = st[i]
        if ar < A * 0.00012 or ar > A * 0.05 or not (0.3 < bw / float(bh) < 2.2) or ar / float(bw * bh) < 0.55:
            continue
        eyes.append((i, cen[i][0], cen[i][1], bw, bh, ar))
    eyes = sorted(eyes, key=lambda e: -e[5])[:40]
    best = None
    for a in range(len(eyes)):
        for b in range(a + 1, len(eyes)):
            ea, eb = (eyes[a], eyes[b]) if eyes[a][1] < eyes[b][1] else (eyes[b], eyes[a])
            dx = eb[1] - ea[1]; dy = abs(eb[2] - ea[2])
            if dx < sw * 0.04 or dy > dx * 0.3 or max(ea[5], eb[5]) / float(min(ea[5], eb[5])) > 2.5:
                continue
            esz = (ea[3] + eb[3]) / 2.0
            if dx < esz * 1.4 or dx > esz * 9:
                continue
            mx, my = (ea[1] + eb[1]) / 2, (ea[2] + eb[2]) / 2
            mb, ms = None, 1e9
            for i in range(1, n):
                if i in (ea[0], eb[0]):
                    continue
                bx, by, bw, bh, ar = st[i]
                if bw < dx * 0.2 or bw > dx * 3.0 or bh > dx * 1.4 or bw < bh * 0.9 or bx <= 0 or bx + bw >= sw:
                    continue
                ccx, ccy = bx + bw / 2.0, by + bh / 2.0
                off = ccy - my
                if off < dx * 0.35 or off > dx * 1.7 or abs(ccx - mx) > dx * 0.35:
                    continue
                c = abs(ccx - mx) / dx * 2 + abs(off - dx * 0.8) / dx + abs(bw - dx * 0.75) / dx * 0.25 - min(ar / (dx * dx * 0.3), 1.0) * 0.3
                if c < ms:
                    mb, ms = (ccx, ccy, float(bw), float(bh)), c
            if mb is None:
                continue  # a pair of dots with nothing under them is not trusted as a face
            sc = ms + dy / dx
            if best is None or sc < best[0]:
                best = (sc, dx, mx, my, mb, math.degrees(math.atan2(eb[2] - ea[2], eb[1] - ea[1])))
    if best is None or best[0] > 1.6:
        return None
    sc, dx, mx, my, (cx, cy, mw, mh), ang = best
    inv = 1.0 / s
    face = (int((mx - dx * 1.1) * inv), int((my - dx * 1.0) * inv), int(dx * 2.2 * inv), int(dx * 2.3 * inv))
    return face, (cx * inv, cy * inv, max(mw, dx * 0.3) * inv, max(mh, dx * 0.12) * inv, float(ang)), float(sc)


def _detect_mouth(rgb):
    """Return (kind, face_rect, mouth, confidence) where kind is 'face', 'anime', 'drawn' or None.
    mouth = (cx, cy, mw, mh[, angle]) in image pixels: centre of the lips (the line between them), lip width
    (corner to corner) and lip height.
    Order: YuNet landmarks (photos, 3D renders, painted faces) -> anime face cascade + mouth search
    (anime/cartoon) -> drawn-face finder (stickman, emoji) -> classic face cascade."""
    h, w = rgb.shape[:2]
    s = min(1.0, 900.0 / max(h, w))
    small = cv2.resize(rgb, (max(1, int(w * s)), max(1, int(h * s))), interpolation=cv2.INTER_AREA) if s < 1 else rgb
    small = np.ascontiguousarray(small)
    gray = cv2.cvtColor(small, cv2.COLOR_RGB2GRAY)
    inv = 1.0 / s
    y = _detect_yunet(small)
    if y is not None:
        (fx, fy, fw, fh), (cx, cy), cw, ang = y
        cx, cy = _lip_gap(gray, cx, cy, cw, ang)
        mw = cw * 1.08
        a = _anime_mouth(small, (fx, fy, fw, fh))
        if a is not None:  # a drawn (anime) face: its mouth is a drawn shape, the photo landmarks only guess it
            cx, cy, mw = a
        face = tuple(int(v * inv) for v in (fx, fy, fw, fh))
        return "face", face, (cx * inv, cy * inv, mw * inv, mw * 0.42 * inv, float(ang)), 10.0
    pimg, p = _pad(small, 0.3)
    geq = cv2.equalizeHist(cv2.cvtColor(pimg, cv2.COLOR_RGB2GRAY))
    md = min(gray.shape)
    found = []
    for name, kind, kw in (("lbpcascade_animeface.xml", "anime", dict(scaleFactor=1.05, minNeighbors=5)),
                           ("haarcascade_frontalface_alt2.xml", "face", dict(scaleFactor=1.1, minNeighbors=8))):
        c = _cascade(name)
        if c is None:
            continue
        try:
            r = c.detectMultiScale3(geq, minSize=(max(24, int(md * 0.08)),) * 2, outputRejectLevels=True, **kw)
            faces, wts = r[0], r[2]
        except Exception:
            faces, wts = (), ()
        for (x, y_, fw, fh), wt in zip(faces, wts):
            found.append((kind, (x - p, y_ - p, fw, fh), float(np.ravel(wt)[0]) if np.size(wt) else 0.0))
        if found:
            break
    d = drawn_face(rgb)
    if found:
        kind, (x, y_, fw, fh), conf = max(found, key=lambda f: f[1][2] * f[1][3])
        # a drawn face (eyes + mouth mark) inside the cascade's face box is the most precise answer
        if d is not None:
            dcx, dcy = d[1][0] * s, d[1][1] * s
            if x <= dcx <= x + fw and y_ + fh * 0.4 <= dcy <= y_ + fh * 1.05:
                return kind, tuple(int(v * inv) for v in (x, y_, fw, fh)), d[1], max(conf, 3.0)
        if kind == "face":
            cx, cy, mw = x + fw * 0.5, y_ + fh * 0.775, fw * 0.36
        else:
            cx, cy, mw = x + fw * 0.5, y_ + fh * 0.74, fw * 0.22
        blob = _mouth_blob(small, (x, y_, fw, fh), kind)
        if blob is not None:
            cx, cy = blob[0], blob[1]
            mw = float(np.clip(blob[2] * 1.05, fw * (0.12 if kind == "anime" else 0.26), fw * 0.5))
        else:
            cy = _lip_line(gray, cx, cy, mw, fh * 0.07)
        return kind, tuple(int(v * inv) for v in (x, y_, fw, fh)), (cx * inv, cy * inv, mw * inv, mw * 0.36 * inv), conf
    if d is not None:
        return "drawn", d[0], d[1], float(np.clip(3.0 - d[2], 0.5, 3.0))
    return None, None, None, 0.0


def _anime_mouth(small, face):
    """If the photo face finder's face is really an anime/cartoon face (the anime face finder agrees strongly on the
    same spot), return the drawn mouth (cx, cy, w) found inside it, else None."""
    c = _cascade("lbpcascade_animeface.xml")
    if c is None:
        return None
    fx, fy, fw, fh = face
    pimg, p = _pad(small, 0.3)
    geq = cv2.equalizeHist(cv2.cvtColor(pimg, cv2.COLOR_RGB2GRAY))
    try:
        r = c.detectMultiScale3(geq, scaleFactor=1.05, minNeighbors=5, minSize=(max(24, int(min(small.shape[:2]) * 0.08)),) * 2,
                                outputRejectLevels=True)
    except Exception:
        return None
    best = None
    for (x, y, w, h), wt in zip(r[0], r[2]):
        x, y = x - p, y - p
        wt = float(np.ravel(wt)[0]) if np.size(wt) else 0.0
        ix = max(0, min(x + w, fx + fw) - max(x, fx)); iy = max(0, min(y + h, fy + fh) - max(y, fy))
        if wt >= 3.0 and ix * iy > 0.4 * min(w * h, fw * fh) and (best is None or wt > best[0]):
            best = (wt, (x, y, w, h))
    if best is None:
        return None
    x, y, w, h = best[1]
    b = _mouth_blob(small, (x, y, w, h), "anime")
    if b is None:
        return None
    bx, by, bw = b
    if not (fx <= bx <= fx + fw and fy + fh * 0.45 <= by <= fy + fh * 1.1):
        return None
    return bx, by, float(np.clip(bw * 1.05, w * 0.12, w * 0.5))


def _rotated(rgb, deg):
    """Rotate the whole picture by deg (counter-clockwise on screen) on an expanded canvas.
    Returns (image, 2x3 matrix original->rotated)."""
    h, w = rgb.shape[:2]
    M = cv2.getRotationMatrix2D((w / 2.0, h / 2.0), deg, 1.0)
    c, sn = abs(M[0, 0]), abs(M[0, 1])
    nw, nh = int(h * sn + w * c), int(h * c + w * sn)
    M[0, 2] += nw / 2.0 - w / 2.0; M[1, 2] += nh / 2.0 - h / 2.0
    edge = np.concatenate([rgb[0], rgb[-1], rgb[:, 0], rgb[:, -1]])
    col = [int(v) for v in np.median(edge, axis=0)]
    return cv2.warpAffine(rgb, M, (nw, nh), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT, borderValue=col), M


def mouth_angle(m):
    return float(m[4]) if m is not None and len(m) > 4 else 0.0


def scale_mouth(m, s, ox=0.0, oy=0.0):
    """Mouth spot scaled by s after shifting by (-ox, -oy); the head angle is kept."""
    if m is None:
        return None
    return ((m[0] - ox) * s, (m[1] - oy) * s, m[2] * s, m[3] * s, mouth_angle(m))


def detect_mouth(rgb):
    """detect_mouth(rgb) -> (kind, face_rect, mouth). mouth = (cx, cy, mw, mh, angle): angle is the head roll in
    degrees (positive = tilted clockwise on screen). Photo faces get their roll from the landmarks; for weak or
    missing results the picture is also tried rotated (+-25, +-45 degrees) so strongly tilted anime, cartoon and
    drawn heads are found upright. A mouth that would fall outside the picture is not used."""
    h, w = rgb.shape[:2]
    kind, face, m, conf = _detect_mouth(rgb)
    if conf < 2.5:
        small_s = min(1.0, 700.0 / max(h, w))
        sm = cv2.resize(rgb, (max(1, int(w * small_s)), max(1, int(h * small_s))), interpolation=cv2.INTER_AREA) if small_s < 1 else rgb
        best = None
        for deg in (25, -25, 45, -45):
            rimg, M = _rotated(sm, deg)
            k2, f2, m2, c2 = _detect_mouth(rimg)
            if m2 is None or (best is not None and c2 <= best[0]):
                continue
            best = (c2, deg, k2, f2, m2, M)
        if best is not None and (m is None or (best[0] > conf + 0.7 and best[0] >= 2.0)):
            c2, deg, k2, f2, m2, M = best
            Mi = cv2.invertAffineTransform(M)
            cx, cy = Mi @ np.array([m2[0], m2[1], 1.0])
            fcx, fcy = Mi @ np.array([f2[0] + f2[2] / 2.0, f2[1] + f2[3] / 2.0, 1.0])
            inv = 1.0 / small_s
            kind = k2
            face = (int((fcx - f2[2] / 2.0) * inv), int((fcy - f2[3] / 2.0) * inv), int(f2[2] * inv), int(f2[3] * inv))
            # rotating the picture counter-clockwise by deg straightened a head tilted clockwise by deg
            m = (cx * inv, cy * inv, m2[2] * inv, m2[3] * inv, mouth_angle(m2) + deg)
    if m is not None:
        cx, cy, mw, mh = (float(v) for v in m[:4])
        ang = float(np.clip(mouth_angle(m), -80, 80))
        if not (0 <= cx < w and 0 <= cy - mh * 0.3 and cy + mh * 0.3 < h) or mw < 4:
            return None, None, None
        m = (cx, cy, mw, mh, ang)
    return kind, face, m


# ----------------------------------------------------------------------------- remembered mouth spots
_MEM_LOCK = threading.Lock()


def _mem_path():
    base = os.environ.get("LOCALAPPDATA") or os.path.join(os.path.expanduser("~"), ".local", "share")
    d = os.path.join(base, "EX CREATOR STUDIO")
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, "mouth_spots.json")


def image_key(rgb):
    """Stable id of a picture (its pixels), so a corrected mouth spot comes back the next time it is opened."""
    h = hashlib.sha1(("%dx%d:" % (rgb.shape[1], rgb.shape[0])).encode())
    h.update(np.ascontiguousarray(rgb).tobytes())
    return h.hexdigest()


def _mem_load():
    try:
        with open(_mem_path(), "r", encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def recall_mouth(key):
    """-> (found, mouth or None). mouth in image pixels; found with None = user chose 'no lip sync'."""
    with _MEM_LOCK:
        e = _mem_load().get(key)
    if not isinstance(e, dict):
        return False, None
    m = e.get("m")
    if m is None:
        return True, None
    try:
        return True, tuple(float(v) for v in m[:5])
    except Exception:
        return False, None


def remember_mouth(key, mouth, forget=False):
    """Save the user's mouth spot for this picture (None = no lip sync); forget=True removes it."""
    with _MEM_LOCK:
        d = _mem_load()
        if forget:
            d.pop(key, None)
        else:
            d[key] = {"m": None if mouth is None else [round(float(v), 2) for v in mouth[:5]], "t": int(time.time())}
        if len(d) > 400:  # keep the newest
            for k in sorted(d, key=lambda k: d[k].get("t", 0) if isinstance(d[k], dict) else 0)[:len(d) - 400]:
                d.pop(k, None)
        try:
            tmp = _mem_path() + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(d, f)
            os.replace(tmp, _mem_path())
        except Exception:
            pass


# ----------------------------------------------------------------------------- audio
def load_wav(path):
    with wave.open(path, "rb") as wf:
        ch, sw, sr, n = wf.getnchannels(), wf.getsampwidth(), wf.getframerate(), wf.getnframes()
        raw = wf.readframes(n)
    if sw == 1:
        a = (np.frombuffer(raw, np.uint8).astype(np.float32) - 128) / 128
    elif sw == 2:
        a = np.frombuffer(raw, "<i2").astype(np.float32) / 32768
    elif sw == 3:
        b = np.frombuffer(raw, np.uint8).reshape(-1, 3)
        a = (b[:, 0].astype(np.int32) | (b[:, 1].astype(np.int32) << 8) | (b[:, 2].astype(np.int8).astype(np.int32) << 16)).astype(np.float32) / 8388608
    else:
        a = np.frombuffer(raw, "<i4").astype(np.float32) / 2147483648
    if ch > 1:
        a = a.reshape(-1, ch).mean(axis=1)
    return a, sr


def lipsync_track(samples, sr, fps=FPS, lead_s=0.04):
    """Per-video-frame mouth parameters from the voice: (openness 0..1, width factor ~0.8..1.2).
    Openness follows the loudness envelope (RMS, noise-gated, compressed, attack/release smoothed);
    width follows spectral brightness so 'ee/s' sounds widen and 'oo/o' sounds round the mouth."""
    n = max(1, int(math.ceil(len(samples) / sr * fps)) + 1)
    hop = sr / fps
    win = int(hop * 2)
    rms = np.zeros(n, np.float32)
    cen = np.zeros(n, np.float32)
    hann = np.hanning(max(8, win)).astype(np.float32)
    freqs = np.fft.rfftfreq(len(hann), 1.0 / sr)
    for i in range(n):
        c = int((i + lead_s * fps) * hop)
        seg = samples[max(0, c - win // 2): max(0, c - win // 2) + win]
        if len(seg) < 8:
            continue
        rms[i] = float(np.sqrt(np.mean(seg * seg)))
        if len(seg) == len(hann):
            sp = np.abs(np.fft.rfft(seg * hann))
            band = (freqs > 150) & (freqs < 6000)
            tot = sp[band].sum()
            if tot > 1e-6:
                cen[i] = float((sp[band] * freqs[band]).sum() / tot)
    if rms.max() <= 1e-5:
        return np.zeros(n, np.float32), np.ones(n, np.float32)
    ref = np.percentile(rms[rms > rms.max() * 0.05], 90) if np.any(rms > rms.max() * 0.05) else rms.max()
    x = np.clip(rms / max(ref, 1e-6), 0, 1.4)
    gate = 0.08
    x = np.clip((x - gate) / (1 - gate), 0, 1.25) ** 0.7
    x = np.clip(x, 0, 1)
    # articulation: take part of the slow "floor" out so the mouth closes between syllables, not only on pauses
    k = max(1, int(round(fps * 0.12)))
    pad = np.pad(x, k, mode="edge")
    floor = np.array([pad[i:i + 2 * k + 1].min() for i in range(len(x))], np.float32)
    x = np.clip(x - 0.55 * floor, 0, None)
    out = np.zeros_like(x)
    v = 0.0
    for i, t in enumerate(x):  # fast attack, quick release (so it can close between syllables)
        v += (t - v) * (0.75 if t > v else 0.55)
        out[i] = v
    # open wide: typical syllable peaks of normal speech reach ~0.92 (a big, clear opening), loud ones cap at 1
    pk = [out[i] for i in range(1, len(out) - 1) if out[i] >= out[i - 1] and out[i] > out[i + 1] and out[i] > 0.08]
    if pk:
        ref = float(np.percentile(pk, 60))
        if ref > 1e-3:
            out = out * (0.92 / ref)
    out = np.clip(out, 0, 1)
    out[out < 0.06] = 0.0  # shut on silence
    # brightness -> width: centroid ~ 500 Hz (oo) .. 3000 Hz (ee/s)
    b = np.clip((cen - 700) / 2300, 0, 1)
    wf = 0.82 + 0.36 * b
    wf = np.convolve(np.pad(wf, 1, mode="edge"), np.ones(3) / 3, mode="valid").astype(np.float32)
    wf[out < 0.05] = 1.0
    return out.astype(np.float32), wf


# ----------------------------------------------------------------------------- rendering
def _smooth(t):
    t = np.clip(t, 0, 1)
    return t * t * (3 - 2 * t)


def render_mouth(base, mouth, openness, width=1.0):
    """Mouth opened by `openness` (0..1) on a copy of base. A tilted head (mouth[4] = roll angle) is handled by
    straightening a patch around the mouth, opening the mouth along the face's own up/down axis, and
    rotating the changed pixels back."""
    ang = mouth_angle(mouth)
    if mouth is None or openness <= 0.02 or abs(ang) < 2.0:
        return _render_axis(base, mouth, openness, width)
    H, W = base.shape[:2]
    cx, cy, mw = float(mouth[0]), float(mouth[1]), max(4.0, float(mouth[2]))
    half = int(math.ceil(2.6 * mw)) + 2
    S = 2 * half
    M = cv2.getRotationMatrix2D((cx, cy), ang, 1.0)  # image -> straight patch
    M[0, 2] += half - cx; M[1, 2] += half - cy
    patch = cv2.warpAffine(base, M, (S, S), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)
    opened = _render_axis(patch, (half, half, mw, float(mouth[3])), openness, width)
    diff = (np.abs(opened.astype(np.int16) - patch.astype(np.int16)).max(axis=2) > 1).astype(np.uint8)
    if not diff.any():
        return base
    diff = cv2.dilate(diff, np.ones((3, 3), np.uint8))
    al = cv2.GaussianBlur(diff.astype(np.float32), (0, 0), max(0.8, mw * 0.03))
    # back into the picture, only over the patch's footprint
    r = int(math.ceil(half * 1.4143)) + 2
    x0, y0, x1, y1 = max(0, int(cx) - r), max(0, int(cy) - r), min(W, int(cx) + r + 1), min(H, int(cy) + r + 1)
    if x1 - x0 < 2 or y1 - y0 < 2:
        return base
    Mi = cv2.invertAffineTransform(M)
    Mi[0, 2] -= x0; Mi[1, 2] -= y0
    back = cv2.warpAffine(opened, Mi, (x1 - x0, y1 - y0), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT)
    a = cv2.warpAffine(al, Mi, (x1 - x0, y1 - y0), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT, borderValue=0)
    a = np.clip(a, 0, 1)[..., None]
    out = base.copy()
    reg = out[y0:y1, x0:x1].astype(np.float32)
    out[y0:y1, x0:x1] = np.clip(reg * (1 - a) + back.astype(np.float32) * a, 0, 255).astype(np.uint8)
    return out


def _lip_curve_r(gray, cx, cy, mw, xs, hw, rf):
    H = gray.shape[0]
    r = max(1.5, rf * mw)
    y0, y1 = int(max(0, math.floor(cy - r - 2))), int(min(H, math.ceil(cy + r + 3)))
    off = np.zeros(len(xs), np.float32)
    if y1 - y0 < 3:
        return off, 1e9
    g = gray[y0:y1].astype(np.float32)
    g = cv2.GaussianBlur(g, (0, 0), max(0.5, mw * 0.012), max(0.5, mw * 0.02))
    yy = np.arange(y0, y1, dtype=np.float32)
    span = float(g.max() - g.min()) + 1.0
    cost = g / span + 1.2 * ((yy - cy) / r)[:, None] ** 2  # dark, and near the detected lip line
    xi = np.clip(np.round(xs).astype(int), 0, gray.shape[1] - 1)
    c = cost[:, xi]
    j = np.argmin(c, axis=0)
    n = np.arange(len(xs))
    jm, jp = np.clip(j - 1, 0, len(yy) - 1), np.clip(j + 1, 0, len(yy) - 1)
    cm, c0, cp = c[jm, n], c[j, n], c[jp, n]
    den = cm - 2 * c0 + cp
    sub = np.where(np.abs(den) > 1e-6, 0.5 * (cm - cp) / np.where(np.abs(den) > 1e-6, den, 1), 0)
    off = (yy[j] + np.clip(sub, -0.5, 0.5) - cy).astype(np.float32)
    # a lip line is a smooth curve (flat, smile or frown): robust quadratic fit so dark specks, teeth gaps or
    # stubble cannot make it zig-zag
    t = (xs - cx) / max(1.0, hw)
    sel = np.abs(t) < 0.9
    if sel.sum() >= 5:
        keep = sel.copy()
        for _ in range(3):
            co = np.polyfit(t[keep], off[keep], 2)
            res = np.abs(np.polyval(co, t) - off)
            mad = float(np.median(res[keep])) + 0.5
            keep = sel & (res < 2.5 * mad)
            if keep.sum() < 5:
                break
        off = np.clip(np.polyval(co, np.clip(t, -1, 1)), -r, r).astype(np.float32)
    # how dark the image is along the fitted curve (lower = it really follows the seam between the lips)
    on = np.abs(t) < 0.8
    gy = np.clip(np.round(cy + off - y0).astype(int), 0, len(yy) - 1)
    dark = float(np.mean(g[gy[on], xi[on]] - g.min()) / span) if on.any() else 1e9
    return off, dark


def _lip_curve(gray, cx, cy, mw, xs, hw):
    """Per-column offset of the lip line (the dark seam between the lips) from cy across the mouth, in the
    straightened patch. Follows a flat, smiling or frowning line so no strip of lip is left on the wrong side."""
    off, d1 = _lip_curve_r(gray, cx, cy, mw, xs, hw, 0.10)
    off2, d2 = _lip_curve_r(gray, cx, cy, mw, xs, hw, 0.18)  # deep drawn smiles need a wider search
    if d2 < d1 - 0.08:
        off = off2
    u = np.abs(xs - cx) / max(1.0, hw)
    return off * (1 - _smooth((u - 1.0) / 0.3))  # ease back to the detected line beyond the corners


def _render_axis(base, mouth, openness, width=1.0):
    """Return a copy of base (HxWx3 uint8 RGB) with the mouth opened by `openness` (0..1).
    The real lips move apart: everything above the lip line (upper lip) is pushed up, everything below it (lower lip
    and jaw) is pushed down, with smooth falloff so nothing is copied or smeared. Only the gap that opens between the
    lips is filled, with a dark rounded mouth interior shaped like a lens that tapers into the real mouth corners."""
    if mouth is None or openness <= 0.02:
        return base
    H, W = base.shape[:2]
    cx, cy, mw, mh = [float(v) for v in mouth[:4]]
    mw = max(4.0, mw)
    openness = float(min(1.0, openness))
    # total opening at the centre; tiny values ease to nothing so a nearly closed mouth shows no thin slit line
    G = openness * float(_smooth((openness - 0.05) / 0.15)) * 0.36 * mw
    if G < 0.7:
        return base
    gap, lift = G * 0.66, G * 0.34    # lower lip/jaw drops more than the upper lip rises
    hw = 0.45 * mw * width            # half width of the opening: just inside the real corners
    x0, x1 = int(max(0, cx - 1.35 * mw)), int(min(W, cx + 1.35 * mw + 1))
    y0, y1 = int(max(0, cy - 0.75 * mw)), int(min(H, cy + 1.6 * mw + 1))
    if x1 - x0 < 4 or y1 - y0 < 4:
        return base
    ww, hh = x1 - x0, y1 - y0
    xs = np.arange(x0, x1, dtype=np.float32)
    gray = cv2.cvtColor(base, cv2.COLOR_RGB2GRAY)
    L = cy + _lip_curve(gray, cx, cy, mw, xs, hw)                     # lip line per column
    u = (xs - cx) / hw
    o = np.clip(1 - u * u, 0, None) ** 0.9                            # lens profile, tapers to the corners
    c = np.exp(-0.5 * ((xs - cx) / (0.5 * mw)) ** 2) * _smooth((1.3 * mw - np.abs(xs - cx)) / (0.3 * mw))
    c = np.maximum(c, o)                                              # chin/jaw moves as one piece
    # forward map (source row -> output row) on a fine grid, per column; monotonic by construction
    step = float(np.clip(mw / 160.0, 0.5, 2.0))
    sy = np.arange(y0 - 2, y1 + 2, step, dtype=np.float32)[:, None]   # (n,1)
    d = sy - L[None, :]                                               # distance below the lip line
    # lower lip (rigid ~0.2mw) then jaw falloff to ~1.5mw below
    lipw = 1 - _smooth((d - 0.12 * mw) / (0.35 * mw))
    amp_dn = o[None, :] * lipw + c[None, :] * (1 - lipw)
    f_dn = 1 - _smooth((d - 0.22 * mw) / (1.15 * mw))
    f_up = 1 - _smooth((-d - 0.10 * mw) / (0.45 * mw))
    fwd = np.where(d >= 0, sy + gap * amp_dn * f_dn, sy - lift * o[None, :] * f_up)
    top = L - lift * o                                                # where the upper lip edge lands
    bot = L + gap * o                                                 # where the lower lip edge lands
    oy = np.arange(y0, y1, dtype=np.float32)
    # invert every column at once: each column's forward map rises monotonically (upper part lands above `top`,
    # lower part below `bot`), so stack the columns with an offset and search them in one go
    n = fwd.shape[0]
    K = float(fwd.max() - fwd.min() + 4 * (y1 - y0) + 16)
    offs = np.arange(ww, dtype=np.float64) * K
    Fl = (fwd.T.astype(np.float64) + offs[:, None]).ravel()
    Fl = np.maximum.accumulate(Fl)
    Q = (oy[:, None].astype(np.float64) + offs[None, :])
    i = np.clip(np.searchsorted(Fl, Q.ravel()), 1, Fl.size - 1)
    col = np.repeat(np.arange(ww)[None, :], hh, axis=0).ravel()
    lo = np.clip(i - 1, col * n, col * n + n - 2)
    f0, f1 = Fl[lo], Fl[lo + 1]
    w = np.clip((Q.ravel() - f0) / np.maximum(f1 - f0, 1e-9), 0, 1)
    sy1 = sy[:, 0].astype(np.float64)
    k = lo - col * n
    mapy = (sy1[k] + w * (sy1[k + 1] - sy1[k])).reshape(hh, ww).astype(np.float32)
    mapx = np.broadcast_to(xs[None, :], (hh, ww)).astype(np.float32).copy()
    warped = cv2.remap(base, mapx, mapy, cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE).astype(np.float32)
    # mouth interior, only inside the gap between the moved lip edges (anti-aliased)
    yy = oy[:, None]
    a_in = np.clip(np.minimum(yy - top[None, :], bot[None, :] - yy) + 0.5, 0, 1) * (o[None, :] > 0)
    hgt = np.maximum(bot - top, 1e-3)[None, :]
    v = np.clip((yy - top[None, :]) / hgt, 0, 1)                     # 0 at upper lip, 1 at lower lip
    near = base[int(max(0, cy - 0.3 * mw)):int(min(H, cy + 0.3 * mw)) + 1, int(max(0, cx - 0.5 * mw)):int(min(W, cx + 0.5 * mw)) + 1]
    near = near.reshape(-1, 3).astype(np.float32) if near.size else np.zeros((1, 3), np.float32)
    sat = float(np.mean(near.max(axis=1) - near.min(axis=1)))       # ~0 for a black-and-white photo
    deep = np.array([10, 2, 4], np.float32)
    warm = np.array([64, 17, 21], np.float32)
    rr = np.clip(np.abs(u), 0, 1)[None, :]
    # dark right under the upper lip and toward the corners, a little warmer low in the middle (rounded look)
    shade = _smooth((v - 0.1) / 0.75) ** 1.3 * (1 - _smooth((rr - 0.15) / 0.7))
    cav = deep + (warm - deep) * shade[..., None]
    Gpx = gap + lift
    if openness > 0.4 and Gpx > 6 and sat >= 18:  # faint hint of upper teeth just under the upper lip, middle part only
        ta = np.clip((openness - 0.4) / 0.4, 0, 1) * 0.28
        tm = _smooth((0.13 - v) / 0.07) * _smooth((0.38 - rr) / 0.18) * ta
        cav = cav * (1 - tm[..., None]) + np.array([182, 172, 165], np.float32) * tm[..., None]
    if openness > 0.25:  # tongue, soft, low in the mouth
        tg = np.clip(1 - ((u[None, :] / 0.5) ** 2 + ((v - 1.08) / 0.5) ** 2), 0, 1) ** 0.8 * 0.45 * min(1.0, openness * 1.4)
        cav = cav * (1 - tg[..., None]) + np.array([118, 44, 50], np.float32) * tg[..., None]
    if sat < 18:  # black-and-white picture: no colour in the mouth either
        k = np.clip((18 - sat) / 12, 0, 1)
        cav = cav * (1 - k) + cav.mean(axis=2, keepdims=True) * k
    # thin shadow on the lips right at the opening edge (lip thickness), not a copy of anything
    edge = np.clip(1 - np.minimum(np.abs(yy - top[None, :]), np.abs(yy - bot[None, :])) / max(1.0, 0.025 * mw), 0, 1) * (o[None, :] > 0.05) * 0.35
    warped *= (1 - edge * (1 - a_in))[..., None]
    out_roi = warped * (1 - a_in[..., None]) + cav * a_in[..., None]
    out = base.copy()
    out[y0:y1, x0:x1] = np.clip(out_roi, 0, 255).astype(np.uint8)
    return out


def shot_tile(rgb, w):
    """RGBA tile of a 'pinned' screenshot: white border + soft drop shadow. Tile centre = screenshot centre."""
    from PIL import Image, ImageDraw, ImageFilter
    h0, w0 = rgb.shape[:2]
    w = max(8, int(round(w))); h = max(8, int(round(w * h0 / float(w0))))
    b = max(2, int(round(w * 0.022))); sh = max(3, int(round(w * 0.045)))
    W2, H2 = w + 2 * b + 2 * sh, h + 2 * b + 2 * sh
    shadow = Image.new("L", (W2, H2), 0)
    ImageDraw.Draw(shadow).rectangle([sh, int(sh * 1.5), sh + w + 2 * b, int(sh * 1.5) + h + 2 * b], fill=120)
    shadow = shadow.filter(ImageFilter.GaussianBlur(sh * 0.5))
    tile = Image.new("RGBA", (W2, H2), (0, 0, 0, 0))
    tile.putalpha(shadow)
    ImageDraw.Draw(tile).rectangle([sh, sh, sh + w + 2 * b - 1, sh + h + 2 * b - 1], fill=(255, 255, 255, 255))
    tile.paste(Image.fromarray(rgb).resize((w, h), Image.LANCZOS), (sh + b, sh + b))
    t = np.asarray(tile).astype(np.float32)
    return t[..., :3], t[..., 3:] / 255.0


def shot_box(shot, scale=1.0):
    """(x0, y0, x1, y1) of the screenshot itself (without shadow) in scaled coords."""
    h0, w0 = shot["rgb"].shape[:2]
    w = shot["w"] * scale; h = w * h0 / float(w0)
    cx, cy = shot["x"] * scale, shot["y"] * scale
    return cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2


def draw_shots(frame, shots, scale=1.0):
    """Draw pinned screenshots on top of frame (HxWx3 uint8), in order (last = front). Returns a new array."""
    if not shots:
        return frame
    out = frame.copy()
    H, W = out.shape[:2]
    for sh in shots:
        w = max(8, int(round(sh["w"] * scale)))
        c = sh.get("_cache")
        if not c or c[0] != w:
            c = (w,) + shot_tile(sh["rgb"], w); sh["_cache"] = c
        rgb, a = c[1], c[2]
        th, tw = rgb.shape[:2]
        x0 = int(round(sh["x"] * scale - tw / 2.0)); y0 = int(round(sh["y"] * scale - th / 2.0))
        ax0, ay0, ax1, ay1 = max(0, x0), max(0, y0), min(W, x0 + tw), min(H, y0 + th)
        if ax1 <= ax0 or ay1 <= ay0:
            continue
        r = rgb[ay0 - y0:ay1 - y0, ax0 - x0:ax1 - x0]; al = a[ay0 - y0:ay1 - y0, ax0 - x0:ax1 - x0]
        reg = out[ay0:ay1, ax0:ax1].astype(np.float32)
        out[ay0:ay1, ax0:ax1] = np.clip(reg * (1 - al) + r * al, 0, 255).astype(np.uint8)
    return out


def fit_even(rgb, max_side=1280):
    h, w = rgb.shape[:2]
    s = min(1.0, max_side / float(max(h, w)))
    if s < 1:
        rgb = cv2.resize(rgb, (int(w * s), int(h * s)), interpolation=cv2.INTER_AREA)
    h, w = rgb.shape[:2]
    if h % 2 or w % 2:
        rgb = np.ascontiguousarray(rgb[: h - h % 2, : w - w % 2])
    return rgb, s


def ffmpeg_exe():
    import imageio_ffmpeg
    return imageio_ffmpeg.get_ffmpeg_exe()


def export_mp4(base_rgb, mouth, wav_path, out_path, fps=FPS, progress=None, shots=None):
    """Render the lip sync animation + TTS audio to an H.264/AAC MP4."""
    img, s = fit_even(base_rgb)
    m = scale_mouth(mouth, s)
    samples, sr = load_wav(wav_path)
    op, wd = lipsync_track(samples, sr, fps)
    tail = int(fps * 0.4)
    n = len(op) + tail
    h, w = img.shape[:2]
    cmd = [ffmpeg_exe(), "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", "%dx%d" % (w, h),
           "-r", str(fps), "-i", "-", "-i", wav_path, "-map", "0:v", "-map", "1:a",
           "-c:v", "libx264", "-preset", "medium", "-crf", "18", "-pix_fmt", "yuv420p",
           "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", out_path]
    p = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                         creationflags=NO_WINDOW)
    try:
        for i in range(n):
            o = op[i] if i < len(op) else 0.0
            wf = wd[i] if i < len(wd) else 1.0
            p.stdin.write(draw_shots(render_mouth(img, m, o, wf), shots, s).tobytes())
            if progress and i % 5 == 0:
                progress(i / float(n))
        p.stdin.close()
    except (BrokenPipeError, OSError):
        pass
    err = p.stderr.read().decode("utf-8", "ignore")
    if p.wait() != 0 or not os.path.exists(out_path):
        raise RuntimeError("ffmpeg failed: " + err[-400:])
    if progress:
        progress(1.0)
    return out_path
