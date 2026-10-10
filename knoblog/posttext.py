"""X-ready post text for each entry, WITHOUT posting.

Ported from the x-algorithm-changelog bot: X weighted character counting (URLs = 23), thread split
(at most 4 posts), and accuracy gates. Every number in the output must be an analysed value or
be computed from one (percent, ratio, duration, count); anything ambiguous yields a skip reason instead
of text.
"""
from __future__ import annotations

import math
import re
import unicodedata
from datetime import datetime, timezone

from . import config as C
from .analyze import CONST_LIKE
from .extractors import parse_num

MAX_CHARS = 280
URL_WEIGHT = 23

# ---------------------------------------------------------------------------
# X weighted length (twitter-text v3 rules; bare domains counted as URLs to be safe)
# ---------------------------------------------------------------------------
_LIGHT = ((0, 4351), (8192, 8205), (8208, 8223), (8242, 8247))
_URL_RE = re.compile(r"https?://\S+|\b[\w-]+(?:\.[\w-]+)*\.[a-z]{2,}\b(?:/\S*)?", re.I)


def _w(ch: str) -> int:
    cp = ord(ch)
    return 1 if any(lo <= cp <= hi for lo, hi in _LIGHT) else 2


def weighted_len(text: str) -> int:
    text = unicodedata.normalize("NFC", text)
    total, pos = 0, 0
    for m in _URL_RE.finditer(text):
        if not re.search(r"[a-z]", m.group(0), re.I):
            continue
        total += sum(_w(c) for c in text[pos:m.start()]) + URL_WEIGHT
        pos = m.end()
    return total + sum(_w(c) for c in text[pos:])


def bare_domains(text: str) -> list[str]:
    return [m.group(0) for m in _URL_RE.finditer(text)
            if not m.group(0).lower().startswith("http") and re.search(r"[a-z]", m.group(0), re.I)]


# ---------------------------------------------------------------------------
# Numbers and names
# ---------------------------------------------------------------------------
def fmt_num(v, exact: bool = False) -> str:
    """Readable: '0.0' -> '0', '125_000' -> '125,000', '0.95f' -> '0.95'. exact=True keeps the literal ('3.0')."""
    if exact:
        return str(v).strip()
    x = parse_num(v)
    if x is None:
        return str(v)
    if x == int(x) and abs(x) < 1e15 and not re.search(r"[eE]", str(v)):
        n = int(x)
        return f"{n:,}" if abs(n) >= 1000 else str(n)
    s = re.sub(r"(_?(f32|f64)|[fFlL]+)$", "", str(v).strip().strip("()").replace("_", "").replace("\u2212", "-"))
    return s.lstrip("+")


NUM_TOKEN = r"(?<![\w.])\d[\d,_]*\.?\d*(?:[eE][-+]?\d+)?"


class Derived:
    def __init__(self):
        self.values: set[float] = set()

    def add(self, s: str) -> str:
        x = parse_num(s.replace(",", ""))
        if x is not None:
            self.values.add(abs(x))
        return s


class Ambiguous(Exception):
    pass


_ACRONYMS = {"oon": "out-of-network", "vqv": "VQV", "vm": "VM", "moe": "MoE", "sid": "SID", "id": "ID", "ids": "IDs",
             "ts": "TS", "vf": "VF", "nsfw": "NSFW", "dm": "DM", "url": "URL", "thompson": "Thompson", "phoenix": "Phoenix",
             "grok": "Grok", "grox": "Grox", "pid": "PID", "ros": "ROS", "imu": "IMU", "gps": "GPS", "ekf": "EKF"}


def humanize(name: str) -> str:
    s = re.sub(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])", " ", name).replace("_", " ").replace(".", " ").lower().strip()
    return " ".join(_ACRONYMS.get(w, w) for w in s.split())


def display_name(c: dict, cfg: dict, strip: str | None = None) -> str:
    name = c["name"]
    if strip:
        name = re.sub(strip, "", name) or name
    if cfg["post"].get("names") == "human":
        return humanize(name)
    # raw: hierarchical keys use "/" so X doesn't auto-link "node.param" as a domain
    return name.replace(".", "/") if c.get("keyed") or "." in name else name


_DURATION_UNITS = (("Secs", 1), ("Seconds", 1), ("Minutes", 60), ("Hours", 3600), ("Ms", 0.001), ("Millis", 0.001))


