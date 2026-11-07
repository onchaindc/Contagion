"""settings.get must fall back to the plugin.yaml defaults when Hermes returns nothing.

ctx.get_config does not merge config_schema defaults (hermes_cli/plugins_context.py:73), so
these defaults live in settings.DEFAULTS and have to stay in sync with plugin.yaml.
"""

import importlib.util
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]


def _load_plugin():
    """Import this checkout as the package ``contagion`` (relative imports need a package)."""
    if "contagion" in sys.modules:
        return sys.modules["contagion"]
    sys.path.insert(0, str(ROOT.parent))
    spec = importlib.util.spec_from_file_location(
        "contagion", ROOT / "__init__.py", submodule_search_locations=[str(ROOT)]
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules["contagion"] = module
    spec.loader.exec_module(module)
    return module


class FakeCtx:
    def __init__(self, values=None):
        self.values = values or {}

    def get_config(self, key, default=None):
        return self.values.get(key, default)


def test_defaults_match_manifest():
    plugin = _load_plugin()
    # PyYAML is not a dependency of this plugin; skip rather than add one for a test.
    yaml = __import__("pytest").importorskip("yaml")

    manifest = yaml.safe_load((ROOT / "plugin.yaml").read_text(encoding="utf-8"))
    schema = manifest["config_schema"]
    for key, spec in schema.items():
        assert plugin.settings.DEFAULTS[key] == spec["default"], key


def test_defaults_are_returned_without_user_settings():
    plugin = _load_plugin()
    plugin.settings.bind(FakeCtx())
    try:
        assert plugin.settings.get("mode") == "enforce"
        assert plugin.settings.get("untrusted_tool_patterns") == [
            "web_search", "web_extract", "browser_*", "mcp__*", "x_search"]
        assert plugin.settings.get("egress_tool_patterns") == [
            "discord*", "feishu_drive_*", "yb_send_*", "react_to_message", "mcp__*"]
        assert plugin.settings.get("extra_secret_paths") == []
    finally:
        plugin.settings.bind(None)


def test_overrides_win_and_lists_are_copies():
    plugin = _load_plugin()
    plugin.settings.bind(FakeCtx({"mode": "monitor", "untrusted_tool_patterns": []}))
    try:
        assert plugin.settings.get("mode") == "monitor"
        assert plugin.settings.get("untrusted_tool_patterns") == []
        defaults = plugin.settings.get("egress_tool_patterns")
        defaults.append("mutated")
        assert "mutated" not in plugin.settings.get("egress_tool_patterns")
    finally:
        plugin.settings.bind(None)


def test_get_config_errors_fall_back():
    class Boom:
        def get_config(self, key, default=None):
            raise RuntimeError("config unavailable")

    plugin = _load_plugin()
    plugin.settings.bind(Boom())
    try:
        assert plugin.settings.get("mode") == "enforce"
    finally:
        plugin.settings.bind(None)
