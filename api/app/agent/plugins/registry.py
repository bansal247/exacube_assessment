"""Plugin discovery: drop a new module into app/agent/plugins/, decorate its
Plugin subclass with @register_plugin, and discover_plugins() picks it up by
scanning the directory -- no router edit, no prompt edit, no core-file edit.

Chosen over entry_points (setup.py/pyproject metadata, more ceremony for a
single-package project) and over a manually-maintained import list in
__init__.py (exactly the kind of "edit a file to add a plugin" the brief
says fails the test). A directory scan + decorator is the smallest thing
that satisfies "drop a file in and it's picked up."
"""

import importlib
import pkgutil

from app.agent.plugins.base import Plugin

_registry: dict[str, Plugin] = {}


def register_plugin(cls: type[Plugin]) -> type[Plugin]:
    instance = cls()
    if instance.name in _registry:
        raise ValueError(f"duplicate plugin name: '{instance.name}'")
    _registry[instance.name] = instance
    return cls


def discover_plugins() -> None:
    """Import every module in this package except itself/base -- their
    top-level @register_plugin decorators do the rest. Safe to call more
    than once (re-importing an already-imported module is a no-op).
    """
    import app.agent.plugins as plugins_pkg

    for module_info in pkgutil.iter_modules(plugins_pkg.__path__):
        if module_info.name in ("base", "registry"):
            continue
        importlib.import_module(f"app.agent.plugins.{module_info.name}")


def get_plugin(name: str) -> Plugin | None:
    return _registry.get(name)


def all_plugins() -> list[Plugin]:
    return list(_registry.values())
