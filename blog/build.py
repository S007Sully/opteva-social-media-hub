#!/usr/bin/env python3
"""Build the opteva.ai blog from blog/posts/*.md.

Usage:  python3 blog/build.py          (run from the repo root or from blog/)

Writes blog/<slug>.html, blog/index.html, blog/og/<slug>.png, blog/feed.xml,
and rewrites the root sitemap.xml. Needs only Python 3 and Pillow.

Post front matter (first block, between --- lines):
  title:       Required. Under 60 characters.
  description: Required. Under 155 characters. Used for meta + lede.
  date:        YYYY-MM-DD publish date.
  updated:     YYYY-MM-DD, optional.
  pillar:      operators | agencies | decisions
  cta:         operator | agency   (which wording the offer block uses)
  draft:       true  -> rendered with noindex, left out of hub/sitemap/feed.

Body is markdown. A "## FAQ" section whose questions are "### " headings
is also emitted as FAQPage schema.
"""
import re, html, json, datetime, pathlib, sys
from xml.sax.saxutils import escape as xesc

ROOT = pathlib.Path(__file__).resolve().parent.parent
BLOG = ROOT / "blog"
POSTS = BLOG / "posts"
OG = BLOG / "og"
SITE = "https://opteva.ai"
AUTHOR_ID = "https://steph.opteva.ai/#person"
ORG_ID = SITE + "/#organization"

PILLARS = {
    "operators": ("For operators", "Aesthetics operators"),
    "agencies": ("For agencies", "Agency owners"),
    "decisions": ("Deciding", "Decision pages"),
}
CTA = {
    "operator": (
        "Send me your spa's Instagram handle.",
        "I will write five posts in your business's own voice and send them back within 48 hours. "
        "Yours to keep either way.",
    ),
    "agency": (
        "Send one client's Instagram handle.",
        "Fifteen minutes. I generate content in that client's voice while you watch, and you say "
        "whether it sounds like them.",
    ),
}

# ---------------------------------------------------------------- markdown
def inline(t):
    t = html.escape(t, quote=False)
    t = re.sub(r"`([^`]+)`", r"<code>\1</code>", t)
    t = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", t)
    t = re.sub(r"(?<![\w*])\*(?!\s)(.+?)(?<!\s)\*(?!\w)", r"<em>\1</em>", t)
    t = re.sub(r"\[([^\]]+)\]\(([^)\s]+)\)", r'<a href="\2">\1</a>', t)
    return t

def slugify(t):
    return re.sub(r"[^a-z0-9]+", "-", t.lower()).strip("-")

def md_to_html(src):
    """Small markdown subset: h2/h3, p, ul/ol, blockquote, hr, tables, raw html."""
    out, toc, faq = [], [], []
    lines = src.splitlines()
    i, n = 0, len(lines)
    in_faq, cur_q = False, None
    def close_q():
        nonlocal cur_q
        cur_q = None
    while i < n:
        ln = lines[i]
        if not ln.strip():
            i += 1; continue
        if ln.startswith("## "):
            text = ln[3:].strip(); hid = slugify(text)
            out.append(f'<h2 id="{hid}">{inline(text)}</h2>'); toc.append((hid, text))
            in_faq = text.strip().lower() == "faq"; close_q(); i += 1; continue
        if ln.startswith("### "):
            text = ln[4:].strip(); out.append(f"<h3>{inline(text)}</h3>")
            if in_faq: cur_q = {"q": text, "a": []}; faq.append(cur_q)
            i += 1; continue
        if re.match(r"^-{3,}$", ln.strip()):
            out.append("<hr/>"); i += 1; continue
        if ln.lstrip().startswith("<"):
            block = []
            while i < n and lines[i].strip(): block.append(lines[i]); i += 1
            out.append("\n".join(block)); continue
        if ln.startswith("> "):
            block = []
            while i < n and lines[i].startswith("> "): block.append(lines[i][2:]); i += 1
            out.append("<blockquote><p>" + inline(" ".join(block)) + "</p></blockquote>"); continue
        if ln.startswith("|"):
            rows = []
            while i < n and lines[i].startswith("|"): rows.append(lines[i]); i += 1
            cells = [[c.strip() for c in r.strip().strip("|").split("|")] for r in rows]
            if len(cells) > 1 and all(re.match(r"^:?-{2,}:?$", c) for c in cells[1]): head, body = cells[0], cells[2:]
            else: head, body = None, cells
            t = ['<div class="tbl"><table>']
            if head: t.append("<thead><tr>" + "".join(f"<th>{inline(c)}</th>" for c in head) + "</tr></thead>")
            t.append("<tbody>" + "".join("<tr>" + "".join(f"<td>{inline(c)}</td>" for c in r) + "</tr>" for r in body) + "</tbody></table></div>")
            out.append("".join(t)); continue
        m = re.match(r"^(\s*)([-*]|\d+\.)\s+", ln)
        if m:
            tag = "ol" if m.group(2)[0].isdigit() else "ul"; items = []
            while i < n and re.match(r"^\s*([-*]|\d+\.)\s+", lines[i]):
                item = re.sub(r"^\s*([-*]|\d+\.)\s+", "", lines[i]); i += 1
                while i < n and lines[i].startswith("  ") and not re.match(r"^\s*([-*]|\d+\.)\s+", lines[i]):
                    item += " " + lines[i].strip(); i += 1
                items.append(f"<li>{inline(item)}</li>")
            out.append(f"<{tag}>" + "".join(items) + f"</{tag}>"); continue
        block = []
        while i < n and lines[i].strip() and not re.match(r"^(#{2,3} |> |\||[-*] |\d+\. |<|-{3,}$)", lines[i]):
            block.append(lines[i].strip()); i += 1
        if not block: block = [ln.strip()]; i += 1
        p = inline(" ".join(block)); out.append(f"<p>{p}</p>")
        if cur_q is not None: cur_q["a"].append(" ".join(block))
    return "\n".join(out), toc, [(f["q"], " ".join(f["a"])) for f in faq if f["a"]]

