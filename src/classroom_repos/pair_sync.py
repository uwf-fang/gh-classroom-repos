from __future__ import annotations

import fnmatch
import hashlib
import json
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .config import Config, PairSyncConfig
from .core import discover_repositories
from .git import is_dirty, is_git_worktree

MARKER_VERSION = 1


@dataclass(frozen=True)
class RepoPair:
    name: str
    provided: Path
    solution: Path


@dataclass(frozen=True)
class PairFileStatus:
    path: str
    status: str
    message: str


@dataclass(frozen=True)
class PairCheckResult:
    pair: RepoPair
    status: str
    dirty: bool
    issues: tuple[PairFileStatus, ...]
    skipped_reason: str | None = None

    @property
    def ok(self) -> bool:
        return self.status == "ok"


@dataclass(frozen=True)
class PairUpdateAction:
    path: str
    status: str
    message: str


@dataclass(frozen=True)
class PairUpdateResult:
    pair: RepoPair
    direction: str
    dirty: bool
    actions: tuple[PairUpdateAction, ...]
    skipped_reason: str | None = None
    marker_updated: bool = False


@dataclass(frozen=True)
class PairCreateResult:
    pair: RepoPair
    actions: tuple[PairUpdateAction, ...]
    skipped_reason: str | None = None
    marker_written: bool = False


@dataclass(frozen=True)
class PairInitResult:
    pair: RepoPair
    actions: tuple[PairUpdateAction, ...]
    skipped_reason: str | None = None
    marker_written: bool = False


def require_pair_sync(config: Config) -> PairSyncConfig:
    if config.pair_sync is None:
        raise ValueError("Config key 'pair_sync' is required for pair commands.")
    return config.pair_sync


def discover_pairs(config: Config) -> list[RepoPair]:
    pair_config = require_pair_sync(config)
    repos = discover_repositories(config.repo_roots)
    by_name = {repo.name: repo for repo in repos}
    pairs: list[RepoPair] = []

    for solution_name, solution in by_name.items():
        if not solution_name.endswith(pair_config.solution_suffix):
            continue
        provided_name = solution_name[: -len(pair_config.solution_suffix)]
        provided = by_name.get(provided_name)
        if provided is not None:
            pairs.append(RepoPair(name=provided_name, provided=provided, solution=solution))

    return sorted(pairs, key=lambda pair: pair.name)


def find_pair(config: Config, pair_name: str | None = None, solution: Path | None = None) -> RepoPair:
    pair_config = require_pair_sync(config)
    if solution is not None:
        solution_path = _resolve_repo_reference(config, solution)
        if not solution_path.name.endswith(pair_config.solution_suffix):
            raise ValueError(f"Solution repo name must end with {pair_config.solution_suffix!r}.")
        provided = solution_path.with_name(solution_path.name[: -len(pair_config.solution_suffix)])
        return RepoPair(name=provided.name, provided=provided, solution=solution_path)

    pairs = discover_pairs(config)
    if pair_name is None:
        if len(pairs) != 1:
            raise ValueError("Specify --pair when zero or multiple pairs are available.")
        return pairs[0]

    for pair in pairs:
        if pair.name == pair_name or pair.provided.name == pair_name or pair.solution.name == pair_name:
            return pair
    raise ValueError(f"Pair not found: {pair_name}")


def check_pairs(config: Config, pair_name: str | None = None) -> list[PairCheckResult]:
    pairs = [find_pair(config, pair_name)] if pair_name else discover_pairs(config)
    return [check_pair(config, pair) for pair in pairs]


def check_pair(config: Config, pair: RepoPair) -> PairCheckResult:
    pair_config = require_pair_sync(config)
    skipped = _repo_skip_reason(pair)
    if skipped:
        return PairCheckResult(pair=pair, status="skipped", dirty=True, issues=(), skipped_reason=skipped)

    plan = _build_file_plan(pair_config, pair.solution, pair.provided)
    marker = _read_marker(pair.provided / pair_config.marker_file)
    marker_hashes = _marker_hashes(marker)
    issues: list[PairFileStatus] = []

    plan_errors = _filter_plan_errors(plan.errors, marker_hashes)
    if plan_errors:
        issues.extend(PairFileStatus(path, "missing_in_solution", message) for path, message in plan_errors)
        return PairCheckResult(pair=pair, status="error", dirty=False, issues=tuple(issues))

    if marker is None:
        for path in sorted(plan.current_files):
            source_hash = _hash_file(pair.solution / path)
            target_hash = _hash_file(pair.provided / path)
            if target_hash != source_hash:
                issues.append(
                    PairFileStatus(path, "uninitialized", "No sync marker; file would be copied from solution.")
                )
        status = "uninitialized" if issues else "uninitialized"
        return PairCheckResult(pair=pair, status=status, dirty=False, issues=tuple(issues))

    all_paths = sorted(set(plan.current_files) | set(marker_hashes))
    for path in all_paths:
        issues.extend(_classify_path(pair, path, marker_hashes.get(path), path in plan.current_files))

    status = _overall_status(issues)
    return PairCheckResult(pair=pair, status=status, dirty=False, issues=tuple(issues))


