"""EX SCREEN RECORDER 2026 - screen recorder tab of EX CREATOR STUDIO.

Video: bundled ffmpeg gdigrab (full desktop, one monitor, one window or a region), H.264 High yuv420p, constant frame rate.
Audio: system sound only (WASAPI loopback of the default output device), never a microphone. Recorded beside the video and
       aligned with the video's wall-clock start time, then muxed as AAC 48 kHz stereo.
Pause makes a new segment; on stop the segments are joined without re-encoding into one +faststart MP4.
Nothing is uploaded anywhere.
(c)2026 EX Inc./HYPERION DIGITAL (c)2026"""
import os, re, sys, json, time, wave, shutil, tempfile, threading, subprocess, math
import numpy as np
import cv2
import tkinter as tk
from tkinter import ttk, messagebox
from PIL import Image, ImageTk, ImageDraw

NAME = "EX SCREEN RECORDER 2026"
NO_WINDOW = 0x08000000 if os.name == "nt" else 0
QUALITY = {"720p30": (1280, 720, 30, 8), "1080p30": (1920, 1080, 30, 12), "1080p60": (1920, 1080, 60, 18)}
SR = 48000
GUID_VIDEOS = (0x18989B1D, 0x99B5, 0x455B, (0x84, 0x1C, 0xAB, 0x7C, 0x74, 0xE4, 0xDD, 0xFC))
GUID_DOWNLOADS = (0x374DE290, 0x123F, 0x4565, (0x91, 0x64, 0x39, 0xC4, 0x92, 0x5E, 0x46, 0x7B))


# ============================================================================ helpers
def known_folder(g, fallback):
    d = os.path.join(os.path.expanduser("~"), fallback)
    if os.name == "nt":
        try:
            import ctypes
            from ctypes import wintypes
            class GUID(ctypes.Structure):
                _fields_ = [("a", wintypes.DWORD), ("b", wintypes.WORD), ("c", wintypes.WORD), ("d", ctypes.c_ubyte * 8)]
            gg = GUID(g[0], g[1], g[2], (ctypes.c_ubyte * 8)(*g[3])); p = ctypes.c_wchar_p()
            if ctypes.windll.shell32.SHGetKnownFolderPath(ctypes.byref(gg), 0, None, ctypes.byref(p)) == 0 and p.value:
                d = p.value
        except Exception:
            pass
    os.makedirs(d, exist_ok=True)
    return d


def videos_dir(): return known_folder(GUID_VIDEOS, "Videos")
def downloads_dir(): return known_folder(GUID_DOWNLOADS, "Downloads")


def data_dir():
    base = os.environ.get("LOCALAPPDATA") or os.path.join(os.path.expanduser("~"), ".local", "share")
    d = os.path.join(base, "EX CREATOR STUDIO"); os.makedirs(d, exist_ok=True); return d


def ffmpeg_exe():
    try:
        import imageio_ffmpeg; return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return shutil.which("ffmpeg") or "ffmpeg"


def list_monitors():
    """[(label, (x, y, w, h))] physical pixels, primary first."""
    out = []
    if os.name == "nt":
        try:
            import ctypes
            from ctypes import wintypes
            mons = []
            class MI(ctypes.Structure):
                _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT), ("rcWork", wintypes.RECT),
                            ("dwFlags", wintypes.DWORD), ("szDevice", wintypes.WCHAR * 32)]
            PROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HMONITOR, wintypes.HDC, ctypes.POINTER(wintypes.RECT), wintypes.LPARAM)
            def cb(h, dc, rc, lp):
                mi = MI(); mi.cbSize = ctypes.sizeof(MI)
                if ctypes.windll.user32.GetMonitorInfoW(h, ctypes.byref(mi)):
                    r = mi.rcMonitor; mons.append(((r.left, r.top, r.right - r.left, r.bottom - r.top), bool(mi.dwFlags & 1)))
                return True
            ctypes.windll.user32.EnumDisplayMonitors(None, None, PROC(cb), 0)
            mons.sort(key=lambda m: (not m[1], m[0][0], m[0][1]))
            for i, ((x, y, w, h), prim) in enumerate(mons):
                out.append(("Monitor %d%s  (%d \u00d7 %d)" % (i + 1, " - primary" if prim else "", w, h), (x, y, w, h)))
        except Exception:
            pass
    return out


def list_windows(exclude=()):
    """visible top-level windows with a title: [(title, hwnd)]"""
    if os.name != "nt":
        return []
    import ctypes
    from ctypes import wintypes
    u = ctypes.windll.user32; out = []
    PROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def cb(h, lp):
        try:
            if not u.IsWindowVisible(h) or u.IsIconic(h): return True
            n = u.GetWindowTextLengthW(h)
            if n <= 0: return True
            b = ctypes.create_unicode_buffer(n + 1); u.GetWindowTextW(h, b, n + 1); t = b.value.strip()
            r = wintypes.RECT(); u.GetWindowRect(h, ctypes.byref(r))
            if t and (r.right - r.left) > 80 and (r.bottom - r.top) > 60 and int(h or 0) not in exclude and t not in ("Program Manager",):
                out.append((t, int(h or 0)))
        except Exception:
            pass
        return True
    u.EnumWindows(PROC(cb), 0)
    seen, res = set(), []
    for t, h in out:
        if t not in seen: seen.add(t); res.append((t, h))
    return res


def virtual_screen():
    if os.name == "nt":
        try:
            import ctypes
            g = ctypes.windll.user32.GetSystemMetrics
            return g(76), g(77), g(78), g(79)
        except Exception:
            pass
    return None


def exclude_from_capture(win):
    """keep our own floating controls / countdown out of the video (Windows 10 2004+). True if it worked."""
    if os.name != "nt": return False
    try:
        import ctypes
        win.update_idletasks()
        hwnd = int(win.frame(), 16)
        return bool(ctypes.windll.user32.SetWindowDisplayAffinity(hwnd, 0x11))
    except Exception:
        return False


def fmt_t(s):
    s = int(max(0, s)); return "%02d:%02d:%02d" % (s // 3600, s // 60 % 60, s % 60) if s >= 3600 else "%02d:%02d" % (s // 60, s % 60)


def probe(path):
    """(duration_s, width, height) from ffmpeg's banner"""
    try:
        r = subprocess.run([ffmpeg_exe(), "-hide_banner", "-i", path], capture_output=True, timeout=20, creationflags=NO_WINDOW)
        t = r.stderr.decode("utf-8", "ignore")
        m = re.search(r"Duration: (\d+):(\d+):([\d.]+)", t); dur = int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3)) if m else 0
        m = re.search(r"Video:.*?(\d{2,5})x(\d{2,5})", t); w, h = (int(m.group(1)), int(m.group(2))) if m else (0, 0)
        return dur, w, h
    except Exception:
        return 0, 0, 0