# ---------------------------------------------------------------- front matter
def parse_post(path):
    raw = path.read_text(encoding="utf-8")
    m = re.match(r"^---\s*\n(.*?)\n---\s*\n(.*)$", raw, re.S)
    if not m: sys.exit(f"{path.name}: missing front matter")
    meta = {}
    for ln in m.group(1).splitlines():
        if ":" in ln:
            k, v = ln.split(":", 1); meta[k.strip()] = v.strip().strip('"')
    for k in ("title", "description", "date", "pillar"):
        if k not in meta: sys.exit(f"{path.name}: front matter needs {k}")
    if meta["pillar"] not in PILLARS: sys.exit(f"{path.name}: pillar must be one of {list(PILLARS)}")
    if len(meta["title"]) > 60: print(f"  warning: {path.name} title is {len(meta['title'])} chars (>60)")
    if len(meta["description"]) > 155: print(f"  warning: {path.name} description is {len(meta['description'])} chars (>155)")
    body_html, toc, faq = md_to_html(m.group(2))
    words = len(re.sub(r"<[^>]+>", " ", body_html).split())
    return dict(meta, slug=path.stem, body=body_html, toc=toc, faq=faq, words=words,
                minutes=max(1, round(words / 220)), draft=meta.get("draft", "false").lower() == "true",
                cta=meta.get("cta", "agency" if meta["pillar"] != "operators" else "operator"),
                updated=meta.get("updated", meta["date"]))

def nice_date(iso):
    return datetime.date.fromisoformat(iso).strftime("%B %-d, %Y")

# ---------------------------------------------------------------- shared chrome
def tokens():
    idx = (ROOT / "index.html").read_text(encoding="utf-8")
    m = re.search(r":root\{.*?\n\}", idx, re.S)
    return m.group(0) if m else ":root{--navy:#1B3A5C;--teal:#3DBCB2;--teal-dark:#2FA89F;--bg:#fff;--bg-off:#f5f7fa;--text:#1a2a3a;--text-secondary:#566a7f;--text-muted:#8fa0b3;--border:#dfe6ed;--border-light:#eaf0f5;--radius:12px;--shadow-teal:0 4px 24px rgba(78,205,196,0.2);}"

