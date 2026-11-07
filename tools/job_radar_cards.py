"""Post images for Job Radar items (brand: navy #0B1F3A, yellow #FFD23F, blue #2F80FF, coral #FF5A5F).

  post_images(job, cfg)        -> [feed 1080x1350 PNG, story 1080x1920 PNG]
  carousel(items, cfg, kind)   -> [cover, one slide per item, end slide] (1080x1350 PNGs)
Every text comes from the job card (facts from the posting / headline). Nothing is invented.
Files: reels/job_radar/posts/<date>/
"""
import datetime as dt
import re
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "reels" / "job_radar" / "posts"
F = r"C:\Windows\Fonts"
NAVY, NAVY2, YELLOW, BLUE, CORAL, GREEN, WHITE, MUTED = (11, 31, 58), (19, 41, 75), (255, 210, 63), (47, 128, 255), \
    (255, 90, 95), (34, 197, 94), (255, 255, 255), (170, 186, 214)


BRAND = ROOT / "assets" / "fonts"  # Montserrat (variable) + Poppins, Google Fonts, SIL OFL (licences beside them)
_FONTS = {}


def font(kind, size):
    """Brand fonts (Montserrat 900 for headlines, Poppins for text); Segoe UI if the brand files are missing."""
    key = (kind, size)
    if key not in _FONTS:
        try:
            if kind == "black":
                f = ImageFont.truetype(str(BRAND / "Montserrat-VF.ttf"), size)
                f.set_variation_by_name("Black")
            else:
                f = ImageFont.truetype(str(BRAND / {"bold": "Poppins-Bold.ttf", "reg": "Poppins-Regular.ttf"}[kind]), size)
        except (OSError, ValueError):
            f = ImageFont.truetype(f"{F}\\" + {"black": "seguibl.ttf", "bold": "segoeuib.ttf", "reg": "segoeui.ttf"}[kind], size)
        _FONTS[key] = f
    return _FONTS[key]


def wrap(d, text, fnt, width, max_lines=4):
    words, lines, cur = (text or "").split(), [], ""
    for w in words:
        t = (cur + " " + w).strip()
        if d.textlength(t, font=fnt) <= width:
            cur = t
        else:
            if cur:
                lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    if len(lines) > max_lines:
        lines = lines[:max_lines]
        while lines[-1] and d.textlength(lines[-1] + "…", font=fnt) > width:
            lines[-1] = lines[-1][:-1]
        lines[-1] += "…"
    return lines


def fit_title(d, text, width, sizes=(96, 84, 72, 62, 54), max_lines=3, kind="black"):
    for s in sizes:
        f = font(kind, s)
        lines = wrap(d, text, f, width, 99)
        if len(lines) <= max_lines:
            return f, lines
    f = font(kind, sizes[-1])
    return f, wrap(d, text, f, width, max_lines)


def base(w, h):
    im = Image.new("RGBA", (w, h), NAVY + (255,))
    d = ImageDraw.Draw(im)
    d.polygon([(w * 0.62, 0), (w, 0), (w, h * 0.36), (w * 0.30, h * 0.36 + 0)], fill=NAVY2)  # calm diagonal band
    d.rectangle([0, 0, w, 14], fill=YELLOW)
    return im, d


