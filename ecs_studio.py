"""EX STUDIO - OBS-style screen recorder with a lip-syncing character "webcam" square.
Recording: bundled ffmpeg gdigrab (H.264, ~30 fps, optional DirectShow microphone).
Final video: recording + character square top-right, lip-synced to TTS lines, audio mixed.
(c)2026 EX Inc./HYPERION DIGITAL (c)2026"""
import ctypes, os, re, subprocess, threading, time, wave
import numpy as np
import cv2
from PIL import Image, ImageDraw
import ecs_core as core

NO_WINDOW = core.NO_WINDOW
FPS = 30
SQ = 512                      # working resolution of the character square
SQ_FRAC, MARGIN_FRAC = 0.23, 0.03
MIX_SR = 48000


# ----------------------------------------------------------------------------- monitors / devices
def list_monitors():
    """[(label, (x, y, w, h) or None)]; None = full virtual desktop."""
    out = [("Full desktop (all monitors)", None)]
    if os.name != "nt":
        return out
    try:
        from ctypes import wintypes
        mons = []
        class MONITORINFOEXW(ctypes.Structure):
            _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT), ("rcWork", wintypes.RECT),
                        ("dwFlags", wintypes.DWORD), ("szDevice", wintypes.WCHAR * 32)]
        PROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HMONITOR, wintypes.HDC, ctypes.POINTER(wintypes.RECT), wintypes.LPARAM)
        def cb(h, dc, rc, lp):
            mi = MONITORINFOEXW(); mi.cbSize = ctypes.sizeof(MONITORINFOEXW)
            if ctypes.windll.user32.GetMonitorInfoW(h, ctypes.byref(mi)):
                r = mi.rcMonitor
                mons.append(((r.left, r.top, r.right - r.left, r.bottom - r.top), bool(mi.dwFlags & 1)))
            return True
        ctypes.windll.user32.EnumDisplayMonitors(None, None, PROC(cb), 0)
        mons.sort(key=lambda m: (not m[1], m[0][0], m[0][1]))
        for i, ((x, y, w, h), prim) in enumerate(mons):
            out.append(("Monitor %d%s  (%dx%d)" % (i + 1, " \u2013 primary" if prim else "", w, h), (x, y, w, h)))
    except Exception:
        pass
    return out


def list_mics():
    if os.name != "nt":
        return []
    try:
        r = subprocess.run([core.ffmpeg_exe(), "-hide_banner", "-list_devices", "true", "-f", "dshow", "-i", "dummy"],
                           capture_output=True, timeout=15, creationflags=NO_WINDOW)
        txt = r.stderr.decode("utf-8", "ignore")
        names = re.findall(r'"([^"]+)"\s*\(audio\)', txt)
        if not names:  # older ffmpeg layout: section header then names
            sec = txt.split("DirectShow audio devices")[-1] if "DirectShow audio devices" in txt else ""
            names = [n for n in re.findall(r'\]\s+"([^"]+)"', sec)]
        seen = []
        for n in names:
            if n not in seen and not n.startswith("@device"):
                seen.append(n)
        return seen
    except Exception:
        return []


class Recorder:
    """ffmpeg gdigrab screen recorder. stop() sends 'q' so the MP4 is finalised cleanly."""
    def __init__(self):
        self.p = None; self.path = None; self.t0 = 0; self.log = None

    def start(self, path, region=None, mic=None, fps=FPS):
        ff = core.ffmpeg_exe()
        cmd = [ff, "-hide_banner", "-loglevel", "error", "-y", "-thread_queue_size", "512",
               "-f", "gdigrab", "-framerate", str(fps), "-draw_mouse", "1"]
        if region:
            x, y, w, h = region
            cmd += ["-offset_x", str(x), "-offset_y", str(y), "-video_size", "%dx%d" % (w - w % 2, h - h % 2)]
        cmd += ["-i", "desktop"]
        if mic:
            cmd += ["-thread_queue_size", "1024", "-f", "dshow", "-i", "audio=" + mic]
        cmd += ["-vf", "scale=trunc(iw/2)*2:trunc(ih/2)*2", "-c:v", "libx264", "-preset", "ultrafast", "-crf", "23",
                "-pix_fmt", "yuv420p", "-r", str(fps)]
        if mic:
            cmd += ["-c:a", "aac", "-b:a", "160k"]
        cmd += ["-movflags", "+faststart", path]
        self.log = path + ".log"
        self.p = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                                  stderr=open(self.log, "wb"), creationflags=NO_WINDOW)
        self.path, self.t0 = path, time.time()

    def running(self):
        return self.p is not None and self.p.poll() is None

    def error_text(self):
        try:
            return open(self.log, "rb").read().decode("utf-8", "ignore")[-500:]
        except Exception:
            return ""

    def stop(self, timeout=20):
        if not self.p:
            return None
        try:
            if self.p.poll() is None:
                self.p.stdin.write(b"q"); self.p.stdin.flush()
        except Exception:
            pass
        try:
            self.p.wait(timeout)
        except Exception:
            self.p.terminate()
            try: self.p.wait(5)
            except Exception: self.p.kill()
        self.p = None
        return self.path if self.path and os.path.exists(self.path) and os.path.getsize(self.path) > 1000 else None


