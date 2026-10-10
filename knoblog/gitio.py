"""Thin wrappers around the git CLI (no third-party deps)."""
from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass

EMPTY_TREE = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"


def run(args: list[str], cwd: str | None = None, check: bool = True) -> str:
    res = subprocess.run(["git", *args], cwd=cwd, capture_output=True, check=check)
    return res.stdout.decode("utf-8", errors="replace")


def ensure_clone(url: str, dest: str, branch: str | None = None, partial: bool = False) -> None:
    """Clone `url` into `dest` (bare, no checkout needed), or fetch if it already exists.

    partial=True uses a blob-less clone: history is complete but file contents are fetched lazily,
    which keeps large repos (PX4, ArduPilot) fast when only a few paths are watched.
    """
    if os.path.isdir(os.path.join(dest, ".git")) or os.path.exists(os.path.join(dest, "HEAD")):
        run(["fetch", "--quiet", "--prune", "origin", "+refs/heads/*:refs/remotes/origin/*"], cwd=dest)
    else:
        os.makedirs(os.path.dirname(os.path.abspath(dest)), exist_ok=True)
        args = ["git", "clone", "--quiet", "--no-checkout"]
        if partial:
            args.append("--filter=blob:none")
        if branch:
            args += ["--branch", branch]
        subprocess.run(args + [url, dest], check=True)


def head_rev(repo: str, branch: str | None = None) -> str:
    """The commit to analyse: origin/<branch> when the repo is a knoblog-managed clone, else HEAD."""
    for ref in ([f"origin/{branch}"] if branch else []) + ["origin/HEAD", "HEAD"]:
        out = run(["rev-parse", "--verify", "--quiet", ref + "^{commit}"], cwd=repo, check=False).strip()
        if out:
            return out
    raise SystemExit(f"no commits found in {repo}")


def default_branch(repo: str) -> str:
    out = run(["symbolic-ref", "--short", "refs/remotes/origin/HEAD"], cwd=repo, check=False).strip()
    return out.split("/", 1)[1] if "/" in out else "main"


@dataclass
class CommitMeta:
    sha: str
    parents: list[str]
    author: str
    date: str  # ISO 8601 strict, committer date
    subject: str
    body: str


def list_commits(repo: str, rev: str = "HEAD", paths: list[str] | None = None, max_commits: int | None = None,
                 since: str | None = None, first_parent: bool = True) -> list[CommitMeta]:
    """History, oldest first (the newest `max_commits` are kept). Merges are diffed against their first parent."""
    sep, end = "\x1f", "\x1e"
    fmt = sep.join(["%H", "%P", "%an", "%cI", "%s", "%b"]) + end
    args = ["log", f"--format={fmt}"] + (["--first-parent"] if first_parent else [])
    if max_commits:
        args.append(f"--max-count={int(max_commits)}")
    if since:
        args.append(f"--since={since}")
    args.append(rev)
    if paths:
        args += ["--", *[f":(glob){p}" for p in paths]]
    out = run(args, cwd=repo)
    commits = []
    for rec in out.split(end):
        rec = rec.strip("\n")
        if not rec:
            continue
        h, p, an, d, s, b = rec.split(sep)
        commits.append(CommitMeta(h, p.split() if p else [], an, d, s, b.strip()))
    commits.reverse()
    return commits


@dataclass
class FileChange:
    status: str  # A, M, D, R
    path: str
    old_path: str | None
    added: int = 0
    deleted: int = 0
    binary: bool = False


def changed_files(repo: str, parent: str, sha: str) -> list[FileChange]:
    ns = run(["diff", "--name-status", "-M", "-z", parent, sha], cwd=repo).split("\0")
    changes: list[FileChange] = []
    i = 0
    while i < len(ns) and ns[i]:
        st = ns[i]
        if st.startswith(("R", "C")):
            changes.append(FileChange(st[0], ns[i + 2], ns[i + 1]))
            i += 3
        else:
            changes.append(FileChange(st[0], ns[i + 1], None))
            i += 2
    stats: dict[str, tuple[int, int, bool]] = {}
    out = run(["diff", "--numstat", "-M", "-z", parent, sha], cwd=repo)
    parts = out.split("\0")
    j = 0
    while j < len(parts):
        rec = parts[j]
        if not rec:
            j += 1
            continue
        a, d, path = rec.split("\t", 2)
        if path == "":  # rename: next two fields are old, new
            path = parts[j + 2]
            j += 3
        else:
            j += 1
        binary = a == "-"
        stats[path] = (0 if binary else int(a), 0 if binary else int(d), binary)
    for c in changes:
        if c.path in stats:
            c.added, c.deleted, c.binary = stats[c.path]
    return changes


def unified_diff(repo: str, parent: str, sha: str, context: int = 0) -> str:
    return run(["diff", f"-U{context}", "-M", "--no-color", parent, sha], cwd=repo)


class BlobReader:
    """Batch blob reader using `git cat-file --batch`."""

    def __init__(self, repo: str):
        self.proc = subprocess.Popen(
            ["git", "cat-file", "--batch"], cwd=repo, stdin=subprocess.PIPE, stdout=subprocess.PIPE
        )

    def read(self, rev: str, path: str) -> bytes | None:
        assert self.proc.stdin and self.proc.stdout
        self.proc.stdin.write(f"{rev}:{path}\n".encode())
        self.proc.stdin.flush()
        header = self.proc.stdout.readline().decode()
        if header.endswith("missing\n") or not header.strip():
            return None
        size = int(header.split()[2])
        data = self.proc.stdout.read(size)
        self.proc.stdout.read(1)  # trailing newline
        return data

    def text(self, rev: str, path: str, limit: int = 2_000_000) -> str | None:
        data = self.read(rev, path)
        if data is None or len(data) > limit or b"\0" in data[:8000]:
            return None
        return data.decode("utf-8", errors="replace")

    def close(self) -> None:
        if self.proc.stdin:
            self.proc.stdin.close()
        self.proc.wait()
