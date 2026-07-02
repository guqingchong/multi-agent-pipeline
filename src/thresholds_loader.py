"""src/thresholds_loader.py — Centralized threshold configuration loader.

Loads ``src/thresholds.yaml`` once and provides cached, dotted-path access
to threshold values.  Used by ``evaluate.py``, ``gate.py`` and ``inspector.py``.

If ``thresholds.yaml`` is missing, malformed, or PyYAML is unavailable, all
``get_threshold`` calls fall back to their caller-supplied defaults so the
pipeline remains operational.
"""

from __future__ import annotations

import functools
import logging
from pathlib import Path
from typing import Any, Dict

logger = logging.getLogger(__name__)


@functools.lru_cache(maxsize=1)
def load_thresholds() -> Dict[str, Any]:
    """Load ``thresholds.yaml`` from the ``src/`` directory.

    Returns an empty dict if the file cannot be loaded for any reason.
    The result is cached for the lifetime of the process.
    """
    try:
        import yaml
    except ImportError:
        logger.warning("thresholds_loader: PyYAML not installed; using code defaults")
        return {}

    # Resolve relative to this module so imports work as package or script.
    candidates = [
        Path(__file__).resolve().parent / "thresholds.yaml",
        Path("src/thresholds.yaml"),
        Path("thresholds.yaml"),
    ]

    for path in candidates:
        if path.exists():
            try:
                with path.open("r", encoding="utf-8") as f:
                    data = yaml.safe_load(f)
                    if isinstance(data, dict):
                        logger.debug("thresholds_loader: loaded %s", path)
                        return data
                    logger.warning("thresholds_loader: %s did not contain a mapping", path)
            except Exception as exc:  # pragma: no cover - defensive fallback
                logger.warning("thresholds_loader: failed to load %s: %s", path, exc)

    logger.warning("thresholds_loader: thresholds.yaml not found; using code defaults")
    return {}


def get_threshold(path: str, default: Any = None) -> Any:
    """Return a threshold value by dotted path.

    Example::

        honesty_min = get_threshold("evaluate.red_lines.honesty_min", 5.0)

    Args:
        path: Dot-separated key path, e.g. ``"gate.timeouts.push"``.
        default: Value to return if the path is missing or thresholds failed to load.

    Returns:
        The configured threshold value, or ``default`` if not found.
    """
    data = load_thresholds()
    keys = path.split(".")
    for key in keys:
        if isinstance(data, dict) and key in data:
            data = data[key]
        else:
            return default
    return data
