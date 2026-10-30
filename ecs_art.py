"""EX ART - layered image editor tab for EX CREATOR STUDIO.  (c)2026 EX Inc./HYPERION DIGITAL (c)2026"""
import colorsys, math, os, sys, tempfile, time, threading
import tkinter as tk
from tkinter import ttk, filedialog, messagebox, simpledialog
import numpy as np
import cv2
from PIL import Image, ImageTk
import ecs_art_core as C
from ecs_art_icons import icon

TOOLS = [  # key, label, shortcut, icon
    ("move", "Move", "V", "move"), ("marquee", "Marquee select", "M", "marquee"), ("lasso", "Lasso select", "L", "lasso"),
    ("selbrush", "Selection brush", "Q", "selbrush"), ("crop", "Crop", "C", "crop"), ("eyedropper", "Eyedropper", "I", "eyedropper"),
    ("brush", "Brush", "B", "brush"), ("pencil", "Pencil", "N", "pencil"), ("eraser", "Eraser", "E", "eraser"),
    ("fill", "Fill bucket", "G", "fill"), ("gradient", "Gradient", "Shift+G", "gradient"), ("text", "Text", "T", "text"),
    ("shape", "Shapes", "U", "shape"), ("smudge", "Smudge", "R", "smudge"), ("blurtool", "Blur / sharpen brush", "Shift+R", "blurtool"),
    ("clone", "Clone stamp", "S", "clone"), ("hand", "Hand (pan) - or hold Space", "H", "hand"), ("zoom", "Zoom", "Z", "zoom"),
]
KEYMAP = {"v": "move", "m": "marquee", "l": "lasso", "q": "selbrush", "c": "crop", "i": "eyedropper", "b": "brush", "n": "pencil",
          "e": "eraser", "g": "fill", "G": "gradient", "t": "text", "u": "shape", "r": "smudge", "R": "blurtool", "s": "clone",
          "h": "hand", "z": "zoom"}
PAINT_TOOLS = ("brush", "pencil", "eraser", "smudge", "blurtool", "clone", "selbrush")


def pictures_dir(ui):
    d = os.path.join(os.path.expanduser("~"), "Pictures")
    if os.name == "nt":
        try:
            import ctypes
            from ctypes import wintypes
            class GUID(ctypes.Structure):
                _fields_ = [("a", wintypes.DWORD), ("b", wintypes.WORD), ("c", wintypes.WORD), ("d", ctypes.c_ubyte * 8)]
            g = GUID(0x33E28130, 0x4E1E, 0x4676, (ctypes.c_ubyte * 8)(0x83, 0x5A, 0x98, 0x39, 0x5C, 0x3B, 0xC3, 0xBB))
            p = ctypes.c_wchar_p()
            if ctypes.windll.shell32.SHGetKnownFolderPath(ctypes.byref(g), 0, None, ctypes.byref(p)) == 0 and p.value:
                d = p.value
        except Exception:
            pass
    if not os.path.isdir(d):
        return ui.downloads_dir()
    return d


class PenPressure:
    """Windows Ink pen pressure: subclass the canvas window and read WM_POINTER pen info. Mouse = pressure 1.0."""
    def __init__(self, widget):
        self.value = 1.0; self.active = False; self.seen = False; self.ok = False; self.dropped = []
        if os.name != "nt":
            return
        try:
            import ctypes
            from ctypes import wintypes
            u = ctypes.windll.user32
            LRESULT = ctypes.c_ssize_t
            WNDPROC = ctypes.WINFUNCTYPE(LRESULT, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)

            class POINTER_INFO(ctypes.Structure):
                _fields_ = [("pointerType", ctypes.c_int), ("pointerId", ctypes.c_uint32), ("frameId", ctypes.c_uint32),
                            ("pointerFlags", ctypes.c_int), ("sourceDevice", wintypes.HANDLE), ("hwndTarget", wintypes.HWND),
                            ("ptPixelLocation", wintypes.POINT), ("ptHimetricLocation", wintypes.POINT),
                            ("ptPixelLocationRaw", wintypes.POINT), ("ptHimetricLocationRaw", wintypes.POINT),
                            ("dwTime", wintypes.DWORD), ("historyCount", ctypes.c_uint32), ("InputData", ctypes.c_int32),
                            ("dwKeyStates", wintypes.DWORD), ("PerformanceCount", ctypes.c_uint64), ("ButtonChangeType", ctypes.c_int)]

            class POINTER_PEN_INFO(ctypes.Structure):
                _fields_ = [("pointerInfo", POINTER_INFO), ("penFlags", ctypes.c_int), ("penMask", ctypes.c_int),
                            ("pressure", ctypes.c_uint32), ("rotation", ctypes.c_uint32), ("tiltX", ctypes.c_int32), ("tiltY", ctypes.c_int32)]
            u.SetWindowLongPtrW.restype = ctypes.c_void_p; u.SetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_void_p]
            u.CallWindowProcW.restype = LRESULT
            u.CallWindowProcW.argtypes = [ctypes.c_void_p, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
            hwnd = widget.winfo_id(); info = POINTER_PEN_INFO(); self_ = self

            sh32 = ctypes.windll.shell32
            sh32.DragQueryFileW.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_wchar_p, ctypes.c_uint]
            sh32.DragQueryPoint.argtypes = [ctypes.c_void_p, ctypes.POINTER(wintypes.POINT)]
            sh32.DragFinish.argtypes = [ctypes.c_void_p]

            def proc(h, msg, wp, lp):
                if msg == 0x0233:                                 # WM_DROPFILES -> queued, handled on the Tk side
                    try:
                        n = sh32.DragQueryFileW(wp, 0xFFFFFFFF, None, 0); files = []
                        for i in range(n):
                            ln = sh32.DragQueryFileW(wp, i, None, 0); buf = ctypes.create_unicode_buffer(ln + 1)
                            sh32.DragQueryFileW(wp, i, buf, ln + 1); files.append(buf.value)
                        pt = wintypes.POINT(); sh32.DragQueryPoint(wp, ctypes.byref(pt)); sh32.DragFinish(wp)
                        self_.dropped.append((files, pt.x, pt.y))
                    except Exception:
                        pass
                    return 0
                if msg in (0x0245, 0x0246, 0x0247):          # WM_POINTERUPDATE / DOWN / UP
                    try:
                        pid = wp & 0xFFFF
                        if u.GetPointerPenInfo(ctypes.c_uint32(pid), ctypes.byref(info)):
                            self_.value = max(0.02, min(1.0, info.pressure / 1024.0)) if msg != 0x0247 else 1.0
                            self_.active = msg != 0x0247; self_.seen = True
                    except Exception:
                        pass
                return u.CallWindowProcW(self_.old, h, msg, wp, lp)
            self.cb = WNDPROC(proc)
            self.old = u.SetWindowLongPtrW(hwnd, -4, ctypes.cast(self.cb, ctypes.c_void_p).value)
            self.ok = bool(self.old)
            if self.ok:
                try: sh32.DragAcceptFiles(wintypes.HWND(hwnd), True)
                except Exception: pass
        except Exception:
            self.ok = False

    def get(self):
        return self.value if (self.seen and self.active) else 1.0


class Slide(tk.Frame):
    """label + slider + value, dark style"""
    def __init__(self, master, ui, label, lo, hi, val, cmd=None, fmt="%d", width=110, release=None, unit=""):
        super().__init__(master, bg=master["bg"])
        self.ui = ui; self.fmt = fmt; self.cmd = cmd; self.unit = unit
        tk.Label(self, text=label, bg=self["bg"], fg=ui.MUT, font=(ui.FONT, 9)).pack(side="left", padx=(0, 4))
        self.var = tk.DoubleVar(value=val)
        self.sc = ttk.Scale(self, from_=lo, to=hi, variable=self.var, length=width, command=self._ch)
        self.sc.pack(side="left")
        self.lbl = tk.Label(self, text="", bg=self["bg"], fg=ui.TXT, font=(ui.FONT, 9, "bold"), width=5, anchor="w")
        self.lbl.pack(side="left", padx=(4, 0)); self._ch()
        if release: self.sc.bind("<ButtonRelease-1>", lambda e: release(self.get()))

    def _ch(self, *_):
        self.lbl.configure(text=(self.fmt % self.var.get()) + self.unit)
        if self.cmd: self.cmd(self.var.get())

    def get(self): return self.var.get()

    def set(self, v): self.var.set(v); self.lbl.configure(text=(self.fmt % v) + self.unit)


