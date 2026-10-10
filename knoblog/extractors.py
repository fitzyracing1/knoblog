"""Parameter extractors: file text -> {name: {"value": raw_text, "type": inferred, ...}}.

Every extractor is deterministic and keeps the value exactly as written in the source, so
change notes can quote it verbatim. Names defined more than once in one file are dropped
(pairing old/new values would be a guess).
"""
from __future__ import annotations

import json
import os
import re
from collections import Counter

from . import yamlmini

try:  # Python 3.11+
    import tomllib  # type: ignore
except ModuleNotFoundError:  # pragma: no cover
    tomllib = None

# ---------------------------------------------------------------------------
# Values
# ---------------------------------------------------------------------------
NUM = r"[-+]?(?:\d[\d_']*\.?\d*(?:[eE][-+]?\d+)?|\.\d+(?:[eE][-+]?\d+)?)"
_SUFFIX = r"(?:_?(?:f32|f64|u8|u16|u32|u64|usize|i8|i16|i32|i64|isize)|[fFlLuU]{1,3})"
_NUM_FULL = re.compile(rf"\(\s*{NUM}{_SUFFIX}?\s*\)|{NUM}{_SUFFIX}?")
_HEX = re.compile(r"0[xX][0-9a-fA-F_]+[uUlL]{0,3}")


def parse_num(v) -> float | None:
    """'0.4', '-43.2', '125_000', '1.0f64', '0.95f', '(10)', '1e-3', 0x10 -> float; anything else -> None."""
    if v is None or isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip().replace("\u2212", "-")
    if _HEX.fullmatch(s):
        return float(int(re.sub(r"[uUlL_]", "", s), 16))
    if not _NUM_FULL.fullmatch(s):
        return None
    s = s.strip("() ")
    s = re.sub(r"(_?(f32|f64|u8|u16|u32|u64|usize|i8|i16|i32|i64|isize)|[fFlLuU]+)$", "", s).replace("_", "").replace("'", "")
    try:
        x = float(s)
    except ValueError:
        return None
    return x if x == x and abs(x) != float("inf") else None


BOOLS = {"true": True, "false": False, "True": True, "False": False, "TRUE": True, "FALSE": False}


def infer_type(raw: str) -> str:
    s = str(raw).strip()
    if s in BOOLS:
        return "bool"
    if parse_num(s) is not None:
        return "number"
    if len(s) >= 2 and s[0] == s[-1] and s[0] in "'\"":
        return "string"
    if s.startswith("[") or s.startswith("vec!["):
        return "list"
    return "expr"


def _rec(value: str, **meta) -> dict:
    value = re.sub(r"\s+", " ", str(value)).strip()
    return {"value": value, "type": meta.pop("type", None) or infer_type(value), **meta}


def _dedupe(pairs: list[tuple[str, dict]]) -> dict[str, dict]:
    counts = Counter(n for n, _ in pairs)
    return {n: r for n, r in pairs if counts[n] == 1}


# ---------------------------------------------------------------------------
# Rust: param!(Name, Type, "key", default) macros and const/static items
# ---------------------------------------------------------------------------
def _split_top_level(s: str) -> list[str]:
    parts, depth, cur, in_str, i = [], 0, [], False, 0
    while i < len(s):
        ch = s[i]
        if in_str:
            cur.append(ch)
            if ch == "\\" and i + 1 < len(s):
                cur.append(s[i + 1]); i += 2; continue
            if ch == '"':
                in_str = False
        elif ch == '"':
            in_str = True; cur.append(ch)
        elif ch in "([{<":
            depth += 1; cur.append(ch)
        elif ch in ")]}>":
            depth -= 1; cur.append(ch)
        elif ch == "," and depth == 0:
            parts.append("".join(cur).strip()); cur = []
        else:
            cur.append(ch)
        i += 1
    if "".join(cur).strip():
        parts.append("".join(cur).strip())
    return parts