def _human_duration(seconds: float) -> str | None:
    for unit, size in (("d", 86400), ("h", 3600), ("min", 60), ("s", 1)):
        if seconds >= size and seconds % size == 0:
            return f"{int(seconds // size)}{unit}"
    return None


def _round_pct(p: float) -> str:
    return f"{p:.1f}".rstrip("0").rstrip(".") if p < 10 else str(int(round(p)))


def change_suffix(old: float, new: float, d: Derived, penalty: bool = False, highlighted: bool = False) -> str:
    if old == new:
        return ""
    if old == 0:
        return " (previously unused)" if highlighted else ""
    if new == 0:
        return " (now unused)" if highlighted else ""
    if (old < 0) != (new < 0):
        return " (sign flipped)"
    ratio = abs(new) / abs(old)
    pct = abs(new - old) / abs(old) * 100
    if penalty:
        return f" ({d.add(_round_pct(pct))}% {'harsher' if abs(new) > abs(old) else 'softer'})"
    if ratio >= 2:
        return f" ({d.add(_round_pct(ratio))}x higher)"
    if ratio <= 0.5:
        return f" ({d.add(_round_pct(1 / ratio))}x lower)"
    return f" ({d.add(_round_pct(pct))}% {'higher' if new > old else 'lower'})"


def _magnitude(old: float, new: float) -> float:
    if old == new:
        return 0.0
    if old == 0 or new == 0 or (old < 0) != (new < 0):
        return 10.0
    return abs(math.log(abs(new) / abs(old)))


def _q(cfg: dict, label: str) -> str:
    return f'"{label}"' if cfg["post"].get("names") == "human" else label


def _highlight(name: str, cfg: dict) -> dict | None:
    for h in cfg.get("highlight", []):
        if re.search(h["pattern"], name):
            return h
    return None


_NUMERIC_DECL = re.compile(r"[fiu](8|16|32|64)|usize|isize|float|double|int\d*|int32")


