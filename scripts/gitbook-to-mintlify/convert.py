#!/usr/bin/env python3
"""Convert the GitBook Git Sync export (ayaka-notes/ayakaleaf-docs, English only) to Mintlify MDX.

Usage: python3 scripts/gitbook-to-mintlify/convert.py --src ../ayakaleaf-docs [--dst .] [--strict]

Generated output (overwritten on every run): index.mdx, the section folders listed in
SECTIONS, images/<section>/, and docs.json "navigation". Other docs.json keys are kept.
"""
import argparse, hashlib, html, json, os, re, shutil, sys, unicodedata, urllib.parse
from html.parser import HTMLParser
import yaml

_args = argparse.ArgumentParser()
_args.add_argument("--src", default=os.path.expanduser("~/ayakaleaf-docs"), help="GitBook Git Sync checkout")
_args.add_argument("--dst", default=os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."), help="Mintlify repo root")
_args.add_argument("--strict", action="store_true", help="exit 1 when there are warnings (for CI)")
ARGS = _args.parse_args()
SRC = os.path.abspath(ARGS.src)
DST = os.path.abspath(ARGS.dst)
WARN = []   # actionable problems; fail the run with --strict
NOTES = []  # known, pre-existing limitations of the GitBook content

# (key, title, source dir, url prefix, tab icon, GitBook variant slug) -- mirrors gitbook-docs.yaml
SECTIONS = [
    ("home", "Home", "docs/home", "", "house-chimney", "home"),
    ("on-premises", "On-Premises", "docs/on-premises", "on-premises", "server", "en"),
    ("blog", "Blog", "docs/blog", "blog", "rss", "en"),
    ("texlive", "TeXLive", "docs/texlive", "texlive", "compact-disc", "en"),
    ("latex", "LaTeX", "latex/en", "latex", "tex", "en"),
    ("dev", "Developer", "docs/dev", "dev", "rectangle-terminal", "untitled"),
]
# Sections that are converted for link resolution but not published yet: no pages, no tab,
# and links pointing into them are rendered as plain text.
UNPUBLISHED = {"latex"}
# SUMMARY.md groups left out of the sidebar. Their pages are still generated, so links keep working.
HIDDEN_GROUPS = {("on-premises", "Getting started")}
# Mintlify translation languages that were removed in favour of another code (old -> kept).
RETIRED_LANGUAGES = {"zh-Hans": "zh-CN", "zh-Hant": "zh-TW"}
UNPUB = "\x02unpublished\x02"
SPACE_IDS = {
    "yLFrF2L1FakWXkhqpOnS": "on-premises",
    "ykgtg1oSTKbEk3s3qtSU": "blog",
    "I2qEfJyb19sFvDmuZcCm": "dev",
    "0bsEVRuqHiTwccEZfTnf": "texlive",
}
# GitBook buttons, styled with Mintlify's Tailwind theme tokens so they follow docs.json colors
BUTTON_CLASSES = {
    "primary": "not-prose inline-flex items-center gap-2 rounded-xl px-4 py-2 text-sm font-semibold no-underline "
               "bg-primary text-white dark:bg-primary-light dark:text-gray-950 hover:opacity-90",
    "secondary": "not-prose inline-flex items-center gap-2 rounded-xl px-4 py-2 text-sm font-semibold no-underline "
                 "border border-gray-950/15 text-gray-900 dark:border-white/20 dark:text-gray-100 hover:bg-gray-950/5 dark:hover:bg-white/5",
}
HINTS = {"info": "Info", "success": "Check", "warning": "Warning", "danger": "Danger", "tip": "Tip"}
HTML_TAGS = set("""a abbr b blockquote br button caption code col colgroup dd del details div dl dt em
figcaption figure form h1 h2 h3 h4 h5 h6 hr i img input kbd li mark ol p pre s small span strong sub
summary sup table tbody td tfoot th thead tr u ul video iframe""".split())
VOID = {"br", "hr", "img", "input", "col"}

# ---------------------------------------------------------------- page index
pages = {}  # abs src path -> {"route": "/x/y", "title": str}


def route_for(sec_prefix, rel):
    rel = rel[:-3]
    if rel == "README":
        rel = "index"
    elif rel.endswith("/README"):
        rel = rel[: -len("/README")] + "/index"
    path = f"{sec_prefix}/{rel}" if sec_prefix else rel
    return path  # page id (no leading slash)


def url_for(page_id):
    u = "/" + page_id
    if u.endswith("/index"):
        u = u[: -len("/index")] or "/"
    return u


def split_fm(text):
    if text.startswith("---\n"):
        end = text.find("\n---\n", 4)
        if end != -1:
            return yaml.safe_load(text[4:end]) or {}, text[end + 5:]
    return {}, text


for key, _, d, prefix, _, _ in SECTIONS:
    base = os.path.join(SRC, d)
    for root, dirs, files in os.walk(base):
        dirs[:] = [x for x in dirs if not x.startswith(".")]
        for f in files:
            if not f.endswith(".md") or f == "SUMMARY.md":
                continue
            p = os.path.join(root, f)
            rel = os.path.relpath(p, base)
            fm, body = split_fm(open(p, encoding="utf-8").read())
            m = re.search(r"^# (.+)$", body, re.M)
            title = (fm.get("title") or (m.group(1) if m else f[:-3])).strip()
            title = re.sub(r"\s*<a [^>]*></a>\s*", "", title)
            pages[os.path.normpath(p)] = {"id": route_for(prefix, rel), "title": title, "section": key, "base": base,
                                          "published": key not in UNPUBLISHED, "icon": fm.get("icon"),
                                          "hidden": bool(fm.get("hidden"))}

# ---------------------------------------------------------------- icons
# Mintlify loads Font Awesome icons from its CDN and only knows some brand names (github, docker, ...).
# Brand-only icons such as `claude` need iconType="brands", otherwise it requests regular/ and shows nothing.
FA_CDN = "https://d3gk2c5xim1je2.cloudfront.net/fontawesome/v7.2.0/"
ICON_CACHE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "icon-types.json")
_icon_types = json.load(open(ICON_CACHE)) if os.path.exists(ICON_CACHE) else {}