def rust_param(path: str, text: str, macro: str = "param") -> dict[str, dict]:
    out = []
    for m in re.finditer(rf"\b{re.escape(macro)}!\s*\(", text):
        start = m.end()
        depth, i, in_str = 1, start, False
        while i < len(text) and depth:
            ch = text[i]
            if in_str:
                if ch == "\\":
                    i += 1
                elif ch == '"':
                    in_str = False
            elif ch == '"':
                in_str = True
            elif ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
            i += 1
        body = re.sub(r"//[^\n]*", "", text[start:i - 1])
        parts = _split_top_level(body)
        if len(parts) >= 4:
            typ = parts[1]
            default = ",".join(parts[3:])
            t = "bool" if typ == "bool" else ("number" if re.fullmatch(r"[fiu](8|16|32|64)|usize|isize", typ) and parse_num(default) is not None else None)
            out.append((parts[0], _rec(default, type=t, decl_type=typ, key=parts[2].strip('"'))))
    return dict(out)  # param! names are unique per file by construction; later wins


LITERALISH = re.compile(r"""[-+*/()\s\d_.eE]+|true|false|True|False|None|"[^"]*"|'[^']*'|vec!\[.*\]|\[.*\]|[\d_.]+\s*as\s*\w+|Duration::\w+\([\d_.]+\)""")
_RUST_CONST = re.compile(r"^\s*(?:pub(?:\([^)]*\))?\s+)?(?:const|static)\s+([A-Z][A-Z0-9_]*)\s*:\s*[^=]+=\s*([^;]+);")


def rust_const(path: str, text: str, literal_only: bool = True) -> dict[str, dict]:
    pairs = []
    for line in text.splitlines():
        m = _RUST_CONST.match(line)
        if m and (not literal_only or LITERALISH.fullmatch(m.group(2).strip())):
            pairs.append((m.group(1), _rec(m.group(2))))
        elif m:
            pairs.append((m.group(1), _rec(m.group(2), type="expr")))
    return _dedupe(pairs)


# ---------------------------------------------------------------------------
# Python: module-level UPPER_CASE constants and typed class/dataclass fields
# ---------------------------------------------------------------------------
_PY_LIT = rf"(?:{NUM}(?:\s*[-+*/]\s*{NUM})*|True|False|None|\"[^\"]*\"|'[^']*')"
_PY_CONST = re.compile(rf"^(\s*)([A-Za-z_][A-Za-z0-9_]*)\s*(:\s*[A-Za-z_][\w\[\], .|]*)?\s*=\s*({_PY_LIT})\s*(?:#.*)?$")


def python_const(path: str, text: str) -> dict[str, dict]:
    pairs = []
    for line in text.splitlines():
        m = _PY_CONST.match(line)
        if not m:
            continue
        indent, name, annot, val = m.groups()
        if indent and not annot and not name.isupper():
            continue  # local variable inside a function
        if not indent and not annot and not name.isupper():
            continue
        pairs.append((name, _rec(val)))
    return _dedupe(pairs)


# ---------------------------------------------------------------------------
# JVM: Scala `val NAME = literal`, Java `static final T NAME = literal;`
# ---------------------------------------------------------------------------
_SCALA_CONST = re.compile(rf"^\s*(?:(?:private|protected|override|final|lazy)\s+)*(?:val|var)\s+([A-Za-z_]\w*)\s*(?::\s*[\w\[\]]+)?\s*=\s*({_PY_LIT}|true|false)\s*$")
_JAVA_CONST = re.compile(rf"^\s*(?:(?:public|private|protected|static|final)\s+)+[\w<>\[\]]+\s+([A-Z][A-Z0-9_]*)\s*=\s*({_PY_LIT}|true|false)\s*;")


def jvm_const(path: str, text: str) -> dict[str, dict]:
    rx = _SCALA_CONST if path.endswith(".scala") else _JAVA_CONST
    return _dedupe([(m.group(1), _rec(m.group(2))) for line in text.splitlines() if (m := rx.match(line))])


# ---------------------------------------------------------------------------
# C / C++: #define NAME literal, constexpr / static const / const T NAME = literal;
# ---------------------------------------------------------------------------
_C_LIT = rf"(?:\(?\s*{NUM}[fFlLuU]{{0,3}}\s*\)?|0[xX][0-9a-fA-F]+[uUlL]{{0,3}}|true|false|\"[^\"]*\")"
_C_DEFINE = re.compile(rf"^\s*#\s*define\s+([A-Za-z_]\w*)\s+({_C_LIT})\s*(?://.*|/\*.*)?$")
_C_CONST = re.compile(rf"^\s*(?:(?:static|inline|constexpr|const|volatile|extern)\s+)+(?:[\w:<>]+\s+)+?([A-Za-z_]\w*)\s*(?:=\s*|\{{\s*)({_C_LIT})\s*\}}?\s*;")


