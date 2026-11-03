"""Static HTML site, Atom, JSON Feed 1.1, changes.json, posts.json/posts.txt. No template engine, no JS."""
from __future__ import annotations

import html
import json
import os
import re
from datetime import datetime, timezone
from xml.sax.saxutils import escape as xesc

from . import __version__
from .analyze import CONST_LIKE


def esc(s) -> str:
    return html.escape(str(s), quote=True)


def inline_md(s: str) -> str:
    """Escape then apply `code`, **bold**, [text](url). Code spans are protected first."""
    codes: list[str] = []

    def stash(m: re.Match) -> str:
        codes.append(f"<code>{esc(m.group(1))}</code>")
        return f"\x00{len(codes) - 1}\x00"

    t = esc(re.sub(r"`([^`]+)`", stash, s))
    t = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", t)
    t = re.sub(r"\[([^\]]+)\]\((https?://[^)\s]+)\)", r'<a href="\2">\1</a>', t)
    return re.sub(r"\x00(\d+)\x00", lambda m: codes[int(m.group(1))], t)


def markdown(md: str) -> str:
    out, para, in_list = [], [], False

    def flush():
        nonlocal para
        if para:
            out.append("<p>" + inline_md(" ".join(para)) + "</p>")
            para = []

    for line in md.splitlines():
        s = line.rstrip()
        if not s.strip():
            flush()
            if in_list:
                out.append("</ul>"); in_list = False
            continue
        m = re.match(r"^(#{2,4})\s+(.*)", s)
        if m:
            flush()
            if in_list:
                out.append("</ul>"); in_list = False
            lvl = min(len(m.group(1)) + 1, 5)
            out.append(f"<h{lvl}>{inline_md(m.group(2))}</h{lvl}>")
            continue
        m = re.match(r"^\s*[-*]\s+(.*)", s)
        if m:
            flush()
            if not in_list:
                out.append("<ul>"); in_list = True
            out.append(f"<li>{inline_md(m.group(1))}</li>")
            continue
        if in_list and line.startswith("  "):
            out[-1] = out[-1][:-5] + " " + inline_md(s.strip()) + "</li>"
            continue
        para.append(s.strip())
    flush()
    if in_list:
        out.append("</ul>")
    return "\n".join(out)


def _dt(iso: str) -> datetime:
    return datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone(timezone.utc)


def fmt_date(iso: str) -> str:
    d = _dt(iso)
    return f"{d:%b} {d.day}, {d.year}"


def iso_utc(iso: str) -> str:
    return _dt(iso).strftime("%Y-%m-%dT%H:%M:%SZ")


def _table(headers: list[str], rows: list[list[str]], cls: str = "") -> str:
    if not rows:
        return ""
    th = "".join(f"<th>{h}</th>" for h in headers)
    trs = "".join("<tr>" + "".join(f"<td>{c}</td>" for c in r) + "</tr>" for r in rows)
    return f'<div class="tw"><table class="{cls}"><thead><tr>{th}</tr></thead><tbody>{trs}</tbody></table></div>'


class Site:
    def __init__(self, cfg: dict, meta: dict):
        self.cfg, self.meta = cfg, meta
        s = cfg["site"]
        self.title = s.get("title") or f"{cfg['name']} changelog"
        self.tagline = s.get("tagline") or ""
        self.url = (s.get("url") or "").rstrip("/") + "/" if s.get("url") else ""
        self.repo_url = cfg.get("repo_url") or ""

    # -- layout ---------------------------------------------------------------
    def page(self, title: str, body: str, depth: int = 0, description: str = "") -> str:
        root = "../" * depth
        s = self.cfg["site"]
        disc = f'<p class="disclaimer">{inline_md(s["disclaimer"])}</p>' if s.get("disclaimer") else ""
        upstream = f' <a href="{esc(self.repo_url)}">Repository</a>' if self.repo_url else ""
        head = self.meta["head_sha"]
        head_link = (f'<a href="{esc(self.repo_url)}/commit/{esc(head)}"><code>{esc(head[:7])}</code></a>' if self.repo_url
                     else f"<code>{esc(head[:7])}</code>")
        proj = f' Site source: <a href="{esc(s["project_url"])}">{esc(s["project_url"].split("github.com/")[-1])}</a>.' if s.get("project_url") else ""
        return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{esc(title)}</title>