def icon_type(name):
    """'regular', 'brands' or 'missing' (cached in icon-types.json so CI runs offline)."""
    if name not in _icon_types:
        import urllib.request
        _icon_types[name] = "missing"
        for kind in ("regular", "brands"):
            try:
                urllib.request.urlopen(urllib.request.Request(FA_CDN + kind + "/" + name + ".svg", method="HEAD"), timeout=20)
                _icon_types[name] = kind
                break
            except Exception:
                continue
    if _icon_types[name] == "missing":
        WARN.append(f"icon not found in Mintlify's Font Awesome: {name}")
    return _icon_types[name]


def nav_icon(name):
    """Page/group icons: the sidebar ignores iconType, so brand-only icons are given as their SVG url."""
    return FA_CDN + "brands/" + name + ".svg" if icon_type(name) == "brands" else name


def add_icon_types(text):
    """Add iconType="brands" to <Icon>/<Card> tags whose icon only exists as a brand."""
    def fix(m):
        if "iconType=" in m.group(0) or icon_type(m.group(2)) != "brands":
            return m.group(0)
        return m.group(0) + ' iconType="brands"'
    return re.sub(r'(<(?:Icon|Card)\b[^>]*?\bicon="([^"]+)")', fix, text)


# ---------------------------------------------------------------- media
MAX_MEDIA = 15 * 1024 * 1024  # Mintlify does not deploy large static files (a 34 MB video 404'd)


def shrink_video(path):
    """Re-encode videos above MAX_MEDIA to 1280px H.264 (cached). Needs ffmpeg ($FFMPEG or PATH)."""
    if os.path.getsize(path) <= MAX_MEDIA:
        return path
    import subprocess
    digest = hashlib.sha1(open(path, "rb").read()).hexdigest()[:12]
    out = os.path.join(CACHE, "video", digest + ".mp4")
    if not os.path.exists(out):
        ffmpeg = os.environ.get("FFMPEG") or shutil.which("ffmpeg")
        if not ffmpeg:
            WARN.append(f"{path}: {os.path.getsize(path) >> 20} MB video needs ffmpeg to shrink (set $FFMPEG)")
            return path
        os.makedirs(os.path.dirname(out), exist_ok=True)
        subprocess.run([ffmpeg, "-loglevel", "error", "-y", "-i", path, "-vf", "scale='min(1280,iw)':-2",
                        "-c:v", "libx264", "-preset", "slow", "-crf", "23", "-c:a", "aac", "-b:a", "96k",
                        "-movflags", "+faststart", out + ".tmp.mp4"], check=True)
        os.replace(out + ".tmp.mp4", out)
    if os.path.getsize(out) > MAX_MEDIA:
        WARN.append(f"{path}: still {os.path.getsize(out) >> 20} MB after re-encoding")
    return out


# ---------------------------------------------------------------- output (only touch files that change)
GENERATED = set()  # absolute paths written by this run


EMPHASIS = re.compile(r"(?<![*\\])(\*\*\*|\*\*|\*)(?=[^\s*])(.+?)(?<=[^\s*])\1(?!\*)")
EMPHASIS_TAGS = {1: ("<em>", "</em>"), 2: ("<strong>", "</strong>"), 3: ("<em><strong>", "</strong></em>")}


def translation_safe_emphasis(text):
    """Write emphasis that starts or ends with punctuation as HTML, e.g. `**Note:**` -> `<strong>Note:</strong>`.

    It renders the same in English, but Mintlify's translations turn `**Note:** text` into
    `**注意：**text`, and Markdown doesn't treat `**` between full-width punctuation and a letter
    as a closing marker. HTML tags survive translation and always render.
    """
    def punct(c):
        return unicodedata.category(c)[0] in "PS"

    def line_sub(line):
        masked = re.sub(r"`+[^`]*`+", lambda m: "\0" * len(m.group()), line)  # skip inline code
        out, last = [], 0
        for m in EMPHASIS.finditer(masked):
            n, inner = len(m.group(1)), m.group(2)
            if not (punct(inner[0]) or punct(inner[-1])):
                continue
            open_tag, close_tag = EMPHASIS_TAGS[n]
            out += [line[last : m.start()], open_tag, line[m.start() + n : m.end() - n], close_tag]
            last = m.end()
        return "".join(out) + line[last:]

    lines, fence = text.split("\n"), None
    body = text.find("\n---\n", 4) + 5 if text.startswith("---\n") else 0  # skip frontmatter
    start = text[:body].count("\n")
    for i in range(start, len(lines)):
        stripped = lines[i].lstrip()
        if fence:
            fence = None if stripped.startswith(fence) else fence
        elif stripped.startswith(("```", "~~~")):
            fence = stripped[:3]
        else:
            lines[i] = line_sub(lines[i])
    return "\n".join(lines)


def write_if_changed(path, text):
    GENERATED.add(os.path.abspath(path))
    if os.path.exists(path) and open(path, encoding="utf-8").read() == text:
        return
    os.makedirs(os.path.dirname(path), exist_ok=True)
    open(path, "w", encoding="utf-8").write(text)


def copy_if_changed(src, dst):
    import filecmp
    GENERATED.add(os.path.abspath(dst))
    if os.path.exists(dst) and filecmp.cmp(src, dst, shallow=False):
        return
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    shutil.copyfile(src, dst)


# Re-encoded videos and files downloaded from the GitBook CDN can come out with different bytes on
# another machine (ffmpeg version, CDN re-compression). Record which source each committed output was
# made from, and keep the committed file while that source is unchanged, so runs stay reproducible.
DERIVED_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "derived-assets.json")
DERIVED = json.load(open(DERIVED_PATH)) if os.path.exists(DERIVED_PATH) else {}
DERIVED_USED = {}


def copy_derived(out, key, make):
    dst = os.path.join(DST, out)
    DERIVED_USED[out] = key
    # no record yet (first run with this file): adopt what is committed
    if os.path.exists(dst) and DERIVED.get(out, key) == key:
        GENERATED.add(os.path.abspath(dst))
        return
    copy_if_changed(make(), dst)


