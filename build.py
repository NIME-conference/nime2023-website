#!/usr/bin/env python3
"""Build a self-contained static archive of https://www.nime2023.org (a Google Sites site).

    python3 build.py            # fetch (cached in ./cache) and write ./site
    python3 build.py --refresh  # ignore cached page HTML and re-fetch pages

What it does:
  * fetches the 24 pages and saves them under the same file names as the
    existing nime.org/web_archive/2023 copy (so old URLs keep working)
  * strips Google's scripts; stores CSS, fonts and images locally
  * downloads linked/embedded Google Drive files, Sheets and Docs and rewrites
    the links/iframes to the local copies
  * does not copy the proceedings papers: the paper PDFs linked from the Papers page are
    pointed at their nime.org proceedings entries instead (see papers.json)
  * replaces embeds that no longer exist with a short note
  * adds a small stylesheet/script (assets/archive.*) so menus work without Google's JS

Only the Python standard library is used.
"""
import hashlib, html, io, json, mimetypes, os, re, shutil, sys, urllib.parse, urllib.request, zipfile
from datetime import date

BASE = "https://www.nime2023.org"
HERE = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(HERE, "cache")
SITE = os.path.join(HERE, "site")
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")
REFRESH = "--refresh" in sys.argv

# live path -> file name used by the existing archive on nime.org
PAGES = [
    ("home", "index.html"),
    ("awards-photos", "Awards  Photos.html"),
    ("registration", "Registration.html"),
    ("program", "Program.html"),
    ("program/papers", "Papers.html"),
    ("program/posters", "Posters.html"),
    ("program/online-in-person-concerts", "Online  In-person Concerts.html"),
    ("program/latam-nime-gathering", "LATAM NIME Gathering.html"),
    ("program/workshops", "Workshops.html"),
    ("program/summer-school", "Summer School.html"),
    ("keynote-speakers", "Keynote Speakers.html"),
    ("important-dates", "Important Dates.html"),
    ("travel-information", "Travel Information.html"),
    ("sponsors", "Sponsors.html"),
    ("code-of-conduct", "Code of Conduct.html"),
    ("call-for-submissions/call-for-papers", "Call for Papers.html"),
    ("call-for-submissions/call-for-work-in-progress-showcase", "Call for Work-In-Progress Showcase.html"),
    ("call-for-submissions/call-for-demos", "Call for Demos.html"),
    ("call-for-submissions/call-for-music", "Call for Music.html"),
    ("call-for-submissions/call-for-art-installations", "Call for Art Installations.html"),
    ("call-for-submissions/call-for-workshops", "Call for Workshops.html"),
    ("call-for-submissions/student-consortium", "Student Consortium.html"),
    ("call-for-submissions/open-jack", "Open Jack.html"),
    ("contact", "Contact.html"),
]
PAGE_MAP = {p: f for p, f in PAGES}
PAGE_MAP[""] = "index.html"

# Drive file id -> {"file", "title", "entry"} for the PDFs on the Papers page. "entry" is the
# NIME-bibliography key (https://nime.org/proc/<entry>/), or null if not in the proceedings.
PAPERS = json.load(open(os.path.join(HERE, "papers.json"), encoding="utf-8"))
PROC = "https://nime.org/proc/"
UNPUBLISHED = "#nime-not-in-proceedings"
PAPER_LABEL = "Paper in the NIME proceedings"
UNPUBLISHED_NOTE = "This paper was not presented or published in the proceedings."

report = {"missing": [], "files": [], "sheets": [], "external_left": set(), "unknown_internal": set()}


# ---------------------------------------------------------------- fetching
def _key(url):
    return hashlib.sha1(url.encode()).hexdigest()


