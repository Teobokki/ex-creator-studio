"""EX CREATOR STUDIO - one board, any character or art, typed text -> TTS voice + lip sync -> MP4.
(c)2026 EX Inc./HYPERION DIGITAL (c)2026"""
import json, os, re, shutil, subprocess, sys, tempfile, threading, time, urllib.request, webbrowser
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

APP = "EX CREATOR STUDIO"
APP_VERSION = "1.4.0"
COPYRIGHT = "\u00a92026 EX Inc./HYPERION DIGITAL \u00a92026"
REPO = "Teobokki/ex-creator-studio"
UPDATE_URL = "https://raw.githubusercontent.com/%s/main/latest.json" % REPO

BG, PANEL, CARD, CARD2, BORDER = "#0b0b0d", "#121216", "#18181d", "#24242b", "#2c2c34"
TXT, MUT, DIM = "#f2f2f4", "#9a9aa6", "#5d5d66"
RED, RED2, GREEN, AMBER = "#FF4545", "#ff6a6a", "#3ddc84", "#ffb020"
BOARD_RGB = (24, 24, 29)
FONT = "Segoe UI"
SHOT_TYPES = [("Screenshots", "*.png *.jpg *.jpeg *.webp *.bmp"), ("All files", "*.*")]
IMG_TYPES = [("Images", "*.png *.jpg *.jpeg *.webp *.gif *.bmp"), ("All files", "*.*")]

if os.name == "nt":
    try:
        import ctypes
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass

import numpy as np
from PIL import Image, ImageOps, ImageTk
import ecs_core as core
import ecs_tts as tts


def res_path(*n):
    return core.res_path(*n)


def downloads_dir():
    d = os.path.join(os.path.expanduser("~"), "Downloads")
    if os.name == "nt":
        try:  # real Downloads known folder (may be redirected)
            import ctypes
            from ctypes import wintypes
            class GUID(ctypes.Structure):
                _fields_ = [("a", wintypes.DWORD), ("b", wintypes.WORD), ("c", wintypes.WORD), ("d", ctypes.c_ubyte * 8)]
            g = GUID(0x374DE290, 0x123F, 0x4565, (ctypes.c_ubyte * 8)(0x91, 0x64, 0x39, 0xC4, 0x92, 0x5E, 0x46, 0x7B))
            p = ctypes.c_wchar_p()
            if ctypes.windll.shell32.SHGetKnownFolderPath(ctypes.byref(g), 0, None, ctypes.byref(p)) == 0 and p.value:
                d = p.value
        except Exception:
            pass
    os.makedirs(d, exist_ok=True)
    return d


def open_image_rgb(path):
    """Open png/jpg/webp/gif/bmp as RGB numpy (first GIF frame, EXIF-rotated, transparency on the board colour)."""
    im = Image.open(path)
    try: im.seek(0)
    except Exception: pass
    im = ImageOps.exif_transpose(im)
    if im.mode in ("RGBA", "LA", "P", "PA"):
        im = im.convert("RGBA")
        bg = Image.new("RGBA", im.size, BOARD_RGB + (255,))
        im = Image.alpha_composite(bg, im)
    im = im.convert("RGB")
    if max(im.size) > 2048:
        im.thumbnail((2048, 2048), Image.LANCZOS)
    return np.ascontiguousarray(np.asarray(im))


def vtuple(v):
    return tuple(int(x) for x in re.findall(r"\d+", str(v))[:4]) or (0,)


def check_update():
    try:
        req = urllib.request.Request(UPDATE_URL + "?t=%d" % time.time(), headers={"User-Agent": "EX-CREATOR-STUDIO/" + APP_VERSION})
        with urllib.request.urlopen(req, timeout=10) as r:
            m = json.loads(r.read(65536).decode("utf-8"))
        ver = str(m["version"])
    except Exception as e:
        return "error", "Could not check for updates (%s)." % (getattr(e, "reason", None) or e)
    if vtuple(ver) > vtuple(APP_VERSION):
        return "newer", {"version": ver, "notes": str(m.get("notes", "")),
                         "link": str(m.get("installer_url") or m.get("url", ""))}
    return "latest", "You have the latest version (%s)." % APP_VERSION


