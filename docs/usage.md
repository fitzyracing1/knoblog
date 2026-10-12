# Usage

## `knoblog run`
Analyses the history and writes the site and feeds.

| Option | Meaning |
|---|---|
| `--repo` | git URL or local path (overrides `repo:` in config) |
| `--config, -c` | knoblog.yml or a preset name |
| `--preset` | `ros2`, `px4`, `ardupilot`, `x-algorithm`, `generic` |
| `--out, -o` | output dir (default `site`) |
| `--branch`, `--max-commits`, `--since YYYY-MM-DD` | history window |
| `--site-url` | public URL used in feeds and post links |
| `--cache-dir` | clone location (default `.knoblog-cache`) |
| `--no-fetch` | use the existing clone as-is |
| `--no-llm` | never call an LLM |
| `--github-output` | write counts to `$GITHUB_OUTPUT` |
| `-v` | verbose |

## Other commands
- `knoblog init [--preset generic] [--out knoblog.yml] [--force]`
- `knoblog extract FILE [--extractor NAME]`
- `knoblog presets`

## Optional LLM summary (`knoblog/summarize.py`)
The summary is used only when a key is set. With `XAI_API_KEY` set, the default base is `https://api.x.ai/v1` and the model is `grok-4`. Otherwise, with `OPENAI_API_KEY` set, the default base is `https://api.openai.com/v1` and the model is `gpt-4o-mini`. Override with `KNOBLOG_LLM_BASE_URL` and `KNOBLOG_LLM_MODEL`.

## GitHub Action
Inputs: `repo`, `config`, `out`, `site-url`, `max-commits`, `python-version`, `extra-args`. Outputs: `entries`, `changes`. For an example workflow, see `examples/workflows/knoblog-pages.yml`.