<meta name="description" content="{esc(description or self.tagline)}">
<meta name="generator" content="knoblog {__version__}">
<link rel="stylesheet" href="{root}style.css">
<link rel="alternate" type="application/atom+xml" title="{esc(self.title)}" href="{root}feed.xml">
<link rel="alternate" type="application/feed+json" title="{esc(self.title)}" href="{root}feed.json">
</head>
<body>
<header class="top">
  <a class="brand" href="{root}index.html">{esc(self.title)}</a>
  <nav><a href="{root}index.html">Changelog</a> <a href="{root}params.html">Parameters</a> <a href="{root}posts.html">Post text</a> <a href="{root}feed.xml">Atom</a> <a href="{root}feed.json">JSON</a>{upstream}</nav>
</header>
{disc}
<main>
{body}
</main>
<footer>
  <p>Generated {esc(self.meta['generated_at'])} from {head_link} by <a href="https://github.com/fitzyracing1/knoblog">knoblog</a> {__version__}.{proj}
  Every value is read from the repository diff; the diff is the source of truth.</p>
</footer>
</body>
</html>
"""

    # -- pieces ---------------------------------------------------------------
    def entry_link(self, e: dict) -> str:
        return f"{self.url}entries/{e['short']}.html" if self.url else ""

    @staticmethod
    def summary_html(e: dict) -> str:
        s = e["summary"]
        if s["source"] in ("note", "llm"):
            return markdown(s["markdown"])
        items = "".join(f"<li>{inline_md(b)}</li>" for b in s["bullets"])
        return f"<ul>{items}</ul>" if items else ""

    def card(self, e: dict, prefix: str = "entries/") -> str:
        s = e["summary"]
        tags = "".join(f'<span class="tag">{esc(t)}</span>' for t in s["tags"])
        src = {"note": "hand-written note, checked against the diff", "llm": "LLM-written from the analysis",
               "auto": "automatic analysis"}[s["source"]]
        st = e["stats"]
        commit = f'<a href="{esc(e["url"])}"><code>{esc(e["short"])}</code></a>' if e.get("url") else f"<code>{esc(e['short'])}</code>"
        return f"""<article class="entry" id="{esc(e['short'])}">
  <div class="meta"><time datetime="{esc(e['date'])}">{fmt_date(e['date'])}</time> · {commit}
    · {st['files']:,} watched files, +{st['lines_added']:,}/−{st['lines_deleted']:,}{' · merge' if e['is_merge'] else ''}</div>
  <h2><a href="{prefix}{esc(e['short'])}.html">{inline_md(s['title'])}</a></h2>
  <div class="tags">{tags}</div>
  <div class="summary">{self.summary_html(e)}</div>
  <p class="more"><a href="{prefix}{esc(e['short'])}.html">Full analysis →</a> <span class="src">Summary: {src}</span></p>