class Player:
    """Plays a WAV asynchronously (winsound on Windows, aplay/paplay elsewhere)."""
    def __init__(self):
        self.proc = None

    def play(self, path):
        self.stop()
        if os.name == "nt":
            import winsound
            winsound.PlaySound(path, winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_NODEFAULT)
        else:
            for p in ("paplay", "aplay"):
                if shutil.which(p):
                    self.proc = subprocess.Popen([p, path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    break

    def stop(self):
        if os.name == "nt":
            try:
                import winsound
                winsound.PlaySound(None, 0)
            except Exception:
                pass
        if self.proc and self.proc.poll() is None:
            self.proc.kill()
        self.proc = None


def trash_icon(color=(242, 242, 244), size=16):
    """Small anti-aliased trash-can icon (drawn, so it renders on every Windows/Tk font setup)."""
    from PIL import ImageDraw
    k = 8; S = size * k
    im = Image.new("RGBA", (S, S), (0, 0, 0, 0)); d = ImageDraw.Draw(im); c = tuple(color) + (255,)
    d.rounded_rectangle([S * .38, S * .04, S * .62, S * .16], radius=S * .04, outline=c, width=int(S * .07))
    d.rounded_rectangle([S * .10, S * .14, S * .90, S * .24], radius=S * .04, fill=c)
    d.polygon([(S * .18, S * .30), (S * .82, S * .30), (S * .75, S * .96), (S * .25, S * .96)], fill=c)
    for x in (.37, .50, .63):
        d.rounded_rectangle([S * (x - .035), S * .40, S * (x + .035), S * .86], radius=S * .03, fill=(0, 0, 0, 0))
    return ImageTk.PhotoImage(im.resize((size, size), Image.LANCZOS))


class Btn(tk.Label):
    def __init__(self, master, text, cmd, fill=CARD2, fg=TXT, hover=None, **kw):
        super().__init__(master, text=text, bg=fill, fg=fg, font=kw.pop("font", (FONT, 10, "bold")), cursor="hand2",
                         padx=kw.pop("padx", 14), pady=kw.pop("pady", 7), **kw)
        self.fill, self.hover, self.cmd, self.enabled, self.fg0 = fill, hover or BORDER, cmd, True, fg
        self.bind("<Button-1>", lambda e: self.enabled and self.cmd())
        self.bind("<Enter>", lambda e: self.enabled and self.configure(bg=self.hover))
        self.bind("<Leave>", lambda e: self.configure(bg=self.fill))

    def set_enabled(self, on):
        self.enabled = on
        self.configure(fg=self.fg0 if on else DIM, cursor="hand2" if on else "arrow")

    def set_fill(self, fill, hover=None):
        self.fill = fill; self.hover = hover or self.hover; self.configure(bg=fill)


class Studio(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(APP)
        self.configure(bg=BG)
        self.geometry("1180x860")
        self.minsize(760, 620)
        try: self.iconbitmap(default=res_path("assets", "logo.ico"))
        except Exception: pass
        try:
            self._ic = tk.PhotoImage(file=res_path("assets", "logo256.png")); self.iconphoto(True, self._ic)
        except Exception: pass
        self.tmp = tempfile.mkdtemp(prefix="EXCreatorStudio_")
        self.player = Player()
        self.src = None; self.src_name = ""; self.kind = None; self.mouth = None; self.face = None
        self.wav = None; self.wav_key = None; self.track = None
        self.playing = False; self.t0 = 0.0; self.busy = False
        self.set_mode = False; self.drag0 = None; self.marker_until = 0.0
        self.shots = []; self.sel = None; self.sdrag = None
        self.photo = None; self.disp = None; self.disp_s = 1.0; self.disp_off = (0, 0); self._resize_job = None
        self.voices = []
        self._style()
        self._build()
        self._make_studio()  # built hidden (for the Ctrl+Shift+R hotkey); REX BOARD stays the screen you see
        self.protocol("WM_DELETE_WINDOW", self.on_close)
        threading.Thread(target=self._load_voices, daemon=True).start()

    # ------------------------------------------------------------------ UI
    def _style(self):
        st = ttk.Style(self)
        try: st.theme_use("clam")
        except Exception: pass
        st.configure("TCombobox", fieldbackground=CARD2, background=CARD2, foreground=TXT, arrowcolor=TXT,
                     bordercolor=BORDER, lightcolor=CARD2, darkcolor=CARD2, selectbackground=CARD2, selectforeground=TXT)
        st.map("TCombobox", fieldbackground=[("readonly", CARD2)], foreground=[("readonly", TXT)])
        self.option_add("*TCombobox*Listbox.background", CARD2); self.option_add("*TCombobox*Listbox.foreground", TXT)
        self.option_add("*TCombobox*Listbox.selectBackground", RED)
        st.configure("Horizontal.TScale", background=PANEL, troughcolor=CARD2, bordercolor=PANEL, lightcolor=RED, darkcolor=RED)
        st.configure("red.Horizontal.TProgressbar", troughcolor=CARD2, background=RED, bordercolor=CARD2, lightcolor=RED, darkcolor=RED)

    def _build(self):
        top = tk.Frame(self, bg=PANEL); top.pack(fill="x")
        try:
            lg = Image.open(res_path("assets", "logo64.png")).resize((34, 34), Image.LANCZOS)
            self._logo = ImageTk.PhotoImage(lg)
            tk.Label(top, image=self._logo, bg=PANEL).pack(side="left", padx=(16, 8), pady=10)
        except Exception:
            pass
        tk.Label(top, text="EX CREATOR STUDIO", bg=PANEL, fg=TXT, font=(FONT, 15, "bold")).pack(side="left")
        tk.Label(top, text="  v" + APP_VERSION, bg=PANEL, fg=MUT, font=(FONT, 9)).pack(side="left", pady=(6, 0))
        Btn(top, "\u27f3  Check for updates", self.check_updates, fill=PANEL, fg=MUT, hover=CARD2,
            font=(FONT, 9, "bold")).pack(side="right", padx=12)
        nav = tk.Frame(top, bg=PANEL); nav.pack(side="left", padx=(28, 0), fill="y")
        self.tabs = {}
        for key, label in (("home", "\u2302  REX BOARD"), ("studio", "\u25c9  EX STUDIO"), ("art", "\u270e  EX ART"), ("rec", "\u25cf  EX SCREEN RECORDER 2026")):
            f = tk.Frame(nav, bg=PANEL); f.pack(side="left", padx=2, fill="y")
            l = tk.Label(f, text=label, bg=PANEL, fg=MUT, font=(FONT, 10, "bold"), padx=14, pady=8, cursor="hand2")
            l.pack(side="top", fill="y", expand=True)
            u = tk.Frame(f, bg=PANEL, height=3); u.pack(side="bottom", fill="x")
            l.bind("<Button-1>", lambda e, k=key: self.show_tab(k))
            self.tabs[key] = (l, u)
        tk.Label(self, text=COPYRIGHT, bg=PANEL, fg=DIM, font=(FONT, 8), pady=3).pack(side="bottom", fill="x")
        self.body = tk.Frame(self, bg=BG); self.body.pack(fill="both", expand=True)
        self.home = tk.Frame(self.body, bg=BG)
        self.studio = None
        self.art = None
        self.screenrec = None
        self.last_rec = "rec"
        self.tab = None
        self.show_tab("home")

        bar = tk.Frame(self.home, bg=BG); bar.pack(fill="x", padx=16, pady=(12, 8))
        Btn(bar, "\u2b06  Upload", self.upload, fill=RED, fg="white", hover=RED2).pack(side="left")
        self.b_mouth = Btn(bar, "\u25ce  Set mouth", self.toggle_set_mouth); self.b_mouth.pack(side="left", padx=(8, 0))
        self.b_detect = Btn(bar, "\u263a  Auto-detect", self.auto_detect); self.b_detect.pack(side="left", padx=(8, 0))
        self.b_shot = Btn(bar, "\u25a3  Add screenshot", self.add_screenshots); self.b_shot.pack(side="left", padx=(8, 0))
        self.b_down = Btn(bar, "\u2b07  Download", self.download, fill=CARD2); self.b_down.pack(side="right")
        self._trash = trash_icon()
        self.b_bin = Btn(bar, " Bin", self.bin, fill=CARD2, hover="#5a1f1f", image=self._trash, compound="left")
        self.b_bin.pack(side="right", padx=(0, 8))
        self.chip = tk.Label(bar, text="", bg=BG, fg=MUT, font=(FONT, 9, "bold")); self.chip.pack(side="left", padx=14)

        stat = tk.Frame(self.home, bg=BG); stat.pack(side="bottom", fill="x", padx=16, pady=(0, 6))
        self.status = tk.Label(stat, text="Upload a character or any art from your Downloads to start.", bg=BG, fg=MUT,
                               font=(FONT, 9), anchor="w")
        self.status.pack(side="left", fill="x", expand=True)
        self.prog = ttk.Progressbar(stat, style="red.Horizontal.TProgressbar", length=180, mode="determinate", maximum=1.0)

        bottom = tk.Frame(self.home, bg=PANEL, highlightbackground=BORDER, highlightthickness=1)
        bottom.pack(side="bottom", fill="x", padx=16, pady=(0, 8))
        tk.Label(bottom, text="WHAT SHOULD YOUR CHARACTER SAY?", bg=PANEL, fg=MUT, font=(FONT, 8, "bold"),
                 anchor="w").pack(fill="x", padx=12, pady=(10, 4))
        self.text = tk.Text(bottom, height=3, bg=CARD, fg=TXT, insertbackground=RED, relief="flat", wrap="word",
                            font=(FONT, 12), padx=10, pady=8, highlightthickness=1, highlightbackground=BORDER,
                            highlightcolor=RED, selectbackground=RED)
        self.text.pack(fill="x", padx=12)
        self.text.bind("<Control-Return>", lambda e: (self.speak(), "break")[1])
        row = tk.Frame(bottom, bg=PANEL); row.pack(fill="x", padx=12, pady=10)
        tk.Label(row, text="Voice", bg=PANEL, fg=MUT, font=(FONT, 9)).pack(side="left")
        self.voice = tk.StringVar(value="Loading voices\u2026")
        self.cb = ttk.Combobox(row, textvariable=self.voice, state="readonly", width=34, font=(FONT, 9))
        self.cb.pack(side="left", padx=(6, 16))
        tk.Label(row, text="Speed", bg=PANEL, fg=MUT, font=(FONT, 9)).pack(side="left")
        self.rate = tk.DoubleVar(value=0)
        ttk.Scale(row, from_=-5, to=5, variable=self.rate, length=140).pack(side="left", padx=6)
        self.b_stop = Btn(row, "\u25a0  Stop", self.stop); self.b_stop.pack(side="right")
        self.b_speak = Btn(row, "\u25b6  Speak", self.speak, fill=RED, fg="white", hover=RED2)
        self.b_speak.pack(side="right", padx=(0, 8))
        tk.Label(row, text="Ctrl+Enter", bg=PANEL, fg=DIM, font=(FONT, 8)).pack(side="right", padx=10)

        self.cv = tk.Canvas(self.home, bg=CARD, highlightthickness=1, highlightbackground=BORDER, cursor="arrow")
        self.cv.pack(fill="both", expand=True, padx=16, pady=(0, 10))
        self.cv.bind("<Configure>", lambda e: self._schedule_fit())
        self.cv.bind("<ButtonPress-1>", self._press)
        self.cv.bind("<B1-Motion>", self._drag)
        self.cv.bind("<ButtonRelease-1>", self._release)
        self.cv.bind("<Button-3>", self._right)
        self.bind_all("<MouseWheel>", self._wheel_any)
        self.cv.bind("<Button-4>", lambda e: self._wheel(e, 120))
        self.cv.bind("<Button-5>", lambda e: self._wheel(e, -120))
        self.bind("<Delete>", lambda e: self.tab == "home" and self.focus_get() is not self.text and self.remove_shot())
        self.bind("<Escape>", lambda e: self.set_mouth_mode(False) if self.tab == "home" else (self.studio.escape() if self.tab == "studio" else None))
        self.bind_all("<KeyPress>", lambda e: self.art.key(e) if (self.tab == "art" and self.art is not None) else None, add="+")
        self.bind_all("<KeyRelease>", lambda e: self.art.key_up(e) if (self.tab == "art" and self.art is not None) else None, add="+")
        self._update_chip()

    def _make_studio(self):
        import ecs_studio_ui
        self.studio = ecs_studio_ui.StudioPanel(self.body, self, sys.modules[__name__])
        if self.voices:
            self.studio.set_voices(self.voices)

    def _make_art(self):
        import ecs_art
        self.art = ecs_art.ArtPanel(self.body, self, sys.modules[__name__])

    def _make_rec(self):
        import ecs_screenrec
        self.screenrec = ecs_screenrec.ScreenRecPanel(self.body, self, sys.modules[__name__])

    def route_rec_hotkey(self):
        """Ctrl+Shift+R is shared by the two recorders: the one that is recording, else the one used last."""
        sr, stu = self.screenrec, self.studio
        if sr is not None and sr.is_active(): sr.toggle(); return
        if stu is not None and stu.rec.running(): stu.toggle_record(from_hotkey=True); return
        if self.last_rec == "studio" and stu is not None: stu.toggle_record(from_hotkey=True); return
        if sr is not None: sr.toggle(); return
        if stu is not None: stu.toggle_record(from_hotkey=True)

    def show_tab(self, key):
        """REX BOARD is the default screen; EX STUDIO and EX ART are separate panels built the first time they're opened."""
        if key == self.tab:
            return
        if key == "studio" and self.studio is None:
            self._make_studio()
        if key == "art" and self.art is None:
            self._make_art()
        if key == "rec" and self.screenrec is None:
            self._make_rec()
        if key in ("rec", "studio"):
            self.last_rec = key
        if self.tab == "home":
            self.stop(); self.set_mouth_mode(False); self.home.pack_forget()
        elif self.tab == "studio":
            self.studio.leave(); self.studio.pack_forget()
        elif self.tab == "art":
            self.art.leave(); self.art.pack_forget()
        elif self.tab == "rec":
            self.screenrec.leave(); self.screenrec.pack_forget()
        {"home": self.home, "studio": self.studio, "art": self.art, "rec": self.screenrec}[key].pack(fill="both", expand=True)
        self.tab = key
        for k, (l, u) in self.tabs.items():
            l.configure(fg=TXT if k == key else MUT); u.configure(bg=RED if k == key else PANEL)

    def set_status(self, t, color=MUT):
        self.status.configure(text=t, fg=color)

    def _update_chip(self):
        if self.src is None:
            self.chip.configure(text="", bg=BG)
        elif self.mouth is not None:
            label = {"face": "Character detected (face)", "anime": "Character detected (anime/cartoon)",
                     "manual": "Character (mouth set by you)"}.get(self.kind, "Character")
            self.chip.configure(text="  \u25cf " + label + " \u00b7 lip sync ON  ", fg=GREEN, bg=CARD)
        else:
            self.chip.configure(text="  \u25cf Art \u00b7 no lip sync (use Set mouth to make it talk)  ", fg=AMBER, bg=CARD)
        has = self.src is not None
        self.b_mouth.set_enabled(has); self.b_detect.set_enabled(has); self.b_shot.set_enabled(has)

    # ------------------------------------------------------------------ voices
    def _load_voices(self):
        v = tts.list_voices()
        self.after(0, lambda: self._set_voices(v))

    def _set_voices(self, v):
        self.voices = v
        if v:
            self.cb.configure(values=v); self.voice.set(v[0])
        else:
            self.cb.configure(values=["Default voice"]); self.voice.set("Default voice")
        if self.studio is not None:
            self.studio.set_voices(v)

    def _voice_name(self):
        v = self.voice.get()
        return v if v in self.voices else None

    # ------------------------------------------------------------------ board
    def _schedule_fit(self):
        if self._resize_job:
            self.after_cancel(self._resize_job)
        self._resize_job = self.after(60, self._fit)

    def _fit(self):
        self._resize_job = None
        cv = self.cv
        cw, ch = max(10, cv.winfo_width()), max(10, cv.winfo_height())
        cv.delete("all")
        if self.src is None:
            self.disp = None
            x, y = cw / 2, ch / 2 - 42  # simple "picture" glyph
            cv.create_rectangle(x - 36, y - 26, x + 36, y + 26, outline=DIM, width=3)
            cv.create_polygon(x - 30, y + 20, x - 8, y - 6, x + 6, y + 8, x + 14, y, x + 30, y + 20, fill=DIM, outline="")
            cv.create_oval(x + 8, y - 20, x + 20, y - 8, fill=RED, outline="")
            cv.create_text(cw / 2, ch / 2 + 20, text="Your board is empty", fill=TXT, font=(FONT, 16, "bold"))
            cv.create_text(cw / 2, ch / 2 + 50, text="Click here or press  \u2b06 Upload  to add a character or any art from Downloads",
                           fill=MUT, font=(FONT, 10))
            return
        h, w = self.src.shape[:2]
        s = min((cw - 24) / w, (ch - 24) / h)
        s = max(0.05, s)
        dw, dh = max(1, int(w * s)), max(1, int(h * s))
        self.disp_s = s
        self.disp_off = ((cw - dw) // 2, (ch - dh) // 2)
        self.disp = np.asarray(Image.fromarray(self.src).resize((dw, dh), Image.LANCZOS if s < 1 else Image.BICUBIC))
        self.photo = ImageTk.PhotoImage(Image.fromarray(self.disp))
        cv.create_image(self.disp_off[0], self.disp_off[1], image=self.photo, anchor="nw", tags="img")
        if self.shots:
            self._show_frame(0)
        self._draw_marker()

    def _disp_mouth(self):
        if self.mouth is None:
            return None
        s = self.disp_s
        return (self.mouth[0] * s, self.mouth[1] * s, self.mouth[2] * s, self.mouth[3] * s)

    def _draw_marker(self):
        self.cv.delete("marker")
        if self.mouth is None or self.disp is None or not (self.set_mode or time.time() < self.marker_until):
            return
        cx, cy, mw, mh = self._disp_mouth()
        ox, oy = self.disp_off
        self.cv.create_oval(ox + cx - mw / 2, oy + cy - mh / 2, ox + cx + mw / 2, oy + cy + mh / 2,
                            outline=RED, width=2, dash=(4, 3), tags="marker")
        self.cv.create_text(ox + cx, oy + cy - mh / 2 - 10, text="mouth", fill=RED, font=(FONT, 8, "bold"), tags="marker")

    def _show_frame(self, op, wf=1.0):
        if self.disp is None or self.photo is None:
            return
        fr = core.render_mouth(self.disp, self._disp_mouth(), op, wf)
        fr = core.draw_shots(fr, self.shots, self.disp_s)  # screenshots sit on top; lip sync is computed underneath
        self.photo.paste(Image.fromarray(fr))
        self._draw_sel()

    # ------------------------------------------------------------------ actions
    def upload(self):
        p = filedialog.askopenfilename(parent=self, title="Choose a character or art", initialdir=downloads_dir(),
                                       filetypes=IMG_TYPES)
        if p:
            self.load_image(p)

    def load_image(self, path):
        try:
            rgb = open_image_rgb(path)
        except Exception as e:
            messagebox.showerror(APP, "Couldn't open that image:\n%s" % e, parent=self); return
        self.stop()
        self.src = rgb
        self.shots = []; self.sel = None
        self.src_name = os.path.basename(path)
        self._invalidate_audio(keep_text=True)
        self.set_mouth_mode(False)
        self.auto_detect(quiet=True)

    def auto_detect(self, quiet=False):
        if self.src is None:
            return
        kind, face, mouth = core.detect_mouth(self.src)
        self.kind, self.face, self.mouth = kind, face, mouth
        self.marker_until = time.time() + 2.5
        self._fit(); self._update_chip()
        self.after(2600, self._draw_marker)
        if mouth is not None:
            self.set_status("%s loaded \u2014 character detected, lip sync is ready. Type below and press Speak." % self.src_name, GREEN)
        else:
            self.set_status("%s loaded \u2014 no face found, shown as art. Use \u25ce Set mouth to make any character talk." % self.src_name, AMBER)

    def toggle_set_mouth(self):
        if self.src is not None:
            self.set_mouth_mode(not self.set_mode)

    def set_mouth_mode(self, on):
        self.set_mode = bool(on) and self.src is not None
        self.cv.configure(cursor="crosshair" if self.set_mode else "arrow")
        self.b_mouth.set_fill(RED if self.set_mode else CARD2, RED2 if self.set_mode else BORDER)
        self.b_mouth.fg0 = "white" if self.set_mode else TXT
        self.b_mouth.configure(fg=self.b_mouth.fg0)
        if self.set_mode:
            self.set_status("Set mouth: click the character's mouth, or drag a box around it.  Right-click = no lip sync (art).  Esc = done.", RED2)
        self._draw_marker()

    def _to_img(self, e):
        ox, oy = self.disp_off
        return (e.x - ox) / self.disp_s, (e.y - oy) / self.disp_s

    def _press(self, e):
        if self.src is None:
            self.upload(); return
        if not self.set_mode:
            self.cv.focus_set()
            i, mode = self._hit(e.x, e.y)
            self.sel = i
            if i is not None:
                sh = self.shots.pop(i); self.shots.append(sh); self.sel = len(self.shots) - 1  # bring to front
                ix, iy = self._to_img(e)
                self.sdrag = (mode, ix - sh["x"], iy - sh["y"], sh["w"], sh["x"])
            else:
                self.sdrag = None
            self._show_frame(0)
            return
        self.drag0 = (e.x, e.y)

    def _drag(self, e):
        if not self.set_mode and self.sdrag and self.sel is not None:
            sh = self.shots[self.sel]; ix, iy = self._to_img(e)
            h, w = self.src.shape[:2]
            if self.sdrag[0] == "move":
                sh["x"] = float(np.clip(ix - self.sdrag[1], 0, w)); sh["y"] = float(np.clip(iy - self.sdrag[2], 0, h))
            else:  # corner handle: width follows the pointer, centre fixed
                sh["w"] = float(np.clip(abs(ix - sh["x"]) * 2, 24, w * 1.5))
            if not self.playing:
                self._show_frame(0)
            return
        if not self.set_mode or not self.drag0:
            return
        self.cv.delete("drag")
        x0, y0 = self.drag0
        self.cv.create_oval(x0, y0, e.x, e.y, outline=RED, width=2, tags="drag")

    def _release(self, e):
        if self.sdrag:
            self.sdrag = None; return
        if not self.set_mode or not self.drag0:
            return
        self.cv.delete("drag")
        x0, y0 = self.drag0; self.drag0 = None
        h, w = self.src.shape[:2]
        if abs(e.x - x0) < 8 and abs(e.y - y0) < 8:
            cx, cy = self._to_img(e)
            mw = self.mouth[2] if self.mouth is not None else max(12.0, min(w, h) * 0.10)
            mh = mw * 0.32
        else:
            ax, ay = (min(x0, e.x) - self.disp_off[0]) / self.disp_s, (min(y0, e.y) - self.disp_off[1]) / self.disp_s
            bx, by = (max(x0, e.x) - self.disp_off[0]) / self.disp_s, (max(y0, e.y) - self.disp_off[1]) / self.disp_s
            cx, cy, mw, mh = (ax + bx) / 2, (ay + by) / 2, max(6.0, bx - ax), max(3.0, by - ay)
        if not (0 <= cx < w and 0 <= cy < h):
            return
        self.mouth = (cx, cy, mw, mh); self.kind = "manual"
        self._update_chip(); self._draw_marker()
        self.set_status("Mouth set \u2014 lip sync ON. Click again to adjust, Esc when done.", GREEN)
        self._preview_wiggle()

    def _right(self, e):
        if not self.set_mode and self.src is not None:
            i, _ = self._hit(e.x, e.y)
            if i is not None:
                self.sel = i; self._show_frame(0)
                m = tk.Menu(self, tearoff=0, bg=CARD2, fg=TXT, activebackground=RED, activeforeground="white")
                m.add_command(label="Bring to front", command=lambda: self._front(i))
                m.add_command(label="Bigger", command=lambda: self._resize_sel(1.15))
                m.add_command(label="Smaller", command=lambda: self._resize_sel(1 / 1.15))
                m.add_separator()
                m.add_command(label="Remove screenshot", command=self.remove_shot)
                m.tk_popup(e.x_root, e.y_root)
            return
        if self.set_mode and self.src is not None:
            self.mouth = None; self.kind = None
            self._update_chip(); self._draw_marker(); self._fit()
            self.set_status("Mouth removed \u2014 this image is treated as art (no lip sync).", AMBER)

    # ------------------------------------------------------------------ screenshots (layers on top of the character)
    def add_screenshots(self):
        if self.src is None:
            messagebox.showinfo(APP, "Upload a character first, then add screenshots around it.", parent=self); return
        ps = filedialog.askopenfilenames(parent=self, title="Add screenshots", initialdir=downloads_dir(), filetypes=SHOT_TYPES)
        added = 0
        for p in self.tk.splitlist(ps) if ps else []:
            try:
                rgb = open_image_rgb(p)
            except Exception as e:
                messagebox.showerror(APP, "Couldn't open %s:\n%s" % (os.path.basename(p), e), parent=self); continue
            if max(rgb.shape[:2]) > 1600:
                im = Image.fromarray(rgb); im.thumbnail((1600, 1600), Image.LANCZOS); rgb = np.asarray(im)
            sw = self.src.shape[1] * 0.35
            x, y = self._chest_pos(len(self.shots), sw * rgb.shape[0] / float(rgb.shape[1]))
            self.shots.append({"rgb": rgb, "x": x, "y": y, "w": sw, "name": os.path.basename(p)})
            added += 1
        if added:
            self.sel = len(self.shots) - 1
            self._show_frame(0)
            self.set_status("Added %d screenshot%s at the chest. Drag to move \u00b7 wheel or corner handle to resize \u00b7 "
                            "right-click or Delete to remove." % (added, "" if added == 1 else "s"), GREEN)

    def _chest_pos(self, n, shot_h=0.0):
        h, w = self.src.shape[:2]
        if self.mouth is not None:
            cx, cy, mw, mh = self.mouth
            fh = self.face[3] if self.face is not None and self.kind != "manual" else mw * 2.8
            x, y = cx, cy + fh * 1.5
        else:
            x, y = w * 0.5, h * 0.70
        k = n % 5  # cascade multiple shots so they don't stack exactly
        x += (k - 2) * w * 0.07 if n else 0; y += k * h * 0.03 if n else 0
        ymax = max(h * 0.5, h - shot_h / 2 - h * 0.03)  # keep the whole screenshot on the board when possible
        return float(np.clip(x, w * 0.1, w * 0.9)), float(np.clip(y, h * 0.1, ymax))

    def _hit(self, ex, ey):
        """Topmost screenshot under canvas point -> (index, 'move'|'resize') or (None, None)."""
        if self.disp is None:
            return None, None
        ox, oy = self.disp_off
        for i in range(len(self.shots) - 1, -1, -1):
            x0, y0, x1, y1 = core.shot_box(self.shots[i], self.disp_s)
            x0, y0, x1, y1 = x0 + ox, y0 + oy, x1 + ox, y1 + oy
            if i == self.sel and abs(ex - x1) <= 9 and abs(ey - y1) <= 9:
                return i, "resize"
            if x0 <= ex <= x1 and y0 <= ey <= y1:
                return i, "move"
        return None, None

    def _draw_sel(self):
        self.cv.delete("sel")
        if self.sel is None or self.sel >= len(self.shots) or self.disp is None or self.playing:
            return
        ox, oy = self.disp_off
        x0, y0, x1, y1 = core.shot_box(self.shots[self.sel], self.disp_s)
        x0, y0, x1, y1 = x0 + ox - 3, y0 + oy - 3, x1 + ox + 3, y1 + oy + 3
        self.cv.create_rectangle(x0, y0, x1, y1, outline=RED, width=2, dash=(6, 3), tags="sel")
        self.cv.create_rectangle(x1 - 6, y1 - 6, x1 + 6, y1 + 6, fill=RED, outline="white", tags="sel")

    def _wheel_any(self, e):
        # Windows sends wheel events to the focused widget; route them to the board when the pointer is over it
        if self.tab != "home" or self.winfo_containing(e.x_root, e.y_root) is not self.cv:
            return
        class P: pass
        q = P(); q.x, q.y, q.delta = e.x_root - self.cv.winfo_rootx(), e.y_root - self.cv.winfo_rooty(), e.delta
        self._wheel(q)

    def _wheel(self, e, delta=None):
        i, _ = self._hit(e.x, e.y)
        if i is None:
            return
        self.sel = i
        self._resize_sel(1.1 if (delta if delta is not None else e.delta) > 0 else 1 / 1.1)

    def _resize_sel(self, f):
        if self.sel is None or self.sel >= len(self.shots):
            return
        sh = self.shots[self.sel]
        sh["w"] = float(np.clip(sh["w"] * f, 24, self.src.shape[1] * 1.5))
        self._show_frame(0)

    def _front(self, i):
        if 0 <= i < len(self.shots):
            self.shots.append(self.shots.pop(i)); self.sel = len(self.shots) - 1; self._show_frame(0)

    def remove_shot(self):
        if self.sel is None or self.sel >= len(self.shots):
            return
        self.shots.pop(self.sel); self.sel = None
        self._show_frame(0)
        self.set_status("Screenshot removed.", MUT)

    def _preview_wiggle(self, i=0):
        if self.playing or self.disp is None:
            return
        n = 24
        if i > n:
            self._show_frame(0); return
        self._show_frame(abs(np.sin(i / n * np.pi * 3)) * 0.8)
        self.after(33, lambda: self._preview_wiggle(i + 1))

    def _text(self):
        return self.text.get("1.0", "end").strip()

    def _invalidate_audio(self, keep_text=True):
        self.player.stop()
        for f in os.listdir(self.tmp):
            try: os.remove(os.path.join(self.tmp, f))
            except Exception: pass
        self.wav = None; self.wav_key = None; self.track = None
        if not keep_text:
            self.text.delete("1.0", "end")

    def _ensure_audio(self, done):
        """Synthesize (in a thread) if the text/voice/speed changed, then call done() on the UI thread."""
        text = self._text()
        if not text:
            messagebox.showinfo(APP, "Type what your character should say in the box under the board.", parent=self)
            return
        key = (text, self.voice.get(), int(round(self.rate.get() * 2)))
        if self.wav and self.wav_key == key and os.path.exists(self.wav):
            done(); return
        self._busy(True, "Generating voice\u2026")
        path = os.path.join(self.tmp, "voice_%d.wav" % int(time.time() * 1000))

        def work():
            try:
                tts.synthesize(text, path, self._voice_name(), key[2])
                s, sr = core.load_wav(path)
                tr = core.lipsync_track(s, sr, core.FPS)
                def ok():
                    self.wav, self.wav_key, self.track = path, key, tr
                    self._busy(False); done()
                self.after(0, ok)
            except Exception as e:
                msg = str(e)
                self.after(0, lambda: (self._busy(False), self.set_status("Voice failed: " + msg, RED2),
                                       messagebox.showerror(APP, msg, parent=self)))
        threading.Thread(target=work, daemon=True).start()

    def _busy(self, on, msg=None):
        self.busy = on
        for b in (self.b_speak, self.b_down, self.b_bin):
            b.set_enabled(not on)
        if msg:
            self.set_status(msg, TXT)

    def speak(self):
        if self.busy:
            return
        self.stop()
        self._ensure_audio(self._play)

    def _play(self):
        self.player.play(self.wav)
        self.playing = True
        self.t0 = time.perf_counter()
        lip = self.mouth is not None and self.disp is not None
        self.set_status("Speaking\u2026" + (" (lip sync)" if lip else " (art \u2014 no lip sync)"), GREEN)
        self._tick()

    def _tick(self):
        if not self.playing:
            return
        op, wd = self.track
        i = int((time.perf_counter() - self.t0) * core.FPS)
        if i >= len(op) + 3:
            self.playing = False
            self._show_frame(0)
            self.set_status("Done. Press \u2b07 Download to save the lip sync video.", MUT)
            return
        if self.mouth is not None:
            self._show_frame(op[min(i, len(op) - 1)], wd[min(i, len(wd) - 1)])
        self.after(12, self._tick)

    def stop(self):
        self.playing = False
        self.player.stop()
        if self.disp is not None:
            self._show_frame(0)

    def bin(self):
        if self.busy:
            return
        self.stop()
        self.set_mouth_mode(False)
        self._invalidate_audio(keep_text=False)
        self.src = None; self.kind = None; self.mouth = None; self.face = None; self.src_name = ""
        self.shots = []; self.sel = None; self.sdrag = None
        self._fit(); self._update_chip()
        self.set_status("Board cleared.", MUT)

    def download(self):
        if self.busy:
            return
        if self.src is None:
            messagebox.showinfo(APP, "Upload a character or art first.", parent=self); return
        self.stop()
        self._ensure_audio(self._export)

    def _export(self):
        out = os.path.join(downloads_dir(), "%s %s.mp4" % (APP, time.strftime("%Y-%m-%d %H-%M-%S")))
        self._busy(True, "Rendering lip sync video\u2026")
        self.prog.configure(value=0); self.prog.pack(side="right")
        src, mouth, wav = self.src, self.mouth, self.wav
        shots = [{"rgb": sh["rgb"], "x": sh["x"], "y": sh["y"], "w": sh["w"]} for sh in self.shots]

        def prog(f):
            self.after(0, lambda: self.prog.configure(value=f))

        def work():
            try:
                core.export_mp4(src, mouth, wav, out, core.FPS, prog, shots)
                self.after(0, lambda: self._exported(out, None))
            except Exception as e:
                msg = str(e)
                self.after(0, lambda: self._exported(out, msg))
        threading.Thread(target=work, daemon=True).start()

    def _exported(self, out, err):
        self._busy(False); self.prog.pack_forget()
        if err:
            self.set_status("Export failed: " + err, RED2)
            messagebox.showerror(APP, "Couldn't save the video:\n" + err, parent=self); return
        self.set_status("Saved: " + out, GREEN)
        if messagebox.askyesno(APP, "Lip sync video saved to:\n\n%s\n\nShow it in its folder?" % out, parent=self):
            if os.name == "nt":
                subprocess.Popen('explorer /select,"%s"' % out)
            else:
                webbrowser.open("file://" + os.path.dirname(out))

    # ------------------------------------------------------------------ updates
    def check_updates(self):
        self.set_status("Checking for updates\u2026", TXT)
        threading.Thread(target=lambda: (lambda r: self.after(0, lambda: self.show_update(*r)))(check_update()),
                         daemon=True).start()

    def show_update(self, st, d):
        if st != "newer":
            self.set_status(d, MUT)
            (messagebox.showinfo if st == "latest" else messagebox.showwarning)(APP, d, parent=self); return
        self.set_status("Update %s available." % d["version"], GREEN)
        if not d["link"].startswith("http"):
            return
        if not messagebox.askyesno(APP, "%s %s is available (you have %s).\n\n%s\n\nDownload and install it now?"
                                   % (APP, d["version"], APP_VERSION, d["notes"]), parent=self):
            return
        dest = os.path.join(tempfile.gettempdir(), os.path.basename(d["link"].split("?")[0]) or "EX.CREATOR.STUDIO.Setup.exe")
        self._busy(True, "Downloading update %s\u2026" % d["version"])
        self.prog.configure(value=0); self.prog.pack(side="right")

        def work():
            try:
                req = urllib.request.Request(d["link"], headers={"User-Agent": "EX-CREATOR-STUDIO/" + APP_VERSION})
                with urllib.request.urlopen(req, timeout=30) as r, open(dest, "wb") as fh:
                    total = int(r.headers.get("Content-Length") or 0); got = 0
                    while True:
                        b = r.read(1 << 16)
                        if not b: break
                        fh.write(b); got += len(b)
                        if total: self.after(0, lambda v=got / total: self.prog.configure(value=v))
                self.after(0, lambda: self._run_installer(dest))
            except Exception as e:
                msg = str(e)
                self.after(0, lambda: (self._busy(False), self.prog.pack_forget(),
                                       self.set_status("Update download failed: " + msg, RED2), webbrowser.open(d["link"])))
        threading.Thread(target=work, daemon=True).start()

    def _run_installer(self, path):
        try:
            if os.name == "nt":
                os.startfile(path)
            else:
                subprocess.Popen([path])
        except Exception as e:
            self._busy(False); self.prog.pack_forget()
            messagebox.showerror(APP, "Couldn't start the installer:\n%s" % e, parent=self); return
        self.on_close()

    def on_close(self):
        self.playing = False
        self.player.stop()
        if self.screenrec is not None:
            if not self.screenrec.close():
                return
        if self.art is not None:
            if not self.art.close():
                return
        if self.studio is not None:
            if not self.studio.close():
                return
        shutil.rmtree(self.tmp, ignore_errors=True)
        self.destroy()


def main():
    app = Studio()  # board starts empty: only an image the user uploads is ever shown
    app.mainloop()


if __name__ == "__main__":
    main()