CSS = """
*{box-sizing:border-box;margin:0;padding:0;}
html{scroll-behavior:smooth;}
body{font-family:'Inter',system-ui,-apple-system,sans-serif;background:var(--bg);color:var(--text);-webkit-font-smoothing:antialiased;overflow-x:hidden;}
a{color:var(--teal-dark);}
.container{max-width:1100px;margin:0 auto;padding:0 24px;}
.btn{display:inline-flex;align-items:center;justify-content:center;gap:10px;padding:17px 38px;border-radius:var(--radius);font-size:17px;font-weight:800;font-family:'Inter',sans-serif;cursor:pointer;border:none;transition:all .25s;text-decoration:none;white-space:nowrap;}
.btn-primary{background:var(--teal);color:#fff;box-shadow:var(--shadow-teal);}
.btn-primary:hover{background:var(--teal-light);transform:translateY(-2px);}
.btn-primary .arrow{transition:transform .25s;}.btn-primary:hover .arrow{transform:translateX(4px);}
.btn-sm{padding:11px 22px;font-size:15px;}
nav{position:fixed;top:0;left:0;right:0;z-index:1000;background:rgba(255,255,255,0.92);backdrop-filter:blur(20px);border-bottom:1px solid var(--border-light);transition:all .3s;}
nav.scrolled{background:rgba(255,255,255,0.97);box-shadow:0 2px 16px rgba(0,0,0,0.06);}
.nav-inner{max-width:1160px;margin:0 auto;padding:0 24px;display:flex;align-items:center;justify-content:space-between;height:72px;gap:16px;}
.nav-logo{display:flex;align-items:center;text-decoration:none;flex-shrink:0;}
.nav-logo img{height:40px;width:auto;}
.nav-links{display:flex;align-items:center;gap:28px;}
.nav-links a:not(.btn){font-size:14px;font-weight:500;color:var(--text-secondary);text-decoration:none;transition:color .2s;display:inline-block;padding:5px 2px;}
.nav-links a:not(.btn):hover,.nav-links a.active{color:var(--teal-dark);}
@media(max-width:768px){.nav-links a:not(.btn){display:none;}.nav-logo img{height:34px;}.nav-inner{height:64px;}}
.section-label{display:inline-flex;align-items:center;gap:8px;font-size:13px;font-weight:700;color:var(--teal-dark);text-transform:uppercase;letter-spacing:2px;margin-bottom:16px;}
.section-label::before{content:'';width:24px;height:2px;background:var(--teal);border-radius:2px;}
footer{background:var(--bg-off);border-top:1px solid var(--border-light);padding:44px 0 22px;}
.footer-inner{display:flex;justify-content:space-between;align-items:flex-start;flex-wrap:wrap;gap:20px;}
.footer-logo img{height:30px;width:auto;}
.footer-contact{margin-top:14px;font-size:13px;color:var(--text-secondary);line-height:2.2;}
.footer-contact a{color:var(--text-secondary);text-decoration:none;display:inline-block;padding:5px 2px;}
.footer-contact a:hover{color:var(--teal-dark);}
.footer-links{display:flex;gap:24px;flex-wrap:wrap;}
.footer-links a{font-size:13px;color:var(--text-muted);text-decoration:none;transition:color .2s;display:inline-block;padding:5px 2px;}
.footer-links a:hover{color:var(--teal-dark);}
.footer-copy{width:100%;text-align:center;margin-top:24px;padding-top:22px;border-top:1px solid var(--border-light);font-size:12px;color:var(--text-muted);}

/* ---- blog ---- */
.post-head{padding:136px 0 36px;}
.post-head .meta{display:flex;flex-wrap:wrap;gap:6px 14px;font-size:13px;color:var(--text-muted);margin-top:6px;}
.post-head .meta .pillar{color:var(--teal-dark);font-weight:600;}
.post-head h1{font-family:'Playfair Display',serif;font-size:clamp(32px,4.6vw,52px);font-weight:700;line-height:1.1;letter-spacing:-0.02em;color:var(--navy);margin:14px 0 18px;text-wrap:balance;}
.lede{font-size:19px;line-height:1.6;color:var(--text-secondary);max-width:40em;}
.byline{display:flex;align-items:center;gap:14px;margin-top:28px;padding-top:24px;border-top:1px solid var(--border-light);}
.byline img{width:48px;height:48px;border-radius:50%;object-fit:cover;flex-shrink:0;}
.byline .who{font-size:14px;line-height:1.45;}
.byline .who b{color:var(--navy);font-weight:700;}
.byline .who span{display:block;color:var(--text-muted);font-size:13px;}
.byline .who a{color:inherit;text-decoration:none;}
.byline .who a:hover{color:var(--teal-dark);}
.narrow{max-width:680px;margin:0 auto;}
.toc{background:var(--bg-off);border:1px solid var(--border-light);border-radius:var(--radius);padding:18px 22px;margin:0 0 36px;font-size:14px;}
.toc b{display:block;font-size:12px;letter-spacing:1.5px;text-transform:uppercase;color:var(--text-muted);margin-bottom:8px;}
.toc ol{padding-left:18px;}.toc li{margin:4px 0;}.toc a{color:var(--text-secondary);text-decoration:none;}.toc a:hover{color:var(--teal-dark);}
.post-body{font-size:17px;line-height:1.8;color:var(--text);}
.post-body>*+*{margin-top:1.1em;}
.post-body h2{font-family:'Playfair Display',serif;font-size:clamp(24px,3vw,32px);font-weight:700;line-height:1.2;letter-spacing:-0.01em;color:var(--navy);margin-top:2em;scroll-margin-top:96px;}
.post-body h3{font-size:19px;font-weight:700;color:var(--navy);margin-top:1.6em;}
.post-body ul,.post-body ol{padding-left:1.4em;}
.post-body li{margin:.35em 0;}
.post-body li::marker{color:var(--teal-dark);}
.post-body blockquote{border-left:3px solid var(--teal);padding:4px 0 4px 20px;color:var(--text-secondary);font-family:'Playfair Display',serif;font-style:italic;font-size:1.1em;}
.post-body hr{border:none;border-top:1px solid var(--border);margin:2.4em auto;width:80px;}
.post-body code{font-family:ui-monospace,Menlo,monospace;font-size:.88em;background:var(--bg-off);padding:1px 6px;border-radius:4px;}
.post-body img{max-width:100%;height:auto;border-radius:var(--radius);}
.post-body .tbl{overflow-x:auto;border:1px solid var(--border);border-radius:var(--radius);}
.post-body table{border-collapse:collapse;width:100%;min-width:520px;font-size:15px;}
.post-body th,.post-body td{text-align:left;padding:10px 14px;border-bottom:1px solid var(--border-light);vertical-align:top;}
.post-body th{font-size:12px;letter-spacing:1px;text-transform:uppercase;color:var(--text-muted);background:var(--bg-off);}
.post-body tr:last-child td{border-bottom:none;}
.post-body .note{background:var(--bg-off);border-left:3px solid var(--teal);padding:14px 18px;border-radius:0 var(--radius) var(--radius) 0;font-size:15.5px;}
.offer{margin:56px 0 0;background:var(--navy);color:#fff;border-radius:var(--radius-lg,20px);padding:40px 36px;}
.offer h2{font-family:'Playfair Display',serif;font-size:clamp(24px,3vw,32px);line-height:1.2;letter-spacing:-0.01em;margin-bottom:10px;}
.offer p{color:rgba(255,255,255,.8);font-size:16px;line-height:1.7;max-width:36em;}
.offer .btn{margin-top:22px;}
@media(max-width:480px){.offer{padding:30px 22px;}.btn{width:100%;padding:17px 20px;}.btn-sm{width:auto;}}
.author{margin-top:48px;padding-top:32px;border-top:1px solid var(--border-light);display:flex;gap:20px;align-items:flex-start;}
.author img{width:72px;height:72px;border-radius:50%;object-fit:cover;flex-shrink:0;box-shadow:0 6px 20px rgba(27,58,92,.18);}
.author .k{font-size:12px;letter-spacing:1.5px;text-transform:uppercase;color:var(--text-muted);margin-bottom:4px;}
.author b{color:var(--navy);font-size:17px;}
.author p{font-size:15px;color:var(--text-secondary);line-height:1.7;margin-top:6px;}
.author a{color:var(--teal-dark);text-decoration:none;}
@media(max-width:480px){.author{flex-direction:column;}}
.related{padding:72px 0 88px;background:var(--bg-off);margin-top:72px;border-top:1px solid var(--border-light);}
.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:18px;margin-top:28px;}
.card{background:#fff;border:1px solid var(--border-light);border-radius:var(--radius);padding:22px 24px;text-decoration:none;color:inherit;display:flex;flex-direction:column;gap:8px;min-width:0;transition:transform .2s,box-shadow .2s;}
.card:hover{transform:translateY(-2px);box-shadow:var(--shadow-lg,0 8px 32px rgba(22,43,69,.12));}
.card .k{font-size:12px;color:var(--teal-dark);font-weight:600;letter-spacing:1px;text-transform:uppercase;}
.card h3{font-family:'Playfair Display',serif;font-size:20px;line-height:1.25;color:var(--navy);font-weight:700;}
.card p{font-size:14.5px;color:var(--text-secondary);line-height:1.6;}
.card .m{font-size:12.5px;color:var(--text-muted);margin-top:auto;padding-top:6px;}
/* hub */
.hub-head{padding:140px 0 28px;}
.hub-head h1{font-family:'Playfair Display',serif;font-size:clamp(34px,5vw,56px);font-weight:700;line-height:1.1;letter-spacing:-0.02em;color:var(--navy);margin-bottom:14px;}
.hub-head p{font-size:18px;color:var(--text-secondary);max-width:40em;line-height:1.6;}
.filters{display:flex;flex-wrap:wrap;gap:8px;margin:28px 0 8px;}
.filters button{font:600 13px 'Inter',sans-serif;padding:8px 14px;border-radius:999px;border:1px solid var(--border);background:#fff;color:var(--text-secondary);cursor:pointer;}
.filters button[aria-pressed="true"]{background:var(--navy);border-color:var(--navy);color:#fff;}
.filters button:focus-visible{outline:2px solid var(--teal);outline-offset:2px;}
.hub-list{padding:8px 0 88px;}
.empty{color:var(--text-muted);font-size:15px;padding:24px 0;}
.draft-banner{background:#FFF4D6;color:#7A5A00;text-align:center;font-size:13px;font-weight:600;padding:8px 16px;margin-top:72px;}
.draft-banner+.post-head{padding-top:48px;}
@media(max-width:768px){.draft-banner{margin-top:64px;}}
@media(prefers-reduced-motion:reduce){html{scroll-behavior:auto;}.btn,.card{transition:none;}}
"""