# ----------------------------------------------------------------------------- media info
def probe(path):
    """(width, height, duration_s, has_audio) parsed from ffmpeg's banner (ffprobe isn't bundled)."""
    r = subprocess.run([core.ffmpeg_exe(), "-hide_banner", "-i", path], capture_output=True, creationflags=NO_WINDOW)
    t = r.stderr.decode("utf-8", "ignore")
    m = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", t)
    dur = int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3)) if m else 0.0
    v = re.search(r"Stream.*Video:.*?(\d{2,5})x(\d{2,5})", t)
    if not v:
        raise RuntimeError("Not a video file")
    return int(v.group(1)), int(v.group(2)), dur, ("Audio:" in t)


# ----------------------------------------------------------------------------- character square
def make_square(rgb):
    """Webcam-style square crop of the character (centred on the face if one is found) + mouth in square coords."""
    kind, face, mouth = core.detect_mouth(rgb)
    h, w = rgb.shape[:2]
    if face is not None:
        fx, fy, fw, fh = face
        side = int(min(min(w, h), max(fw, fh) * 2.3))
        cx, cy = fx + fw / 2, fy + fh * 0.55
    else:
        side = min(w, h); cx, cy = w / 2, h / 2
    x0 = int(np.clip(cx - side / 2, 0, w - side)); y0 = int(np.clip(cy - side / 2, 0, h - side))
    crop = rgb[y0:y0 + side, x0:x0 + side]
    s = SQ / float(side)
    sq = cv2.resize(crop, (SQ, SQ), interpolation=cv2.INTER_AREA if s < 1 else cv2.INTER_CUBIC)
    m = None
    if mouth is not None:
        m = ((mouth[0] - x0) * s, (mouth[1] - y0) * s, mouth[2] * s, mouth[3] * s)
        if not (0 <= m[0] < SQ and 0 <= m[1] < SQ):
            m, kind = None, None
    return np.ascontiguousarray(sq), (kind if m is not None else None), m


_MASKS = {}


def masks(S):
    """Anti-aliased rounded-square alpha mask and border ring for an SxS overlay."""
    if S not in _MASKS:
        k = 4; B = S * k; r = int(B * 0.13); bw = max(2 * k, int(B * 0.018))
        a = Image.new("L", (B, B), 0); ImageDraw.Draw(a).rounded_rectangle([0, 0, B - 1, B - 1], radius=r, fill=255)
        inner = Image.new("L", (B, B), 0)
        ImageDraw.Draw(inner).rounded_rectangle([bw, bw, B - 1 - bw, B - 1 - bw], radius=max(1, r - bw), fill=255)
        a = np.asarray(a.resize((S, S), Image.LANCZOS)).astype(np.float32) / 255
        ia = np.asarray(inner.resize((S, S), Image.LANCZOS)).astype(np.float32) / 255
        _MASKS[S] = (a[..., None], np.clip(a - ia, 0, 1)[..., None])
    return _MASKS[S]


BORDER_RGB = np.array([255, 69, 69], np.float32)