def remove_stale(dirs):
    """Delete files in generated folders that this run did not produce."""
    for d in dirs:
        root = os.path.join(DST, d)
        for cur, _, files in os.walk(root, topdown=False):
            for f in files:
                fp = os.path.abspath(os.path.join(cur, f))
                if fp not in GENERATED:
                    os.remove(fp)
            if not os.listdir(cur):
                os.rmdir(cur)


# ---------------------------------------------------------------- assets
asset_map = {}  # abs src asset -> url
MAGIC = [(b"\x89PNG", ".png"), (b"\xff\xd8", ".jpg"), (b"GIF8", ".gif"), (b"%PDF", ".pdf"), (b"<svg", ".svg"), (b"<?xml", ".svg")]


def asset_url(abs_path, sec_key):
    abs_path = os.path.normpath(abs_path)
    if abs_path in asset_map:
        return asset_map[abs_path]
    if not os.path.isfile(abs_path):
        WARN.append(f"missing asset: {abs_path}")
        return None
    name = os.path.basename(abs_path)
    stem, ext = os.path.splitext(name)
    if not ext or len(ext) > 5 or " " in ext:
        stem, ext = name, ""
        head = open(abs_path, "rb").read(64)
        for sig, e in MAGIC:
            if head.lstrip().startswith(sig):
                ext = e
                break
        if not ext and b"ftyp" in head[:12]:
            ext = ".mp4"
        if not ext and head[:4] == b"RIFF" and head[8:12] == b"WEBP":
            ext = ".webp"
    safe = re.sub(r"[^A-Za-z0-9._-]+", "-", stem).strip("-").lower()
    if not safe or not stem.isascii():
        safe = (safe + "-" if safe and stem.isascii() else "img-") + hashlib.sha1(stem.encode()).hexdigest()[:8]
    out = f"images/{sec_key}/{safe}{ext.lower()}"
    i = 1
    while out in asset_map.values():
        out = f"images/{sec_key}/{safe}-{i}{ext.lower()}"
        i += 1
    os.makedirs(os.path.join(DST, os.path.dirname(out)), exist_ok=True)
    if ext.lower() in (".mp4", ".mov", ".webm"):
        copy_derived(out, "sha1:" + hashlib.sha1(open(abs_path, "rb").read()).hexdigest(), lambda: shrink_video(abs_path))
    elif abs_path.startswith(CACHE + os.sep):
        copy_derived(out, "url:" + os.path.relpath(abs_path, CACHE), lambda: abs_path)
    else:
        copy_if_changed(abs_path, os.path.join(DST, out))
    asset_map[abs_path] = "/" + out
    return "/" + out


CACHE = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".cache")


def fetch_remote(url, src_file):
    """Download a GitBook-hosted file once (cached across runs) so the site does not depend on GitBook."""
    import urllib.request
    path = urllib.parse.urlparse(url).path
    name = urllib.parse.unquote(urllib.parse.unquote(path)).rsplit("/", 1)[-1]
    local = os.path.join(CACHE, hashlib.sha1(url.split("?")[0].encode()).hexdigest()[:10], name)
    if not os.path.exists(local):
        os.makedirs(os.path.dirname(local), exist_ok=True)
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            data = urllib.request.urlopen(req, timeout=120).read()
        except Exception as e:  # keep the remote url, but report it
            WARN.append(f"{src_file}: download failed {url[:100]} ({e})")
            return None
        if data.lstrip()[:1] in (b"{", b"<") and b"<svg" not in data[:2000]:
            WARN.append(f"{src_file}: download returned non-media content {url[:100]}")
            return None
        open(local, "wb").write(data)
    return local


# ---------------------------------------------------------------- link rewriting
def rewrite_url(url, src_file, sec_key):
    """Return (new_url, target_title_or_None)."""
    url = url.strip()
    if url.startswith("<") and url.endswith(">"):
        url = url[1:-1]
    url = re.sub(r"\\([!-/:-@\[-`{-~])", r"\1", url)  # markdown backslash escapes
    m = re.match(r"https://app\.gitbook\.com/(?:o/[^/]+/)?s/([A-Za-z0-9]+)/?(.*)$", url)
    if m:
        prefix = SPACE_IDS.get(m.group(1))
        if prefix is None:
            WARN.append(f"{src_file}: unknown gitbook space {url}")
            return url, None
        rest = m.group(2)
        return "/" + "/".join(x for x in (prefix, rest) if x), None
    if re.match(r"https://(files\.gitbook\.com|[0-9]+-files\.gitbook\.io)/", url):
        local = fetch_remote(html.unescape(url), src_file)
        if local:
            return asset_url(local, sec_key), None
        return url, None
    m = re.match(r"https?://ayakaleaf-pro\.ayaka\.space(/.*)?$", url)
    if m:
        path = m.group(1) or "/"
        if any(path == f"/{x}" or path.startswith(f"/{x}/") for x in UNPUBLISHED):
            return UNPUB, None
        return path, None
    if re.match(r"^[a-z]+:", url) or url.startswith("#") or url.startswith("/"):
        return url, None
    path, _, frag = url.partition("#")
    path = urllib.parse.unquote(path)
    target = os.path.normpath(os.path.join(os.path.dirname(src_file), path))
    if ".gitbook/assets/" in target.replace(os.sep, "/"):
        u = asset_url(target, sec_key)
        return (u or url), None
    if target.endswith(".md"):
        pg = pages.get(target)
        if pg is None:
            WARN.append(f"{src_file}: broken link {url}")
            return url, None
        if not pg["published"]:
            return UNPUB, pg["title"]
        return url_for(pg["id"]) + (("#" + frag) if frag else ""), pg["title"]
    if os.path.isdir(target) and os.path.join(target, "README.md") in pages:
        pg = pages[os.path.join(target, "README.md")]
        return url_for(pg["id"]) + (("#" + frag) if frag else ""), pg["title"]
    WARN.append(f"{src_file}: unresolved link {url}")
    return url, None


def _norm(t):
    return re.sub(r"[^a-z0-9]+", " ", strip_tags(t).lower()).strip()


def page_by_title(text):
    """Resolve GitBook '/broken/pages/<hash>' links by their label (exact title, then containment)."""
    want = _norm(text)
    if not want:
        return None
    published = [p for p in pages.values() if p["published"]]
    exact = [p for p in published if _norm(p["title"]) == want]
    if exact:
        return exact[0]
    near = [p for p in published if len(want) > 4 and (want in _norm(p["title"]) or _norm(p["title"]) in want and len(_norm(p["title"])) > 6)]
    return near[0] if len(near) == 1 else None