def nav(active=""):
    return f"""<nav id="navbar"><div class="nav-inner">
  <a href="/" class="nav-logo"><img src="/Optevalogo.png" alt="Opteva"/></a>
  <div class="nav-links">
    <a href="/#options">The Problem</a>
    <a href="/#does">What It Does</a>
    <a href="/blog/"{' class="active"' if active=="blog" else ''}>Blog</a>
    <a href="/#faq">FAQ</a>
    <a href="/#book" class="btn btn-primary btn-sm">I'm in</a>
  </div></div></nav>"""

FOOTER = """<footer><div class="container"><div class="footer-inner">
  <div>
    <div class="footer-logo"><img src="/Optevalogo.png" alt="Opteva"/></div>
    <div class="footer-contact">
      <a href="mailto:hello@opteva.ai">hello@opteva.ai</a><br/>
      <a href="tel:+13128988620">+1 (312) 898-8620</a><br/>
      Easton, PA
    </div>
  </div>
  <div class="footer-links">
    <a href="/#options">The Problem</a>
    <a href="/#does">What It Does</a>
    <a href="/blog/">Blog</a>
    <a href="/#faq">FAQ</a>
    <a href="/privacy.html">Privacy</a>
    <a href="/terms.html">Terms</a>
  </div>
</div><div class="footer-copy">&copy; %d Opteva. All rights reserved.</div></div></footer>
<script>var navbar=document.getElementById('navbar');window.addEventListener('scroll',function(){navbar.classList.toggle('scrolled',window.scrollY>20);},{passive:true});</script>
""" % datetime.date.today().year