def fetch(url, force=False):
    """Return (bytes_path, meta) for url, cached on disk. meta: status, ctype, cdisp, url."""
    os.makedirs(CACHE, exist_ok=True)
    k = _key(url)
    body, meta_p = os.path.join(CACHE, k + ".bin"), os.path.join(CACHE, k + ".json")
    if os.path.exists(meta_p) and not force:
        return body, json.load(open(meta_p))
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=120) as r, open(body, "wb") as out:
            shutil.copyfileobj(r, out)
            meta = {"status": r.status, "ctype": r.headers.get("Content-Type", ""),
                    "cdisp": r.headers.get("Content-Disposition", ""), "url": r.geturl()}
    except urllib.error.HTTPError as e:
        open(body, "wb").close()
        meta = {"status": e.code, "ctype": "", "cdisp": "", "url": url}
    except Exception as e:  # network trouble: do not cache
        print("  ! fetch failed", url[:100], e)
        return body, {"status": 0, "ctype": "", "cdisp": "", "url": url}
    meta["src"] = url
    json.dump(meta, open(meta_p, "w"))
    return body, meta


def ok(meta):
    return meta["status"] == 200


def read(path):
    with open(path, "rb") as f:
        return f.read()


def put(rel, data):
    p = os.path.join(SITE, rel)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    if isinstance(data, str):
        data = data.encode("utf-8")
    with open(p, "wb") as f:
        f.write(data)


def copy_in(src, rel):
    p = os.path.join(SITE, rel)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    shutil.copyfile(src, p)


def q(rel):
    """site-relative path -> href value"""
    return urllib.parse.quote(rel)


EXT = {"image/jpeg": ".jpg", "image/png": ".png", "image/gif": ".gif", "image/webp": ".webp",
       "image/svg+xml": ".svg", "image/x-icon": ".ico", "image/vnd.microsoft.icon": ".ico",
       "font/woff2": ".woff2", "font/woff": ".woff", "font/ttf": ".ttf", "text/css": ".css"}


alias = {}  # expired tokenised URL -> rel path fetched via a fresh token


def full_res(u):
    """googleusercontent image URLs end in =wNNNN (resized); =s0 gives the original upload."""
    return re.sub(r"=w\d+$", "=s0", u) if "googleusercontent.com" in u else u


def page_images(doc):
    us = re.findall(r'<img\b[^>]*?\ssrc="(https://[^"]+)"', doc) + re.findall(r"url\((https://lh\d[^)]+)\)", doc)
    return [html.unescape(u) for u in us]


def prefetch_images(path, doc):
    """Google Sites image URLs carry tokens that expire within a minute or so: grab them all
    at once, and retry failures against a freshly fetched copy of the page (same order)."""
    from concurrent.futures import ThreadPoolExecutor
    want = [full_res(u) for u in page_images(doc)]
    with ThreadPoolExecutor(8) as ex:
        got = list(ex.map(asset, want))
    for attempt in range(3):
        bad = [i for i, r in enumerate(got) if not r]
        if not bad:
            break
        body, meta = fetch(f"{BASE}/{path}", force=True)
        fresh = [full_res(u) for u in page_images(read(body).decode("utf-8", "replace"))]
        if len(fresh) != len(want):
            break
        with ThreadPoolExecutor(8) as ex:
            for i, r in zip(bad, ex.map(asset, [fresh[i] for i in bad])):
                if r:
                    got[i] = alias[want[i]] = r


def asset(url, sub="img"):
    """Download a static resource; return site-relative path or None."""
    if url.startswith("//"):
        url = "https:" + url
    if url in alias:
        return alias[url]
    body, meta = fetch(url)
    ctype = meta["ctype"].split(";")[0].strip().lower()
    if not ok(meta) or ctype.startswith("text/html") or os.path.getsize(body) == 0:
        return None
    ext = EXT.get(ctype) or os.path.splitext(urllib.parse.urlparse(url).path)[1][:6] or ".bin"
    rel = f"assets/{sub}/{_key(url)[:16]}{ext}"
    if not os.path.exists(os.path.join(SITE, rel)):
        copy_in(body, rel)
    return rel