# ---------------------------------------------------------------- helpers
class Protect:
    def __init__(self):
        self.store = []

    def put(self, s):
        self.store.append(s)
        return f"\x00{len(self.store) - 1}\x00"

    def restore(self, s):
        for _ in range(5):
            n = re.sub(r"\x00(\d+)\x00", lambda m: self.store[int(m.group(1))], s)
            if n == s:
                break
            s = n
        return s


def attr_str(s):
    return s.replace("\\", "\\\\").replace('"', "&quot;")


def strip_tags(s):
    return html.unescape(re.sub(r"<[^>]+>", "", s)).strip()


def fa_icon(cls):
    m = re.search(r"\bfa-([a-z0-9-]+)", cls or "")
    return m.group(1) if m else None


class CellParser(HTMLParser):
    """Parse a GitBook <table> into rows of raw cell HTML plus header attributes."""

    def __init__(self):
        super().__init__(convert_charrefs=False)
        self.heads, self.rows, self.cur, self.depth, self.buf, self.in_cell = [], [], None, 0, [], False

    def handle_starttag(self, tag, attrs):
        if tag == "th":
            self.heads.append(dict(attrs))
        if tag == "tr":
            self.cur = []
        if tag == "td":
            self.in_cell, self.buf = True, []
            return
        if self.in_cell:
            self.buf.append(self.get_starttag_text())

    def handle_endtag(self, tag):
        if tag == "td":
            self.in_cell = False
            self.cur.append("".join(self.buf))
            return
        if tag == "tr" and self.cur:
            self.rows.append(self.cur)
            self.cur = None
            return
        if self.in_cell:
            self.buf.append(f"</{tag}>")

    def handle_data(self, d):
        if self.in_cell:
            self.buf.append(d)

    def handle_entityref(self, n):
        if self.in_cell:
            self.buf.append(f"&{n};")

    def handle_charref(self, n):
        if self.in_cell:
            self.buf.append(f"&#{n};")


def cards_table(tbl, src, sec):
    p = CellParser()
    p.feed(tbl)
    roles = []
    for h in p.heads:
        if "data-card-target" in h:
            roles.append("target")
        elif "data-card-cover" in h:
            roles.append("cover")
        else:
            roles.append("text")
    out = ["<CardGroup cols={3}>"]  # GitBook card views show three per row
    for row in p.rows:
        title, href, img, icon, body = None, None, None, None, []
        for role, cell in zip(roles, row):
            a = re.search(r'href="([^"]*)"', cell)
            if role == "target":
                if a:
                    href = a.group(1)
            elif role == "cover":
                if a:
                    img = a.group(1)
            else:
                ic = re.search(r'<i class="([^"]*)"', cell)
                if ic and not icon:
                    icon = fa_icon(ic.group(1))
                cell = re.sub(r"<i [^>]*>.*?</i>", "", cell)
                cell = re.sub(r'<span class="math">([\s\S]*?)</span>', lambda mm: "\x01" + html.unescape(mm.group(1)) + "\x01", cell)
                txt = strip_tags(cell)
                if not txt:
                    continue
                if title is None:
                    title = txt
                    if a and not href:
                        href = a.group(1)
                else:
                    body.append(txt)
        # titles are plain strings: math can't render there
        title = re.sub(r"\x01([^\x01]*)\x01", lambda mm: re.sub(r"\\(La)?TeX", lambda t: (t.group(1) or "") + "TeX", mm.group(1)).strip(), title or "")
        attrs = [f'title="{attr_str(title)}"']
        if icon:
            attrs.append(f'icon="{icon}"')
        if href:
            attrs.append(f'href="{attr_str(rewrite_url(href, src, sec)[0])}"')
        if img:
            u = rewrite_url(img, src, sec)[0]
            attrs.append(f'img="{attr_str(u)}"')
        out.append(f"  <Card {' '.join(attrs)}>")
        if body:
            text = " ".join(body).replace("{", "&#123;").replace("}", "&#125;").replace("<", "&lt;")
            text = re.sub(r"\x01([^\x01]*)\x01", lambda mm: "$" + mm.group(1) + "$", text)
            out.append("    " + text)
        out.append("  </Card>")
    out.append("</CardGroup>")
    return "\n".join(out)


def fence_from_pre(attrs, inner):
    lang = re.search(r'class="language-([^"\s]+)', attrs)
    title = re.search(r'data-title="([^"]*)"', attrs)
    code = html.unescape(re.sub(r"</?(?:code|span|mark|strong|em|a)\b[^>]*>", "", inner))
    meta = [lang.group(1) if lang else "text"]
    if title:
        meta.append(f'title="{html.unescape(title.group(1))}"')
    if 'data-overflow="wrap"' in attrs:
        meta.append("wrap")
    if 'data-expandable="true"' in attrs:
        meta.append("expandable")
    code = code.rstrip("\n")
    fence = "````" if "```" in code else "```"
    return f"{fence}{' '.join(meta)}\n{code}\n{fence}"


