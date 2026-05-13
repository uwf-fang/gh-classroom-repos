from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

DEFAULT_CONFIG = "classroom-repos.yml"


@dataclass(frozen=True)
class CheckedFileRule:
    path: str
    kind: str = "file"
    required_patterns: tuple[str, ...] = ()
    required_globs: tuple[str, ...] = ()


@dataclass(frozen=True)
class Config:
    path: Path
    repo_roots: tuple[Path, ...]
    template_root: Path
    managed_files: tuple[str, ...]
    checked_files: tuple[CheckedFileRule, ...] = field(default_factory=tuple)


def load_config(config_path: Path, default_repo_root: Path | None = None) -> Config:
    config_path = config_path.expanduser().resolve()
    with config_path.open("r", encoding="utf-8") as handle:
        raw = yaml.safe_load(handle) or {}

    if not isinstance(raw, dict):
        raise ValueError("Config file must contain a mapping.")

    base = config_path.parent
    repo_roots = _optional_list(raw, "repo_roots")
    managed_files = _required_list_with_alias(raw, "managed_files", "same_files")
    template_root_raw = raw.get("template_root", "templates")
    template_root = _resolve_path(base, str(template_root_raw))
    default_root = (default_repo_root or Path.cwd()).expanduser().resolve()

    checked_files_raw = _optional_list_with_alias(raw, "checked_files", "similar_files")
    checked_files = tuple(_parse_checked_rule(item) for item in checked_files_raw)

    return Config(
        path=config_path,
        repo_roots=tuple(_resolve_path(base, str(root)) for root in repo_roots) if repo_roots else (default_root,),
        template_root=template_root,
        managed_files=tuple(str(item) for item in managed_files),
        checked_files=checked_files,
    )


def _required_list(raw: dict[str, Any], key: str) -> list[Any]:
    value = raw.get(key)
    if not isinstance(value, list) or not value:
        raise ValueError(f"Config key '{key}' must be a non-empty list.")
    return value


def _required_list_with_alias(raw: dict[str, Any], key: str, alias: str) -> list[Any]:
    if key in raw:
        return _required_list(raw, key)
    if alias in raw:
        return _required_list(raw, alias)
    raise ValueError(f"Config key '{key}' must be a non-empty list.")


def _optional_list(raw: dict[str, Any], key: str) -> list[Any]:
    value = raw.get(key, [])
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError(f"Config key '{key}' must be a list when provided.")
    return value


def _optional_list_with_alias(raw: dict[str, Any], key: str, alias: str) -> list[Any]:
    if key in raw:
        return _optional_list(raw, key)
    return _optional_list(raw, alias)


def _parse_checked_rule(item: Any) -> CheckedFileRule:
    if isinstance(item, str):
        return CheckedFileRule(path=item)
    if not isinstance(item, dict) or "path" not in item:
        raise ValueError("Each checked_files entry must be a path string or mapping with a path.")

    kind = str(item.get("kind", "file"))
    if kind not in {"file", "directory"}:
        raise ValueError(f"Unsupported checked file kind for {item['path']!r}: {kind}")

    return CheckedFileRule(
        path=str(item["path"]),
        kind=kind,
        required_patterns=tuple(str(pattern) for pattern in item.get("required_patterns", [])),
        required_globs=tuple(str(pattern) for pattern in item.get("required_globs", [])),
    )


def _resolve_path(base: Path, value: str) -> Path:
    path = Path(value).expanduser()
    if path.is_absolute():
        return path.resolve()
    return (base / path).resolve()
