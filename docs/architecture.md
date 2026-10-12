# Architecture

| File | Role |
|---|---|
| `knoblog/cli.py` | commands `run`, `init`, `extract`, `presets` |
| `knoblog/config.py`, `yamlmini.py`, `presets/` | config loading, built-in minimal YAML parser, presets |
| `knoblog/gitio.py` | cloning (blob-less) and reading history |
| `knoblog/extractors.py` | per-format value extractors (registry) |
| `knoblog/analyze.py` | value diffs per commit, percent change |
| `knoblog/summarize.py` | optional LLM summary |
| `knoblog/posttext.py` | X-ready post text and number gate |
| `knoblog/render.py`, `static/style.css` | static site, Atom feed, JSON feed |
| `scripts/parity_x_algorithm.py` | parity check against the x-algorithm-changelog bot |
| `tests/` | unittest suites |

Workflows: `ci.yml`, `action-smoke.yml`, `demo.yml` (weekly demos), `publish-pypi.yml`.
