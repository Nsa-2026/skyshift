"""Arrays -> PNG/GIF with a shared stretch so that blinking is meaningful."""
import io
import numpy as np
from PIL import Image


def stretch_limits(cube, lo=1.0, hi=99.7):
    v = cube[np.isfinite(cube)]
    if v.size == 0:
        return 0.0, 1.0
    return float(np.percentile(v, lo)), float(np.percentile(v, hi))


def to_u8(img, lims, mode="asinh", diverging=False):
    a, b = lims
    if diverging:
        m = max(abs(a), abs(b)) or 1
        x = np.clip(img / m, -1, 1)
        x = 0.5 + 0.5 * np.arcsinh(5 * x) / np.arcsinh(5)
    else:
        x = np.clip((img - a) / ((b - a) or 1), 0, 1)
        if mode == "asinh":
            x = np.arcsinh(10 * x) / np.arcsinh(10)
    x = np.nan_to_num(x, nan=0.0)
    return (x * 255).astype(np.uint8)


def colorize(u8, diverging=False, nanmask=None):
    if diverging:
        t = u8.astype(float) / 255
        r = np.clip(2 * t, 0, 1); bch = np.clip(2 - 2 * t, 0, 1); g = 1 - np.abs(2 * t - 1)
        rgb = np.stack([r, np.minimum(r, bch) * 0 + g * 0.9 + 0.1 * np.minimum(r, bch), bch], -1)
        rgb = np.stack([np.where(t > .5, 1, 2 * t), np.where(t > .5, 2 - 2 * t, 2 * t), np.where(t < .5, 1, 2 - 2 * t)], -1)
        rgb = (rgb * 255).astype(np.uint8)
    else:
        rgb = np.repeat(u8[..., None], 3, -1)
    if nanmask is not None:
        rgb[nanmask] = (60, 20, 40)
    return rgb


def mark_target(im, scale, color=(255, 209, 102)):
    """Two ticks pointing at the field centre (the requested position)."""
    from PIL import ImageDraw
    d = ImageDraw.Draw(im)
    W, H = im.size
    cx, cy = W / 2, H / 2
    g, L = 3 * scale, 4 * scale
    d.line([(cx - g - L, cy), (cx - g, cy)], fill=color, width=max(1, scale // 2))
    d.line([(cx, cy - g - L), (cx, cy - g)], fill=color, width=max(1, scale // 2))
    return im


def png(img, lims, diverging=False, scale=4, flip=True, mark=True):
    u8 = to_u8(img, lims, diverging=diverging)
    rgb = colorize(u8, diverging, ~np.isfinite(img))
    if flip:
        rgb = rgb[::-1]          # FITS y up -> screen; north up, east left
    im = Image.fromarray(rgb).resize((rgb.shape[1] * scale, rgb.shape[0] * scale), Image.NEAREST)
    if mark:
        mark_target(im, scale)
    b = io.BytesIO(); im.save(b, "PNG")
    return b.getvalue()


def gif(imgs, lims, diverging=False, scale=4, ms=250, labels=None):
    from PIL import ImageDraw
    frames = []
    for i, img in enumerate(imgs):
        rgb = colorize(to_u8(img, lims, diverging=diverging), diverging, ~np.isfinite(img))[::-1]
        im = Image.fromarray(rgb).resize((rgb.shape[1] * scale, rgb.shape[0] * scale), Image.NEAREST)
        mark_target(im, scale)
        if labels:
            ImageDraw.Draw(im).text((4, 4), labels[i], fill=(255, 220, 0))
        frames.append(im)
    b = io.BytesIO()
    frames[0].save(b, "GIF", save_all=True, append_images=frames[1:], duration=ms, loop=0)
    return b.getvalue()