def grab_frame(path, t=None):
    cap = cv2.VideoCapture(path)
    try:
        if t is not None: cap.set(cv2.CAP_PROP_POS_MSEC, max(0.0, t) * 1000)
        ok, f = cap.read()
        if not ok and t:
            cap.set(cv2.CAP_PROP_POS_MSEC, 0); ok, f = cap.read()
        return cv2.cvtColor(f, cv2.COLOR_BGR2RGB) if ok else None
    finally:
        cap.release()


# ============================================================================ system audio (loopback)
class SystemAudio:
    """Default output device loopback via WASAPI (soundcard). Runs while the tab is open (for the meter) and while recording.
    Silence gaps (WASAPI delivers no packets when nothing plays) are filled from the wall clock so audio never drifts."""
    def __init__(self, fake=False):
        self.level = 0.0; self.err = None; self.ok = False; self.device = ""
        self._run = False; self._th = None; self._lock = threading.Lock(); self._cap = False
        self.fake = fake or bool(os.environ.get("EXREC_FAKE_AUDIO"))
        self.seg = None

    def start(self):
        if self._run: return
        self._run = True; self._th = threading.Thread(target=self._loop, daemon=True); self._th.start()

    def stop(self):
        self._run = False

    def _loop(self):
        if self.fake:
            self.ok = True; self.device = "Test tone (fake loopback)"; ph = 0.0; t_next = time.time()
            while self._run:
                n = 960; t = (np.arange(n) + ph) / SR; ph += n
                beep = (np.sin(2 * np.pi * 440 * t) * 0.25 * ((t % 1.0) < 0.15)).astype(np.float32)
                self._feed(np.stack([beep, beep], 1), time.time())
                t_next += n / SR; time.sleep(max(0, t_next - time.time()))
            return
        try:
            import soundcard as sc
            spk = sc.default_speaker(); self.device = spk.name
            mic = sc.get_microphone(id=str(spk.name), include_loopback=True)
            with mic.recorder(samplerate=SR, channels=2, blocksize=SR // 50) as r:
                self.ok = True; self.err = None
                while self._run:
                    d = r.record(numframes=SR // 50)
                    if d is None or len(d) == 0:
                        self._feed(None, time.time()); continue
                    d = np.asarray(d, np.float32)
                    if d.ndim == 1: d = np.stack([d, d], 1)
                    self._feed(d[:, :2], time.time())
        except Exception as e:
            self.ok = False; self.err = str(e) or e.__class__.__name__; self.level = 0.0

    def _feed(self, d, now):
        if d is not None:
            pk = float(np.abs(d).max()) if len(d) else 0.0
            self.level = max(pk, self.level * 0.82)
        else:
            self.level *= 0.82
        with self._lock:
            s = self.seg
            if not self._cap or s is None or d is None: return
            if s["t0"] is None: s["t0"] = now - len(d) / SR
            exp = int((now - s["t0"]) * SR)
            gap = exp - (s["n"] + len(d))
            if gap > SR // 10:
                s["chunks"].append(np.zeros((gap, 2), np.float32)); s["n"] += gap
            s["chunks"].append(d); s["n"] += len(d)

    def begin(self):
        with self._lock:
            self.seg = {"t0": None, "n": 0, "chunks": [], "wall0": time.time()}; self._cap = True

    def end(self):
        with self._lock:
            self._cap = False; s = self.seg; self.seg = None
        if s is None: return None
        a = np.concatenate(s["chunks"], 0) if s["chunks"] else np.zeros((0, 2), np.float32)
        return {"pcm": a, "t0": s["t0"] if s["t0"] is not None else s["wall0"], "t_end": time.time()}


def write_wav(path, pcm):
    x = (np.clip(pcm, -1, 1) * 32767).astype("<i2")
    with wave.open(path, "wb") as w:
        w.setnchannels(2); w.setsampwidth(2); w.setframerate(SR); w.writeframes(x.tobytes())


# ============================================================================ recording engine
class Engine:
    def __init__(self, audio):
        self.audio = audio; self.p = None; self.segs = []; self.tmp = None; self.state = "idle"
        self.elapsed0 = 0.0; self.t_seg = 0.0; self.src = None; self.q = None; self.with_audio = True; self.err = ""

    def elapsed(self):
        return self.elapsed0 + ((time.time() - self.t_seg) if self.state == "rec" else 0.0)

    def _cmd(self, path):
        W, H, fps, mb = QUALITY[self.q]; s = self.src
        cmd = [ffmpeg_exe(), "-hide_banner", "-loglevel", "info", "-y", "-thread_queue_size", "1024"]
        if os.name == "nt":
            cmd += ["-f", "gdigrab", "-framerate", str(fps), "-draw_mouse", "1"]
            if s["kind"] == "window":
                cmd += ["-i", "title=" + s["title"]]
            else:
                if s.get("rect"):
                    x, y, w, h = s["rect"]
                    cmd += ["-offset_x", str(x), "-offset_y", str(y), "-video_size", "%dx%d" % (w - w % 2, h - h % 2)]
                cmd += ["-i", "desktop"]
        else:  # Linux/X11 (development + tests)
            disp = os.environ.get("DISPLAY", ":0"); x, y, w, h = s.get("rect") or (0, 0, 1280, 720)
            cmd += ["-f", "x11grab", "-framerate", str(fps), "-video_size", "%dx%d" % (w - w % 2, h - h % 2), "-i", "%s+%d,%d" % (disp, x, y)]
        vf = ("scale=w='min(%d,iw)':h='min(%d,ih)':force_original_aspect_ratio=decrease:flags=lanczos,"
              "scale=trunc(iw/2)*2:trunc(ih/2)*2,format=yuv420p" % (W, H))
        cmd += ["-vf", vf, "-c:v", "libx264", "-preset", "superfast" if fps >= 60 else "veryfast", "-crf", "20",
                "-maxrate", "%dM" % mb, "-bufsize", "%dM" % (mb * 2), "-g", str(fps * 2), "-profile:v", "high",
                "-pix_fmt", "yuv420p", "-r", str(fps), "-fps_mode", "cfr", "-an", path]
        return cmd

    def start(self, src, q, with_audio):
        self.src, self.q, self.with_audio = src, q, with_audio
        self.tmp = tempfile.mkdtemp(prefix="EXScreenRec_"); self.segs = []; self.elapsed0 = 0.0; self.err = ""
        self._seg_start()

    def _seg_start(self):
        i = len(self.segs); path = os.path.join(self.tmp, "seg_%03d.mp4" % i); log = path + ".log"
        lf = open(log, "wb")
        if self.with_audio: self.audio.begin()
        t_launch = time.time()
        self.p = subprocess.Popen(self._cmd(path), stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=lf, creationflags=NO_WINDOW)
        self.segs.append({"path": path, "log": log, "launch": t_launch, "audio": None})
        self.t_seg = time.time(); self.state = "rec"

    def alive(self):
        return self.p is not None and self.p.poll() is None

    def log_tail(self):
        try: return open(self.segs[-1]["log"], "rb").read().decode("utf-8", "ignore")[-600:]
        except Exception: return ""

    def _seg_stop(self):
        p = self.p; self.p = None
        if p is not None:
            try:
                if p.poll() is None: p.stdin.write(b"q"); p.stdin.flush()
            except Exception:
                pass
            try: p.wait(20)
            except Exception:
                p.terminate()
                try: p.wait(5)
                except Exception: p.kill()
        if self.with_audio and self.segs: self.segs[-1]["audio"] = self.audio.end()
        self.elapsed0 += time.time() - self.t_seg

    def pause(self):
        if self.state != "rec": return
        self._seg_stop(); self.state = "paused"

    def resume(self):
        if self.state != "paused": return
        self._seg_start()

    def stop(self):
        if self.state == "rec": self._seg_stop()
        self.state = "idle"

    @staticmethod
    def _video_start(seg):
        try:
            t = open(seg["log"], "rb").read().decode("utf-8", "ignore")
            m = re.search(r"Input #0.*?start: ([0-9.]+)", t, re.S)
            if m:
                v = float(m.group(1))
                if abs(v - seg["launch"]) < 30: return v
        except Exception:
            pass
        return seg["launch"] + 0.25

    def finalize(self, out_path, progress=None):
        """mux each segment with its aligned system audio, then join the segments (copy) into one faststart MP4."""
        ff = ffmpeg_exe(); parts = []; self.offsets = []
        good = [s for s in self.segs if os.path.exists(s["path"]) and os.path.getsize(s["path"]) > 2000]
        if not good: raise RuntimeError("No video was captured.\n\n" + self.log_tail())
        for i, s in enumerate(good):
            if progress: progress(i / (len(good) + 1))
            av = s["path"][:-4] + "_av.mp4"
            if s.get("audio") is not None:
                a = s["audio"]; v0 = self._video_start(s); off = a["t0"] - v0; pcm = a["pcm"]
                self.offsets.append((round(v0 - s["launch"], 3), round(off, 3), round(len(pcm) / SR, 2)))
                if off > 0: pcm = np.concatenate([np.zeros((int(off * SR), 2), np.float32), pcm], 0)
                elif off < 0: pcm = pcm[int(-off * SR):]
                vd = probe(s["path"])[0] or len(pcm) / SR; n = int(round(vd * SR))
                pcm = pcm[:n] if len(pcm) >= n else np.concatenate([pcm, np.zeros((n - len(pcm), 2), np.float32)], 0)
                wav = s["path"][:-4] + ".wav"; write_wav(wav, pcm)
                cmd = [ff, "-hide_banner", "-loglevel", "error", "-y", "-i", s["path"], "-i", wav, "-map", "0:v", "-map", "1:a",
                       "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-ar", str(SR), "-ac", "2", av]
            else:
                vd = probe(s["path"])[0] or 1.0
                cmd = [ff, "-hide_banner", "-loglevel", "error", "-y", "-i", s["path"], "-f", "lavfi", "-t", "%.3f" % vd, "-i",
                       "anullsrc=channel_layout=stereo:sample_rate=%d" % SR, "-map", "0:v", "-map", "1:a",
                       "-c:v", "copy", "-c:a", "aac", "-b:a", "128k", av]
            r = subprocess.run(cmd, capture_output=True, creationflags=NO_WINDOW, timeout=600)
            if r.returncode != 0 or not os.path.exists(av): raise RuntimeError("ffmpeg (mux) failed:\n" + r.stderr.decode("utf-8", "ignore")[-500:])
            parts.append(av)
        lst = os.path.join(self.tmp, "parts.txt")
        with open(lst, "w", encoding="utf-8") as f:
            for p in parts: f.write("file '%s'\n" % p.replace("\\", "/").replace("'", "'\\''"))
        cmd = [ff, "-hide_banner", "-loglevel", "error", "-y", "-f", "concat", "-safe", "0", "-i", lst, "-c", "copy",
               "-movflags", "+faststart", out_path]
        r = subprocess.run(cmd, capture_output=True, creationflags=NO_WINDOW, timeout=600)
        if r.returncode != 0 or not os.path.exists(out_path): raise RuntimeError("ffmpeg (join) failed:\n" + r.stderr.decode("utf-8", "ignore")[-500:])
        if progress: progress(1.0)
        shutil.rmtree(self.tmp, ignore_errors=True); self.tmp = None
        return out_path

    def discard(self):
        if self.tmp: shutil.rmtree(self.tmp, ignore_errors=True); self.tmp = None


# ============================================================================ global hotkeys
class Hotkeys:
    """RegisterHotKey on a message-loop thread: Ctrl+Shift+R start/stop, Ctrl+Shift+F9 pause/resume (Windows only)."""
    def __init__(self, table):
        self.table = table; self.tid = None; self.ok = {}
        if os.name == "nt": threading.Thread(target=self._loop, daemon=True).start()

    def _loop(self):
        import ctypes
        from ctypes import wintypes
        u, k = ctypes.windll.user32, ctypes.windll.kernel32
        self.tid = k.GetCurrentThreadId()
        for hid, (mods, vk, cb) in self.table.items():
            self.ok[hid] = bool(u.RegisterHotKey(None, hid, mods | 0x4000, vk))
        msg = wintypes.MSG()
        while u.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            if msg.message == 0x0312 and msg.wParam in self.table:
                try: self.table[msg.wParam][2]()
                except Exception: pass
        for hid in self.table: u.UnregisterHotKey(None, hid)

    def close(self):
        if self.tid:
            try:
                import ctypes; ctypes.windll.user32.PostThreadMessageW(self.tid, 0x0012, 0, 0)
            except Exception:
                pass


# ============================================================================ UI
def _ico(name, size=16, color=(242, 242, 244)):
    S = size * 4; im = Image.new("RGBA", (S, S), (0, 0, 0, 0)); d = ImageDraw.Draw(im); c = tuple(color) + (255,)
    if name == "rec": d.ellipse((S * .18, S * .18, S * .82, S * .82), fill=c)
    elif name == "stop": d.rounded_rectangle((S * .22, S * .22, S * .78, S * .78), radius=S * .08, fill=c)
    elif name == "pause":
        d.rectangle((S * .24, S * .18, S * .42, S * .82), fill=c); d.rectangle((S * .58, S * .18, S * .76, S * .82), fill=c)
    elif name == "play": d.polygon([(S * .28, S * .16), (S * .84, S * .5), (S * .28, S * .84)], fill=c)
    elif name == "folder":
        d.polygon([(S * .08, S * .24), (S * .4, S * .24), (S * .48, S * .34), (S * .92, S * .34), (S * .92, S * .84), (S * .08, S * .84)], fill=c)
    elif name == "art":
        d.rounded_rectangle((S * .08, S * .16, S * .92, S * .84), radius=S * .08, outline=c, width=max(2, S // 12))
        d.polygon([(S * .16, S * .76), (S * .4, S * .46), (S * .58, S * .66), (S * .68, S * .56), (S * .84, S * .76)], fill=c)
    elif name == "trash":
        d.rectangle((S * .14, S * .18, S * .86, S * .28), fill=c); d.rectangle((S * .4, S * .08, S * .6, S * .18), fill=c)
        d.polygon([(S * .22, S * .32), (S * .78, S * .32), (S * .72, S * .92), (S * .28, S * .92)], fill=c)
    elif name == "region":
        w = max(2, S // 12)
        for (a, b) in (((.1, .1), (.36, .1)), ((.1, .1), (.1, .36)), ((.9, .9), (.64, .9)), ((.9, .9), (.9, .64)),
                       ((.9, .1), (.64, .1)), ((.9, .1), (.9, .36)), ((.1, .9), (.36, .9)), ((.1, .9), (.1, .64))):
            d.line((a[0] * S, a[1] * S, b[0] * S, b[1] * S), fill=c, width=w)
    elif name == "refresh":
        d.arc((S * .16, S * .16, S * .84, S * .84), 30, 320, fill=c, width=max(2, S // 10))
        d.polygon([(S * .66, S * .06), (S * .92, S * .3), (S * .6, S * .36)], fill=c)
    return ImageTk.PhotoImage(im.resize((size, size), Image.LANCZOS))


class ScreenRecPanel(tk.Frame):
    def __init__(self, master, app, ui):
        super().__init__(master, bg=ui.BG)
        self.app, self.ui = app, ui
        self.audio = SystemAudio(); self.eng = Engine(self.audio)
        self.icons = {}; self.thumbs = {}; self.busy = False; self.floater = None; self.cd_win = None
        self.region = None; self.monitors = list_monitors(); self.windows = []
        self.cfg = self._load_cfg()
        self.recent = [r for r in self.cfg.get("recent", []) if os.path.exists(r.get("path", ""))]
        self._build()
        self.hk = Hotkeys({0xEC6: (0x0002 | 0x0004, 0x52, lambda: self.after(0, self.app.route_rec_hotkey)),
                           0xEC7: (0x0002 | 0x0004, 0x78, lambda: self.after(0, self.toggle_pause))})
        self.after(1200, self._hk_label)
        self.audio.start()
        self._tick()
        self.refresh_recent()

    # ------------------------------------------------------------------ config
    def _cfg_path(self): return os.path.join(data_dir(), "screen_recorder.json")

    def _load_cfg(self):
        try: return json.load(open(self._cfg_path(), encoding="utf-8"))
        except Exception: return {}

    def _save_cfg(self):
        c = {"source": self.src_var.get(), "quality": self.q_var.get(), "audio": self.aud_var.get(), "countdown": self.cd_var.get(),
             "minimise": self.min_var.get(), "floating": self.fl_var.get(), "folder": self.dir_var.get(),
             "region": self.region, "recent": self.recent[:40]}
        try: json.dump(c, open(self._cfg_path(), "w", encoding="utf-8"), indent=1)
        except Exception: pass

    # ------------------------------------------------------------------ layout
    def I(self, n, s=16, c=(242, 242, 244)):
        k = (n, s, c)
        if k not in self.icons: self.icons[k] = _ico(n, s, c)
        return self.icons[k]

    def _card(self, parent, title):
        ui = self.ui
        f = tk.Frame(parent, bg=ui.PANEL, highlightthickness=1, highlightbackground=ui.BORDER)
        tk.Label(f, text=title, bg=ui.PANEL, fg=ui.MUT, font=(ui.FONT, 8, "bold"), anchor="w").pack(fill="x", padx=14, pady=(12, 6))
        return f

    def _seg(self, parent, var, opts, cmd=None):
        ui = self.ui; row = tk.Frame(parent, bg=ui.PANEL); btns = {}
        def paint():
            for k, b in btns.items(): b.set_fill(ui.RED if var.get() == k else ui.CARD2, ui.RED2 if var.get() == k else ui.BORDER)
        for k, lab in opts:
            b = ui.Btn(row, lab, None, font=(ui.FONT, 9, "bold"), padx=12, pady=5)
            b.cmd = (lambda k=k: (var.set(k), paint(), cmd and cmd(), self._save_cfg())); b.pack(side="left", padx=(0, 4)); btns[k] = b
        paint(); row.paint = paint
        var.trace_add("write", lambda *a: paint())
        return row

    def _chk(self, parent, text, var):
        ui = self.ui
        return tk.Checkbutton(parent, text=text, variable=var, command=self._save_cfg, bg=ui.PANEL, fg=ui.TXT, selectcolor=ui.CARD2,
                              activebackground=ui.PANEL, activeforeground=ui.TXT, font=(ui.FONT, 9), bd=0, highlightthickness=0, anchor="w")

    def _build(self):
        ui = self.ui; F = ui.FONT; B = ui.Btn; BG, P = ui.BG, ui.PANEL; cfg = self.cfg
        # ---- header / transport
        top = tk.Frame(self, bg=BG); top.pack(fill="x", padx=16, pady=(14, 8))
        tk.Label(top, text=NAME, bg=BG, fg=ui.TXT, font=(F, 16, "bold")).pack(side="left")
        tk.Label(top, text="   Records your screen to a YouTube-ready MP4. Nothing is uploaded.", bg=BG, fg=ui.MUT, font=(F, 9)).pack(side="left", pady=(6, 0))
        bar = tk.Frame(self, bg=P, highlightthickness=1, highlightbackground=ui.BORDER); bar.pack(fill="x", padx=16, pady=(0, 10))
        self.b_rec = B(bar, "  Start recording", self.toggle, fill=ui.RED, fg="white", hover=ui.RED2, font=(F, 11, "bold"), padx=18, pady=10,
                       image=self.I("rec", 16, (255, 255, 255)), compound="left")
        self.b_rec.pack(side="left", padx=12, pady=12)
        self.b_pause = B(bar, "  Pause", self.toggle_pause, font=(F, 10, "bold"), padx=14, pady=9, image=self.I("pause"), compound="left")
        self.b_pause.pack(side="left", pady=12); self.b_pause.set_enabled(False)
        tf = tk.Frame(bar, bg=P); tf.pack(side="left", padx=22)
        self.t_lbl = tk.Label(tf, text="00:00", bg=P, fg=ui.TXT, font=(F, 24, "bold")); self.t_lbl.pack(anchor="w")
        self.st_lbl = tk.Label(tf, text="Ready", bg=P, fg=ui.MUT, font=(F, 9, "bold")); self.st_lbl.pack(anchor="w")
        self.hk_lbl = tk.Label(bar, text="", bg=P, fg=ui.DIM, font=(F, 8), justify="right"); self.hk_lbl.pack(side="right", padx=14)
        self.prog = ttk.Progressbar(bar, style="red.Horizontal.TProgressbar", length=160, mode="determinate", maximum=1.0)
        # ---- body
        body = tk.Frame(self, bg=BG); body.pack(fill="both", expand=True, padx=16, pady=(0, 10))
        left = tk.Frame(body, bg=BG, width=470); left.pack(side="left", fill="y"); left.pack_propagate(False)
        right = tk.Frame(body, bg=BG); right.pack(side="left", fill="both", expand=True, padx=(10, 0))
        # source
        c = self._card(left, "WHAT TO RECORD"); c.pack(fill="x")
        self.src_var = tk.StringVar(value=cfg.get("source", "desktop"))
        self.seg_src = self._seg(c, self.src_var, [("desktop", "Full screen"), ("monitor", "Monitor"), ("window", "Window"), ("region", "Region")],
                                 self._src_changed)
        self.seg_src.pack(fill="x", padx=14)
        self.src_detail = tk.Frame(c, bg=P); self.src_detail.pack(fill="x", padx=14, pady=(8, 12))
        self.mon_var = tk.StringVar(value=self.monitors[0][0] if self.monitors else "")
        self.win_var = tk.StringVar(value="")
        # quality
        c = self._card(left, "QUALITY"); c.pack(fill="x", pady=(10, 0))
        self.q_var = tk.StringVar(value=cfg.get("quality", "1080p30") if cfg.get("quality") in QUALITY else "1080p30")
        self._seg(c, self.q_var, [("720p30", "720p 30 fps"), ("1080p30", "1080p 30 fps"), ("1080p60", "1080p 60 fps")]).pack(fill="x", padx=14)
        tk.Label(c, text="H.264 High + AAC 48 kHz in MP4 (+faststart) \u00b7 ready to upload as it is. Smaller sources are never upscaled.",
                 bg=P, fg=ui.DIM, font=(F, 8), anchor="w", wraplength=430, justify="left").pack(fill="x", padx=14, pady=(6, 12))
        # audio
        c = self._card(left, "SOUND"); c.pack(fill="x", pady=(10, 0))
        r = tk.Frame(c, bg=P); r.pack(fill="x", padx=14)
        self.aud_var = tk.BooleanVar(value=cfg.get("audio", True))
        self._chk(r, "Record system audio (what you hear)", self.aud_var).pack(side="left")
        self.meter = tk.Canvas(c, height=14, bg=ui.CARD, highlightthickness=0); self.meter.pack(fill="x", padx=14, pady=(8, 2))
        self.aud_lbl = tk.Label(c, text="Starting system audio\u2026", bg=P, fg=ui.DIM, font=(F, 8), anchor="w", wraplength=430, justify="left")
        self.aud_lbl.pack(fill="x", padx=14, pady=(0, 12))
        # options
        c = self._card(left, "OPTIONS"); c.pack(fill="x", pady=(10, 0))
        self.cd_var = tk.BooleanVar(value=cfg.get("countdown", True)); self.min_var = tk.BooleanVar(value=cfg.get("minimise", True))
        self.fl_var = tk.BooleanVar(value=cfg.get("floating", True))
        self._chk(c, "3-2-1 countdown before recording", self.cd_var).pack(fill="x", padx=14)
        self._chk(c, "Minimise this window while recording", self.min_var).pack(fill="x", padx=14)
        self._chk(c, "Small floating Stop / Pause button", self.fl_var).pack(fill="x", padx=14)
        r = tk.Frame(c, bg=P); r.pack(fill="x", padx=14, pady=(8, 12))
        tk.Label(r, text="Save to", bg=P, fg=ui.MUT, font=(F, 9)).pack(side="left", padx=(0, 8))
        self.dir_var = tk.StringVar(value=cfg.get("folder", "videos"))
        sg = self._seg(r, self.dir_var, [("videos", "Videos"), ("downloads", "Downloads")]); sg.configure(bg=P); sg.pack(side="left")
        B(r, "", self.open_folder, padx=8, pady=5, image=self.I("folder"), font=(F, 9, "bold")).pack(side="right")
        # ---- recent
        c = self._card(right, "RECENT RECORDINGS"); c.pack(fill="both", expand=True)
        self.lc = tk.Canvas(c, bg=ui.CARD, highlightthickness=0); self.lc.pack(fill="both", expand=True, padx=14)
        self.lc.bind("<Button-1>", self._lc_click); self.lc.bind("<Double-Button-1>", lambda e: self.open_sel())
        self.lc.bind("<MouseWheel>", lambda e: self.lc.yview_scroll(-1 if e.delta > 0 else 1, "units"))
        self.lc.bind("<Configure>", lambda e: self.draw_recent())
        self.sel = None
        br = tk.Frame(c, bg=P); br.pack(fill="x", padx=14, pady=(8, 4))
        self.b_open = B(br, "  Open", self.open_sel, font=(F, 9, "bold"), padx=10, pady=5, image=self.I("play"), compound="left"); self.b_open.pack(side="left")
        self.b_show = B(br, "  Show in folder", self.show_sel, font=(F, 9, "bold"), padx=10, pady=5, image=self.I("folder"), compound="left"); self.b_show.pack(side="left", padx=6)
        self.b_del = B(br, "  Delete", self.delete_sel, font=(F, 9, "bold"), padx=10, pady=5, hover="#5a1f1f", image=self.I("trash"), compound="left"); self.b_del.pack(side="right")
        fr = tk.Frame(c, bg=P); fr.pack(fill="x", padx=14, pady=(4, 12))
        tk.Label(fr, text="Frame", bg=P, fg=ui.MUT, font=(F, 9)).pack(side="left")
        self.fr_var = tk.DoubleVar(value=0.0)
        self.fr_scale = ttk.Scale(fr, from_=0, to=1, variable=self.fr_var, command=lambda v: self._frame_lbl()); self.fr_scale.pack(side="left", fill="x", expand=True, padx=8)
        self.fr_lbl = tk.Label(fr, text="00:00", bg=P, fg=ui.MUT, font=(F, 9), width=6); self.fr_lbl.pack(side="left")
        self.b_art = B(fr, "  Send frame to EX ART", self.send_frame, fill=ui.RED, fg="white", hover=ui.RED2, font=(F, 9, "bold"), padx=10, pady=5,
                       image=self.I("art", 16, (255, 255, 255)), compound="left"); self.b_art.pack(side="right", padx=(8, 0))
        self._src_changed(); self._sel_buttons()

    def _src_changed(self):
        ui = self.ui; P = ui.PANEL; F = ui.FONT
        for w in self.src_detail.winfo_children(): w.destroy()
        k = self.src_var.get()
        if k == "desktop":
            vs = virtual_screen()
            t = "All monitors (%d \u00d7 %d)" % (vs[2], vs[3]) if vs else "The whole screen"
            tk.Label(self.src_detail, text=t, bg=P, fg=ui.TXT, font=(F, 9), anchor="w").pack(fill="x")
        elif k == "monitor":
            if not self.monitors: self.monitors = list_monitors()
            vals = [m[0] for m in self.monitors] or ["(no monitors found)"]
            if self.mon_var.get() not in vals: self.mon_var.set(vals[0])
            ttk.Combobox(self.src_detail, textvariable=self.mon_var, values=vals, state="readonly", font=(F, 9)).pack(fill="x")
        elif k == "window":
            row = tk.Frame(self.src_detail, bg=P); row.pack(fill="x")
            self.win_cb = ttk.Combobox(row, textvariable=self.win_var, state="readonly", font=(F, 9)); self.win_cb.pack(side="left", fill="x", expand=True)
            ui.Btn(row, "", self._refresh_windows, padx=8, pady=4, image=self.I("refresh")).pack(side="left", padx=(6, 0))
            tk.Label(self.src_detail, text="The window must stay open and not minimised. Some hardware-accelerated windows record black; use Region then.",
                     bg=P, fg=ui.DIM, font=(F, 8), anchor="w", wraplength=430, justify="left").pack(fill="x", pady=(4, 0))
            self._refresh_windows()
        elif k == "region":
            row = tk.Frame(self.src_detail, bg=P); row.pack(fill="x")
            ui.Btn(row, "  Pick region\u2026", self.pick_region, font=(F, 9, "bold"), padx=10, pady=5, image=self.I("region"), compound="left").pack(side="left")
            self.reg_lbl = tk.Label(row, text="", bg=P, fg=ui.TXT, font=(F, 9)); self.reg_lbl.pack(side="left", padx=10)
            self.region = self.region or self.cfg.get("region"); self._reg_text()
        self._save_cfg() if hasattr(self, "dir_var") else None

    def _reg_text(self):
        if getattr(self, "reg_lbl", None) is not None and self.reg_lbl.winfo_exists():
            r = self.region
            self.reg_lbl.configure(text=("%d \u00d7 %d at %d, %d" % (r[2], r[3], r[0], r[1])) if r else "Drag a rectangle on your screen")

    def _refresh_windows(self):
        own = set()
        try: own.add(int(self.app.frame(), 16))
        except Exception: pass
        self.windows = list_windows(own)
        vals = [t for t, h in self.windows] or ["(no windows found)"]
        self.win_cb.configure(values=vals)
        if self.win_var.get() not in vals: self.win_var.set(vals[0])

    def _hk_label(self):
        ok = self.hk.ok
        if os.name != "nt": self.hk_lbl.configure(text="Global hotkeys work on Windows"); return
        a = "Ctrl+Shift+R  start / stop" if (ok.get(0xEC6) or getattr(getattr(self.app, "studio", None), "hotkey", None) is not None) else "Ctrl+Shift+R  unavailable (used by another app)"
        b = "Ctrl+Shift+F9  pause / resume" if ok.get(0xEC7) else "Ctrl+Shift+F9  unavailable"
        self.hk_lbl.configure(text=a + "\n" + b + "\nwork while minimised")

    # ------------------------------------------------------------------ region picker
    def pick_region(self):
        vs = virtual_screen() or (0, 0, self.winfo_screenwidth(), self.winfo_screenheight())
        t = tk.Toplevel(self); t.overrideredirect(True); t.geometry("%dx%d%+d%+d" % (vs[2], vs[3], vs[0], vs[1]))
        try: t.attributes("-alpha", 0.35); t.attributes("-topmost", True)
        except Exception: pass
        cv = tk.Canvas(t, bg="#000000", highlightthickness=0, cursor="crosshair"); cv.pack(fill="both", expand=True)
        cv.create_text(vs[2] // 2, 60, text="Drag to choose the area to record \u00b7 Esc cancels", fill="white", font=(self.ui.FONT, 16, "bold"))
        st = {}
        def press(e): st["p"] = (e.x_root, e.y_root, e.x, e.y)
        def drag(e):
            if "p" not in st: return
            cv.delete("r"); _, _, x0, y0 = st["p"]
            cv.create_rectangle(x0, y0, e.x, e.y, outline="#ff3b4a", width=3, tags="r")
            cv.create_text((x0 + e.x) // 2, (y0 + e.y) // 2, text="%d \u00d7 %d" % (abs(e.x - x0), abs(e.y - y0)), fill="white", font=(self.ui.FONT, 14, "bold"), tags="r")
        def release(e):
            if "p" not in st: return
            X0, Y0, _, _ = st["p"]; x0, x1 = sorted((X0, e.x_root)); y0, y1 = sorted((Y0, e.y_root))
            t.destroy()
            if x1 - x0 >= 64 and y1 - y0 >= 64:
                self.region = (x0, y0, x1 - x0, y1 - y0); self._reg_text(); self._save_cfg()
        cv.bind("<ButtonPress-1>", press); cv.bind("<B1-Motion>", drag); cv.bind("<ButtonRelease-1>", release)
        t.bind("<Escape>", lambda e: t.destroy()); t.focus_force()

    # ------------------------------------------------------------------ recording
    def is_active(self):
        return self.eng.state in ("rec", "paused") or self.cd_win is not None

    def _source(self):
        k = self.src_var.get()
        if k == "monitor":
            m = [r for lab, r in self.monitors if lab == self.mon_var.get()]
            if not m: raise RuntimeError("Choose a monitor first.")
            return {"kind": "monitor", "rect": m[0]}
        if k == "window":
            t = self.win_var.get()
            if not t or t.startswith("("): raise RuntimeError("Choose a window first (press the refresh button if it's missing).")
            return {"kind": "window", "title": t}
        if k == "region":
            if not self.region: raise RuntimeError("Press 'Pick region\u2026' and drag the area to record first.")
            return {"kind": "region", "rect": tuple(self.region)}
        if os.name != "nt":
            return {"kind": "desktop", "rect": (0, 0, self.winfo_screenwidth(), self.winfo_screenheight())}
        return {"kind": "desktop"}

    def toggle(self):
        if self.busy: return
        if self.cd_win is not None: self._cd_cancel(); return
        if self.eng.state in ("rec", "paused"): self.stop()
        else: self.start()

    def start(self):
        try: src = self._source()
        except Exception as e:
            messagebox.showinfo(NAME, str(e), parent=self.app); return
        self._save_cfg(); self._src = src
        if self.min_var.get(): self.app.iconify()
        if self.cd_var.get(): self.after(250 if self.min_var.get() else 0, lambda: self._countdown(3))
        else: self.after(400 if self.min_var.get() else 0, self._go)

    def _countdown(self, n):
        if n == 3 or self.cd_win is None:
            t = tk.Toplevel(self); t.overrideredirect(True)
            try: t.attributes("-topmost", True); t.attributes("-alpha", 0.92)
            except Exception: pass
            S = 220; r = self._src.get("rect") or (virtual_screen() or (0, 0, self.winfo_screenwidth(), self.winfo_screenheight()))
            if self._src["kind"] == "desktop" and self.monitors: r = self.monitors[0][1]
            t.geometry("%dx%d%+d%+d" % (S, S, r[0] + (r[2] - S) // 2, r[1] + (r[3] - S) // 2))
            cv = tk.Canvas(t, width=S, height=S, bg=self.ui.PANEL, highlightthickness=0); cv.pack()
            t.cv = cv; self.cd_win = t; exclude_from_capture(t)
            t.bind("<Escape>", lambda e: self._cd_cancel())
        t = self.cd_win; cv = t.cv; cv.delete("all"); S = 220
        self.st_lbl.configure(text="Get ready\u2026 recording in %d" % n, fg=self.ui.AMBER)
        cv.create_oval(16, 16, S - 16, S - 16, outline=self.ui.RED, width=6)
        cv.create_text(S // 2, S // 2 - 8, text=str(n), fill="white", font=(self.ui.FONT, 72, "bold"))
        cv.create_text(S // 2, S - 46, text="Recording starts", fill=self.ui.MUT, font=(self.ui.FONT, 10, "bold"))
        if n > 1: self._cd_job = self.after(1000, lambda: self._countdown(n - 1))
        else: self._cd_job = self.after(1000, self._cd_done)

    def _cd_done(self):
        if self.cd_win is not None: self.cd_win.destroy(); self.cd_win = None
        self.after(120, self._go)

    def _cd_cancel(self):
        try: self.after_cancel(self._cd_job)
        except Exception: pass
        if self.cd_win is not None: self.cd_win.destroy(); self.cd_win = None
        self.app.deiconify(); self.st_lbl.configure(text="Cancelled", fg=self.ui.MUT)

    def _go(self):
        try:
            self.eng.start(self._src, self.q_var.get(), bool(self.aud_var.get()) and self.audio.ok)
        except Exception as e:
            self.app.deiconify(); messagebox.showerror(NAME, "Recording couldn't start:\n\n%s" % e, parent=self.app); return
        self.b_rec.configure(text="  Stop recording", image=self.I("stop", 16, (255, 255, 255))); self.b_pause.set_enabled(True)
        if self.fl_var.get(): self._floater()
        self.after(1800, self._check_alive)

    def _check_alive(self):
        if self.eng.state == "rec" and not self.eng.alive():
            err = self.eng.log_tail(); self.eng.state = "idle"; self.eng.discard(); self.audio.end()
            self._reset_ui(); self.app.deiconify()
            messagebox.showerror(NAME, "The recorder stopped unexpectedly:\n\n" + err[-500:], parent=self.app)

    def toggle_pause(self):
        if self.eng.state == "rec": self.eng.pause()
        elif self.eng.state == "paused": self.eng.resume(); self.after(1800, self._check_alive)
        else: return
        self._paint_pause()

    def _paint_pause(self):
        p = self.eng.state == "paused"
        self.b_pause.configure(text="  Resume" if p else "  Pause", image=self.I("play" if p else "pause"))
        if self.floater is not None: self.floater.bp.configure(image=self.I("play" if p else "pause"))

    def stop(self):
        if self.eng.state not in ("rec", "paused"): return
        self.busy = True; self._close_floater()
        self.st_lbl.configure(text="Saving\u2026", fg=self.ui.AMBER); self.b_rec.set_enabled(False); self.b_pause.set_enabled(False)
        self.prog.pack(side="right", padx=10); self.prog["value"] = 0
        self.update_idletasks()
        folder = videos_dir() if self.dir_var.get() == "videos" else downloads_dir()
        out = os.path.join(folder, "%s %s.mp4" % (NAME, time.strftime("%Y-%m-%d %H-%M-%S")))
        res = {}
        def work():
            try:
                self.eng.stop(); res["path"] = self.eng.finalize(out, progress=lambda f: res.__setitem__("p", f))
            except Exception as e:
                res["err"] = str(e)
        th = threading.Thread(target=work, daemon=True); th.start()
        def poll():
            self.prog["value"] = res.get("p", 0)
            if th.is_alive(): self.after(150, poll); return
            self.busy = False; self.prog.pack_forget(); self._reset_ui()
            try: self.app.deiconify(); self.app.lift()
            except Exception: pass
            if "err" in res:
                self.eng.discard(); messagebox.showerror(NAME, "Couldn't save the recording:\n\n" + res["err"], parent=self.app); return
            p = res["path"]; dur, w, h = probe(p)
            self.recent.insert(0, {"path": p, "dur": dur, "w": w, "h": h, "q": self.q_var.get(), "when": time.strftime("%Y-%m-%d %H:%M")})
            self._save_cfg(); self.sel = 0; self.refresh_recent()
            self.st_lbl.configure(text="Saved %s  (%s, %d \u00d7 %d)" % (os.path.basename(p), fmt_t(dur), w, h), fg=self.ui.GREEN)
        poll()

    def _reset_ui(self):
        self.b_rec.set_enabled(True); self.b_rec.configure(text="  Start recording", image=self.I("rec", 16, (255, 255, 255)))
        self.b_pause.set_enabled(False); self.b_pause.configure(text="  Pause", image=self.I("pause")); self._close_floater()
        self.t_lbl.configure(text="00:00", fg=self.ui.TXT)

    # ------------------------------------------------------------------ floating controls
    def _floater(self):
        ui = self.ui; t = tk.Toplevel(self); t.overrideredirect(True)
        try: t.attributes("-topmost", True)
        except Exception: pass
        t.configure(bg=ui.BORDER); f = tk.Frame(t, bg=ui.PANEL); f.pack(padx=1, pady=1)
        dot = tk.Label(f, image=self.I("rec", 12, (255, 59, 74)), bg=ui.PANEL); dot.pack(side="left", padx=(10, 4), pady=8)
        t.lbl = tk.Label(f, text="00:00", bg=ui.PANEL, fg="white", font=(ui.FONT, 11, "bold"), width=6); t.lbl.pack(side="left")
        t.bp = tk.Label(f, image=self.I("pause"), bg=ui.CARD2, cursor="hand2", padx=8, pady=6); t.bp.pack(side="left", padx=(6, 4), pady=6)
        t.bp.bind("<Button-1>", lambda e: self.toggle_pause())
        bs = tk.Label(f, image=self.I("stop", 16, (255, 255, 255)), bg=ui.RED, cursor="hand2", padx=8, pady=6); bs.pack(side="left", padx=(0, 8), pady=6)
        bs.bind("<Button-1>", lambda e: self.stop())
        mv = {}
        def p(e): mv["o"] = (e.x_root - t.winfo_x(), e.y_root - t.winfo_y())
        def d(e): t.geometry("+%d+%d" % (e.x_root - mv["o"][0], e.y_root - mv["o"][1]))
        for w in (f, dot, t.lbl): w.bind("<ButtonPress-1>", p); w.bind("<B1-Motion>", d)
        r = self.monitors[0][1] if self.monitors else (0, 0, self.winfo_screenwidth(), self.winfo_screenheight())
        t.update_idletasks(); t.geometry("+%d+%d" % (r[0] + r[2] - t.winfo_reqwidth() - 24, r[1] + 24))
        t.excluded = exclude_from_capture(t)
        self.floater = t

    def _close_floater(self):
        if self.floater is not None:
            try: self.floater.destroy()
            except Exception: pass
            self.floater = None

    # ------------------------------------------------------------------ tick: timer + meter
    def _tick(self):
        try:
            e = self.eng; ui = self.ui
            if e.state in ("rec", "paused"):
                el = e.elapsed(); txt = fmt_t(el)
                blink = e.state == "paused" and int(time.time() * 2) % 2
                self.t_lbl.configure(text=txt, fg=ui.MUT if blink else (ui.RED if e.state == "rec" else ui.AMBER))
                self.st_lbl.configure(text="\u25cf REC  %s" % e.q if e.state == "rec" else "PAUSED - press Resume or Ctrl+Shift+F9", fg=ui.RED if e.state == "rec" else ui.AMBER)
                if self.floater is not None: self.floater.lbl.configure(text=txt, fg="#ffb020" if e.state == "paused" else "white")
                try: self.app.title("\u25cf REC %s - %s" % (txt, ui.APP))
                except Exception: pass
            elif not self.busy and self.app.title() != ui.APP:
                try: self.app.title(ui.APP)
                except Exception: pass
            self._meter()
        except Exception:
            pass
        self.after(100, self._tick)

    def _meter(self):
        cv = self.meter; w = max(10, cv.winfo_width()); h = 14; cv.delete("all")
        a = self.audio; on = self.aud_var.get()
        n = 40; lv = 0.0 if not on else min(1.0, (20 * math.log10(max(a.level, 1e-5)) + 60) / 60)
        k = int(round(lv * n)); bw = (w - (n - 1) * 2) / n
        for i in range(n):
            x0 = i * (bw + 2)
            col = ("#2fd27a" if i < n * 0.7 else "#ffb020" if i < n * 0.9 else "#ff3b4a") if i < k else "#2a2a33"
            cv.create_rectangle(x0, 2, x0 + bw, h - 2, fill=col, outline="")
        if not on: txt, col = "System audio is off - the video will have a silent soundtrack.", self.ui.DIM
        elif a.ok: txt, col = "From: %s" % (a.device or "default output"), self.ui.DIM
        elif a.err: txt, col = "System audio isn't available (%s). Recordings will be silent." % a.err[:80], self.ui.AMBER
        else: txt, col = "Starting system audio\u2026", self.ui.DIM
        if self.aud_lbl.cget("text") != txt: self.aud_lbl.configure(text=txt, fg=col)

    # ------------------------------------------------------------------ recent list
    def refresh_recent(self):
        self.recent = [r for r in self.recent if os.path.exists(r["path"])]
        def load():
            for r in list(self.recent):
                p = r["path"]
                if p in self.thumbs: continue
                f = grab_frame(p, min(1.0, r.get("dur", 0) / 2))
                self.thumbs[p] = f
            self.after(0, self.draw_recent)
        threading.Thread(target=load, daemon=True).start()
        self.draw_recent(); self._sel_buttons()

    def draw_recent(self):
        ui = self.ui; cv = self.lc; cv.delete("all"); W = max(200, cv.winfo_width()); RH = 76
        self._tk_thumbs = []
        if not self.recent:
            cv.create_text(W // 2, 60, text="Your recordings will appear here.", fill=ui.MUT, font=(ui.FONT, 10))
            cv.configure(scrollregion=(0, 0, W, 120)); return
        for i, r in enumerate(self.recent):
            y = i * RH; sel = i == self.sel
            cv.create_rectangle(0, y, W, y + RH - 2, fill="#2a1418" if sel else ui.CARD, outline="")
            if sel: cv.create_rectangle(0, y, 3, y + RH - 2, fill=ui.RED, outline="")
            f = self.thumbs.get(r["path"])
            if f is not None:
                th = cv2.resize(f, (112, 63), interpolation=cv2.INTER_AREA); ph = ImageTk.PhotoImage(Image.fromarray(th)); self._tk_thumbs.append(ph)
                cv.create_image(12, y + 6, image=ph, anchor="nw")
            else:
                cv.create_rectangle(12, y + 6, 124, y + 69, fill=ui.CARD2, outline="")
            cv.create_text(138, y + 18, text=os.path.basename(r["path"]), fill=ui.TXT, font=(ui.FONT, 9, "bold"), anchor="w")
            try: mb = os.path.getsize(r["path"]) / 1e6
            except Exception: mb = 0
            cv.create_text(138, y + 40, text="%s  \u00b7  %d \u00d7 %d  \u00b7  %.1f MB  \u00b7  %s" % (fmt_t(r.get("dur", 0)), r.get("w", 0), r.get("h", 0), mb, r.get("q", "")),
                           fill=ui.MUT, font=(ui.FONT, 9), anchor="w")
            cv.create_text(138, y + 58, text="%s  \u00b7  %s" % (r.get("when", ""), os.path.dirname(r["path"])), fill=ui.DIM, font=(ui.FONT, 8), anchor="w")
        cv.configure(scrollregion=(0, 0, W, len(self.recent) * RH))

    def _lc_click(self, e):
        i = int(self.lc.canvasy(e.y) // 76)
        if 0 <= i < len(self.recent): self.sel = i; self.draw_recent(); self._sel_buttons()

    def _cur(self):
        return self.recent[self.sel] if self.sel is not None and 0 <= self.sel < len(self.recent) else None

    def _sel_buttons(self):
        r = self._cur()
        for b in (self.b_open, self.b_show, self.b_del, self.b_art): b.set_enabled(r is not None)
        if r: self.fr_scale.configure(to=max(0.1, r.get("dur", 0))); self.fr_var.set(min(self.fr_var.get(), r.get("dur", 0))); self._frame_lbl()

    def _frame_lbl(self): self.fr_lbl.configure(text=fmt_t(self.fr_var.get()))

    def _startfile(self, p):
        if os.name == "nt": os.startfile(p)
        else: subprocess.Popen(["xdg-open", p])

    def open_sel(self):
        r = self._cur()
        if r: self._startfile(r["path"])

    def show_sel(self):
        r = self._cur()
        if not r: return
        if os.name == "nt": subprocess.Popen(["explorer", "/select,", os.path.normpath(r["path"])])
        else: self._startfile(os.path.dirname(r["path"]))

    def open_folder(self):
        self._startfile(videos_dir() if self.dir_var.get() == "videos" else downloads_dir())

    def delete_sel(self):
        r = self._cur()
        if not r: return
        if not messagebox.askyesno(NAME, "Delete this recording from your computer?\n\n%s" % os.path.basename(r["path"]), parent=self.app): return
        try: os.remove(r["path"])
        except Exception as e:
            messagebox.showerror(NAME, "Couldn't delete it:\n%s" % e, parent=self.app); return
        self.recent.remove(r); self.thumbs.pop(r["path"], None); self.sel = None; self._save_cfg(); self.refresh_recent()

    def send_frame(self):
        r = self._cur()
        if not r: return
        f = grab_frame(r["path"], self.fr_var.get())
        if f is None: messagebox.showerror(NAME, "Couldn't read a frame from that video.", parent=self.app); return
        rgba = np.concatenate([f, np.full(f.shape[:2] + (1,), 255, np.uint8)], 2)
        self.app.show_tab("art"); self.app.update_idletasks()
        self.app.art.place_pixels(rgba, "Frame %s" % fmt_t(self.fr_var.get()).replace(":", "."), label="Frame from recording")

    # ------------------------------------------------------------------ tab life-cycle
    def leave(self): pass

    def close(self):
        if self.eng.state in ("rec", "paused"):
            if not messagebox.askyesno(NAME, "A recording is running. Stop and save it before closing?", parent=self.app): return False
            self.stop()
            while self.busy: self.update(); time.sleep(0.05)
        self.audio.stop(); self.hk.close(); self._save_cfg()
        return True
