"""Plain-English notes from the analysis.

Precedence per entry: hand-written note (notes_dir/<short>.md) > cached LLM summary (optional) > deterministic.
The deterministic summary only states facts present in the analysis.
"""
from __future__ import annotations

import json
import os
import re
import urllib.request

from .analyze import CONST_LIKE

MAX_LIST = 8


def humanize(name: str) -> str:
    s = re.sub(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])", " ", name)
    return s.replace("_", " ").replace(".", " ").lower().strip()


def _fmt_list(items: list[str], limit: int = MAX_LIST) -> str:
    if len(items) <= limit:
        return "".join(items) if len(items) <= 1 else ", ".join(items[:-1]) + " and " + items[-1]
    return ", ".join(items[:limit]) + f" and {len(items) - limit} more"


def _short(v, n: int = 40) -> str:
    v = str(v)
    return v if len(v) <= n else v[: n - 1] + "…"


def _plural(n: int, word: str, plural: str | None = None) -> str:
    return f"{n} {word if n == 1 else (plural or word + 's')}"


def highlight_label(name: str, cfg: dict) -> str | None:
    for h in cfg.get("highlight", []):
        if re.search(h["pattern"], name):
            return h.get("label", "highlighted")
    return None


def pct_text(c: dict) -> str:
    if c.get("pct") is None:
        return ""
    p = c["pct"]
    return f" ({'+' if p > 0 else '−'}{abs(p):.3g}%)"


def merge_mirrored(changes: list[dict]) -> list[dict]:
    """Identical changes to the same name in several files (mirrored configs) are reported once."""
    merged: dict[tuple, dict] = {}
    for c in changes:
        k = (c["extractor"], c["name"], c["kind"], c["old"], c["new"])
        if k in merged:
            merged[k]["files"].append(c["file"])
        else:
            merged[k] = {**c, "files": [c["file"]]}
    return list(merged.values())


def _where(c: dict, home: set[str]) -> str:
    files = c.get("files") or [c["file"]]
    if set(files) <= home:
        return ""
    return " (in " + " and ".join(f"`{f}`" for f in files) + ")"


def describe(c: dict, home: set[str]) -> str:
    if c["type"] == "bool" and {c["old"], c["new"]} <= {"true", "false", "True", "False"}:
        return f"`{c['name']}` is now {'ON' if c['new'] in ('true', 'True') else 'OFF'}" + _where(c, home)
    return f"`{c['name']}`: `{c['old']}` → `{c['new']}`{pct_text(c)}" + _where(c, home)


