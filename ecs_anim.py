"""REX ANIMATE - frame-by-frame 2D animation tab of EX CREATOR STUDIO.

Built on the EX ART engine and panel: every frame is a small layered document (drawn with the same brushes, selections,
shapes, text and colour picker), plus a shared background, a frame strip with holds, playback, an audio track with a
waveform, and video-ready export (MP4 H.264 + AAC, GIF, PNG sequence).  Project format: .rexanim (zip).
(c)2026 EX Inc./HYPERION DIGITAL (c)2026"""
import io, os, sys, json, math, time, wave, zipfile, shutil, tempfile, threading, subprocess
import numpy as np
import cv2
import tkinter as tk
from tkinter import ttk, filedialog, messagebox, colorchooser
from PIL import Image, ImageTk, ImageDraw, ImageFont, GifImagePlugin, PngImagePlugin  # noqa: F401 (plugins loaded on the UI thread)
import ecs_art_core as C
import ecs_art as A

NAME = "REX ANIMATE"
FPS_CHOICES = (6, 8, 12, 24, 30)
SIZES = [("1920 \u00d7 1080 (video, landscape)", 1920, 1080), ("1080 \u00d7 1920 (vertical)", 1080, 1920), ("1080 \u00d7 1080 (square)", 1080, 1080)]
NO_WINDOW = 0x08000000 if os.name == "nt" else 0
SR = 48000
_FONTS = {}


def fonts():
    if not _FONTS:
        try: _FONTS.update(C.system_fonts())
        except Exception: pass
    return _FONTS


def ffmpeg_exe():
    try:
        import imageio_ffmpeg; return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return shutil.which("ffmpeg") or "ffmpeg"


def res_path(*p):
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, *p)


# ============================================================================ packed layers (frames that are not open)
def pack_layer(l):
    a = l.px[..., 3]
    ys = np.flatnonzero(a.any(1))
    if len(ys) == 0:
        bb, crop = None, None
    else:
        xs = np.flatnonzero(a.any(0)); bb = (int(xs[0]), int(ys[0]), int(xs[-1]) + 1, int(ys[-1]) + 1)
        crop = l.px[bb[1]:bb[3], bb[0]:bb[2]].copy()
    return {"id": l.id, "name": l.name, "visible": l.visible, "locked": l.locked, "opacity": l.opacity, "blend": l.blend,
            "text": dict(l.text) if l.text else None, "bb": bb, "crop": crop}


def unpack_layer(p, w, h):
    px = C.blank(w, h)
    if p["bb"] is not None:
        x0, y0, x1, y1 = p["bb"]; px[y0:y1, x0:x1] = p["crop"]
    return C.Layer(p["name"], px, visible=p["visible"], locked=p["locked"], opacity=p["opacity"], blend=p["blend"],
                   text=dict(p["text"]) if p["text"] else None)


# ============================================================================ text (editable, styled)
def font_file(family, bold):
    f = fonts()
    if bold:
        for k in (family + " Bold", family.replace(" Regular", "") + " Bold"):
            if k in f: return f[k], False
    if family in f: return f[family], bold
    for k in ("Segoe UI Bold" if bold else "Segoe UI", "Arial Bold" if bold else "Arial", "DejaVu Sans Bold" if bold else "DejaVu Sans"):
        if k in f: return f[k], False
    return (next(iter(f.values())) if f else None), bold


