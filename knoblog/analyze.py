"""Deterministic analysis of one commit: which watched parameters changed, old -> new.

Everything is derived mechanically from git; nothing is guessed.
"""
from __future__ import annotations

import os
import re
from collections import Counter, defaultdict

from . import config as C
from .yamlmini import unquote
from .extractors import KEYED, LITERALISH, auto_extractors, infer_type, parse_num, run_extractor
from .gitio import EMPTY_TREE, BlobReader, CommitMeta, changed_files, run, unified_diff

FAMILY = {"px4_param_define": "px4", "px4_module_yaml": "px4"}  # same parameter namespace, different file formats
CONST_LIKE = {"rust_const", "python_const", "c_const", "jvm_const"}  # only diffed in modified files; added/removed only for UPPER names
DOC_EXTS = {".md", ".txt", ".rst"}


# ---------------------------------------------------------------------------
# File-level noise classification
# ---------------------------------------------------------------------------
def _strip_comments(path: str, text: str) -> str:
    ext = os.path.splitext(path)[1]
    if ext in {".py", ".toml", ".yml", ".yaml", ".bazel", ".bzl", ".cfg", ".ini"} or path.endswith("BUILD"):
        text = re.sub(r"(?m)^\s*#.*$", "", text)
        text = re.sub(r"(?m)\s+#[^\n'\"]*$", "", text)
        if ext == ".py":
            text = re.sub(r'(?s)"""(.*?)"""', '""""""', text)
    else:
        text = re.sub(r"(?s)/\*.*?\*/", "", text)
        text = re.sub(r"(?m)(^|\s)//.*$", r"\1", text)
    return text


def classify_file_change(path: str, old: str | None, new: str | None, noise: list[re.Pattern]) -> str:
    """'noise' (only configured noise lines changed), 'whitespace', 'comments' or 'code'."""
    if old is None or new is None:
        return "code"
    strip = lambda t: "\n".join(l for l in t.splitlines() if not any(rx.search(l) for rx in noise))
    no_ws = lambda t: re.sub(r"\s+", "", t)
    o, n = strip(old), strip(new)
    if o == n:
        return "noise"
    if no_ws(o) == no_ws(n):
        return "whitespace"
    if no_ws(_strip_comments(path, o)) == no_ws(_strip_comments(path, n)):
        return "comments"
    return "code"


# ---------------------------------------------------------------------------
# Optional: generic numeric tweaks found by pairing -/+ lines in the same hunk
# ---------------------------------------------------------------------------
NUM_TOKEN = re.compile(r"(?<![\w.])[-+]?(?:\d[\d_]*\.?\d*(?:[eE][-+]?\d+)?|\.\d+)(?![\w])")
BOOL_TOKEN = re.compile(r"\b(true|false|True|False)\b")
TEST_PATH = re.compile(r"(^|/)(tests?|testing|testdata)/|_test\.\w+$|(^|/)test_[^/]+$|Test\.\w+$")
TEST_LINE = re.compile(r"\bassert|\bexpect\(|\.apply\(&|should|#\[test\]")
COMMENT_LINE = re.compile(r"^\s*(//|#|\*|/\*|--)")
LOCK_FILE = re.compile(r"(^|/)(Cargo\.lock|package-lock\.json|yarn\.lock|poetry\.lock|uv\.lock|[^/]*\.lock)$")


def _skeleton(line: str) -> str:
    return re.sub(r"\s+", "", BOOL_TOKEN.sub("?", NUM_TOKEN.sub("#", line)))


def _label_for(line: str) -> str:
    for rx in (r"\s*(?:pub\s+)?(?:const\s+|static\s+|val\s+|var\s+|let\s+)?([A-Za-z_][\w.]*)\s*(?::[^=]*)?=",
               r"\s*([A-Za-z_][\w.]*)\s*:", r"\s*\"([^\"]+)\"\s*[:=]"):
        m = re.match(rx, line)
        if m:
            return m.group(1)
    return ""