def update_pairs(
    config: Config,
    pair_name: str | None = None,
    apply: bool = False,
    backward: bool = False,
) -> list[PairUpdateResult]:
    pairs = [find_pair(config, pair_name)] if pair_name else discover_pairs(config)
    return [update_pair(config, pair, apply=apply, backward=backward) for pair in pairs]


def update_pair(config: Config, pair: RepoPair, apply: bool = False, backward: bool = False) -> PairUpdateResult:
    pair_config = require_pair_sync(config)
    skipped = _repo_skip_reason(pair)
    direction = "backward" if backward else "forward"
    if skipped:
        return PairUpdateResult(pair=pair, direction=direction, dirty=True, actions=(), skipped_reason=skipped)

    if backward:
        return _update_backward(pair_config, pair, apply=apply)
    return _update_forward(pair_config, pair, apply=apply)


def init_pairs(config: Config, pair_name: str | None = None, apply: bool = False) -> list[PairInitResult]:
    pairs = [find_pair(config, pair_name)] if pair_name else discover_pairs(config)
    return [init_pair(config, pair, apply=apply) for pair in pairs]


def init_pair(config: Config, pair: RepoPair, apply: bool = False) -> PairInitResult:
    pair_config = require_pair_sync(config)
    skipped = _repo_skip_reason(pair)
    if skipped:
        return PairInitResult(pair=pair, actions=(), skipped_reason=skipped)

    plan = _build_file_plan(pair_config, pair.solution, pair.provided)
    if plan.errors:
        actions = tuple(PairUpdateAction(path, "error", message) for path, message in plan.errors)
        return PairInitResult(pair=pair, actions=actions, skipped_reason="solution is missing configured paths")

    actions: list[PairUpdateAction] = []
    for path in sorted(plan.current_files):
        source_hash = _hash_file(pair.solution / path)
        target_hash = _hash_file(pair.provided / path)
        if target_hash is None:
            actions.append(PairUpdateAction(path, "error", "provided repo is missing configured file"))
        elif target_hash == source_hash:
            actions.append(PairUpdateAction(path, "recorded" if apply else "would_record", "matches solution"))
        else:
            actions.append(
                PairUpdateAction(
                    path,
                    "recorded_diff" if apply else "would_record_diff",
                    "differs from solution; recording current provided file as baseline",
                )
            )

    if any(action.status == "error" for action in actions):
        return PairInitResult(
            pair=pair,
            actions=tuple(actions),
            skipped_reason="provided repo is missing configured files",
        )

    if apply:
        _write_marker(pair_config, pair, sorted(plan.current_files), deleted_files=())

    return PairInitResult(pair=pair, actions=tuple(actions), marker_written=apply)


def create_pair(config: Config, solution: Path, apply: bool = False) -> PairCreateResult:
    pair_config = require_pair_sync(config)
    pair = find_pair(config, solution=solution)

    if not is_git_worktree(pair.solution):
        return PairCreateResult(pair=pair, actions=(), skipped_reason="solution is not a Git worktree")
    if is_dirty(pair.solution):
        return PairCreateResult(pair=pair, actions=(), skipped_reason="solution repository has uncommitted changes")
    if pair.provided.exists():
        return PairCreateResult(pair=pair, actions=(), skipped_reason="provided repository already exists")

    plan = _build_file_plan(pair_config, pair.solution, pair.provided)
    if plan.errors:
        actions = tuple(PairUpdateAction(path, "error", message) for path, message in plan.errors)
        return PairCreateResult(pair=pair, actions=actions, skipped_reason="solution is missing configured paths")

    actions = tuple(
        PairUpdateAction(path, "created" if apply else "would_create", "copy from solution")
        for path in sorted(plan.current_files)
    )

    if apply:
        pair.provided.mkdir(parents=True)
        for path in sorted(plan.current_files):
            _copy_path(pair.solution / path, pair.provided / path)
        subprocess.run(["git", "init"], cwd=pair.provided, check=True, capture_output=True, text=True)
        _write_marker(pair_config, pair, sorted(plan.current_files), deleted_files=())

    return PairCreateResult(pair=pair, actions=actions, marker_written=apply)


