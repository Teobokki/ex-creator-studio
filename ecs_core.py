"""EX CREATOR STUDIO - lip sync engine (face/mouth detection, audio envelope, mouth rendering, MP4 export).
(c)2026 EX Inc./HYPERION DIGITAL (c)2026"""
import math, os, subprocess, sys, wave
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
    ry0, ry1 = int(max(0, y + fh * 0.50)), int(min(H, y + fh * 0.97))
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
    n, lab, st, cen = cv2.connectedComponentsWithStats(m, 8)
    rh, rw = m.shape
    exp_y = (fh * (0.775 if kind == "face" else 0.72)) - (ry0 - y)
    best, bs = None, 1e9
    for i in range(1, n):
        bx, by, bw, bh, area = st[i]
        if area < max(6, rw * rh * 0.002) or bw < rw * 0.10:
            continue
        if bx <= 0 or bx + bw >= rw:  # touches the side: chin/jaw outline, hair, collar
            continue
        if bw < bh * 1.2 or bw > rw * 0.95:
            continue
        ccx, ccy = cen[i]
        cost = abs(ccx - rw / 2) / rw * 2 + abs(ccy - exp_y) / rh * 1.2 - min(1.0, area / (rw * rh * 0.05)) * 0.3
        if cost < bs:
            best, bs = (rx0 + bx + bw / 2.0, ry0 + by + bh / 2.0, float(bw)), cost
    return best