def overlay_geom(W, H):
    S = int(round(H * SQ_FRAC)); S -= S % 2
    S = max(32, min(S, W // 2))
    mg = max(6, int(round(H * MARGIN_FRAC)))
    return W - S - mg, mg, S


def char_frame(sq, mouth, op, wf, S):
    """The character square at size S with the mouth at openness op."""
    img = cv2.resize(sq, (S, S), interpolation=cv2.INTER_AREA)
    if mouth is not None and op > 0.02:
        k = S / float(SQ)
        img = core.render_mouth(img, (mouth[0] * k, mouth[1] * k, mouth[2] * k, mouth[3] * k), op, wf)
    return img


def composite(frame, sq, mouth, op=0.0, wf=1.0):
    """Paste the rounded, red-bordered character square into the top-right of frame (in place)."""
    H, W = frame.shape[:2]
    x, y, S = overlay_geom(W, H)
    if x < 0 or y + S > H:
        return frame
    a, ring = masks(S)
    c = char_frame(sq, mouth, op, wf, S).astype(np.float32)
    reg = frame[y:y + S, x:x + S].astype(np.float32)
    # soft drop shadow feel: darken just around the square
    out = reg * (1 - a) + c * a
    out = out * (1 - ring) + BORDER_RGB * ring
    frame[y:y + S, x:x + S] = np.clip(out, 0, 255).astype(np.uint8)
    return frame


# ----------------------------------------------------------------------------- lines / mixing
class Line:
    def __init__(self, t, text, wav, voice, rate):
        self.t, self.text, self.wav, self.voice, self.rate = float(t), text, wav, voice, rate
        s, sr = core.load_wav(wav)
        self.samples, self.sr = s, sr
        self.dur = len(s) / float(sr)
        self.op, self.wd = core.lipsync_track(s, sr, FPS)

    def mouth_at(self, t):
        i = int((t - self.t) * FPS)
        if 0 <= i < len(self.op):
            return float(self.op[i]), float(self.wd[i])
        return 0.0, 1.0


def mouth_at(lines, t):
    best = (0.0, 1.0)
    for ln in lines:
        o = ln.mouth_at(t)
        if o[0] > best[0]:
            best = o
    return best


def _resample(x, sr, to):
    if sr == to or len(x) == 0:
        return x
    n = int(round(len(x) * to / float(sr)))
    return np.interp(np.linspace(0, len(x) - 1, n), np.arange(len(x)), x).astype(np.float32)


def mix_lines(lines, duration, path):
    n = int(max(duration, max([l.t + l.dur for l in lines] or [0])) * MIX_SR) + 1
    mix = np.zeros(n, np.float32)
    for ln in lines:
        x = _resample(ln.samples, ln.sr, MIX_SR)
        i = int(ln.t * MIX_SR)
        j = min(n, i + len(x))
        mix[i:j] += x[: j - i]
    mix = np.clip(mix, -1, 1)
    with wave.open(path, "wb") as wf:
        wf.setnchannels(1); wf.setsampwidth(2); wf.setframerate(MIX_SR)
        wf.writeframes((mix * 32767).astype("<i2").tobytes())
    return path


def export_final(rec_path, sq, mouth, lines, out_path, tmpdir, progress=None, cancel=None, no_square=False):
    """Recording + lip-syncing character square top-right + TTS lines mixed with any recorded audio."""
    W, H, dur, has_audio = probe(rec_path)
    if W % 2 or H % 2:
        W, H = W - W % 2, H - H % 2
    end = max(dur, max([l.t + l.dur for l in lines] or [0]))
    mixwav = os.path.join(tmpdir, "mix_%d.wav" % int(time.time() * 1000))
    mix_lines(lines, end, mixwav)
    ff = core.ffmpeg_exe()
    dec = subprocess.Popen([ff, "-hide_banner", "-loglevel", "error", "-i", rec_path, "-an",
                            "-vf", "fps=%d,scale=%d:%d" % (FPS, W, H), "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
                           stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, creationflags=NO_WINDOW)
    log = out_path + ".log"
    enc_cmd = [ff, "-hide_banner", "-loglevel", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24",
               "-s", "%dx%d" % (W, H), "-r", str(FPS), "-i", "-", "-i", mixwav]
    if has_audio:
        enc_cmd += ["-i", rec_path, "-filter_complex",
                    "[2:a]aresample=%d[r];[1:a][r]amix=inputs=2:duration=first:normalize=0[a]" % MIX_SR,
                    "-map", "0:v", "-map", "[a]"]
    else:
        enc_cmd += ["-map", "0:v", "-map", "1:a"]
    enc_cmd += ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p",
                "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", out_path]
    enc = subprocess.Popen(enc_cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=open(log, "wb"),
                           creationflags=NO_WINDOW)
    fsz = W * H * 3
    buf = bytearray(fsz)
    last, alive, i = None, True, 0
    try:
        while True:
            if cancel and cancel():
                raise RuntimeError("cancelled")
            t = i / float(FPS)
            if alive:
                got = dec.stdout.readinto(buf)
                if got == fsz:
                    last = np.frombuffer(buf, np.uint8).reshape(H, W, 3)
                else:
                    alive = False
            if last is None:
                raise RuntimeError("Could not read the recording")
            if not alive and t >= end:
                break
            fr = last.copy()  # recording ended but a line is still talking -> hold the last frame
            op, wf = mouth_at(lines, t)
            if not no_square:
                composite(fr, sq, mouth, op, wf)
            enc.stdin.write(fr.tobytes())
            i += 1
            if progress and i % 10 == 0:
                progress(min(0.99, t / max(0.1, end)))
        enc.stdin.close()
    except (BrokenPipeError, OSError):
        pass
    finally:
        try: dec.kill()
        except Exception: pass
    rc = enc.wait()
    try: os.remove(mixwav)
    except Exception: pass
    err = ""
    try: err = open(log, "rb").read().decode("utf-8", "ignore"); os.remove(log)
    except Exception: pass
    if rc != 0 or not os.path.exists(out_path):
        raise RuntimeError("ffmpeg failed: " + err[-400:])
    if progress:
        progress(1.0)
    return out_path
