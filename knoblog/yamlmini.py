"""A small, dependency-free YAML subset reader that keeps scalars as their raw source text.

Why not PyYAML? Two reasons: knoblog is stdlib-first, and change notes must quote numbers exactly as
written ("0.50" stays "0.50"), which a type-resolving loader cannot do.

Supported: block mappings and sequences (any indent), "- key: value" items, flow [..] / {..}
collections (also multi-line), single/double-quoted scalars, literal/folded block scalars (| >),
comments, document markers. Not supported: anchors/aliases/tags/complex keys (they are kept as raw
strings or raise YamlError). Every scalar is returned as a str; quoted scalars keep their quotes so
callers can tell "1.0" (a string) from 1.0 (a number).
"""
from __future__ import annotations

import re


class YamlError(ValueError):
    pass


def _strip_comment(s: str) -> str:
    """Remove a trailing ' # comment' that is not inside quotes."""
    q = None
    for i, ch in enumerate(s):
        if q:
            if ch == q and not (q == '"' and i and s[i - 1] == "\\"):
                q = None
        elif ch in "'\"" and (i == 0 or s[i - 1] in " \t[{,:-"):
            q = ch
        elif ch == "#" and (i == 0 or s[i - 1] in " \t"):
            return s[:i].rstrip()
    return s.rstrip()


def _split_key(s: str) -> tuple[str, str] | None:
    """'key: value' -> (key, value); returns None if s is not a mapping entry."""
    q = None
    depth = 0
    for i, ch in enumerate(s):
        if q:
            if ch == q:
                q = None
            continue
        if ch in "'\"" and i == 0:
            q = ch
        elif ch in "[{":
            depth += 1
        elif ch in "]}":
            depth -= 1
        elif ch == ":" and depth == 0 and (i + 1 == len(s) or s[i + 1] in " \t"):
            key = s[:i].strip()
            if len(key) >= 2 and key[0] == key[-1] and key[0] in "'\"":
                key = key[1:-1]
            return key, s[i + 1:].strip()
    return None


def _split_flow(body: str) -> list[str]:
    parts, depth, q, cur = [], 0, None, []
    for ch in body:
        if q:
            cur.append(ch)
            if ch == q:
                q = None
            continue
        if ch in "'\"":
            q = ch
        elif ch in "[{":
            depth += 1
        elif ch in "]}":
            depth -= 1
        elif ch == "," and depth == 0:
            parts.append("".join(cur).strip()); cur = []
            continue
        cur.append(ch)
    if "".join(cur).strip():
        parts.append("".join(cur).strip())
    return parts


def parse_flow(s: str):
    s = s.strip()
    if s.startswith("[") and s.endswith("]"):
        return [parse_flow(p) for p in _split_flow(s[1:-1])]
    if s.startswith("{") and s.endswith("}"):
        out = {}
        for p in _split_flow(s[1:-1]):
            kv = _split_key(p) if ": " in p or p.endswith(":") else None
            if kv is None:
                out[p] = None
            else:
                out[kv[0]] = parse_flow(kv[1]) if kv[1] else None
        return out
    return s


def _balanced(s: str) -> bool:
    depth, q = 0, None
    for ch in s:
        if q:
            if ch == q:
                q = None
        elif ch in "'\"":
            q = ch
        elif ch in "[{":
            depth += 1
        elif ch in "]}":
            depth -= 1
    return depth <= 0


