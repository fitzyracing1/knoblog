"""knoblog command line.

  knoblog run --repo <url or path> --config knoblog.yml [--out site]
  knoblog init [--preset ros2] [--out knoblog.yml]
  knoblog extract FILE [--extractor NAME]
  knoblog presets
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
import time
from datetime import datetime, timezone

from . import __version__
from . import config as C
from . import posttext, render
from .analyze import CONST_LIKE, analyze_commit
from .extractors import REGISTRY, auto_extractors, run_extractor
from .gitio import EMPTY_TREE, ensure_clone, head_rev, list_commits, run, unified_diff
from .summarize import deterministic, llm_config, llm_summarize, load_note

PKG = os.path.dirname(os.path.abspath(__file__))


def log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def browse_url(repo: str) -> str:
    """https URL for commit links from a clone URL (GitHub/GitLab style), or ''."""
    repo = repo.strip()
    m = re.match(r"^git@([^:]+):(.+?)(\.git)?/?$", repo)
    if m:
        return f"https://{m.group(1)}/{m.group(2)}"
    m = re.match(r"^(https?://[^/]+/.+?)(\.git)?/?$", repo)
    return m.group(1) if m else ""


def resolve_repo(repo: str, cfg: dict, args) -> tuple[str, str]:
    """Return (local git dir, browsable url)."""
    if os.path.isdir(repo):
        url = cfg.get("repo_url") or browse_url(run(["remote", "get-url", "origin"], cwd=repo, check=False).strip())
        if not args.no_fetch and run(["remote"], cwd=repo, check=False).strip():
            log(f"[fetch] {repo}")
            run(["fetch", "--quiet", "origin"], cwd=repo, check=False)
        return repo, url
    cache = args.cache_dir or os.path.join(".knoblog-cache")
    dest = os.path.join(cache, re.sub(r"[^A-Za-z0-9._-]+", "_", browse_url(repo).split("//")[-1] or repo))
    if not (args.no_fetch and os.path.isdir(dest)):
        log(f"[clone] {repo} -> {dest} ({'blob-less partial' if cfg['clone']['partial'] else 'full'})")
        ensure_clone(repo, dest, cfg.get("branch"), partial=cfg["clone"]["partial"])
    return dest, cfg.get("repo_url") or browse_url(repo)


def summarise(e: dict, cfg: dict, repo: str, meta, llm, state: dict) -> None:
    auto = deterministic(e, cfg)
    e["auto"] = auto
    sc = cfg["summaries"]
    base = cfg.get("_dir") or "."
    rel = lambda p: p if not p or os.path.isabs(p) else os.path.join(base, p)
    note = load_note(rel(sc.get("notes_dir")), e["short"])
    cached = load_note(rel(sc.get("llm_cache_dir")), e["short"])
    mk = lambda src, n: {"source": src, "title": n["title"] or auto["headline"], "markdown": n["markdown"], "bullets": auto["bullets"], "tags": auto["tags"]}
    if note:
        e["summary"] = mk("note", note)
    elif cached:
        e["summary"] = mk("llm", cached)
    elif llm and e["class"] != "noop" and state["llm_calls"] < int(sc.get("llm_limit") or 0) and sc.get("llm_cache_dir"):
        state["llm_calls"] += 1
        try:
            parent = meta.parents[0] if meta.parents else EMPTY_TREE
            text = llm_summarize(llm, e, unified_diff(repo, parent, meta.sha, context=2))
            d = rel(sc["llm_cache_dir"])
            os.makedirs(d, exist_ok=True)
            with open(os.path.join(d, f"{e['short']}.md"), "w", encoding="utf-8") as f:
                f.write(text + "\n")
            e["summary"] = mk("llm", load_note(d, e["short"]))
        except Exception as exc:  # deterministic fallback
            log(f"[llm] {e['short']} failed ({exc}); using deterministic summary")
    if "summary" not in e:
        e["summary"] = {"source": "auto", "title": auto["headline"], "markdown": "", "bullets": auto["bullets"], "tags": auto["tags"]}


def history_records(entries: list[dict], cfg: dict) -> list[dict]:
    """Flat change records for params.html / changes.json (bulk additions are left to entry pages)."""
    out = []
    limit = cfg["post"]["bulk_added_limit"]
    for e in entries:
        bulk = e["is_root"] or sum(c["kind"] == "added" for c in e["changes"]) > limit
        for c in e["changes"]:
            if bulk and c["kind"] in ("added", "removed"):
                continue
            if (c["kind"] in ("changed", "moved") and c["old"] == c["new"]) or c["kind"] == "renamed" or c.get("file_deleted"):
                continue
            out.append({**render.change_record(e, c), "pct": c.get("pct")})
    return out


def cmd_run(args) -> int:
    t0 = time.time()
    overrides: dict = {}
    if args.site_url is not None:
        overrides.setdefault("site", {})["url"] = args.site_url
    if args.max_commits:
        overrides.setdefault("history", {})["max_commits"] = args.max_commits
    if args.since:
        overrides.setdefault("history", {})["since"] = args.since
    if args.no_llm:
        overrides.setdefault("summaries", {})["llm"] = False
    if args.branch:
        overrides["branch"] = args.branch
    cfg = C.load(args.config, args.preset, overrides)
    repo_arg = args.repo or cfg.get("repo")
    if not repo_arg:
        raise SystemExit("no repository: pass --repo <url or path> or set `repo:` in the config")
    repo, url = resolve_repo(repo_arg, cfg, args)
    cfg["repo_url"] = url
    cfg["name"] = cfg.get("name") or (url or os.path.abspath(repo)).rstrip("/").split("/")[-1]
    h = cfg["history"]
    rev = head_rev(repo, cfg.get("branch"))
    paths = cfg["watch"]["paths"] if h.get("limit_log_to_watched_paths") and cfg["watch"]["paths"] != ["**"] else None
    commits = list_commits(repo, rev, paths, h.get("max_commits"), h.get("since"), h.get("first_parent", True))
    log(f"[walk] {len(commits)} commits (rev {rev[:7]}, newest {h.get('max_commits')}{', path-limited' if paths else ''})")
    llm = llm_config() if cfg["summaries"].get("llm") else None
    entries, state = [], {"llm_calls": 0}
    for meta in commits:
        e = analyze_commit(repo, meta, cfg, url)
        summarise(e, cfg, repo, meta, llm, state)
        entries.append(e)
        if args.verbose:
            log(f"[entry] {e['date'][:10]} {e['short']} {e['class']:5} {len(e['changes']):4} changes  {e['summary']['title'][:80]}")
    posts = posttext.post_text_for(entries, cfg) if cfg["post"].get("enabled") else []
    posts_by_sha = {p["sha"]: p for p in posts}
    now = datetime.now(timezone.utc)
    meta = {"generated_at": now.strftime("%Y-%m-%d %H:%M UTC"), "generated_iso": now.strftime("%Y-%m-%dT%H:%M:%SZ"), "head_sha": rev}
    site = render.Site(cfg, meta)
    out = args.out
    if os.path.isdir(out):
        shutil.rmtree(out)
    os.makedirs(os.path.join(out, "entries"))
    shutil.copy(os.path.join(PKG, "static", "style.css"), os.path.join(out, "style.css"))
    real = [e for e in entries if e["class"] != "noop"]
    noops = [e for e in entries if e["class"] == "noop"]
    newest = list(reversed(real))
    records = history_records(real, cfg)
    newest_records = list(reversed(records))
    seen, current = set(), []
    for r in newest_records:
        if r["kind"] in ("changed", "moved") and r["extractor"] not in CONST_LIKE and (r["name"], r["file"]) not in seen:
            seen.add((r["name"], r["file"]))
            current.append(r)
    render.write(os.path.join(out, "index.html"), site.page(site.title, site.index(newest, list(reversed(noops)), current[:40])))
    render.write(os.path.join(out, "params.html"), site.page("Parameter history · " + site.title, site.params(newest_records)))
    render.write(os.path.join(out, "posts.html"), site.page("Post text · " + site.title, site.posts_page(list(reversed(posts)))))
    for i, e in enumerate(real):
        prev = real[i - 1] if i else None
        nxt = real[i + 1] if i + 1 < len(real) else None
        title = f"{render.fmt_date(e['date'])}: {e['summary']['title'].replace('`', '')}"
        render.write(os.path.join(out, "entries", f"{e['short']}.html"),
                     site.page(title + " · " + site.title, site.detail(e, prev, nxt, posts_by_sha.get(e["sha"])), depth=1, description=title))
    render.write(os.path.join(out, "feed.xml"), site.atom(newest))
    render.write(os.path.join(out, "feed.json"), json.dumps(site.json_feed(newest, posts_by_sha), indent=1, ensure_ascii=False))
    for r in newest_records:
        r.pop("pct", None)
    render.write(os.path.join(out, "changes.json"), json.dumps({"generator": f"knoblog {__version__}", "repo": url, "head": rev,
                                                                 "generated": meta["generated_iso"], "changes": newest_records}, indent=1, ensure_ascii=False))
    render.write(os.path.join(out, "posts.json"), json.dumps({"generator": f"knoblog {__version__}", "note": "Text only; knoblog never posts.",
                                                               "posts": list(reversed(posts))}, indent=1, ensure_ascii=False))
    render.write(os.path.join(out, "posts.txt"), render.posts_txt(list(reversed(posts))))
    slim = [{k: e[k] for k in ("sha", "short", "url", "date", "class", "is_root", "is_merge", "stats", "summary", "changes", "components", "lfs")}
            for e in newest]
    render.write(os.path.join(out, "entries.json"), json.dumps({"generated": meta["generated_iso"], "head": rev, "entries": slim}, indent=1, ensure_ascii=False))
    render.write(os.path.join(out, ".nojekyll"), "")
    ready = sum(p["status"] == "ready" for p in posts)
    log(f"[done] {len(real)} entries ({len(noops)} no-op), {len(records)} value changes, {ready} post-ready -> {out} in {time.time() - t0:.1f}s")
    if args.github_output and os.environ.get("GITHUB_OUTPUT"):
        with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as f:
            f.write(f"entries={len(real)}\nchanges={len(records)}\nposts_ready={ready}\nsite_dir={out}\n")
    return 0


def cmd_init(args) -> int:
    if os.path.exists(args.out) and not args.force:
        raise SystemExit(f"{args.out} exists (use --force)")
    src = os.path.join(PKG, "presets", "_template.yml")
    text = open(src, encoding="utf-8").read().replace("extends: generic", f"extends: {args.preset}")
    render.write(args.out, text)
    log(f"wrote {args.out} (extends preset {args.preset!r}); edit watch.paths, then: knoblog run --repo <url or path> --config {args.out}")
    return 0


def cmd_extract(args) -> int:
    text = open(args.file, encoding="utf-8", errors="replace").read()
    names = [args.extractor] if args.extractor else auto_extractors(args.file, text)
    out = {n: run_extractor(n, args.file, text, {}) for n in names}
    print(json.dumps(out, indent=1, ensure_ascii=False))
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="knoblog", description="Plain-English changelogs + X-ready post text for parameter/config changes in a git repo.")
    p.add_argument("--version", action="version", version=f"knoblog {__version__}")
    sub = p.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="analyse history and write the site + feeds")
    r.add_argument("--repo", help="git URL or local path (overrides `repo:` in the config)")
    r.add_argument("--config", "-c", help="knoblog.yml (or a preset name)")
    r.add_argument("--preset", help="preset to start from (ros2, px4, ardupilot, x-algorithm, generic)")
    r.add_argument("--out", "-o", default="site")
    r.add_argument("--branch")
    r.add_argument("--max-commits", type=int)
    r.add_argument("--since", help="only commits after this date (YYYY-MM-DD)")
    r.add_argument("--site-url", help="public URL of the site (used in feeds and post links)")
    r.add_argument("--cache-dir", help="where remote repos are cloned (default .knoblog-cache)")
    r.add_argument("--no-fetch", action="store_true", help="use the existing clone as-is")
    r.add_argument("--no-llm", action="store_true", help="never call an LLM")
    r.add_argument("--github-output", action="store_true", help="write counts to $GITHUB_OUTPUT")
    r.add_argument("-v", "--verbose", action="store_true")
    r.set_defaults(func=cmd_run)
    i = sub.add_parser("init", help="write a starter knoblog.yml")
    i.add_argument("--preset", default="generic")
    i.add_argument("--out", default="knoblog.yml")
    i.add_argument("--force", action="store_true")
    i.set_defaults(func=cmd_init)
    x = sub.add_parser("extract", help="print the values knoblog extracts from one file")
    x.add_argument("file")
    x.add_argument("--extractor", choices=sorted(REGISTRY))
    x.set_defaults(func=cmd_extract)
    ps = sub.add_parser("presets", help="list built-in presets")
    ps.set_defaults(func=lambda a: print("\n".join(n for n in C.presets() if not n.startswith("_"))) or 0)
    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