def deterministic(entry: dict, cfg: dict) -> dict:
    bullets: list[str] = []
    tags: list[str] = []
    if entry["class"] == "noop":
        return {"headline": "No functional change (noise / formatting only).", "bullets": [], "tags": ["no-op"]}
    home = set(cfg["summaries"].get("home_files") or [])
    ch = entry["changes"]
    changed = merge_mirrored([c for c in ch if c["kind"] in ("changed", "moved") and c["old"] != c["new"] and c["extractor"] not in CONST_LIKE])
    labels: dict[str, list[dict]] = {}
    for c in changed:
        lab = highlight_label(c["name"], cfg)
        if lab:
            labels.setdefault(lab, []).append(c)
    hl = [c for v in labels.values() for c in v]
    flips = [c for c in changed if c not in hl and c["type"] == "bool"]
    values = sorted([c for c in changed if c not in hl and c["type"] != "bool"], key=lambda c: c["type"] != "number")
    for lab, cs in labels.items():
        tags.append(lab + "s")
        bullets.append(f"{lab[0].upper() + lab[1:]} defaults changed: " + _fmt_list([describe(c, home) for c in cs]) + ".")
    if flips:
        tags.append("flags")
        bullets.append("Flags flipped: " + _fmt_list([describe(c, home) for c in flips]) + ".")
    if values:
        tags.append("params")
        bullets.append("Parameter defaults changed: " + _fmt_list([describe(c, home) for c in values]) + ".")
    moved = [c for c in ch if c["kind"] == "moved"]
    if moved:
        bullets.append("Moved between files: " + _fmt_list([f"`{c['name']}` ({c['from']} → {c['file']})" for c in moved]) + ".")
    still = [c for c in ch if c["kind"] == "removed" and c.get("still_defined_in")]
    by_file: dict[str, list[str]] = {}
    for c in still:
        by_file.setdefault(c["still_defined_in"], []).append(f"`{c['name']}`")
    for f, names in by_file.items():
        bullets.append(f"{_plural(len(names), 'parameter')} no longer defined in the changed file(s) but still defined in `{f}` (moved, not deleted): " + _fmt_list(names) + ".")
    new_files = {f["path"] for f in entry["files"] if f["status"] == "A"}
    bulk: dict[str, list[dict]] = {}
    for c in ch:
        if c["kind"] == "added" and c["file"] in new_files and c["extractor"] not in CONST_LIKE:
            bulk.setdefault(c["file"], []).append(c)
    bulk = {f: cs for f, cs in bulk.items() if len(cs) > 10}
    for f, cs in bulk.items():
        hls = [c for c in cs if highlight_label(c["name"], cfg)]
        bullets.append(f"New file `{f}` defines {len(cs)} parameters" + (f", including {len(hls)} highlighted" if hls else "") + " (see the full analysis).")
        tags.append("params")
    ren: dict[tuple[str, str], int] = {}
    for c in ch:
        if c["kind"] == "renamed":
            k = (c["old_name"].split(".", 1)[0], c["name"].split(".", 1)[0])
            ren[k] = ren.get(k, 0) + 1
    for (a, b), n in ren.items():
        bullets.append(f"Section renamed: `{a}` → `{b}` ({_plural(n, 'key')}, values unchanged).")
        tags.append("renamed")
    gone: dict[str, int] = {}
    for c in ch:
        if c["kind"] == "removed" and c.get("file_deleted"):
            gone[c["file"]] = gone.get(c["file"], 0) + 1
    for f, n in gone.items():
        bullets.append(f"File `{f}` deleted ({_plural(n, 'value')} no longer defined there).")
    for word, key in (("Added", "added"), ("Removed", "removed")):
        cs = [c for c in ch if c["kind"] == key and not c.get("still_defined_in") and not c.get("file_deleted") and c["extractor"] not in CONST_LIKE
              and not (key == "added" and c["file"] in bulk)]
        if not cs:
            continue
        flags = [c for c in cs if c["type"] == "bool"]
        rest = [c for c in cs if c["type"] != "bool"]
        if flags:
            desc = [f"`{c['name']}`" + (f" (default {'on' if c['new'] in ('true', 'True') else 'off'})" if key == "added" else "") for c in flags]
            bullets.append(f"{word} {_plural(len(flags), 'flag')}: " + _fmt_list(desc) + ".")
        if rest:
            desc = [f"`{c['name']}`" + (f" = `{str(c['new'])[:40]}`" if key == "added" else "") for c in rest]
            bullets.append(f"{word} {_plural(len(rest), 'parameter')}: " + _fmt_list(desc) + ".")
        tags.append("params")

    comps = entry["components"]
    by_kind: dict[tuple[str, str], list[str]] = {}
    for key in ("added", "removed"):
        for c in sorted(comps[key], key=lambda c: c["path"]):
            by_kind.setdefault((key, c["kind"]), []).append(f"`{c['name']}`")
    for (key, kind), names in sorted(by_kind.items()):
        bullets.append(f"{'New' if key == 'added' else 'Removed'} {kind.lower()}{'s' if len(names) > 1 else ''}: " + _fmt_list(names) + ".")
        tags.append(kind.lower() + "s")
    if comps["renamed"]:
        bullets.append("Renamed: " + _fmt_list([f"`{c['old']}` → `{c['new']}`" for c in comps["renamed"]]) + ".")
    consts = [c for c in ch if c["extractor"] in CONST_LIKE and c["kind"] == "changed"]
    if consts:
        tags.append("constants")
        bullets.append("Constants changed: " + _fmt_list([f"`{c['name']}` `{c['old'][:40]}` → `{c['new'][:40]}` ({os.path.basename(c['file'])})" for c in consts], 6) + ".")
    if entry.get("tweaks"):
        tags.append("numeric tweaks")
        desc = [f"`{t['label'] or t['new_line'][:50]}` {', '.join(f'{o}→{n}' for o, n in t['changes'][:3])} ({os.path.basename(t['file'])})" for t in entry["tweaks"]]
        bullets.append("Other literal values changed in code: " + _fmt_list(desc, 5) + ".")
    for l in entry.get("lfs", []):
        gb = lambda n: f"{n / 1e9:.2f} GB" if n and n >= 1e8 else (f"{n / 1e6:.1f} MB" if n else "?")
        bullets.append(f"Git LFS artifact `{l['file']}` " + ({"A": "added", "D": "removed"}.get(l["status"], "replaced"))
                       + (f" ({gb(l['old_size'])} → {gb(l['new_size'])})" if l["status"] not in ("A", "D") else "") + ".")
        tags.append("artifacts")
    mods = list(entry["modules"].items())
    if mods:
        top = [f"`{m if m.startswith('(') else m + '/'}` (+{v['lines_added']:,}/−{v['lines_deleted']:,})" for m, v in mods[:5]]
        bullets.append("Most activity in " + _fmt_list(top, 5) + ".")
    st = entry["stats"]
    quiet = st["whitespace_files"] + st["comment_only_files"] + st["noise_files"]
    if quiet:
        bullets.append(f"{_plural(quiet, 'file')} changed only in formatting, comments or noise lines.")

    hn = (lambda n: humanize(n)) if cfg["summaries"].get("names") == "human" else (lambda n: n)
    order = [r["kind"] for r in cfg["components"]["rules"]]
    if entry["is_root"]:
        headline = f"Initial commit: {st['files']:,} watched files."
    elif hl:
        lab = next(iter(labels))
        headline = f"{lab[0].upper() + lab[1:]}s changed: " + _fmt_list([f"{hn(c['name'])} {c['old']}→{c['new']}" for c in hl], 3) + "."
    elif by_kind:
        k = sorted(by_kind.items(), key=lambda kv: (kv[0][0] != "added", order.index(kv[0][1]) if kv[0][1] in order else 99, -len(kv[1])))[0]
        headline = f"{'New' if k[0][0] == 'added' else 'Removed'} {k[0][1].lower()}{'s' if len(k[1]) > 1 else ''} {_fmt_list(k[1], 3)}" + (" and other changes." if len(bullets) > 2 else ".")
    elif flips:
        headline = "Flags flipped: " + _fmt_list([f"{hn(c['name'])} {'on' if c['new'] in ('true', 'True') else 'off'}" for c in flips], 3) + "."
    elif values:
        headline = "Parameter changes: " + _fmt_list([f"{hn(c['name'])} {_short(c['old'])}→{_short(c['new'])}" for c in values], 3) + "."
    elif moved:
        pairs: dict[tuple[str, str], int] = {}
        for c in moved:
            k = (os.path.basename(c["from"]), os.path.basename(c["file"]))
            pairs[k] = pairs.get(k, 0) + 1
        headline = f"{_plural(len(moved), 'parameter')} moved: " + _fmt_list([f"{a} → {b}" for a, b in pairs], 2) + (
            " (values unchanged)." if all(c["old"] == c["new"] for c in moved) else ".")
    elif ren:
        (a, b), n = next(iter(ren.items()))
        headline = f"Section renamed: {a} → {b} ({_plural(n, 'key')}, values unchanged)."
    elif any(c["kind"] == "added" and c["extractor"] not in CONST_LIKE for c in ch):
        n = sum(c["kind"] == "added" and c["extractor"] not in CONST_LIKE for c in ch)
        headline = f"{_plural(n, 'new parameter')} in " + _fmt_list([m if m.startswith('(') else f"{m}/" for m, _ in mods[:3]]) + "."
    elif rem := [c for c in ch if c["kind"] == "removed" and not c.get("still_defined_in") and not c.get("file_deleted") and c["extractor"] not in CONST_LIKE]:
        headline = f"Removed {_plural(len(rem), 'parameter')}: " + _fmt_list([hn(c["name"]) for c in rem], 3) + "."
    elif consts:
        headline = "Constants changed: " + _fmt_list([f"{c['name']} {c['old']}→{c['new']}" for c in consts], 3) + "."
    elif entry.get("lfs") and st["files"] == len(entry["lfs"]):
        headline = "Model artifact (Git LFS) updated: " + ", ".join(os.path.basename(l["file"]) for l in entry["lfs"]) + "."
    elif gone:
        headline = "Removed " + _fmt_list([f"{f}" for f in gone], 3) + "."
    elif entry["class"] == "docs":
        headline = "Documentation-only update."
    else:
        headline = "Code changes in " + _fmt_list([m if m.startswith("(") else f"{m}/" for m, _ in mods[:3]]) + f" ({_plural(st['files'], 'file')})."
    return {"headline": headline.replace("`", ""), "bullets": bullets, "tags": sorted(set(tags))}