class _Parser:
    def __init__(self, text: str):
        self.raw = text.replace("\t", "    ").splitlines()
        self.i = 0

    def _next(self):
        """Next meaningful line as (index, indent, content) without consuming it."""
        j = self.i
        while j < len(self.raw):
            line = self.raw[j]
            content = _strip_comment(line)
            if content.strip() and content.strip() not in ("---", "...") and not content.strip().startswith("%"):
                return j, len(content) - len(content.lstrip(" ")), content.strip()
            j += 1
        return None

    def parse_node(self, indent: int):
        nxt = self._next()
        if nxt is None or nxt[1] < indent:
            return None
        j, ind, content = nxt
        if content == "-" or content.startswith("- "):
            return self.parse_seq(ind)
        return self.parse_map(ind)

    def _value(self, rest: str, ind: int):
        """Parse the value part after 'key:' or '- '."""
        if rest == "" or rest.startswith("&"):
            nxt = self._next()
            if nxt and nxt[1] > ind and (nxt[2][0] in "[{" or (_split_key(nxt[2]) is None and not (nxt[2] == "-" or nxt[2].startswith("- ")))):
                # value on the following, more-indented line(s): a flow collection or a plain/quoted scalar
                self.i = nxt[0] + 1
                if nxt[2][0] in "[{":
                    return self._value(nxt[2], ind)
                parts = [nxt[2]]
                while (n2 := self._next()) and n2[1] > ind and _split_key(n2[2]) is None:
                    parts.append(n2[2]); self.i = n2[0] + 1
                return " ".join(parts)
            if nxt and (nxt[1] > ind or (nxt[1] == ind and (nxt[2] == "-" or nxt[2].startswith("- ")))):
                return self.parse_node(nxt[1])
            return None
        if re.match(r"^[|>][-+0-9]*$", rest):
            lines = []
            while self.i < len(self.raw):
                line = self.raw[self.i]
                if line.strip() and len(line) - len(line.lstrip(" ")) <= ind:
                    break
                lines.append(line.strip())
                self.i += 1
            body = ("\n" if rest[0] == "|" else " ").join(lines).strip()
            return body if "-" in rest else body + "\n"  # clip/keep chomping -> one trailing newline
        if rest[0] in "[{":
            buf = rest
            while not _balanced(buf) and self.i < len(self.raw):
                buf += " " + _strip_comment(self.raw[self.i]).strip()
                self.i += 1
            if not _balanced(buf):
                raise YamlError(f"unterminated flow collection: {buf[:40]!r}")
            return parse_flow(buf)
        # plain or quoted scalar continued on more-indented lines (valid YAML; PX4 module.yaml uses it)
        parts = [rest]
        while (nxt := self._next()) and nxt[1] > ind:
            if rest[0] not in "'\"" and _split_key(nxt[2]) is not None:
                raise YamlError(f"line {nxt[0] + 1}: unexpected indent (mapping inside a scalar)")
            parts.append(nxt[2]); self.i = nxt[0] + 1
        return " ".join(parts)

    def parse_map(self, indent: int) -> dict:
        out: dict = {}
        while True:
            nxt = self._next()
            if nxt is None or nxt[1] < indent:
                return out
            j, ind, content = nxt
            if ind > indent:
                raise YamlError(f"line {j + 1}: unexpected indent")
            if content == "-" or content.startswith("- "):
                return out  # a sequence at the same indent belongs to the parent key
            kv = _split_key(content)
            if kv is None:
                raise YamlError(f"line {j + 1}: expected 'key: value', got {content!r}")
            self.i = j + 1
            out[kv[0]] = self._value(kv[1], ind)

    def parse_seq(self, indent: int) -> list:
        out: list = []
        while True:
            nxt = self._next()
            if nxt is None or nxt[1] != indent or not (nxt[2] == "-" or nxt[2].startswith("- ")):
                return out
            j, ind, content = nxt
            rest = content[1:].strip()
            if rest and _split_key(rest) and not rest.startswith(("[", "{", '"', "'")):
                # "- key: value" starts an inline mapping; continue it at the column of "key".
                col = ind + (len(content) - len(content[1:].lstrip()))
                self.raw[j] = " " * col + rest
                self.i = j
                out.append(self.parse_map(col))
            else:
                self.i = j + 1
                out.append(self._value(rest, ind))


def loads(text: str):
    """Parse the first YAML document in `text` (raw-string scalars)."""
    return _Parser(text).parse_node(0)


def unquote(v):
    if isinstance(v, str) and len(v) >= 2 and v[0] == v[-1] and v[0] in "'\"":
        return v[1:-1]
    return v


def to_python(v):
    """Raw tree -> plain Python values (for config files): numbers, bools, null, unquoted strings."""
    if isinstance(v, dict):
        return {k: to_python(x) for k, x in v.items()}
    if isinstance(v, list):
        return [to_python(x) for x in v]
    if v is None:
        return None
    s = v
    if len(s) >= 2 and s[0] == s[-1] and s[0] in "'\"":
        return s[1:-1].replace("\\\\", "\\") if s[0] == '"' else s[1:-1]
    low = s.lower()
    if low in ("true", "yes", "on"):
        return True
    if low in ("false", "no", "off"):
        return False
    if low in ("null", "~"):
        return None
    if re.fullmatch(r"[-+]?\d+", s):
        return int(s)
    if re.fullmatch(r"[-+]?(\d+\.\d*|\.\d+|\d+)([eE][-+]?\d+)?", s):
        return float(s)
    return s
