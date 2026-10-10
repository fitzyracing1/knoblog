"""Config loading: YAML (stdlib subset parser, or PyYAML if installed), JSON or TOML; presets via `extends:`."""
from __future__ import annotations

import copy
import json
import os
import re

from . import yamlmini

PRESET_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "presets")

DEFAULTS: dict = {
    "name": None,                     # label used in post headers; defaults to the repo name
    "repo": None,                     # URL or local path (CLI --repo wins)
    "repo_url": None,                 # browsable https URL for commit links (derived from repo when it is a GitHub URL)
    "branch": None,
    "clone": {"partial": True},
    "history": {"max_commits": 300, "since": None, "first_parent": True, "limit_log_to_watched_paths": True},
    "watch": {"paths": ["**"], "exclude": []},
    "extractors": [{"use": "auto"}],
    "noise": {"line_patterns": [], "ignore_paths": []},
    "components": {"rules": [], "exclude": r"(^|/)(tests?|test_[^/]*|[^/]*_test\.\w+|[^/]*Test\.\w+|mod\.rs|__init__\.py)$|/tests?/"},
    "highlight": [],                  # [{pattern, label, penalty_if_negative}]
    "tweaks": False,                  # diff-based literal tweaks (- / + line pairs differing only in numbers)
    "lfs": True,
    "track_moves": True,              # git-grep removed names to tell "moved to another file" from "deleted"
    "summaries": {"names": "raw", "home_files": [], "notes_dir": None, "llm": False, "llm_cache_dir": None, "llm_limit": 10},
    "site": {"title": None, "tagline": "Plain-English notes for every parameter, threshold, flag and config change.",
             "url": "", "project_url": "", "disclaimer": "", "author": "knoblog"},
    "post": {"enabled": True, "header": "{name} update ({date}): ",
             "disclaimer": "Auto-generated from the repo diff; values are repo defaults.",
             "names": "raw", "min_change_pct": 0, "const_min_change_pct": 20,
             "component_kinds": ["Candidate source", "Filter", "Scorer"], "component_paths": ["**"],
             "new_values": True,
             "exclude_paths": ["**/test/**", "**/tests/**", "**/testing/**", "**/testdata/**", "**/*_test.*", "**/test_*", "**/*Test.*"],
             "const_trigger_paths": ["**"], "const_trigger_names": ".*",
             "bulk_added_limit": 50, "max_posts": 4, "max_facts": 4},
}


def _merge(base: dict, over: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


def parse_text(text: str, path: str = "config.yml") -> dict:
    if path.endswith(".json"):
        return json.loads(text)
    if path.endswith(".toml"):
        import tomllib
        return tomllib.loads(text)
    try:
        import yaml  # optional
        return yaml.safe_load(text) or {}
    except ModuleNotFoundError:
        return yamlmini.to_python(yamlmini.loads(text)) or {}


def preset_path(name: str) -> str | None:
    p = os.path.join(PRESET_DIR, f"{name}.yml")
    return p if os.path.exists(p) else None


def presets() -> list[str]:
    return sorted(f[:-4] for f in os.listdir(PRESET_DIR) if f.endswith(".yml"))


def load(path: str | None = None, preset: str | None = None, overrides: dict | None = None) -> dict:
    """DEFAULTS <- preset (or `extends:` in the file) <- config file <- overrides."""
    cfg = copy.deepcopy(DEFAULTS)
    data: dict = {}
    if path:
        if not os.path.exists(path) and preset_path(path):
            path = preset_path(path)
        with open(path, encoding="utf-8") as f:
            data = parse_text(f.read(), path) or {}
        data["_dir"] = os.path.dirname(os.path.abspath(path))
    base = preset or data.pop("extends", None)
    if base:
        bp = preset_path(base)
        if not bp:
            raise SystemExit(f"unknown preset {base!r}; available: {', '.join(presets())}")
        with open(bp, encoding="utf-8") as f:
            cfg = _merge(cfg, parse_text(f.read(), bp))
    cfg = _merge(cfg, data)
    cfg = _merge(cfg, overrides or {})
    validate(cfg)
    return cfg


def validate(cfg: dict) -> None:
    from .extractors import REGISTRY
    for e in cfg["extractors"]:
        if e.get("use") != "auto" and e.get("use") not in REGISTRY:
            raise SystemExit(f"unknown extractor {e.get('use')!r}; available: auto, {', '.join(sorted(REGISTRY))}")
    for rx in cfg["noise"]["line_patterns"] + [h["pattern"] for h in cfg["highlight"]] + [r["pattern"] for r in cfg["components"]["rules"]]:
        re.compile(rx)


# ---------------------------------------------------------------------------
# Globs: "**" matches across directories, "*" within one.
# ---------------------------------------------------------------------------
_glob_cache: dict[str, re.Pattern] = {}


def glob_re(pattern: str) -> re.Pattern:
    if pattern not in _glob_cache:
        i, out = 0, []
        while i < len(pattern):
            c = pattern[i]
            if pattern.startswith("**/", i):
                out.append("(?:.*/)?"); i += 3; continue
            if pattern.startswith("**", i):
                out.append(".*"); i += 2; continue
            out.append({"*": "[^/]*", "?": "[^/]"}.get(c, re.escape(c)))
            i += 1
        _glob_cache[pattern] = re.compile("^" + "".join(out) + "$")
    return _glob_cache[pattern]


def match_any(path: str, patterns: list[str]) -> bool:
    return any(glob_re(p).match(path) for p in patterns)


def watched(path: str, cfg: dict) -> bool:
    w = cfg["watch"]
    return match_any(path, w["paths"]) and not match_any(path, w.get("exclude") or [])
