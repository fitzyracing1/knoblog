# knoblog

knoblog (v0.1.1) walks a git history and pulls parameter, gain, threshold, flag and config-default values out of each commit. It reports exactly what changed (for example `max_vel_x 0.26 → 0.5`) with the commit link, file and percent change. It writes a static site, an Atom feed, a JSON feed of structured changes, and X-ready post text. It never posts anything.

- Python ≥ 3.11, no runtime dependencies. Optional extras: `yaml` (PyYAML), `test` (pytest).
- Console script: `knoblog` (`knoblog.cli:main`). Also available as a composite GitHub Action (`action.yml`).
- Values are quoted verbatim from diffs. The LLM summary is optional, and a post-text gate rejects numbers that do not come from the analysed values.

Pages: [Getting started](getting-started.md) · [Usage](usage.md) · [Architecture](architecture.md) · [Contributing](contributing.md)

CI (`ci.yml`) runs `python -m unittest discover -s tests -v`. A run is **lit** when it succeeds and **dark** otherwise.

MIT License. Copyright (c) 2026 Joshua Almeida