def c_const(path: str, text: str) -> dict[str, dict]:
    text = re.sub(r"(?s)/\*.*?\*/", lambda m: "\n" * m.group(0).count("\n"), text)
    pairs = []
    for line in text.splitlines():
        m = _C_DEFINE.match(line) or _C_CONST.match(line)
        if m:
            pairs.append((m.group(1), _rec(m.group(2))))
    return _dedupe(pairs)


# ---------------------------------------------------------------------------
# PX4: PARAM_DEFINE_FLOAT(NAME, 0.95f); and module.yaml parameter definitions
# ---------------------------------------------------------------------------
_PX4_DEFINE = re.compile(r"PARAM_DEFINE_(FLOAT|INT32)\s*\(\s*([A-Z0-9_]+)\s*,\s*([^)]+?)\s*\)\s*;")


def px4_param_define(path: str, text: str) -> dict[str, dict]:
    return _dedupe([(m.group(2), _rec(m.group(3), decl_type=m.group(1).lower())) for m in _PX4_DEFINE.finditer(text)])


def px4_module_yaml(path: str, text: str) -> dict[str, dict]:
    doc = yamlmini.loads(text)
    pairs = []
    groups = doc.get("parameters") if isinstance(doc, dict) else None
    for g in groups if isinstance(groups, list) else []:
        defs = g.get("definitions") if isinstance(g, dict) else None
        for name, d in (defs or {}).items() if isinstance(defs, dict) else []:
            if isinstance(d, dict) and "default" in d and not isinstance(d["default"], (dict, list)):
                pairs.append((name, _rec(d["default"], decl_type=yamlmini.unquote(d.get("type") or ""), group=g.get("group"))))
    return _dedupe(pairs)


# ---------------------------------------------------------------------------
# ArduPilot: AP_GROUPINFO("NAME", idx, Class, member, default) and GSCALAR/ASCALAR(var, "NAME", default)
# ---------------------------------------------------------------------------
_AP_GROUPINFO = re.compile(r"AP_GROUPINFO(?:_FLAGS(?:_FRAME)?)?\s*\(\s*\"([A-Z0-9_]+)\"\s*,\s*\d+\s*,\s*(\w+)\s*,\s*[\w.\[\]]+\s*,\s*([^,()]+(?:\([^()]*\))?[^,()]*?)\s*(?:,[^)]*)?\)")
_AP_SCALAR = re.compile(r"\b[GA]SCALAR\s*\(\s*\w+\s*,\s*\"([A-Z0-9_]+)\"\s*,\s*([^)]+?)\s*\)")


def _file_defines(text: str) -> dict[str, str]:
    """#define NAME <literal> in this file; macros defined more than once (#if branches) are left unresolved."""
    seen: Counter = Counter()
    vals: dict[str, str] = {}
    for line in re.sub(r"(?s)/\*.*?\*/", "", text).splitlines():
        m = _C_DEFINE.match(line)
        if m:
            seen[m.group(1)] += 1
            vals[m.group(1)] = m.group(2)
        elif (m2 := re.match(r"^\s*#\s*define\s+([A-Za-z_]\w*)\b", line)):
            seen[m2.group(1)] += 1
    return {k: v for k, v in vals.items() if seen[k] == 1}


def ardupilot(path: str, text: str, resolve_macros: bool = True) -> dict[str, dict]:
    """Defaults that name a macro #defined to a literal in the same file are resolved (value) with the macro kept (macro)."""
    defines = _file_defines(text) if resolve_macros else {}

    def rec(raw: str, **meta) -> dict:
        raw = raw.strip()
        if raw in defines:
            return _rec(defines[raw], macro=raw, **meta)
        return _rec(raw, **meta)

    pairs = []
    for m in _AP_GROUPINFO.finditer(text):
        pairs.append((f"{m.group(2)}.{m.group(1)}", rec(m.group(3), group=m.group(2))))
    for m in _AP_SCALAR.finditer(text):
        pairs.append((m.group(1), rec(m.group(2))))
    return _dedupe(pairs)