</article>"""

    def blob(self, e: dict, path: str) -> str:
        if not self.repo_url:
            return esc(path)
        return f'<a href="{esc(self.repo_url)}/blob/{esc(e["sha"])}/{esc(path)}">{esc(path)}</a>'

    @staticmethod
    def pct_cell(c: dict) -> str:
        p = c.get("pct")
        if p is None:
            return ""
        cls = "up" if p > 0 else "down"
        return f'<span class="{cls}">{"+" if p > 0 else "−"}{abs(p):.3g}%</span>'

    def detail(self, e: dict, prev: dict | None, nxt: dict | None, post: dict | None) -> str:
        code = lambda s: f"<code>{esc(s)}</code>" if s is not None else "—"
        parts = [self.card(e, prefix="")]
        w = e.get("why")
        if w:
            pr = (f" · <a href='{esc(w['pr_url'])}'>PR #{w['pr_number']}</a>" if w.get("pr_url") else
                  (f" · PR #{w['pr_number']}" if w.get("pr_number") else ""))
            parts.append(f"<h3>Why it changed</h3><p><strong>{esc(w['subject'])}</strong>{pr}</p>" +
                         (f"<blockquote>{esc(w['message'])}</blockquote>" if w.get("message") else ""))
        if e["summary"]["source"] != "auto" and e["auto"]["bullets"]:
            parts.append("<h3>Automatic analysis</h3><ul>" + "".join(f"<li>{inline_md(b)}</li>" for b in e["auto"]["bullets"]) + "</ul>")
        ch = e["changes"]
        params = [c for c in ch if c["extractor"] not in CONST_LIKE]
        consts = [c for c in ch if c["extractor"] in CONST_LIKE]
        name_cell = lambda c: code(c["name"]) + (f"<br><span class='muted'>key {esc(c['key'])}</span>" if c.get("key") else "")
        rows = [[name_cell(c), code(c["old"]), code(c["new"]), self.pct_cell(c), self.blob(e, c["file"]), esc(c["extractor"])]
                for c in params if c["kind"] in ("changed", "moved") and c["old"] != c["new"]]
        parts.append("<h3>Changed values</h3>" + (_table(["Name", "Old", "New", "Change", "File", "Extractor"], rows) or "<p class='muted'>None.</p>"))
        added = [c for c in params if c["kind"] == "added"]
        if added:
            parts.append(f"<h3>Added ({len(added)})</h3>" + _table(["Name", "Value", "Type", "File"],
                         [[name_cell(c), code(str(c["new"])[:80]), esc(c["type"]), self.blob(e, c["file"])] for c in added[:300]]) +
                         (f"<p class='muted'>…and {len(added) - 300} more.</p>" if len(added) > 300 else ""))
        removed = [c for c in params if c["kind"] == "removed"]
        if removed:
            parts.append(f"<h3>Removed ({len(removed)})</h3>" + ("<details><summary>Show removed values</summary>" if len(removed) > 30 else "") + _table(["Name", "Last value", "Note"],
                         [[name_cell(c), code(str(c["old"])[:80]), esc(f"still defined in {c['still_defined_in']}" if c.get("still_defined_in") else f"removed from {c['file']}")]
                          for c in removed]) + ("</details>" if len(removed) > 30 else ""))
        ren = [c for c in params if c["kind"] == "renamed"]
        if ren:
            parts.append(f"<h3>Renamed keys ({len(ren)})</h3><details><summary>Show renamed keys (values unchanged)</summary>" +
                         _table(["Old name", "New name", "Value"], [[code(c["old_name"]), code(c["name"]), code(str(c["new"])[:80])] for c in ren]) + "</details>")
        moved = [c for c in params if c["kind"] == "moved"]
        if moved:
            parts.append("<h3>Moved</h3>" + _table(["Name", "From", "To"], [[code(c["name"]), esc(c["from"]), esc(c["file"])] for c in moved]))
        if consts:
            parts.append("<h3>Constants</h3>" + _table(["Name", "Change", "Old", "New", "Δ", "File"],
                         [[code(c["name"]), esc(c["kind"]), code(c["old"]), code(c["new"]), self.pct_cell(c), self.blob(e, c["file"])] for c in consts]))
        comps = e["components"]
        crows = [[esc(x["kind"]), "added", self.blob(e, x["path"])] for x in comps["added"]]
        crows += [[esc(x["kind"]), "removed", esc(x["path"])] for x in comps["removed"]]
        crows += [[esc(x["kind"]), f"renamed from {code(x['old'])}", self.blob(e, x["path"])] for x in comps["renamed"]]
        crows += [[esc(x["kind"]), "modified", self.blob(e, x["path"])] for x in comps["changed"]]
        if crows or self.cfg["components"]["rules"]:
            parts.append("<h3>Components</h3>" + (_table(["Kind", "Change", "File"], crows[:400]) or "<p class='muted'>None detected.</p>"))
        tw = [t for t in e.get("tweaks", []) if t["changes"]]
        if tw:
            parts.append("<h3>Other literal values</h3>" + _table(["Old line", "New line", "File"],
                         [[code(t["old_line"]), code(t["new_line"]), self.blob(e, t["file"])] for t in tw]))
        if post:
            if post["status"] == "ready":
                parts.append("<h3>Post text (X-ready, not posted)</h3>" + "".join(
                    f'<div class="post">{esc(p)}</div><p class="badge">{n}/280 weighted chars</p>' for p, n in zip(post["posts"], post["weighted_lengths"])))
            else:
                parts.append(f"<h3>Post text</h3><p class='muted'>No post: {esc(post['reason'])}.</p>")
        mods = [[f"<code>{esc(m)}</code>", str(v["added"]), str(v["modified"]), str(v["removed"]), f"+{v['lines_added']:,}", f"−{v['lines_deleted']:,}"]
                for m, v in e["modules"].items()]
        parts.append("<h3>Modules touched</h3>" + _table(["Module", "Added", "Modified", "Removed", "Lines +", "Lines −"], mods))
        files = e["files"]
        note = {"code": "", "noise": "noise lines only", "whitespace": "formatting only", "comments": "comments only"}
        frows = [[esc(f["status"]), self.blob(e, f["path"]) if f["status"] != "D" else esc(f["path"]), f"+{f['added']}/−{f['deleted']}", esc(note[f["kind"]])]
                 for f in files[:1500]]
        parts.append(f"<h3>Watched files ({len(files):,})</h3><details><summary>Show file list</summary>" + _table(["", "Path", "Lines", "Note"], frows) + "</details>")
        if e.get("body") or e.get("subject"):
            parts.append(f"<h3>Commit message</h3><pre>{esc(e['subject'])}\n\n{esc(e.get('body') or '')}</pre>")
        nav = '<nav class="pn">'
        nav += f'<a href="{esc(prev["short"])}.html">← older ({fmt_date(prev["date"])})</a>' if prev else "<span></span>"
        nav += f'<a href="{esc(nxt["short"])}.html">newer ({fmt_date(nxt["date"])}) →</a>' if nxt else "<span></span>"
        parts.append(nav + "</nav>")
        return "\n".join(parts)

    def index(self, newest: list[dict], noops: list[dict], current: list[dict]) -> str:
        repo = f'<a href="{esc(self.repo_url)}">{esc(self.repo_url.split("github.com/")[-1])}</a>' if self.repo_url else esc(self.cfg["name"])
        intro = f"""<section class="intro">
