"""EX CREATOR STUDIO - offline text-to-speech to WAV.
Windows: SAPI5 (SAPI.SpVoice via comtypes), fallback System.Speech through PowerShell.
Linux/dev: espeak-ng / espeak if present.  (c)2026 EX Inc./HYPERION DIGITAL (c)2026"""
import os, shutil, subprocess, sys, tempfile

NO_WINDOW = 0x08000000 if os.name == "nt" else 0
_last_error = ""


def _sapi(dynamic=False):
    import comtypes.client  # noqa
    import comtypes
    try:
        comtypes.CoInitialize()
    except Exception:
        pass
    return comtypes.client.CreateObject("SAPI.SpVoice", dynamic=dynamic)


def _ps(script, timeout=60):
    exe = shutil.which("powershell") or os.path.join(os.environ.get("SystemRoot", r"C:\Windows"),
                                                    r"System32\WindowsPowerShell\v1.0\powershell.exe")
    r = subprocess.run([exe, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", script],
                       capture_output=True, timeout=timeout, creationflags=NO_WINDOW)
    if r.returncode != 0:
        raise RuntimeError(r.stderr.decode("utf-8", "ignore")[-300:])
    return r.stdout.decode("utf-8", "ignore")


def list_voices():
    """Return a list of installed voice names (may be empty)."""
    if os.name == "nt":
        for dyn in (False, True):
            try:
                v = _sapi(dyn)
                toks = v.GetVoices()
                names = [toks.Item(i).GetDescription() for i in range(toks.Count)]
                if names:
                    return names
            except Exception:
                pass
        try:
            out = _ps("Add-Type -AssemblyName System.Speech; (New-Object System.Speech.Synthesis.SpeechSynthesizer)"
                      ".GetInstalledVoices() | ForEach-Object { $_.VoiceInfo.Name }", 30)
            return [l.strip() for l in out.splitlines() if l.strip()]
        except Exception:
            return []
    for e in ("espeak-ng", "espeak"):
        if shutil.which(e):
            return ["espeak (default)"]
    return []


def _sapi_to_wav(text, wav_path, voice, rate, dynamic):
    from comtypes.client import CreateObject
    sp = _sapi(dynamic)
    if voice:
        toks = sp.GetVoices()
        for i in range(toks.Count):
            if toks.Item(i).GetDescription() == voice:
                sp.Voice = toks.Item(i)
                break
    sp.Rate = rate
    sp.Volume = 100
    fs = CreateObject("SAPI.SpFileStream", dynamic=dynamic)
    try:
        fmt = CreateObject("SAPI.SpAudioFormat", dynamic=dynamic)
        fmt.Type = 22  # SAFT22kHz16BitMono
        fs.Format = fmt
    except Exception:
        pass
    fs.Open(wav_path, 3, False)  # SSFMCreateForWrite
    try:
        sp.AudioOutputStream = fs
        sp.Speak(text, 0)  # synchronous
    finally:
        fs.Close()


def synthesize(text, wav_path, voice=None, rate=0):
    """Speak `text` into a 16-bit PCM WAV at wav_path. rate: -10 (slow) .. 10 (fast). Returns wav_path."""
    global _last_error
    text = (text or "").strip()
    if not text:
        raise ValueError("Type something to say first.")
    rate = int(max(-10, min(10, rate)))
    if os.path.exists(wav_path):
        os.remove(wav_path)
    errs = []
    if os.name == "nt":
        for dyn in (False, True):
            try:
                _sapi_to_wav(text, wav_path, voice, rate, dyn)
                if os.path.exists(wav_path) and os.path.getsize(wav_path) > 100:
                    return wav_path
            except Exception as e:
                errs.append("SAPI%s: %s" % (" (dynamic)" if dyn else "", e))
        try:
            tf = tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8")
            tf.write(text); tf.close()
            q = lambda s: "'" + s.replace("'", "''") + "'"
            sel = ("try { $s.SelectVoice(%s) } catch {}; " % q(voice)) if voice else ""
            _ps("Add-Type -AssemblyName System.Speech; $s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
                + sel + "$s.Rate = %d; $s.SetOutputToWaveFile(%s); $s.Speak([IO.File]::ReadAllText(%s)); $s.Dispose()"
                % (rate, q(wav_path), q(tf.name)), 120)
            os.unlink(tf.name)
            if os.path.exists(wav_path) and os.path.getsize(wav_path) > 100:
                return wav_path
        except Exception as e:
            errs.append("System.Speech: %s" % e)
    else:
        for e in ("espeak-ng", "espeak"):
            if shutil.which(e):
                subprocess.run([e, "-s", str(int(175 + rate * 12)), "-w", wav_path, text], check=True)
                return wav_path
        errs.append("no TTS engine")
    _last_error = "; ".join(errs)
    raise RuntimeError("Text-to-speech failed (" + _last_error + ")")