CSS_URL = re.compile(r"url\(\s*(['\"]?)([^)'\"]+)\1\s*\)")


def localize_css_urls(css, base_url, prefix):
    """Rewrite url(...) in CSS text. prefix: path from the CSS's location to site root."""
    def sub(m):
        u = html.unescape(m.group(2).strip())
        if u.startswith("data:") or u.startswith("#"):
            return m.group(0)
        full = urllib.parse.urljoin(base_url, u)
        if "drive.google.com/thumbnail" in full:
            return "none"
        sub_dir = "fonts" if re.search(r"\.(woff2?|ttf|otf)(\?|$)", full) or "fonts.gstatic" in full else "img"
        rel = asset(full_res(full), sub_dir) or asset(full, sub_dir)
        if not rel:
            report["missing"].append(("css url", full[:120]))
            return m.group(0)
        return f"url({prefix}{q(rel)})"
    return CSS_URL.sub(sub, css)


def stylesheet(url):
    body, meta = fetch(url)
    if not ok(meta):
        return None
    css = read(body).decode("utf-8", "replace")
    css = re.sub(r"@import\s+url\(([^)]+)\);?", "", css)
    css = localize_css_urls(css, url, "../../")
    rel = f"assets/css/{_key(url)[:16]}.css"
    put(rel, css)
    return rel


# ---------------------------------------------------------------- Google Drive / Docs
def safe_name(n):
    n = re.sub(r"[^\w.\- ]+", "_", n).strip(" .")
    return n or "file"


used_names = {}


def unique(rel, ident):
    """Avoid two different sources colliding on the same output path."""
    if used_names.setdefault(rel, ident) == ident:
        return rel
    stem, ext = os.path.splitext(rel)
    return unique(f"{stem}-{ident[:6]}{ext}", ident)


def drive_file(fid, sub):
    """Download a Drive file. Returns site-relative path or None if not available."""
    body, meta = fetch(f"https://drive.usercontent.google.com/download?id={fid}&export=download&confirm=t")
    if not ok(meta) or meta["ctype"].startswith("text/html"):
        return None
    m = re.search(r'filename="([^"]*)"', meta["cdisp"])
    name = safe_name(urllib.parse.unquote(m.group(1)) if m else fid)
    rel = unique(f"files/{sub}/{name}" if sub else f"files/{name}", fid)
    if not os.path.exists(os.path.join(SITE, rel)):
        copy_in(body, rel)
        report["files"].append((rel, os.path.getsize(body)))
    return rel