def head(title, desc, url, image, extra="", noindex=False):
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1.0"/>
<link rel="icon" type="image/png" href="/opteva-mark.png"/>
<link rel="apple-touch-icon" href="/opteva-mark.png"/>
<title>{html.escape(title)}</title>
<meta name="description" content="{html.escape(desc, quote=True)}"/>
{'<meta name="robots" content="noindex,nofollow"/>' if noindex else ''}
<link rel="canonical" href="{url}"/>
<link rel="alternate" type="application/rss+xml" title="Opteva Blog" href="{SITE}/blog/feed.xml"/>
<meta property="og:type" content="{'article' if extra else 'website'}"/>
<meta property="og:site_name" content="Opteva"/>
<meta property="og:locale" content="en_US"/>
<meta property="og:url" content="{url}"/>
<meta property="og:title" content="{html.escape(title, quote=True)}"/>
<meta property="og:description" content="{html.escape(desc, quote=True)}"/>
<meta property="og:image" content="{image}"/>
<meta property="og:image:width" content="1200"/>
<meta property="og:image:height" content="630"/>
<meta name="twitter:card" content="summary_large_image"/>
<meta name="twitter:title" content="{html.escape(title, quote=True)}"/>
<meta name="twitter:description" content="{html.escape(desc, quote=True)}"/>
<meta name="twitter:image" content="{image}"/>
<meta name="theme-color" content="#1B3A5C"/>
{extra}
<link rel="preconnect" href="https://fonts.googleapis.com"/>
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin/>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&family=Playfair+Display:ital,wght@0,500;0,700;1,500&display=swap" rel="stylesheet"/>
<style>
{tokens()}
{CSS}
</style>
</head>
<body>
"""

def ld(obj):
    return '<script type="application/ld+json">' + json.dumps(obj, ensure_ascii=False) + "</script>"

# ---------------------------------------------------------------- share image
def share_image(post):
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError:
        print("  Pillow not installed; skipping share image"); return
    OG.mkdir(exist_ok=True)
    W, H = 1200, 630
    im = Image.new("RGB", (W, H), "#FBFAF7"); d = ImageDraw.Draw(im)
    def font(paths, size):
        for p in paths:
            try: return ImageFont.truetype(p, size)
            except Exception: pass
        return ImageFont.load_default()
    SERIF = ["/System/Library/Fonts/Supplemental/Georgia Bold.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSerif-Bold.ttf"]
    SANS = ["/System/Library/Fonts/Supplemental/Arial.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"]
    logo = Image.open(ROOT / "Optevalogo.png").convert("RGBA"); lw = 190; lh = int(logo.height * lw / logo.width)
    im.paste(logo.resize((lw, lh)), (72, 60), logo.resize((lw, lh)))
    label = PILLARS[post["pillar"]][0].upper()
    d.text((W - 72 - d.textlength(label, font=font(SANS, 20)), 72), label, font=font(SANS, 20), fill="#2FA89F")
    # title, wrapped, auto-sized
    size = 60
    while size > 34:
        f = font(SERIF, size); words = post["title"].split(); lines = []; cur = ""
        for w in words:
            t = (cur + " " + w).strip()
            if d.textlength(t, font=f) <= W - 144: cur = t
            else: lines.append(cur); cur = w
        lines.append(cur)
        if len(lines) <= 3: break
        size -= 4
    y = 200
    for ln in lines:
        d.text((72, y), ln, font=f, fill="#1B3A5C"); y += int(size * 1.18)
    d.rectangle([72, 500, 132, 504], fill="#3DBCB2")
    try:
        head_img = Image.open(ROOT / "stephanie-sullivan.png").convert("RGBA").resize((56, 56))
        mask = Image.new("L", (56, 56), 0); ImageDraw.Draw(mask).ellipse([0, 0, 55, 55], fill=255)
        im.paste(head_img, (72, 528), mask); tx = 144
    except Exception:
        tx = 72
    d.text((tx, 530), "Stephanie Sullivan", font=font(SANS, 22), fill="#1B3A5C")
    d.text((tx, 558), "26-year med spa operator  ·  Founder, Opteva  ·  opteva.ai/blog", font=font(SANS, 18), fill="#8fa0b3")
    im.save(OG / f"{post['slug']}.png", optimize=True)

# ---------------------------------------------------------------- render post
def render_post(p, all_posts):
    url = f"{SITE}/blog/{p['slug']}.html"; img = f"{SITE}/blog/og/{p['slug']}.png"
    label, _ = PILLARS[p["pillar"]]
    graph = [{
        "@type": "Article", "@id": url + "#article", "headline": p["title"], "description": p["description"],
        "datePublished": p["date"], "dateModified": p["updated"], "mainEntityOfPage": url, "image": img,
        "wordCount": p["words"], "inLanguage": "en-US", "articleSection": PILLARS[p["pillar"]][1],
        "author": {"@type": "Person", "@id": AUTHOR_ID, "name": "Stephanie Sullivan", "url": "https://steph.opteva.ai/",
                   "jobTitle": "Founder", "worksFor": {"@id": ORG_ID}},
        "publisher": {"@id": ORG_ID},
    }, {
        "@type": "BreadcrumbList", "itemListElement": [
            {"@type": "ListItem", "position": 1, "name": "Opteva", "item": SITE + "/"},
            {"@type": "ListItem", "position": 2, "name": "Blog", "item": SITE + "/blog/"},
            {"@type": "ListItem", "position": 3, "name": p["title"], "item": url}]
    }]
    if p["faq"]:
        graph.append({"@type": "FAQPage", "@id": url + "#faq", "mainEntity": [
            {"@type": "Question", "name": q, "acceptedAnswer": {"@type": "Answer", "text": a}} for q, a in p["faq"]]})
    extra = ld({"@context": "https://schema.org", "@graph": graph})
    toc_html = ""
    if len(p["toc"]) >= 4:
        toc_html = '<div class="toc"><b>In this post</b><ol>' + "".join(f'<li><a href="#{h}">{html.escape(t)}</a></li>' for h, t in p["toc"]) + "</ol></div>"
    cta_h, cta_p = CTA[p["cta"]]
    related = [r for r in all_posts if r["slug"] != p["slug"] and not r["draft"]]
    related.sort(key=lambda r: (r["pillar"] != p["pillar"], r["date"]), reverse=False)
    related = sorted(related, key=lambda r: (r["pillar"] != p["pillar"], ))[:3]
    rel_html = ""
    if related:
        rel_html = '<section class="related"><div class="container"><div class="section-label">Keep reading</div><h2 style="font-family:\'Playfair Display\',serif;font-size:clamp(24px,3vw,34px);color:var(--navy);">More from the blog</h2><div class="cards">' + "".join(card(r) for r in related) + "</div></div></section>"
    draft_banner = '<div class="draft-banner">Draft preview. Not indexed, not listed, not in the sitemap.</div>' if p["draft"] else ""
    dates = nice_date(p["date"]) + (f' &middot; Updated {nice_date(p["updated"])}' if p["updated"] != p["date"] else "")
    out = head(p["title"] + " | Opteva", p["description"], url, img, extra, noindex=p["draft"])
    out += nav("blog") + draft_banner + f"""
