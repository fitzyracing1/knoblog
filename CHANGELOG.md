# Changelog

## Unreleased

- "Why it changed": each entry, `changes.json` record and `posts.json` record now carries the commit message plus the
  PR number and URL. PRs are parsed from the message offline; `--pr-lookup` adds the GitHub API. Entry pages show
  a "Why it changed" section.

## 0.1.1 (2026-10-09)

- Packaging: removed the invalid trove classifier `Topic :: Scientific/Engineering :: Robotics` (PyPI rejected the
  upload) and used `Topic :: Scientific/Engineering` instead. All classifiers are now validated against the
  official `trove-classifiers` list.
- First release published to PyPI: `pip install knoblog`.
- No functional changes since 0.1.0.

## 0.1.0 (2026-10-09)

- First release: plain-English changelog site, Atom/JSON feeds and X-ready post text for parameter and config
  changes (presets: generic, ros2, px4, ardupilot, x-algorithm), plus a GitHub Action.