# ---------------------------------------------------------------- page conversion
def convert(src, info):
    sec = info["section"]
    fm, body = split_fm(open(src, encoding="utf-8").read())
    P = Protect()
    vis = {k: v.get("visible", True) for k, v in (fm.get("layout") or {}).items() if isinstance(v, dict)}
    landing = vis.get("tableOfContents") is False and vis.get("title") is False

    # H1 becomes the frontmatter title
    body = re.sub(r"\A\s*# .*\n", "", body, count=1)

    # {% code %} wrappers -> fence meta
    def code_dir(m):
        params = dict(re.findall(r'(\w+)="([^"]*)"', m.group(1)))
        fence = m.group(2)
        first, rest = fence.split("\n", 1)
        ind, tick, lang = re.match(r"(\s*)(`{3,}|~{3,})\s*(\S*)", first).groups()
        meta = [lang or "text"]
        if params.get("title"):
            meta.append(f'title="{params["title"]}"')
        if params.get("overflow") == "wrap":
            meta.append("wrap")
        if params.get("lineNumbers") == "true":
            meta.append("lines")
        if params.get("expandable") == "true":
            meta.append("expandable")
        return f"{ind}{tick}{' '.join(meta)}\n{rest}"

    lines, out, i = body.split("\n"), [], 0
    while i < len(lines):
        m = re.match(r"^([ \t>]*)\{%\s*code([^%]*)%\}\s*$", lines[i])
        f = re.match(r"^([ \t>]*)(`{3,}|~{3,})\s*(\S*)\s*$", lines[i + 1]) if m and i + 1 < len(lines) else None
        if not f:
            out.append(lines[i])
            i += 1
            continue
        params = dict(re.findall(r'(\w+)="([^"]*)"', m.group(2)))
        meta = [f.group(3) or "text"]
        if params.get("title"):
            meta.append(f'title="{params["title"]}"')
        if params.get("overflow") == "wrap":
            meta.append("wrap")
        if params.get("lineNumbers") == "true":
            meta.append("lines")
        if params.get("expandable") == "true":
            meta.append("expandable")
        out.append(f"{f.group(1)}{f.group(2)}{' '.join(meta)}")
        j = i + 2
        while j < len(lines) and not re.match(r"^[ \t>]*" + f.group(2) + r"\s*$", lines[j]):
            out.append(lines[j])
            j += 1
        out.append(lines[j] if j < len(lines) else "")
        j += 1
        while j < len(lines) and not lines[j].strip(" \t>"):
            j += 1
        if j < len(lines) and re.match(r"^[ \t>]*\{%\s*endcode\s*%\}\s*$", lines[j]):
            j += 1
        else:
            WARN.append(f"{src}: code directive without endcode")
        i = j
    body = "\n".join(out)
    if "{% code" in body:
        WARN.append(f"{src}: unmatched code directive")

    # raw <pre><code> blocks -> fences
    body = re.sub(r"(?m)^([ \t]*)<pre([^>]*)>(?:\s*)<code[^>]*>([\s\S]*?)</code></pre>",
                  lambda m: m.group(1) + P.put(fence_from_pre(m.group(2), m.group(3)).replace("\n", "\n" + m.group(1))), body)

    # protect fenced code
    def prot_fence(m):
        block = m.group(0)
        first = block.split("\n", 1)[0]
        if re.match(r"\s*(`{3,}|~{3,})\s*$", first):  # no language -> text
            block = first.rstrip() + "text" + block[len(first):]
        return P.put(block)

    body = re.sub(r"(?m)^([ \t]*)(`{3,}|~{3,})[^\n]*\n[\s\S]*?\n\1?[ \t]*\2[ \t]*$", prot_fence, body)

    # cards tables (need links rewritten, do before generic link pass)
    body = re.sub(r'<table data-view="cards"[\s\S]*?</table>', lambda m: P.put(cards_table(m.group(0), src, sec)), body)

    def html_code(m):
        inner = html.unescape(m.group(1))
        tick = "``" if "`" in inner else "`"
        return P.put(f"{tick} {inner} {tick}" if tick == "``" else f"`{inner}`")

    body = re.sub(r"<code>([^<\n`]*)</code>", html_code, body)

    # protect inline code
    body = re.sub(r"(`+)(?!`)((?:(?!\n[ \t]*\n)[\s\S])*?[^`])\1(?!`)", lambda m: P.put(m.group(0)), body)

    # math: GitBook uses $$..$$ for both inline and block
    def math_sub(text):
        out, i = [], 0
        for m in re.finditer(r"\$\$((?:(?!\n[ \t]*\n)[\s\S])+?)\$\$", text):  # never across paragraphs
            out.append(text[i:m.start()])
            expr = m.group(1)
            ls = text.rfind("\n", 0, m.start()) + 1
            le = text.find("\n", m.end())
            le = len(text) if le == -1 else le
            alone = text[ls:m.start()].strip() in ("", "*", "-") and not text[m.end():le].strip()
            block = alone and (text[ls:m.start()].strip() == "")
            out.append(P.put(f"$${expr}$$" if block or "$" in expr else f"${expr.strip()}$"))
            i = m.end()
        out.append(text[i:])
        return "".join(out)

    body = math_sub(body)
    body = re.sub(r'<span class="math">([\s\S]*?)</span>', lambda m: P.put(f"${html.unescape(m.group(1))}$"), body)
    body = re.sub(r"(?<!\\)\$", r"\\$", body)
    body = re.sub(r"(?<![~\\])~(?!~)", r"\\~", body)

    # GitBook directives
    body = re.sub(r'\{%\s*hint style="(\w+)"\s*%\}', lambda m: f"<{HINTS.get(m.group(1), 'Note')}>\n", body)
    def fix_hints(text):
        out, opened = [], []
        for tok in re.split(r"(<(?:Info|Check|Warning|Danger|Tip|Note)>\n|\{%\s*endhint\s*%\})", text):
            m = re.match(r"<(\w+)>\n", tok)
            if m and m.group(1) in set(HINTS.values()) | {"Note"}:
                opened.append(m.group(1))
                out.append(tok)
            elif re.match(r"\{%\s*endhint", tok):
                out.append(f"\n</{opened.pop() if opened else 'Note'}>")
            else:
                out.append(tok)
        return "".join(out)

    body = fix_hints(body)

    def step_block(m):
        inner = m.group(1)
        h = re.match(r"\s*#{1,6} (.+)\n", inner)
        title = ""
        if h:
            title = re.sub(r"\s*<a [^>]*></a>", "", h.group(1)).strip()
            inner = inner[h.end():]
        return f'<Step title="{attr_str(strip_tags(title))}">\n{inner.strip()}\n</Step>'

    while True:
        n = re.sub(r"\{%\s*step\s*%\}((?:(?!\{%\s*(?:end)?step\s*%\})[\s\S])*?)\{%\s*endstep\s*%\}", step_block, body)
        if n == body:
            break
        body = n
    body = re.sub(r"\{%\s*stepper\s*%\}", "<Steps>", body)
    body = re.sub(r"\{%\s*endstepper\s*%\}", "</Steps>", body)
    body = re.sub(r'\{%\s*tabs\s*%\}', "<Tabs>", body)
    body = re.sub(r'\{%\s*endtabs\s*%\}', "</Tabs>", body)
    body = re.sub(r'\{%\s*tab title="([^"]*)"\s*%\}', lambda m: f'<Tab title="{attr_str(m.group(1))}">\n', body)
    body = re.sub(r'\{%\s*endtab\s*%\}', "\n</Tab>", body)

    def columns(m):
        inner = m.group(1)
        n = len(re.findall(r"\{%\s*column\b", inner))
        inner = re.sub(r"\{%\s*column(?:[^%]|%(?!\}))*%\}", "<Column>\n", inner)
        inner = re.sub(r"\{%\s*endcolumn\s*%\}", "\n</Column>", inner)
        cols = f"<Columns cols={{{max(1, min(n, 4))}}}>\n{inner.strip()}\n</Columns>"
        # landing pages stack several column blocks; give each room like GitBook does
        return f'<div style={{{{ margin: "4rem 0" }}}}>\n\n{cols}\n\n</div>' if landing else cols

    body = re.sub(r"\{%\s*columns(?:[^%]|%(?!\}))*%\}([\s\S]*?)\{%\s*endcolumns\s*%\}", columns, body)

    def content_ref(m):
        url = m.group(1)
        new, title = rewrite_url(url, src, sec)
        inner_link = re.search(r"\[([^\]]*)\]", m.group(2))
        t = title or (inner_link.group(1) if inner_link else new)
        return P.put(f'<Card title="{attr_str(t)}" icon="file-lines" href="{attr_str(new)}" horizontal />')

    body = re.sub(r'\{%\s*content-ref url="([^"]*)"\s*%\}([\s\S]*?)\{%\s*endcontent-ref\s*%\}', content_ref, body)

    def embed(m):
        url = rewrite_url(html.unescape(m.group(1)), src, sec)[0]
        caption = (m.group(2) or "").strip()
        yt = re.match(r"https?://(?:www\.)?youtube\.com/watch\?v=([\w-]+)", url) or re.match(r"https?://youtu\.be/([\w-]+)", url)
        if yt:
            out = (f'<iframe className="w-full aspect-video rounded-xl" src="https://www.youtube.com/embed/{yt.group(1)}" '
                   f'title="YouTube video player" frameBorder="0" allow="accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture" allowFullScreen />')
        elif re.search(r"videos\.ctfassets\.net|\.mp4|\.webm|\.mov", url, re.I):
            out = f'<video autoPlay muted loop playsInline controls className="w-full aspect-video rounded-xl" src="{attr_str(url)}" />'  # browsers only autoplay muted video
        else:
            label = caption or urllib.parse.urlparse(url).netloc
            out = f'<Card title="{attr_str(label)}" icon="arrow-up-right-from-square" href="{attr_str(url)}" horizontal />'
            caption = ""
        if caption:
            out = f'<Frame caption="{attr_str(strip_tags(caption))}">\n  {out}\n</Frame>'
        return P.put(out)

    body = re.sub(r'\{%\s*embed url="([^"]*)"(?:[^%]|%(?!\}))*%\}(?:([\s\S]*?)\{%\s*endembed\s*%\})?', embed, body)

    def file_dir(m):
        new, _ = rewrite_url(m.group(1), src, sec)
        cap = (m.group(2) or "").strip() or os.path.basename(urllib.parse.unquote(m.group(1)))
        return P.put(f'<Card title="{attr_str(strip_tags(cap))}" icon="file-arrow-down" href="{attr_str(new)}" horizontal />')

    body = re.sub(r'\{%\s*file src="([^"]*)"(?:[^%]|%(?!\}))*%\}(?:([\s\S]*?)\{%\s*endfile\s*%\})?', file_dir, body)
    if "{%" in body:
        for x in set(re.findall(r"\{%(?:[^%]|%(?!\}))*%\}", body)):
            WARN.append(f"{src}: leftover directive {x}")

    # raw HTML hint blocks
    def gb_hint(m):
        c = HINTS.get(m.group(1), "Note")
        inner = re.sub(r"</?p>", "", m.group(2))
        return f"<{c}>{inner}</{c}>"

    body = re.sub(r'<div data-gb-custom-block data-tag="hint" data-style="(\w+)"[^>]*>([\s\S]*?)</div>', gb_hint, body)

    # figures
    def figure(m):
        inner = m.group(1)
        cap = re.search(r"<figcaption>([\s\S]*?)</figcaption>", inner)
        cap = strip_tags(cap.group(1)) if cap else ""
        imgs = re.findall(r"<img [^>]*>", inner)
        if not imgs:
            WARN.append(f"{src}: figure without img")
            return inner
        img = imgs[0]
        if cap:
            return f'<Frame caption="{attr_str(cap)}">\n  {img}\n</Frame>'
        return f"<Frame>\n  {img}\n</Frame>"

    body = re.sub(r"<figure>([\s\S]*?)</figure>", figure, body)
    body = re.sub(r'<div[^>]*data-with-frame="true"[^>]*>\s*([\s\S]*?)\s*</div>',
                  lambda m: m.group(1).strip() if m.group(1).strip().startswith("<Frame") else f"<Frame>\n  {m.group(1).strip()}\n</Frame>", body)

    # details/summary -> Accordion
    def details(m):
        open_ = "open" in m.group(1)
        title = strip_tags(m.group(2)).replace("`", "")
        return f'<Accordion title="{attr_str(title)}"{" defaultOpen" if open_ else ""}>'

    body = re.sub(r"<details([^>]*)>\s*<summary>([\s\S]*?)</summary>", details, body)
    body = body.replace("</details>", "</Accordion>")

    # inline decorations
    body = re.sub(r'<i class="([^"]*)"[^>]*>[^<]*</i>', lambda m: f'<Icon icon="{fa_icon(m.group(1))}" />' if fa_icon(m.group(1)) else "", body)
    body = re.sub(r"<mark[^>]*>([\s\S]*?)</mark>", r"\1", body)

    # GitBook buttons
    def button(m):
        attrs = m.group(1)
        href = re.search(r'href="([^"]*)"', attrs).group(1)
        kind = "primary" if "primary" in attrs else "secondary"
        icon = re.search(r'data-icon="([^"]*)"', attrs)
        new, _ = rewrite_url(html.unescape(href), src, sec)
        ic = f'<Icon icon="{icon.group(1)}" color="currentColor" /> ' if icon else ""
        return f'<a href="{attr_str(new)}" className="{BUTTON_CLASSES[kind]}">{ic}{m.group(2)}</a>'

    body = re.sub(r'<a ([^>]*class="button[^"]*"[^>]*)>([\s\S]*?)</a>', button, body)

    # heading anchors injected by GitBook
    body = re.sub(r'\s*<a href="#[^"]*" id="[^"]*"></a>', "", body)

    # comments
    body = re.sub(r"<!--([\s\S]*?)-->", lambda m: P.put("{/*" + m.group(1).replace("*/", "* /") + "*/}"), body)

    # markdown links / images
    def md_link(m):
        bang, text, url, ttl = m.group(1), m.group(2), m.group(3), m.group(4)
        if url.startswith("\x00"):
            return m.group(0)
        if re.match(r"^(\.\./)*/?broken/pages/", url):
            hit = page_by_title(text)
            if hit:
                return f"[{text}](" + P.put(url_for(hit["id"])) + ")"
            NOTES.append(f"{src}: link already broken in GitBook [{text}], kept as text")
            return text
        new, title = rewrite_url(url, src, sec)
        if new == UNPUB:
            NOTES.append(f"{src}: link into unpublished section kept as text [{text or title}]")
            return text or title or ""
        if not bang and ttl and ttl.strip('"') == "mention":
            ttl = None
            if title and (not text.strip() or text.strip() == url.strip("<>")):
                text = title
        if not bang and not text.strip():
            text = title or new
        if bang and re.fullmatch(r"\{+\s*alt\s*\}+", text.strip()):
            text = ""
        for ch in ' ()<>{}\\':
            new = new.replace(ch, "%{:02X}".format(ord(ch)))
        return f"{bang}[{text}](" + P.put(new + ((" " + ttl) if ttl else "")) + ")"

    body = re.sub(r'(!)\[([^\[\]]*)\]\((<[^>]*>|(?:\\.|[^()\s\\]|\((?:\\.|[^()\s\\])*\))+)(\s+"[^"]*")?\)', md_link, body)
    body = re.sub(r'(?<!!)()\[((?:[^\[\]]|\[[^\]]*\])*)\]\((<[^>]*>|(?:\\.|[^()\s\\]|\((?:\\.|[^()\s\\])*\))+)(\s+"[^"]*")?\)', md_link, body)
    # autolinks are not valid MDX
    body = re.sub(r"<(https?://[^>\s]+)>", r"[\1](\1)", body)

    # HTML attribute urls
    def attr_url(m):
        new, _ = rewrite_url(html.unescape(m.group(2)), src, sec)
        return f'{m.group(1)}="{attr_str(new)}"'

    body = re.sub(r'\b(href|src)="([^"]*)"', attr_url, body)

    # HTML tags: whitelist + JSX-ify; everything else is literal text
    def tag(m):
        full, close, name, attrs = m.group(0), m.group(1), m.group(2), m.group(3) or ""
        lname = name.lower()
        if name[0].isupper() and name in {"Info", "Check", "Warning", "Danger", "Tip", "Note", "Steps", "Step", "Tabs", "Tab",
                                           "Columns", "Column", "Card", "CardGroup", "Frame", "Accordion", "Icon"}:
            return P.put(full)
        if lname not in HTML_TAGS or name != lname:
            return full.replace("<", "&lt;").replace(">", "&gt;")
        if close:
            return P.put("</table></div>" if lname == "table" else f"</{lname}>")
        attrs = re.sub(r'\s(data-[\w-]+|valign)(="[^"]*")?', "", attrs)
        attrs = re.sub(r'\sstyle="[^"]*"', "", attrs)
        # Mintlify drops align on headings; use Tailwind instead
        al = re.search(r'\salign="(center|right|left)"', attrs) if lname in {"h1", "h2", "h3", "h4", "h5", "h6", "p", "div", "td", "th"} else None
        if al:
            attrs = attrs.replace(al.group(0), "") + ' style={{ textAlign: "%s" }}' % al.group(1)
        attrs = re.sub(r"\sclass=", " className=", attrs)
        attrs = re.sub(r"\s(open)(?=[\s/>]|$)", "", attrs) if lname == "details" else attrs
        if lname == "table":
            # GitBook tables span the content width; prose makes tables display:block, which stops
            # columns from stretching, so scroll in a wrapper instead
            attrs += ' style={{ display: "table", width: "100%" }}'
        attrs = attrs.rstrip().rstrip("/").rstrip()
        selfclose = " /" if lname in VOID else ""
        if lname == "table":
            return P.put(f'<div style={{{{ overflowX: "auto" }}}}><table{attrs}>')
        return P.put(f"<{lname}{attrs}{selfclose}>")

    body = re.sub(r"<(/?)([A-Za-z][A-Za-z0-9]*)(\s[^<>]*?)?\s*/?>", tag, body)
    # restore </Hint> closings (protected via tag()) and escape stray characters
    body = body.replace("<", "&lt;")
    body = re.sub(r"(?<!\\)([{}])", r"\\\1", body)

    body = P.restore(body)
    body = re.sub(r"\n{3,}", "\n\n", body).strip() + "\n"

    out_fm = {"title": info["title"]}
    if fm.get("description"):
        out_fm["description"] = " ".join(str(fm["description"]).split())
    if fm.get("icon"):
        out_fm["icon"] = nav_icon(fm["icon"])
    # GitBook layout toggles -> closest Mintlify page mode
    if landing:
        # only custom mode hides the title; restore width and typography with a wrapper
        out_fm["mode"] = "custom"
        body = ('<div className="prose dark:prose-invert" style={{ maxWidth: "56rem", margin: "0 auto", padding: "2.5rem 1.25rem" }}>\n\n'
                + body.strip() + "\n\n</div>\n")
    elif vis.get("tableOfContents") is False:
        out_fm["mode"] = "center"  # no left sidebar (Mintlify cannot keep the outline without it)
    elif vis.get("outline") is False or (fm.get("layout") or {}).get("width") == "wide":
        out_fm["mode"] = "wide"
    if fm.get("tags"):
        out_fm["keywords"] = fm["tags"]
    if fm.get("hidden"):
        out_fm["noindex"] = True
    return "---\n" + yaml.safe_dump(out_fm, allow_unicode=True, sort_keys=False, width=1000) + "---\n\n" + body