# ---------------------------------------------------------------------------
# Facts
# ---------------------------------------------------------------------------
def facts(entry: dict, cfg: dict, known: set[str] | None = None) -> tuple[list[dict], Derived]:
    """Facts sorted by importance. Only trigger facts make an entry post-worthy. Raises Ambiguous."""
    pc = cfg["post"]
    d = Derived()
    if entry.get("class") not in ("code", "docs"):
        return [], d
    if entry.get("is_root"):
        raise Ambiguous("initial commit; needs a hand-written post")
    skip = pc.get("exclude_paths") or []
    ch = [c for c in entry.get("changes", []) if not C.match_any(c["file"], skip)]
    params = [c for c in ch if c["extractor"] not in CONST_LIKE]
    if sum(c["kind"] == "added" for c in params) > pc["bulk_added_limit"]:
        raise Ambiguous(f"bulk change ({sum(c['kind'] == 'added' for c in params)} parameters added); needs a hand-written post")
    known = known or set()
    ex = pc.get("names") != "human"  # raw mode quotes literals exactly (3.0 stays 3.0: ROS cares about int vs double)
    out: list[dict] = []

    by_name: dict[tuple[str, str], dict] = {}
    for c in params:
        if c["kind"] not in ("changed", "moved") or c["old"] == c["new"]:
            continue
        k = (c["extractor"], c["name"])
        if k in by_name:
            if (by_name[k]["old"], by_name[k]["new"]) != (c["old"], c["new"]):
                raise Ambiguous(f"{c['name']} changed differently in {by_name[k]['file']} and {c['file']}")
            continue
        by_name[k] = c
    for c in by_name.values():
        h = _highlight(c["name"], cfg)
        if c["type"] == "bool" or {c["old"], c["new"]} <= {"true", "false", "True", "False"}:
            if {str(c["old"]).lower(), str(c["new"]).lower()} != {"true", "false"}:
                raise Ambiguous(f"{c['name']}: unclear bool change")
            on = str(c["new"]).lower() == "true"
            label = display_name(c, cfg, r"^Enable(?=[A-Z])" if pc.get("names") == "human" else None)
            out.append({"kind": "flag", "trigger": True, "score": 80 + on, "text": f"{label}: switched {'ON' if on else 'OFF'} by default"})
            continue
        old, new = parse_num(c["old"]), parse_num(c["new"])
        if old is None or new is None:
            if _NUMERIC_DECL.fullmatch(str(c.get("decl_type") or "")) and (old is None) != (new is None):
                raise Ambiguous(f"{c['name']}: non-literal numeric default {c['old']} -> {c['new']}")
            if c["type"] in ("string", "expr") and len(str(c["old"])) + len(str(c["new"])) < 60:
                out.append({"kind": "string", "trigger": False, "score": 5, "text": f"{display_name(c, cfg)}: {c['old']} → {c['new']}"})
            continue
        pct = abs(new - old) / abs(old) * 100 if old else float("inf")
        trig = pct >= float(pc.get("min_change_pct") or 0)
        if h:
            penalty = h.get("penalty_if_negative") and (old < 0 or new < 0)
            noun = h.get("negative_noun", "penalty") if penalty else h.get("noun", "")
            label = display_name(c, cfg, h.get("strip"))
            label = f"{_q(cfg, label)} {noun}".rstrip() if noun else label
            out.append({"kind": "highlight", "trigger": trig, "score": (100 if trig else 6) + abs(new - old),
                        "text": f"{label} {fmt_num(c['old'], ex)} → {fmt_num(c['new'], ex)}{change_suffix(old, new, d, bool(penalty), True)}"})
            continue
        text = f"{display_name(c, cfg)} {fmt_num(c['old'], ex)} → {fmt_num(c['new'], ex)}"
        if pc.get("names") == "human":
            for unit, size in _DURATION_UNITS:
                if c["name"].endswith(unit):
                    a, b = _human_duration(old * size), _human_duration(new * size)
                    if a and b:
                        text = f"{humanize(c['name'][: -len(unit)])} {a} → {b}"
                        for tok in re.findall(r"\d+", a + " " + b):
                            d.add(tok)
                    break
        out.append({"kind": "param", "trigger": trig, "score": (60 if trig else 6) + _magnitude(old, new),
                    "text": text + change_suffix(old, new, d)})

    for c in params:
        if c["kind"] == "added" and c["name"] not in known:
            h = _highlight(c["name"], cfg)
            v = parse_num(c["new"])
            if c["type"] == "bool":
                on = str(c["new"]).lower() == "true"
                label = display_name(c, cfg, r"^Enable(?=[A-Z])" if pc.get("names") == "human" else None)
                out.append({"kind": "new_flag", "trigger": on, "score": 50 if on else 10, "text": f"new flag {_q(cfg, label)} ({'ON' if on else 'off'} by default)"})
            elif h and v is not None:
                label = display_name(c, cfg, h.get("strip"))
                noun = h.get("noun", "")
                if v != 0:
                    out.append({"kind": "highlight", "trigger": True, "score": 95, "text": f"new {_q(cfg, label)} {noun} = {fmt_num(c['new'], ex)}".replace("  ", " ")})
                else:
                    out.append({"kind": "new_param", "trigger": False, "score": 8, "text": f"new {_q(cfg, label)} {noun} added at 0 (no effect yet)".replace("  ", " ")})
            elif h and v is None and _NUMERIC_DECL.fullmatch(str(c.get("decl_type") or "")):
                raise Ambiguous(f"new {c['name']} has a non-literal default")
            elif v is not None and pc.get("new_values", True):
                out.append({"kind": "new_param", "trigger": False, "score": 9, "text": f"new {display_name(c, cfg)} = {fmt_num(c['new'], ex)}"})
        elif c["kind"] == "removed" and not c.get("still_defined_in") and not c.get("file_deleted"):
            h = _highlight(c["name"], cfg)
            v = parse_num(c["old"])
            if h and v not in (None, 0):
                noun = h.get("noun", "")
                out.append({"kind": "highlight", "trigger": True, "score": 90,
                            "text": f"{_q(cfg, display_name(c, cfg, h.get('strip')))} {noun} removed (was {fmt_num(c['old'], ex)})".replace("  ", " ")})

    for key, word in (("added", "new"), ("removed", "removed")):
        groups: dict[str, list[str]] = {}
        for comp in entry.get("components", {}).get(key, []):
            if comp["kind"] in pc["component_kinds"] and C.match_any(comp["path"], pc["component_paths"]) and not C.match_any(comp["path"], skip):
                groups.setdefault(comp["kind"], []).append(comp["name"])
        for kind, names in groups.items():
            plural = kind.lower() + ("s" if len(names) > 1 else "")
            more = ""
            if len(names) > 3:
                more = f" and {d.add(str(len(names) - 3))} more"
            prio = len(pc["component_kinds"]) - pc["component_kinds"].index(kind)
            out.append({"kind": "component", "trigger": True, "score": 70 + prio + (0.5 if key == "added" else 0),
                        "text": f"{word} {plural}: {', '.join(names[:3])}{more}"})

    trig_paths = pc.get("const_trigger_paths") or ["**"]
    trig_names = pc.get("const_trigger_names") or ".*"
    for c in ch:
        if c["extractor"] not in CONST_LIKE or c["kind"] != "changed":
            continue
        where = _module_hint(c["file"], trig_paths)
        trig = C.match_any(c["file"], trig_paths) or bool(re.search(trig_names, c["name"]))
        if {str(c["old"]).lower(), str(c["new"]).lower()} == {"true", "false"}:
            out.append({"kind": "constant", "trigger": trig, "score": 75 if trig else 4,
                        "text": f"{display_name(c, cfg)}{where}: switched {'ON' if str(c['new']).lower() == 'true' else 'OFF'}"})
            continue
        old, new = parse_num(c["old"]), parse_num(c["new"])
        if old is None or new is None or old == 0 or new == 0 or (old < 0) != (new < 0):
            continue
        if max(abs(new) / abs(old), abs(old) / abs(new)) < 1 + float(pc.get("const_min_change_pct") or 0) / 100:
            continue
        out.append({"kind": "constant", "trigger": trig, "score": (40 if trig else 4) + _magnitude(old, new),
                    "text": f"{display_name(c, cfg)}{where} {fmt_num(c['old'], ex)} → {fmt_num(c['new'], ex)}{change_suffix(old, new, d)}"})
    out.sort(key=lambda f: -f["score"])
    seen: set[str] = set()
    out = [f for f in out if not (f["text"] in seen or seen.add(f["text"]))]  # mirrored files -> one fact
    return out, d


