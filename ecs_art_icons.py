"""EX ART tool icons, drawn with PIL (crisp at any DPI, no font dependence). (c)2026 EX Inc./HYPERION DIGITAL (c)2026"""
import math
from PIL import Image, ImageDraw


def icon(name, size=22, color=(232, 232, 236), k=6):
    if name == "undo":
        return icon("redo", size, color, k).transpose(Image.FLIP_LEFT_RIGHT)
    S = size * k
    im = Image.new("RGBA", (S, S), (0, 0, 0, 0)); d = ImageDraw.Draw(im)
    c = tuple(color) + (255,); lw = max(2, int(S * 0.075)); f = lambda *v: [x * S for x in v]
    if name == "move":
        d.line(f(.5, .1, .5, .9), fill=c, width=lw); d.line(f(.1, .5, .9, .5), fill=c, width=lw)
        for (x, y, dx, dy) in ((.5, .08, 0, 1), (.5, .92, 0, -1), (.08, .5, 1, 0), (.92, .5, -1, 0)):
            a = .16
            d.polygon([(x * S, y * S), ((x + dx * a - dy * a * .7) * S, (y + dy * a - dx * a * .7) * S),
                       ((x + dx * a + dy * a * .7) * S, (y + dy * a + dx * a * .7) * S)], fill=c)
    elif name == "marquee":
        for i in range(8):
            t0, t1 = i / 8, i / 8 + 1 / 16
            for (x0, y0, x1, y1) in ((.12, .18, .88, .18), (.12, .82, .88, .82)):
                d.line([((x0 + (x1 - x0) * t0) * S, y0 * S), ((x0 + (x1 - x0) * t1) * S, y1 * S)], fill=c, width=lw)
            for (x0, y0, y1) in ((.12, .18, .82), (.88, .18, .82)):
                d.line([(x0 * S, (y0 + (y1 - y0) * t0) * S), (x0 * S, (y0 + (y1 - y0) * t1) * S)], fill=c, width=lw)
    elif name == "lasso":
        d.ellipse(f(.12, .14, .88, .62), outline=c, width=lw)
        d.line(f(.32, .58, .28, .78, .40, .90), fill=c, width=lw, joint="curve")
    elif name == "selbrush":
        for i in range(12):
            a0 = i / 12 * 360
            d.arc(f(.1, .1, .9, .9), a0, a0 + 15, fill=c, width=lw)
        d.ellipse(f(.36, .36, .64, .64), fill=c)
    elif name == "crop":
        d.line(f(.26, .06, .26, .74, .94, .74), fill=c, width=lw); d.line(f(.06, .26, .74, .26, .74, .94), fill=c, width=lw)
    elif name == "eyedropper":
        d.line(f(.22, .78, .62, .38), fill=c, width=int(lw * 1.6)); d.line(f(.12, .88, .22, .78), fill=c, width=lw)
        d.ellipse(f(.56, .1, .9, .44), fill=c); d.line(f(.5, .3, .7, .5), fill=c, width=lw)
    elif name == "brush":
        d.line(f(.88, .1, .5, .52), fill=c, width=int(lw * 1.3))
        d.polygon([tuple(p) for p in [f(.44, .46), f(.56, .58), f(.4, .78), f(.12, .9), f(.24, .62)]], fill=c)
    elif name == "pencil":
        d.polygon([tuple(p) for p in [f(.72, .1), f(.9, .28), f(.36, .82), f(.18, .64)]], outline=c, width=lw)
        d.polygon([tuple(p) for p in [f(.18, .64), f(.36, .82), f(.1, .9)]], fill=c)
    elif name == "eraser":
        d.polygon([tuple(p) for p in [f(.56, .12), f(.9, .46), f(.5, .86), f(.16, .52)]], outline=c, width=lw)
        d.polygon([tuple(p) for p in [f(.33, .35), f(.67, .69), f(.5, .86), f(.16, .52)]], fill=c)
        d.line(f(.5, .9, .92, .9), fill=c, width=lw)
    elif name == "fill":
        d.polygon([tuple(p) for p in [f(.42, .14), f(.78, .5), f(.46, .82), f(.10, .46)]], outline=c, width=lw)
        d.polygon([tuple(p) for p in [f(.10, .46), f(.78, .5), f(.46, .82)]], fill=c)
        d.ellipse(f(.76, .58, .92, .80), fill=c); d.polygon([tuple(p) for p in [f(.76, .66), f(.92, .66), f(.84, .5)]], fill=c)
    elif name == "gradient":
        for i in range(20):
            v = int(40 + 200 * i / 19)
            d.rectangle([(.1 + .8 * i / 20) * S, .22 * S, (.1 + .8 * (i + 1) / 20) * S, .78 * S], fill=(v, v, v, 255))
        d.rectangle(f(.1, .22, .9, .78), outline=c, width=lw)
    elif name == "text":
        d.line(f(.16, .2, .84, .2), fill=c, width=int(lw * 1.5)); d.line(f(.5, .2, .5, .86), fill=c, width=int(lw * 1.5))
        d.line(f(.36, .86, .64, .86), fill=c, width=lw)
    elif name == "shape":
        d.rectangle(f(.1, .1, .6, .6), outline=c, width=lw); d.ellipse(f(.38, .38, .92, .92), fill=c)
    elif name == "smudge":
        d.line(f(.12, .78, .3, .6, .5, .66, .7, .42, .88, .22), fill=c, width=int(lw * 1.4), joint="curve")
        d.ellipse(f(.74, .08, .96, .30), fill=c)
    elif name == "blurtool":
        d.polygon([tuple(p) for p in [f(.5, .08), f(.78, .52), f(.22, .52)]], fill=c); d.ellipse(f(.22, .34, .78, .9), fill=c)
    elif name == "clone":
        d.rounded_rectangle(f(.14, .62, .86, .8), radius=S * .04, fill=c); d.rectangle(f(.1, .82, .9, .9), fill=c)
        d.rectangle(f(.42, .3, .58, .62), fill=c); d.ellipse(f(.3, .06, .7, .38), fill=c)
    elif name == "hand":
        for i, x in enumerate((.24, .40, .56, .72)):
            d.rounded_rectangle([(x - .06) * S, (.12 + (.06 if i in (0, 3) else 0)) * S, (x + .06) * S, .6 * S], radius=S * .06, fill=c)
        d.rounded_rectangle(f(.18, .44, .80, .9), radius=S * .14, fill=c)
        d.line(f(.1, .44, .2, .6), fill=c, width=int(lw * 1.6))
    elif name == "zoom":
        d.ellipse(f(.1, .1, .66, .66), outline=c, width=lw); d.line(f(.6, .6, .9, .9), fill=c, width=int(lw * 1.6))
    elif name == "swap":
        d.arc(f(.2, .2, .8, .8), 200, 340, fill=c, width=lw)
        d.polygon([tuple(p) for p in [f(.74, .26), f(.94, .44), f(.70, .48)]], fill=c)
        d.polygon([tuple(p) for p in [f(.26, .26), f(.06, .44), f(.30, .48)]], fill=c)
    elif name == "eye":
        d.chord(f(.08, .26, .92, .74), 180, 360, outline=c, width=lw); d.chord(f(.08, .26, .92, .74), 0, 180, outline=c, width=lw)
        d.ellipse(f(.38, .38, .62, .62), fill=c)
    elif name == "lock":
        d.rounded_rectangle(f(.2, .44, .8, .9), radius=S * .06, fill=c); d.arc(f(.3, .12, .7, .62), 180, 360, fill=c, width=lw)
        d.line(f(.3, .38, .3, .46), fill=c, width=lw); d.line(f(.7, .38, .7, .46), fill=c, width=lw)
    elif name in ("undo", "redo"):
        sx = 1 if name == "redo" else -1; cx = .5
        d.arc(f(.18, .26, .82, .86), 180 if name == "redo" else 0, 360 if name == "redo" else 180, fill=c, width=lw) if False else None
        d.arc(f(.2, .3, .8, .9), 200 if name == "redo" else -20, 360 if name == "redo" else 160, fill=c, width=lw)
        tip = (.8, .6) if name == "redo" else (.2, .6)
        d.polygon([tuple(p) for p in [f(tip[0] - .14 * sx * -1 if False else tip[0], tip[1] - .02)]] + [((tip[0] - .16) * S, .44 * S), ((tip[0] + .16) * S, .44 * S)] if False else
                  [((tip[0]) * S, (tip[1] + .04) * S), ((tip[0] - .17) * S, (tip[1] - .14) * S), ((tip[0] + .17) * S, (tip[1] - .14) * S)], fill=c)
    elif name == "plus":
        d.line(f(.5, .16, .5, .84), fill=c, width=lw); d.line(f(.16, .5, .84, .5), fill=c, width=lw)
    elif name == "dup":
        d.rectangle(f(.1, .1, .62, .62), outline=c, width=lw); d.rectangle(f(.38, .38, .9, .9), fill=c)
    elif name == "mask":
        d.rectangle(f(.1, .16, .9, .84), outline=c, width=lw); d.ellipse(f(.3, .26, .7, .74), fill=c)
    elif name == "merge":
        d.line(f(.5, .1, .5, .6), fill=c, width=lw); d.polygon([tuple(p) for p in [f(.28, .44), f(.72, .44), f(.5, .7)]], fill=c)
        d.line(f(.14, .86, .86, .86), fill=c, width=lw)
    elif name == "flatten":
        for y in (.24, .5, .76): d.line([(.14 * S, y * S), (.86 * S, y * S)], fill=c, width=lw)
    elif name == "trash":
        d.rounded_rectangle(f(.38, .06, .62, .18), radius=S * .04, outline=c, width=lw)
        d.rectangle(f(.12, .16, .88, .26), fill=c); d.polygon([tuple(p) for p in [f(.2, .3), f(.8, .3), f(.73, .94), f(.27, .94)]], fill=c)
    elif name == "download":
        d.line(f(.5, .1, .5, .64), fill=c, width=lw); d.polygon([tuple(p) for p in [f(.26, .46), f(.74, .46), f(.5, .74)]], fill=c)
        d.line(f(.14, .9, .86, .9), fill=c, width=lw)
    elif name == "send":
        d.line(f(.1, .5, .74, .5), fill=c, width=lw); d.polygon([tuple(p) for p in [f(.56, .26), f(.92, .5), f(.56, .74)]], fill=c)
    elif name == "camera":
        d.rounded_rectangle(f(.08, .28, .92, .86), radius=S * .1, outline=c, width=lw)
        d.polygon([tuple(p) for p in [f(.32, .28), f(.4, .14), f(.6, .14), f(.68, .28)]], fill=c)
        d.ellipse(f(.32, .38, .68, .74), outline=c, width=lw)
    elif name == "image":
        d.rounded_rectangle(f(.08, .14, .92, .86), radius=S * .08, outline=c, width=lw)
        d.polygon([tuple(p) for p in [f(.16, .78), f(.4, .46), f(.58, .66), f(.68, .56), f(.84, .78)]], fill=c)
        d.ellipse(f(.62, .26, .76, .4), fill=c)
    elif name == "chev":
        d.polygon([tuple(p) for p in [f(.22, .36), f(.78, .36), f(.5, .7)]], fill=c)
    return im.resize((size, size), Image.LANCZOS)