def load_note(directory: str | None, short: str) -> dict | None:
    if not directory:
        return None
    path = os.path.join(directory, f"{short}.md")
    if not os.path.exists(path):
        return None
    text = open(path, encoding="utf-8").read().strip()
    title = None
    if text.startswith("# "):
        first, _, text = text.partition("\n")
        title = first[2:].strip()
    return {"title": title, "markdown": text.strip()}


LLM_PROMPT = """You write release notes for a repository's configuration and parameter changes.
Rules: use ONLY facts in the structured analysis and diff excerpt; never speculate about motives; say "unclear from
the diff" rather than guess. Start with a one-line title prefixed by "# " (max 90 chars), then one short paragraph and
3-8 bullet points. Quote parameter names and old→new values exactly, in `backticks`. No marketing tone, no emojis.

STRUCTURED ANALYSIS (JSON):
{analysis}

DIFF EXCERPT (truncated):
{diff}
"""


def llm_config() -> dict | None:
    """Optional. XAI_API_KEY or OPENAI_API_KEY (any OpenAI-compatible endpoint via KNOBLOG_LLM_BASE_URL)."""
    if os.environ.get("XAI_API_KEY"):
        return {"key": os.environ["XAI_API_KEY"], "base": os.environ.get("KNOBLOG_LLM_BASE_URL", "https://api.x.ai/v1"),
                "model": os.environ.get("KNOBLOG_LLM_MODEL", "grok-4")}
    if os.environ.get("OPENAI_API_KEY"):
        return {"key": os.environ["OPENAI_API_KEY"], "base": os.environ.get("KNOBLOG_LLM_BASE_URL", "https://api.openai.com/v1"),
                "model": os.environ.get("KNOBLOG_LLM_MODEL", "gpt-4o-mini")}
    return None


def llm_summarize(cfg: dict, entry: dict, diff_excerpt: str) -> str:
    compact = {k: entry[k] for k in ("short", "date", "class", "stats", "modules", "components", "changes", "tweaks")}
    prompt = LLM_PROMPT.format(analysis=json.dumps(compact, indent=1)[:30000], diff=diff_excerpt[:40000])
    req = urllib.request.Request(cfg["base"].rstrip("/") + "/chat/completions",
                                 data=json.dumps({"model": cfg["model"], "temperature": 0,
                                                  "messages": [{"role": "user", "content": prompt}]}).encode(),
                                 headers={"Authorization": f"Bearer {cfg['key']}", "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=180) as resp:
        return json.load(resp)["choices"][0]["message"]["content"].strip()