def detect_mouth(rgb):
    """Return (kind, face_rect, mouth) where kind is 'face', 'anime' or None.
    mouth = (cx, cy, mw, mh) in image pixels."""
    h, w = rgb.shape[:2]
    s = min(1.0, 900.0 / max(h, w))
    small = cv2.resize(rgb, (max(1, int(w * s)), max(1, int(h * s))), interpolation=cv2.INTER_AREA) if s < 1 else rgb
    gray = cv2.cvtColor(small, cv2.COLOR_RGB2GRAY)
    geq = cv2.equalizeHist(gray)
    md = min(gray.shape)
    best = None
    for name, kind, kw in (("lbpcascade_animeface.xml", "anime", dict(scaleFactor=1.05, minNeighbors=5)),
                           ("haarcascade_frontalface_default.xml", "face", dict(scaleFactor=1.1, minNeighbors=6)),
                           ("haarcascade_frontalface_alt2.xml", "face", dict(scaleFactor=1.1, minNeighbors=5))):
        c = _cascade(name)
        if c is None:
            continue
        try:
            faces = c.detectMultiScale(geq, minSize=(max(24, int(md * 0.08)),) * 2, **kw)
        except Exception:
            faces = ()
        for (x, y, fw, fh) in faces:
            if best is None or fw * fh > best[1][2] * best[1][3] * 1.15:
                best = (kind, (x, y, fw, fh))
        if best is not None:
            break
    if best is None:
        return None, None, None
    kind, (x, y, fw, fh) = best
    if kind == "face":
        cx, cy, mw = x + fw * 0.5, y + fh * 0.775, fw * 0.36
    else:
        cx, cy, mw = x + fw * 0.5, y + fh * 0.72, fw * 0.22
    blob = _mouth_blob(small, (x, y, fw, fh), kind)
    if blob is not None:
        cx, cy = blob[0], blob[1]
        mw = float(np.clip(blob[2] * 1.05, fw * (0.16 if kind == "anime" else 0.26), fw * 0.5))
    else:
        cy = _lip_line(gray, cx, cy, mw, fh * 0.07)
    mh = mw * 0.32
    inv = 1.0 / s
    face = tuple(int(v * inv) for v in (x, y, fw, fh))
    return kind, face, (cx * inv, cy * inv, mw * inv, mh * inv)


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
    out = np.zeros_like(x)
    v = 0.0
    for i, t in enumerate(x):  # fast attack, slower release
        v += (t - v) * (0.75 if t > v else 0.45)
        out[i] = v
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
    """Return a copy of base (HxWx3 uint8 RGB) with the mouth opened by `openness` (0..1).
    The jaw/lower lip is warped down with a smooth displacement field, the upper lip lifts slightly,
    and a soft-edged mouth cavity (dark interior, teeth hint, tongue) is blended into the gap."""
    if mouth is None or openness <= 0.02:
        return base
    H, W = base.shape[:2]
    cx, cy, mw, mh = mouth
    mw = max(4.0, mw)
    openness = float(min(1.0, openness))
    gap = openness * 0.30 * mw  # open distance in px
    lift = gap * 0.18
    x0, x1 = int(max(0, cx - 1.9 * mw)), int(min(W, cx + 1.9 * mw))
    y0, y1 = int(max(0, cy - 0.9 * mw)), int(min(H, cy + 2.0 * mw))
    if x1 - x0 < 4 or y1 - y0 < 4:
        return base
    roi = base[y0:y1, x0:x1]
    hh, ww = roi.shape[:2]
    xs = np.arange(ww, dtype=np.float32) + x0
    ys = np.arange(hh, dtype=np.float32) + y0
    dx = (xs - cx) / mw
    gx = np.exp(-(dx / (0.62 * width)) ** 2 * 0.5) * _smooth((1.9 - np.abs(dx)) / 0.5)
    dy = (ys - cy) / mw
    down = _smooth((dy + 0.04) / 0.14) * (1 - _smooth((dy - 0.55) / 1.2))  # lower lip + jaw
    up = _smooth((dy + 0.75) / 0.6) * (1 - _smooth((dy + 0.02) / 0.06))     # upper lip
    disp = gx[None, :] * (down[:, None] * gap - up[:, None] * lift)
    mapy = (ys[:, None] - disp).astype(np.float32)
    mapx = np.broadcast_to(xs[None, :], (hh, ww)).astype(np.float32)
    big = base
    warped = cv2.remap(big, mapx, mapy, cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)
    # cavity: lens shape - flatter upper lip line, rounder lower lip
    ax = max(1.5, mw * 0.40 * width * (0.80 + 0.20 * openness))
    ecx, ecy = cx - x0, cy - y0 - lift
    ay_t, ay_b = max(0.6, lift * 1.1 + gap * 0.08), max(1.0, gap * 0.95)
    t = np.linspace(0, 2 * np.pi, 72, endpoint=False)
    sn = np.sin(t)
    px = ecx + ax * np.cos(t) * (1 - 0.08 * np.abs(sn))
    py = ecy + np.where(sn > 0, ay_b, ay_t) * sn + lift
    mask = np.zeros((hh, ww), np.uint8)
    cv2.fillPoly(mask, [np.round(np.stack([px, py], 1) * 16).astype(np.int32)], 255, cv2.LINE_AA, 4)
    top, bot = ecy + lift - ay_t, ecy + lift + ay_b
    hgt = max(1.0, bot - top)
    lip_band = base[int(min(H - 1, max(0, cy - 1))):int(min(H, cy + 2)), int(max(0, cx - mw * 0.3)):int(min(W, cx + mw * 0.3))]
    dark = lip_band.reshape(-1, 3).min(axis=0).astype(np.float32) if lip_band.size else np.array([40, 10, 15], np.float32)
    inner = np.clip(dark * 0.25 + np.array([62, 16, 24], np.float32), 0, 255)
    yy = np.arange(hh, dtype=np.float32)[:, None]
    xx = np.arange(ww, dtype=np.float32)[None, :]
    vy = (yy - top) / hgt
    cav = np.empty((hh, ww, 3), np.float32)
    cav[:] = inner
    cav *= (0.55 + 0.45 * _smooth(vy / 0.6))[..., None]  # shadow under the upper lip
    if openness > 0.25:  # upper teeth hint
        ta = np.clip((openness - 0.25) / 0.35, 0, 1) * 0.6
        tm = (_smooth((0.2 - vy) / 0.06) * _smooth((ax * 0.55 - np.abs(xx - ecx)) / (ax * 0.25)) * ta)[..., None]
        cav = cav * (1 - tm) + np.array([226, 220, 212], np.float32) * tm
    tongue = ((xx - ecx) / (ax * 0.58)) ** 2 + ((yy - (bot - hgt * 0.12)) / (hgt * 0.42)) ** 2
    tw = (np.clip(1.0 - tongue, 0, 1) ** 0.6 * 0.75 * min(1.0, openness * 1.6))[..., None]
    cav = cav * (1 - tw) + np.array([168, 68, 78], np.float32) * tw
    rr = np.sqrt(((xx - ecx) / ax) ** 2)
    cav *= (1 - 0.5 * _smooth((rr - 0.6) / 0.4))[..., None]  # darker corners
    blur = max(0.7, min(ax, hgt) * 0.08)
    a = cv2.GaussianBlur(mask.astype(np.float32) / 255, (0, 0), blur)[..., None]
    roi_out = warped.astype(np.float32) * (1 - a) + cav * a
    out = base.copy()
    out[y0:y1, x0:x1] = np.clip(roi_out, 0, 255).astype(np.uint8)
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
    m = None if mouth is None else (mouth[0] * s, mouth[1] * s, mouth[2] * s, mouth[3] * s)
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