# ---------------------------------------------------------------------------
# Structured config: YAML (incl. ROS 2 params files), JSON, TOML -> dotted keys
# ---------------------------------------------------------------------------
def _flatten(node, prefix: str, out: list, drop: set[str], raw: bool) -> None:
    if isinstance(node, dict):
        for k, v in node.items():
            k = str(k)
            key = prefix if k in drop else (f"{prefix}.{k}" if prefix else k)
            _flatten(v, key, out, drop, raw)
    elif isinstance(node, list):
        if all(not isinstance(x, (dict, list)) for x in node):
            vals = [x if raw else _py_lit(x) for x in node]
            out.append((prefix, _rec("[" + ", ".join(str(v) for v in vals) + "]", type="list")))
        else:
            for i, v in enumerate(node):
                _flatten(v, f"{prefix}[{i}]", out, drop, raw)
    elif prefix:
        out.append((prefix, _rec("null" if node is None else (node if raw else _py_lit(node)))))


def _py_lit(v) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, str):
        return json.dumps(v)
    if v is None:
        return "null"
    return str(v)


def yaml_keys(path: str, text: str) -> dict[str, dict]:
    return _dedupe(_flat(yamlmini.loads(text), set(), raw=True))


def ros2_yaml(path: str, text: str) -> dict[str, dict]:
    """ROS 2 parameter files: node -> ros__parameters -> params. The ros__parameters level is dropped."""
    return _dedupe(_flat(yamlmini.loads(text), {"ros__parameters"}, raw=True))


def json_keys(path: str, text: str) -> dict[str, dict]:
    return _dedupe(_flat(json.loads(text), set(), raw=False))


def toml_keys(path: str, text: str) -> dict[str, dict]:
    if tomllib is None:
        return {}
    return _dedupe(_flat(tomllib.loads(text), set(), raw=False))


def _flat(doc, drop: set[str], raw: bool) -> list:
    out: list = []
    _flatten(doc, "", out, drop, raw)
    return out


# ---------------------------------------------------------------------------
# Registry + auto-detection
# ---------------------------------------------------------------------------
REGISTRY = {
    "rust_param": rust_param,
    "rust_const": rust_const,
    "python_const": python_const,
    "c_const": c_const,
    "jvm_const": jvm_const,
    "px4_param_define": px4_param_define,
    "px4_module_yaml": px4_module_yaml,
    "ardupilot": ardupilot,
    "yaml": yaml_keys,
    "ros2_yaml": ros2_yaml,
    "json": json_keys,
    "toml": toml_keys,
}

# Extractors whose names are hierarchical config keys (rendered with "/" in posts so X doesn't auto-link "a.b").
KEYED = {"yaml", "ros2_yaml", "json", "toml", "ardupilot"}


def auto_extractors(path: str, text: str) -> list[str]:
    """Pick extractors from file extension + content."""
    ext = os.path.splitext(path)[1].lower()
    if ext == ".rs":
        return (["rust_param"] if "param!(" in text else []) + ["rust_const"]
    if ext == ".py":
        return ["python_const"]
    if ext in (".scala", ".java"):
        return ["jvm_const"]
    if ext in (".h", ".hpp", ".hh", ".c", ".cc", ".cpp", ".cxx"):
        out = ["c_const"]
        if "PARAM_DEFINE_" in text:
            out.append("px4_param_define")
        if "AP_GROUPINFO" in text or "GSCALAR(" in text or "ASCALAR(" in text:
            out.append("ardupilot")
        return out
    if ext in (".yaml", ".yml"):
        if "ros__parameters" in text:
            return ["ros2_yaml"]
        if re.search(r"(?m)^parameters:", text) and "definitions:" in text:
            return ["px4_module_yaml"]
        return ["yaml"]
    if ext == ".json":
        return ["json"]
    if ext == ".toml":
        return ["toml"]
    return []


def run_extractor(name: str, path: str, text: str, options: dict | None = None) -> dict[str, dict]:
    fn = REGISTRY[name]
    try:
        return fn(path, text, **(options or {}))
    except (yamlmini.YamlError, ValueError, json.JSONDecodeError, RecursionError):
        return {}
    except Exception as exc:  # tomllib.TOMLDecodeError etc.
        if type(exc).__name__.endswith("DecodeError"):
            return {}
        raise