<header class="post-head"><div class="container"><div class="narrow">
  <div class="meta"><span class="pillar">{label}</span><span>{dates}</span><span>{p['minutes']} min read</span></div>
  <h1>{html.escape(p['title'])}</h1>
  <p class="lede">{html.escape(p['description'])}</p>
  <div class="byline">
    <img src="/stephanie-sullivan.png" alt="Stephanie Sullivan"/>
    <div class="who"><b><a href="https://steph.opteva.ai/" rel="author">Stephanie Sullivan</a></b><span>26-year med spa operator. Founder of Opteva.</span></div>
  </div>
</div></div></header>
<main class="container"><div class="narrow">
  {toc_html}
  <article class="post-body">
{p['body']}
  </article>
  <aside class="offer" id="offer">
    <h2>{cta_h}</h2>
    <p>{cta_p}</p>
    <a href="/#book" class="btn btn-primary">I'm in <span class="arrow">&rarr;</span></a>
  </aside>
  <div class="author">
    <img src="/stephanie-sullivan.png" alt="Stephanie Sullivan"/>
    <div><div class="k">Written by</div><b>Stephanie Sullivan</b>
      <p>I ran a med spa for 26 years before I built software. Opteva writes each client's social content in that client's own voice and publishes it through GoHighLevel. <a href="https://steph.opteva.ai/">More about me</a>.</p>
    </div>
  </div>