def _module_hint(path: str, trig_paths: list[str]) -> str:
    parts = path.split("/")
    if C.match_any(path, trig_paths) or len(parts) < 2 or trig_paths == ["**"]:
        return ""
    if parts[0] == "grox" and len(parts) > 3 and parts[1] == "flows":
        return f" (grox {parts[2].replace('_', ' ')})"
    return f" ({parts[0]})"


def allowed_numbers(entry: dict) -> set[float]:
    vals: set[float] = set()

    def add(v):
        if v is None:
            return
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            vals.add(abs(float(v)))
            return
        for tok in re.findall(r"\d[\d_,']*\.?\d*(?:[eE][-+]?\d+)?", str(v)):
            x = parse_num(tok.replace(",", "").rstrip("."))
            if x is not None:
                vals.add(abs(x))

    for c in entry.get("changes", []):
        add(c.get("old")); add(c.get("new"))
    for v in entry.get("stats", {}).values():
        add(v)
    return vals


def verified_note_title(entry: dict) -> str | None:
    s = entry.get("summary") or {}
    if s.get("source") != "note" or not s.get("title"):
        return None
    title = s["title"].replace("`", "").replace("\u2212", "-").strip()
    if "#" in title or "@" in title or bare_domains(title):
        return None
    allowed = allowed_numbers(entry)
    for tok in re.findall(NUM_TOKEN, title):
        x = parse_num(tok.replace(",", "").rstrip("."))
        if x is None or abs(x) not in allowed:
            return None
    return title


def short_date(iso: str) -> str:
    dt = datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone(timezone.utc)
    return f"{dt:%b} {dt.day}"