def pill(d, x, y, text, bg, fg, size=34):
    f = font("black", size)
    tw = d.textlength(text, font=f)
    d.rounded_rectangle([x, y, x + tw + 44, y + size + 30], radius=(size + 30) // 2, fill=bg)
    d.text((x + 22, y + 11), text, font=f, fill=fg)
    return x + tw + 44


def dshort(s):
    try:
        d = dt.datetime.fromisoformat(str(s).replace("Z", "+00:00"))
        return f"{d.day} {d:%b %Y}"
    except Exception:
        return ""


def days_left(s):
    try:
        d = dt.datetime.fromisoformat(str(s).replace("Z", "+00:00"))
        return (d.date() - dt.date.today()).days
    except Exception:
        return None


def headline_badge(j):
    if j.get("govt"):
        st = j.get("student") or ""
        if "Scholarship" in st:
            return "SCHOLARSHIP UPDATE", GREEN
        if "Student benefit" in st:
            return "STUDENT BENEFIT", GREEN
        return {"Govt job": "GOVT JOB ALERT", "Exam date": "EXAM UPDATE", "Scheme": "NEW SCHEME"}.get(j.get("kind"), "UPDATE"), YELLOW
    dl = days_left(j.get("deadline"))
    if j.get("tier") == 1:
        return "NO SHORTLIST · TEST FOR ALL", GREEN
    if dl is not None and 0 <= dl <= 3:
        return "DEADLINE ALERT", CORAL
    return "FRESHER JOB UPDATE", YELLOW


def facts(j):
    rows = []
    if j.get("govt"):
        st = {"Telangana": "Telangana", "Andhra Pradesh": "Andhra Pradesh"}.get(j.get("location"), "All India")
        rows.append(("FOR", f"{st} students"))
        off = j.get("official") or {}
        if off.get("name") and off["name"] != "Find official site":
            rows.append(("OFFICIAL SITE", re.sub(r"^https?://(www\.)?", "", off["url"]).split("/")[0]))
    else:
        if j.get("kind"):
            rows.append(("TYPE", j["kind"] + (" · IT" if j.get("category") == "IT" else "")))
        if j.get("batch"):
            rows.append(("BATCH", ", ".join(j["batch"])))
    for t in (j.get("tests") or [])[:1]:
        if t.get("start"):
            rows.append(("TEST" if not j.get("govt") else "DATE", dshort(t["start"]) + (f"  –  {dshort(t['end'])}" if t.get("end") else "")))
    if j.get("duration"):
        rows.append(("DURATION", j["duration"]))
    if j.get("pay"):
        rows.append(("PAY", j["pay"]))
    if j.get("location") and not j.get("govt"):
        rows.append(("LOCATION", j["location"]))
    return rows[:5]


CUTOUT = ROOT / "assets" / "brand" / "sreekar_cutout.png"
IG_HANDLE, YT_HANDLE = "@sreekar_polepalli", "@sreekarpolepalli"
TAGLINE = "Daily jobs, exams & scholarships for AP & TS students"
RED, WAGREEN = (255, 0, 51), (37, 211, 102)


def ig_icon(size):
    """Instagram-style glyph, drawn (rounded square, gradient, camera outline)."""
    im = Image.new("RGBA", (size, size))
    g = Image.new("RGBA", (size, size))
    gd = ImageDraw.Draw(g)
    for i in range(size):  # diagonal-ish gradient purple -> pink -> orange
        t = i / size
        c = (int(131 + (253 - 131) * t), int(58 + (100 - 58) * t * 0.6), int(180 - 140 * t))
        gd.line([(0, size - i), (size, size - i)], fill=c + (255,))
    m = Image.new("L", (size, size), 0)
    ImageDraw.Draw(m).rounded_rectangle([0, 0, size - 1, size - 1], radius=size // 4, fill=255)
    im.paste(g, (0, 0), m)
    d = ImageDraw.Draw(im)
    s = size
    d.rounded_rectangle([s * .2, s * .2, s * .8, s * .8], radius=s * .17, outline=WHITE, width=max(2, s // 14))
    d.ellipse([s * .36, s * .36, s * .64, s * .64], outline=WHITE, width=max(2, s // 14))
    d.ellipse([s * .66, s * .27, s * .73, s * .34], fill=WHITE)
    return im


def yt_icon(size):
    im = Image.new("RGBA", (size, size))
    d = ImageDraw.Draw(im)
    d.rounded_rectangle([0, size * .16, size - 1, size * .84], radius=size * .2, fill=RED)
    d.polygon([(size * .40, size * .33), (size * .40, size * .67), (size * .70, size * .5)], fill=WHITE)
    return im


def wa_icon(size):
    im = Image.new("RGBA", (size, size))
    d = ImageDraw.Draw(im)
    d.ellipse([0, 0, size - 1, size - 1], fill=WAGREEN)
    d.ellipse([size * .22, size * .2, size * .78, size * .76], outline=WHITE, width=max(2, size // 12))
    d.polygon([(size * .24, size * .66), (size * .19, size * .84), (size * .38, size * .76)], fill=WHITE)
    return im


def cutout():
    try:
        return Image.open(CUTOUT).convert("RGBA")
    except OSError:
        return None


PROFILE = [ROOT / "assets" / "brand" / n for n in ("profile.jpg", "profile.png", "profile.jpeg", "profile.webp")]


def avatar(size):
    """The Instagram profile picture (assets/brand/profile.jpg); an 'S' monogram until it is added."""
    src = next((p for p in PROFILE if p.exists()), None)
    if src:
        pic = Image.open(src).convert("RGBA")
        sq = min(pic.size)
        pic = pic.crop(((pic.width - sq) // 2, (pic.height - sq) // 2, (pic.width + sq) // 2, (pic.height + sq) // 2))
        bg = pic.resize((size, size), Image.LANCZOS)
    else:
        bg = Image.new("RGBA", (size, size), NAVY2 + (255,))
        bd = ImageDraw.Draw(bg)
        f = font("black", int(size * .55))
        bd.text(((size - bd.textlength("S", font=f)) / 2, size * .12), "S", font=f, fill=YELLOW)
    m = Image.new("L", (size, size), 0)
    ImageDraw.Draw(m).ellipse([0, 0, size - 1, size - 1], fill=255)
    out = Image.new("RGBA", (size + 8, size + 8), (0, 0, 0, 0))
    ImageDraw.Draw(out).ellipse([0, 0, size + 7, size + 7], fill=YELLOW)
    out.paste(bg, (4, 4), m)
    return out


def brand_bar(im, w, y, size=30, center=True):
    """[photo] @sreekar_polepalli [IG]  [YT] @sreekarpolepalli  [WA] Channel: link in bio"""
    d = ImageDraw.Draw(im)
    texts = [IG_HANDLE, YT_HANDLE, "link in bio"]

    def width(sz):
        return int(sz * 2.6) + 8 + 18 + sum(int(sz * 1.45) + 8 + d.textlength(t, font=font("bold", sz)) + 26 for t in texts) - 26
    while size > 18 and width(size) > w - 70:  # always fits the slide
        size -= 1
    f = font("bold", size)
    ic = int(size * 1.45)
    parts = [(ig_icon(ic), texts[0]), (yt_icon(ic), texts[1]), (wa_icon(ic), texts[2])]
    av = avatar(int(size * 2.6))
    total = (av.width + 18 if av else 0) + sum(ic + 8 + d.textlength(t, font=f) + 26 for _, t in parts) - 26
    x = (w - total) / 2 if center else 60
    if av:
        im.alpha_composite(av, (int(x), int(y - (av.height - ic) / 2)))
        x += av.width + 18
    for icon, t in parts:
        im.alpha_composite(icon, (int(x), int(y)))
        x += ic + 8
        d.text((x, y + (ic - size * 1.35) / 2), t, font=f, fill=WHITE)
        x += d.textlength(t, font=f) + 26


def watermark(im):
    """Faint handle so reposts still credit the page."""
    layer = Image.new("RGBA", im.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    f = font("black", 46)
    t = IG_HANDLE
    tl = d.textlength(t, font=f)
    tile = Image.new("RGBA", (int(tl) + 20, 70), (0, 0, 0, 0))
    ImageDraw.Draw(tile).text((10, 5), t, font=f, fill=(255, 255, 255, 16))
    tile = tile.rotate(28, expand=True)
    layer.alpha_composite(tile, (im.width - tile.width - 30, int(im.height * 0.20)))
    im.alpha_composite(layer)


def footer(d, w, h, cfg, story=False, im=None):
    y = h - (400 if story else 262)
    d.rounded_rectangle([60, y, w - 60, y + 100], radius=26, fill=BLUE)
    t = "APPLY LINK → WhatsApp channel (link in bio)"
    size = 40
    while size > 26 and d.textlength(t, font=font("black", size)) > w - 180:
        size -= 2
    f = font("black", size)
    d.text(((w - d.textlength(t, font=f)) / 2, y + 50 - size * 0.62), t, font=f, fill=WHITE)
    if im is not None:
        brand_bar(im, w, y + 128, size=27)
    f2 = font("reg", 24)
    t2 = TAGLINE + "  ·  verify on official sites"
    d.text(((w - d.textlength(t2, font=f2)) / 2, y + 212), t2, font=f2, fill=MUTED)


def _old_footer(d, w, h, cfg, story=False):
    y = h - (330 if story else 190)
    d.rounded_rectangle([60, y, w - 60, y + 120], radius=26, fill=BLUE)
    t = "APPLY LINK → WhatsApp channel (link in bio)"
    size = 40
    while size > 26 and d.textlength(t, font=font("black", size)) > w - 180:
        size -= 2
    f = font("black", size)
    d.text(((w - d.textlength(t, font=f)) / 2, y + 60 - size * 0.62), t, font=f, fill=WHITE)
    h2 = (cfg.get("instagram") or "@sreekar_polepalli") + "   ·   Verify on the official site before applying"
    s2 = 32
    while s2 > 22 and d.textlength(h2, font=font("bold", s2)) > w - 120:
        s2 -= 2
    f2 = font("bold", s2)
    d.text(((w - d.textlength(h2, font=f2)) / 2, y + 140), h2, font=f2, fill=MUTED)


def draw_post(j, cfg, w, h, story=False):
    im, d = base(w, h)
    top = 250 if story else 90
    label, col = headline_badge(j)
    pill(d, 70, top, label, col, NAVY)
    title = j.get("company") if not j.get("govt") else j.get("title")
    sub = j.get("title") if not j.get("govt") else ""
    f, lines = fit_title(d, title or "", w - 140, sizes=(96, 84, 72, 62) if not j.get("govt") else (72, 64, 56, 50),
                         max_lines=3 if not j.get("govt") else 5)
    y = top + 110
    for ln in lines:
        d.text((70, y), ln, font=f, fill=YELLOW if not j.get("govt") else WHITE)
        y += int(f.size * 1.12)
    if sub:
        fs = font("bold", 50)
        for ln in wrap(d, sub, fs, w - 140, 2):
            d.text((70, y + 6), ln, font=fs, fill=WHITE)
            y += 62
    y += 40
    fl, fv = font("bold", 26), font("bold", 40)
    for lab, val in facts(j)[:5 if story else 4]:
        d.rounded_rectangle([70, y, w - 70, y + 96], radius=20, fill=NAVY2)
        d.rectangle([70, y, 82, y + 96], fill=YELLOW)
        d.text((108, y + 10), lab, font=fl, fill=MUTED)
        d.text((108, y + 42), wrap(d, val, fv, w - 220, 1)[0], font=fv, fill=WHITE)
        y += 112
    dl = days_left(j.get("deadline"))
    if j.get("deadline") and dl is not None and dl >= 0:
        y += 10
        box = CORAL if dl <= 7 else GREEN
        d.rounded_rectangle([70, y, w - 70, y + 130], radius=26, fill=box)
        fb = font("black", 54)
        t = f"LAST DATE: {dshort(j['deadline'])}"
        d.text((105, y + 12), t, font=fb, fill=WHITE)
        t2 = "TODAY!" if dl == 0 else f"Only {dl} day{'s' if dl > 1 else ''} left"
        d.text((105, y + 78), t2, font=font("bold", 36), fill=WHITE)
    footer(d, w, h, cfg, story, im)
    watermark(im)
    return im


def slug(j):
    return re.sub(r"[^a-z0-9]+", "-", f"{j.get('company', '')} {j.get('title', '')}".lower()).strip("-")[:48]


def post_images(j, cfg):
    out = OUT / f"{dt.date.today():%Y-%m-%d}"
    out.mkdir(parents=True, exist_ok=True)
    paths = []
    for name, (w, h, story) in {"feed": (1080, 1350, False), "story": (1080, 1920, True)}.items():
        p = out / f"{slug(j)}_{name}.png"
        draw_post(j, cfg, w, h, story).convert("RGB").save(p, optimize=True)
        paths.append(p)
    return paths


def carousel(items, cfg, kind):
    out = OUT / f"{dt.date.today():%Y-%m-%d}" / f"carousel_{kind}"
    out.mkdir(parents=True, exist_ok=True)
    for old in out.glob("*.png"):
        old.unlink()
    w, h, paths = 1080, 1350, []
    cut = None  # user 2026-11-07: no big photo in carousels (profile picture in the brand bar only)
    # cover: your photo on the right, headline on the left
    im, d = base(w, h)
    if cut:
        ph = cut.resize((int(cut.width * 860 / cut.height), 860))
        im.alpha_composite(ph, (w - ph.width + 170, h - ph.height - 200))
    pill(d, 70, 100, f"{dt.date.today():%d %b %Y}", YELLOW, NAVY)
    head = "THIS WEEK FOR AP & TS STUDENTS" if kind == "student" else "TOP FRESHER JOBS THIS WEEK"
    f, lines = fit_title(d, head, 620 if cut else w - 140, sizes=(120, 104, 92, 82, 72), max_lines=5)
    y = 230
    for ln in lines:
        d.text((70, y), ln, font=f, fill=YELLOW)
        y += int(f.size * 1.1)
    sub = ["Scholarships", "Last dates", "Exams", "Govt jobs"] if kind == "student" else \
        ["Guaranteed tests", "Trusted companies", "Deadlines"]
    for s_ in sub:
        d.text((70, y + 26), "✓ " + s_ if False else "• " + s_, font=font("bold", 40), fill=WHITE)
        y += 56
    d.rounded_rectangle([60, h - 200, w - 60, h - 110], radius=24, fill=BLUE)
    t = f"{len(items)} updates  ·  SWIPE  →"
    fb = font("black", 46)
    d.text(((w - d.textlength(t, font=fb)) / 2, h - 182), t, font=fb, fill=WHITE)
    d.text((70, h - 92), "by Sreekar  ·  SE III Consultant", font=font("bold", 30), fill=WHITE)
    d.text((70, h - 52), TAGLINE, font=font("reg", 26), fill=MUTED)
    paths.append(out / "01_cover.png")
    im.convert("RGB").save(paths[-1], optimize=True)
    for i, j in enumerate(items, 2):
        im = draw_post(j, cfg, w, h)
        d = ImageDraw.Draw(im)
        d.text((w - 150, 92), f"{i - 1}/{len(items)}", font=font("black", 40), fill=MUTED)
        paths.append(out / f"{i:02d}_{slug(j)[:30]}.png")
        im.convert("RGB").save(paths[-1], optimize=True)
    # end slide: your photo + where to follow
    im, d = base(w, h)
    if cut:
        ph = cut.resize((int(cut.width * 700 / cut.height), 700))
        im.alpha_composite(ph, (w - ph.width + 120, h - ph.height))
    f = font("black", 92)
    d.text((70, 100), "SAVE IT.", font=f, fill=YELLOW)
    d.text((70, 205), "SHARE IT.", font=f, fill=YELLOW)
    for k, ln in enumerate(wrap(d, TAGLINE, font("bold", 40), w - 140, 2)):
        d.text((70, 330 + k * 50), ln, font=font("bold", 40), fill=WHITE)
    rows = [(ig_icon(78), "Instagram", IG_HANDLE), (yt_icon(78), "YouTube", YT_HANDLE),
            (wa_icon(78), "WhatsApp channel", "link in bio")]
    y = 480
    for icon, plat, handle in rows:
        d.rounded_rectangle([60, y - 14, w - 60, y + 92], radius=22, fill=NAVY2)
        im.alpha_composite(icon, (82, y))
        d.text((182, y - 2), plat, font=font("bold", 30), fill=MUTED)
        d.text((182, y + 30), handle, font=font("black", 42), fill=WHITE)
        y += 128
    d.rounded_rectangle([60, h - 170, w - 60, h - 80], radius=24, fill=YELLOW)
    t = "FOLLOW FOR MORE"
    fb = font("black", 50)
    d.text(((w - d.textlength(t, font=fb)) / 2, h - 158), t, font=fb, fill=NAVY)
    paths.append(out / f"{len(items) + 2:02d}_follow.png")
    im.convert("RGB").save(paths[-1], optimize=True)
    return paths