SHEET_WRAP = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>{title}</title>
<style>body{{margin:0;font:14px/1.4 Roboto,Arial,sans-serif;color:#222}}
.bar{{position:sticky;top:0;left:0;background:#f1f3f4;border-bottom:1px solid #ccc;padding:6px 10px;display:flex;gap:6px 14px;flex-wrap:wrap;align-items:center;z-index:5}}
.bar b{{margin-right:auto}}.bar a{{color:#1a56b0;text-decoration:none}}.bar a.on{{font-weight:bold;color:#000}}
.tab{{display:none}}.tab.on{{display:block}}</style>
{css}</head><body>
<div class="bar"><b>{title}</b>{tabs}<a href="{xlsx}" download>Download .xlsx</a></div>
{body}
<script>document.querySelectorAll('.bar a[data-t]').forEach(function(a){{a.addEventListener('click',function(e){{e.preventDefault();
document.querySelectorAll('.tab,.bar a[data-t]').forEach(function(x){{x.classList.remove('on')}});
a.classList.add('on');document.getElementById(a.dataset.t).classList.add('on')}})}});</script>
</body></html>"""

sheet_done = {}


def sheet(sid):
    """Static copy of a Google Sheet: one HTML page (all tabs) + .xlsx. Returns html rel path or None."""
    if sid in sheet_done:
        return sheet_done[sid]
    zbody, zmeta = fetch(f"https://docs.google.com/spreadsheets/d/{sid}/export?format=zip")
    xbody, xmeta = fetch(f"https://docs.google.com/spreadsheets/d/{sid}/export?format=xlsx")
    if not ok(zmeta) or "zip" not in zmeta["ctype"]:
        sheet_done[sid] = None
        return None
    tbody, tmeta = fetch(f"https://docs.google.com/spreadsheets/d/{sid}/htmlembed")
    m = re.search(r"<title>(.*?)(?: - Google (?:Drive|Sheets))?</title>", read(tbody).decode("utf-8", "replace")) if ok(tmeta) else None
    title = re.sub(r"\s+", " ", html.unescape(m.group(1))).strip() if m else sid
    slug = unique(f"embeds/sheets/{safe_name(title.replace('|', '-'))}.html", sid)
    xrel = slug[:-5] + ".xlsx"
    z = zipfile.ZipFile(zbody)
    css, tabs, bodies = "", [], []
    for n in z.namelist():
        if n.endswith(".css"):
            css += "<style>" + z.read(n).decode("utf-8", "replace") + "</style>\n"
    names = [n for n in z.namelist() if n.endswith(".html")]
    for i, n in enumerate(names):
        t = z.read(n).decode("utf-8", "replace")
        css += "".join(re.findall(r"(?s)<style[^>]*>.*?</style>", t)) + "\n"
        t = re.sub(r"(?s)<style[^>]*>.*?</style>|<link[^>]*>|<meta[^>]*>|<script.*?</script>", "", t)
        on = " on" if i == 0 else ""
        bodies.append(f'<div class="tab{on}" id="t{i}">{t}</div>')
        if len(names) > 1:
            tabs.append(f'<a href="#t{i}" data-t="t{i}" class="{on.strip()}">{html.escape(n[:-5])}</a>')
    css = localize_css_urls(css, "https://docs.google.com/", "../../")
    if ok(xmeta):
        copy_in(xbody, xrel)
    put(slug, SHEET_WRAP.format(title=html.escape(title), css=css, tabs="".join(tabs),
                                xlsx=q(os.path.basename(xrel)), body="\n".join(bodies)))
    report["sheets"].append((slug, len(names)))
    sheet_done[sid] = slug
    return slug


def gdoc(did):
    """Google Doc -> PDF copy. Returns rel path or None."""
    body, meta = fetch(f"https://docs.google.com/document/d/{did}/export?format=pdf")
    if not ok(meta) or "pdf" not in meta["ctype"]:
        return None
    m = re.search(r'filename="([^"]*)"', meta["cdisp"])
    name = safe_name(urllib.parse.unquote(m.group(1)) if m else did + ".pdf")
    rel = unique(f"files/{name}", did)
    copy_in(body, rel)
    report["files"].append((rel, os.path.getsize(body)))
    return rel


def google_link(url, sub):
    """Map a Google Drive/Docs URL to a local copy (site-relative path), or None."""
    m = re.search(r"docs\.google\.com/spreadsheets/d/([\w-]+)", url)
    if m:
        return sheet(m.group(1))
    m = re.search(r"docs\.google\.com/document/d/([\w-]+)", url)
    if m:
        return gdoc(m.group(1))
    m = re.search(r"drive\.google\.com/(?:file/d/([\w-]+)|open\?id=([\w-]+)|uc\?id=([\w-]+))", url)
    if m:
        fid = m.group(1) or m.group(2) or m.group(3)
        return drive_file(fid, sub) or sheet(fid) or gdoc(fid)
    return None


# ---------------------------------------------------------------- HTML rewriting
def attr_sub(doc, tag, attr, fn):
    """Replace attr="value" on <tag ...> using fn(unescaped_value, whole_tag) -> new value or None."""
    pat = re.compile(r"<%s\b[^>]*>" % tag, re.I)

    def tag_sub(m):
        t = m.group(0)
        am = re.search(r'(\s%s=)"([^"]*)"' % re.escape(attr), t)
        if not am:
            return t
        new = fn(html.unescape(am.group(2)), t)
        if new is None:
            return t
        return t[:am.start()] + am.group(1) + '"' + html.escape(new, quote=True) + '"' + t[am.end():]
    return pat.sub(tag_sub, doc)


def element_span(doc, start):
    """Given index of '<div' (or any tag) start, return end index of its matching close tag."""
    name = re.match(r"<(\w+)", doc[start:]).group(1)
    depth = 0
    for m in re.finditer(r"<(/?)%s\b[^>]*>" % name, doc[start:]):
        depth += -1 if m.group(1) else 1
        if depth == 0:
            return start + m.end()
    return len(doc)


def note(text, link=None, label=None):
    a = f' <a href="{html.escape(link, quote=True)}" target="_blank" rel="noopener">{html.escape(label or link)}</a>' if link else ""
    return f'<div class="nime-archive-note"><span>{text}{a}</span></div>'


def process_page(path, out):
    body, meta = fetch(f"{BASE}/{path}", force=REFRESH)
    if not ok(meta):
        report["missing"].append(("page", path))
        return
    doc = read(body).decode("utf-8", "replace")
    sub = "papers" if path == "program/papers" else ""
    prefetch_images(path, doc)

    # --- remove scripts and script-only leftovers
    doc = re.sub(r"(?s)<script\b.*?</script>", "", doc)
    doc = re.sub(r'\snonce="[^"]*"', "", doc)
    doc = re.sub(r'<link\b[^>]*rel="(?:preload|preconnect|dns-prefetch|canonical)"[^>]*>', "", doc)
    doc = re.sub(r'<meta\b[^>]*(?:og:url|og:image|twitter:image|itemprop="(?:url|image|thumbnailUrl|imageUrl)")[^>]*>', "", doc)

    # --- stylesheets
    def css_href(v, t):
        if "stylesheet" not in t:
            return None
        rel = stylesheet(urllib.parse.urljoin(BASE, v))
        return q(rel) if rel else None
    doc = attr_sub(doc, "link", "href", css_href)

    def icon_href(v, t):
        if 'rel="icon"' not in t and "shortcut icon" not in t:
            return None
        rel = asset(v)
        return q(rel) if rel else None
    doc = attr_sub(doc, "link", "href", icon_href)

    # --- images (first: Google's image URLs carry tokens that expire within minutes)
    def img_src(v, t):
        if v.startswith("data:"):
            return None
        full = urllib.parse.urljoin(BASE, v)
        rel = asset(full_res(full)) or asset(full)
        if not rel:
            report["missing"].append(("img", f"{out}: {v[:110]}"))
        return q(rel) if rel else None
    doc = attr_sub(doc, "img", "src", img_src)
    doc = re.sub(r'\ssrcset="[^"]*"', "", doc)
    doc = re.sub(r"<img\b(?![^>]*\sloading=)", '<img loading="lazy"', doc)

    # --- url(...) in inline styles and <style> blocks
    doc = re.sub(r'(?s)(<style\b[^>]*>)(.*?)(</style>)',
                 lambda m: m.group(1) + localize_css_urls(m.group(2), BASE, "") + m.group(3), doc)
    doc = re.sub(r'\sstyle="([^"]*url\([^"]*)"',
                 lambda m: ' style="' + html.escape(localize_css_urls(html.unescape(m.group(1)), BASE, ""), quote=True) + '"', doc)

    # --- embeds: iframes carry data-src and are normally activated by Google's JS
    def iframe_sub(m):
        t = m.group(0)
        sm = re.search(r'\sdata-src="([^"]*)"', t) or re.search(r'\ssrc="([^"]*)"', t)
        if not sm:
            return t
        src = html.unescape(sm.group(1))
        label = re.search(r'aria-label="([^"]*)"', t)
        label = html.unescape(label.group(1)) if label else ""
        label = re.sub(r"^(Drive|Spreadsheet|Document|YouTube Video|YouTube), ", "", label)
        yt = re.search(r"youtube(?:-nocookie)?\.com/embed/([\w-]+)", src)
        if yt:
            report["external_left"].add(f"https://www.youtube.com/watch?v={yt.group(1)}")
            return (f'<iframe class="YMEQtf nime-live" frameborder="0" allowfullscreen loading="lazy" '
                    f'title="{html.escape(label, quote=True)}" src="https://www.youtube.com/embed/{yt.group(1)}"></iframe>')
        local = google_link(src, sub)
        if local and local.endswith(".html"):
            return (f'<iframe class="YMEQtf nime-live" frameborder="0" loading="lazy" '
                    f'title="{html.escape(label, quote=True)}" src="{q(local)}"></iframe>')
        if local and re.search(r"\.(mp4|webm|mov|m4v)$", local, re.I):
            return f'<video class="YMEQtf nime-live" controls preload="metadata" src="{q(local)}"></video>'
        if local and local.lower().endswith(".pdf"):
            return f'<iframe class="YMEQtf nime-live" frameborder="0" loading="lazy" src="{q(local)}"></iframe>'
        if local:
            return note(f"Embedded file: ", local, os.path.basename(local))
        report["missing"].append(("embed", f"{out}: {label or src}"))
        what = "video" if re.search(r"\.(mp4|mov|webm|m4v)$", label, re.I) else "file"
        return note(f"<strong>{html.escape(label)}</strong><br>This embedded {what} was hosted on Google Drive "
                    f"and is no longer available.")
    doc = re.sub(r"(?s)<iframe\b[^>]*>\s*</iframe>", iframe_sub, doc)

    # --- custom HTML embeds (data-code), e.g. the Flickr album
    while True:
        m = re.search(r'<div\b[^>]*\sdata-code="([^"]*)"[^>]*>', doc)
        if not m:
            break
        code = html.unescape(m.group(1))
        code = re.sub(r"(?s)<script\b.*?</script>", "", code)
        code = re.sub(r'\s(?:width|height)="\d+"', "", code)
        code = attr_sub(code, "img", "src", lambda v, t: (lambda r: q(r) if r else None)(asset(urllib.parse.urljoin(BASE, v))))
        link = re.search(r'href="([^"]+)"', code)
        cap = ""
        if link:
            report["external_left"].add(link.group(1))
            host = urllib.parse.urlsplit(link.group(1)).netloc.replace("www.", "")
            cap = (f'<p class="nime-embed-caption"><a href="{html.escape(link.group(1), quote=True)}" target="_blank" '
                   f'rel="noopener">View the full album on {html.escape(host)}</a> (external site)</p>')
        doc = doc[:m.start()] + f'<div class="nime-html-embed">{code}{cap}</div>' + doc[element_span(doc, m.start()):]

    # --- links
    def a_href(v, t):
        u = v.strip()
        for pre in (BASE, "https://nime2023.org", "http://www.nime2023.org", "http://nime2023.org"):
            if u == pre or u.startswith(pre + "/") or u.startswith(pre + "#") or u.startswith(pre + "?"):
                u = u[len(pre):] or "/"
        if u.startswith("/") and not u.startswith("//"):
            p = urllib.parse.urlsplit(u)
            key = p.path.strip("/")
            if key in PAGE_MAP:
                return q(PAGE_MAP[key]) + ("#" + p.fragment if p.fragment else "")
            report["unknown_internal"].add(u)
            return BASE + u
        if re.search(r"(drive|docs)\.google\.com/", u):
            fm = re.search(r"drive\.google\.com/(?:file/d/|open\?id=|uc\?id=)([\w-]+)", u)
            if fm and fm.group(1) in PAPERS:
                entry = PAPERS[fm.group(1)]["entry"]
                return f"{PROC}{entry}/" if entry else UNPUBLISHED
            local = google_link(u, sub)
            if local:
                return q(local)
            report["missing"].append(("link", f"{out}: {u[:110]}"))
            return None
        if u.startswith("http") and "google.com/url?" in u:
            qs = urllib.parse.parse_qs(urllib.parse.urlsplit(u).query)
            if qs.get("q"):
                return qs["q"][0]
        return None
    doc = attr_sub(doc, "a", "href", a_href)
    # paper links: label them as proceedings entries; papers that are not in the proceedings get a note
    doc = re.sub(r'(<a\b[^>]*href="%s[^"]*"[^>]*>\s*<span\b[^>]*>)\d+\.pdf(</span>)' % re.escape(PROC),
                 lambda m: m.group(1) + PAPER_LABEL + m.group(2), doc)
    doc = re.sub(r'<a\b[^>]*href="%s"[^>]*>\s*(<span\b[^>]*>)\d+\.pdf(</span>)\s*</a>' % re.escape(UNPUBLISHED),
                 lambda m: m.group(1).replace(" text-decoration: underline;", " font-style: italic;") + UNPUBLISHED_NOTE + m.group(2), doc)
    doc = attr_sub(doc, "a", "data-url", lambda v, t: (q(PAGE_MAP[v.strip("/")]) if v.startswith("/") and v.strip("/") in PAGE_MAP else None))

    # --- navigation: show sub-pages (Program, Call for Submissions) without needing a click
    doc = re.sub(r'(<div class="oGuwee[^"]*") style="display:none;"', r'\1 style=""', doc)
    doc = doc.replace('<svg class="dvmRw" ', '<svg class="dvmRw FWGjId" ').replace('aria-expanded="false" aria-haspopup="true"', 'aria-expanded="true" aria-haspopup="true"')

    # --- collapsible sections (e.g. each paper's authors + PDF): expanded by default, archive.js toggles
    doc = doc.replace('class="vhaaFf qUO6Ue"', 'class="qUO6Ue"')

    # --- our additions
    head = '<link rel="stylesheet" href="assets/archive.css"><script defer src="assets/archive.js"></script>'
    doc = doc.replace("</head>", head + "</head>", 1)
    foot = (f'<div class="nime-archive-footer">Archived copy of <a href="{BASE}/{path}">{BASE.replace("https://", "")}</a>, '
            f'captured {date.today().isoformat()} and hosted by <a href="https://nime.org/">nime.org</a>. '
            f'Interactive features of the original site (search, forms) are not available.</div>')
    doc = doc.replace("</body>", foot + "</body>", 1)
    put(out, doc)
    print(f"  {out}  ({len(doc)//1024} KB)")


def main():
    if os.path.exists(SITE):
        shutil.rmtree(SITE)
    os.makedirs(SITE)
    for path, out in PAGES:
        process_page(path, out)
    for f in ("archive.css", "archive.js"):
        shutil.copyfile(os.path.join(HERE, "overlay", f), os.path.join(SITE, "assets", f))
    print("\n== report")
    print("sheets:", len(report["sheets"]), report["sheets"])
    tot = sum(s for _, s in report["files"])
    print(f"downloaded files: {len(report['files'])} ({tot/1e6:.0f} MB)")
    print("unknown internal links:", sorted(report["unknown_internal"]))
    print("external embeds kept:", sorted(report["external_left"]))
    print("missing / unavailable:")
    for kind, what in report["missing"]:
        print("  ", kind, "|", what)
    json.dump({k: (sorted(v) if isinstance(v, set) else v) for k, v in report.items()},
              open(os.path.join(HERE, "build-report.json"), "w"), indent=1)


if __name__ == "__main__":
    main()