def compose(entry: dict, cfg: dict, known: set[str] | None = None) -> dict:
    """{"posts": [...], "facts": [...], "headline_source": ...} or {"posts": [], "reason": ...}; raises Ambiguous."""
    pc = cfg["post"]
    fs, derived = facts(entry, cfg, known)
    triggers = [f for f in fs if f["trigger"]]
    if not triggers:
        return {"posts": [], "facts": fs, "reason": "no meaningful change"}
    lead = triggers[0]
    shown = [lead] + [f for f in fs if f is not lead][: pc["max_facts"] - 1]
    title = verified_note_title(entry) if lead["kind"] in ("component", "constant") else None
    header = pc["header"].format(name=cfg.get("name") or "repo", date=short_date(entry["date"]))
    head_text, bullets = (header + title, shown) if title else (header + lead["text"], shown[1:])
    site_url = (cfg["site"].get("url") or "").rstrip("/")
    link_lines = []
    if site_url:
        link_lines.append(f"Details: {site_url}/entries/{entry['short']}.html")
    if entry.get("url"):
        link_lines.append(f"Commit: {entry['url']}")
    links = "\n".join(link_lines)
    disclaimer = pc.get("disclaimer") or ""

    def build(bul: list[dict]) -> list[str] | None:
        shown_ids = {id(x) for x in bul} | (set() if title else {id(lead)})
        more = sum(1 for f in fs if f["trigger"] and id(f) not in shown_ids)
        tail_lines = [f"+{derived.add(str(more))} more on the details page." if site_url else f"+{derived.add(str(more))} more in the commit."] if more > 0 else []
        tail = "\n".join(x for x in tail_lines + [links, disclaimer] if x)
        lines = [f"- {b['text']}" for b in bul]
        single = head_text + ("\n" + "\n".join(lines) if lines else "") + ("\n\n" + tail if tail else "")
        if weighted_len(single) <= MAX_CHARS:
            return [single]
        posts, cur = [], head_text
        for ln in lines:
            cand = cur + "\n" + ln
            if weighted_len(cand) <= MAX_CHARS:
                cur = cand
            else:
                posts.append(cur)
                cur = ln
        last = cur + "\n\n" + tail
        if weighted_len(last) <= MAX_CHARS:
            posts.append(last)
        else:
            posts += [cur, tail]
        if len(posts) > pc["max_posts"] or any(weighted_len(p) > MAX_CHARS for p in posts):
            return None
        return posts

    def best(bul: list[dict]) -> list[str] | None:
        for k in range(len(bul), -1, -1):
            got = build(bul[:k])
            if got:
                return got
        return None

    a, b = best([x for x in bullets if x["trigger"]]), best(bullets)
    posts = b if (b and (not a or len(b) <= len(a))) else a
    if not posts:
        raise Ambiguous("could not fit the lead change into the thread limit")
    check_posts(posts, entry, derived, header, pc)
    return {"posts": posts, "facts": fs, "headline_source": "note" if title else "analysis"}


def check_posts(posts: list[str], entry: dict, derived: Derived, header: str, pc: dict) -> None:
    allowed = allowed_numbers(entry) | derived.values
    for i, p in enumerate(posts):
        n = weighted_len(p)
        if n > MAX_CHARS:
            raise Ambiguous(f"post is {n} weighted chars")
        if re.search(r"(^|\s)[@#＠＃]\w", p):
            raise Ambiguous("post contains a mention or hashtag")
        body = p[len(header):] if i == 0 and p.startswith(header) else p
        body = re.sub(r"https?://\S+", " ", body)
        if pc.get("disclaimer"):
            body = body.replace(pc["disclaimer"], " ")
        if bare_domains(body):
            raise Ambiguous(f"post contains a bare domain-like token: {bare_domains(body)}")
        for tok in re.findall(NUM_TOKEN, body):
            x = parse_num(tok.replace(",", "").rstrip("."))
            if x is None or abs(x) not in allowed:
                raise Ambiguous(f"unverified number {tok!r} in post")
    if len(posts) > pc["max_posts"]:
        raise Ambiguous("thread too long")
    if pc.get("disclaimer") and not any(pc["disclaimer"] in p for p in posts):
        raise Ambiguous("disclaimer missing")


def known_names(entries_oldest_first: list[dict], upto_index: int) -> set[str]:
    known: set[str] = set()
    for e in entries_oldest_first[:upto_index]:
        for c in e.get("changes", []):
            if c["extractor"] in CONST_LIKE:
                continue
            if c["kind"] in ("added", "moved"):
                known.add(c["name"])
            elif c["kind"] == "removed" and not c.get("still_defined_in"):
                known.discard(c["name"])
    return known


def post_text_for(entries_oldest_first: list[dict], cfg: dict) -> list[dict]:
    out = []
    for i, e in enumerate(entries_oldest_first):
        rec = {"sha": e["sha"], "short": e["short"], "date": e["date"], "commit_url": e.get("url")}
        try:
            r = compose(e, cfg, known_names(entries_oldest_first, i))
            if r["posts"]:
                rec.update(status="ready", posts=r["posts"], weighted_lengths=[weighted_len(p) for p in r["posts"]],
                           headline_source=r["headline_source"])
            else:
                rec.update(status="skip", reason=r["reason"])
        except Ambiguous as exc:
            rec.update(status="skip", reason=f"ambiguous: {exc}")
        out.append(rec)
    return out