def numeric_tweaks(diff_text: str, noise: list[re.Pattern]) -> list[dict]:
    tweaks: list[dict] = []
    cur_file, minus, plus = None, [], []

    def flush():
        if not cur_file or not minus or not plus:
            return
        if TEST_PATH.search(cur_file) or os.path.splitext(cur_file)[1] in DOC_EXTS or LOCK_FILE.search(cur_file):
            return
        used = set()
        for m in minus:
            if TEST_LINE.search(m) or COMMENT_LINE.match(m) or any(rx.search(m) for rx in noise):
                continue
            sk = _skeleton(m)
            if len(re.sub(r"[^A-Za-z]", "", sk)) < 2:
                continue
            for idx, p in enumerate(plus):
                if idx in used or _skeleton(p) != sk or p.strip() == m.strip():
                    continue
                ov = NUM_TOKEN.findall(m) + BOOL_TOKEN.findall(m)
                nv = NUM_TOKEN.findall(p) + BOOL_TOKEN.findall(p)
                used.add(idx)
                tweaks.append({"file": cur_file, "label": _label_for(p) or _label_for(m), "old_line": m.strip()[:200],
                               "new_line": p.strip()[:200],
                               "changes": [[o, n] for o, n in zip(ov, nv) if o != n] if len(ov) == len(nv) else []})
                break

    for line in diff_text.splitlines():
        if line.startswith("diff --git"):
            flush(); minus, plus, cur_file = [], [], None
        elif line.startswith("+++ "):
            cur_file = line[6:] if line.startswith("+++ b/") else None
        elif line.startswith("--- "):
            continue
        elif line.startswith("@@"):
            flush(); minus, plus = [], []
        elif line.startswith("-"):
            minus.append(line[1:])
        elif line.startswith("+"):
            plus.append(line[1:])
    flush()
    return tweaks


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def extractors_for(path: str, text: str, cfg: dict) -> list[tuple[str, dict]]:
    out: list[tuple[str, dict]] = []
    for spec in cfg["extractors"]:
        if spec.get("paths") and not C.match_any(path, spec["paths"]):
            continue
        if spec.get("exclude") and C.match_any(path, spec["exclude"]):
            continue
        names = auto_extractors(path, text) if spec["use"] == "auto" else [spec["use"]]
        for n in names:
            if n not in [x[0] for x in out]:
                out.append((n, spec.get("options") or {}))
    return out


_C_NUMEXPR = re.compile(r"(?:[-+*/()\s<>|&~]|0[xX][0-9a-fA-F]+[uUlL]*|\d[\d_.]*(?:[eE][-+]?\d+)?[fFuUlL]*)+")


def _const_literal(v: str) -> bool:
    v = str(v).strip()
    return bool(LITERALISH.fullmatch(v) or (_C_NUMEXPR.fullmatch(v) and re.search(r"\d", v)))


def change_metrics(old, new) -> dict:
    o, n = parse_num(old), parse_num(new)
    if o is None or n is None:
        return {"pct": None, "ratio": None}
    pct = None if o == 0 else round((n - o) / abs(o) * 100, 4)
    ratio = None if o == 0 or (o < 0) != (n < 0) else round(n / o, 6)
    return {"pct": pct, "ratio": ratio}


def list_items(v) -> list[str] | None:
    """Items of a flat list value "[a, b]" (raw strings), or None."""
    v = str(v).strip() if v is not None else ""
    if not (v.startswith("[") and v.endswith("]")):
        return None
    from .yamlmini import _split_flow
    return _split_flow(v[1:-1])


def same_value(a: str, b: str) -> bool:
    if a == b:
        return True
    if str(a).lower() == str(b).lower() and str(a).lower() in ("true", "false"):
        return True  # YAML True == true
    la, lb = list_items(a), list_items(b)
    if la is not None and lb is not None:
        return len(la) == len(lb) and all(same_value(unquote(x), unquote(y)) for x, y in zip(la, lb))
    x, y = parse_num(a), parse_num(b)
    return x is not None and y is not None and x == y and infer_type(a) == infer_type(b) and not ("." in a) ^ ("." in b)


def classify_component(path: str, cfg: dict) -> str | None:
    comp = cfg["components"]
    if comp.get("exclude") and re.search(comp["exclude"], path):
        return None
    for r in comp["rules"]:
        if re.search(r["pattern"], path):
            return r["kind"]
    return None