class ArtPanel(tk.Frame):
    TOOL_LIST = TOOLS
    PICK_ALL = False        # Move tool auto-select may also pick the bottom layer
    def __init__(self, master, app, ui):
        super().__init__(master, bg=ui.BG)
        self.app, self.ui = app, ui
        self.tmp = tempfile.mkdtemp(prefix="EXArt_")
        self.primary = (0, 0, 0); self.secondary = (255, 255, 255)          # black / white by default
        self.recent = []
        self.tool = "brush"
        self.opt = {"size": 24, "hardness": 0.8, "opacity": 1.0, "flow": 1.0, "pressure": True,
                    "tolerance": 32, "contiguous": True, "sample_all": False, "grad": "linear", "grad_tr": False,
                    "sel_shape": "rect", "sel_op": "new", "feather": 0, "shape": "rect", "shape_fill": True, "stroke": 6,
                    "strength": 0.5, "fx": "blur", "font": None, "font_size": 72, "pencil_size": 2, "eraser_size": 40}
        self.zoom = 1.0; self.ox = 0.0; self.oy = 0.0
        self.photo = None; self._view_job = None; self._ants = []; self._ant_off = 0
        self.space = False; self.drag = None; self.stroke = None; self.clip = None
        self.tf = None; self.crop_rect = None; self.clone_src = None; self.lasso_pts = None
        self.fonts = None; self.tool_btns = {}; self.icons = {}
        self.edit_mask = False
        self.mouse_doc = (0, 0)
        self._build()
        self.new_doc(1920, 1080, (255, 255, 255, 255), push=False)      # white background by default
        self.set_tool("brush")
        self.after(150, self._ants_tick)

    # ================================================================== layout
    def _build(self):
        ui = self.ui; B = ui.Btn; F = ui.FONT
        P, BG, CARD, CARD2, BORDER = ui.PANEL, ui.BG, ui.CARD, ui.CARD2, ui.BORDER
        # --- menu row
        top = tk.Frame(self, bg=BG); top.pack(fill="x", padx=12, pady=(8, 4))
        for name, fn in (("File", self._m_file), ("Edit", self._m_edit), ("Image", self._m_image), ("Layer", self._m_layer),
                         ("Select", self._m_select), ("Adjust", self._m_adjust), ("Filter", self._m_filter)):
            self.icons.setdefault("chev", ImageTk.PhotoImage(icon("chev", 10, (154, 154, 166))))
            b = B(top, name + " ", None, fill=BG, fg=ui.TXT, hover=CARD2, font=(F, 9, "bold"), padx=10, pady=5,
                  image=self.icons["chev"], compound="right")
            b.cmd = (lambda b=b, fn=fn: self._popup(b, fn)); b.pack(side="left")
        I = lambda n, s=14, c=(242, 242, 244): self.icons.setdefault(n + str(c), ImageTk.PhotoImage(icon(n, s, c)))
        B(top, "  Export PNG", self.quick_export, fill=ui.RED, fg="white", hover=ui.RED2, font=(F, 9, "bold"),
          padx=12, pady=5, image=I("download", 14, (255, 255, 255)), compound="left").pack(side="right")
        B(top, "  EX STUDIO", self.send_studio, font=(F, 9, "bold"), padx=10, pady=5, image=I("send"), compound="left").pack(side="right", padx=(0, 6))
        B(top, "  REX BOARD", self.send_board, font=(F, 9, "bold"), padx=10, pady=5, image=I("send"), compound="left").pack(side="right", padx=(0, 6))
        for ic, fn, tip in (("camera", self.take_screenshot, "Take a screenshot onto a new layer (Ctrl+Shift+P)"),
                            ("image", lambda: self.open_file(as_layer=True), "Add images / screenshots as new layers (Ctrl+Shift+O) \u00b7 or drop files, or Ctrl+V")):
            b = B(top, "", fn, padx=9, pady=5, image=I(ic, 16)); b.pack(side="right", padx=(0, 6))
            b.bind("<Enter>", lambda e, w=b, tip=tip: (w.configure(bg=ui.BORDER), self._tip(w, tip)), add="+")
            b.bind("<Leave>", lambda e, w=b: (w.configure(bg=w.fill), self._tip(None, None)), add="+")
        self.redo_b = B(top, "", self.redo, padx=8, pady=5, image=I("redo", 16)); self.redo_b.pack(side="right", padx=(0, 12))
        self.undo_b = B(top, "", self.undo, padx=8, pady=5, image=I("undo", 16)); self.undo_b.pack(side="right", padx=(0, 4))
        # --- options bar
        self.opts = tk.Frame(self, bg=P, highlightthickness=1, highlightbackground=BORDER, height=38)
        self.opts.pack(fill="x", padx=12, pady=(0, 6)); self.opts.pack_propagate(False)
        # --- status
        st = tk.Frame(self, bg=BG); st.pack(side="bottom", fill="x", padx=12, pady=(0, 4))
        self.st_left = tk.Label(st, text="", bg=BG, fg=ui.MUT, font=(F, 9), anchor="w"); self.st_left.pack(side="left")
        self.st_right = tk.Label(st, text="", bg=BG, fg=ui.MUT, font=(F, 9), anchor="e"); self.st_right.pack(side="right")
        main = tk.Frame(self, bg=BG); main.pack(fill="both", expand=True, padx=12, pady=(0, 4))
        # --- left toolbar
        tb = tk.Frame(main, bg=P, highlightthickness=1, highlightbackground=BORDER); tb.pack(side="left", fill="y")
        grid = tk.Frame(tb, bg=P); grid.pack(side="top", padx=4, pady=6)
        for i, (key, label, sc, ic) in enumerate(self.TOOL_LIST):
            self.icons[key] = ImageTk.PhotoImage(icon(ic, 20))
            l = tk.Label(grid, image=self.icons[key], bg=P, width=34, height=32, cursor="hand2")
            l.grid(row=i // 2, column=i % 2, padx=1, pady=1)
            l.bind("<Button-1>", lambda e, k=key: self.set_tool(k))
            l.bind("<Enter>", lambda e, t="%s  (%s)" % (label, sc), w=l: self._tip(w, t))
            l.bind("<Leave>", lambda e: self._tip(None, None))
            self.tool_btns[key] = l
        # primary / secondary colour chips
        self.chips = tk.Canvas(tb, width=74, height=62, bg=P, highlightthickness=0); self.chips.pack(side="top", pady=(8, 2))
        self.chips.bind("<Button-1>", self._chip_click)
        tk.Label(tb, text="X swap \u00b7 D reset", bg=P, fg=ui.DIM, font=(F, 7)).pack(side="top")
        # --- right panel
        rp = tk.Frame(main, bg=P, width=312, highlightthickness=1, highlightbackground=BORDER)
        rp.pack(side="right", fill="y"); rp.pack_propagate(False)
        self._build_color(rp)
        tabs = tk.Frame(rp, bg=P); tabs.pack(fill="x", padx=8, pady=(8, 0))
        self.rtabs = {}
        for k, t in (("layers", "LAYERS"), ("history", "HISTORY")):
            f = tk.Frame(tabs, bg=P); f.pack(side="left")
            l = tk.Label(f, text=t, bg=P, fg=ui.MUT, font=(F, 8, "bold"), padx=10, pady=4, cursor="hand2"); l.pack()
            u = tk.Frame(f, bg=P, height=2); u.pack(fill="x")
            l.bind("<Button-1>", lambda e, k=k: self.show_rtab(k)); self.rtabs[k] = (l, u)
        self.rbody = tk.Frame(rp, bg=P); self.rbody.pack(fill="both", expand=True, padx=8, pady=(2, 8))
        self._build_layers(); self._build_history(); self.show_rtab("layers")
        # --- canvas
        self.cv = tk.Canvas(main, bg="#1a1a1f", highlightthickness=0, cursor="crosshair")
        self.cv.pack(side="left", fill="both", expand=True, padx=6)
        self.cv.bind("<Configure>", lambda e: self.schedule_view())
        self.cv.bind("<ButtonPress-1>", self._press); self.cv.bind("<B1-Motion>", self._motion_drag)
        self.cv.bind("<ButtonRelease-1>", self._release); self.cv.bind("<Motion>", self._hover)
        self.cv.bind("<Double-Button-1>", self._double)
        self.cv.bind("<ButtonPress-2>", self._pan_start); self.cv.bind("<B2-Motion>", self._pan_move)
        self.cv.bind("<ButtonPress-3>", self._right_click)
        self.cv.bind("<MouseWheel>", self._wheel)
        self.cv.bind("<Button-4>", lambda e: self._wheel(e, 120)); self.cv.bind("<Button-5>", lambda e: self._wheel(e, -120))
        self.cv.bind("<Enter>", lambda e: self.cv.focus_set())
        self.cv.bind("<Leave>", lambda e: self.cv.delete("cur"))
        self.pen = None
        self.after(500, self._init_pen)
        self.tipw = None
        self._draw_chips()

    def _init_pen(self):
        try: self.pen = PenPressure(self.cv)
        except Exception: self.pen = None
        self._drop_poll()

    def _drop_poll(self):
        try:
            while self.pen is not None and self.pen.dropped:
                files, vx, vy = self.pen.dropped.pop(0); self.drop_files(files, self.to_doc(vx, vy))
        except Exception as ex:
            self._status("Drop failed: %s" % ex)
        self.after(150, self._drop_poll)

    IMG_EXT = (".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif", ".tif", ".tiff")

    def drop_files(self, files, at=None):
        ok = [f for f in files if f.lower().endswith(self.IMG_EXT)]
        ex = [f for f in files if f.lower().endswith(".exart")]
        if not ok and ex:
            self.open_file(ex[0]); return
        if not ok:
            self._status("Drop PNG, JPG or WEBP images here"); return
        if self.tf: self.tf_apply()
        for i, f in enumerate(ok):
            try: px = C.open_image(f)
            except Exception as e:
                messagebox.showerror(self.ui.APP, "Couldn't open %s:\n%s" % (os.path.basename(f), e), parent=self); continue
            a = None if at is None else (at[0] + 30 * i, at[1] + 30 * i)
            self.place_pixels(px, os.path.splitext(os.path.basename(f))[0], at=a, transform=(i == len(ok) - 1))

    def place_pixels(self, px, name, at=None, transform=True, label="Place image"):
        """put an RGBA image on its own new layer (scaled down to fit), centred or at a doc point, then show the transform box."""
        d = self.doc
        if self.tf: self.tf_apply()
        h, w = px.shape[:2]
        if w > d.w or h > d.h:
            s = min(d.w * 0.9 / w, d.h * 0.9 / h)
            px = cv2.resize(px, (max(1, int(w * s)), max(1, int(h * s))), interpolation=cv2.INTER_AREA); h, w = px.shape[:2]
        cx, cy = (d.w / 2.0, d.h / 2.0) if at is None else at
        x0 = int(round(min(max(cx - w / 2.0, 0), d.w - w))); y0 = int(round(min(max(cy - h / 2.0, 0), d.h - h)))
        full = C.blank(d.w, d.h); full[y0:y0 + h, x0:x0 + w] = px
        self._insert_layer(C.Layer(name or "Image", full)); d.sel = None; self._update_ants()
        self.commit(label, full=True)
        if transform:
            if self.tool != "move": self.set_tool("move")
            self.tf_start()
            self._status("%s added on its own layer - drag to move, handles resize, Enter applies" % (name or "Image"))

    def take_screenshot(self):
        app = self.app
        try:
            from PIL import ImageGrab
        except Exception:
            self._status("Screenshots aren't available"); return
        if self.tf: self.tf_apply()
        app.withdraw()
        def grab():
            im = None
            try:
                try: im = ImageGrab.grab(all_screens=True)
                except TypeError: im = ImageGrab.grab()
            except Exception as e:
                err = e
            app.deiconify(); app.lift()
            if im is None:
                self._status("Couldn't take a screenshot"); return
            self.place_pixels(np.asarray(im.convert("RGBA")).copy(), "Screenshot " + time.strftime("%H%M%S"), label="Screenshot")
        app.after(450, grab)

    def _tip(self, w, text):
        if self.tipw is not None:
            self.tipw.destroy(); self.tipw = None
        if w is None: return
        t = tk.Toplevel(self); t.overrideredirect(True); t.configure(bg=self.ui.BORDER)
        tk.Label(t, text=text, bg=self.ui.CARD2, fg=self.ui.TXT, font=(self.ui.FONT, 9), padx=8, pady=3).pack(padx=1, pady=1)
        if w.winfo_rooty() - self.winfo_rooty() < 40:
            t.update_idletasks(); t.geometry("+%d+%d" % (w.winfo_rootx() + w.winfo_width() - t.winfo_reqwidth(), w.winfo_rooty() + w.winfo_height() + 4))
        else:
            t.geometry("+%d+%d" % (w.winfo_rootx() + w.winfo_width() + 6, w.winfo_rooty() + 4))
        self.tipw = t

    def show_rtab(self, k):
        for kk, (l, u) in self.rtabs.items():
            l.configure(fg=self.ui.TXT if kk == k else self.ui.MUT); u.configure(bg=self.ui.RED if kk == k else self.ui.PANEL)
        self.lay_f.pack_forget(); self.his_f.pack_forget()
        (self.lay_f if k == "layers" else self.his_f).pack(fill="both", expand=True)

    def _popup(self, w, fn):
        m = tk.Menu(self, tearoff=0, bg=self.ui.CARD2, fg=self.ui.TXT, activebackground=self.ui.RED, activeforeground="white",
                    bd=0, relief="flat", font=(self.ui.FONT, 9))
        fn(m)
        m.tk_popup(w.winfo_rootx(), w.winfo_rooty() + w.winfo_height())

    # ================================================================== colour picker
    def _build_color(self, rp):
        ui = self.ui; P = ui.PANEL; F = ui.FONT
        tk.Label(rp, text="COLOUR", bg=P, fg=ui.MUT, font=(F, 8, "bold"), anchor="w").pack(fill="x", padx=10, pady=(8, 2))
        self.wheel = tk.Canvas(rp, width=292, height=196, bg=P, highlightthickness=0); self.wheel.pack(padx=8)
        self.wR, self.wr, self.sq = 94, 78, 106
        self.wcx, self.wcy = 100, 98
        ring = self._ring_img(); self._ring = ImageTk.PhotoImage(ring)
        self.wheel.create_image(self.wcx - self.wR, self.wcy - self.wR, image=self._ring, anchor="nw")
        self.sq_item = self.wheel.create_image(self.wcx - self.sq / 2, self.wcy - self.sq / 2, anchor="nw")
        self.wheel.bind("<ButtonPress-1>", self._wheel_press); self.wheel.bind("<B1-Motion>", self._wheel_drag)
        # side: big chip, hex, brightness
        x = 206
        self.wheel.create_text(x, 16, text="HEX", fill=ui.MUT, font=(F, 8, "bold"), anchor="w")
        self.hexv = tk.StringVar()
        he = tk.Entry(self.wheel, textvariable=self.hexv, width=9, bg=ui.CARD2, fg=ui.TXT, insertbackground=ui.TXT, relief="flat",
                      font=("Consolas", 10), highlightthickness=1, highlightbackground=ui.BORDER, highlightcolor=ui.RED)
        self.wheel.create_window(x, 36, window=he, anchor="w")
        he.bind("<Return>", self._hex_enter); he.bind("<FocusOut>", self._hex_enter)
        self.rgb_lbl = self.wheel.create_text(x, 62, text="", fill=ui.MUT, font=(F, 8), anchor="w")
        self.cur_chip = self.wheel.create_rectangle(x, 78, x + 78, 112, outline=ui.BORDER)
        self.wheel.create_text(x, 128, text="Black / White", fill=ui.MUT, font=(F, 8), anchor="w")
        bk = self.wheel.create_rectangle(x, 138, x + 36, 160, fill="#000000", outline=ui.BORDER)
        wh = self.wheel.create_rectangle(x + 42, 138, x + 78, 160, fill="#FFFFFF", outline=ui.BORDER)
        self.wheel.tag_bind(bk, "<Button-1>", lambda e: self.set_color((0, 0, 0)))
        self.wheel.tag_bind(wh, "<Button-1>", lambda e: self.set_color((255, 255, 255)))
        self.wheel.tag_bind(bk, "<Button-3>", lambda e: self.set_color((0, 0, 0), secondary=True))
        self.wheel.tag_bind(wh, "<Button-3>", lambda e: self.set_color((255, 255, 255), secondary=True))
        # brightness (value) slider, all the way down to pure black
        vf = tk.Frame(rp, bg=P); vf.pack(fill="x", padx=10, pady=(2, 0))
        tk.Label(vf, text="Brightness", bg=P, fg=ui.MUT, font=(F, 8)).pack(side="left")
        self.vval = tk.DoubleVar(value=0)
        self.vscale = ttk.Scale(vf, from_=0, to=100, variable=self.vval, command=self._v_slide, length=200)
        self.vscale.pack(side="left", padx=6)
        self.vlbl = tk.Label(vf, text="0%", bg=P, fg=ui.TXT, font=(F, 8, "bold"), width=4); self.vlbl.pack(side="left")
        # swatches 16 x 4 = 64
        tk.Label(rp, text="SWATCHES   (left: colour \u00b7 right: 2nd colour)", bg=P, fg=ui.MUT, font=(F, 7, "bold"),
                 anchor="w").pack(fill="x", padx=10, pady=(6, 1))
        self.sw = tk.Canvas(rp, width=292, height=74, bg=P, highlightthickness=0); self.sw.pack(padx=8)
        pal = C.palette()[:64]; s = 17.6
        for i, c in enumerate(pal):
            x0, y0 = 2 + (i % 16) * s, 2 + (i // 16) * 18
            r = self.sw.create_rectangle(x0, y0, x0 + s - 2, y0 + 16, fill=C.rgb_to_hex(c), outline="#33333b")
            self.sw.tag_bind(r, "<Button-1>", lambda e, c=c: self.set_color(c))
            self.sw.tag_bind(r, "<Button-3>", lambda e, c=c: self.set_color(c, secondary=True))
        tk.Label(rp, text="RECENT", bg=P, fg=ui.MUT, font=(F, 7, "bold"), anchor="w").pack(fill="x", padx=10, pady=(4, 1))
        self.rc = tk.Canvas(rp, width=292, height=20, bg=P, highlightthickness=0); self.rc.pack(padx=8)
        self.h, self.s, self.v = 0.0, 0.0, 0.0
        self._sync_color()

    def _ring_img(self):
        R, r = self.wR, self.wr; S = 2 * R
        yy, xx = np.mgrid[0:S * 2, 0:S * 2].astype(np.float32) / 2.0
        dx, dy = xx - R, yy - R; d = np.sqrt(dx * dx + dy * dy)
        hue = (np.degrees(np.arctan2(-dy, dx)) % 360) / 360.0
        hsv = np.stack([hue * 360, np.ones_like(hue), np.ones_like(hue)], 2).astype(np.float32)
        rgb = cv2.cvtColor(hsv, cv2.COLOR_HSV2RGB)
        a = np.clip(R - d, 0, 1) * np.clip(d - r, 0, 1)
        P = np.array(C.hex_to_rgb(self.ui.PANEL), np.float32) / 255
        out = rgb * a[..., None] + P * (1 - a[..., None])
        im = Image.fromarray((out * 255).astype(np.uint8)).resize((S, S), Image.LANCZOS)
        return im

    def _sq_img(self):
        n = self.sq
        s = np.linspace(0, 1, n, dtype=np.float32)[None, :].repeat(n, 0)
        v = np.linspace(1, 0, n, dtype=np.float32)[:, None].repeat(n, 1)       # bottom row = pure black
        hsv = np.stack([np.full_like(s, self.h * 360), s, v], 2)
        rgb = cv2.cvtColor(hsv, cv2.COLOR_HSV2RGB)
        return Image.fromarray((rgb * 255 + 0.5).astype(np.uint8))

    def _wheel_press(self, e):
        dx, dy = e.x - self.wcx, e.y - self.wcy; d = math.hypot(dx, dy)
        if abs(dx) <= self.sq / 2 + 3 and abs(dy) <= self.sq / 2 + 3: self._wdrag = "sq"
        elif self.wr - 6 <= d <= self.wR + 4: self._wdrag = "ring"
        else: self._wdrag = None
        self._wheel_drag(e)

    def _wheel_drag(self, e):
        k = getattr(self, "_wdrag", None)
        if k == "ring":
            self.h = (math.degrees(math.atan2(-(e.y - self.wcy), e.x - self.wcx)) % 360) / 360.0
        elif k == "sq":
            self.s = min(1, max(0, (e.x - (self.wcx - self.sq / 2)) / (self.sq - 1)))
            self.v = min(1, max(0, 1 - (e.y - (self.wcy - self.sq / 2)) / (self.sq - 1)))
        else:
            return
        r, g, b = colorsys.hsv_to_rgb(self.h, self.s, self.v)
        self.primary = (int(round(r * 255)), int(round(g * 255)), int(round(b * 255)))
        self._sync_color(from_hsv=True)

    def _v_slide(self, *_):
        if getattr(self, "_vlock", False): return
        self.v = self.vval.get() / 100.0
        r, g, b = colorsys.hsv_to_rgb(self.h, self.s, self.v)
        self.primary = (int(round(r * 255)), int(round(g * 255)), int(round(b * 255)))
        self._sync_color(from_hsv=True)

    def _hex_enter(self, e=None):
        try: self.set_color(C.hex_to_rgb(self.hexv.get()))
        except Exception: self.hexv.set(C.rgb_to_hex(self.primary))

    def set_color(self, c, secondary=False):
        c = tuple(int(v) for v in c[:3])
        if secondary: self.secondary = c
        else: self.primary = c
        self._sync_color()

    def _sync_color(self, from_hsv=False):
        if not from_hsv:
            h, s, v = colorsys.rgb_to_hsv(*[x / 255.0 for x in self.primary])
            if s > 0 and v > 0: self.h = h
            self.s, self.v = s, v
        self._sqp = ImageTk.PhotoImage(self._sq_img()); self.wheel.itemconfigure(self.sq_item, image=self._sqp)
        w = self.wheel; w.delete("mk")
        a = math.radians(self.h * 360); rm = (self.wR + self.wr) / 2
        mx, my = self.wcx + math.cos(a) * rm, self.wcy - math.sin(a) * rm
        w.create_oval(mx - 6, my - 6, mx + 6, my + 6, outline="white", width=2, tags="mk")
        w.create_oval(mx - 7, my - 7, mx + 7, my + 7, outline="black", width=1, tags="mk")
        sx = self.wcx - self.sq / 2 + self.s * (self.sq - 1); sy = self.wcy - self.sq / 2 + (1 - self.v) * (self.sq - 1)
        w.create_oval(sx - 5, sy - 5, sx + 5, sy + 5, outline="white" if self.v < 0.6 else "black", width=2, tags="mk")
        hx = C.rgb_to_hex(self.primary); self.hexv.set(hx)
        w.itemconfigure(self.cur_chip, fill=hx); w.itemconfigure(self.rgb_lbl, text="R %d  G %d  B %d" % self.primary)
        self._vlock = True; self.vval.set(self.v * 100); self._vlock = False; self.vlbl.configure(text="%d%%" % round(self.v * 100))
        self._draw_chips()

    def _remember(self, c):
        c = tuple(c[:3])
        if c in self.recent: self.recent.remove(c)
        self.recent.insert(0, c); del self.recent[16:]
        self.rc.delete("all")
        for i, cc in enumerate(self.recent):
            r = self.rc.create_rectangle(2 + i * 17.6, 2, 2 + i * 17.6 + 15.6, 18, fill=C.rgb_to_hex(cc), outline="#33333b")
            self.rc.tag_bind(r, "<Button-1>", lambda e, cc=cc: self.set_color(cc))
            self.rc.tag_bind(r, "<Button-3>", lambda e, cc=cc: self.set_color(cc, secondary=True))

    def _draw_chips(self):
        c = self.chips; c.delete("all")
        c.create_rectangle(30, 26, 66, 58, fill=C.rgb_to_hex(self.secondary), outline="#777780")
        c.create_rectangle(8, 6, 44, 38, fill=C.rgb_to_hex(self.primary), outline="#ddd")
        self._swap_ic = getattr(self, "_swap_ic", None) or ImageTk.PhotoImage(icon("swap", 14))
        c.create_image(60, 12, image=self._swap_ic)

    def _chip_click(self, e):
        if e.x > 50 and e.y < 22: self.swap_colors()
        elif e.x > 44 or e.y > 38: self._pick_dialog(secondary=True)

    def _pick_dialog(self, secondary=False):
        from tkinter import colorchooser
        c = colorchooser.askcolor(color=C.rgb_to_hex(self.secondary if secondary else self.primary), parent=self)
        if c and c[0]: self.set_color(c[0], secondary=secondary)

    def swap_colors(self):
        self.primary, self.secondary = self.secondary, self.primary; self._sync_color()

    def reset_colors(self):
        self.primary, self.secondary = (0, 0, 0), (255, 255, 255); self._sync_color()

    # ================================================================== layers panel
    def _build_layers(self):
        ui = self.ui; P = ui.PANEL; F = ui.FONT
        self.lay_f = f = tk.Frame(self.rbody, bg=P)
        row = tk.Frame(f, bg=P); row.pack(fill="x", pady=(6, 2))
        self.blend_v = tk.StringVar(value="Normal")
        cb = ttk.Combobox(row, textvariable=self.blend_v, values=C.BLENDS, state="readonly", width=10, font=(F, 9))
        cb.pack(side="left"); cb.bind("<<ComboboxSelected>>", lambda e: self._set_blend())
        self.op_s = Slide(row, ui, "Opacity", 0, 100, 100, cmd=self._op_live, width=96, release=self._op_done, unit="%")
        self.op_s.pack(side="left", padx=(8, 0))
        self.lc = tk.Canvas(f, bg=ui.CARD, highlightthickness=1, highlightbackground=ui.BORDER, height=110)
        self.lc.bind("<ButtonPress-1>", self._lc_press); self.lc.bind("<B1-Motion>", self._lc_drag)
        self.lc.bind("<ButtonRelease-1>", self._lc_release); self.lc.bind("<Double-Button-1>", self._lc_double)
        self.lc.bind("<Configure>", lambda e: self.draw_layers())
        self.lc.bind("<MouseWheel>", lambda e: self.lc.yview_scroll(-1 if e.delta > 0 else 1, "units"))
        self.mask_lbl = tk.Label(f, text="", bg=P, fg=ui.AMBER, font=(F, 8), anchor="w"); self.mask_lbl.pack(side="bottom", fill="x")
        bt = tk.Frame(f, bg=P); bt.pack(side="bottom", fill="x")
        self.lc.pack(fill="both", expand=True, pady=4)
        for t, fn, tip in (("plus", self.add_layer, "New layer (Ctrl+Shift+N)"), ("dup", self.dup_layer, "Duplicate (Ctrl+J)"),
                           ("mask", self.toggle_mask, "Add / remove layer mask"), ("merge", self.merge_down, "Merge down (Ctrl+E)"),
                           ("flatten", self.flatten, "Flatten (Ctrl+Shift+E)"), ("trash", self.del_layer, "Delete layer")):
            self.icons["L" + t] = ImageTk.PhotoImage(icon(t, 15))
            b = ui.Btn(bt, "", fn, padx=9, pady=5, image=self.icons["L" + t]); b.pack(side="left", padx=(0, 3))
            b.bind("<Enter>", lambda e, w=b, tip=tip: (w.configure(bg=ui.BORDER), self._tip(w, tip)), add="+")
            b.bind("<Leave>", lambda e, w=b: (w.configure(bg=w.fill), self._tip(None, None)), add="+")
        self._thumbs = {}; self._lcd = None

    ROW = 50

    def _thumb(self, arr, w=44, h=32, rgba=True):
        H, W = arr.shape[:2]; s = min(w / W, h / H); tw, th = max(1, int(W * s)), max(1, int(H * s))
        step = max(1, int(1 / s / 2))
        small = cv2.resize(arr[::step, ::step], (tw, th), interpolation=cv2.INTER_AREA)
        if rgba:
            chk = C.checker(th, tw, s=4)[..., :3]; a = small[..., 3:4].astype(np.float32) / 255
            out = small[..., :3].astype(np.float32) / 255 * a + chk * (1 - a)
            im = Image.fromarray((out * 255).astype(np.uint8))
        else:
            im = Image.fromarray(small).convert("RGB")
        bg = Image.new("RGB", (w, h), (24, 24, 29)); bg.paste(im, ((w - tw) // 2, (h - th) // 2))
        return ImageTk.PhotoImage(bg)

    def draw_layers(self):
        if not hasattr(self, "doc"): return
        lc = self.lc; lc.delete("all"); ui = self.ui; d = self.doc
        W = max(200, lc.winfo_width()); R = self.ROW; n = len(d.layers)
        self._eye = getattr(self, "_eye", None) or ImageTk.PhotoImage(icon("eye", 16))
        self._eye_off = getattr(self, "_eye_off", None) or ImageTk.PhotoImage(icon("eye", 16, (90, 90, 98)))
        self._lock = getattr(self, "_lock", None) or ImageTk.PhotoImage(icon("lock", 14, (255, 176, 32)))
        self._thumbs = {}
        for row, i in enumerate(range(n - 1, -1, -1)):
            l = d.layers[i]; y = row * R; act = i == d.active
            lc.create_rectangle(0, y, W, y + R - 1, fill="#2a1416" if act else ui.CARD, outline="", tags=("row", "r%d" % i))
            if act: lc.create_rectangle(0, y, 3, y + R - 1, fill=ui.RED, outline="")
            lc.create_image(18, y + R / 2, image=self._eye if l.visible else self._eye_off, tags=("eye", "e%d" % i))
            t = self._thumb(l.px); self._thumbs[l.id] = t
            ol = ui.TXT if (act and not self.edit_mask) else "#3a3a44"
            lc.create_rectangle(35, y + 8, 81, y + 42, outline=ol)
            lc.create_image(58, y + R / 2, image=t)
            xx = 88
            if l.mask is not None:
                mt = self._thumb(l.mask, 30, 32, rgba=False); self._thumbs[(l.id, "m")] = mt
                lc.create_rectangle(xx - 1, y + 8, xx + 31, y + 42, outline=ui.TXT if (act and self.edit_mask) else "#3a3a44")
                lc.create_image(xx + 15, y + R / 2, image=mt, tags=("mthumb", "m%d" % i)); xx += 38
            nm = l.name if len(l.name) < 22 else l.name[:20] + "\u2026"
            lc.create_text(xx, y + 18, text=nm, fill=ui.TXT if l.visible else ui.MUT, font=(ui.FONT, 9, "bold"), anchor="w")
            sub = "%s \u00b7 %d%%" % (l.blend, round(l.opacity * 100)) + ("  \u00b7 T" if l.text else "")
            lc.create_text(xx, y + 34, text=sub, fill=ui.DIM if not act else ui.MUT, font=(ui.FONT, 8), anchor="w")
            lc.create_image(W - 16, y + R / 2, image=self._lock if l.locked else "", tags=("lock", "k%d" % i))
            if not l.locked: lc.create_text(W - 16, y + R / 2, text="\u25cb", fill="#3a3a44", font=(ui.FONT, 9), tags=("lock", "k%d" % i))
            lc.create_line(0, y + R - 1, W, y + R - 1, fill="#24242b")
        lc.configure(scrollregion=(0, 0, W, n * R))
        L = d.layer; self.blend_v.set(L.blend); self.op_s.set(L.opacity * 100)
        self.mask_lbl.configure(text="Painting on the LAYER MASK (black hides, white shows) - click the layer thumbnail to go back"
                                if (self.edit_mask and L.mask is not None) else "")

    def _lc_index(self, e):
        y = self.lc.canvasy(e.y); row = int(y // self.ROW); n = len(self.doc.layers)
        if row < 0 or row >= n: return None
        return n - 1 - row

    def _lc_press(self, e):
        i = self._lc_index(e)
        if i is None: return
        if self.tf: self.tf_apply()
        l = self.doc.layers[i]; W = self.lc.winfo_width()
        if e.x < 32:
            l.visible = not l.visible; self.commit("Show layer" if l.visible else "Hide layer", full=True); return
        if e.x > W - 30:
            l.locked = not l.locked; self.commit("Lock layer" if l.locked else "Unlock layer"); return
        self.doc.active = i
        self.edit_mask = bool(l.mask is not None and 86 <= e.x <= 122)
        self._lcd = (i, e.y, False); self.draw_layers(); self._opts_refresh()

    def _lc_drag(self, e):
        if not self._lcd: return
        i, y0, moved = self._lcd
        if abs(e.y - y0) > 6: self._lcd = (i, y0, True)
        if self._lcd[2]:
            self.lc.delete("dropline")
            j = self._lc_index(e)
            y = (int(self.lc.canvasy(e.y) // self.ROW) + (1 if self.lc.canvasy(e.y) % self.ROW > self.ROW / 2 else 0)) * self.ROW
            self.lc.create_line(0, y, self.lc.winfo_width(), y, fill=self.ui.RED, width=2, tags="dropline")

    def _lc_release(self, e):
        dd = self._lcd; self._lcd = None; self.lc.delete("dropline")
        if not dd or not dd[2]: return
        i = dd[0]; n = len(self.doc.layers)
        y = self.lc.canvasy(e.y); slot = int(y // self.ROW) + (1 if y % self.ROW > self.ROW / 2 else 0)
        slot = max(0, min(n, slot))
        tgt = n - slot                      # insertion index in bottom-up order
        if tgt > i: tgt -= 1
        tgt = max(0, min(n - 1, tgt))
        if tgt == i: return
        l = self.doc.layers.pop(i); self.doc.layers.insert(tgt, l); self.doc.active = tgt
        self.commit("Reorder layers", full=True)

    def _lc_double(self, e):
        i = self._lc_index(e)
        if i is None or e.x < 86: return
        l = self.doc.layers[i]
        if l.text and self.tool == "text":
            self.text_dialog(edit=i); return
        nm = simpledialog.askstring("Rename layer", "Layer name:", initialvalue=l.name, parent=self)
        if nm and nm.strip():
            l.name = nm.strip()[:60]; self.commit("Rename layer")

    def _set_blend(self):
        self.doc.layer.blend = self.blend_v.get(); self.commit("Blend mode: " + self.blend_v.get(), full=True)

    def _op_live(self, v):
        if not hasattr(self, "doc"): return
        L = self.doc.layer
        if abs(L.opacity - v / 100.0) > 1e-4:
            L.opacity = v / 100.0; self._op_pending = True
            if not getattr(self, "_op_job", None): self._op_job = self.after(60, self._op_flush)

    def _op_flush(self):
        self._op_job = None; self.full_refresh()

    def _op_done(self, v):
        if getattr(self, "_op_pending", False):
            self._op_pending = False; self.commit("Opacity %d%%" % round(v), full=True)

    # ================================================================== history panel
    def _build_history(self):
        ui = self.ui
        self.his_f = f = tk.Frame(self.rbody, bg=ui.PANEL)
        self.hl = tk.Listbox(f, bg=ui.CARD, fg=ui.TXT, selectbackground=ui.RED, selectforeground="white", activestyle="none",
                             relief="flat", highlightthickness=1, highlightbackground=ui.BORDER, font=(ui.FONT, 9), bd=0)
        self.hl.pack(fill="both", expand=True, pady=(6, 4))
        self.hl.bind("<<ListboxSelect>>", self._hl_pick)
        tk.Label(f, text="Click a step to go back to it. Up to 80 steps.", bg=ui.PANEL, fg=ui.DIM, font=(ui.FONT, 8)).pack(anchor="w")

    def draw_history(self):
        self.hl.delete(0, "end")
        for i, (lab, _) in enumerate(self.hist.items):
            self.hl.insert("end", ("  >  " if i == self.hist.pos else "      ") + lab)
            if i > self.hist.pos: self.hl.itemconfigure(i, fg=self.ui.DIM)
        self.hl.selection_clear(0, "end"); self.hl.see(self.hist.pos)
        self.undo_b.set_enabled(self.hist.can_undo()); self.redo_b.set_enabled(self.hist.can_redo())

    def _hl_pick(self, e):
        s = self.hl.curselection()
        if not s or s[0] == self.hist.pos: return
        self.cancel_modes(); self.hist.goto(s[0]); self.after_history()

    # ================================================================== document / rendering
    def new_doc(self, w, h, bg, push=True):
        self.cancel_modes()
        self.doc = C.Doc(w, h, bg if bg is not None else (0, 0, 0, 0))
        if bg is None: self.doc.layers[0].name = "Layer 1"
        self.hist = C.History(self.doc)
        self._doc_changed()

    def _doc_changed(self):
        d = self.doc
        yy, xx = np.mgrid[0:d.h, 0:d.w]
        self.chk = np.where(((xx // 12) + (yy // 12)) % 2 == 0, 205, 232).astype(np.uint8)
        self.disp = np.zeros((d.h, d.w, 3), np.uint8)
        self.full_refresh(); self.fit()
        self.draw_layers(); self.draw_history(); self._update_ants(); self._status()

    def _region_rgb(self, x0, y0, x1, y1, override=None):
        bg = self.chk[y0:y1, x0:x1, None].astype(np.float32) * (1 / 255.0)
        rgb, _ = C.composite(self.doc.layers, x0, y0, x1, y1, bg=bg, override=override)
        return C.to_u8(rgb)

    def refresh(self, r, override=None):
        if r is None: return
        d = self.doc
        x0, y0, x1, y1 = max(0, int(r[0])), max(0, int(r[1])), min(d.w, int(math.ceil(r[2]))), min(d.h, int(math.ceil(r[3])))
        if x1 <= x0 or y1 <= y0: return
        # big regions: do it in horizontal bands (keeps memory and latency in check)
        band = max(64, int(4_000_000 / max(1, x1 - x0)))
        for yb in range(y0, y1, band):
            yb1 = min(y1, yb + band)
            self.disp[yb:yb1, x0:x1] = self._region_rgb(x0, yb, x1, yb1, override)
        self.schedule_view()

    def full_refresh(self, override=None):
        self.refresh((0, 0, self.doc.w, self.doc.h), override)

    def schedule_view(self, delay=8):
        if self._view_job is None:
            self._view_job = self.after(delay, self.render_view)

    def _view_rect(self):
        cv = self.cv; cw, ch = max(2, cv.winfo_width()), max(2, cv.winfo_height()); z = self.zoom; d = self.doc
        vx0 = max(0, int(math.floor(-self.ox / z))); vy0 = max(0, int(math.floor(-self.oy / z)))
        vx1 = min(d.w, int(math.ceil((cw - self.ox) / z)) + 1); vy1 = min(d.h, int(math.ceil((ch - self.oy) / z)) + 1)
        tw, th = max(1, int(round((vx1 - vx0) * z))), max(1, int(round((vy1 - vy0) * z)))
        return vx0, vy0, vx1, vy1, tw, th

    def render_view(self, preview=None):
        self._view_job = None
        if not hasattr(self, "doc"): return
        cv = self.cv; z = self.zoom; d = self.doc
        vx0, vy0, vx1, vy1, tw, th = self._view_rect()
        cv.delete("img")
        if preview is not None and vx1 > vx0 and vy1 > vy0:
            self.photo = ImageTk.PhotoImage(Image.fromarray(preview))
            cv.create_image(self.ox + vx0 * z, self.oy + vy0 * z, image=self.photo, anchor="nw", tags="img")
            cv.tag_lower("img")
        elif vx1 > vx0 and vy1 > vy0:
            crop = self.disp[vy0:vy1, vx0:vx1]
            tw, th = max(1, int(round((vx1 - vx0) * z))), max(1, int(round((vy1 - vy0) * z)))
            if z < 1:
                step = max(1, int(1 / z / 2))
                small = crop[::step, ::step] if step > 1 else crop
                img = cv2.resize(small, (tw, th), interpolation=cv2.INTER_AREA)
            elif z >= 2:
                img = cv2.resize(crop, (tw, th), interpolation=cv2.INTER_NEAREST)
            else:
                img = cv2.resize(crop, (tw, th), interpolation=cv2.INTER_LINEAR)
            self.photo = ImageTk.PhotoImage(Image.fromarray(img))
            cv.create_image(self.ox + vx0 * z, self.oy + vy0 * z, image=self.photo, anchor="nw", tags="img")
            cv.tag_lower("img")
        cv.delete("frame")
        x0, y0, x1, y1 = self.ox, self.oy, self.ox + d.w * z, self.oy + d.h * z
        cv.create_rectangle(x0 - 1, y0 - 1, x1, y1, outline="#000000", tags="frame")
        self._draw_ants(); self._draw_overlays()

    def fit(self):
        self.update_idletasks()
        cw, ch = max(100, self.cv.winfo_width()), max(100, self.cv.winfo_height())
        if cw < 150: cw, ch = 860, 560
        z = min((cw - 40) / self.doc.w, (ch - 40) / self.doc.h); z = min(z, 1.0) if z > 0 else 0.1
        self.set_zoom(z, center=True)

    def set_zoom(self, z, cx=None, cy=None, center=False):
        z = max(0.02, min(32.0, z)); cw, ch = self.cv.winfo_width(), self.cv.winfo_height()
        if cw < 150: cw, ch = 860, 560
        if center:
            self.ox = (cw - self.doc.w * z) / 2; self.oy = (ch - self.doc.h * z) / 2
        else:
            cx = cw / 2 if cx is None else cx; cy = ch / 2 if cy is None else cy
            dx, dy = (cx - self.ox) / self.zoom, (cy - self.oy) / self.zoom
            self.ox, self.oy = cx - dx * z, cy - dy * z
        self.zoom = z; self.schedule_view(); self._status()

    def to_doc(self, x, y):
        return (x - self.ox) / self.zoom, (y - self.oy) / self.zoom

    def to_view(self, x, y):
        return self.ox + x * self.zoom, self.oy + y * self.zoom

    def _status(self, msg=None):
        if msg is not None:
            self.st_left.configure(text=msg, fg=self.ui.MUT); self._msg_t = time.time()
        d = self.doc; x, y = self.mouse_doc
        pen = ""
        if self.pen is not None and self.pen.seen: pen = "  \u00b7  pen pressure ON"
        self.st_right.configure(text="%d \u00d7 %d px  \u00b7  %d%%  \u00b7  x %d  y %d  \u00b7  %s%s" % (
            d.w, d.h, round(self.zoom * 100), x, y, d.layer.name, pen))

    # ================================================================== history plumbing
    def commit(self, label, full=False, region=None):
        self.hist.push(label)
        if full: self.full_refresh()
        elif region is not None: self.refresh(region)
        self.draw_layers(); self.draw_history()

    def after_history(self):
        d = self.doc
        if self.disp.shape[:2] != (d.h, d.w):
            self._doc_changed(); return
        self.full_refresh(); self.draw_layers(); self.draw_history(); self._update_ants()

    def undo(self):
        if self.tf: self.tf_cancel(); return
        self.cancel_modes()
        if self.hist.undo(): self.after_history(); self._status("Undo")

    def redo(self):
        self.cancel_modes()
        if self.hist.redo(): self.after_history(); self._status("Redo")

    # ================================================================== selection outline (marching ants)
    def _update_ants(self):
        sel = self.doc.sel
        if sel is None: self._ants = []
        else:
            sc = min(1.0, 1600.0 / max(self.doc.w, self.doc.h))
            self._ants = C.sel_contours(sel, sc)
        self.schedule_view()

    def _draw_ants(self):
        cv = self.cv; cv.delete("ants")
        for c in self._ants:
            pts = []
            for x, y in c[:: max(1, len(c) // 4000)]:
                pts += [self.ox + x * self.zoom, self.oy + y * self.zoom]
            if len(pts) < 4: continue
            pts += pts[:2]
            cv.create_line(*pts, fill="#ffffff", width=1, tags="ants")
            cv.create_line(*pts, fill="#000000", width=1, dash=(4, 4), dashoffset=self._ant_off, tags=("ants", "antd"))

    def _ants_tick(self):
        if self._ants and self.winfo_ismapped():
            self._ant_off = (self._ant_off + 1) % 8
            for it in self.cv.find_withtag("antd"): self.cv.itemconfigure(it, dashoffset=self._ant_off)
        self.after(140, self._ants_tick)

    # ================================================================== tools + options bar
    def set_tool(self, k):
        if self.tf and k != "move": self.tf_apply()
        if self.tool == "crop" and k != "crop": self.crop_rect = None
        self.tool = k
        for kk, l in self.tool_btns.items():
            l.configure(bg=self.ui.RED if kk == k else self.ui.PANEL)
        self.cv.configure(cursor={"hand": "fleur", "move": "fleur", "zoom": "plus", "text": "xterm", "eyedropper": "tcross"}.get(k, "crosshair"))
        self._opts_refresh(); self._draw_overlays()
        lab = [t for t in self.TOOL_LIST if t[0] == k][0]
        self._status("%s  (%s)" % (lab[1], lab[2]))

    def _sz_key(self):
        return {"pencil": "pencil_size", "eraser": "eraser_size"}.get(self.tool, "size")

    def _seg(self, parent, label, options, key, cmd=None):
        ui = self.ui; f = tk.Frame(parent, bg=parent["bg"]); f.pack(side="left", padx=(10, 0))
        if label: tk.Label(f, text=label, bg=f["bg"], fg=ui.MUT, font=(ui.FONT, 9)).pack(side="left", padx=(0, 4))
        btns = []
        def pick(v):
            self.opt[key] = v
            for b, vv in btns: b.set_fill(ui.RED if vv == v else ui.CARD2, ui.RED2 if vv == v else ui.BORDER)
            if cmd: cmd()
        for t, v in options:
            b = ui.Btn(f, t, None, font=(ui.FONT, 8, "bold"), padx=8, pady=3); b.cmd = (lambda v=v: pick(v)); b.pack(side="left", padx=1)
            btns.append((b, v))
        pick(self.opt[key])

    def _chk(self, parent, label, key):
        ui = self.ui; v = tk.BooleanVar(value=self.opt[key])
        c = tk.Checkbutton(parent, text=label, variable=v, command=lambda: self.opt.__setitem__(key, v.get()), bg=parent["bg"],
                           fg=ui.TXT, selectcolor=ui.CARD2, activebackground=parent["bg"], activeforeground=ui.TXT,
                           font=(ui.FONT, 9), bd=0, highlightthickness=0)
        c.pack(side="left", padx=(10, 0))

    def _hint(self, parent, t):
        tk.Label(parent, text=t, bg=parent["bg"], fg=self.ui.DIM, font=(self.ui.FONT, 8)).pack(side="left", padx=(14, 0))

    def _opts_refresh(self):
        o = self.opts
        for w in o.winfo_children(): w.destroy()
        ui = self.ui; P = ui.PANEL; t = self.tool
        lab = [x for x in self.TOOL_LIST if x[0] == t][0][1]
        if self.tf: lab = "Free transform"
        tk.Label(o, text=lab.upper(), bg=P, fg=ui.TXT, font=(ui.FONT, 8, "bold")).pack(side="left", padx=(10, 4))
        S = lambda *a, **k: Slide(o, ui, *a, **k).pack(side="left", padx=(10, 0))
        setter = lambda key, scale=1.0: (lambda v: self.opt.__setitem__(key, v * scale))
        if self.tf:
            self.tf_lbl = tk.Label(o, text="", bg=P, fg=ui.MUT, font=(ui.FONT, 9)); self.tf_lbl.pack(side="left", padx=10)
            ui.Btn(o, "Flip H", lambda: self.tf_flip(1), font=(ui.FONT, 8, "bold"), padx=8, pady=3).pack(side="left", padx=2)
            ui.Btn(o, "Flip V", lambda: self.tf_flip(0), font=(ui.FONT, 8, "bold"), padx=8, pady=3).pack(side="left", padx=2)
            ui.Btn(o, "\u2713  Apply (Enter)", self.tf_apply, fill=ui.RED, fg="white", hover=ui.RED2, font=(ui.FONT, 8, "bold"),
                   padx=10, pady=3).pack(side="right", padx=6)
            ui.Btn(o, "Cancel (Esc)", self.tf_cancel, font=(ui.FONT, 8, "bold"), padx=10, pady=3).pack(side="right")
            self._hint(o, "Drag inside to move \u00b7 handles resize (Shift keeps the ratio) \u00b7 red handle rotates (Shift = 15\u00b0) \u00b7 Enter or click outside applies")
            self._tf_label(); return
        if t in ("brush", "eraser", "pencil", "smudge", "blurtool", "clone", "selbrush"):
            k = self._sz_key()
            self.size_s = Slide(o, ui, "Size", 1, 500, self.opt[k], cmd=setter(k), width=120, unit=" px")
            self.size_s.pack(side="left", padx=(10, 0))
            if t != "pencil": S("Hardness", 0, 100, self.opt["hardness"] * 100, cmd=setter("hardness", 0.01), width=80, unit="%")
            if t in ("brush", "eraser", "pencil", "clone"): S("Opacity", 1, 100, self.opt["opacity"] * 100, cmd=setter("opacity", 0.01), width=80, unit="%")
            if t in ("brush", "eraser"): S("Flow", 1, 100, self.opt["flow"] * 100, cmd=setter("flow", 0.01), width=70, unit="%")
            if t in ("smudge", "blurtool"): S("Strength", 1, 100, self.opt["strength"] * 100, cmd=setter("strength", 0.01), width=80, unit="%")
            if t == "blurtool": self._seg(o, "", (("Blur", "blur"), ("Sharpen", "sharpen")), "fx")
            if t in ("brush", "eraser"): self._chk(o, "Pen pressure", "pressure")
            if t == "clone": self._hint(o, "Alt+click to set the source")
            if t == "selbrush": self._hint(o, "Paint a selection \u00b7 Alt = remove")
            if t == "brush": self._hint(o, "[ ] size \u00b7 Shift+click = straight line \u00b7 Alt = pick colour")
        elif t in ("marquee", "lasso"):
            if t == "marquee": self._seg(o, "", (("\u25ad Rect", "rect"), ("\u25ef Ellipse", "ellipse")), "sel_shape")
            self._seg(o, "", (("New", "new"), ("Add", "add"), ("Subtract", "sub"), ("Intersect", "int")), "sel_op")
            S("Feather", 0, 100, self.opt["feather"], cmd=setter("feather"), width=80, unit=" px")
            self._hint(o, "Shift = add \u00b7 Alt = subtract \u00b7 Ctrl+D deselect")
        elif t == "fill":
            S("Tolerance", 0, 255, self.opt["tolerance"], cmd=setter("tolerance"), width=100)
            S("Opacity", 1, 100, self.opt["opacity"] * 100, cmd=setter("opacity", 0.01), width=80, unit="%")
            self._chk(o, "Contiguous", "contiguous"); self._chk(o, "All layers", "sample_all")
        elif t == "gradient":
            self._seg(o, "", (("Linear", "linear"), ("Radial", "radial")), "grad")
            self._chk(o, "Colour \u2192 transparent", "grad_tr")
            S("Opacity", 1, 100, self.opt["opacity"] * 100, cmd=setter("opacity", 0.01), width=80, unit="%")
            self._hint(o, "Drag from start to end \u00b7 primary \u2192 secondary colour")
        elif t == "shape":
            self._seg(o, "", (("\u25ad Rect", "rect"), ("\u25ef Ellipse", "ellipse"), ("\u2571 Line", "line")), "shape")
            self._seg(o, "", (("Fill", True), ("Outline", False)), "shape_fill")
            S("Stroke", 1, 100, self.opt["stroke"], cmd=setter("stroke"), width=80, unit=" px")
            self._hint(o, "Shift = square / circle / 45\u00b0 \u00b7 each shape gets its own layer")
        elif t == "text":
            self._hint(o, "Click on the canvas to add text \u00b7 double-click a text layer (with this tool) to edit it")
        elif t == "move":
            for key, lab_ in (("auto_pick", "Auto-select layer"), ("move_box", "Show transform handles")):
                v = tk.BooleanVar(value=self.opt.get(key, True))
                tk.Checkbutton(o, text=lab_, variable=v, command=lambda key=key, v=v: self.opt.__setitem__(key, v.get()), bg=P, fg=ui.TXT,
                               selectcolor=ui.CARD2, activebackground=P, activeforeground=ui.TXT, font=(ui.FONT, 9), bd=0,
                               highlightthickness=0).pack(side="left", padx=(10, 0))
            ui.Btn(o, "Free transform", self.tf_start, font=(ui.FONT, 8, "bold"), padx=8, pady=3).pack(side="left", padx=10)
            self._hint(o, "Click an image to select it, drag to move, handles resize \u00b7 drop / paste images to add them")
        elif t == "crop":
            self._hint(o, "Drag the area to keep")
            ui.Btn(o, "\u2713  Crop (Enter)", self.crop_apply, fill=ui.RED, fg="white", hover=ui.RED2, font=(ui.FONT, 8, "bold"),
                   padx=10, pady=3).pack(side="right", padx=6)
            ui.Btn(o, "Cancel", self.crop_cancel, font=(ui.FONT, 8, "bold"), padx=10, pady=3).pack(side="right")
        elif t == "eyedropper":
            self._chk(o, "All layers", "sample_all"); self._hint(o, "Click = colour \u00b7 Alt+click = 2nd colour")
        elif t in ("hand", "zoom"):
            ui.Btn(o, "Fit (Ctrl+0)", self.fit, font=(ui.FONT, 8, "bold"), padx=8, pady=3).pack(side="left", padx=6)
            ui.Btn(o, "100% (Ctrl+1)", lambda: self.set_zoom(1.0), font=(ui.FONT, 8, "bold"), padx=8, pady=3).pack(side="left")
            self._hint(o, "Wheel = zoom \u00b7 Space+drag or middle mouse = pan" + (" \u00b7 click = zoom in, Alt+click = out" if t == "zoom" else ""))

    # ================================================================== mouse
    def _mods(self, e):
        st = e.state
        return bool(st & 0x1), bool(st & 0x4), bool(st & (0x20000 if os.name == "nt" else 0x8))

    def _pan_start(self, e):
        self._pan = (e.x, e.y, self.ox, self.oy)

    def _pan_move(self, e):
        x, y, ox, oy = self._pan; self.ox, self.oy = ox + e.x - x, oy + e.y - y; self.schedule_view()

    def _wheel(self, e, delta=None):
        d = delta if delta is not None else e.delta
        f = 1.18 if d > 0 else 1 / 1.18
        self.set_zoom(self.zoom * f, e.x, e.y)
        return "break"

    def _press(self, e):
        self.cv.focus_set()
        sh, ctrl, alt = self._mods(e)
        x, y = self.to_doc(e.x, e.y); self.drag = {"x0": x, "y0": y, "vx0": e.x, "vy0": e.y, "moved": False}
        t = self.tool
        if self.space or t == "hand":
            self._pan_start(e); self.drag["pan"] = True; return
        if t == "zoom":
            self.set_zoom(self.zoom * (1 / 1.6 if alt else 1.6), e.x, e.y); self.drag = None; return
        if self.tf:
            self.tf_press(e, sh); return
        if alt and t in ("brush", "pencil", "fill", "gradient", "shape", "text", "eyedropper"):
            self.pick_color(x, y, secondary=(t == "eyedropper")); self.drag = None; return
        if t == "eyedropper":
            self.pick_color(x, y); self.drag = None; return
        L = self.doc.layer
        if t in ("brush", "pencil", "eraser", "smudge", "blurtool", "clone", "fill", "gradient", "move") and L.locked:
            self._status("This layer is locked - unlock it in the Layers panel"); self.drag = None; return
        if t in ("brush", "pencil", "eraser"):
            if L.text and not self.edit_mask:
                L.text = None
            target = "mask" if (self.edit_mask and L.mask is not None) else "px"
            sz = self.opt[self._sz_key()]
            self.stroke = C.Stroke(self.doc, target, "erase" if t == "eraser" else "paint", self.primary, sz,
                                   self.opt["hardness"] if t != "pencil" else 1.0, self.opt["opacity"],
                                   self.opt["flow"] if t != "pencil" else 1.0, pencil=(t == "pencil"))
            if t != "eraser": self._remember(self.primary)
            if sh and getattr(self, "_last_pt", None):
                self.stroke.to(*self._last_pt, 1.0)
            self.refresh(self.stroke.to(x, y, self._pressure()))
        elif t in ("smudge", "blurtool", "clone"):
            if t == "clone":
                if alt:
                    self.clone_src = (x, y); self._status("Clone source set"); self.drag = None; self._draw_overlays(); return
                if not self.clone_src:
                    self._status("Alt+click first to set the clone source"); self.drag = None; return
                if getattr(self, "_clone_off", None) is None or not getattr(self, "_clone_keep", False):
                    self._clone_off = (int(round(self.clone_src[0] - x)), int(round(self.clone_src[1] - y)))
            mode = "smudge" if t == "smudge" else ("clone" if t == "clone" else self.opt["fx"])
            st = self.opt["opacity"] if t == "clone" else self.opt["strength"]
            self.stroke = C.FxStroke(self.doc, mode, self.opt["size"], self.opt["hardness"], st,
                                     clone_off=getattr(self, "_clone_off", None))
            self.refresh(self.stroke.to(x, y, self._pressure()))
        elif t == "selbrush":
            self._sb_start(x, y, alt)
        elif t in ("marquee", "lasso"):
            self._sel_op = "add" if sh else ("sub" if alt else self.opt["sel_op"])
            if t == "lasso": self.lasso_pts = [(x, y)]
        elif t == "fill":
            self.fill_at(x, y)
        elif t == "text":
            self.text_dialog(at=(x, y)); self.drag = None
        elif t == "move":
            self._move_press(e, sh)
        elif t == "crop":
            self.crop_rect = None

    def _move_press(self, e, sh):
        d = self.doc; x, y = self.to_doc(e.x, e.y)
        if d.sel is None and self.opt.get("auto_pick", True):
            xi, yi = int(x), int(y)
            if 0 <= xi < d.w and 0 <= yi < d.h:
                for i in range(len(d.layers) - 1, 0 if (len(d.layers) > 1 and not self.PICK_ALL) else -1, -1):
                    l = d.layers[i]
                    if l.visible and not l.locked and l.px[yi, xi, 3] > 8:
                        if i != d.active: d.active = i; self.edit_mask = False; self.draw_layers()
                        break
                else:
                    if len(d.layers) > 1:
                        self._status("Click an image or a layer's pixels to select and move it"); self.drag = None; return
        if d.layer.locked:
            self._status("This layer is locked - unlock it in the Layers panel"); self.drag = None; return
        if d.sel is None and self.opt.get("move_box", True):
            self.tf_start()
            if self.tf: self.tf_press(e, sh, force="move")
            else: self.drag = None
            return
        self.tf_start(move=True)
        if self.tf: self.tf_press(e, sh, force="move")

    def _pressure(self):
        if self.pen is None or not self.opt.get("pressure", True): return 1.0
        return self.pen.get()

    def _motion_drag(self, e):
        if not self.drag: return
        x, y = self.to_doc(e.x, e.y); self.mouse_doc = (int(x), int(y))
        if abs(e.x - self.drag["vx0"]) + abs(e.y - self.drag["vy0"]) > 2: self.drag["moved"] = True
        if self.drag.get("pan"): self._pan_move(e); return
        if self.tf: self.tf_drag(e); return
        t = self.tool; sh, ctrl, alt = self._mods(e)
        if self.stroke is not None:
            self.refresh(self.stroke.to(x, y, self._pressure()))
        elif t == "selbrush":
            self._sb_to(x, y)
        elif t == "lasso" and self.lasso_pts is not None:
            if math.hypot(x - self.lasso_pts[-1][0], y - self.lasso_pts[-1][1]) * self.zoom > 2: self.lasso_pts.append((x, y))
            self._draw_overlays()
        elif t in ("marquee", "gradient", "shape", "crop"):
            if sh and t in ("marquee", "shape", "crop") and not (t == "shape" and self.opt["shape"] == "line"):
                d = max(abs(x - self.drag["x0"]), abs(y - self.drag["y0"]))
                x = self.drag["x0"] + math.copysign(d, x - self.drag["x0"]); y = self.drag["y0"] + math.copysign(d, y - self.drag["y0"])
            elif sh and t in ("gradient", "shape"):
                a = math.atan2(y - self.drag["y0"], x - self.drag["x0"]); a = round(a / (math.pi / 4)) * math.pi / 4
                r = math.hypot(x - self.drag["x0"], y - self.drag["y0"])
                x, y = self.drag["x0"] + r * math.cos(a), self.drag["y0"] + r * math.sin(a)
            self.drag["x1"], self.drag["y1"] = x, y
            if t == "crop":
                self.crop_rect = self._norm_rect(self.drag["x0"], self.drag["y0"], x, y)
            self._draw_overlays()
        self._hover(e)

    def _release(self, e):
        dr = self.drag; self.drag = None
        if not dr: return
        if dr.get("pan"): return
        if self.tf:
            self.tf_release(e); return
        t = self.tool
        if self.stroke is not None:
            st = self.stroke; self.stroke = None
            self._last_pt = st.last[:2] if st.last else None
            lab = {"brush": "Brush", "pencil": "Pencil", "eraser": "Eraser", "smudge": "Smudge", "clone": "Clone stamp",
                   "blurtool": "Blur" if self.opt["fx"] == "blur" else "Sharpen"}[t]
            if getattr(st, "target", "px") == "mask": lab += " (mask)"
            if st.bbox is not None: self.commit(lab)
            elif getattr(st, "target", "px") == "mask": st.L.mask = st.before
            else: st.L.px = st.before
            return
        if t == "selbrush":
            self._sb_end(); return
        x1, y1 = dr.get("x1", dr["x0"]), dr.get("y1", dr["y0"])
        if t == "marquee":
            if not dr["moved"]:
                self.deselect(); return
            m = C.sel_shape(self.doc.w, self.doc.h, self.opt["sel_shape"], [(dr["x0"], dr["y0"]), (x1, y1)], self.opt["feather"])
            self._set_sel(C.combine_sel(self.doc.sel, m, self._sel_op), "Marquee")
        elif t == "lasso":
            pts = self.lasso_pts or []; self.lasso_pts = None
            if len(pts) < 3 or not dr["moved"]:
                self.deselect(); self._draw_overlays(); return
            m = C.sel_shape(self.doc.w, self.doc.h, "lasso", pts, self.opt["feather"])
            self._set_sel(C.combine_sel(self.doc.sel, m, self._sel_op), "Lasso")
        elif t == "gradient" and dr["moved"]:
            self.gradient_apply((dr["x0"], dr["y0"]), (x1, y1))
        elif t == "shape" and dr["moved"]:
            self.shape_apply((dr["x0"], dr["y0"]), (x1, y1))
        elif t == "crop":
            if self.crop_rect and (self.crop_rect[2] - self.crop_rect[0] < 2 or self.crop_rect[3] - self.crop_rect[1] < 2):
                self.crop_rect = None
        self._draw_overlays()

    def _norm_rect(self, x0, y0, x1, y1):
        d = self.doc
        a, b = int(round(max(0, min(x0, x1)))), int(round(max(0, min(y0, y1))))
        c, dd = int(round(min(d.w, max(x0, x1)))), int(round(min(d.h, max(y0, y1))))
        return (a, b, c, dd)

    def _hover(self, e):
        x, y = self.to_doc(e.x, e.y); self.mouse_doc = (int(x), int(y))
        cv = self.cv; cv.delete("cur")
        if self.tool in PAINT_TOOLS and not self.space and not self.tf:
            r = max(1.5, self.opt[self._sz_key()] * self.zoom / 2)
            cv.create_oval(e.x - r, e.y - r, e.x + r, e.y + r, outline="#000000", tags="cur")
            cv.create_oval(e.x - r + 1, e.y - r + 1, e.x + r - 1, e.y + r - 1, outline="#ffffff", tags="cur")
        if not hasattr(self, "_hov_t") or time.time() - self._hov_t > 0.05:
            self._hov_t = time.time(); self._status()

    def _right_click(self, e):
        m = tk.Menu(self, tearoff=0, bg=self.ui.CARD2, fg=self.ui.TXT, activebackground=self.ui.RED, activeforeground="white",
                    font=(self.ui.FONT, 9))
        self._m_edit(m); m.tk_popup(e.x_root, e.y_root)

    def _double(self, e):
        if self.tool == "text":
            L = self.doc.layer
            if L.text: self.text_dialog(edit=self.doc.active)
        elif self.tool in ("hand",):
            self.fit()

    # ================================================================== overlays (previews drawn in view space)
    def _draw_overlays(self):
        cv = self.cv; cv.delete("ov"); d = self.drag or {}; t = self.tool
        V = self.to_view
        if t in ("marquee", "shape", "gradient") and "x1" in d:
            x0, y0 = V(d["x0"], d["y0"]); x1, y1 = V(d["x1"], d["y1"])
            if t == "marquee":
                (cv.create_oval if self.opt["sel_shape"] == "ellipse" else cv.create_rectangle)(
                    x0, y0, x1, y1, outline="white", dash=(4, 4), tags="ov")
            elif t == "gradient":
                cv.create_line(x0, y0, x1, y1, fill="white", width=2, tags="ov"); cv.create_line(x0, y0, x1, y1, fill="black", dash=(3, 3), tags="ov")
                cv.create_oval(x0 - 4, y0 - 4, x0 + 4, y0 + 4, fill=C.rgb_to_hex(self.primary), outline="white", tags="ov")
                cv.create_oval(x1 - 4, y1 - 4, x1 + 4, y1 + 4, fill=C.rgb_to_hex(self.secondary), outline="white", tags="ov")
            else:
                col = C.rgb_to_hex(self.primary); w = max(1, self.opt["stroke"] * self.zoom)
                if self.opt["shape"] == "line": cv.create_line(x0, y0, x1, y1, fill=col, width=w, capstyle="round", tags="ov")
                else:
                    fn = cv.create_oval if self.opt["shape"] == "ellipse" else cv.create_rectangle
                    if self.opt["shape_fill"]: fn(x0, y0, x1, y1, fill=col, outline="", tags="ov")
                    else: fn(x0, y0, x1, y1, outline=col, width=w, tags="ov")
        if t == "lasso" and self.lasso_pts and len(self.lasso_pts) > 1:
            pts = [c for p in self.lasso_pts for c in V(*p)]
            cv.create_line(*pts, fill="white", tags="ov"); cv.create_line(*pts, fill="black", dash=(3, 3), tags="ov")
        if t == "crop" and self.crop_rect:
            x0, y0 = V(*self.crop_rect[:2]); x1, y1 = V(*self.crop_rect[2:])
            W, H = self.to_view(self.doc.w, self.doc.h); ox, oy = self.ox, self.oy
            for r in ((ox, oy, W, y0), (ox, y1, W, H), (ox, y0, x0, y1), (x1, y0, W, y1)):
                cv.create_rectangle(*r, fill="#000000", stipple="gray50", outline="", tags="ov")
            cv.create_rectangle(x0, y0, x1, y1, outline="white", width=1, tags="ov")
            for i in (1, 2):
                cv.create_line(x0 + (x1 - x0) * i / 3, y0, x0 + (x1 - x0) * i / 3, y1, fill="#bbbbbb", tags="ov")
                cv.create_line(x0, y0 + (y1 - y0) * i / 3, x1, y0 + (y1 - y0) * i / 3, fill="#bbbbbb", tags="ov")
            cv.create_text(x0 + 4, y0 - 10, text="%d \u00d7 %d" % (self.crop_rect[2] - self.crop_rect[0], self.crop_rect[3] - self.crop_rect[1]),
                           fill="white", anchor="w", font=(self.ui.FONT, 9, "bold"), tags="ov")
        if t == "clone" and self.clone_src:
            x, y = V(*self.clone_src)
            cv.create_line(x - 8, y, x + 8, y, fill="white", tags="ov"); cv.create_line(x, y - 8, x, y + 8, fill="white", tags="ov")
            cv.create_oval(x - 5, y - 5, x + 5, y + 5, outline="black", tags="ov")
        if self.tf: self._draw_tf()

    # ================================================================== simple tools
    def pick_color(self, x, y, secondary=False):
        d = self.doc; xi, yi = int(x), int(y)
        if not (0 <= xi < d.w and 0 <= yi < d.h): return
        layers = d.layers if self.opt["sample_all"] or self.tool != "eyedropper" else [d.layer]
        if self.tool != "eyedropper": layers = d.layers
        rgb, a = C.composite(layers, xi, yi, xi + 1, yi + 1)
        if a[0, 0, 0] <= 0.01: return
        c = tuple(int(round(v * 255)) for v in rgb[0, 0]); self.set_color(c, secondary=secondary); self._remember(c)

    def _sel_f(self):
        return None if self.doc.sel is None else self.doc.sel.astype(np.float32) / 255.0

    def fill_at(self, x, y):
        d = self.doc; L = d.layer; xi, yi = int(x), int(y)
        if not (0 <= xi < d.w and 0 <= yi < d.h): return
        src = C.flatten_rgba(d) if self.opt["sample_all"] else L.px
        m = C.flood_mask(src, xi, yi, self.opt["tolerance"], self.opt["contiguous"])
        if m is None: return
        mf = m.astype(np.float32) / 255.0
        sf = self._sel_f()
        if sf is not None:
            if sf[yi, xi] <= 0: mf = sf          # clicked outside the selection: nothing to fill beyond it
            else: mf = mf * sf
        if self.edit_mask and L.mask is not None:
            v = float(np.dot(self.primary, [0.299, 0.587, 0.114]))
            L.mask = (L.mask + (v - L.mask.astype(np.float32)) * mf).clip(0, 255).astype(np.uint8)
        else:
            L.px = C.paint_where(L.px, mf, self.primary, self.opt["opacity"]); L.text = None
        self._remember(self.primary); self.commit("Fill", full=True)

    def gradient_apply(self, p0, p1):
        d = self.doc; L = d.layer
        c0 = self.primary + (255,); c1 = (self.primary + (0,)) if self.opt["grad_tr"] else (self.secondary + (255,))
        g = C.gradient(d.w, d.h, p0, p1, c0, c1, self.opt["grad"])
        sf = self._sel_f(); mf = (sf if sf is not None else np.ones((d.h, d.w), np.float32)) * self.opt["opacity"]
        L.px = C.over(L.px, g, mf); L.text = None
        self.commit("Gradient", full=True)

    def shape_apply(self, p0, p1):
        d = self.doc; k = self.opt["shape"]
        px = C.draw_shape(d.w, d.h, k, p0, p1, self.primary, self.opt["stroke"], self.opt["shape_fill"] or k == "line")
        name = {"rect": "Rectangle", "ellipse": "Ellipse", "line": "Line"}[k]
        self._insert_layer(C.Layer(name, px)); self._remember(self.primary)
        self.commit("Shape: " + name, full=True)

    # ================================================================== selections
    def _set_sel(self, m, label):
        if m is not None and not (m > 0).any(): m = None
        self.doc.sel = m; self._update_ants(); self.commit(label)

    def deselect(self):
        if self.doc.sel is not None: self._set_sel(None, "Deselect")

    def select_all(self):
        self._set_sel(np.full((self.doc.h, self.doc.w), 255, np.uint8), "Select all")

    def invert_sel(self):
        s = self.doc.sel
        self._set_sel(np.full((self.doc.h, self.doc.w), 255, np.uint8) if s is None else (255 - s), "Invert selection")

    def select_layer(self):
        a = self.doc.layer.px[..., 3]; self._set_sel(a.copy(), "Select layer pixels")

    def _sb_start(self, x, y, sub):
        self._sb = {"m": (self.doc.sel.astype(np.float32) / 255 if self.doc.sel is not None else np.zeros((self.doc.h, self.doc.w), np.float32)),
                    "sub": sub, "last": None, "t": 0}
        self._sb_to(x, y)

    def _sb_to(self, x, y):
        sb = self._sb; k = C.dab(self.opt["size"], self.opt["hardness"]); m = sb["m"]
        pts = [(x, y)]
        if sb["last"]:
            lx, ly = sb["last"]; n = int(math.hypot(x - lx, y - ly) / max(1, self.opt["size"] * 0.15))
            pts = [(lx + (x - lx) * i / max(1, n), ly + (y - ly) * i / max(1, n)) for i in range(1, n + 1)] or [(x, y)]
        for px, py in pts:
            b = C.dab_box(px, py, k, self.doc.w, self.doc.h)
            if b is None: continue
            X0, Y0, X1, Y1, kk = b
            m[Y0:Y1, X0:X1] = np.minimum(m[Y0:Y1, X0:X1], 1 - kk) if sb["sub"] else np.maximum(m[Y0:Y1, X0:X1], kk)
            # live red tint where the selection is painted
            r = max(1, self.opt["size"] * self.zoom / 2); vx, vy = self.to_view(px, py)
        sb["last"] = (x, y)
        if time.time() - sb["t"] > 0.12:
            sb["t"] = time.time(); self.doc.sel = (m * 255).astype(np.uint8); self._update_ants()

    def _sb_end(self):
        m = self._sb["m"]; self._sb = None
        self._set_sel((m * 255 + 0.5).astype(np.uint8), "Selection brush")

    # ================================================================== clipboard
    def copy(self, cut=False):
        d = self.doc; L = d.layer; sel = d.sel
        if sel is None:
            self.clip = (L.px.copy(), (0, 0)); bb = (0, 0, d.w, d.h)
        else:
            bb = C.sel_bbox(sel)
            if bb is None: return
            x0, y0, x1, y1 = bb; piece = L.px[y0:y1, x0:x1].copy()
            piece[..., 3] = (piece[..., 3].astype(np.float32) * sel[y0:y1, x0:x1] / 255.0 + 0.5).astype(np.uint8)
            self.clip = (piece, (x0, y0))
        try:
            self._sys_clip_set(Image.fromarray(self.clip[0], "RGBA"))
        except Exception:
            pass
        if cut and not L.locked:
            self.clear_sel_pixels(label="Cut")
        else:
            self._status("Copied %d \u00d7 %d px" % (self.clip[0].shape[1], self.clip[0].shape[0]))

    def _sys_clip_set(self, im):
        if os.name != "nt": return
        import io, ctypes
        b = io.BytesIO(); im.convert("RGB").save(b, "BMP"); data = b.getvalue()[14:]
        k = ctypes.windll.kernel32; u = ctypes.windll.user32
        k.GlobalAlloc.restype = ctypes.c_void_p; k.GlobalLock.restype = ctypes.c_void_p; k.GlobalLock.argtypes = [ctypes.c_void_p]
        k.GlobalUnlock.argtypes = [ctypes.c_void_p]; u.SetClipboardData.argtypes = [ctypes.c_uint, ctypes.c_void_p]
        if not u.OpenClipboard(None): return
        try:
            u.EmptyClipboard(); h = k.GlobalAlloc(0x0042, len(data)); p = k.GlobalLock(h)
            ctypes.memmove(p, data, len(data)); k.GlobalUnlock(h); u.SetClipboardData(8, h)
        finally:
            u.CloseClipboard()

    def paste(self):
        d = self.doc; piece = None; pos = None
        try:
            from PIL import ImageGrab
            im = ImageGrab.grabclipboard() if os.name == "nt" else None
        except Exception:
            im = None
        if self.clip is not None:
            piece, pos = self.clip
            if im is not None and isinstance(im, Image.Image) and im.size != (piece.shape[1], piece.shape[0]): piece = None
        if piece is None and isinstance(im, Image.Image):
            self.place_pixels(np.asarray(im.convert("RGBA")).copy(), "Pasted image", label="Paste"); return
        if piece is None and isinstance(im, list) and im:
            self.drop_files(im); return
        if piece is None:
            self._status("Nothing to paste"); return
        ph, pw = piece.shape[:2]
        if pos is None or pos[0] + pw > d.w or pos[1] + ph > d.h:
            pos = (max(0, (d.w - pw) // 2), max(0, (d.h - ph) // 2))
        px = C.blank(d.w, d.h); x0, y0 = pos; x1, y1 = min(d.w, x0 + pw), min(d.h, y0 + ph)
        px[y0:y1, x0:x1] = piece[:y1 - y0, :x1 - x0]
        if self.tf: self.tf_apply()
        self._insert_layer(C.Layer("Pasted layer", px)); d.sel = None; self._update_ants()
        self.commit("Paste", full=True)
        if self.tool != "move": self.set_tool("move")
        self.tf_start()

    def clear_sel_pixels(self, label="Clear"):
        d = self.doc; L = d.layer
        if L.locked: return
        if d.sel is None:
            self._status("Make a selection first (or use Delete layer)"); return
        px = L.px.copy(); px[..., 3] = (px[..., 3].astype(np.float32) * (1 - d.sel / 255.0) + 0.5).astype(np.uint8)
        L.px = px; L.text = None; self.commit(label, full=True)

    def fill_sel(self, secondary=False):
        d = self.doc; L = d.layer
        if L.locked: return
        mf = self._sel_f()
        if mf is None: mf = np.ones((d.h, d.w), np.float32)
        L.px = C.paint_where(L.px, mf, self.secondary if secondary else self.primary); L.text = None
        self.commit("Fill selection", full=True)

    # ================================================================== layers
    def _insert_layer(self, l):
        self.doc.layers.insert(self.doc.active + 1, l); self.doc.active += 1; self.edit_mask = False

    def _next_name(self):
        n = 1; names = {l.name for l in self.doc.layers}
        while "Layer %d" % n in names: n += 1
        return "Layer %d" % n

    def add_layer(self):
        self._insert_layer(C.Layer(self._next_name(), C.blank(self.doc.w, self.doc.h))); self.commit("New layer")

    def dup_layer(self):
        L = self.doc.layer; self._insert_layer(L.clone(deep=True, name=L.name + " copy")); self.commit("Duplicate layer", full=True)

    def del_layer(self):
        d = self.doc
        if len(d.layers) <= 1:
            d.layers[0] = C.Layer("Layer 1", C.blank(d.w, d.h))
        else:
            d.layers.pop(d.active); d.active = max(0, d.active - 1)
        self.edit_mask = False; self.commit("Delete layer", full=True)

    def toggle_mask(self):
        L = self.doc.layer
        if L.mask is None:
            L.mask = np.full((self.doc.h, self.doc.w), 255, np.uint8) if self.doc.sel is None else self.doc.sel.copy()
            self.edit_mask = True; self.commit("Add layer mask", full=True)
            self._status("Layer mask added - paint black to hide, white to show")
        else:
            if messagebox.askyesno("Layer mask", "Apply the mask to the layer?\n(No = just remove it)", parent=self):
                px = L.px.copy(); px[..., 3] = (px[..., 3].astype(np.float32) * L.mask / 255.0 + 0.5).astype(np.uint8); L.px = px
            L.mask = None; self.edit_mask = False; self.commit("Remove layer mask", full=True)

    def _bake(self, l):
        """layer -> straight RGBA with its opacity and mask baked in (Normal)"""
        px = l.px.copy(); a = px[..., 3].astype(np.float32) * l.opacity
        if l.mask is not None: a *= l.mask / 255.0
        px[..., 3] = (a + 0.5).astype(np.uint8); return px

    def merge_down(self):
        d = self.doc
        if d.active == 0: self._status("Nothing below to merge into"); return
        up, lo = d.layers[d.active], d.layers[d.active - 1]
        lo2 = lo.clone(deep=False); lo2.id = C.new_id()
        base = C.Layer("tmp", self._bake(lo)); top = C.Layer("tmp2", up.px, mask=up.mask, opacity=up.opacity, blend=up.blend)
        rgb, a = C.composite([base, top], 0, 0, d.w, d.h)
        merged = C.Layer(lo.name, C.to_u8(rgb, a), visible=lo.visible)
        d.layers[d.active - 1:d.active + 1] = [merged]; d.active -= 1; self.edit_mask = False
        self.commit("Merge down", full=True)

    def flatten(self):
        d = self.doc; px = C.flatten_rgba(d)
        d.layers = [C.Layer("Background", px)]; d.active = 0; self.edit_mask = False
        self.commit("Flatten image", full=True)

    def merge_visible(self):
        d = self.doc; px = C.flatten_rgba(d)
        d.layers = [l for l in d.layers if not l.visible] + [C.Layer("Merged", px)]; d.active = len(d.layers) - 1
        self.commit("Merge visible", full=True)

    def flip_layer(self, horiz=True):
        L = self.doc.layer
        if L.locked: return
        L.px = np.ascontiguousarray(L.px[:, ::-1] if horiz else L.px[::-1]); L.text = None
        if L.mask is not None: L.mask = np.ascontiguousarray(L.mask[:, ::-1] if horiz else L.mask[::-1])
        self.commit("Flip layer " + ("horizontal" if horiz else "vertical"), full=True)

    # ================================================================== canvas-level ops
    def _all_arrays(self, fn):
        d = self.doc
        for l in d.layers:
            l.px = np.ascontiguousarray(fn(l.px))
            if l.mask is not None: l.mask = np.ascontiguousarray(fn(l.mask))
            l.text = None if l.text else None
        if d.sel is not None: d.sel = np.ascontiguousarray(fn(d.sel))
        d.h, d.w = d.layers[0].px.shape[:2]

    def rotate_canvas(self, k):
        self.cancel_modes(); self._all_arrays(lambda a: np.rot90(a, k))
        self.hist.push({1: "Rotate canvas 90\u00b0 CCW", -1: "Rotate canvas 90\u00b0 CW", 2: "Rotate canvas 180\u00b0"}[k]); self._doc_changed()

    def flip_canvas(self, horiz=True):
        self.cancel_modes(); self._all_arrays(lambda a: a[:, ::-1] if horiz else a[::-1])
        self.hist.push("Flip canvas " + ("horizontal" if horiz else "vertical")); self.after_history()

    def crop_to(self, r, label="Crop"):
        x0, y0, x1, y1 = r
        if x1 - x0 < 1 or y1 - y0 < 1: return
        self._all_arrays(lambda a: a[y0:y1, x0:x1])
        for l in self.doc.layers:
            if l.text: l.text = None
        self.hist.push(label); self._doc_changed()

    def crop_apply(self):
        if self.crop_rect: r = self.crop_rect; self.crop_rect = None; self.crop_to(r)
        self._draw_overlays()

    def crop_cancel(self):
        self.crop_rect = None; self._draw_overlays()

    def crop_to_sel(self):
        if self.doc.sel is None: self._status("Make a selection first"); return
        bb = C.sel_bbox(self.doc.sel)
        if bb: self.doc.sel = None; self.crop_to(bb, "Crop to selection")

    def canvas_size_dialog(self):
        d = self.doc
        w = simpledialog.askinteger("Image size", "New width in px (height keeps the aspect ratio):", initialvalue=d.w,
                                    minvalue=1, maxvalue=C.MAX_SIDE, parent=self)
        if not w or w == d.w: return
        h = max(1, int(round(d.h * w / d.w)))
        if h > C.MAX_SIDE: return
        inter = cv2.INTER_AREA if w < d.w else cv2.INTER_CUBIC
        def rs(a):
            if a.ndim == 2: return cv2.resize(a, (w, h), interpolation=inter)
            pm = a.astype(np.float32); pm[..., :3] *= pm[..., 3:4] / 255.0
            o = cv2.resize(pm, (w, h), interpolation=inter); al = np.clip(o[..., 3:4], 0, 255)
            rgb = np.where(al > 0.5, o[..., :3] / np.maximum(al, 1e-3) * 255.0, 0)
            return np.concatenate([rgb.clip(0, 255), al], 2).astype(np.uint8)
        self.cancel_modes(); self._all_arrays(rs)
        self.hist.push("Image size %d \u00d7 %d" % (w, h)); self._doc_changed()

    # ================================================================== free transform / move
    def tf_start(self, move=False):
        if self.tf: return
        d = self.doc; L = d.layer
        if L.locked: self._status("This layer is locked"); return
        if self.edit_mask: self.edit_mask = False
        src = L.px
        if d.sel is not None:
            sf = d.sel.astype(np.float32) / 255.0
            piece = src.copy(); piece[..., 3] = (src[..., 3] * sf + 0.5).astype(np.uint8)
            rest = src.copy(); rest[..., 3] = (src[..., 3] * (1 - sf) + 0.5).astype(np.uint8)
            bb = C.sel_bbox(d.sel)
        else:
            piece = src; rest = C.blank(d.w, d.h); bb = C.content_bbox(src)
        if bb is None:
            self._status("Nothing to transform on this layer"); return
        self.tf = {"src": src, "piece": piece, "rest": rest, "bb": bb, "sx": 1.0, "sy": 1.0, "ang": 0.0, "tx": 0.0, "ty": 0.0,
                   "move": move, "prevR": None, "flipx": 1, "flipy": 1, "sel0": d.sel, "L": L}
        L.px = rest.copy(); self.tf["work"] = L.px; L.text = None
        self._tf_update()
        if not move: self._opts_refresh()

    def _tf_M(self, tf=None):
        tf = tf or self.tf; x0, y0, x1, y1 = tf["bb"]; cx, cy = (x0 + x1) / 2.0, (y0 + y1) / 2.0
        return C.tf_matrix(cx, cy, tf["sx"] * tf["flipx"], tf["sy"] * tf["flipy"], tf["ang"], tf["tx"], tf["ty"])

    def _tf_corners(self):
        x0, y0, x1, y1 = self.tf["bb"]; M = self._tf_M()
        pts = np.array([[x0, y0, 1], [x1, y0, 1], [x1, y1, 1], [x0, y1, 1]], np.float64)
        return (M @ pts.T).T

    def _tf_update(self):
        tf = self.tf; d = self.doc; M = self._tf_M(); c = self._tf_corners()
        R = (max(0, int(c[:, 0].min()) - 2), max(0, int(c[:, 1].min()) - 2), min(d.w, int(c[:, 0].max()) + 3), min(d.h, int(c[:, 1].max()) + 3))
        P = tf["prevR"]; work = tf["work"]; rest = tf["rest"]
        if P is not None and P[2] > P[0] and P[3] > P[1]:
            work[P[1]:P[3], P[0]:P[2]] = rest[P[1]:P[3], P[0]:P[2]]
        if R[2] > R[0] and R[3] > R[1]:
            M2 = M.copy(); M2[0, 2] -= R[0]; M2[1, 2] -= R[1]
            interp = cv2.INTER_NEAREST if (tf["sx"] == 1 and tf["sy"] == 1 and tf["ang"] == 0 and float(tf["tx"]).is_integer()
                                           and float(tf["ty"]).is_integer()) else cv2.INTER_LINEAR
            warped = C.affine(tf["piece"], M2, R[2] - R[0], R[3] - R[1], interp)
            work[R[1]:R[3], R[0]:R[2]] = C.over(rest[R[1]:R[3], R[0]:R[2]], warped)
        U = R if P is None else (min(R[0], P[0]), min(R[1], P[1]), max(R[2], P[2]), max(R[3], P[3]))
        tf["prevR"] = R
        self.refresh(U); self._tf_label()

    def _tf_preview(self):
        """fast view-resolution preview while dragging a transform: everything is composited at screen size."""
        from types import SimpleNamespace as NS
        tf = self.tf; d = self.doc; z = self.zoom
        vx0, vy0, vx1, vy1, tw, th = self._view_rect()
        if vx1 <= vx0 or vy1 <= vy0: return False
        key = (z, self.ox, self.oy, tw, th, d.active)
        pc = tf.get("pv")
        if pc is None or pc["key"] != key:
            def small(a, interp=cv2.INTER_AREA):
                return None if a is None else cv2.resize(np.ascontiguousarray(a[vy0:vy1, vx0:vx1]), (tw, th), interpolation=interp)
            L = tf["L"]; i = d.layers.index(L)
            bg = self._tf_bg(small)
            mk = lambda l: NS(id=l.id, visible=True, opacity=l.opacity, blend=l.blend, px=small(l.px), mask=small(l.mask))
            rgb, _ = C.composite([mk(l) for l in d.layers[:i] if l.visible], 0, 0, tw, th, bg=bg)
            under = np.concatenate([C.to_u8(rgb), np.full((th, tw, 1), 255, np.uint8)], 2)
            pc = tf["pv"] = {"key": key, "under": NS(id=-1, visible=True, opacity=1.0, blend="Normal", px=under, mask=None),
                             "above": [mk(l) for l in d.layers[i + 1:] if l.visible], "rest": small(tf["rest"]), "mask": small(L.mask), "L": L}
        M = self._tf_M(); fx, fy = tw / (vx1 - vx0), th / (vy1 - vy0)
        if pc.get("pm_f") != (fx, fy):
            # piece scaled to screen size once (premultiplied), so each drag step only warps a screen-sized image
            x0, y0, x1, y1 = tf["bb"]; pad = 2
            x0, y0, x1, y1 = max(0, x0 - pad), max(0, y0 - pad), min(d.w, x1 + pad), min(d.h, y1 + pad)
            crop = tf["piece"][y0:y1, x0:x1]; sw, sh_ = max(1, int(round((x1 - x0) * fx))), max(1, int(round((y1 - y0) * fy)))
            pm = crop.astype(np.float32); pm[..., :3] *= pm[..., 3:4] / 255.0
            pc["pm"] = cv2.resize(pm, (sw, sh_), interpolation=cv2.INTER_AREA)
            pc["pm_D"] = np.array([[sw / (x1 - x0), 0, -x0 * sw / (x1 - x0)], [0, sh_ / (y1 - y0), -y0 * sh_ / (y1 - y0)], [0, 0, 1]])
            pc["pm_f"] = (fx, fy)
        S = np.array([[fx, 0, -vx0 * fx], [0, fy, -vy0 * fy], [0, 0, 1]])
        Mv = (S @ np.vstack([M, [0, 0, 1]]) @ np.linalg.inv(pc["pm_D"]))[:2]
        L = pc["L"]
        if L.blend == "Normal" and pc["mask"] is None and all(l.blend == "Normal" for l in pc["above"]):
            # fast path: premultiplied float at screen size, and only inside the old + new box on screen
            if "under_f" not in pc:
                pc["under_f"] = pc["under"].px[..., :3].astype(np.float32) * (1 / 255.0)
                r = pc["rest"].astype(np.float32) * (1 / 255.0); r[..., :3] *= r[..., 3:4]; pc["rest_f"] = r
                if pc["above"]:
                    rgb2, a2 = C.composite(pc["above"], 0, 0, tw, th); pc["above_P"] = rgb2 * a2; pc["above_a"] = a2
            def comp(x0, y0, x1, y1, w4):
                act = pc["rest_f"][y0:y1, x0:x1] * (1 - w4[..., 3:4]) if w4 is not None else pc["rest_f"][y0:y1, x0:x1].copy()
                if w4 is not None: act += w4
                sa = act[..., 3:4] * L.opacity
                res = pc["under_f"][y0:y1, x0:x1] * (1 - sa); res += act[..., :3] * L.opacity
                if "above_P" in pc: res *= (1 - pc["above_a"][y0:y1, x0:x1]); res += pc["above_P"][y0:y1, x0:x1]
                res *= 255.0
                return cv2.convertScaleAbs(res)
            if "base" not in pc:
                pc["base"] = comp(0, 0, tw, th, None); pc["buf"] = pc["base"].copy(); pc["prevR"] = None
            x0, y0, x1, y1 = tf["bb"]
            cd = np.array([[x0, y0, 1], [x1, y0, 1], [x1, y1, 1], [x0, y1, 1]], np.float64).T
            V = (S @ np.vstack([M, [0, 0, 1]]) @ cd)[:2]
            R = (max(0, int(V[0].min()) - 2), max(0, int(V[1].min()) - 2), min(tw, int(V[0].max()) + 3), min(th, int(V[1].max()) + 3))
            buf = pc["buf"]; P_ = pc["prevR"]
            if P_ is not None: buf[P_[1]:P_[3], P_[0]:P_[2]] = pc["base"][P_[1]:P_[3], P_[0]:P_[2]]
            if R[2] > R[0] and R[3] > R[1]:
                Mr = Mv.copy(); Mr[0, 2] -= R[0]; Mr[1, 2] -= R[1]
                out = cv2.warpAffine(pc["pm"], Mr, (R[2] - R[0], R[3] - R[1]), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT, borderValue=(0, 0, 0, 0))
                out *= (1 / 255.0)
                buf[R[1]:R[3], R[0]:R[2]] = comp(R[0], R[1], R[2], R[3], out)
            pc["prevR"] = R
            self.render_view(preview=buf); self._tf_label(); tf["stale"] = True
            return True
        out = cv2.warpAffine(pc["pm"], Mv, (tw, th), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT, borderValue=(0, 0, 0, 0))
        al = out[..., 3:4]; rgb_ = np.where(al > 0.5, out[..., :3] / np.maximum(al, 1e-3) * 255.0, 0)
        warped = np.concatenate([(rgb_ + 0.5).clip(0, 255), (al + 0.5).clip(0, 255)], 2).astype(np.uint8)
        act = NS(id=L.id, visible=L.visible, opacity=L.opacity, blend=L.blend, px=C.over(pc["rest"], warped), mask=pc["mask"])
        rgb, _ = C.composite([pc["under"], act] + pc["above"], 0, 0, tw, th, bg=np.zeros((th, tw, 1), np.float32))
        self.render_view(preview=C.to_u8(rgb)); self._tf_label(); tf["stale"] = True
        return True

    def _tf_bg(self, small):
        return small(self.chk)[..., None].astype(np.float32) * (1 / 255.0)

    def _tf_label(self):
        if self.tf and getattr(self, "tf_lbl", None) is not None and self.tf_lbl.winfo_exists():
            t = self.tf; self.tf_lbl.configure(text="W %d%%   H %d%%   \u2220 %.1f\u00b0   X %+d  Y %+d" % (
                t["sx"] * 100, t["sy"] * 100, t["ang"], t["tx"], t["ty"]))

    def _draw_tf(self):
        cv = self.cv; c = self._tf_corners(); V = [self.to_view(x, y) for x, y, in c[:, :2]]
        if self.tf["move"]: return
        cv.create_polygon(*[v for p in V for v in p], outline="white", fill="", tags="ov")
        cv.create_polygon(*[v for p in V for v in p], outline="black", dash=(3, 3), fill="", tags="ov")
        hs = []
        for i in range(4):
            a, b = V[i], V[(i + 1) % 4]; hs.append(a); hs.append(((a[0] + b[0]) / 2, (a[1] + b[1]) / 2))
        for x, y in hs:
            cv.create_rectangle(x - 4, y - 4, x + 4, y + 4, fill="white", outline="black", tags="ov")
        cx, cy = np.mean([p[0] for p in V]), np.mean([p[1] for p in V])
        cv.create_oval(cx - 4, cy - 4, cx + 4, cy + 4, outline="white", tags="ov")
        tx_, ty_ = hs[1]; dx, dy = tx_ - cx, ty_ - cy; n = math.hypot(dx, dy) or 1.0
        rx, ry = tx_ + dx / n * 28, ty_ + dy / n * 28
        cv.create_line(tx_, ty_, rx, ry, fill="black", width=3, tags="ov"); cv.create_line(tx_, ty_, rx, ry, fill="white", tags="ov")
        cv.create_oval(rx - 6, ry - 6, rx + 6, ry + 6, fill=self.ui.RED, outline="white", width=2, tags="ov")
        self._tf_rot = (rx, ry)
        self._tf_handles = hs

    def tf_press(self, e, shift, force=None):
        tf = self.tf
        if tf is None: return
        x, y = self.to_doc(e.x, e.y); mode = force
        if mode is None:
            rot = getattr(self, "_tf_rot", None)
            if rot is not None and math.hypot(rot[0] - e.x, rot[1] - e.y) <= 10: mode = "rotate"
        if mode is None:
            hs = getattr(self, "_tf_handles", [])
            for i, (hx, hy) in enumerate(hs):
                if abs(hx - e.x) <= 8 and abs(hy - e.y) <= 8: mode = ("corner" if i % 2 == 0 else "edge", i); break
            if mode is None:
                c = self._tf_corners()[:, :2]
                inside = cv2.pointPolygonTest(c.astype(np.float32).reshape(-1, 1, 2), (float(x), float(y)), True) >= -6 / self.zoom
                if inside: mode = "move"
                elif tf["move"] or shift and False: mode = "rotate"
                else:
                    # click outside the box applies it; with the Move tool the same click can pick another layer
                    self.tf_apply(); tf = None
                    if self.tool == "move" and self.opt.get("move_box", True): self._move_press(e, shift)
                    else: self.drag = None
                    return
        x0, y0, x1, y1 = tf["bb"]
        M = self._tf_M(); ux, uy = M[:, 0] / (np.linalg.norm(M[:, 0]) or 1), M[:, 1] / (np.linalg.norm(M[:, 1]) or 1)
        dg = {"mode": mode, "x": x, "y": y, "sx": tf["sx"], "sy": tf["sy"], "ang": tf["ang"], "tx": tf["tx"], "ty": tf["ty"],
              "cx": (x0 + x1) / 2 + tf["tx"], "cy": (y0 + y1) / 2 + tf["ty"], "ux": ux, "uy": uy}
        if isinstance(mode, tuple):
            c = self._tf_corners()[:, :2]; H = []
            for i in range(4): H.append(c[i]); H.append((c[i] + c[(i + 1) % 4]) / 2)
            j = mode[1]; anc = H[(j + 4) % 8]; hv = H[j] - anc
            px_, py_ = float(hv @ ux), float(hv @ uy)
            dg["anc"] = anc; dg["sgx"] = 0 if abs(px_) < 1e-6 else (1 if px_ > 0 else -1)
            dg["sgy"] = 0 if abs(py_) < 1e-6 else (1 if py_ > 0 else -1)
            if mode[0] == "edge":
                if j in (1, 5): dg["sgx"] = 0
                else: dg["sgy"] = 0
        tf["drag"] = dg

    def tf_drag(self, e):
        tf = self.tf; dg = tf.get("drag")
        if not dg: return
        sh, ctrl, alt = self._mods(e)
        x, y = self.to_doc(e.x, e.y); m = dg["mode"]
        if m == "move":
            tf["tx"] = dg["tx"] + round(x - dg["x"]); tf["ty"] = dg["ty"] + round(y - dg["y"])
        elif m == "rotate":
            a0 = math.atan2(dg["y"] - dg["cy"], dg["x"] - dg["cx"]); a1 = math.atan2(y - dg["cy"], x - dg["cx"])
            ang = dg["ang"] + math.degrees(a1 - a0)
            if sh: ang = round(ang / 15) * 15
            tf["ang"] = ang
        else:
            # resize from the opposite handle (anchored); Shift on a corner keeps the aspect ratio
            kind, i = m; x0, y0, x1, y1 = tf["bb"]; bw, bh = max(1.0, x1 - x0), max(1.0, y1 - y0)
            ux, uy, anc, sgx, sgy = dg["ux"], dg["uy"], dg["anc"], dg["sgx"], dg["sgy"]
            v = np.array([x, y], np.float64) - anc
            sx, sy = dg["sx"], dg["sy"]
            if sgx: sx = max(2.0, float(v @ ux) * sgx) / bw
            if sgy: sy = max(2.0, float(v @ uy) * sgy) / bh
            if kind == "corner" and sh:
                W0, H0 = dg["sx"] * bw, dg["sy"] * bh
                k = max(2.0 / min(W0, H0), (sx * bw * W0 + sy * bh * H0) / (W0 * W0 + H0 * H0))
                sx, sy = dg["sx"] * k, dg["sy"] * k
            ctr = anc + ux * sgx * sx * bw / 2.0 + uy * sgy * sy * bh / 2.0
            if not sgx: ctr = ctr  # edge: anchor already sits on the centre line
            tf["sx"], tf["sy"] = sx, sy
            tf["tx"] = float(ctr[0] - (x0 + x1) / 2.0); tf["ty"] = float(ctr[1] - (y0 + y1) / 2.0)
        try: ok = self._tf_preview()
        except Exception: ok = False
        if not ok: self._tf_update()
        self._draw_overlays()

    def tf_release(self, e):
        if self.tf:
            self.tf["drag"] = None
            if self.tf.get("stale"): self.tf["stale"] = False; self._tf_update()
        if self.tf and self.tf["move"]: self.tf_apply(label="Move")

    def tf_flip(self, horiz):
        if not self.tf: return
        if horiz: self.tf["flipx"] *= -1
        else: self.tf["flipy"] *= -1
        self._tf_update(); self._draw_overlays()

    def tf_apply(self, label="Free transform"):
        tf = self.tf
        if not tf: return
        if tf.get("stale"): tf["stale"] = False; self._tf_update()
        self.tf = None; d = self.doc
        moved = not (tf["sx"] == 1 and tf["sy"] == 1 and tf["ang"] == 0 and tf["tx"] == 0 and tf["ty"] == 0 and tf["flipx"] == 1 and tf["flipy"] == 1)
        if not moved:
            tf["L"].px = tf["src"]; self._opts_refresh(); self.full_refresh(); return
        if d.sel is not None:
            M = self._tf_M(tf)
            d.sel = cv2.warpAffine(d.sel, M, (d.w, d.h), flags=cv2.INTER_LINEAR, borderValue=0); self._update_ants()
        self._opts_refresh(); self.commit(label, full=True)

    def tf_cancel(self):
        tf = self.tf
        if not tf: return
        self.tf = None; tf["L"].px = tf["src"]; self._opts_refresh(); self.full_refresh(); self._draw_overlays()

    def cancel_modes(self):
        if self.tf: self.tf_apply()
        self.crop_rect = None; self.lasso_pts = None; self.drag = None
        if self.stroke is not None:
            st = self.stroke; self.stroke = None
            if st.bbox is not None: self.commit("Brush")

    # ================================================================== dialogs
    def _dlg(self, title, w=420, h=300):
        ui = self.ui; t = tk.Toplevel(self); t.title(title); t.configure(bg=ui.PANEL); t.transient(self.app)
        t.resizable(False, False)
        self.update_idletasks()
        x = self.app.winfo_rootx() + (self.app.winfo_width() - w) // 2; y = self.app.winfo_rooty() + (self.app.winfo_height() - h) // 3
        t.geometry("%dx%d+%d+%d" % (w, h, max(0, x), max(0, y)))
        t.after(30, lambda: t.winfo_exists() and t.geometry("+%d+%d" % (max(0, x), max(0, y))))
        tk.Label(t, text=title.upper(), bg=ui.PANEL, fg=ui.TXT, font=(ui.FONT, 11, "bold"), anchor="w").pack(fill="x", padx=16, pady=(14, 8))
        try: t.grab_set()
        except Exception: pass
        return t

    def _dlg_buttons(self, t, ok, cancel=None, ok_text="OK"):
        ui = self.ui; row = tk.Frame(t, bg=ui.PANEL); row.pack(side="bottom", fill="x", padx=16, pady=14)
        ui.Btn(row, ok_text, ok, fill=ui.RED, fg="white", hover=ui.RED2, padx=18).pack(side="right")
        ui.Btn(row, "Cancel", cancel or t.destroy, padx=14).pack(side="right", padx=(0, 8))
        t.bind("<Return>", lambda e: ok()); t.bind("<Escape>", lambda e: (cancel or t.destroy)())
        self._last_dlg = (t, ok)

    def new_dialog(self):
        ui = self.ui; t = self._dlg("New canvas", 440, 340)
        f = tk.Frame(t, bg=ui.PANEL); f.pack(fill="x", padx=16)
        names = [p[0] for p in C.PRESETS] + ["Custom"]
        pv = tk.StringVar(value=names[0]); wv = tk.StringVar(value="1920"); hv = tk.StringVar(value="1080")
        tk.Label(f, text="Size", bg=ui.PANEL, fg=ui.MUT, font=(ui.FONT, 9)).grid(row=0, column=0, sticky="w", pady=4)
        cb = ttk.Combobox(f, textvariable=pv, values=names, state="readonly", width=30, font=(ui.FONT, 9)); cb.grid(row=0, column=1, columnspan=3, sticky="w")
        def preset(e=None):
            for p in C.PRESETS:
                if p[0] == pv.get(): wv.set(str(p[1])); hv.set(str(p[2]))
        cb.bind("<<ComboboxSelected>>", preset)
        E = lambda var: tk.Entry(f, textvariable=var, width=8, bg=ui.CARD2, fg=ui.TXT, insertbackground=ui.TXT, relief="flat",
                                 font=(ui.FONT, 10), highlightthickness=1, highlightbackground=ui.BORDER, highlightcolor=ui.RED)
        tk.Label(f, text="Width", bg=ui.PANEL, fg=ui.MUT, font=(ui.FONT, 9)).grid(row=1, column=0, sticky="w", pady=6)
        ew = E(wv); ew.grid(row=1, column=1, sticky="w")
        tk.Label(f, text="Height", bg=ui.PANEL, fg=ui.MUT, font=(ui.FONT, 9)).grid(row=1, column=2, sticky="w", padx=(12, 4))
        eh = E(hv); eh.grid(row=1, column=3, sticky="w")
        for e_ in (ew, eh): e_.bind("<KeyRelease>", lambda e: pv.set("Custom"))
        tk.Label(f, text="Background", bg=ui.PANEL, fg=ui.MUT, font=(ui.FONT, 9)).grid(row=2, column=0, sticky="w", pady=(10, 4))
        bgv = tk.StringVar(value="white")
        bf = tk.Frame(f, bg=ui.PANEL); bf.grid(row=2, column=1, columnspan=3, sticky="w", pady=(10, 4))
        for txt, v in (("White", "white"), ("Black", "black"), ("Transparent", "transparent"), ("2nd colour", "secondary")):
            tk.Radiobutton(bf, text=txt, variable=bgv, value=v, bg=ui.PANEL, fg=ui.TXT, selectcolor=ui.CARD2, activebackground=ui.PANEL,
                           activeforeground=ui.TXT, font=(ui.FONT, 9), highlightthickness=0).pack(side="left", padx=(0, 8))
        tk.Label(t, text="Up to %d px per side. 4K = 3840 \u00d7 2160." % C.MAX_SIDE, bg=ui.PANEL, fg=ui.DIM, font=(ui.FONT, 8)).pack(anchor="w", padx=16, pady=6)
        def ok():
            try: w, h = int(wv.get()), int(hv.get())
            except ValueError: return
            if not (1 <= w <= C.MAX_SIDE and 1 <= h <= C.MAX_SIDE): return
            if not self._maybe_save(): return
            bg = {"white": (255, 255, 255, 255), "black": (0, 0, 0, 255), "transparent": None,
                  "secondary": tuple(self.secondary) + (255,)}[bgv.get()]
            t.destroy(); self.new_doc(w, h, bg); self._status("New canvas %d \u00d7 %d" % (w, h))
        self._dlg_buttons(t, ok, ok_text="Create")

    def text_dialog(self, at=None, edit=None):
        ui = self.ui; d = self.doc
        if self.fonts is None:
            self._status("Loading fonts\u2026"); self.update_idletasks(); self.fonts = C.system_fonts()
        fonts = list(self.fonts.keys()) or ["(default)"]
        if edit is not None:
            L = d.layers[edit]; T = dict(L.text)
        else:
            dflt = next((n for n in ("Segoe UI Bold", "Segoe UI", "Arial Bold", "Arial", "DejaVu Sans Bold", "DejaVu Sans") if n in self.fonts), fonts[0])
            T = {"text": "", "font": self.opt["font"] or dflt, "size": self.opt["font_size"], "color": list(self.primary),
                 "x": int(at[0]), "y": int(at[1]), "align": "left"}
        t = self._dlg("Edit text" if edit is not None else "Add text", 480, 360)
        txt = tk.Text(t, height=5, bg=ui.CARD, fg=ui.TXT, insertbackground=ui.RED, relief="flat", font=(ui.FONT, 12), padx=8, pady=6,
                      highlightthickness=1, highlightbackground=ui.BORDER, highlightcolor=ui.RED, wrap="word")
        txt.pack(fill="x", padx=16); txt.insert("1.0", T["text"] or "Your text"); txt.tag_add("sel", "1.0", "end"); txt.focus_set()
        row = tk.Frame(t, bg=ui.PANEL); row.pack(fill="x", padx=16, pady=8)
        fv = tk.StringVar(value=T["font"] if T["font"] in self.fonts else fonts[0])
        ttk.Combobox(row, textvariable=fv, values=fonts, width=28, font=(ui.FONT, 9)).pack(side="left")
        sv = tk.StringVar(value=str(int(T["size"])))
        tk.Label(row, text="Size", bg=ui.PANEL, fg=ui.MUT, font=(ui.FONT, 9)).pack(side="left", padx=(10, 4))
        tk.Spinbox(row, from_=6, to=1000, textvariable=sv, width=5, bg=ui.CARD2, fg=ui.TXT, buttonbackground=ui.CARD2, relief="flat",
                   font=(ui.FONT, 10), insertbackground=ui.TXT).pack(side="left")
        row2 = tk.Frame(t, bg=ui.PANEL); row2.pack(fill="x", padx=16)
        col = [tuple(T["color"])]
        chip = tk.Label(row2, text="   Colour   ", bg=C.rgb_to_hex(col[0]), fg="white" if sum(col[0]) < 380 else "black",
                        font=(ui.FONT, 9, "bold"), cursor="hand2", padx=6, pady=4); chip.pack(side="left")
        def pickc(e=None):
            from tkinter import colorchooser
            c = colorchooser.askcolor(color=C.rgb_to_hex(col[0]), parent=t)
            if c and c[0]:
                col[0] = tuple(int(v) for v in c[0]); chip.configure(bg=C.rgb_to_hex(col[0]), fg="white" if sum(col[0]) < 380 else "black"); prev()
        chip.bind("<Button-1>", pickc)
        av = tk.StringVar(value=T.get("align", "left"))
        for a in ("left", "center", "right"):
            tk.Radiobutton(row2, text=a.title(), variable=av, value=a, bg=ui.PANEL, fg=ui.TXT, selectcolor=ui.CARD2, activebackground=ui.PANEL,
                           font=(ui.FONT, 9), highlightthickness=0, command=lambda: prev()).pack(side="left", padx=(10, 0))
        tk.Label(t, text="Live preview on the canvas. Move the text later with the Move tool.", bg=ui.PANEL, fg=ui.DIM,
                 font=(ui.FONT, 8)).pack(anchor="w", padx=16, pady=(8, 0))
        lid = d.layers[edit].id if edit is not None else -1
        state = {"job": None}
        def build():
            try: size = max(6, min(1000, int(sv.get())))
            except ValueError: size = 72
            fp = self.fonts.get(fv.get(), "")
            T2 = dict(T, text=txt.get("1.0", "end-1c"), font=fv.get(), font_path=fp, size=size, color=list(col[0]), align=av.get())
            return T2, C.render_text(d.w, d.h, T2)
        def prev(*_):
            if state["job"]: t.after_cancel(state["job"])
            state["job"] = t.after(120, prev_now)
        def prev_now():
            state["job"] = None
            if not t.winfo_exists(): return
            T2, px = build()
            if edit is not None:
                self.full_refresh(override={lid: (px, d.layers[edit].mask)})
            else:
                tmp = C.Layer("t", px); ls = d.layers[:d.active + 1] + [tmp] + d.layers[d.active + 1:]
                save = d.layers; d.layers = ls; self.full_refresh(); d.layers = save
        txt.bind("<KeyRelease>", prev); fv.trace_add("write", prev); sv.trace_add("write", prev)
        def ok():
            T2, px = build()
            if not T2["text"].strip(): cancel(); return
            t.destroy(); self.opt["font"] = T2["font"]; self.opt["font_size"] = T2["size"]
            if edit is not None:
                L = d.layers[edit]; L.px = px; L.text = T2; self.commit("Edit text", full=True)
            else:
                nm = T2["text"].strip().splitlines()[0][:24]
                self._insert_layer(C.Layer(nm, px, text=T2)); self.commit("Text", full=True)
            self._remember(col[0])
        def cancel():
            t.destroy(); self.full_refresh()
        self._dlg_buttons(t, ok, cancel, ok_text="Add" if edit is None else "Update")
        t.unbind("<Return>"); t.bind("<Control-Return>", lambda e: ok())
        prev()

    ADJ = {
        "brightness": ("Brightness / Contrast", [("brightness", "Brightness", -100, 100, 0), ("contrast", "Contrast", -100, 100, 0)]),
        "huesat": ("Hue / Saturation", [("hue", "Hue", -180, 180, 0), ("saturation", "Saturation", -100, 100, 0), ("lightness", "Lightness", -100, 100, 0)]),
        "levels": ("Levels", [("in_black", "Input black", 0, 254, 0), ("gamma", "Midtones (gamma) x100", 10, 400, 100),
                              ("in_white", "Input white", 1, 255, 255), ("out_black", "Output black", 0, 255, 0), ("out_white", "Output white", 0, 255, 255)]),
        "blur": ("Gaussian blur", [("radius", "Radius (px)", 0.3, 100, 4)]),
        "sharpen": ("Sharpen", [("amount", "Amount %", 0, 300, 80), ("radius", "Radius (px)", 0.3, 10, 1.5)]),
        "noise": ("Add noise", [("amount", "Amount %", 0, 60, 8)]),
        "cinematic": ("Cinematic look", [("strength", "Strength %", 0, 100, 100)]),
    }

    def adjust_now(self, kind, p=None, label=None):
        d = self.doc; L = d.layer
        if L.locked: self._status("This layer is locked"); return
        L.px = C.apply_in_sel(L.px, C.adjust(L.px, kind, p or {}), d.sel); L.text = None
        self.commit(label or kind.title(), full=True)

    def adjust_dialog(self, kind, preset=None):
        d = self.doc; L = d.layer; ui = self.ui
        if L.locked: self._status("This layer is locked"); return
        title, params = self.ADJ[kind]
        if preset: title = "Cinematic: " + preset
        t = self._dlg(title, 460, 140 + 46 * len(params) + (40 if kind in ("noise", "cinematic") else 0))
        vals = {}; src = L.px; lid = L.id; state = {"job": None}
        # preview on a reduced copy for big canvases (fast), the final result is full resolution
        def cur():
            p = {}
            for k, _, lo, hi, dv in params:
                v = vals[k].get(); p[k] = v / 100.0 if k == "gamma" else v
            if kind == "cinematic": p["preset"] = pv.get()
            if kind == "noise": p["mono"] = mono.get()
            return p
        def prev(*_):
            if state["job"]: t.after_cancel(state["job"])
            state["job"] = t.after(90, prev_now)
        def prev_now():
            state["job"] = None
            if not t.winfo_exists(): return
            out = C.apply_in_sel(src, C.adjust(src, kind, cur()), d.sel)
            self.full_refresh(override={lid: (out, L.mask)})
        for k, lab, lo, hi, dv in params:
            row = tk.Frame(t, bg=ui.PANEL); row.pack(fill="x", padx=16, pady=3)
            s_ = Slide(row, ui, lab, lo, hi, dv, cmd=lambda v: prev(), width=230, fmt="%.1f" if hi <= 10 else "%d")
            s_.pack(side="left"); vals[k] = s_
        pv = tk.StringVar(value=preset or C.CINEMATIC[0]); mono = tk.BooleanVar(value=True)
        if kind == "cinematic":
            row = tk.Frame(t, bg=ui.PANEL); row.pack(fill="x", padx=16, pady=3)
            cb = ttk.Combobox(row, textvariable=pv, values=C.CINEMATIC, state="readonly", width=22, font=(ui.FONT, 9)); cb.pack(side="left")
            cb.bind("<<ComboboxSelected>>", prev)
        if kind == "noise":
            tk.Checkbutton(t, text="Monochromatic", variable=mono, command=prev, bg=ui.PANEL, fg=ui.TXT, selectcolor=ui.CARD2,
                           activebackground=ui.PANEL, font=(ui.FONT, 9), highlightthickness=0).pack(anchor="w", padx=16)
        if d.sel is not None:
            tk.Label(t, text="Applies inside the selection only.", bg=ui.PANEL, fg=ui.DIM, font=(ui.FONT, 8)).pack(anchor="w", padx=16)
        def ok():
            p = cur(); t.destroy()
            self.adjust_now(kind, p, label=title)
        def cancel():
            t.destroy(); self.full_refresh()
        self._dlg_buttons(t, ok, cancel); prev()

    # ================================================================== files
    OPEN_TYPES = [("Images and EX ART projects", "*.png *.jpg *.jpeg *.webp *.bmp *.gif *.exart"), ("EX ART project", "*.exart"),
                  ("All files", "*.*")]

    def _maybe_save(self):
        if not self.doc.dirty or self.hist.pos == 0: return True
        r = messagebox.askyesnocancel(self.ui.APP, "Save your art first?", parent=self)
        if r is None: return False
        if r: return self.save_project()
        return True

    def open_file(self, path=None, as_layer=False):
        if path is None:
            if as_layer:
                paths = filedialog.askopenfilenames(parent=self.app, title="Add images (each on its own layer)",
                                                    initialdir=pictures_dir(self.ui), filetypes=[("Images", "*.png *.jpg *.jpeg *.webp *.bmp *.gif"), ("All files", "*.*")])
                if paths: self.drop_files(list(paths))
                return
            path = filedialog.askopenfilename(parent=self.app, title="Open image", initialdir=pictures_dir(self.ui), filetypes=self.OPEN_TYPES)
        if not path: return
        try:
            if path.lower().endswith(".exart"):
                if not self._maybe_save(): return
                self.cancel_modes(); self.doc = C.load_exart(path); self.hist = C.History(self.doc); self.doc.dirty = False
                self._doc_changed(); self._status("Opened " + os.path.basename(path)); return
            px = C.open_image(path)
        except Exception as e:
            messagebox.showerror(self.ui.APP, "Couldn't open that file:\n%s" % e, parent=self); return
        name = os.path.splitext(os.path.basename(path))[0]
        if as_layer:
            self.place_pixels(px, name); return
        if not self._maybe_save(): return
        self.cancel_modes()
        d = C.Doc(px.shape[1], px.shape[0]); d.layers = [C.Layer(name, px)]
        self.doc = d; self.hist = C.History(d); d.dirty = False; self._doc_changed()
        self._status("Opened %s (%d \u00d7 %d)" % (os.path.basename(path), d.w, d.h))

    def save_project(self, as_new=False):
        p = self.doc.path
        if as_new or not p:
            p = filedialog.asksaveasfilename(parent=self.app, title="Save EX ART project", initialdir=pictures_dir(self.ui),
                                             initialfile="EX ART %s.exart" % time.strftime("%Y-%m-%d %H%M"), defaultextension=".exart",
                                             filetypes=[("EX ART project", "*.exart")])
            if not p: return False
        try:
            self.cancel_modes(); C.save_exart(self.doc, p)
        except Exception as e:
            messagebox.showerror(self.ui.APP, "Couldn't save:\n%s" % e, parent=self); return False
        self._status("Saved %s (%d layers)" % (p, len(self.doc.layers))); return True

    def export_as(self, fmt="PNG", transparent=False):
        ext = ".jpg" if fmt == "JPG" else ".png"
        p = filedialog.asksaveasfilename(parent=self.app, title="Export " + ("transparent PNG" if transparent else fmt),
                                         initialdir=pictures_dir(self.ui), initialfile="EX ART %s%s" % (time.strftime("%Y-%m-%d %H%M%S"), ext),
                                         defaultextension=ext, filetypes=[(fmt, "*" + ext)])
        if p: self._export(p, fmt, transparent)

    def _export(self, p, fmt, transparent):
        try:
            self.cancel_modes(); C.export(self.doc, p, fmt, transparent)
        except Exception as e:
            messagebox.showerror(self.ui.APP, "Couldn't export:\n%s" % e, parent=self); return None
        self._status("Exported " + p); return p

    def quick_export(self):
        """one click: transparent-aware PNG into Pictures (Downloads if there is no Pictures folder)"""
        transparent = bool((C.flatten_rgba(self.doc)[..., 3] < 255).any())
        p = os.path.join(pictures_dir(self.ui), "EX ART %s.png" % time.strftime("%Y-%m-%d %H%M%S"))
        if self._export(p, "PNG", transparent):
            self._status("Saved to " + p + ("  (transparent PNG)" if transparent else ""))

    def _flat_tmp(self):
        p = os.path.join(self.tmp, "EX ART %s.png" % time.strftime("%H%M%S")); C.export(self.doc, p, "PNG", False); return p

    def send_board(self):
        self.cancel_modes(); p = self._flat_tmp(); self.app.show_tab("home"); self.app.load_image(p)
        self.app.set_status("Your EX ART was sent to the REX BOARD.")

    def send_studio(self):
        self.cancel_modes(); p = self._flat_tmp(); self.app.show_tab("studio"); s = self.app.studio
        try:
            s.src_rgb = self.ui.open_image_rgb(p); s.char_name = "EX ART"; s.auto_detect()
        except Exception as e:
            messagebox.showerror(self.ui.APP, "Couldn't send to EX STUDIO:\n%s" % e, parent=self)

    # ================================================================== menus
    def _mi(self, m, label, fn, acc=None, enabled=True):
        m.add_command(label=label, command=fn, accelerator=acc or "", state="normal" if enabled else "disabled")

    def _m_file(self, m):
        self._mi(m, "New canvas\u2026", self.new_dialog, "Ctrl+N"); self._mi(m, "Open\u2026", self.open_file, "Ctrl+O")
        self._mi(m, "Add image / screenshot file\u2026", lambda: self.open_file(as_layer=True), "Ctrl+Shift+O")
        self._mi(m, "Paste image from clipboard", self.paste, "Ctrl+V"); self._mi(m, "Take screenshot", self.take_screenshot, "Ctrl+Shift+P")
        m.add_separator()
        self._mi(m, "Save project (.exart)", self.save_project, "Ctrl+S"); self._mi(m, "Save project as\u2026", lambda: self.save_project(True), "Ctrl+Shift+S")
        m.add_separator()
        self._mi(m, "Export PNG\u2026", lambda: self.export_as("PNG"), "Ctrl+Alt+S"); self._mi(m, "Export JPG\u2026", lambda: self.export_as("JPG"))
        self._mi(m, "Export transparent PNG\u2026", lambda: self.export_as("PNG", True)); self._mi(m, "Quick export to Pictures", self.quick_export)
        m.add_separator()
        self._mi(m, "Send to REX BOARD", self.send_board); self._mi(m, "Send to EX STUDIO character", self.send_studio)

    def _m_edit(self, m):
        self._mi(m, "Undo", self.undo, "Ctrl+Z", self.hist.can_undo()); self._mi(m, "Redo", self.redo, "Ctrl+Y", self.hist.can_redo())
        m.add_separator()
        self._mi(m, "Cut", lambda: self.copy(cut=True), "Ctrl+X"); self._mi(m, "Copy", self.copy, "Ctrl+C"); self._mi(m, "Paste", self.paste, "Ctrl+V")
        self._mi(m, "Clear", self.clear_sel_pixels, "Delete"); m.add_separator()
        self._mi(m, "Fill with colour", self.fill_sel, "Alt+Backspace"); self._mi(m, "Fill with 2nd colour", lambda: self.fill_sel(True), "Ctrl+Backspace")
        m.add_separator(); self._mi(m, "Free transform", self.tf_start, "Ctrl+T")
        self._mi(m, "Flip layer horizontal", lambda: self.flip_layer(True)); self._mi(m, "Flip layer vertical", lambda: self.flip_layer(False))

    def _m_image(self, m):
        self._mi(m, "Rotate canvas 90\u00b0 clockwise", lambda: self.rotate_canvas(-1)); self._mi(m, "Rotate canvas 90\u00b0 anticlockwise", lambda: self.rotate_canvas(1))
        self._mi(m, "Rotate canvas 180\u00b0", lambda: self.rotate_canvas(2)); m.add_separator()
        self._mi(m, "Flip canvas horizontal", lambda: self.flip_canvas(True)); self._mi(m, "Flip canvas vertical", lambda: self.flip_canvas(False))
        m.add_separator(); self._mi(m, "Crop to selection", self.crop_to_sel); self._mi(m, "Image size\u2026", self.canvas_size_dialog)
        m.add_separator(); self._mi(m, "Zoom to fit", self.fit, "Ctrl+0"); self._mi(m, "Actual pixels (100%)", lambda: self.set_zoom(1.0), "Ctrl+1")

    def _m_layer(self, m):
        self._mi(m, "New layer", self.add_layer, "Ctrl+Shift+N"); self._mi(m, "Duplicate layer", self.dup_layer, "Ctrl+J")
        self._mi(m, "Delete layer", self.del_layer); self._mi(m, "Rename layer\u2026", self.rename_layer, "F2"); m.add_separator()
        self._mi(m, "Add / remove layer mask", self.toggle_mask); m.add_separator()
        self._mi(m, "Merge down", self.merge_down, "Ctrl+E"); self._mi(m, "Merge visible", self.merge_visible)
        self._mi(m, "Flatten image", self.flatten, "Ctrl+Shift+E"); m.add_separator()
        self._mi(m, "Move layer up", lambda: self.move_layer(1), "Ctrl+]"); self._mi(m, "Move layer down", lambda: self.move_layer(-1), "Ctrl+[")

    def _m_select(self, m):
        self._mi(m, "Select all", self.select_all, "Ctrl+A"); self._mi(m, "Deselect", self.deselect, "Ctrl+D")
        self._mi(m, "Invert selection", self.invert_sel, "Ctrl+Shift+I"); self._mi(m, "Select layer pixels", self.select_layer)

    def _m_adjust(self, m):
        self._mi(m, "Brightness / Contrast\u2026", lambda: self.adjust_dialog("brightness"))
        self._mi(m, "Hue / Saturation\u2026", lambda: self.adjust_dialog("huesat"), "Ctrl+U")
        self._mi(m, "Levels\u2026", lambda: self.adjust_dialog("levels"), "Ctrl+L"); m.add_separator()
        self._mi(m, "Invert", lambda: self.adjust_now("invert", label="Invert"), "Ctrl+I")
        self._mi(m, "Grayscale", lambda: self.adjust_now("grayscale", label="Grayscale"), "Ctrl+Shift+U")

    def _m_filter(self, m):
        self._mi(m, "Gaussian blur\u2026", lambda: self.adjust_dialog("blur")); self._mi(m, "Sharpen\u2026", lambda: self.adjust_dialog("sharpen"))
        self._mi(m, "Add noise\u2026", lambda: self.adjust_dialog("noise")); m.add_separator()
        sub = tk.Menu(m, tearoff=0, bg=self.ui.CARD2, fg=self.ui.TXT, activebackground=self.ui.RED, activeforeground="white", font=(self.ui.FONT, 9))
        for n in C.CINEMATIC: sub.add_command(label=n, command=lambda n=n: self.adjust_dialog("cinematic", n))
        m.add_cascade(label="Cinematic looks", menu=sub)

    def rename_layer(self):
        L = self.doc.layer
        nm = simpledialog.askstring("Rename layer", "Layer name:", initialvalue=L.name, parent=self)
        if nm and nm.strip(): L.name = nm.strip()[:60]; self.commit("Rename layer")

    def move_layer(self, dirn):
        d = self.doc; i = d.active; j = i + dirn
        if 0 <= j < len(d.layers):
            d.layers[i], d.layers[j] = d.layers[j], d.layers[i]; d.active = j; self.commit("Reorder layers", full=True)

    # ================================================================== keyboard
    def key(self, e):
        """called by the main window for every key while EX ART is the open tab"""
        w = self.focus_get()
        if isinstance(w, (tk.Entry, tk.Text, tk.Spinbox, ttk.Combobox, ttk.Entry)): return None
        try:
            if w is not None and w.winfo_toplevel() is not self.app: return None
        except Exception:
            pass
        sh, ctrl, alt = self._mods(e); ks = e.keysym; k = ks.lower() if len(ks) == 1 else ks
        if ctrl:
            act = {("z", False): self.undo, ("z", True): self.redo, ("y", False): self.redo, ("s", False): self.save_project,
                   ("s", True): lambda: self.save_project(True), ("n", False): self.new_dialog, ("n", True): self.add_layer,
                   ("o", False): self.open_file, ("o", True): lambda: self.open_file(as_layer=True), ("p", True): self.take_screenshot, ("j", False): self.dup_layer,
                   ("e", False): self.merge_down, ("e", True): self.flatten, ("t", False): self.tf_start, ("a", False): self.select_all,
                   ("d", False): self.deselect, ("i", True): self.invert_sel, ("i", False): lambda: self.adjust_now("invert", label="Invert"),
                   ("u", False): lambda: self.adjust_dialog("huesat"), ("u", True): lambda: self.adjust_now("grayscale", label="Grayscale"),
                   ("l", False): lambda: self.adjust_dialog("levels"), ("c", False): self.copy, ("x", False): lambda: self.copy(True),
                   ("v", False): self.paste, ("0", False): self.fit, ("1", False): lambda: self.set_zoom(1.0),
                   ("plus", False): lambda: self.set_zoom(self.zoom * 1.25), ("equal", False): lambda: self.set_zoom(self.zoom * 1.25),
                   ("minus", False): lambda: self.set_zoom(self.zoom / 1.25), ("bracketright", False): lambda: self.move_layer(1),
                   ("bracketleft", False): lambda: self.move_layer(-1), ("BackSpace", False): lambda: self.fill_sel(True)}
            if alt and k == "s": self.export_as("PNG"); return "break"
            f = act.get((k, sh)) or (act.get((k, False)) if k in ("plus", "equal", "minus", "0", "1") else None)
            if f: f(); return "break"
            return None
        if k == "space":
            if not self.space:
                self.space = True; self.cv.configure(cursor="fleur")
            return "break"
        if k in ("Return", "KP_Enter"):
            if self.tf: self.tf_apply()
            elif self.tool == "crop": self.crop_apply()
            return "break"
        if k == "Escape":
            if self.tf: self.tf_cancel()
            elif self.crop_rect: self.crop_cancel()
            else: self.deselect()
            return "break"
        if k in ("Delete", "BackSpace"):
            if alt and k == "BackSpace": self.fill_sel()
            else: self.clear_sel_pixels()
            return "break"
        if k == "F2": self.rename_layer(); return "break"
        if k == "bracketleft" or k == "bracketright":
            sk = self._sz_key(); v = self.opt[sk]; step = max(1, v * 0.12)
            self.opt[sk] = max(1, min(500, v + (step if k == "bracketright" else -step)))
            if getattr(self, "size_s", None) is not None and self.size_s.winfo_exists(): self.size_s.set(self.opt[sk])
            return "break"
        if k == "x": self.swap_colors(); return "break"
        if k == "d": self.reset_colors(); return "break"
        if k in ("Left", "Right", "Up", "Down") and self.tool == "move":
            n = 10 if sh else 1; dx, dy = {"Left": (-n, 0), "Right": (n, 0), "Up": (0, -n), "Down": (0, n)}[k]
            self.tf_start(move=True)
            if self.tf: self.tf["tx"] += dx; self.tf["ty"] += dy; self._tf_update(); self.tf_apply(label="Nudge")
            return "break"
        if len(ks) == 1:
            kk = ks.upper() if (sh and ks.lower() in ("g", "r")) else ks.lower()
            if kk in KEYMAP and any(t[0] == KEYMAP[kk] for t in self.TOOL_LIST): self.set_tool(KEYMAP[kk]); return "break"
        if ks.isdigit() and self.tool in ("brush", "eraser", "pencil", "clone"):
            v = int(ks) or 10; self.opt["opacity"] = v / 10.0; self._opts_refresh(); return "break"
        return None

    def key_up(self, e):
        if e.keysym == "space" and self.space:
            self.space = False; self.set_tool(self.tool)

    # ================================================================== tab life-cycle
    def leave(self):
        self.cancel_modes(); self._tip(None, None)

    def escape(self):
        if self.tf: self.tf_cancel()
        else: self.deselect()

    def close(self):
        """False = the user cancelled closing the app"""
        try:
            if self.doc.dirty and self.hist.pos > 0:
                r = messagebox.askyesnocancel(self.ui.APP, "Save your EX ART before closing?", parent=self)
                if r is None: return False
                if r and not self.save_project(): return False
        except Exception:
            pass
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)
        return True