def confirm_backward(pair_name: str) -> bool:
    import typer

    typer.secho("DANGEROUS: backward update copies provided repo files into the solution repo.", fg=typer.colors.RED)
    response = typer.prompt(f"Type {pair_name!r} to confirm")
    return response == pair_name


@dataclass(frozen=True)
class _FilePlan:
    current_files: tuple[str, ...]
    errors: tuple[tuple[str, str], ...]


def _build_file_plan(pair_config: PairSyncConfig, source: Path, target: Path) -> _FilePlan:
    files: set[str] = set()
    errors: list[tuple[str, str]] = []

    for pattern in pair_config.paths:
        matches = _expand_pattern(source, pattern)
        if not matches:
            errors.append((pattern, "Configured path does not exist in solution repo."))
            continue
        files.update(matches)

    marker_path = Path(pair_config.marker_file).as_posix()
    return _FilePlan(
        current_files=tuple(sorted(path for path in files if path != marker_path and (source / path).is_file())),
        errors=tuple(errors),
    )


def _expand_pattern(root: Path, pattern: str) -> list[str]:
    if pattern.endswith("/**"):
        base = root / pattern[:-3]
        if base.is_dir():
            return sorted(path.relative_to(root).as_posix() for path in base.rglob("*") if path.is_file())
        return []

    if any(char in pattern for char in "*?["):
        return sorted(path.relative_to(root).as_posix() for path in root.glob(pattern) if path.is_file())

    path = root / pattern
    if path.is_file():
        return [Path(pattern).as_posix()]
    if path.is_dir():
        return sorted(child.relative_to(root).as_posix() for child in path.rglob("*") if child.is_file())
    return []


def _update_forward(pair_config: PairSyncConfig, pair: RepoPair, apply: bool) -> PairUpdateResult:
    plan = _build_file_plan(pair_config, pair.solution, pair.provided)
    marker = _read_marker(pair.provided / pair_config.marker_file)
    marker_hashes = _marker_hashes(marker)
    plan_errors = _filter_plan_errors(plan.errors, marker_hashes)
    if plan_errors:
        actions = tuple(PairUpdateAction(path, "error", message) for path, message in plan_errors)
        return PairUpdateResult(pair=pair, direction="forward", dirty=False, actions=actions)

    actions: list[PairUpdateAction] = []

    for path in sorted(plan.current_files):
        source_hash = _hash_file(pair.solution / path)
        target_hash = _hash_file(pair.provided / path)
        marker_hash = marker_hashes.get(path)
        if marker_hash is not None and target_hash != marker_hash and source_hash != target_hash:
            actions.append(PairUpdateAction(path, "conflict", "provided file changed independently; not overwritten"))
            continue
        if target_hash == source_hash:
            actions.append(PairUpdateAction(path, "unchanged", "already matches solution"))
            continue
        if apply:
            _copy_path(pair.solution / path, pair.provided / path)
        actions.append(PairUpdateAction(path, "updated" if apply else "would_update", "copy from solution"))

    for path in sorted(set(marker_hashes) - set(plan.current_files)):
        target = pair.provided / path
        if target.exists():
            if apply:
                target.unlink()
            actions.append(PairUpdateAction(path, "deleted" if apply else "would_delete", "removed from solution"))

    marker_updated = False
    if apply and not any(action.status in {"error", "conflict"} for action in actions):
        deleted = tuple(action.path for action in actions if action.status == "deleted")
        _write_marker(pair_config, pair, sorted(plan.current_files), deleted_files=deleted)
        marker_updated = True

    return PairUpdateResult(
        pair=pair,
        direction="forward",
        dirty=False,
        actions=tuple(actions),
        marker_updated=marker_updated,
    )


def _update_backward(pair_config: PairSyncConfig, pair: RepoPair, apply: bool) -> PairUpdateResult:
    plan = _build_file_plan(pair_config, pair.provided, pair.solution)
    if plan.errors:
        actions = tuple(PairUpdateAction(path, "error", message) for path, message in plan.errors)
        return PairUpdateResult(pair=pair, direction="backward", dirty=False, actions=actions)

    actions: list[PairUpdateAction] = []
    for path in sorted(plan.current_files):
        source_hash = _hash_file(pair.provided / path)
        target_hash = _hash_file(pair.solution / path)
        if target_hash == source_hash:
            actions.append(PairUpdateAction(path, "unchanged", "already matches provided repo"))
            continue
        if apply:
            _copy_path(pair.provided / path, pair.solution / path)
        actions.append(PairUpdateAction(path, "updated" if apply else "would_update", "copy from provided repo"))

    return PairUpdateResult(pair=pair, direction="backward", dirty=False, actions=tuple(actions))