<h1>{esc(self.title)}</h1>
<p>{inline_md(self.tagline)} Generated from the git history of {repo}.</p>
<p class="muted">{len(newest)} entries · newest first · <a href="feed.xml">Atom</a> · <a href="feed.json">JSON Feed</a> · <a href="changes.json">changes.json</a>{f' · {len(noops)} no-op commits collapsed' if noops else ''}</p>
</section>"""
        cur = ""
        if current:
            rows = [[f"<code>{esc(c['name'])}</code><br><span class='muted'>{esc(c['file'])}</span>", f"<code>{esc(c['new'])}</code>",
                     f"<a href='entries/{esc(c['short'])}.html'>{fmt_date(c['date'])}</a>" + (f" (was <code>{esc(c['old'])}</code>)" if c["old"] is not None else "")]
                    for c in current]
            cur = (f"<section class='weights'><details><summary>Recently changed values ({len(current)})</summary>" +
                   _table(["Name", "Value", "Last changed"], rows) + "<p><a href='params.html'>Full history →</a></p></details></section>")
        cards = "\n".join(self.card(e) for e in newest)
        nsec = ""
        if noops:
            nsec = "<section><h2>No-op commits</h2><ul>" + "".join(
                f"<li>{fmt_date(e['date'])} · <code>{esc(e['short'])}</code> — only noise, formatting or comments changed</li>" for e in noops) + "</ul></section>"
        return intro + cur + cards + nsec

    def params(self, records: list[dict]) -> str:
        rows = [[f"<a href='entries/{esc(r['short'])}.html'>{fmt_date(r['date'])}</a>", esc(r["kind"]),
                 f"<code>{esc(r['name'])}</code><br><span class='muted'>{esc(r['file'])}</span>",
                 f"<code>{esc(r['old'])}</code>" if r["old"] is not None else "—",
                 f"<code>{esc(r['new'])}</code>" if r["new"] is not None else "—", self.pct_cell(r)] for r in records]
        return ("<h1>Parameter history</h1><p class='muted'>Every extracted value change, newest first (bulk additions in "
                "initial or large commits are listed on their entry pages). Also available as <a href='changes.json'>changes.json</a>.</p>" +
                _table(["Date", "Change", "Name", "Old", "New", "Δ"], rows, "params"))

    def posts_page(self, posts: list[dict]) -> str:
        ready = [p for p in posts if p["status"] == "ready"]
        parts = [f"<h1>Post text</h1><p class='muted'>X-ready text for {len(ready)} of {len(posts)} commits: ≤280 weighted characters per post "
                 "(links count as 23), threads split automatically, and every number checked against the diff. Nothing is posted. "
                 "Also available as <a href='posts.json'>posts.json</a> and <a href='posts.txt'>posts.txt</a>.</p>"]
        for p in ready:
            parts.append(f"<h3><a href='entries/{esc(p['short'])}.html'>{fmt_date(p['date'])} · {esc(p['short'])}</a></h3>" + "".join(
                f'<div class="post">{esc(t)}</div><p class="badge">{n}/280</p>' for t, n in zip(p["posts"], p["weighted_lengths"])))
        return "\n".join(parts)

    # -- feeds ----------------------------------------------------------------
    def atom(self, newest: list[dict], limit: int = 50) -> str:
        updated = iso_utc(newest[0]["date"]) if newest else self.meta["generated_iso"]
        authority = (self.url.split("//", 1)[-1].split("/", 1)[0] if self.url else "knoblog.invalid")
        items = []
        for e in newest[:limit]:
            link = self.entry_link(e) or e.get("url") or ""
            title = re.sub(r"`", "", e["summary"]["title"])
            content = self.summary_html(e) + (f'<p><a href="{e["url"]}">Commit {e["short"]}</a></p>' if e.get("url") else "")
            items.append(f"""  <entry>
    <title>{xesc(fmt_date(e['date']) + ' — ' + title)}</title>
    <link rel="alternate" type="text/html" href="{xesc(link)}"/>
    {f'<link rel="related" href="{xesc(e["url"])}"/>' if e.get("url") else ""}
    <id>tag:{xesc(authority)},2026:{e['sha']}</id>
    <published>{iso_utc(e['date'])}</published>
    <updated>{iso_utc(e['date'])}</updated>
    <summary>{xesc(title)}</summary>
    <content type="html">{xesc(content)}</content>
  </entry>""")
        return f"""<?xml version="1.0" encoding="utf-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <title>{xesc(self.title)}</title>
  <subtitle>{xesc(self.tagline)}</subtitle>
  <link rel="self" type="application/atom+xml" href="{xesc(self.url)}feed.xml"/>
  <link rel="alternate" type="text/html" href="{xesc(self.url or self.repo_url)}"/>
  <id>{xesc(self.url or ('urn:knoblog:' + (self.cfg['name'] or 'repo')))}</id>
  <updated>{updated}</updated>
  <author><name>{xesc(self.cfg['site'].get('author') or 'knoblog')}</name></author>
  <generator version="{__version__}">knoblog</generator>
{chr(10).join(items)}
</feed>
"""

    def json_feed(self, newest: list[dict], posts_by_sha: dict, limit: int = 100) -> dict:
        items = []
        for e in newest[:limit]:
            p = posts_by_sha.get(e["sha"]) or {}
            items.append({
                "id": e["sha"], "url": self.entry_link(e) or e.get("url"), "external_url": e.get("url") or None,
                "title": re.sub(r"`", "", e["summary"]["title"]), "content_html": self.summary_html(e),
                "date_published": iso_utc(e["date"]), "tags": e["summary"]["tags"],
                "_knoblog": {"commit": e["sha"], "commit_url": e.get("url"), "class": e["class"],
                             "changes": [change_record(e, c) for c in e["changes"]],
                             "post": {k: p[k] for k in ("status", "posts", "weighted_lengths", "reason") if k in p} or None},
            })
        feed = {"version": "https://jsonfeed.org/version/1.1", "title": self.title, "description": self.tagline,
                "home_page_url": self.url or self.repo_url or None, "feed_url": (self.url + "feed.json") if self.url else None,
                "authors": [{"name": self.cfg["site"].get("author") or "knoblog"}], "items": items}
        return {k: v for k, v in feed.items() if v is not None}


def change_record(e: dict, c: dict) -> dict:
    r = {"commit": e["sha"], "short": e["short"], "date": e["date"], "commit_url": e.get("url"),
         "file": c["file"], "file_url": f"{e['url'].rsplit('/commit/', 1)[0]}/blob/{e['sha']}/{c['file']}" if e.get("url") else None,
         "extractor": c["extractor"], "name": c["name"], "kind": c["kind"], "type": c["type"],
         "old": c["old"], "new": c["new"], "pct_change": round(c["pct"], 4) if c.get("pct") is not None else None,
         "ratio": round(c["ratio"], 6) if c.get("ratio") is not None else None}
    w = e.get("why") or {}
    r["commit_message"] = w.get("subject", e.get("subject"))
    r["pr_number"], r["pr_url"] = w.get("pr_number"), w.get("pr_url")
    for k in ("key", "decl_type", "from", "still_defined_in", "old_name", "list_index", "file_deleted"):
        if c.get(k):
            r[k] = c[k]
    return r


def posts_txt(posts: list[dict]) -> str:
    out = []
    for p in posts:
        if p["status"] != "ready":
            continue
        out.append(f"=== {iso_utc(p['date'])[:10]} {p['short']} ({len(p['posts'])} post{'s' if len(p['posts']) > 1 else ''}) ===")
        for i, (t, n) in enumerate(zip(p["posts"], p["weighted_lengths"]), 1):
            out.append(f"--- {i}/{len(p['posts'])} [{n}/280] ---\n{t}")
        out.append("")
    return "\n".join(out)


def write(path: str, text: str) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
