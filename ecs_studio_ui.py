"""EX STUDIO panel UI (screen recorder + lip-syncing character square). (c)2026 EX Inc./HYPERION DIGITAL (c)2026"""
import os, shutil, subprocess, tempfile, threading, time, webbrowser
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
import numpy as np
import cv2
from PIL import Image, ImageTk
import ecs_core as core
import ecs_tts as tts
import ecs_studio as st


def fmt_t(t):
    t = max(0.0, t)
    return "%02d:%04.1f" % (int(t // 60), t % 60)


class Hotkey:
    """Global Ctrl+Shift+R via RegisterHotKey on its own message-loop thread (Windows only)."""
    def __init__(self, callback):
        self.cb, self.tid, self.ok = callback, None, False
        if os.name == "nt":
            threading.Thread(target=self._loop, daemon=True).start()

    def _loop(self):
        import ctypes
        from ctypes import wintypes
        u32, k32 = ctypes.windll.user32, ctypes.windll.kernel32
        self.tid = k32.GetCurrentThreadId()
        if not u32.RegisterHotKey(None, 0xEC5, 0x0002 | 0x0004 | 0x4000, 0x52):  # CTRL|SHIFT|NOREPEAT, 'R'
            return
        self.ok = True
        msg = wintypes.MSG()
        while u32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            if msg.message == 0x0312:
                self.cb()
        u32.UnregisterHotKey(None, 0xEC5)

    def close(self):
        if self.tid:
            try:
                import ctypes
                ctypes.windll.user32.PostThreadMessageW(self.tid, 0x0012, 0, 0)
            except Exception:
                pass


class StudioPanel(tk.Frame):
    def __init__(self, master, app, ui):
        super().__init__(master, bg=ui.BG)
        self.app, self.ui = app, ui
        self.tmp = tempfile.mkdtemp(prefix="EXStudio_")
        self.player = ui.Player()
        self.rec = st.Recorder()
        self.rec_path = None; self.rec_info = None; self.cap = None; self.cap_t = -1.0
        self.frame = None; self.pos = 0.0; self.dur = 0.0
        self.sq = None; self.mouth = None; self.kind = None; self.char_name = ""
        self.lines = []; self.speak_line = None
        self.playing = False; self.p_t0 = 0.0; self.p_pos0 = 0.0; self.p_prev = 0.0
        self.recording = False
        self.busy = False; self.set_mode = False; self.drag0 = None; self.marker_until = 0
        self.photo = None; self.disp_geom = None; self._job = None; self._scrub_lock = False
        self.monitors = st.list_monitors(); self.mics = []
        self._build()
        self.hotkey = Hotkey(lambda: self.after(0, self._hotkey))
        threading.Thread(target=self._load_mics, daemon=True).start()

    # ------------------------------------------------------------------ layout
    def _build(self):
        ui = self.ui; B = ui.Btn; P, BG, CARD, CARD2, BORDER = ui.PANEL, ui.BG, ui.CARD, ui.CARD2, ui.BORDER
        F = ui.FONT
        rec = tk.Frame(self, bg=BG); rec.pack(fill="x", padx=16, pady=(12, 6))
        self.b_rec = B(rec, "\u25cf  Start recording", self.toggle_record, fill=ui.RED, fg="white", hover=ui.RED2)
        self.b_rec.pack(side="left")
        self.rec_lbl = tk.Label(rec, text="", bg=BG, fg=ui.RED, font=(F, 11, "bold")); self.rec_lbl.pack(side="left", padx=10)
        tk.Label(rec, text="Screen", bg=BG, fg=ui.MUT, font=(F, 9)).pack(side="left", padx=(6, 4))
        self.src_var = tk.StringVar(value=self.monitors[1][0] if len(self.monitors) > 1 else self.monitors[0][0])
        ttk.Combobox(rec, textvariable=self.src_var, state="readonly", width=30, font=(F, 9),
                     values=[m[0] for m in self.monitors]).pack(side="left")
        tk.Label(rec, text="Mic", bg=BG, fg=ui.MUT, font=(F, 9)).pack(side="left", padx=(12, 4))
        self.mic_var = tk.StringVar(value="No microphone")
        self.mic_cb = ttk.Combobox(rec, textvariable=self.mic_var, state="readonly", width=24, font=(F, 9),
                                   values=["No microphone"])
        self.mic_cb.pack(side="left")
        self.min_var = tk.BooleanVar(value=True)
        tk.Checkbutton(rec, text="Minimise while recording", variable=self.min_var, bg=BG, fg=ui.MUT, selectcolor=CARD2,
                       activebackground=BG, activeforeground=ui.TXT, font=(F, 9), highlightthickness=0, bd=0).pack(side="left", padx=10)
        self.b_down = B(rec, "\u2b07  Download", self.download); self.b_down.pack(side="right")
        self._trash = ui.trash_icon()
        self.b_bin = B(rec, " Bin", self.bin, hover="#5a1f1f", image=self._trash, compound="left")
        self.b_bin.pack(side="right", padx=(0, 8))
        self.b_open = B(rec, "Open video\u2026", self.open_video, fill=BG, fg=ui.MUT, hover=CARD2, font=(F, 9, "bold"))
        self.b_open.pack(side="right", padx=(0, 8))

        ch = tk.Frame(self, bg=BG); ch.pack(fill="x", padx=16, pady=(0, 8))
        tk.Label(ch, text="CHARACTER SQUARE", bg=BG, fg=ui.MUT, font=(F, 8, "bold")).pack(side="left", padx=(0, 8))
        B(ch, "\u2b06  Choose image", self.choose_char, font=(F, 9, "bold"), pady=5).pack(side="left")
        self.b_mouth = B(ch, "\u25ce  Set mouth", self.toggle_set_mouth, font=(F, 9, "bold"), pady=5)
        self.b_mouth.pack(side="left", padx=(8, 0))
        self.b_detect = B(ch, "\u263a  Auto-detect", self.auto_detect, font=(F, 9, "bold"), pady=5)
        self.b_detect.pack(side="left", padx=(8, 0))
        self.chip = tk.Label(ch, text="", bg=BG, fg=ui.MUT, font=(F, 9, "bold")); self.chip.pack(side="left", padx=12)
        self.hk_lbl = tk.Label(ch, text="Ctrl+Shift+R = start/stop recording" if os.name == "nt" else "",
                               bg=BG, fg=ui.DIM, font=(F, 8)); self.hk_lbl.pack(side="right")

        # bottom-up: status, text card, transport; preview fills the rest
        stat = tk.Frame(self, bg=BG); stat.pack(side="bottom", fill="x", padx=16, pady=(0, 6))
        self.status = tk.Label(stat, text="Press \u25cf Start recording to capture your screen, then make your character talk.",
                               bg=BG, fg=ui.MUT, font=(F, 9), anchor="w")
        self.status.pack(side="left", fill="x", expand=True)
        self.prog = ttk.Progressbar(stat, style="red.Horizontal.TProgressbar", length=180, mode="determinate", maximum=1.0)

        card = tk.Frame(self, bg=P, highlightbackground=BORDER, highlightthickness=1)
        card.pack(side="bottom", fill="x", padx=16, pady=(0, 8))
        left = tk.Frame(card, bg=P); left.pack(side="left", fill="both", expand=True)
        tk.Label(left, text="WHAT SHOULD YOUR CHARACTER SAY?", bg=P, fg=ui.MUT, font=(F, 8, "bold"),
                 anchor="w").pack(fill="x", padx=12, pady=(10, 4))
        self.text = tk.Text(left, height=2, bg=CARD, fg=ui.TXT, insertbackground=ui.RED, relief="flat", wrap="word",
                            font=(F, 12), padx=10, pady=6, highlightthickness=1, highlightbackground=BORDER,
                            highlightcolor=ui.RED, selectbackground=ui.RED)
        self.text.pack(fill="x", padx=12)
        self.text.bind("<Control-Return>", lambda e: (self.add_line(), "break")[1])
        row = tk.Frame(left, bg=P); row.pack(fill="x", padx=12, pady=8)
        tk.Label(row, text="Voice", bg=P, fg=ui.MUT, font=(F, 9)).pack(side="left")
        self.voice = tk.StringVar(value="Loading voices\u2026")
        self.cb = ttk.Combobox(row, textvariable=self.voice, state="readonly", width=26, font=(F, 9)); self.cb.pack(side="left", padx=(6, 12))
        tk.Label(row, text="Speed", bg=P, fg=ui.MUT, font=(F, 9)).pack(side="left")
        self.rate = tk.DoubleVar(value=0)
        ttk.Scale(row, from_=-5, to=5, variable=self.rate, length=110).pack(side="left", padx=6)
        self.b_add = B(row, "\uff0b  Add line at 00:00.0", self.add_line, fill=ui.RED, fg="white", hover=ui.RED2)
        self.b_add.pack(side="right")
        self.b_speak = B(row, "\u25b6  Speak", self.speak); self.b_speak.pack(side="right", padx=(0, 8))
        right = tk.Frame(card, bg=P); right.pack(side="right", fill="y", padx=(0, 12), pady=10)
        tk.Label(right, text="SPEECH LINES", bg=P, fg=ui.MUT, font=(F, 8, "bold"), anchor="w").pack(fill="x")
        self.lb = tk.Listbox(right, height=4, width=40, bg=CARD, fg=ui.TXT, selectbackground=ui.RED, relief="flat",
                             highlightthickness=1, highlightbackground=BORDER, font=(F, 9), activestyle="none")
        self.lb.pack(fill="both", expand=True, pady=(4, 4))
        self.lb.bind("<Double-Button-1>", lambda e: self._jump_line())
        lr = tk.Frame(right, bg=P); lr.pack(fill="x")
        B(lr, "\u2716  Delete line", self.delete_line, font=(F, 8, "bold"), pady=3, padx=8).pack(side="right")
        B(lr, "\u21e5  Go to", self._jump_line, font=(F, 8, "bold"), pady=3, padx=8).pack(side="right", padx=(0, 6))

        tr = tk.Frame(self, bg=BG); tr.pack(side="bottom", fill="x", padx=16, pady=(0, 8))
        self.b_play = B(tr, "\u25b6  Play", self.toggle_play, font=(F, 9, "bold"), pady=5); self.b_play.pack(side="left")
        self.time_lbl = tk.Label(tr, text="00:00.0 / 00:00.0", bg=BG, fg=ui.TXT, font=("Consolas", 10)); self.time_lbl.pack(side="right")
        self.scrub = tk.DoubleVar(value=0)
        self.sc = ttk.Scale(tr, from_=0, to=1, variable=self.scrub, command=self._on_scrub)
        self.sc.pack(side="left", fill="x", expand=True, padx=10)

        self.cv = tk.Canvas(self, bg=CARD, highlightthickness=1, highlightbackground=BORDER)
        self.cv.pack(fill="both", expand=True, padx=16, pady=(0, 8))
        self.cv.bind("<Configure>", lambda e: self._schedule_render())
        self.cv.bind("<ButtonPress-1>", self._press)
        self.cv.bind("<B1-Motion>", self._drag)
        self.cv.bind("<ButtonRelease-1>", self._release)
        self.cv.bind("<Button-3>", self._right)
        self._update_chip(); self._update_controls()

    def set_status(self, t, color=None):
        self.status.configure(text=t, fg=color or self.ui.MUT)

    def set_voices(self, v):
        self.voices = v or []
        vals = v if v else ["Default voice"]
        self.cb.configure(values=vals); self.voice.set(vals[0])

    def _voice_name(self):
        v = self.voice.get()
        return v if v in getattr(self, "voices", []) else None

    def _load_mics(self):
        m = st.list_mics()
        self.after(0, lambda: (setattr(self, "mics", m), self.mic_cb.configure(values=["No microphone"] + m)))

    def _update_chip(self):
        ui = self.ui
        if self.sq is None:
            self.chip.configure(text="  No character yet \u2014 choose an image from Downloads  ", fg=ui.MUT, bg=ui.BG)
        elif self.mouth is not None:
            lab = {"face": "face detected", "anime": "anime/cartoon detected", "manual": "mouth set by you"}.get(self.kind, "")
            self.chip.configure(text="  \u25cf %s \u00b7 %s \u00b7 lip sync ON  " % (self.char_name, lab), fg=ui.GREEN, bg=ui.CARD)
        else:
            self.chip.configure(text="  \u25cf %s \u00b7 art \u00b7 no lip sync (use Set mouth)  " % self.char_name, fg=ui.AMBER, bg=ui.CARD)
        self.b_mouth.set_enabled(self.sq is not None); self.b_detect.set_enabled(self.sq is not None)

    def _update_controls(self):
        has = self.rec_path is not None
        self.b_play.set_enabled(has and not self.busy)
        self.b_add.configure(text="\uff0b  Add line at " + fmt_t(self.pos))
        end = self._end()
        self.time_lbl.configure(text="%s / %s" % (fmt_t(self.pos), fmt_t(end)))

    def _end(self):
        return max(self.dur, max([l.t + l.dur for l in self.lines] or [0.0]))

    # ------------------------------------------------------------------ recording
    def _region(self):
        for lab, reg in self.monitors:
            if lab == self.src_var.get():
                return reg
        return None

    def _hotkey(self):
        self.toggle_record(from_hotkey=True)

    def toggle_record(self, from_hotkey=False):
        if self.rec.running():
            self.stop_record()
        elif not self.busy:
            self.start_record()

    def start_record(self):
        self.app.stop()
        self.stop_play()
        path = os.path.join(self.tmp, "rec_%s.mp4" % time.strftime("%Y%m%d_%H%M%S"))
        mic = self.mic_var.get() if self.mic_var.get() in self.mics else None
        region = self._region()
        minimise = self.min_var.get()
        self.set_status("Recording\u2026  Restore this window and press \u25a0 Stop" +
                        (" (or Ctrl+Shift+R)" if self.hotkey.ok else "") + " when you're done.", self.ui.RED2)
        def go():
            try:
                self.rec.start(path, region, mic)
            except Exception as e:
                self._rec_failed(str(e)); return
            self.recording = True
            self.b_rec.configure(text="\u25a0  Stop recording")
            self._rec_tick()
            self.after(1500, self._rec_check)
        if minimise:
            self.app.iconify(); self.after(450, go)
        else:
            go()

    def _rec_check(self):
        if self.rec.p is not None and not self.rec.running():
            err = self.rec.error_text()
            self.rec.p = None
            self._rec_failed(err or "ffmpeg stopped unexpectedly")

    def _rec_failed(self, err):
        self.recording = False
        self.b_rec.configure(text="\u25cf  Start recording"); self.rec_lbl.configure(text="")
        self._restore()
        self.set_status("Recording failed.", self.ui.RED2)
        messagebox.showerror(self.ui.APP, "Screen recording couldn't start:\n\n" + err[-400:] +
                             ("\n\nTip: choose 'No microphone' and try again." if self.mic_var.get() in self.mics else ""),
                             parent=self.app)

    def _rec_tick(self):
        if not self.recording or not self.rec.running():
            return
        el = time.time() - self.rec.t0
        dot = "\u25cf" if int(el * 2) % 2 == 0 else "\u25cb"
        self.rec_lbl.configure(text="%s REC  %02d:%02d" % (dot, int(el // 60), int(el % 60)))
        try:
            self.app.title("%s REC %02d:%02d \u2013 %s" % (dot, int(el // 60), int(el % 60), self.ui.APP))
        except Exception:
            pass
        self.after(250, self._rec_tick)

    def _restore(self):
        try:
            self.app.deiconify(); self.app.lift(); self.app.focus_force()
        except Exception:
            pass
        self.app.title(self.ui.APP)
        if self.app.tab != "studio":
            self.app.show_tab("studio")

    def stop_record(self):
        self.recording = False
        self._busy(True, "Finishing recording\u2026")
        self.b_rec.configure(text="\u25cf  Start recording"); self.rec_lbl.configure(text="")
        self._restore()
        def work():
            p = self.rec.stop()
            err = self.rec.error_text() if not p else ""
            self.after(0, lambda: self._recorded(p, err))
        threading.Thread(target=work, daemon=True).start()

    def _recorded(self, path, err):
        self._busy(False)
        if not path:
            self.set_status("Recording failed.", self.ui.RED2)
            messagebox.showerror(self.ui.APP, "The recording couldn't be saved.\n\n" + err[-400:], parent=self.app); return
        self.load_recording(path)
        self.set_status("Recording ready (%s). Scrub to a moment, type a line and press \uff0b Add line \u2014 then \u2b07 Download."
                        % fmt_t(self.dur), self.ui.GREEN)

    def open_video(self):
        p = filedialog.askopenfilename(parent=self.app, title="Open a screen recording", initialdir=self.ui.downloads_dir(),
                                       filetypes=[("Videos", "*.mp4 *.mkv *.mov *.avi *.webm"), ("All files", "*.*")])
        if p:
            self.load_recording(p)

    def load_recording(self, path):
        try:
            info = st.probe(path)
        except Exception as e:
            messagebox.showerror(self.ui.APP, "Couldn't open that video:\n%s" % e, parent=self.app); return
        self.stop_play()
        if self.cap is not None:
            self.cap.release()
        self.cap = cv2.VideoCapture(path)
        self.rec_path, self.rec_info = path, info
        self.dur = info[2]
        if self.dur <= 0 and self.cap.isOpened():
            n, f = self.cap.get(cv2.CAP_PROP_FRAME_COUNT), self.cap.get(cv2.CAP_PROP_FPS) or st.FPS
            self.dur = n / f if n > 0 else 0
        self.cap_t = -1; self.frame = None
        self.seek(0.0)
        self._update_controls()

    # ------------------------------------------------------------------ playback / preview
    def _read_at(self, t):
        if self.cap is None or not self.cap.isOpened():
            return
        if self.frame is None or t < self.cap_t - 1e-3 or t > self.cap_t + 1.0:
            self.cap.set(cv2.CAP_PROP_POS_MSEC, t * 1000.0)
            ok, f = self.cap.read()
            if ok:
                self.frame = cv2.cvtColor(f, cv2.COLOR_BGR2RGB)
                self.cap_t = (self.cap.get(cv2.CAP_PROP_POS_MSEC) or t * 1000) / 1000.0
            return
        while self.cap_t + 1.0 / st.FPS <= t:
            ok, f = self.cap.read()
            if not ok:
                break
            self.frame = cv2.cvtColor(f, cv2.COLOR_BGR2RGB)
            self.cap_t = (self.cap.get(cv2.CAP_PROP_POS_MSEC) or (self.cap_t + 1.0 / st.FPS) * 1000) / 1000.0

    def seek(self, t):
        self.pos = float(np.clip(t, 0, self._end() or 0))
        self._read_at(min(self.pos, max(0, self.dur - 0.05)))
        self._scrub_lock = True
        self.sc.configure(to=max(0.1, self._end())); self.scrub.set(self.pos)
        self._scrub_lock = False
        self.render()
        self._update_controls()

    def _on_scrub(self, v):
        if self._scrub_lock:
            return
        if self.playing:
            self.stop_play()
        self.seek(float(v))

    def _schedule_render(self):
        if self._job:
            self.after_cancel(self._job)
        self._job = self.after(50, self.render)

    def render(self, op=None, wf=1.0):
        self._job = None
        cv = self.cv
        cw, ch = max(20, cv.winfo_width()), max(20, cv.winfo_height())
        if self.frame is not None:
            fh, fw = self.frame.shape[:2]
        else:
            fw, fh = 1920, 1080
        s = min((cw - 16) / fw, (ch - 16) / fh)
        dw, dh = max(2, int(fw * s)), max(2, int(fh * s))
        ox, oy = (cw - dw) // 2, (ch - dh) // 2
        if self.frame is not None:
            disp = cv2.resize(self.frame, (dw, dh), interpolation=cv2.INTER_AREA)
        else:
            disp = np.empty((dh, dw, 3), np.uint8); disp[:] = (14, 14, 18)
            step = max(16, dw // 16)
            disp[::step, :] = (24, 24, 30); disp[:, ::step] = (24, 24, 30)
        if op is None:
            op, wf = st.mouth_at(self.lines, self.pos) if self.playing else (0.0, 1.0)
        if self.sq is not None:
            st.composite(disp, self.sq, self.mouth, op, wf)
        self.disp_geom = (ox, oy, dw, dh)
        img = Image.fromarray(disp)
        if self.photo is not None and (self.photo.width(), self.photo.height()) == (dw, dh) and cv.find_withtag("frame"):
            self.photo.paste(img)
        else:
            self.photo = ImageTk.PhotoImage(img)
            cv.delete("all")
            cv.create_image(ox, oy, image=self.photo, anchor="nw", tags="frame")
        cv.delete("ui")
        F = self.ui.FONT
        if self.frame is None:
            cv.create_text(ox + dw * 0.42, oy + dh / 2 - 14, text="EX STUDIO", fill=self.ui.TXT, font=(F, 20, "bold"), tags="ui")
            cv.create_text(ox + dw * 0.42, oy + dh / 2 + 18, fill=self.ui.MUT, font=(F, 10), tags="ui",
                           text="Press \u25cf Start recording to capture your screen (or Open video\u2026)")
        if self.sq is None:
            x, y, S = st.overlay_geom(dw, dh)
            cv.create_rectangle(ox + x, oy + y, ox + x + S, oy + y + S, outline=self.ui.RED, dash=(5, 4), width=2, tags="ui")
            cv.create_text(ox + x + S / 2, oy + y + S / 2, text="Character\nsquare\n(click to choose)", fill=self.ui.MUT,
                           font=(F, 8, "bold"), justify="center", tags="ui")
        elif self.mouth is not None and (self.set_mode or time.time() < self.marker_until):
            x, y, S = st.overlay_geom(dw, dh); k = S / float(st.SQ)
            cx, cy, mw, mh = [v * k for v in self.mouth]
            cv.create_oval(ox + x + cx - mw / 2, oy + y + cy - mh / 2, ox + x + cx + mw / 2, oy + y + cy + mh / 2,
                           outline=self.ui.RED, width=2, dash=(4, 3), tags="ui")
        if self.set_mode and self.sq is not None:
            x, y, S = st.overlay_geom(dw, dh)
            cv.create_rectangle(ox + x - 3, oy + y - 3, ox + x + S + 3, oy + y + S + 3, outline=self.ui.GREEN, width=2, tags="ui")

    def toggle_play(self):
        if self.playing:
            self.stop_play()
        elif self.rec_path and not self.busy:
            if self.pos >= self._end() - 0.05:
                self.seek(0)
            self.playing = True
            self.p_t0, self.p_pos0, self.p_prev = time.perf_counter(), self.pos, self.pos - 1e-3
            self.b_play.configure(text="\u275a\u275a  Pause")
            self._play_tick()

    def stop_play(self):
        was = self.playing
        self.playing = False
        self.player.stop()
        self.b_play.configure(text="\u25b6  Play")
        if was:
            self.render()

    def _play_tick(self):
        if not self.playing:
            return
        t = self.p_pos0 + time.perf_counter() - self.p_t0
        end = self._end()
        if t >= end:
            self.pos = end; self.stop_play(); self._update_controls(); return
        for ln in self.lines:  # start each line's voice as the playhead crosses it
            if self.p_prev < ln.t <= t:
                self.player.play(ln.wav)
        self.p_prev = t
        self.pos = t
        self._read_at(min(t, max(0, self.dur - 0.05)))
        op, wf = st.mouth_at(self.lines, t)
        self.render(op, wf)
        self._scrub_lock = True; self.scrub.set(t); self._scrub_lock = False
        self._update_controls()
        self.after(15, self._play_tick)

    # ------------------------------------------------------------------ character
    def choose_char(self):
        p = filedialog.askopenfilename(parent=self.app, title="Choose a character for the square",
                                       initialdir=self.ui.downloads_dir(), filetypes=self.ui.IMG_TYPES)
        if not p:
            return
        try:
            rgb = self.ui.open_image_rgb(p)
        except Exception as e:
            messagebox.showerror(self.ui.APP, "Couldn't open that image:\n%s" % e, parent=self.app); return
        self.src_rgb = rgb; self.char_name = os.path.basename(p)
        self.auto_detect()

    def auto_detect(self):
        if getattr(self, "src_rgb", None) is None:
            return
        self.sq, self.kind, self.mouth = st.make_square(self.src_rgb)
        self.marker_until = time.time() + 2.5
        self.after(2600, self.render)
        self._update_chip(); self.render()
        if self.mouth is not None:
            self.set_status("Character ready \u2014 lip sync ON.", self.ui.GREEN)
        else:
            self.set_status("No face found \u2014 shown as art. Use \u25ce Set mouth and click the mouth in the square.", self.ui.AMBER)

    def toggle_set_mouth(self):
        if self.sq is not None:
            self.set_mouth_mode(not self.set_mode)

    def set_mouth_mode(self, on):
        self.set_mode = bool(on) and self.sq is not None
        ui = self.ui
        self.b_mouth.set_fill(ui.RED if self.set_mode else ui.CARD2, ui.RED2 if self.set_mode else ui.BORDER)
        self.b_mouth.fg0 = "white" if self.set_mode else ui.TXT; self.b_mouth.configure(fg=self.b_mouth.fg0)
        self.cv.configure(cursor="crosshair" if self.set_mode else "arrow")
        if self.set_mode:
            self.set_status("Set mouth: click the mouth inside the character square (or drag a box). Right-click = no lip sync. Esc = done.", ui.RED2)
        self.render()

    def escape(self):
        self.set_mouth_mode(False)

    def _sq_rect(self):
        if not self.disp_geom:
            return None
        ox, oy, dw, dh = self.disp_geom
        x, y, S = st.overlay_geom(dw, dh)
        return ox + x, oy + y, S

    def _to_sq(self, ex, ey):
        r = self._sq_rect()
        x, y, S = r
        return (ex - x) / S * st.SQ, (ey - y) / S * st.SQ

    def _press(self, e):
        r = self._sq_rect()
        inside = r and r[0] <= e.x <= r[0] + r[2] and r[1] <= e.y <= r[1] + r[2]
        if self.sq is None and inside:
            self.choose_char(); return
        if self.set_mode and inside:
            self.drag0 = (e.x, e.y)

    def _drag(self, e):
        if self.set_mode and self.drag0:
            self.cv.delete("drag")
            self.cv.create_oval(self.drag0[0], self.drag0[1], e.x, e.y, outline=self.ui.RED, width=2, tags="drag")

    def _release(self, e):
        if not (self.set_mode and self.drag0):
            return
        self.cv.delete("drag")
        x0, y0 = self.drag0; self.drag0 = None
        if abs(e.x - x0) < 6 and abs(e.y - y0) < 6:
            cx, cy = self._to_sq(e.x, e.y)
            mw = self.mouth[2] if self.mouth is not None else st.SQ * 0.18
            mh = mw * 0.32
        else:
            ax, ay = self._to_sq(min(x0, e.x), min(y0, e.y)); bx, by = self._to_sq(max(x0, e.x), max(y0, e.y))
            cx, cy, mw, mh = (ax + bx) / 2, (ay + by) / 2, max(6.0, bx - ax), max(3.0, by - ay)
        if not (0 <= cx < st.SQ and 0 <= cy < st.SQ):
            return
        self.mouth, self.kind = (cx, cy, mw, mh), "manual"
        self._update_chip()
        self.set_status("Mouth set \u2014 lip sync ON. Esc when done.", self.ui.GREEN)
        self._wiggle()

    def _right(self, e):
        if self.set_mode and self.sq is not None:
            self.mouth, self.kind = None, None
            self._update_chip(); self.render()
            self.set_status("Mouth removed \u2014 the square is shown as art (no lip sync).", self.ui.AMBER)

    def _wiggle(self, i=0):
        if self.playing:
            return
        if i > 24:
            self.render(0.0); return
        self.render(abs(np.sin(i / 24.0 * np.pi * 3)) * 0.8)
        self.after(33, lambda: self._wiggle(i + 1))

    # ------------------------------------------------------------------ speech
    def _text(self):
        return self.text.get("1.0", "end").strip()

    def _synth(self, done):
        text = self._text()
        if not text:
            messagebox.showinfo(self.ui.APP, "Type what your character should say in the box under the screen.", parent=self.app)
            return
        rate = int(round(self.rate.get() * 2)); voice = self.voice.get()
        path = os.path.join(self.tmp, "line_%d.wav" % int(time.time() * 1000))
        self._busy(True, "Generating voice\u2026")
        def work():
            try:
                tts.synthesize(text, path, self._voice_name(), rate)
                ln = st.Line(0.0, text, path, voice, rate)
                self.after(0, lambda: (self._busy(False), done(ln)))
            except Exception as e:
                msg = str(e)
                self.after(0, lambda: (self._busy(False), self.set_status("Voice failed: " + msg, self.ui.RED2),
                                       messagebox.showerror(self.ui.APP, msg, parent=self.app)))
        threading.Thread(target=work, daemon=True).start()

    def speak(self):
        """Preview: say the text now over the current frame (doesn't add a line)."""
        if self.busy:
            return
        self.stop_play()
        def done(ln):
            self.speak_line = ln
            self.player.play(ln.wav)
            self._speak_t0 = time.perf_counter()
            if self.sq is None:
                self.set_status("Speaking\u2026 (choose a character image to see lip sync)", self.ui.MUT)
            self._speak_tick()
        self._synth(done)

    def _speak_tick(self):
        ln = self.speak_line
        if ln is None or self.playing:
            return
        t = time.perf_counter() - self._speak_t0
        if t > ln.dur + 0.1:
            self.speak_line = None; self.render(0.0); return
        op, wf = ln.mouth_at(t)
        self.render(op, wf)
        self.after(15, self._speak_tick)

    def add_line(self):
        if self.busy:
            return
        self.stop_play()
        at = self.pos
        def done(ln):
            ln.t = at
            self.lines.append(ln); self.lines.sort(key=lambda l: l.t)
            self._refresh_lines()
            self.text.delete("1.0", "end")
            self.set_status("Line added at %s (%.1fs). Add more, press \u25b6 Play to preview, or \u2b07 Download." % (fmt_t(at), ln.dur), self.ui.GREEN)
        self._synth(done)

    def _refresh_lines(self):
        self.lb.delete(0, "end")
        for ln in self.lines:
            t = ln.text if len(ln.text) <= 34 else ln.text[:33] + "\u2026"
            self.lb.insert("end", " %s  \u201c%s\u201d  (%.1fs)" % (fmt_t(ln.t), t, ln.dur))
        self.sc.configure(to=max(0.1, self._end()))
        self._update_controls()

    def _sel(self):
        s = self.lb.curselection()
        return s[0] if s else None

    def delete_line(self):
        i = self._sel()
        if i is None:
            if not self.lines:
                return
            messagebox.showinfo(self.ui.APP, "Select a line in the list first.", parent=self.app); return
        ln = self.lines.pop(i)
        try: os.remove(ln.wav)
        except Exception: pass
        self._refresh_lines(); self.render()

    def _jump_line(self):
        i = self._sel()
        if i is not None:
            self.stop_play(); self.seek(self.lines[i].t)

    # ------------------------------------------------------------------ bin / export
    def _busy(self, on, msg=None):
        self.busy = on
        for b in (self.b_down, self.b_bin, self.b_add, self.b_speak, self.b_rec):
            b.set_enabled(not on)
        self._update_controls()
        if msg:
            self.set_status(msg, self.ui.TXT)

    def bin(self):
        if self.busy or self.rec.running():
            return
        self.stop_play(); self.player.stop(); self.set_mouth_mode(False)
        if self.cap is not None:
            self.cap.release(); self.cap = None
        for f in os.listdir(self.tmp):
            try: os.remove(os.path.join(self.tmp, f))
            except Exception: pass
        self.rec_path = self.rec_info = self.frame = None; self.dur = 0.0; self.pos = 0.0
        self.sq = self.mouth = self.kind = None; self.src_rgb = None; self.char_name = ""
        self.lines = []; self.speak_line = None
        self.text.delete("1.0", "end")
        self._refresh_lines(); self._update_chip(); self.seek(0); self.render()
        self.set_status("EX STUDIO cleared.", self.ui.MUT)

    def download(self):
        if self.busy:
            return
        if not self.rec_path:
            messagebox.showinfo(self.ui.APP, "Record your screen first (\u25cf Start recording).", parent=self.app); return
        if self.sq is None:
            if not messagebox.askyesno(self.ui.APP, "No character square chosen. Export the recording without a character?",
                                       parent=self.app):
                return
        if not self.lines and self._text():
            # convenience: typed text but no line yet -> add it at the playhead, then export
            def then(ln):
                ln.t = self.pos; self.lines.append(ln); self._refresh_lines(); self._export()
            self.stop_play(); self._synth(then); return
        self.stop_play()
        self._export()

    def _export(self):
        out = os.path.join(self.ui.downloads_dir(), "EX STUDIO %s.mp4" % time.strftime("%Y-%m-%d %H-%M-%S"))
        self._busy(True, "Rendering EX STUDIO video\u2026")
        self.prog.configure(value=0); self.prog.pack(side="right")
        rec, lines = self.rec_path, list(self.lines)
        sq = self.sq if self.sq is not None else None
        mouth = self.mouth
        def prog(f):
            self.after(0, lambda: self.prog.configure(value=f))
        def work():
            try:
                st.export_final(rec, sq, mouth, lines, out, self.tmp, prog) if sq is not None else \
                    st.export_final(rec, np.zeros((st.SQ, st.SQ, 3), np.uint8), None, lines, out, self.tmp, prog, no_square=True)
                self.after(0, lambda: self._exported(out, None))
            except Exception as e:
                msg = str(e)
                self.after(0, lambda: self._exported(out, msg))
        threading.Thread(target=work, daemon=True).start()

    def _exported(self, out, err):
        self._busy(False); self.prog.pack_forget()
        if err:
            self.set_status("Export failed: " + err, self.ui.RED2)
            messagebox.showerror(self.ui.APP, "Couldn't save the video:\n" + err, parent=self.app); return
        self.set_status("Saved: " + out, self.ui.GREEN)
        if messagebox.askyesno(self.ui.APP, "EX STUDIO video saved to:\n\n%s\n\nShow it in its folder?" % out, parent=self.app):
            if os.name == "nt":
                subprocess.Popen('explorer /select,"%s"' % out)
            else:
                webbrowser.open("file://" + os.path.dirname(out))

    # ------------------------------------------------------------------ lifecycle
    def leave(self):
        self.stop_play(); self.player.stop(); self.speak_line = None; self.set_mouth_mode(False)

    def close(self):
        """Called on app exit. Returns False to cancel closing."""
        if self.rec.running():
            if not messagebox.askyesno(self.ui.APP, "A screen recording is running. Stop it and quit?", parent=self.app):
                return False
            self.rec.stop()
        self.hotkey.close()
        self.player.stop()
        if self.cap is not None:
            self.cap.release()
        shutil.rmtree(self.tmp, ignore_errors=True)
        return True