GENERIC_NAMES = {"filter", "task_filter", "filters", "scorer", "scorers", "source", "sources", "rules", "rule"}


def component_name(path: str) -> str:
    base = os.path.splitext(os.path.basename(path))[0]
    if base.lower() in GENERIC_NAMES:
        return f"{os.path.basename(os.path.dirname(path))}/{base}"
    return base


def top_module(path: str) -> str:
    return path.split("/", 1)[0] if "/" in path else "(repo root)"


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------
def analyze_commit(repo: str, meta: CommitMeta, cfg: dict, repo_url: str) -> dict:
    parent = meta.parents[0] if meta.parents else EMPTY_TREE
    noise = [re.compile(p) for p in cfg["noise"]["line_patterns"]]
    all_files = changed_files(repo, parent, meta.sha)
    files = [f for f in all_files if C.watched(f.path, cfg) and not C.match_any(f.path, cfg["noise"]["ignore_paths"])]
    blobs = BlobReader(repo)
    file_kinds: dict[str, str] = {}
    old_vals: dict[tuple[str, str, str], dict] = {}   # (extractor, file, name) -> record
    new_vals: dict[tuple[str, str, str], dict] = {}
    lfs: list[dict] = []
    try:
        for fc in files:
            old_path = fc.old_path or fc.path
            old = blobs.text(parent, old_path) if fc.status != "A" and parent != EMPTY_TREE else None
            new = blobs.text(meta.sha, fc.path) if fc.status != "D" else None
            file_kinds[fc.path] = classify_file_change(fc.path, old, new, noise) if fc.status in ("M", "R") else "code"
            if cfg.get("lfs"):
                lt = [t for t in (old, new) if t and t.startswith("version https://git-lfs")]
                if lt:
                    size = lambda t: int(m.group(1)) if t and (m := re.search(r"^size (\d+)", t, re.M)) else None
                    lfs.append({"file": fc.path, "status": fc.status, "old_size": size(old), "new_size": size(new)})
                    continue
            if file_kinds[fc.path] != "code":
                continue
            for text, bucket, path in ((old, old_vals, old_path), (new, new_vals, fc.path)):
                if not text:
                    continue
                for ext, opts in extractors_for(path, text, cfg):
                    if ext in CONST_LIKE and fc.status not in ("M", "R"):
                        continue
                    for name, rec in run_extractor(ext, path, text, opts).items():
                        bucket[(ext, fc.path, name)] = rec
    finally:
        blobs.close()

    changes: list[dict] = []
    gone = [k for k in old_vals if k not in new_vals]
    fresh = [k for k in new_vals if k not in old_vals]
    fam = lambda e: FAMILY.get(e, e)
    gone_by = {(fam(e), n): (e, f) for e, f, n in gone}
    fresh_names = {(fam(e), n) for e, f, n in fresh}
    for key in sorted(set(old_vals) | set(new_vals)):
        ext, f, name = key
        o, n = old_vals.get(key), new_vals.get(key)
        base = {"extractor": ext, "file": f, "name": name, "keyed": ext in KEYED}
        if o and n:
            if same_value(o["value"], n["value"]) and o.get("decl_type") == n.get("decl_type"):
                continue
            if ext in CONST_LIKE and not (_const_literal(o["value"]) and _const_literal(n["value"])):
                continue  # expressions/calls on either side: not a value change we can state exactly
            changes.append({**base, "kind": "changed", "old": o["value"], "new": n["value"], "type": n["type"],
                            **{k: n[k] for k in ("decl_type", "key") if k in n}, **change_metrics(o["value"], n["value"])})
        elif n:
            src = gone_by.get((fam(ext), name))
            if src is not None:
                ov = old_vals[(src[0], src[1], name)]
                same = same_value(ov["value"], n["value"])
                changes.append({**base, "kind": "moved", "from": src[1], "old": n["value"] if same else ov["value"], "new": n["value"], "type": n["type"],
                                **({"old_literal": ov["value"]} if same and ov["value"] != n["value"] else {}),
                                **{k: n[k] for k in ("decl_type", "key") if k in n}, **change_metrics(ov["value"], n["value"])})
            elif ext not in CONST_LIKE or name.isupper():
                changes.append({**base, "kind": "added", "old": None, "new": n["value"], "type": n["type"],
                                **{k: n[k] for k in ("decl_type", "key") if k in n}, "pct": None, "ratio": None})
        elif o and (fam(ext), name) not in fresh_names and (ext not in CONST_LIKE or name.isupper()):
            changes.append({**base, "kind": "removed", "old": o["value"], "new": None, "type": o["type"],
                            **{k: o[k] for k in ("decl_type", "key") if k in o}, "pct": None, "ratio": None})

    changes = _refine_keyed(changes, {fc.path for fc in files if fc.status == "D"})

    if cfg.get("track_moves"):
        _mark_still_defined(repo, meta.sha, cfg, [c for c in changes if c["kind"] == "removed" and c["extractor"] not in CONST_LIKE])

    comps: dict[str, list] = {"added": [], "removed": [], "renamed": [], "changed": []}
    for fc in files:
        kind = classify_component(fc.path, cfg)
        old_kind = classify_component(fc.old_path, cfg) if fc.old_path else kind
        if fc.status == "A" and kind:
            comps["added"].append({"kind": kind, "name": component_name(fc.path), "path": fc.path})
        elif fc.status == "D" and kind:
            comps["removed"].append({"kind": kind, "name": component_name(fc.path), "path": fc.path})
        elif fc.status == "R" and (kind or old_kind):
            if component_name(fc.old_path) != component_name(fc.path):
                comps["renamed"].append({"kind": kind or old_kind, "old": component_name(fc.old_path), "new": component_name(fc.path), "path": fc.path})
            elif file_kinds.get(fc.path) == "code" and kind:
                comps["changed"].append({"kind": kind, "name": component_name(fc.path), "path": fc.path})
        elif fc.status == "M" and kind and file_kinds.get(fc.path) == "code":
            comps["changed"].append({"kind": kind, "name": component_name(fc.path), "path": fc.path})

    tweaks: list[dict] = []
    if cfg.get("tweaks") and parent != EMPTY_TREE:
        covered = {c["file"] for c in changes} | {l["file"] for l in lfs}
        known = {(c["file"], c["name"]) for c in changes}
        tweaks = [t for t in numeric_tweaks(unified_diff(repo, parent, meta.sha, context=0), noise)
                  if C.watched(t["file"], cfg) and file_kinds.get(t["file"]) == "code" and t["changes"]
                  and t["file"] not in covered and (t["file"], t["label"]) not in known]

    modules: dict[str, dict] = defaultdict(lambda: {"added": 0, "removed": 0, "modified": 0, "lines_added": 0, "lines_deleted": 0})
    for fc in files:
        m = modules[top_module(fc.path)]
        m[{"A": "added", "D": "removed"}.get(fc.status, "modified")] += 1
        m["lines_added"] += fc.added
        m["lines_deleted"] += fc.deleted
    kinds = Counter(file_kinds.values())
    meaningful = [fc for fc in files if file_kinds.get(fc.path) == "code"]
    if not meaningful:
        cls = "noop"
    elif all(os.path.splitext(fc.path)[1] in DOC_EXTS for fc in meaningful):
        cls = "docs"
    else:
        cls = "code"
    return {
        "sha": meta.sha, "short": meta.sha[:7], "url": f"{repo_url}/commit/{meta.sha}" if repo_url else "",
        "date": meta.date, "author": meta.author, "subject": meta.subject, "body": meta.body,
        "is_merge": len(meta.parents) > 1, "is_root": not meta.parents, "parent": meta.parents[0] if meta.parents else None,
        "class": cls,
        "stats": {"files": len(files), "files_total": len(all_files), "added_files": sum(f.status == "A" for f in files),
                  "removed_files": sum(f.status == "D" for f in files), "lines_added": sum(f.added for f in files),
                  "lines_deleted": sum(f.deleted for f in files), "noise_files": kinds.get("noise", 0),
                  "whitespace_files": kinds.get("whitespace", 0), "comment_only_files": kinds.get("comments", 0)},
        "modules": dict(sorted(modules.items(), key=lambda kv: -(kv[1]["lines_added"] + kv[1]["lines_deleted"]))),
        "files": [{"status": f.status, "path": f.path, "old_path": f.old_path, "added": f.added, "deleted": f.deleted,
                   "kind": file_kinds.get(f.path, "code")} for f in files],
        "changes": changes, "components": comps, "tweaks": tweaks, "lfs": lfs,
    }