def render_text(w, h, t):
    """t: text, font, size, color, bold, stroke, stroke_color, shadow, x, y (centre), angle (deg, anticlockwise)"""
    path, fake_bold = font_file(t.get("font", "Segoe UI"), t.get("bold", False))
    size = max(4, int(round(t.get("size", 96))))
    fnt = None
    for cand in (path, "segoeuib.ttf" if t.get("bold") else "segoeui.ttf", "arialbd.ttf" if t.get("bold") else "arial.ttf", "DejaVuSans-Bold.ttf"):
        if not cand: continue
        try: fnt = ImageFont.truetype(cand, size); break
        except Exception: pass
    if fnt is None:
        try: fnt = ImageFont.load_default(size=size)
        except Exception: fnt = ImageFont.load_default()
    col = C.hex_to_rgb(t.get("color", "#000000")); sw = int(t.get("stroke", 0)); sc = C.hex_to_rgb(t.get("stroke_color", "#ffffff"))
    fb = max(1, size // 28) if fake_bold else 0
    txt = t.get("text", "") or " "; sp = size // 5
    d0 = ImageDraw.Draw(Image.new("L", (4, 4)))
    bx = d0.multiline_textbbox((0, 0), txt, font=fnt, stroke_width=sw + fb, align="center", spacing=sp)
    pad = sw + fb + size // 6 + 4; sh = max(2, size // 14) if t.get("shadow") else 0
    W, H = bx[2] - bx[0] + 2 * pad + sh, bx[3] - bx[1] + 2 * pad + sh
    im = Image.new("RGBA", (W, H), col + (0,))
    o = (pad - bx[0], pad - bx[1])
    if sh:
        shadow = Image.new("RGBA", (W, H), (0, 0, 0, 0)); ds = ImageDraw.Draw(shadow)
        ds.multiline_text((o[0] + sh, o[1] + sh), txt, font=fnt, fill=(0, 0, 0, 150), stroke_width=sw + fb, stroke_fill=(0, 0, 0, 150),
                          align="center", spacing=sp)
        im = Image.alpha_composite(im, shadow)
    d = ImageDraw.Draw(im)
    if sw: d.multiline_text(o, txt, font=fnt, fill=sc + (255,), stroke_width=sw + fb, stroke_fill=sc + (255,), align="center", spacing=sp)
    d.multiline_text(o, txt, font=fnt, fill=col + (255,), stroke_width=fb, stroke_fill=col + (255,), align="center", spacing=sp)
    ang = float(t.get("angle", 0.0))
    if abs(ang) > 0.01: im = im.rotate(ang, resample=Image.BICUBIC, expand=True)
    out = Image.new("RGBA", (w, h), col + (0,))
    x0 = int(round(t.get("x", w / 2) - im.width / 2)); y0 = int(round(t.get("y", h / 2) - im.height / 2))
    sx0, sy0 = max(0, -x0), max(0, -y0); dx0, dy0 = max(0, x0), max(0, y0)
    cw, ch = min(im.width - sx0, w - dx0), min(im.height - sy0, h - dy0)
    if cw > 0 and ch > 0: out.alpha_composite(im.crop((sx0, sy0, sx0 + cw, sy0 + ch)), (dx0, dy0))
    return np.asarray(out).copy()


# ============================================================================ sample: the red-haired stickman
_HEAD = {}


def stick_head(scale=0.2):
    if scale not in _HEAD:
        hd = Image.open(res_path("assets", "rex_stickman_head.png")).convert("RGBA")
        _HEAD[scale] = hd.resize((int(hd.width * scale), int(hd.height * scale)), Image.LANCZOS)
    return _HEAD[scale]


def _limb(p, a1, a2, l1, l2):
    k = (p[0] + l1 * math.sin(a1), p[1] + l1 * math.cos(a1))
    return k, (k[0] + l2 * math.sin(a1 + a2), k[1] + l2 * math.cos(a1 + a2))


def draw_stickman(w, h, hip, pose, s=1.0, lw=11):
    """pose angles in radians from straight down: lt/lk rt/rk legs (thigh, knee), la/le ra/re arms, tilt = head tilt (deg)"""
    body, th, sh_, ua, fa = 175 * s, 92 * s, 92 * s, 92 * s, 88 * s
    neck = (hip[0], hip[1] - body); shoulder = (neck[0], neck[1] + 22 * s)
    segs = [(hip, neck)]
    for side in ("l", "r"):
        k, f = _limb(hip, pose[side + "t"], pose[side + "k"], th, sh_); segs += [(hip, k), (k, f)]
        e, hnd = _limb(shoulder, pose[side + "a"], pose[side + "e"], ua, fa); segs += [(shoulder, e), (e, hnd)]
    big = Image.new("L", (w * 2, h * 2), 0); d = ImageDraw.Draw(big); r = lw * s
    for a, b in segs:
        d.line([(a[0] * 2, a[1] * 2), (b[0] * 2, b[1] * 2)], fill=255, width=max(2, int(lw * 2 * s)))
        for q in (a, b): d.ellipse([(q[0] - r / 2) * 2, (q[1] - r / 2) * 2, (q[0] + r / 2) * 2, (q[1] + r / 2) * 2], fill=255)
    al = np.asarray(big.resize((w, h), Image.LANCZOS))
    out = np.zeros((h, w, 4), np.uint8); out[..., 3] = al; im = Image.fromarray(out)
    hd = stick_head(round(0.2 * s, 3)); tilt = pose.get("tilt", 0.0)
    hw, hh = hd.size
    if tilt: hd = hd.rotate(tilt, resample=Image.BICUBIC, expand=True)
    nx = int(neck[0] - hd.width * 0.518); ny = int(neck[1] - hh * 0.985 - (hd.height - hh) / 2)
    if nx >= 0 and ny >= 0 and nx + hd.width <= w and ny + hd.height <= h: im.alpha_composite(hd, (nx, ny))
    return np.asarray(im).copy()


def _leg(s): return 182 * s


def sample_frames(w, h):
    """28 frames at 12 fps: walk in, wave hello, jump (with a linked title on frames 13-28)."""
    ground = h * 0.84; s = h / 1080.0; frames = []; link = C.new_id(); x_end = w * 0.18 + 11 * w * 0.028
    for i in range(28):
        if i < 12:
            ph = i / 6.0 * math.pi; x = w * 0.18 + i * w * 0.028; sw = 0.42 * math.sin(ph)
            pose = {"lt": sw, "lk": -0.45 * max(0, -math.sin(ph)), "rt": -sw, "rk": -0.45 * max(0, math.sin(ph)),
                    "la": -0.7 * math.sin(ph) - 0.12, "le": 0.4, "ra": 0.7 * math.sin(ph) + 0.12, "re": 0.4}
            hip = (x, ground - _leg(s) + 8 * s * abs(math.sin(ph)))
        elif i < 20:
            k = (i - 12) / 8.0; wv = math.sin(k * 4 * math.pi)
            pose = {"lt": 0.14, "lk": 0, "rt": -0.14, "rk": 0, "la": -0.22, "le": -0.1, "ra": 1.75, "re": 0.95 + 0.4 * wv, "tilt": -5 * wv}
            hip = (x_end, ground - _leg(s) * 0.99)
        else:
            k = (i - 20) / 7.0; up = math.sin(min(1.0, k) * math.pi); crouch = (1 - up) * (1.0 if i in (20, 27) else 0.4)
            pose = {"lt": 0.3 + 0.5 * crouch, "lk": -1.0 * crouch, "rt": -0.3 - 0.5 * crouch, "rk": 1.0 * crouch,
                    "la": -(0.5 + 1.7 * up), "le": 0.0, "ra": 0.5 + 1.7 * up, "re": 0.0}
            leg = _leg(s) * (math.cos(0.3 + 0.5 * crouch) * 0.5 + 0.5 * math.cos(0.3 + 0.5 * crouch - 1.0 * crouch))
            hip = (x_end, ground - leg - up * 240 * s)
        layers = [C.Layer("Stickman", draw_stickman(w, h, hip, pose, s))]
        if i >= 12:
            t = {"kind": "rextext", "text": "HI, I'M REX!", "font": "Segoe UI", "size": round(118 * s), "color": "#111111", "bold": True,
                 "stroke": int(round(7 * s)), "stroke_color": "#ffffff", "shadow": True, "x": w * 0.765, "y": h * 0.2, "angle": 4.0, "link": link}
            layers.append(C.Layer("T  HI, I'M REX!", render_text(w, h, t), text=t))
        frames.append({"layers": [pack_layer(l) for l in layers], "hold": 1, "active": 0})
    return frames


def sample_background(w, h):
    bg = np.full((h, w, 3), 255, np.uint8); g = int(h * 0.84)
    bg[g:g + max(2, h // 270)] = (40, 40, 46)
    return bg


def _cover(px, W, H, col):
    """fit an image to cover the canvas (centre crop); transparent parts sit on the background colour"""
    h, w = px.shape[:2]; s = max(W / w, H / h)
    im = cv2.resize(px, (max(W, int(math.ceil(w * s))), max(H, int(math.ceil(h * s)))), interpolation=cv2.INTER_AREA if s < 1 else cv2.INTER_CUBIC)
    y0, x0 = (im.shape[0] - H) // 2, (im.shape[1] - W) // 2; im = im[y0:y0 + H, x0:x0 + W]
    a = im[..., 3:4].astype(np.float32) / 255.0
    return (im[..., :3] * a + np.array(col, np.float32) * (1 - a)).astype(np.uint8)


def _aicon(name, size=14, color=(242, 242, 244)):
    S = size * 4; im = Image.new("RGBA", (S, S), (0, 0, 0, 0)); d = ImageDraw.Draw(im); c = tuple(color) + (255,); lw = max(2, S // 10)
    R = lambda *v: [x * S for x in v]
    if name == "play": d.polygon([(S * .26, S * .14), (S * .86, S * .5), (S * .26, S * .86)], fill=c)
    elif name == "pause": d.rectangle(R(.22, .16, .42, .84), fill=c); d.rectangle(R(.58, .16, .78, .84), fill=c)
    elif name in ("prev", "next"):
        d.polygon([(S * .2, S * .5), (S * .62, S * .18), (S * .62, S * .82)], fill=c); d.rectangle(R(.68, .18, .8, .82), fill=c)
        if name == "next": im = im.transpose(Image.FLIP_LEFT_RIGHT)
    elif name in ("first", "last"):
        d.rectangle(R(.12, .18, .24, .82), fill=c); d.polygon([(S * .28, S * .5), (S * .6, S * .2), (S * .6, S * .8)], fill=c)
        d.polygon([(S * .58, S * .5), (S * .9, S * .2), (S * .9, S * .8)], fill=c)
        if name == "last": im = im.transpose(Image.FLIP_LEFT_RIGHT)
    elif name == "add":
        d.rounded_rectangle(R(.1, .22, .68, .9), radius=S * .06, outline=c, width=lw)
        d.line(R(.8, .04, .8, .4), fill=c, width=lw); d.line(R(.62, .22, .98, .22), fill=c, width=lw)
    elif name == "dup":
        d.rounded_rectangle(R(.3, .1, .9, .7), radius=S * .06, outline=c, width=lw); d.rounded_rectangle(R(.1, .3, .7, .9), radius=S * .06, outline=c, width=lw)
    elif name == "ins":
        d.rounded_rectangle(R(.36, .18, .9, .86), radius=S * .06, outline=c, width=lw); d.polygon([(S * .04, S * .32), (S * .28, S * .52), (S * .04, S * .72)], fill=c)
    elif name == "copy":
        d.rounded_rectangle(R(.32, .1, .88, .66), radius=S * .06, outline=c, width=lw); d.rounded_rectangle(R(.12, .34, .68, .9), radius=S * .06, outline=c, width=lw)
    elif name == "paste":
        d.rounded_rectangle(R(.16, .16, .84, .92), radius=S * .06, outline=c, width=lw); d.rectangle(R(.34, .06, .66, .24), fill=c)
    elif name == "del":
        d.line(R(.2, .2, .8, .8), fill=c, width=lw + 1); d.line(R(.8, .2, .2, .8), fill=c, width=lw + 1)
    elif name == "minus": d.line(R(.2, .5, .8, .5), fill=c, width=lw + 1)
    elif name == "plus": d.line(R(.2, .5, .8, .5), fill=c, width=lw + 1); d.line(R(.5, .2, .5, .8), fill=c, width=lw + 1)
    elif name == "audio":
        for i, hh in enumerate((.3, .6, .9, .5, .7, .35)):
            x = S * (.12 + i * .15); d.line((x, S * (.5 - hh / 2), x, S * (.5 + hh / 2)), fill=c, width=lw)
    return im.resize((size, size), Image.LANCZOS)


ANIM_TOOLS = [t for t in A.TOOLS if t[0] in ("move", "marquee", "lasso", "eyedropper", "brush", "pencil", "eraser", "fill", "text", "shape", "hand", "zoom")]
MAX_DRAW_LAYERS = 5
CELL_W, CELL_H, HOLD_W, STRIP_Y, WAVE_H = 104, 58, 26, 20, 28


class FrameHistory(C.History):
    """undo/redo for one frame, with a smaller memory budget (several frames keep their own history)"""
    def push(self, label):
        super().push(label)
        while len(self.items) > 6 and self.mem() > 4.5e8:
            self.items.pop(0); self.pos -= 1


class AnimPanel(A.ArtPanel):
    TOOL_LIST = ANIM_TOOLS
    PICK_ALL = True
    OPEN_TYPES = [("REX ANIMATE project", "*.rexanim"), ("All files", "*.*")]

    def __init__(self, master, app, ui):
        self.W, self.H = 1920, 1080
        self.fps = 12; self.frames = []; self.cur = 0; self._lru = []
        self.bg_color = (255, 255, 255); self.bg_img = None; self.bg_rgb = None; self._bgf = None
        self.audio = None; self.onion = False; self._ghost = None
        self.playing = False; self.loop = True; self.from_here = False
        self.proj_path = None; self.proj_name = "Untitled"; self.dirty = False
        self.fclip = None; self.exporting = False; self._strip_job = None; self._sdrag = None
        self.player = ui.Player()
        super().__init__(master, app, ui)
        self.opt.update({"size": 10, "hardness": 0.95, "stab": 0.55, "pencil_size": 3, "eraser_size": 30, "stroke": 8, "shape_fill": False})
        self.set_tool("brush")
        self.after(1500, self._autosave_tick)

    # ================================================================== layout
    def _build(self):
        super()._build()
        ui = self.ui; B = ui.Btn; F = ui.FONT; P = ui.PANEL
        top = self.undo_b.master
        for w in top.winfo_children():
            t = str(w.cget("text")) if isinstance(w, tk.Label) else ""
            if t.startswith("Image"): w.configure(text="Frame ")
            elif "Export PNG" in t: w.configure(text="  Export video"); w.cmd = self.export_dialog
            elif "REX BOARD" in t: w.pack_forget()
        main = self.cv.master
        tl = tk.Frame(main, bg=P, highlightthickness=1, highlightbackground=ui.BORDER)     # between the toolbar and the right panel
        tl.pack(side="bottom", fill="x", padx=6, pady=(6, 0), before=self.cv)
        row = tk.Frame(tl, bg=P); row.pack(fill="x", padx=6, pady=(5, 2))
        I = lambda n, c=(242, 242, 244): self.icons.setdefault("a" + n + str(c), ImageTk.PhotoImage(_aicon(n, 14, c)))
        def ib(parent, ic, fn, tip, **kw):
            b = B(parent, "", fn, padx=7, pady=4, image=I(ic), **kw); b.pack(side="left", padx=1)
            b.bind("<Enter>", lambda e, w=b: (w.configure(bg=ui.BORDER), self._tip(w, tip)), add="+")
            b.bind("<Leave>", lambda e, w=b: (w.configure(bg=w.fill), self._tip(None, None)), add="+")
            return b
        tk.Label(row, text="FRAMES", bg=P, fg=ui.MUT, font=(F, 8, "bold")).pack(side="left", padx=(4, 6))
        ib(row, "add", self.add_frame, "New blank frame after this one (Alt+N)")
        ib(row, "dup", self.dup_frame, "Duplicate this frame (Alt+D)")
        ib(row, "ins", self.insert_blank, "Insert a blank frame before this one")
        ib(row, "copy", self.copy_frame, "Copy frame"); ib(row, "paste", self.paste_frame, "Paste frame after this one")
        ib(row, "del", self.del_frame, "Delete this frame")
        tk.Frame(row, bg=ui.BORDER, width=1, height=22).pack(side="left", padx=8)
        tk.Label(row, text="Hold", bg=P, fg=ui.MUT, font=(F, 9)).pack(side="left", padx=(0, 3))
        ib(row, "minus", lambda: self.set_hold(self.frames[self.cur]["hold"] - 1), "Show this drawing for fewer frames")
        self.hold_l = tk.Label(row, text="1", bg=P, fg=ui.TXT, font=(F, 9, "bold"), width=3); self.hold_l.pack(side="left")
        ib(row, "plus", lambda: self.set_hold(self.frames[self.cur]["hold"] + 1), "Hold this drawing for more frames")
        tk.Frame(row, bg=ui.BORDER, width=1, height=22).pack(side="left", padx=8)
        ib(row, "first", lambda: self.goto(0), "First frame (Home)"); ib(row, "prev", lambda: self.step(-1), "Previous frame ( , )")
        self.play_b = B(row, "", self.toggle_play, fill=ui.RED, fg="white", hover=ui.RED2, padx=12, pady=4, image=I("play", (255, 255, 255)))
        self.play_b.pack(side="left", padx=3)
        ib(row, "next", lambda: self.step(1), "Next frame ( . )"); ib(row, "last", lambda: self.goto(len(self.frames) - 1), "Last frame (End)")
        def chk(text, var, fn):
            c = tk.Checkbutton(row, text=text, variable=var, command=fn, bg=P, fg=ui.TXT, selectcolor=ui.CARD2, activebackground=P,
                               activeforeground=ui.TXT, font=(F, 9), bd=0, highlightthickness=0); c.pack(side="left", padx=(8, 0)); return c
        self.loop_v = tk.BooleanVar(value=True); self.here_v = tk.BooleanVar(value=False); self.onion_v = tk.BooleanVar(value=False)
        chk("Loop", self.loop_v, lambda: setattr(self, "loop", self.loop_v.get()))
        chk("From here", self.here_v, lambda: setattr(self, "from_here", self.here_v.get()))
        row = tk.Frame(tl, bg=P); row.pack(fill="x", padx=6, pady=(0, 2))
        tk.Label(row, text="FPS", bg=P, fg=ui.MUT, font=(F, 9)).pack(side="left", padx=(4, 3))
        self.fps_v = tk.StringVar(value="12")
        cb = ttk.Combobox(row, textvariable=self.fps_v, values=[str(v) for v in FPS_CHOICES], state="readonly", width=3, font=(F, 9))
        cb.pack(side="left"); cb.bind("<<ComboboxSelected>>", lambda e: self.set_fps(int(self.fps_v.get())))
        self.time_l = tk.Label(row, text="", bg=P, fg=ui.MUT, font=(F, 9)); self.time_l.pack(side="right", padx=6)
        self.aud_x = ib(row, "del", self.remove_audio, "Remove the audio track"); self.aud_x.pack_forget()
        self.aud_b = B(row, "  Audio", self.import_audio, font=(F, 8, "bold"), padx=8, pady=4, image=I("audio"), compound="left")
        self.aud_b.pack(side="right", padx=2)
        c = tk.Checkbutton(row, text="Onion skin", variable=self.onion_v, command=self._onion_toggle, bg=P, fg=ui.DIM, selectcolor=ui.CARD2,
                           activebackground=P, activeforeground=ui.TXT, font=(F, 8), bd=0, highlightthickness=0)
        c.pack(side="right", padx=8)
        self.strip = tk.Canvas(tl, bg=ui.CARD, height=STRIP_Y + CELL_H + WAVE_H + 12, highlightthickness=0, xscrollincrement=1)
        self.strip.pack(fill="x", padx=6, pady=(2, 0))
        self.sbar = ttk.Scrollbar(tl, orient="horizontal", command=self.strip.xview); self.sbar.pack(fill="x", padx=6, pady=(0, 4))
        self.strip.configure(xscrollcommand=self.sbar.set)
        s = self.strip
        s.bind("<ButtonPress-1>", self._s_press); s.bind("<B1-Motion>", self._s_drag); s.bind("<ButtonRelease-1>", self._s_release)
        s.bind("<Double-Button-1>", self._s_double); s.bind("<ButtonPress-3>", self._s_menu)
        s.bind("<MouseWheel>", lambda e: (s.xview_scroll(-60 if e.delta > 0 else 60, "units"), "break")[1])
        s.bind("<Button-4>", lambda e: s.xview_scroll(-60, "units")); s.bind("<Button-5>", lambda e: s.xview_scroll(60, "units"))
        s.bind("<Configure>", lambda e: self.draw_strip())
        self._sthumbs = {}
        self.cv.bind("<Configure>", self._autofit, add="+")

    def _autofit(self, e=None):
        """keep the canvas fitted while the panel is still settling its size (first show / new project)"""
        if time.time() < getattr(self, "_autofit_until", 0) and not self.playing:
            if getattr(self, "_af_job", None): self.after_cancel(self._af_job)
            self._af_job = self.after(60, self.fit)

    def _onion_toggle(self):
        self.onion = bool(self.onion_v.get()); self._make_ghost(); self.full_refresh()

    # ================================================================== project / frames
    def new_doc(self, w, h, bg, push=True):
        """a new animation: one blank frame on a white (or given) background"""
        self.new_project(w, h, [{"layers": [pack_layer(C.Layer("Layer 1", C.blank(w, h)))], "hold": 1, "active": 0}])

    def new_project(self, w, h, frames, bg=None, bg_color=(255, 255, 255), fps=12, audio=None, name="Untitled", path=None):
        self.stop_play(); self.cancel_modes() if hasattr(self, "doc") else None
        self.W, self.H = int(w), int(h); self.frames = frames; self._lru = []; self.cur = 0
        for f in frames: f.setdefault("hold", 1); f.setdefault("active", 0); f["flat"] = None
        self.bg_color = tuple(bg_color); self.bg_img = bg; self.fps = fps; self.audio = audio
        self.proj_name, self.proj_path = name, path; self.dirty = False
        if hasattr(self, "fps_v"): self.fps_v.set(str(fps))
        self._autofit_until = time.time() + 4
        self._rebuild_bg(); self._open(frames[0]); f = frames[0]
        self.doc, self.hist = f["doc"], f["hist"]
        self._doc_changed(); self._make_ghost(); self._audio_ui(); self.draw_strip()

    def _rebuild_bg(self):
        if self.bg_img is not None: self.bg_rgb = _cover(self.bg_img, self.W, self.H, self.bg_color)
        else: self.bg_rgb = np.empty((self.H, self.W, 3), np.uint8); self.bg_rgb[:] = self.bg_color
        self._bgf = None; self._bgver = getattr(self, "_bgver", 0) + 1
        for f in self.frames: f["flat"] = None

    def _open(self, f):
        if "doc" not in f:
            d = C.Doc(self.W, self.H, (0, 0, 0, 0)); d.layers = [unpack_layer(p, self.W, self.H) for p in f["layers"]] or [C.Layer("Layer 1", C.blank(self.W, self.H))]
            d.active = min(f.get("active", 0), len(d.layers) - 1); d.dirty = False
            f["doc"] = d; f["hist"] = FrameHistory(d, limit=40); f["hist"].items[0] = ("Frame opened", d.snapshot())
        if f in self._lru: self._lru.remove(f)
        self._lru.append(f)
        while len(self._lru) > 4:
            old = self._lru.pop(0); self._pack(old)

    def _pack(self, f):
        if "doc" in f:
            f["layers"] = [pack_layer(l) for l in f["doc"].layers]; f["active"] = f["doc"].active
            del f["doc"]; del f["hist"]

    def _layers_of(self, f):
        """(layers, packed?) for any frame"""
        return (f["doc"].layers, False) if "doc" in f else (f["layers"], True)

    def _ticks(self):
        return sum(f["hold"] for f in self.frames)

    def _tick_of(self, i):
        return sum(f["hold"] for f in self.frames[:i])

    def _frame_at_tick(self, t):
        acc = 0
        for i, f in enumerate(self.frames):
            acc += f["hold"]
            if t < acc: return i
        return len(self.frames) - 1

    def goto(self, i, keep_play=False):
        i = max(0, min(len(self.frames) - 1, int(i)))
        if not keep_play: self.stop_play()
        if i == self.cur and getattr(self, "doc", None) is self.frames[i].get("doc"):
            self.full_refresh(); self.draw_strip(); return
        self.cancel_modes(); self._tip(None, None)
        old = self.frames[self.cur] if 0 <= self.cur < len(self.frames) else None
        if old is not None and "doc" in old: old["active"] = old["doc"].active
        self.cur = i; f = self.frames[i]; self._open(f)
        self.doc, self.hist = f["doc"], f["hist"]
        self._make_ghost(); self.full_refresh(); self.draw_layers(); self.draw_history(); self._update_ants(); self._status()
        self.draw_strip(); self._see(i)

    def step(self, d):
        n = len(self.frames)
        self.goto((self.cur + d) % n if self.loop else self.cur + d)

    def _changed(self, i=None, strip=True):
        i = self.cur if i is None else i
        if 0 <= i < len(self.frames): self.frames[i]["flat"] = None; self.frames[i]["ver"] = self.frames[i].get("ver", 0) + 1
        self.dirty = True
        if strip and self._strip_job is None: self._strip_job = self.after(250, self._strip_now)

    def _strip_now(self):
        self._strip_job = None; self.draw_strip()

    def _new_frame(self):
        return {"layers": [pack_layer(C.Layer("Layer 1", C.blank(self.W, self.H)))], "hold": 1, "active": 0, "flat": None}

    def _clone_frame(self, f):
        ls, packed = self._layers_of(f)
        lay = [dict(p, crop=None if p["crop"] is None else p["crop"].copy(), text=dict(p["text"]) if p["text"] else None) for p in ls] if packed \
            else [pack_layer(l) for l in ls]
        return {"layers": lay, "hold": f["hold"], "active": (f["doc"].active if "doc" in f else f["active"]), "flat": None}

    def _insert(self, at, f, label):
        self.frames.insert(at, f); self.dirty = True; self.goto(at); self._status("%s - frame %d of %d" % (label, at + 1, len(self.frames)))

    def add_frame(self): self._insert(self.cur + 1, self._new_frame(), "New frame")

    def insert_blank(self): self._insert(self.cur, self._new_frame(), "Blank frame inserted")

    def dup_frame(self):
        self.cancel_modes(); self._insert(self.cur + 1, self._clone_frame(self.frames[self.cur]), "Frame duplicated")

    def copy_frame(self):
        self.cancel_modes(); self.fclip = self._clone_frame(self.frames[self.cur]); self._status("Frame %d copied" % (self.cur + 1))

    def paste_frame(self):
        if self.fclip is None: self._status("Copy a frame first"); return
        self._insert(self.cur + 1, self._clone_frame(self.fclip), "Frame pasted")

    def del_frame(self):
        if len(self.frames) <= 1:
            self.cancel_modes(); f = self.frames[0]; self._pack(f); self._lru = []
            self.frames[0] = self._new_frame(); self.cur = -1; self.dirty = True; self.goto(0); return
        self.cancel_modes(); f = self.frames.pop(self.cur)
        if f in self._lru: self._lru.remove(f)
        self.dirty = True; i = min(self.cur, len(self.frames) - 1); self.cur = -1; self.goto(i)
        self._status("Frame deleted")

    def set_hold(self, n):
        n = max(1, min(240, int(n))); f = self.frames[self.cur]
        if f["hold"] != n: f["hold"] = n; self.dirty = True; self.draw_strip()

    def move_frame(self, i, j):
        if i == j or not (0 <= i < len(self.frames)): return
        self.cancel_modes(); f = self.frames.pop(i); j = max(0, min(len(self.frames), j)); self.frames.insert(j, f); self.dirty = True
        self.cur = -1; self.goto(j)

    def set_fps(self, v):
        self.fps = int(v); self.dirty = True; self.draw_strip(); self._status("%d frames per second" % self.fps)

    # ================================================================== history plumbing (per frame)
    def commit(self, label, full=False, region=None):
        super().commit(label, full, region); self._changed()

    def after_history(self):
        super().after_history(); self._changed()

    def _doc_changed(self):
        d = self.doc
        self.chk = np.zeros((1, 1), np.uint8)
        self.disp = np.zeros((d.h, d.w, 3), np.uint8)
        self.full_refresh(); self.fit(); self.draw_layers(); self.draw_history(); self._update_ants(); self._status()

    def add_layer(self):
        if sum(1 for l in self.doc.layers if not l.text) >= MAX_DRAW_LAYERS:
            self._status("Up to %d drawing layers per frame (text layers don't count)" % MAX_DRAW_LAYERS); return
        super().add_layer()

    # ================================================================== rendering
    def _bg_f(self):
        if self._bgf is None: self._bgf = self.bg_rgb.astype(np.float32) * (1 / 255.0)
        return self._bgf

    def _make_ghost(self):
        """onion skin (off unless the user ticks it): previous drawing as a faint red ghost"""
        self._ghost = None
        if not self.onion or self.cur <= 0 or not self.frames: return
        prev = self.frames[self.cur - 1]; ls, packed = self._layers_of(prev)
        a = np.zeros((self.H, self.W), np.float32)
        for l in ls:
            if packed:
                if l["bb"] is None or not l["visible"]: continue
                x0, y0, x1, y1 = l["bb"]; a[y0:y1, x0:x1] = np.maximum(a[y0:y1, x0:x1], l["crop"][..., 3] * (l["opacity"] / 255.0))
            elif l.visible: a = np.maximum(a, l.px[..., 3] * (l.opacity / 255.0))
        self._ghost = a * 0.28

    def _region_rgb(self, x0, y0, x1, y1, override=None):
        bg = self._bg_f()[y0:y1, x0:x1]
        if self._ghost is not None:
            g = self._ghost[y0:y1, x0:x1, None]; bg = bg * (1 - g) + np.array([0.9, 0.2, 0.2], np.float32) * g
        rgb, _ = C.composite(self.doc.layers, x0, y0, x1, y1, bg=bg, override=override)
        return C.to_u8(rgb)

    def _tf_bg(self, small):
        return small(self.bg_rgb).astype(np.float32) * (1 / 255.0)

    def _small_layers(self, ls, packed, s, w, h):
        out = []
        for l in ls:
            if packed:
                if l["bb"] is None or not l["visible"]: continue
                x0, y0, x1, y1 = l["bb"]; X0, Y0 = int(x0 * s), int(y0 * s)
                X1, Y1 = max(X0 + 1, min(w, int(math.ceil(x1 * s)))), max(Y0 + 1, min(h, int(math.ceil(y1 * s))))
                if X0 >= w or Y0 >= h: continue
                sm = cv2.resize(l["crop"], (X1 - X0, Y1 - Y0), interpolation=cv2.INTER_AREA)
                px = np.zeros((h, w, 4), np.uint8); px[Y0:Y1, X0:X1] = sm[:Y1 - Y0, :X1 - X0]
                out.append(C.Layer("s", px, opacity=l["opacity"], blend=l["blend"]))
            elif l.visible:
                px = cv2.resize(l.px, (w, h), interpolation=cv2.INTER_AREA)
                mk = None if l.mask is None else cv2.resize(l.mask, (w, h), interpolation=cv2.INTER_AREA)
                out.append(C.Layer("s", px, opacity=l.opacity, blend=l.blend, mask=mk))
        return out

    def flat(self, i, s=None):
        """small RGB preview of frame i (cached) - playback, thumbnails"""
        f = self.frames[i]
        s = s or min(1.0, 960.0 / max(self.W, self.H))
        if f.get("flat") is not None and f["flat"][0] == s: return f["flat"][1]
        w, h = max(1, int(round(self.W * s))), max(1, int(round(self.H * s)))
        bg = cv2.resize(self.bg_rgb, (w, h), interpolation=cv2.INTER_AREA).astype(np.float32) * (1 / 255.0)
        ls, packed = self._layers_of(f)
        rgb, _ = C.composite(self._small_layers(ls, packed, s, w, h), 0, 0, w, h, bg=bg)
        im = C.to_u8(rgb); f["flat"] = (s, im); return im

    def render_frame(self, layers, packed):
        bg = self._bg_f()
        ls = [unpack_layer(p, self.W, self.H) for p in layers] if packed else layers
        rgb, _ = C.composite(ls, 0, 0, self.W, self.H, bg=bg)
        return C.to_u8(rgb)

    def _show_flat(self, i):
        """draw frame i from its cached preview (fast enough for 30 fps playback)"""
        im = self.flat(i); z = self.zoom
        vx0, vy0, vx1, vy1, tw, th = self._view_rect()
        if vx1 <= vx0 or vy1 <= vy0: return
        s = im.shape[1] / float(self.W)
        crop = im[int(vy0 * s):max(int(vy0 * s) + 1, int(math.ceil(vy1 * s))), int(vx0 * s):max(int(vx0 * s) + 1, int(math.ceil(vx1 * s)))]
        self.render_view(preview=cv2.resize(crop, (tw, th), interpolation=cv2.INTER_LINEAR if z * s < 1.5 else cv2.INTER_NEAREST))

    # ================================================================== frame strip
    def _cell_x(self, i):
        x = 8
        for f in self.frames[:i]: x += CELL_W + (f["hold"] - 1) * HOLD_W + 6
        return x

    def _cell_w(self, i):
        return CELL_W + (self.frames[i]["hold"] - 1) * HOLD_W

    def _thumb_img(self, i):
        f = self.frames[i]; key = (id(f), f.get("ver", 0), self._bgver)
        tk_im = self._sthumbs.get(i)
        if tk_im is not None and tk_im[0] == key: return tk_im[1]
        im = self.flat(i); h, w = im.shape[:2]; s = min((CELL_W - 4) / w, (CELL_H - 4) / h)
        sm = cv2.resize(im, (max(1, int(w * s)), max(1, int(h * s))), interpolation=cv2.INTER_AREA)
        ph = ImageTk.PhotoImage(Image.fromarray(sm)); self._sthumbs[i] = (key, ph); return ph

    def draw_strip(self):
        if not hasattr(self, "strip") or not self.frames: return
        s = self.strip; s.delete("all"); ui = self.ui; y0 = STRIP_Y; F = ui.FONT
        for i, f in enumerate(self.frames):
            x = self._cell_x(i); w = self._cell_w(i); act = i == self.cur
            s.create_text(x + 2, 9, text=str(i + 1), anchor="w", fill=ui.TXT if act else ui.DIM, font=(F, 8, "bold"))
            if f["hold"] > 1: s.create_text(x + w - 2, 9, text="\u00d7%d" % f["hold"], anchor="e", fill=ui.AMBER, font=(F, 8, "bold"))
            s.create_rectangle(x, y0, x + w, y0 + CELL_H, fill="#101014", outline=ui.RED if act else ui.BORDER, width=2 if act else 1)
            s.create_image(x + CELL_W / 2, y0 + CELL_H / 2, image=self._thumb_img(i))
            for k in range(1, f["hold"]):
                hx = x + CELL_W + (k - 1) * HOLD_W
                s.create_rectangle(hx + 3, y0 + 6, hx + HOLD_W - 3, y0 + CELL_H - 6, fill="#1d1d23", outline="")
                s.create_line(hx + 6, y0 + CELL_H / 2, hx + HOLD_W - 6, y0 + CELL_H / 2, fill=ui.DIM, width=2)
        self._draw_wave()
        W = self._cell_x(len(self.frames)) + 40
        s.configure(scrollregion=(0, 0, max(W, s.winfo_width()), int(s["height"])))
        self.hold_l.configure(text=str(self.frames[self.cur]["hold"]) if 0 <= self.cur < len(self.frames) else "1")
        self._time_label(); self._playhead(self._tick_of(self.cur))

    def _x_of_tick(self, t):
        """timeline x for a time in frame ticks (piecewise: thumbnails + hold cells)"""
        acc = 0
        for i, f in enumerate(self.frames):
            if t < acc + f["hold"]:
                x = self._cell_x(i); u = t - acc
                return x + (u * CELL_W if u < 1 else CELL_W + (u - 1) * HOLD_W)
            acc += f["hold"]
        return self._cell_x(len(self.frames)) - 6

    def _tick_of_x(self, x):
        acc = 0
        for i, f in enumerate(self.frames):
            cx = self._cell_x(i); w = self._cell_w(i)
            if x < cx + w + 6:
                u = x - cx
                if u < CELL_W: return acc + max(0.0, u / CELL_W)
                return acc + min(f["hold"] - 1e-3, 1 + (u - CELL_W) / HOLD_W)
            acc += f["hold"]
        return max(0, acc - 1e-3)

    def _draw_wave(self):
        s = self.strip; y = STRIP_Y + CELL_H + 6; hh = WAVE_H
        s.create_rectangle(8, y, self._cell_x(len(self.frames)) - 6, y + hh, fill="#121216", outline="")
        if not self.audio:
            s.create_text(14, y + hh / 2, text="No audio - drop an MP3 / WAV or click Audio to add a track", anchor="w", fill=self.ui.DIM, font=(self.ui.FONT, 8))
            return
        pcm = self.audio["pcm"]; spt = SR / float(self.fps); pts_top = []; pts_bot = []
        peak = max(1.0, float(np.abs(pcm).max())) if len(pcm) else 1.0
        x_end = self._cell_x(len(self.frames)) - 6
        n = max(2, int(x_end - 8) // 2)
        for k in range(n + 1):
            x = 8 + k * (x_end - 8) / n; t = self._tick_of_x(x)
            a0 = int(t * spt); a1 = int(a0 + spt / 6) + 1
            v = min(1.0, float(np.abs(pcm[a0:a1]).max()) / peak * 0.92) if a0 < len(pcm) else 0.0
            pts_top += [x, y + hh / 2 - v * hh / 2]; pts_bot += [x, y + hh / 2 + v * hh / 2]
        s.create_polygon(*(pts_top + list(np.array(pts_bot).reshape(-1, 2)[::-1].ravel())), fill="#3a7bd5", outline="")
        s.create_line(8, y + hh / 2, x_end, y + hh / 2, fill="#2b4f86")
        s.create_text(12, y + 1, text="\u266a " + self.audio["name"], anchor="nw", fill=self.ui.TXT, font=(self.ui.FONT, 7, "bold"))

    def _playhead(self, tick):
        s = self.strip; s.delete("ph"); x = self._x_of_tick(tick)
        s.create_line(x, 2, x, int(s["height"]) - 2, fill=self.ui.RED, width=2, tags="ph")

    def _time_label(self):
        T = self._ticks(); t = self._tick_of(self.cur) if 0 <= self.cur < len(self.frames) else 0
        self.time_l.configure(text="Frame %d / %d   \u00b7   %s / %s   \u00b7   %d fps" % (
            self.cur + 1, len(self.frames), self._fmt(t / self.fps), self._fmt(T / self.fps), self.fps))

    @staticmethod
    def _fmt(sec):
        return "%d:%05.2f" % (int(sec // 60), sec % 60)

    def _see(self, i):
        s = self.strip; x0 = self._cell_x(i); x1 = x0 + self._cell_w(i); w = s.winfo_width()
        l = s.canvasx(0)
        if x0 < l or x1 > l + w:
            total = max(1, self._cell_x(len(self.frames)) + 40); s.xview_moveto(max(0, (x0 + (x1 - x0) / 2 - w / 2) / total))

    def _s_index(self, e):
        x = self.strip.canvasx(e.x)
        for i in range(len(self.frames)):
            cx = self._cell_x(i)
            if cx - 3 <= x <= cx + self._cell_w(i) + 3: return i
        return None

    def _s_press(self, e):
        y = e.y
        if y > STRIP_Y + CELL_H + 2:
            self.stop_play(); self._sdrag = {"scrub": True}; self._scrub(e); return
        i = self._s_index(e)
        if i is None: return
        self._sdrag = {"i": i, "x": e.x, "moved": False}
        if i != self.cur: self.goto(i)

    def _scrub(self, e):
        t = self._tick_of_x(self.strip.canvasx(e.x)); i = self._frame_at_tick(int(t))
        if i != self.cur: self.goto(i)
        self._playhead(int(t)); self._blip(int(t))

    def _s_drag(self, e):
        d = self._sdrag
        if not d: return
        if d.get("scrub"): self._scrub(e); return
        if abs(e.x - d["x"]) > 8: d["moved"] = True
        if d["moved"]:
            s = self.strip; s.delete("drop"); j = self._drop_slot(e); x = self._cell_x(j) - 3
            s.create_line(x, STRIP_Y - 4, x, STRIP_Y + CELL_H + 4, fill=self.ui.RED, width=3, tags="drop")

    def _drop_slot(self, e):
        x = self.strip.canvasx(e.x)
        for j in range(len(self.frames)):
            if x < self._cell_x(j) + self._cell_w(j) / 2: return j
        return len(self.frames)

    def _s_release(self, e):
        d = self._sdrag; self._sdrag = None; self.strip.delete("drop")
        if not d or d.get("scrub") or not d["moved"]: return
        j = self._drop_slot(e); i = d["i"]
        if j > i: j -= 1
        if j != i: self.move_frame(i, j); self._status("Frame moved to position %d" % (j + 1))

    def _s_double(self, e):
        i = self._s_index(e)
        if i is None or e.y > STRIP_Y + CELL_H: return
        from tkinter import simpledialog
        n = simpledialog.askinteger("Hold frame", "Show frame %d for how many frames?" % (i + 1), initialvalue=self.frames[i]["hold"],
                                    minvalue=1, maxvalue=240, parent=self)
        if n: self.goto(i); self.set_hold(n)

    def _s_menu(self, e):
        i = self._s_index(e)
        if i is not None and i != self.cur: self.goto(i)
        ui = self.ui
        m = tk.Menu(self, tearoff=0, bg=ui.CARD2, fg=ui.TXT, activebackground=ui.RED, activeforeground="white", font=(ui.FONT, 9))
        self._m_frames(m); m.tk_popup(e.x_root, e.y_root)

    # ================================================================== playback + audio
    def toggle_play(self):
        if self.playing: self.stop_play()
        else: self.play()

    def play(self):
        if self.exporting: return
        self.cancel_modes()
        if len(self.frames) < 1: return
        self._status("Preparing playback\u2026"); self.update_idletasks()
        for i in range(len(self.frames)): self.flat(i)
        start = self._tick_of(self.cur) if self.from_here else 0
        if not self.from_here and self.cur != 0: pass
        self.playing = True; self._p_start = start; self._p_t0 = time.perf_counter(); self._p_last = -1
        self.play_b.configure(image=self.icons.setdefault("apause", ImageTk.PhotoImage(_aicon("pause", 14, (255, 255, 255)))))
        self._play_audio(start); self._status("Playing at %d fps \u00b7 P or the button stops" % self.fps)
        self._play_tick()

    def _play_tick(self):
        if not self.playing: return
        T = self._ticks(); el = time.perf_counter() - self._p_t0; t = self._p_start + int(el * self.fps)
        if t >= T:
            if self.loop:
                self._p_start = 0; self._p_t0 = time.perf_counter(); t = 0; self._play_audio(0)
            else:
                self.stop_play(); return
        i = self._frame_at_tick(t)
        if i != self._p_last:
            self._p_last = i; self.cur_play = i; self._show_flat(i)
            self.time_l.configure(text="Frame %d / %d   \u00b7   %s / %s   \u00b7   %d fps" % (i + 1, len(self.frames), self._fmt(t / self.fps), self._fmt(T / self.fps), self.fps))
        self._playhead(t)
        nxt = self._p_t0 + (t + 1 - self._p_start) / float(self.fps)
        self.after(max(1, int((nxt - time.perf_counter()) * 1000)), self._play_tick)

    def stop_play(self):
        if not self.playing: return
        self.playing = False; self.player.stop()
        self.play_b.configure(image=self.icons["aplay(255, 255, 255)"])
        i = getattr(self, "cur_play", self.cur); self.cur = -1 if i != self.cur else self.cur
        if self.cur == -1: self.goto(i)
        else: self.full_refresh(); self.draw_strip()
        self._status("Stopped")

    def _wav(self, a0, a1, name="play.wav"):
        pcm = self.audio["pcm"][max(0, a0):max(0, a1)]
        p = os.path.join(self.tmp, name)
        with wave.open(p, "wb") as w:
            w.setnchannels(1); w.setsampwidth(2); w.setframerate(SR); w.writeframes(pcm.astype("<i2").tobytes())
        return p

    def _play_audio(self, tick):
        if not self.audio: return
        a0 = int(tick * SR / self.fps); a1 = int(self._ticks() * SR / self.fps)
        if a0 >= len(self.audio["pcm"]): self.player.stop(); return
        try: self.player.play(self._wav(a0, a1))
        except Exception: pass

    def _blip(self, tick):
        """frame-accurate scrubbing: play just this frame's slice of audio"""
        if not self.audio: return
        now = time.perf_counter()
        if now - getattr(self, "_blip_t", 0) < 0.05: return
        self._blip_t = now; a0 = int(tick * SR / self.fps); a1 = a0 + int(SR / self.fps)
        if a0 >= len(self.audio["pcm"]): return
        try: self.player.play(self._wav(a0, a1, "blip%d.wav" % (int(now * 10) % 3)))
        except Exception: pass

    def import_audio(self, path=None):
        if path is None:
            path = filedialog.askopenfilename(parent=self.app, title="Add an audio track", initialdir=self.ui.downloads_dir(),
                                              filetypes=[("Audio", "*.mp3 *.wav *.m4a *.ogg *.flac"), ("All files", "*.*")])
        if not path: return
        self._status("Reading %s\u2026" % os.path.basename(path)); self.update_idletasks()
        try: pcm = self._decode_audio(path)
        except Exception as e:
            messagebox.showerror(self.ui.APP, "Couldn't read that audio file:\n%s" % e, parent=self); return
        with open(path, "rb") as fh: raw = fh.read()
        self.audio = {"name": os.path.basename(path), "pcm": pcm, "raw": raw}
        self.dirty = True; self._audio_ui(); self.draw_strip()
        sec = len(pcm) / SR; need = int(math.ceil(sec * self.fps))
        self._status("Audio added: %s (%s) \u00b7 the animation is %s - add or hold frames to match (%d frames at %d fps)" % (
            self.audio["name"], self._fmt(sec), self._fmt(self._ticks() / self.fps), need, self.fps))

    @staticmethod
    def _decode_audio(path):
        r = subprocess.run([ffmpeg_exe(), "-v", "error", "-nostdin", "-i", path, "-vn", "-f", "s16le", "-acodec", "pcm_s16le", "-ac", "1", "-ar", str(SR), "-"],
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE, creationflags=NO_WINDOW, timeout=180)
        if r.returncode != 0 or not r.stdout:
            raise RuntimeError((r.stderr or b"no audio found").decode("utf-8", "replace")[-400:])
        return np.frombuffer(r.stdout[: len(r.stdout) // 2 * 2], "<i2").copy()

    def remove_audio(self):
        if self.audio: self.audio = None; self.dirty = True; self._audio_ui(); self.draw_strip(); self._status("Audio track removed")

    def _audio_ui(self):
        if not hasattr(self, "aud_b"): return
        if self.audio:
            self.aud_x.pack(side="right", padx=(0, 2)); self.aud_b.pack_forget(); self.aud_b.pack(side="right", padx=2, before=self.aud_x)
        else: self.aud_x.pack_forget()

    # ================================================================== drawing: stabiliser (smoothing)
    def _press(self, e):
        super()._press(e)
        if self.stroke is not None and self.tool in ("brush", "pencil", "eraser"):
            self._stab = list(self.to_doc(e.x, e.y))
        else:
            self._stab = None

    def _motion_drag(self, e):
        st = getattr(self, "_stab", None)
        if self.stroke is not None and st is not None and self.opt.get("stab", 0) > 0.01 and self.drag and not self.drag.get("pan"):
            x, y = self.to_doc(e.x, e.y); self.mouse_doc = (int(x), int(y)); self.drag["moved"] = True
            k = 1.0 - min(0.92, self.opt["stab"] * 0.9)
            st[0] += (x - st[0]) * k; st[1] += (y - st[1]) * k
            self.refresh(self.stroke.to(st[0], st[1], self._pressure())); self._hover(e); return
        super()._motion_drag(e)

    def _release(self, e):
        st = getattr(self, "_stab", None)
        if self.stroke is not None and st is not None and self.opt.get("stab", 0) > 0.01:
            x, y = self.to_doc(e.x, e.y); k = 1.0 - min(0.92, self.opt["stab"] * 0.9)
            for _ in range(6):
                st[0] += (x - st[0]) * max(k, 0.35); st[1] += (y - st[1]) * max(k, 0.35)
                self.refresh(self.stroke.to(st[0], st[1], self._pressure()))
        self._stab = None
        super()._release(e)

    def _opts_refresh(self):
        super()._opts_refresh()
        if self.tf and self.winfo_width() < 1500:
            for w in self.opts.winfo_children():
                if isinstance(w, tk.Label) and str(w.cget("fg")) == self.ui.DIM: w.destroy()
        if self.tool in ("brush", "pencil", "eraser") and not self.tf:
            o = self.opts; kids = o.winfo_children()
            s = A.Slide(o, self.ui, "Smoothing", 0, 100, self.opt.get("stab", 0.55) * 100, cmd=lambda v: self.opt.__setitem__("stab", v / 100.0),
                        width=80, unit="%")
            hint = [w for w in kids if isinstance(w, tk.Label) and str(w.cget("fg")) == self.ui.DIM]
            if hint: hint[-1].destroy()
            s.pack(side="left", padx=(10, 0))
        if self.tool == "text" and not self.tf:
            for w in self.opts.winfo_children():
                if isinstance(w, tk.Label) and str(w.cget("fg")) == self.ui.DIM:
                    w.configure(text="Click to add text \u00b7 double-click text (Text or Move tool) to edit \u00b7 Move tool: drag, resize, rotate - it stays editable")

    def _double(self, e):
        if self.tool in ("text", "move") and self.doc.layer.text:
            if self.tf: self.tf_cancel()
            self.text_dialog(edit=self.doc.active); return
        super()._double(e)

    # ================================================================== text (editable, styled, frame ranges)
    def _find_link(self, f, link):
        ls, packed = self._layers_of(f)
        for i, l in enumerate(ls):
            t = l["text"] if packed else l.text
            if t and t.get("link") == link: return i
        return None

    def _link_range(self, link):
        idx = [i for i, f in enumerate(self.frames) if self._find_link(f, link) is not None]
        return (min(idx), max(idx)) if idx else (self.cur, self.cur)

    def apply_text(self, T, f0, f1, label="Text"):
        T = dict(T); link = T.setdefault("link", C.new_id()); T["kind"] = "rextext"
        px = render_text(self.W, self.H, T); name = "T  " + (T["text"].strip().splitlines() or ["Text"])[0][:22]
        proto = pack_layer(C.Layer(name, px, text=T))
        for i, f in enumerate(self.frames):
            j = self._find_link(f, link); inr = f0 <= i <= f1
            if not inr and j is None: continue
            if "doc" in f:
                d = f["doc"]
                if inr:
                    if j is None: d.layers.append(C.Layer(name, px, text=dict(T))); j = len(d.layers) - 1
                    else: L = d.layers[j]; L.px = px; L.text = dict(T); L.name = name
                    if f is self.frames[self.cur]: d.active = j
                else:
                    d.layers.pop(j); d.active = min(d.active, len(d.layers) - 1)
                    if not d.layers: d.layers = [C.Layer("Layer 1", C.blank(self.W, self.H))]; d.active = 0
                if i != self.cur: f["hist"].push(label)
            else:
                if inr:
                    p = dict(proto, text=dict(T))
                    if j is None: f["layers"].append(p)
                    else: p.update(visible=f["layers"][j]["visible"], locked=f["layers"][j]["locked"], opacity=f["layers"][j]["opacity"]); f["layers"][j] = p
                else:
                    f["layers"].pop(j)
                    if not f["layers"]: f["layers"] = [pack_layer(C.Layer("Layer 1", C.blank(self.W, self.H)))]
                    f["active"] = min(f["active"], len(f["layers"]) - 1)
            self._changed(i, strip=False)
        self.commit(label, full=True); self.draw_strip()

    def text_dialog(self, at=None, edit=None):
        ui = self.ui; d = self.doc; F = ui.FONT
        fl = sorted(fonts().keys()) or ["Segoe UI"]
        if edit is not None and d.layers[edit].text and d.layers[edit].text.get("kind") == "rextext":
            T = dict(d.layers[edit].text); r0, r1 = self._link_range(T["link"])
        else:
            edit = None
            dflt = next((n for n in ("Segoe UI", "Arial", "DejaVu Sans") if n in fonts()), fl[0])
            T = {"kind": "rextext", "text": "", "font": self.opt.get("font") or dflt, "size": int(self.opt.get("font_size", 96)),
                 "color": C.rgb_to_hex(self.primary), "bold": True, "stroke": 0, "stroke_color": "#FFFFFF", "shadow": False,
                 "x": float(at[0]) if at else self.W / 2, "y": float(at[1]) if at else self.H / 2, "angle": 0.0}
            r0 = r1 = self.cur
        t = self._dlg("Edit text" if edit is not None else "Add text", 540, 430)
        txt = tk.Text(t, height=3, bg=ui.CARD, fg=ui.TXT, insertbackground=ui.RED, relief="flat", font=(F, 12), padx=8, pady=6,
                      highlightthickness=1, highlightbackground=ui.BORDER, highlightcolor=ui.RED, wrap="word")
        txt.pack(fill="x", padx=16); txt.insert("1.0", T["text"] or "Your text"); txt.tag_add("sel", "1.0", "end"); txt.focus_set()
        L_ = lambda p, s: tk.Label(p, text=s, bg=ui.PANEL, fg=ui.MUT, font=(F, 9))
        SP = lambda p, var, lo, hi, w=5: tk.Spinbox(p, from_=lo, to=hi, textvariable=var, width=w, bg=ui.CARD2, fg=ui.TXT, buttonbackground=ui.CARD2,
                                                    relief="flat", font=(F, 10), insertbackground=ui.TXT)
        CK = lambda p, s, var: tk.Checkbutton(p, text=s, variable=var, command=lambda: prev(), bg=ui.PANEL, fg=ui.TXT, selectcolor=ui.CARD2,
                                              activebackground=ui.PANEL, activeforeground=ui.TXT, font=(F, 9), highlightthickness=0)
        r = tk.Frame(t, bg=ui.PANEL); r.pack(fill="x", padx=16, pady=(10, 4))
        fv = tk.StringVar(value=T["font"] if T["font"] in fl else next((n for n in ("Segoe UI", "Arial", "DejaVu Sans") if n in fl), fl[0]))
        L_(r, "Font").pack(side="left"); ttk.Combobox(r, textvariable=fv, values=fl, width=26, font=(F, 9)).pack(side="left", padx=(6, 12))
        sv = tk.StringVar(value=str(int(T["size"]))); L_(r, "Size").pack(side="left"); SP(r, sv, 8, 800).pack(side="left", padx=6)
        bv = tk.BooleanVar(value=T.get("bold", False)); CK(r, "Bold", bv).pack(side="left", padx=(8, 0))
        r2 = tk.Frame(t, bg=ui.PANEL); r2.pack(fill="x", padx=16, pady=4)
        col = {"c": T["color"], "s": T.get("stroke_color", "#FFFFFF")}
        def chip(parent, key, label):
            b = tk.Label(parent, text=label, cursor="hand2", font=(F, 9, "bold"), padx=8, pady=3, highlightthickness=1,
                         highlightbackground="#5a5a66"); b.pack(side="left", padx=(0, 6))
            def paint():
                c = C.hex_to_rgb(col[key]); b.configure(bg=col[key], fg="white" if sum(c) < 380 else "black")
            def pick(e=None):
                c = colorchooser.askcolor(color=col[key], parent=t)
                if c and c[1]: col[key] = c[1].upper(); paint(); prev()
            b.bind("<Button-1>", pick); paint(); return paint
        L_(r2, "Colour").pack(side="left", padx=(0, 6)); pc = chip(r2, "c", " Text ")
        ui.Btn(r2, "Use picker colour", lambda: (col.__setitem__("c", C.rgb_to_hex(self.primary)), pc(), prev()), font=(F, 8, "bold"),
               padx=8, pady=3).pack(side="left", padx=(0, 14))
        shv = tk.BooleanVar(value=T.get("shadow", False)); CK(r2, "Drop shadow", shv).pack(side="left")
        r3 = tk.Frame(t, bg=ui.PANEL); r3.pack(fill="x", padx=16, pady=4)
        stv = tk.StringVar(value=str(int(T.get("stroke", 0)))); L_(r3, "Outline").pack(side="left"); SP(r3, stv, 0, 60, 4).pack(side="left", padx=6)
        L_(r3, "px").pack(side="left", padx=(0, 8)); pcs = chip(r3, "s", " Outline colour ")
        r4 = tk.Frame(t, bg=ui.PANEL); r4.pack(fill="x", padx=16, pady=(10, 4)); n = len(self.frames)
        f0v, f1v = tk.StringVar(value=str(r0 + 1)), tk.StringVar(value=str(r1 + 1))
        L_(r4, "Show on frames").pack(side="left"); SP(r4, f0v, 1, n, 4).pack(side="left", padx=6); L_(r4, "to").pack(side="left")
        SP(r4, f1v, 1, n, 4).pack(side="left", padx=6)
        ui.Btn(r4, "All", lambda: (f0v.set("1"), f1v.set(str(n))), font=(F, 8, "bold"), padx=8, pady=3).pack(side="left", padx=4)
        ui.Btn(r4, "Only this", lambda: (f0v.set(str(self.cur + 1)), f1v.set(str(self.cur + 1))), font=(F, 8, "bold"), padx=8, pady=3).pack(side="left", padx=4)
        tk.Label(t, text="Titles and captions can stay on a range of frames. Move / resize / rotate it later with the Move tool - it stays editable.",
                 bg=ui.PANEL, fg=ui.DIM, font=(F, 8), wraplength=500, justify="left").pack(anchor="w", padx=16, pady=(6, 0))
        lid = d.layers[edit].id if edit is not None else -1; state = {"job": None}
        def build():
            try: size = max(8, min(800, int(sv.get())))
            except ValueError: size = 96
            try: stw = max(0, min(60, int(stv.get())))
            except ValueError: stw = 0
            return dict(T, text=txt.get("1.0", "end-1c"), font=fv.get(), size=size, bold=bv.get(), color=col["c"], stroke=stw,
                        stroke_color=col["s"], shadow=shv.get())
        def prev(*_):
            if state["job"]: t.after_cancel(state["job"])
            state["job"] = t.after(120, prev_now)
        def prev_now():
            state["job"] = None
            if not t.winfo_exists(): return
            px = render_text(self.W, self.H, build())
            if edit is not None: self.full_refresh(override={lid: (px, None)})
            else:
                tmp = C.Layer("t", px); save = d.layers; d.layers = d.layers + [tmp]; self.full_refresh(); d.layers = save
        txt.bind("<KeyRelease>", prev)
        for v in (fv, sv, stv): v.trace_add("write", prev)
        def ok():
            T2 = build()
            if not T2["text"].strip(): cancel(); return
            try: a, b = sorted((max(1, min(n, int(f0v.get()))), max(1, min(n, int(f1v.get())))))
            except ValueError: a = b = self.cur + 1
            t.destroy(); self.opt["font"] = T2["font"]; self.opt["font_size"] = T2["size"]
            self.apply_text(T2, a - 1, b - 1, "Edit text" if edit is not None else "Text")
            self._remember(C.hex_to_rgb(T2["color"]))
            if self.tool == "text": self.set_tool("move")
        def cancel():
            t.destroy(); self.full_refresh()
        self._dlg_buttons(t, ok, cancel, ok_text="Add" if edit is None else "Update")
        t.unbind("<Return>"); t.bind("<Control-Return>", lambda e: ok())
        prev()

    def tf_start(self, move=False):
        T = self.doc.layer.text if hasattr(self, "doc") else None
        super().tf_start(move)
        if self.tf: self.tf["rextext"] = dict(T) if (T and T.get("kind") == "rextext" and self.doc.sel is None) else None

    def tf_apply(self, label="Free transform"):
        tf = self.tf
        T = tf.get("rextext") if tf else None
        if not T or tf["flipx"] != 1 or tf["flipy"] != 1:
            super().tf_apply(label); return
        moved = not (tf["sx"] == 1 and tf["sy"] == 1 and tf["ang"] == 0 and tf["tx"] == 0 and tf["ty"] == 0)
        self.tf = None
        if not moved:
            tf["L"].px = tf["src"]; tf["L"].text = T; self._opts_refresh(); self.full_refresh(); return
        M = self._tf_M(tf); x, y = M @ np.array([T["x"], T["y"], 1.0])
        T2 = dict(T, x=float(x), y=float(y), size=max(8, int(round(T["size"] * math.sqrt(abs(tf["sx"] * tf["sy"]))))),
                  stroke=int(round(T.get("stroke", 0) * math.sqrt(abs(tf["sx"] * tf["sy"])))), angle=float(T.get("angle", 0.0)) - tf["ang"])
        tf["L"].px = tf["src"]; tf["L"].text = T; self._opts_refresh()
        r0, r1 = self._link_range(T["link"]); self.apply_text(T2, r0, r1, "Transform text")

    def tf_cancel(self):
        tf = self.tf
        super().tf_cancel()
        if tf and tf.get("rextext"): tf["L"].text = tf["rextext"]

    # ================================================================== background (shared by every frame)
    def set_bg_image(self, path=None, px=None):
        if px is None:
            if path is None:
                path = filedialog.askopenfilename(parent=self.app, title="Background image", initialdir=A.pictures_dir(self.ui),
                                                  filetypes=[("Images", "*.png *.jpg *.jpeg *.webp *.bmp"), ("All files", "*.*")])
            if not path: return
            try: px = C.open_image(path)
            except Exception as e:
                messagebox.showerror(self.ui.APP, "Couldn't open that image:\n%s" % e, parent=self); return
        self.bg_img = px; self._rebuild_bg(); self.dirty = True; self.full_refresh(); self.draw_strip()
        self._status("Background set for every frame (it fills the canvas)")

    def set_bg_color(self):
        c = colorchooser.askcolor(color=C.rgb_to_hex(self.bg_color), parent=self, title="Background colour")
        if c and c[0]:
            self.bg_color = tuple(int(v) for v in c[0]); self._rebuild_bg(); self.dirty = True; self.full_refresh(); self.draw_strip()

    def clear_bg(self):
        self.bg_img = None; self.bg_color = (255, 255, 255); self._rebuild_bg(); self.dirty = True; self.full_refresh(); self.draw_strip()
        self._status("Background: white")

    # ================================================================== menus + keys
    def _m_file(self, m):
        mi = self._mi
        mi(m, "New animation\u2026", self.new_dialog, "Ctrl+N"); mi(m, "Open project (.rexanim)\u2026", self.open_file, "Ctrl+O")
        mi(m, "Open the sample (REX walk, wave, jump)", self.load_sample)
        if os.path.exists(self._autosave_path()): mi(m, "Recover autosave", self.recover_autosave)
        m.add_separator()
        mi(m, "Save project", self.save_project, "Ctrl+S"); mi(m, "Save project as\u2026", lambda: self.save_project(True), "Ctrl+Shift+S")
        m.add_separator()
        mi(m, "Add image / screenshot to this frame\u2026", lambda: self.open_file(as_layer=True), "Ctrl+Shift+O")
        mi(m, "Paste image from clipboard", self.paste, "Ctrl+V"); mi(m, "Take screenshot onto this frame", self.take_screenshot, "Ctrl+Shift+P")
        mi(m, "Background image\u2026", self.set_bg_image); mi(m, "Background colour\u2026", self.set_bg_color); mi(m, "Plain white background", self.clear_bg)
        mi(m, "Add audio track (MP3 / WAV)\u2026", self.import_audio); mi(m, "Remove audio track", self.remove_audio, enabled=bool(self.audio))
        m.add_separator()
        mi(m, "Export video / GIF / PNG sequence\u2026", self.export_dialog, "Ctrl+E")
        mi(m, "Export this frame as PNG\u2026", lambda: self.export_as("PNG"))
        mi(m, "Send video to EX STUDIO", self.send_studio)

    def _m_frames(self, m):
        mi = self._mi
        mi(m, "New frame", self.add_frame, "Alt+N"); mi(m, "Duplicate frame", self.dup_frame, "Alt+D"); mi(m, "Insert blank frame before", self.insert_blank)
        mi(m, "Copy frame", self.copy_frame); mi(m, "Paste frame", self.paste_frame, enabled=self.fclip is not None)
        mi(m, "Delete frame", self.del_frame); m.add_separator()
        mi(m, "Hold for\u2026", lambda: self._s_double(type("E", (), {"x": self._cell_x(self.cur) - self.strip.canvasx(0) + 5, "y": STRIP_Y + 5})()))
        mi(m, "Move frame left", lambda: self.move_frame(self.cur, self.cur - 1)); mi(m, "Move frame right", lambda: self.move_frame(self.cur, self.cur + 1))

    def _m_image(self, m):
        self._m_frames(m); m.add_separator()
        self._mi(m, "Flip frame horizontal", lambda: self.flip_canvas(True)); self._mi(m, "Flip frame vertical", lambda: self.flip_canvas(False))
        m.add_separator(); self._mi(m, "Zoom to fit", self.fit, "Ctrl+0"); self._mi(m, "Actual pixels (100%)", lambda: self.set_zoom(1.0), "Ctrl+1")

    def key(self, e):
        w = self.focus_get()
        if isinstance(w, (tk.Entry, tk.Text, tk.Spinbox, ttk.Combobox, ttk.Entry)): return None
        try:
            if w is not None and w.winfo_toplevel() is not self.app: return None
        except Exception:
            pass
        sh, ctrl, alt = self._mods(e); ks = e.keysym; k = ks.lower() if len(ks) == 1 else ks
        if ctrl and not sh and k == "e": self.export_dialog(); return "break"
        if alt and not ctrl and k in ("n", "d"): (self.add_frame if k == "n" else self.dup_frame)(); return "break"
        if not ctrl and not alt:
            if k in ("comma", "less"): self.step(-1); return "break"
            if k in ("period", "greater"): self.step(1); return "break"
            if k == "Home": self.goto(0); return "break"
            if k == "End": self.goto(len(self.frames) - 1); return "break"
            if k == "p" or (k == "Return" and not self.tf and self.tool != "crop"): self.toggle_play(); return "break"
        if self.playing and k not in ("space",): self.stop_play()
        return super().key(e)

    # ================================================================== files
    def _maybe_save(self):
        if not self.dirty: return True
        r = messagebox.askyesnocancel(self.ui.APP, "Save your animation first?", parent=self)
        if r is None: return False
        if r: return self.save_project()
        return True

    def new_dialog(self):
        ui = self.ui; t = self._dlg("New animation", 460, 290); F = ui.FONT
        f = tk.Frame(t, bg=ui.PANEL); f.pack(fill="x", padx=16)
        pv = tk.StringVar(value=SIZES[0][0]); fv = tk.StringVar(value=str(self.fps)); bgv = tk.StringVar(value="white")
        tk.Label(f, text="Canvas", bg=ui.PANEL, fg=ui.MUT, font=(F, 9)).grid(row=0, column=0, sticky="w", pady=6)
        ttk.Combobox(f, textvariable=pv, values=[s[0] for s in SIZES], state="readonly", width=32, font=(F, 9)).grid(row=0, column=1, sticky="w", padx=8)
        tk.Label(f, text="Frames / sec", bg=ui.PANEL, fg=ui.MUT, font=(F, 9)).grid(row=1, column=0, sticky="w", pady=6)
        ttk.Combobox(f, textvariable=fv, values=[str(v) for v in FPS_CHOICES], state="readonly", width=6, font=(F, 9)).grid(row=1, column=1, sticky="w", padx=8)
        tk.Label(f, text="Background", bg=ui.PANEL, fg=ui.MUT, font=(F, 9)).grid(row=2, column=0, sticky="w", pady=6)
        bf = tk.Frame(f, bg=ui.PANEL); bf.grid(row=2, column=1, sticky="w", padx=8)
        for txt, v in (("White", "white"), ("Black", "black"), ("2nd colour", "secondary")):
            tk.Radiobutton(bf, text=txt, variable=bgv, value=v, bg=ui.PANEL, fg=ui.TXT, selectcolor=ui.CARD2, activebackground=ui.PANEL,
                           activeforeground=ui.TXT, font=(F, 9), highlightthickness=0).pack(side="left", padx=(0, 8))
        def ok():
            if not self._maybe_save(): return
            w, h = [(s[1], s[2]) for s in SIZES if s[0] == pv.get()][0]
            col = {"white": (255, 255, 255), "black": (0, 0, 0), "secondary": tuple(self.secondary)}[bgv.get()]
            t.destroy()
            self.new_project(w, h, [self._blank_frame(w, h)], bg_color=col, fps=int(fv.get()))
            self._status("New animation %d \u00d7 %d at %s fps - draw on frame 1, then add frames" % (w, h, fv.get()))
        self._dlg_buttons(t, ok, ok_text="Create")

    @staticmethod
    def _blank_frame(w, h):
        return {"layers": [pack_layer(C.Layer("Layer 1", C.blank(w, h)))], "hold": 1, "active": 0}

    def load_sample(self):
        if not self._maybe_save(): return
        self._status("Building the sample\u2026"); self.update_idletasks()
        bg = sample_background(1920, 1080); bg = np.concatenate([bg, np.full(bg.shape[:2] + (1,), 255, np.uint8)], 2)
        self.new_project(1920, 1080, sample_frames(1920, 1080), bg=bg, fps=12, name="REX walk wave jump")
        self._status("Sample: REX walks in, waves and jumps (28 frames, 12 fps) - press Play or P")

    def open_file(self, path=None, as_layer=False):
        if as_layer: return super().open_file(path, as_layer=True)
        if path is None:
            path = filedialog.askopenfilename(parent=self.app, title="Open REX ANIMATE project", initialdir=self._proj_dir(),
                                              filetypes=[("REX ANIMATE project", "*.rexanim"), ("Images", "*.png *.jpg *.jpeg *.webp"), ("All files", "*.*")])
        if not path: return
        if not path.lower().endswith(".rexanim"):
            self.drop_files([path]); return
        if not self._maybe_save(): return
        try: self.load_project(path)
        except Exception as e:
            messagebox.showerror(self.ui.APP, "Couldn't open that project:\n%s" % e, parent=self)

    AUDIO_EXT = (".mp3", ".wav", ".m4a", ".ogg", ".flac")

    def drop_files(self, files, at=None):
        pj = [f for f in files if f.lower().endswith(".rexanim")]
        au = [f for f in files if f.lower().endswith(self.AUDIO_EXT)]
        if pj: self.open_file(pj[0]); return
        if au: self.import_audio(au[0])
        im = [f for f in files if f.lower().endswith(self.IMG_EXT)]
        if im: super().drop_files(im, at)
        elif not au: self._status("Drop images, MP3 / WAV audio or a .rexanim project here")

    def _proj_dir(self):
        d = os.path.join(A.pictures_dir(self.ui), "REX ANIMATE")
        try: os.makedirs(d, exist_ok=True)
        except Exception: return self.ui.downloads_dir()
        return d

    def save_project(self, as_new=False):
        p = self.proj_path
        if as_new or not p:
            p = filedialog.asksaveasfilename(parent=self.app, title="Save REX ANIMATE project", initialdir=self._proj_dir(),
                                             initialfile="%s.rexanim" % (self.proj_name if self.proj_name != "Untitled" else "REX ANIMATE " + time.strftime("%Y-%m-%d %H%M")),
                                             defaultextension=".rexanim", filetypes=[("REX ANIMATE project", "*.rexanim")])
            if not p: return False
        try:
            self.cancel_modes(); self.write_project(p)
        except Exception as e:
            messagebox.showerror(self.ui.APP, "Couldn't save:\n%s" % e, parent=self); return False
        self.proj_path = p; self.proj_name = os.path.splitext(os.path.basename(p))[0]; self.dirty = False
        self._status("Saved %s (%d frames)" % (p, len(self.frames))); return True

    @staticmethod
    def _png(a):
        ok, b = cv2.imencode(".png", cv2.cvtColor(a, cv2.COLOR_RGBA2BGRA if a.shape[2] == 4 else cv2.COLOR_RGB2BGR), [cv2.IMWRITE_PNG_COMPRESSION, 3])
        return b.tobytes()

    @staticmethod
    def _unpng(b):
        a = cv2.imdecode(np.frombuffer(b, np.uint8), cv2.IMREAD_UNCHANGED)
        if a.ndim == 2: a = cv2.cvtColor(a, cv2.COLOR_GRAY2BGRA)
        if a.shape[2] == 3: a = cv2.cvtColor(a, cv2.COLOR_BGR2BGRA)
        return cv2.cvtColor(a, cv2.COLOR_BGRA2RGBA)

    def write_project(self, p):
        meta = {"app": NAME, "format": 1, "w": self.W, "h": self.H, "fps": self.fps, "bg_color": list(self.bg_color),
                "background": self.bg_img is not None, "audio": None, "current": self.cur, "frames": []}
        tmp = p + ".part"
        with zipfile.ZipFile(tmp, "w", zipfile.ZIP_STORED) as z:
            for i, f in enumerate(self.frames):
                ls = [pack_layer(l) for l in f["doc"].layers] if "doc" in f else f["layers"]
                act = f["doc"].active if "doc" in f else f["active"]
                fm = {"hold": f["hold"], "active": act, "layers": []}
                for j, l in enumerate(ls):
                    e = {k: l[k] for k in ("name", "visible", "locked", "opacity", "blend", "text")}; e["bb"] = l["bb"]
                    if l["bb"] is not None:
                        e["file"] = "f%04d_l%02d.png" % (i, j); z.writestr(e["file"], self._png(l["crop"]))
                    fm["layers"].append(e)
                meta["frames"].append(fm)
            if self.bg_img is not None: z.writestr("background.png", self._png(self.bg_img))
            if self.audio:
                an = "audio/" + self.audio["name"]; z.writestr(an, self.audio["raw"]); meta["audio"] = an
            try: z.writestr("preview.png", self._png(self.flat(0)))
            except Exception: pass
            z.writestr("project.json", json.dumps(meta, indent=1))
        os.replace(tmp, p)

    def load_project(self, p, as_autosave=False):
        with zipfile.ZipFile(p) as z:
            meta = json.loads(z.read("project.json").decode("utf-8"))
            W, H = int(meta["w"]), int(meta["h"]); frames = []
            for fm in meta["frames"]:
                ls = []
                for e in fm["layers"]:
                    crop = self._unpng(z.read(e["file"])) if e.get("bb") else None
                    ls.append({"id": C.new_id(), "name": e["name"], "visible": e["visible"], "locked": e["locked"], "opacity": e["opacity"],
                               "blend": e.get("blend", "Normal"), "text": e.get("text"), "bb": tuple(e["bb"]) if e.get("bb") else None, "crop": crop})
                frames.append({"layers": ls or [pack_layer(C.Layer("Layer 1", C.blank(W, H)))], "hold": int(fm.get("hold", 1)), "active": int(fm.get("active", 0))})
            bg = self._unpng(z.read("background.png")) if meta.get("background") else None
            audio = None
            if meta.get("audio"):
                raw = z.read(meta["audio"]); name = os.path.basename(meta["audio"])
                ap = os.path.join(self.tmp, "in_" + name)
                with open(ap, "wb") as fh: fh.write(raw)
                try: audio = {"name": name, "pcm": self._decode_audio(ap), "raw": raw}
                except Exception: audio = None
        nm = os.path.splitext(os.path.basename(p))[0]
        self.new_project(W, H, frames, bg=bg, bg_color=tuple(meta.get("bg_color", (255, 255, 255))), fps=int(meta.get("fps", 12)), audio=audio,
                         name="Recovered animation" if as_autosave else nm, path=None if as_autosave else p)
        c = int(meta.get("current", 0))
        if 0 < c < len(frames): self.goto(c)
        self.dirty = bool(as_autosave)
        self._status("Opened %s (%d frames, %d fps%s)" % (os.path.basename(p), len(frames), self.fps, ", with audio" if audio else ""))

    def _autosave_path(self):
        base = os.environ.get("LOCALAPPDATA") or os.path.join(os.path.expanduser("~"), ".local", "share")
        return os.path.join(base, "EX CREATOR STUDIO", "REX ANIMATE autosave.rexanim")

    def _autosave_tick(self):
        try:
            if self.dirty and not self.playing and not self.exporting and not self.tf and self.stroke is None and \
                    time.time() - getattr(self, "_as_t", 0) > 120:
                p = self._autosave_path(); os.makedirs(os.path.dirname(p), exist_ok=True); self.write_project(p); self._as_t = time.time()
        except Exception:
            pass
        self.after(20000, self._autosave_tick)

    def recover_autosave(self):
        if not self._maybe_save(): return
        try: self.load_project(self._autosave_path(), as_autosave=True)
        except Exception as e:
            messagebox.showerror(self.ui.APP, "Couldn't recover the autosave:\n%s" % e, parent=self)

    # ================================================================== export
    def _export(self, p, fmt, transparent):
        try:
            self.cancel_modes(); im = Image.fromarray(self.render_frame(self.doc.layers, False))
            im.save(p, "JPEG", quality=95) if fmt == "JPG" else im.save(p, "PNG")
        except Exception as e:
            messagebox.showerror(self.ui.APP, "Couldn't export:\n%s" % e, parent=self); return None
        self._status("Exported frame %d to %s" % (self.cur + 1, p)); return p

    def quick_export(self):
        self.export_dialog()

    def _dest_dir(self, where):
        try:
            import ecs_screenrec as R
            d = R.videos_dir() if where == "videos" else R.downloads_dir()
        except Exception:
            d = os.path.join(os.path.expanduser("~"), "Videos" if where == "videos" else "Downloads")
        try: os.makedirs(d, exist_ok=True)
        except Exception: d = self.ui.downloads_dir()
        return d

    def export_dialog(self):
        if self.exporting: self._status("An export is already running"); return
        self.stop_play(); self.cancel_modes()
        ui = self.ui; F = ui.FONT; t = self._dlg("Export animation", 500, 360)
        fmt = tk.StringVar(value="mp4"); res = tk.StringVar(value="1080"); dest = tk.StringVar(value="videos")
        smooth = tk.BooleanVar(value=True); aud = tk.BooleanVar(value=bool(self.audio))
        def radios(label, var, opts):
            r = tk.Frame(t, bg=ui.PANEL); r.pack(fill="x", padx=16, pady=4)
            tk.Label(r, text=label, bg=ui.PANEL, fg=ui.MUT, font=(F, 9), width=12, anchor="w").pack(side="left")
            for txt, v in opts:
                tk.Radiobutton(r, text=txt, variable=var, value=v, bg=ui.PANEL, fg=ui.TXT, selectcolor=ui.CARD2, activebackground=ui.PANEL,
                               activeforeground=ui.TXT, font=(F, 9), highlightthickness=0).pack(side="left", padx=(0, 10))
        radios("Format", fmt, (("MP4 video", "mp4"), ("GIF", "gif"), ("PNG sequence", "png")))
        short = min(self.W, self.H)
        radios("Resolution", res, (("1080p", "1080"), ("720p", "720")))
        radios("Save to", dest, (("Videos", "videos"), ("Downloads", "downloads")))
        def ck(text, var, state="normal"):
            tk.Checkbutton(t, text=text, variable=var, bg=ui.PANEL, fg=ui.TXT, selectcolor=ui.CARD2, activebackground=ui.PANEL, state=state,
                           activeforeground=ui.TXT, font=(F, 9), highlightthickness=0, disabledforeground=ui.DIM).pack(anchor="w", padx=16, pady=2)
        ck("Smooth video (24 / 30 fps file that holds each drawing - plays well everywhere)", smooth)
        ck("Include the audio track" + ("" if self.audio else " (no audio added)"), aud, "normal" if self.audio else "disabled")
        T = self._ticks()
        tk.Label(t, text="%d frames \u00b7 %d drawings \u00b7 %s at %d fps \u00b7 MP4 is H.264%s. GIFs are made at up to 720 px." % (
            T, len(self.frames), self._fmt(T / self.fps), self.fps, " + AAC audio" if self.audio else ""), bg=ui.PANEL, fg=ui.DIM, font=(F, 8),
                 wraplength=460, justify="left").pack(anchor="w", padx=16, pady=(8, 0))
        def ok():
            t.destroy()
            self.export(fmt.get(), int(res.get()), smooth.get(), aud.get() and bool(self.audio), dest=self._dest_dir(dest.get()))
        self._dlg_buttons(t, ok, ok_text="Export")

    def _snapshot(self):
        snap = []
        for f in self.frames:
            if "doc" in f: snap.append(([l.clone() for l in f["doc"].layers], False, f["hold"]))
            else: snap.append((list(f["layers"]), True, f["hold"]))
        return snap

    def export(self, fmt="mp4", res=1080, smooth=True, with_audio=True, dest=None, path=None, wait=False, on_done=None):
        """MP4 (H.264 + AAC), GIF or PNG sequence; runs in the background with progress in the status bar."""
        if self.exporting: return None
        self.cancel_modes(); snap = self._snapshot(); fps = self.fps; T = self._ticks()
        s = min(1.0, res / float(min(self.W, self.H)))
        ow, oh = max(2, int(round(self.W * s / 2)) * 2), max(2, int(round(self.H * s / 2)) * 2)
        base = "REX ANIMATE %s %s" % (self.proj_name if self.proj_name != "Untitled" else "", time.strftime("%Y-%m-%d %H%M%S"))
        base = " ".join(base.split()); dest = dest or self._dest_dir("videos")
        if path is None: path = os.path.join(dest, base + {"mp4": ".mp4", "gif": ".gif", "png": ""}[fmt])
        out_fps = (30 if fps == 30 else 24) if (smooth and fmt == "mp4") else fps
        pcm = self.audio["pcm"] if (with_audio and self.audio and fmt == "mp4") else None
        self.exporting = True; self._exp = {"k": 0, "n": 1, "err": None, "done": False, "path": path}
        def frames_iter(w, h):
            last = None; img = None
            n_out = int(math.ceil(T * out_fps / float(fps)))
            starts = np.cumsum([0] + [hld for _, _, hld in snap])
            for k in range(n_out):
                tick = int(k * fps / float(out_fps)); i = int(np.searchsorted(starts, tick, side="right") - 1); i = min(i, len(snap) - 1)
                if i != last:
                    ls, packed, _ = snap[i]; img = self.render_frame(ls, packed)
                    if (w, h) != (self.W, self.H): img = cv2.resize(img, (w, h), interpolation=cv2.INTER_AREA)
                    last = i
                self._exp["k"], self._exp["n"] = k + 1, n_out
                yield img
        def run():
            try:
                if fmt == "mp4": self._write_mp4(path, frames_iter(ow, oh), ow, oh, out_fps, T / float(fps), pcm)
                elif fmt == "gif":
                    gs = min(1.0, 720.0 / max(self.W, self.H)); gw, gh = max(2, int(self.W * gs)), max(2, int(self.H * gs)); ims = []; durs = []
                    for i, (ls, packed, hld) in enumerate(snap):
                        img = cv2.resize(self.render_frame(ls, packed), (gw, gh), interpolation=cv2.INTER_AREA)
                        ims.append(Image.fromarray(img).convert("P", palette=Image.ADAPTIVE, colors=255)); durs.append(int(round(hld * 1000.0 / fps)))
                        self._exp["k"], self._exp["n"] = i + 1, len(snap)
                    ims[0].save(path, save_all=True, append_images=ims[1:], duration=durs, loop=0, optimize=False, disposal=1)
                else:
                    os.makedirs(path, exist_ok=True)
                    for k, img in enumerate(frames_iter(ow, oh)):
                        with open(os.path.join(path, "frame_%04d.png" % (k + 1)), "wb") as fh: fh.write(self._png(img))
            except Exception as e:
                self._exp["err"] = str(e)
            self._exp["done"] = True
        th = threading.Thread(target=run, daemon=True); th.start()
        if wait:
            while th.is_alive():                       # keep Tk serviced (a GC in the worker may need the main thread)
                th.join(0.05)
                try: self.update()
                except Exception: pass
            self.exporting = False
            if self._exp["err"]: raise RuntimeError(self._exp["err"])
            return path
        def poll():
            ex = self._exp
            if not ex["done"]:
                self._status("Exporting %s\u2026 %d / %d" % (fmt.upper(), ex["k"], ex["n"])); self.after(200, poll); return
            self.exporting = False
            if ex["err"]:
                messagebox.showerror(self.ui.APP, "Export failed:\n%s" % ex["err"], parent=self); self._status("Export failed"); return
            self._status("Saved %s" % path)
            if on_done: on_done(path)
            else:
                if messagebox.askyesno(self.ui.APP, "Exported:\n%s\n\nOpen the folder?" % path, parent=self):
                    try:
                        if os.name == "nt": os.startfile(os.path.dirname(path) if fmt != "png" else path)
                    except Exception: pass
        poll(); return path

    def _write_mp4(self, path, frames, w, h, fps, dur, pcm):
        ff = ffmpeg_exe(); tmpa = None
        cmd = [ff, "-y", "-v", "error", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", "%dx%d" % (w, h), "-r", str(fps), "-i", "-"]
        if pcm is not None:
            n = int(round(dur * SR)); a = np.zeros(n, "<i2"); m = min(n, len(pcm)); a[:m] = pcm[:m]
            tmpa = os.path.join(self.tmp, "exp_audio.raw")
            with open(tmpa, "wb") as fh: fh.write(a.tobytes())
            cmd += ["-f", "s16le", "-ar", str(SR), "-ac", "1", "-i", tmpa]
        cmd += ["-t", "%.4f" % dur, "-c:v", "libx264", "-preset", "medium", "-crf", "18", "-pix_fmt", "yuv420p", "-r", str(fps)]
        if pcm is not None: cmd += ["-c:a", "aac", "-b:a", "192k", "-ac", "2"]
        cmd += ["-movflags", "+faststart", path]
        errf = tempfile.TemporaryFile()
        p = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=errf, creationflags=NO_WINDOW)
        try:
            for img in frames: p.stdin.write(np.ascontiguousarray(img).tobytes())
            p.stdin.close(); rc = p.wait(timeout=600)
        except Exception:
            try: p.kill()
            except Exception: pass
            raise
        if rc != 0:
            errf.seek(0); raise RuntimeError(errf.read().decode("utf-8", "replace")[-600:] or "ffmpeg failed")
        if tmpa:
            try: os.remove(tmpa)
            except Exception: pass

    def send_studio(self):
        if self.exporting: return
        p = os.path.join(self.tmp, "REX ANIMATE %s.mp4" % time.strftime("%H%M%S"))
        def done(path):
            self.app.show_tab("studio"); s = self.app.studio
            try:
                s.load_recording(path); self.app.set_status("Your REX ANIMATE video was sent to EX STUDIO.")
            except Exception as e:
                messagebox.showerror(self.ui.APP, "Couldn't send to EX STUDIO:\n%s" % e, parent=self)
        self.export("mp4", 1080, True, bool(self.audio), path=p, on_done=done)

    def send_board(self):
        self.cancel_modes(); p = os.path.join(self.tmp, "frame %s.png" % time.strftime("%H%M%S")); self._export(p, "PNG", False)
        self.app.show_tab("home"); self.app.load_image(p)

    # ================================================================== tab life-cycle
    def leave(self):
        self.stop_play(); super().leave()

    def close(self):
        self.stop_play()
        if self.exporting:
            if not messagebox.askyesno(self.ui.APP, "An export is still running. Quit anyway?", parent=self): return False
        if self.dirty:
            r = messagebox.askyesnocancel(self.ui.APP, "Save your animation before closing?", parent=self)
            if r is None: return False
            if r and not self.save_project(): return False
        try: self.player.stop()
        except Exception: pass
        shutil.rmtree(self.tmp, ignore_errors=True)
        return True