</div></main>
{rel_html}
{FOOTER}
</body>
</html>
"""
    (BLOG / f"{p['slug']}.html").write_text(out, encoding="utf-8")

def card(r):
    return f"""<a class="card" href="/blog/{r['slug']}.html" data-pillar="{r['pillar']}">
  <span class="k">{PILLARS[r['pillar']][0]}</span>
  <h3>{html.escape(r['title'])}</h3>
  <p>{html.escape(r['description'])}</p>
  <span class="m">{nice_date(r['date'])} &middot; {r['minutes']} min read</span>
</a>"""

# ---------------------------------------------------------------- hub, feed, sitemap
def render_hub(published):
    url = SITE + "/blog/"
    graph = {"@context": "https://schema.org", "@type": "Blog", "@id": url + "#blog", "url": url, "name": "Opteva Blog",
             "description": "Social media content for aesthetics operators and the agencies that serve them, written by a 26-year med spa operator.",
             "publisher": {"@id": ORG_ID},
             "blogPost": [{"@type": "BlogPosting", "headline": p["title"], "url": f"{SITE}/blog/{p['slug']}.html", "datePublished": p["date"]} for p in published]}
    filters = "".join(f'<button type="button" data-f="{k}" aria-pressed="false">{v[0]}</button>' for k, v in PILLARS.items())
    body = "".join(card(p) for p in published) if published else '<p class="empty">First post coming soon.</p>'
    out = head("Blog | Opteva", "What to post, how to stay compliant, and how agencies run social for a mixed client roster. Written by a 26-year med spa operator, not a software company.",
               url, SITE + "/og-image.png", ld(graph), noindex=not published)
    out += nav("blog") + f"""
