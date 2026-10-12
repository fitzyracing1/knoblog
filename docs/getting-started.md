# Getting started

```bash
pip install knoblog          # Python 3.11+
knoblog init --preset ros2   # writes knoblog.yml
# edit watch.paths in knoblog.yml, then:
knoblog run --repo https://github.com/ros-navigation/navigation2 --config knoblog.yml --out site
python -m http.server -d site
```

Built-in demos from a clone of this repo:

```bash
knoblog run --config examples/nav2.yml --out site-nav2
knoblog run --config examples/px4.yml  --out site-px4
knoblog extract path/to/params.yaml
```

Remote repos are cloned blob-less into `.knoblog-cache/`.
