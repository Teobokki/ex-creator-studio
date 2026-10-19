# Recolour the EX Inc. master logo (grey disc + white EX) to the red #FF4545 variant; build icon sizes.
from PIL import Image
import numpy as np, sys
src = Image.open(sys.argv[1]).convert("RGBA").resize((1024, 1024), Image.LANCZOS)
a = np.array(src).astype(np.float32)
rgb, al = a[..., :3], a[..., 3:]
grey = np.array([107, 114, 128], np.float32); red = np.array([255, 69, 69], np.float32); white = np.array([255, 255, 255], np.float32)
# t = 0 at disc grey, 1 at white (antialiased edges between)
t = np.clip(((rgb - grey) / (white - grey)).mean(axis=2, keepdims=True), 0, 1)
out = np.concatenate([red + (white - red) * t, al], axis=2).clip(0, 255).astype(np.uint8)
im = Image.fromarray(out, "RGBA")
im.save("assets/logo1024.png"); im.resize((256, 256), Image.LANCZOS).save("assets/logo256.png")
im.resize((64, 64), Image.LANCZOS).save("assets/logo64.png"); im.resize((48, 48), Image.LANCZOS).save("assets/logo48.png")
im.save("assets/logo.ico", sizes=[(16, 16), (20, 20), (24, 24), (32, 32), (40, 40), (48, 48), (64, 64), (128, 128), (256, 256)])