def _mark_still_defined(repo: str, sha: str, cfg: dict, removed: list[dict]) -> None:
    """A name that vanished from a changed file may still be defined in another file (it moved). Check with git grep."""
    if not removed or len(removed) > 200:
        return
    names = sorted({c["name"].split(".")[-1] for c in removed})
    pattern = "|".join(re.escape(n) for n in names)
    out = run(["grep", "-l", "-E", rf"\b({pattern})\b", sha, "--", *[f":(glob){p}" for p in cfg["watch"]["paths"]]], cwd=repo, check=False)
    candidates = [l.split(":", 1)[1] for l in out.splitlines() if ":" in l]
    candidates = [p for p in candidates if C.watched(p, cfg)][:50]
    if not candidates:
        return
    blobs = BlobReader(repo)
    try:
        found: dict[tuple[str, str], str] = {}
        for path in candidates:
            text = blobs.text(sha, path)
            if not text:
                continue
            for ext, opts in extractors_for(path, text, cfg):
                for name in run_extractor(ext, path, text, opts):
                    found.setdefault((ext, name), path)
        for c in removed:
            p = found.get((c["extractor"], c["name"]))
            if p and p != c["file"]:
                c["still_defined_in"] = p
    finally:
        blobs.close()


def _refine_keyed(changes: list[dict], deleted_files: set[str]) -> list[dict]:
    """Structured-config post-processing.

    * numeric lists of equal length that changed are split per element (max_velocity[0] 0.26 -> 0.5);
    * a top-level section renamed with identical values (dwb_controller.* -> nav2_controller.*) becomes one
      "renamed" record per key instead of N removed + N added;
    * removals caused by deleting a whole file are flagged (file_deleted) so summaries can collapse them.
    """
    out: list[dict] = []
    for c in changes:
        if c["kind"] == "changed" and c.get("keyed") and c["type"] == "list":
            lo, ln = list_items(c["old"]), list_items(c["new"])
            if lo and ln and len(lo) == len(ln) and all(parse_num(x) is not None for x in lo + ln):
                for i, (a, b) in enumerate(zip(lo, ln)):
                    if not same_value(a, b):
                        out.append({**c, "name": f"{c['name']}[{i}]", "old": a, "new": b, "type": "number",
                                    "list_index": i, **change_metrics(a, b)})
                continue
        out.append(c)
    removed = [c for c in out if c["kind"] == "removed" and c.get("keyed")]
    added = [c for c in out if c["kind"] == "added" and c.get("keyed")]
    split = lambda n: n.split(".", 1) if "." in n else (None, n)
    add_by = {}
    for c in added:
        head, rest = split(c["name"])
        if head is not None:
            add_by.setdefault((c["extractor"], c["file"], rest), []).append(c)
    pairs: dict[tuple[str, str, str], list[tuple[dict, dict]]] = {}
    for c in removed:
        head, rest = split(c["name"])
        for a in add_by.get((c["extractor"], c["file"], rest), []):
            if same_value(c["old"], a["new"]) and split(a["name"])[0] != head:
                pairs.setdefault((c["file"], head, split(a["name"])[0]), []).append((c, a))
                break
    drop: set[int] = set()
    for (f, old_head, new_head), ps in pairs.items():
        total = sum(1 for c in removed if c["file"] == f and split(c["name"])[0] == old_head)
        if len(ps) >= 3 and len(ps) >= 0.8 * total:
            for r, a in ps:
                drop.add(id(r))
                a.update(kind="renamed", old_name=r["name"], old=r["old"], pct=None, ratio=None)
    out = [c for c in out if id(c) not in drop]
    for c in out:
        if c["kind"] == "removed" and c["file"] in deleted_files:
            c["file_deleted"] = True
    return out