# ---------------------------------------------------------------- navigation
def summary_tree(base):
    lines = open(os.path.join(base, "SUMMARY.md"), encoding="utf-8").read().splitlines()
    groups, cur, stack = [], None, []
    for ln in lines:
        h = re.match(r"^##\s+(.+)", ln)
        if h:
            cur = {"group": h.group(1).strip(), "items": []}
            groups.append(cur)
            stack = []
            continue
        m = re.match(r"^(\s*)[*-]\s+\[([^\]]*)\]\(([^)]*)\)", ln)
        if not m:
            continue
        if cur is None:
            cur = {"group": None, "items": []}
            groups.append(cur)
        node = {"label": m.group(2).strip(), "target": urllib.parse.unquote(m.group(3)), "children": []}
        indent = len(m.group(1).expandtabs(2))
        while stack and stack[-1][0] >= indent:
            stack.pop()
        (stack[-1][1]["children"] if stack else cur["items"]).append(node)
        stack.append((indent, node))
    return groups


listed = set()


def node_to_nav(n, key, base=None):
    if re.match(r"^[a-z]+:", n["target"]):
        NOTES.append(f"SUMMARY {key}: skipped external sidebar link {n['label']} -> {n['target']}")
        return None
    sec = dict((s[0], s) for s in SECTIONS)[key]
    base = os.path.join(SRC, sec[2])
    abs_t = os.path.normpath(os.path.join(base, n["target"]))
    pg = pages.get(abs_t)
    if not pg:
        WARN.append(f"SUMMARY {key}: unresolved {n['target']}")
        return None
    listed.add(abs_t)
    if pg["hidden"]:
        return None  # GitBook `hidden: true`: reachable by link, never in the sidebar (children included)
    if n["label"] != pg["title"]:
        pg["sidebar"] = n["label"]
    if not n["children"]:
        return pg["id"]
    kids = [x for x in (node_to_nav(c, key) for c in n["children"]) if x]
    group = {"group": n["label"], "root": pg["id"], "pages": kids}
    if pg.get("icon"):
        group["icon"] = nav_icon(pg["icon"])  # Mintlify shows the group's icon, not the root page's
    return group


