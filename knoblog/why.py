""""Why it changed": the commit message and, when findable, the pull request behind each entry.

Offline first: PR numbers are read from the commit subject/body (``Fix foo (#123)``, ``Merge pull request #123``).
With ``--pr-lookup`` (or ``why.pr_lookup: true``) the GitHub API ``/commits/{sha}/pulls`` endpoint is asked as
well, using ``GITHUB_TOKEN``/``GH_TOKEN`` when set. Results are cached in a JSON file so reruns are free.
Network failures never fail a run; the entry simply has no PR.
"""
from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request

PR_PATTERNS = [
    re.compile(r"^Merge pull request #(\d+)\b"),
    re.compile(r"\(#(\d+)\)\s*$"),
    re.compile(r"\(#(\d+)\)"),
]


def github_slug(repo_url: str) -> str | None:
    m = re.match(r"^https://github\.com/([^/]+/[^/]+?)/?$", repo_url or "")
    return m.group(1) if m else None


def pr_from_message(subject: str, body: str = "") -> int | None:
    for rx in PR_PATTERNS:
        m = rx.search(subject or "")
        if m:
            return int(m.group(1))
    m = re.search(r"(?mi)^\s*(?:pull request|pr)[: ]+#?(\d+)\s*$", body or "")
    return int(m.group(1)) if m else None


def first_paragraph(body: str, limit: int = 600) -> str:
    para = (body or "").strip().split("\n\n", 1)[0].strip()
    para = "\n".join(l for l in para.splitlines() if not re.match(r"^\s*(Signed-off-by|Co-authored-by|Change-Id):", l, re.I)).strip()
    return para if len(para) <= limit else para[: limit - 1].rstrip() + "…"


class PRLookup:
    def __init__(self, slug: str | None, enabled: bool, cache_path: str | None = None, max_calls: int = 200):
        self.slug, self.enabled, self.cache_path, self.max_calls = slug, enabled and bool(slug), cache_path, max_calls
        self.calls = 0
        self.cache: dict = {}
        if cache_path and os.path.exists(cache_path):
            try:
                self.cache = json.load(open(cache_path, encoding="utf-8"))
            except (OSError, ValueError):
                self.cache = {}

    def lookup(self, sha: str) -> dict | None:
        if sha in self.cache:
            return self.cache[sha]
        if not self.enabled or self.calls >= self.max_calls:
            return None
        self.calls += 1
        req = urllib.request.Request(f"https://api.github.com/repos/{self.slug}/commits/{sha}/pulls",
                                     headers={"Accept": "application/vnd.github+json", "User-Agent": "knoblog"})
        tok = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
        if tok:
            req.add_header("Authorization", f"Bearer {tok}")
        try:
            with urllib.request.urlopen(req, timeout=15) as r:
                data = json.load(r)
        except (urllib.error.URLError, OSError, ValueError):
            return None  # not cached: may succeed next time
        merged = [p for p in data if isinstance(p, dict) and p.get("merged_at")] or [p for p in data if isinstance(p, dict)]
        hit = {"number": merged[0]["number"], "url": merged[0]["html_url"], "title": merged[0].get("title", "")} if merged else None
        self.cache[sha] = hit
        return hit

    def save(self) -> None:
        if self.cache_path and self.cache:
            os.makedirs(os.path.dirname(os.path.abspath(self.cache_path)), exist_ok=True)
            with open(self.cache_path, "w", encoding="utf-8") as f:
                json.dump(self.cache, f, indent=1, sort_keys=True)


def annotate(entry: dict, repo_url: str, lookup: PRLookup | None = None) -> dict:
    """Attach entry['why'] = {subject, message, pr_number, pr_url, pr_title, pr_source}."""
    why = {"subject": entry.get("subject", ""), "message": first_paragraph(entry.get("body", "")),
           "pr_number": None, "pr_url": None, "pr_title": None, "pr_source": None}
    slug = github_slug(repo_url)
    hit = lookup.lookup(entry["sha"]) if lookup else None
    if hit:
        why.update(pr_number=hit["number"], pr_url=hit["url"], pr_title=hit.get("title") or None, pr_source="github-api")
    else:
        n = pr_from_message(entry.get("subject", ""), entry.get("body", ""))
        if n:
            why.update(pr_number=n, pr_url=f"https://github.com/{slug}/pull/{n}" if slug else None, pr_source="commit-message")
    entry["why"] = why
    return why
