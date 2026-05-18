"""Plugin discovery & loader.

User adds a plugins.yaml listing .py files to import at startup. Each .py file
typically calls ModelRegistry.register(...) / SubAgentRegistry.register(...) etc.

Format:
    # config/plugins.yaml
    load_at_startup:
      - G:/my_private_code/my_models.py
      - G:/my_private_code/my_collectors.py

Loading is done by exec'ing each file's source under a __plugin__ namespace,
which lets the user simply write top-level register() calls.
"""
from __future__ import annotations
import importlib.util
import logging
from pathlib import Path
from typing import List, Optional
import yaml

log = logging.getLogger(__name__)

DEFAULT_CONFIG_PATH = Path("config/plugins.yaml")


def load_plugins(config_path: Optional[Path] = None) -> List[str]:
    """Load all plugin .py files listed in config/plugins.yaml.

    Returns a list of file paths that were loaded successfully.
    Failures are logged but do NOT raise (a broken user plugin should not
    prevent the rest of the CLI from working).
    """
    cfg_path = config_path or DEFAULT_CONFIG_PATH
    if not cfg_path.exists():
        return []
    try:
        cfg = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
    except Exception as exc:
        log.warning("plugins.yaml parse failed: %s", exc)
        return []

    files: List[str] = cfg.get("load_at_startup", []) or []
    loaded: List[str] = []
    for f in files:
        path = Path(f).expanduser()
        if not path.exists():
            log.warning("plugin file not found: %s", path)
            continue
        try:
            spec = importlib.util.spec_from_file_location(
                f"_fa_plugin_{path.stem}", str(path)
            )
            if spec is None or spec.loader is None:
                continue
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            loaded.append(str(path))
        except Exception as exc:
            log.warning("plugin load failed for %s: %s", path, exc)
    return loaded