def main():
    # clean Mintlify starter kit content
    for f in ["quickstart.mdx", "tester-02.mdx"]:
        if os.path.exists(os.path.join(DST, f)):
            os.remove(os.path.join(DST, f))
    shutil.rmtree(os.path.join(DST, "cn"), ignore_errors=True)

    tabs = []
    for key, title, d, prefix, icon, _ in SECTIONS:
        if key in UNPUBLISHED:
            continue
        base = os.path.join(SRC, d)
        groups = []
        for g in summary_tree(base):
            items = [x for x in (node_to_nav(n, key) for n in g["items"]) if x]
            if g["group"] is None:
                # leading entries before the first heading
                gname = "Overview" if key != "home" else "Home"
            else:
                gname = g["group"][:1].upper() + g["group"][1:]
            if (key, g["group"]) in HIDDEN_GROUPS:
                continue
            groups.append({"group": gname, "pages": items})
        tabs.append({"tab": title, "icon": icon, "groups": groups})

    unlisted = []
    for src, info in sorted(pages.items()):
        if not info["published"]:
            continue
        out = convert(src, info)
        if info.get("sidebar"):
            out = out.replace("---\n\n", f"sidebarTitle: {json.dumps(info['sidebar'], ensure_ascii=False)}\n---\n\n", 1)
        write_if_changed(os.path.join(DST, info["id"] + ".mdx"), translation_safe_emphasis(add_icon_types(out)))
        if src not in listed:
            unlisted.append(info["id"])

    cfg_path = os.path.join(DST, "docs.json")
    cfg = json.load(open(cfg_path, encoding="utf-8"))
    nav = cfg.get("navigation", {})
    # With Mintlify translations, navigation is per language; only the default (English) one is ours.
    langs = nav.get("languages")
    target = next((l for l in langs if l.get("default")), langs[0]) if langs else nav
    target["tabs"] = tabs
    cfg["navigation"] = nav
    # GitBook also served every page under its explicit variant slug (e.g. /on-premises/en/...)
    redirects = [{"source": "/home", "destination": "/"}]
    for retired, kept in RETIRED_LANGUAGES.items():
        redirects.append({"source": f"/{retired}", "destination": f"/{kept}"})
        redirects.append({"source": f"/{retired}/:slug*", "destination": f"/{kept}/:slug*"})
    for key, _, _, prefix, _, variant in SECTIONS:
        if prefix and key not in UNPUBLISHED:
            redirects.append({"source": f"/{prefix}/{variant}", "destination": f"/{prefix}"})
            redirects.append({"source": f"/{prefix}/{variant}/:slug*", "destination": f"/{prefix}/:slug*"})
    cfg["redirects"] = redirects
    for t in tabs:
        icon_type(t["icon"])
    write_if_changed(cfg_path, json.dumps(cfg, indent=2, ensure_ascii=False) + "\n")
    write_if_changed(DERIVED_PATH, json.dumps(dict(sorted(DERIVED_USED.items())), indent=0) + "\n")
    open(ICON_CACHE, "w").write(json.dumps(dict(sorted(_icon_types.items())), indent=0) + "\n")
    remove_stale(["images"] + [s[3] for s in SECTIONS if s[3]])

    print(f"pages: {sum(p['published'] for p in pages.values())} published, {len(pages)} indexed  assets: {len(asset_map)}  hidden (not in SUMMARY.md): {len(unlisted)}")
    for u in unlisted:
        print("  hidden:", u)
    for n in NOTES:
        print("NOTE", n)
    for w in WARN:
        print("WARN", w)
    if ARGS.strict and WARN:
        sys.exit(1)


if __name__ == "__main__":
    main()
