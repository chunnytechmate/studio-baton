"""Configuration loading and access.

Layering, lowest precedence first:

1. ``src/baton/defaults.yaml`` shipped in the package
2. the profile's ``baton.yaml``
3. ``BATON__`` environment overrides (``BATON__DOCS__DRIVER=notion``)

Secrets are never values in this tree. Config names the *environment variable*
that holds a credential; :meth:`Config.secret` reads it. That is what makes a
profile safe to commit to a private repository and safe to paste into an issue
with the env vars redacted.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from ..errors import ConfigError
from . import paths

ENV_PREFIX = "BATON__"
_MISSING = object()


def _deep_merge(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    """Merge ``overlay`` onto ``base``, recursing into dicts only.

    Lists replace wholesale rather than concatenating: a profile that lists
    three preserve rules means exactly those three, not those three plus
    whatever the defaults happened to include.
    """
    merged = dict(base)
    for key, value in overlay.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def _coerce(raw: str) -> Any:
    """Interpret an environment override using YAML scalar rules."""
    try:
        return yaml.safe_load(raw)
    except yaml.YAMLError:
        return raw


def _env_overrides() -> dict[str, Any]:
    """Collect ``BATON__A__B=value`` variables into a nested dict."""
    overrides: dict[str, Any] = {}
    for name, raw in os.environ.items():
        if not name.startswith(ENV_PREFIX):
            continue
        trail = [part.lower() for part in name[len(ENV_PREFIX) :].split("__") if part]
        if not trail:
            continue
        cursor = overrides
        for part in trail[:-1]:
            nxt = cursor.setdefault(part, {})
            if not isinstance(nxt, dict):
                nxt = {}
                cursor[part] = nxt
            cursor = nxt
        cursor[trail[-1]] = _coerce(raw)
    return overrides


def _load_yaml(path: Path) -> dict[str, Any]:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ConfigError(
            f"Cannot read {path}: {exc}",
            remedy="Check the file exists and is readable by this user.",
        ) from exc
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise ConfigError(
            f"{path} is not valid YAML: {exc}",
            remedy="Fix the syntax error at the reported line and re-run.",
        ) from exc
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise ConfigError(
            f"{path} must contain a mapping at the top level, found {type(data).__name__}.",
            remedy="Wrap the contents in `key: value` pairs.",
        )
    return data


@dataclass(frozen=True)
class Config:
    """Resolved configuration plus the paths it was resolved from."""

    data: dict[str, Any]
    config_file: Path
    profile_dir: Path
    _state_dir: Path = field(repr=False, default=Path("."))

    # -- lookup ------------------------------------------------------------

    def get(self, dotted: str, default: Any = _MISSING) -> Any:
        """Read a dotted key such as ``docs.properties.status``.

        Args:
            dotted: Dot-separated path into the config tree.
            default: Returned when the key is absent. Omit it to make the key
                required — a missing required key is a :class:`ConfigError`,
                not a ``None`` that surfaces as a confusing failure later.

        Raises:
            ConfigError: The key is absent and no default was supplied.
        """
        cursor: Any = self.data
        for part in dotted.split("."):
            if not isinstance(cursor, dict) or part not in cursor:
                if default is _MISSING:
                    raise ConfigError(
                        f"Missing required setting `{dotted}` in {self.config_file}.",
                        remedy=f"Add `{dotted}` to baton.yaml, or run `baton config show` "
                        "to see the effective configuration.",
                    )
                return default
            cursor = cursor[part]
        return cursor

    def section(self, dotted: str) -> dict[str, Any]:
        """Read a dotted key that must be a mapping."""
        value = self.get(dotted, {})
        if not isinstance(value, dict):
            raise ConfigError(
                f"Setting `{dotted}` must be a mapping, found {type(value).__name__}.",
                remedy="Correct the type in baton.yaml.",
            )
        return value

    # -- secrets -----------------------------------------------------------

    def secret(self, dotted_env_key: str, *, required: bool = True) -> str | None:
        """Read the credential named by a ``*_env`` setting.

        Args:
            dotted_env_key: Config path holding an environment variable *name*,
                for example ``docs.notion.token_env``.
            required: Raise when the variable is unset or empty.

        Returns:
            The credential value, or ``None`` when optional and unset.

        Raises:
            ConfigError: The variable is required but unset.
        """
        env_name = self.get(dotted_env_key)
        if not isinstance(env_name, str) or not env_name:
            raise ConfigError(
                f"Setting `{dotted_env_key}` must name an environment variable.",
                remedy="Set it to the variable name that holds the credential, "
                "for example `token_env: NOTION_API_TOKEN`.",
            )
        value = os.environ.get(env_name, "")
        if not value:
            if not required:
                return None
            raise ConfigError(
                f"Environment variable {env_name} is not set.",
                remedy=f"Export {env_name} (or add it to your .env) and re-run. "
                "Run `baton doctor` to check every credential at once.",
                details={"env": env_name, "setting": dotted_env_key},
            )
        return value

    # -- paths -------------------------------------------------------------

    def path(self, dotted: str, default: Any = _MISSING) -> Path:
        """Resolve a configured path relative to the profile directory."""
        raw = self.get(dotted, default)
        candidate = Path(str(raw)).expanduser()
        return candidate if candidate.is_absolute() else self.profile_dir / candidate

    @property
    def state_dir(self) -> Path:
        """Directory holding mutable run state."""
        return self._state_dir

    # -- convenience -------------------------------------------------------

    @property
    def locale(self) -> str:
        return str(self.get("locale", "en"))

    @property
    def timezone(self) -> str:
        return str(self.get("timezone", "UTC"))

    def label(self, key: str) -> str:
        """Look up a domain label, falling back to the key itself."""
        return str(self.section("labels").get(key, key))


def packaged_defaults() -> dict[str, Any]:
    """The defaults shipped inside the wheel."""
    return _load_yaml(Path(__file__).resolve().parent.parent / "defaults.yaml")


def load(explicit: str | Path | None = None) -> Config:
    """Load the effective configuration.

    Args:
        explicit: A profile directory or a direct path to a ``baton.yaml``.

    Returns:
        The merged :class:`Config`.

    Raises:
        ConfigError: No profile found, or the profile is malformed.
    """
    config_file = paths.find_config(explicit)
    profile_dir = config_file.parent

    merged = _deep_merge(packaged_defaults(), _load_yaml(config_file))
    merged = _deep_merge(merged, _env_overrides())

    version = merged.get("version")
    if version != 1:
        raise ConfigError(
            f"Unsupported config version {version!r} in {config_file}.",
            remedy="This build understands `version: 1`. Upgrade Baton or the profile.",
        )

    return Config(
        data=merged,
        config_file=config_file,
        profile_dir=profile_dir,
        _state_dir=paths.state_dir(profile_dir),
    )
