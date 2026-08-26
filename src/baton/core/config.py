"""Configuration loading and access.

Layering, lowest precedence first:

1. ``src/baton/defaults.yaml`` shipped in the package
2. the profile's ``baton.yaml``
3. ``BATON__`` environment overrides (``BATON__DOCS__DRIVER=notion``)

Secrets are never values in this tree. Config names the *environment variable*
that holds a credential; :meth:`Config.secret` reads it. That is what makes a
profile safe to commit to a private repository and safe to paste into an issue
with the env vars redacted.

Where those variables come from: :func:`load` reads the profile's ``.env`` into
the process environment before anything asks for a credential. A variable
already set in the real environment always wins, so a shell export overrides
the file rather than the other way around — the file is the default, the
environment is the override. Loading it into ``os.environ`` rather than into a
private mapping is deliberate: ``doctor``, detached jobs, and vendor SDKs that
read the environment for themselves then all see one set of values.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from ..errors import ConfigError
from . import paths

ENV_PREFIX = "BATON__"
_MISSING = object()

#: A POSIX environment variable name — what a `.env` line may declare.
_ENV_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


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
    """Collect ``BATON__A__B=value`` variables into a nested dict.

    Two variables that fold onto the same setting — one scalar, one nested
    under it — are rejected instead of resolved: the winner used to depend on
    ``os.environ``'s iteration order, which a user can neither see nor rely
    on, and the loser was silently converted (a scalar became a dict, or a
    whole section was replaced by a scalar) on the way. Naming both variables
    in an error is the only outcome that cannot surprise anyone (M5).
    """
    sources: dict[tuple[str, ...], str] = {}
    for name in os.environ:
        if not name.startswith(ENV_PREFIX):
            continue
        trail = tuple(part.lower() for part in name[len(ENV_PREFIX) :].split("__") if part)
        if not trail:
            continue
        duplicate = sources.get(trail)
        if duplicate is not None:
            # Case does not distinguish settings: BATON__DOCS__DRIVER and
            # baton__docs__driver are the same setting to two variables.
            raise ConfigError(
                f"`{duplicate}` and `{name}` both set the same override "
                f"(`{ENV_PREFIX}{'__'.join(trail).upper()}`).",
                remedy="Remove one of the two — which one would win is not deterministic.",
            )
        sources[trail] = name

    for trail, name in sources.items():
        for deeper, deeper_name in sources.items():
            if len(deeper) > len(trail) and deeper[: len(trail)] == trail:
                raise ConfigError(
                    f"`{name}` sets `{ENV_PREFIX}{'__'.join(trail).upper()}` to a single "
                    f"value while `{deeper_name}` sets a key inside it "
                    f"(`{ENV_PREFIX}{'__'.join(deeper).upper()}`).",
                    remedy="A setting cannot be both a value and a section. "
                    "Set the individual keys, or remove one of the two variables.",
                )

    overrides: dict[str, Any] = {}
    for trail, name in sources.items():
        cursor = overrides
        for part in trail[:-1]:
            cursor = cursor.setdefault(part, {})
        cursor[trail[-1]] = _coerce(os.environ[name])
    return overrides


def parse_env_file(text: str, source: Path) -> dict[str, str]:
    """Parse ``.env`` text into a mapping.

    The format is the small, boring subset every ``.env`` file agrees on:
    ``KEY=value`` one per line, blank lines and ``#`` comment lines ignored, an
    optional ``export`` prefix tolerated, and a value wrapped in matching
    single or double quotes unwrapped. Everything else is taken literally —
    there is no escape processing and no inline-comment stripping, so a ``#``
    inside a token stays part of the token rather than silently truncating a
    credential.

    Args:
        text: The file's contents.
        source: The path the text came from, used in error messages.

    Returns:
        The variables the file declares, in the order it declares them.

    Raises:
        ConfigError: A line is not blank, not a comment, and not ``KEY=value``.
    """
    values: dict[str, str] = {}
    for number, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].lstrip()
        key, separator, value = line.partition("=")
        key = key.strip()
        if not separator or not _ENV_NAME.fullmatch(key):
            raise ConfigError(
                f"{source} line {number} is not `KEY=value`.",
                remedy="Write one `KEY=value` per line, or start the line with `#` "
                "to comment it out.",
                details={"file": str(source), "line": number},
            )
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        values[key] = value
    return values


def apply_env_file(profile_dir: Path) -> dict[str, str]:
    """Load ``<profile>/.env`` into the process environment.

    A variable that already holds a non-empty value is left alone: an export in
    the shell, or a variable injected by a container, outranks the file. Empty
    counts as unset here for the same reason it does in :meth:`Config.secret` —
    an exported-but-blank credential is a hole, not a decision.

    Args:
        profile_dir: The directory holding ``baton.yaml``.

    Returns:
        The variables that were read from the file, whether or not each one was
        applied. An absent file is not an error and returns an empty mapping.

    Raises:
        ConfigError: The file exists but cannot be read or parsed.
    """
    path = profile_dir / ".env"
    if not path.is_file():
        return {}
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ConfigError(
            f"Cannot read {path}: {exc}",
            remedy="Check the file is readable by this user, or remove it.",
        ) from exc
    values = parse_env_file(text, path)
    for key, value in values.items():
        if not os.environ.get(key, ""):
            os.environ[key] = value
    return values


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
        ConfigError: No profile found, the profile is malformed, or its ``.env``
            exists but cannot be read or parsed.
    """
    config_file = paths.find_config(explicit)
    profile_dir = config_file.parent

    # Before the overrides are collected, so a `BATON__…` line in the profile's
    # .env carries the same weight as one exported in the shell.
    apply_env_file(profile_dir)

    merged = _deep_merge(packaged_defaults(), _load_yaml(config_file))
    merged = _deep_merge(merged, _env_overrides())

    version = merged.get("version")
    # `True == 1` and `1.0 == 1` in Python, so the value comparison alone would
    # let `version: true` and `version: 1.0` through the gate. Require an int
    # that is not a bool.
    if isinstance(version, bool) or not isinstance(version, int) or version != 1:
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
