"""User-tunable retrieval settings, stored as JSON at ~/.archived/config.json.

Only a small, safe set of knobs is exposed. Missing or unknown keys fall
back to the defaults below, so a partial or absent file is always fine.
The path can be overridden with ARCHIVED_CONFIG (handy for tests).
"""

import json
import os

DEFAULT_PATH = os.path.expanduser("~/.archived/config.json")

# key -> (default value, one-line help). The default's type also decides
# how a string from the CLI is coerced (float vs int).
SETTINGS = {
    "min_cosine": (0.6, "semantic relatedness bar for search (0-1)"),
    "dedup_min_cosine": (0.8, "stricter bar for skipping duplicates (0-1)"),
    "search_limit": (5, "default number of hits a search returns (>=1)"),
}
DEFAULTS = {k: v for k, (v, _) in SETTINGS.items()}

_cache = {}


def _path():
    """The config file to read/write (respects ARCHIVED_CONFIG)."""
    return os.environ.get("ARCHIVED_CONFIG", DEFAULT_PATH)


def _load():
    """Load settings merged over defaults, cached per path."""
    path = _path()
    if path not in _cache:
        try:
            with open(path) as f:
                _cache[path] = {**DEFAULTS, **json.load(f)}
        except Exception:
            _cache[path] = dict(DEFAULTS)
    return _cache[path]


def get(key):
    """Read one setting (its default if unset or the file is missing)."""
    return _load().get(key, DEFAULTS.get(key))


def all_values():
    """Every setting with its current value, defaults filled in."""
    return dict(_load())


def _coerce(key, value):
    """Turn a CLI string into the setting's type and validate its range."""
    value = type(DEFAULTS[key])(value)
    if key.endswith("cosine") and not 0.0 <= value <= 1.0:
        raise ValueError("cosine must be between 0 and 1")
    if key == "search_limit" and value < 1:
        raise ValueError("search_limit must be >= 1")
    return value


def set_value(key, value):
    """Persist one known setting; returns the coerced value it stored."""
    if key not in SETTINGS:
        raise KeyError(key)
    coerced = _coerce(key, value)
    data = all_values()
    data[key] = coerced
    path = _path()
    if path != ":memory:":
        os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump(data, f, indent=2)
    _cache.pop(path, None)
    return coerced