<header class="hub-head"><div class="container">
  <div class="section-label">Blog</div>
  <h1>Written by someone who ran the business.</h1>
  <p>What to post, how to stay compliant, and how an agency runs social for a mixed roster without hiring. Every page here comes from 26 years of operating a med spa, not from a software company's content calendar.</p>
  <div class="filters" role="group" aria-label="Filter posts"><button type="button" data-f="all" aria-pressed="true">All</button>{filters}</div>
</div></header>
<main class="hub-list"><div class="container"><div class="cards" id="posts">{body}</div></div></main>
{FOOTER}
<script>
(function(){{var bs=document.querySelectorAll('.filters button'),cs=document.querySelectorAll('#posts .card');
bs.forEach(function(b){{b.addEventListener('click',function(){{var f=b.dataset.f;bs.forEach(function(x){{x.setAttribute('aria-pressed',x===b)}});
cs.forEach(function(c){{c.hidden=!(f==='all'||c.dataset.pillar===f)}});}});}});}})();
</script>
</body>
</html>
"""
    (BLOG / "index.html").write_text(out, encoding="utf-8")

def render_feed(published):
    items = "".join(f"""
  <item><title>{xesc(p['title'])}</title><link>{SITE}/blog/{p['slug']}.html</link><guid>{SITE}/blog/{p['slug']}.html</guid>
  <pubDate>{datetime.datetime.fromisoformat(p['date']).strftime('%a, %d %b %Y 09:00:00 -0400')}</pubDate><description>{xesc(p['description'])}</description></item>""" for p in published)
    (BLOG / "feed.xml").write_text(f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel><title>Opteva Blog</title><link>{SITE}/blog/</link><description>Social media content for aesthetics operators and the agencies that serve them.</description><language>en-us</language>{items}
</channel></rss>
""", encoding="utf-8")

def render_sitemap(published):
    today = datetime.date.today().isoformat()
    rows = [(SITE + "/", today, "monthly", "1.0")]
    if published: rows.append((SITE + "/blog/", today, "weekly", "0.8"))
    rows += [(f"{SITE}/blog/{p['slug']}.html", p["updated"], "monthly", "0.7") for p in published]
    rows += [(SITE + "/privacy.html", "2026-09-30", "yearly", "0.2"), (SITE + "/terms.html", "2026-09-30", "yearly", "0.2")]
    (ROOT / "sitemap.xml").write_text('<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n' +
        "".join(f"  <url><loc>{u}</loc><lastmod>{m}</lastmod><changefreq>{c}</changefreq><priority>{pr}</priority></url>\n" for u, m, c, pr in rows) + "</urlset>\n", encoding="utf-8")

# ---------------------------------------------------------------- main
def main():
    POSTS.mkdir(parents=True, exist_ok=True)
    posts = sorted((parse_post(p) for p in POSTS.glob("*.md")), key=lambda p: p["date"], reverse=True)
    published = [p for p in posts if not p["draft"]]
    for p in posts:
        render_post(p, posts); share_image(p)
        print(f"  {'draft    ' if p['draft'] else 'published'}  /blog/{p['slug']}.html  ({p['words']} words, {p['minutes']} min)")
    render_hub(published); render_feed(published); render_sitemap(published)
    print(f"built {len(posts)} post(s), {len(published)} published; hub, feed, sitemap updated")

if __name__ == "__main__":
    main()