def _filter_plan_errors(
    errors: tuple[tuple[str, str], ...],
    marker_hashes: dict[str, str],
) -> tuple[tuple[str, str], ...]:
    filtered: list[tuple[str, str]] = []
    for pattern, message in errors:
        if any(char in pattern for char in "*?[") and any(fnmatch.fnmatch(path, pattern) for path in marker_hashes):
            continue
        filtered.append((pattern, message))
    return tuple(filtered)


def _classify_path(
    pair: RepoPair,
    path: str,
    marker_hash: str | None,
    exists_in_solution: bool,
) -> list[PairFileStatus]:
    source_hash = _hash_file(pair.solution / path) if exists_in_solution else None
    target_hash = _hash_file(pair.provided / path)
    if source_hash == target_hash and marker_hash == source_hash:
        return []
    if marker_hash is None:
        return [PairFileStatus(path, "untracked_by_marker", "File is not recorded in sync marker.")]
    if source_hash is None:
        if target_hash is None:
            return [PairFileStatus(path, "deleted_in_solution", "File was removed from solution and provided repo.")]
        return [
            PairFileStatus(
                path, "deleted_in_solution", "File was removed from solution but still exists in provided repo."
            )
        ]

    solution_changed = source_hash != marker_hash
    provided_changed = target_hash != marker_hash
    if source_hash == target_hash:
        return [PairFileStatus(path, "marker_stale", "Both repos match, but marker is stale.")]
    if solution_changed and not provided_changed:
        return [PairFileStatus(path, "needs_forward_sync", "Solution changed; provided repo needs update.")]
    if provided_changed and not solution_changed:
        return [PairFileStatus(path, "provided_newer", "Provided repo changed after last sync.")]
    if solution_changed and provided_changed:
        return [PairFileStatus(path, "conflict", "Both repos changed after last sync.")]
    return [PairFileStatus(path, "missing_in_provided", "Provided repo is missing a synced file.")]


def _overall_status(issues: list[PairFileStatus]) -> str:
    if not issues:
        return "ok"
    priority = [
        "conflict",
        "missing_in_solution",
        "provided_newer",
        "needs_forward_sync",
        "deleted_in_solution",
        "untracked_by_marker",
        "marker_stale",
        "missing_in_provided",
    ]
    statuses = {issue.status for issue in issues}
    for status in priority:
        if status in statuses:
            return status
    return "needs_attention"


def _repo_skip_reason(pair: RepoPair) -> str | None:
    if not is_git_worktree(pair.solution):
        return "solution is not a Git worktree"
    if not is_git_worktree(pair.provided):
        return "provided repo is not a Git worktree"
    if is_dirty(pair.solution):
        return "solution repository has uncommitted changes"
    if is_dirty(pair.provided):
        return "provided repository has uncommitted changes"
    return None


def _read_marker(path: Path) -> dict | None:
    if not path.exists():
        return None
    with path.open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    return data if isinstance(data, dict) else None


def _marker_hashes(marker: dict | None) -> dict[str, str]:
    if not marker:
        return {}
    hashes = marker.get("files", {})
    if not isinstance(hashes, dict):
        return {}
    return {str(path): str(value) for path, value in hashes.items()}


def _write_marker(
    pair_config: PairSyncConfig,
    pair: RepoPair,
    paths: list[str],
    deleted_files: tuple[str, ...],
) -> None:
    marker_path = pair.provided / pair_config.marker_file
    marker_path.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "version": MARKER_VERSION,
        "solution_repo": pair.solution.name,
        "solution_head": _git_head(pair.solution),
        "paths": list(pair_config.paths),
        "files": {path: _hash_file(pair.provided / path) for path in paths if (pair.provided / path).is_file()},
        "deleted_files": sorted(deleted_files),
    }
    marker_path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _git_head(repo: Path) -> str:
    result = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"], check=True, capture_output=True, text=True)
    return result.stdout.strip()


def _copy_path(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, target)


def _hash_file(path: Path) -> str | None:
    if not path.is_file():
        return None
    hasher = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def _resolve_repo_reference(config: Config, reference: Path) -> Path:
    path = reference.expanduser()
    if path.is_absolute():
        return path.resolve()
    candidates = [(Path.cwd() / path).resolve()]
    candidates.extend((root / path).resolve() for root in config.repo_roots)
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return candidates[0]
