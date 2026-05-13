from __future__ import annotations

import fnmatch
import re
import shutil
from dataclasses import dataclass
from pathlib import Path

from .config import CheckedFileRule, Config
from .git import is_dirty, is_git_worktree


@dataclass(frozen=True)
class CheckIssue:
    repo: str
    path: str
    code: str
    message: str


@dataclass(frozen=True)
class RepoCheck:
    repo: Path
    dirty: bool
    issues: tuple[CheckIssue, ...] = ()

    @property
    def ok(self) -> bool:
        return not self.issues


@dataclass(frozen=True)
class UpdateAction:
    repo: Path
    path: str
    status: str
    message: str


@dataclass(frozen=True)
class UpdateResult:
    repo: Path
    dirty: bool
    actions: tuple[UpdateAction, ...] = ()
    skipped_reason: str | None = None


def discover_repositories(repo_roots: tuple[Path, ...]) -> list[Path]:
    repos: list[Path] = []
    seen: set[Path] = set()
    for root in repo_roots:
        if not root.exists():
            continue

        root_repo: Path | None = root.resolve() if is_git_worktree(root) else None
        child_repos: list[Path] = []
        for git_dir in root.rglob(".git"):
            repo = git_dir.parent.resolve()
            if repo == root_repo:
                continue
            if is_git_worktree(repo):
                child_repos.append(repo)

        candidates = child_repos or ([root_repo] if root_repo else [])
        for repo in candidates:
            if repo not in seen:
                repos.append(repo)
                seen.add(repo)
    return sorted(repos)


def check_repositories(config: Config, repos: list[Path] | None = None) -> list[RepoCheck]:
    targets = repos if repos is not None else discover_repositories(config.repo_roots)
    return [check_repository(config, repo) for repo in targets]


def check_repository(config: Config, repo: Path) -> RepoCheck:
    repo = repo.resolve()
    issues: list[CheckIssue] = []
    dirty = is_dirty(repo) if is_git_worktree(repo) else True

    if not is_git_worktree(repo):
        issues.append(_issue(repo, ".", "not_git_repo", "Path is not a Git worktree."))
        return RepoCheck(repo=repo, dirty=dirty, issues=tuple(issues))

    for relative in config.managed_files:
        issues.extend(_check_managed_file(config, repo, relative))

    for rule in config.checked_files:
        issues.extend(_check_checked_file(repo, rule))

    return RepoCheck(repo=repo, dirty=dirty, issues=tuple(issues))


def update_repositories(config: Config, repos: list[Path] | None = None, apply: bool = False) -> list[UpdateResult]:
    targets = repos if repos is not None else discover_repositories(config.repo_roots)
    return [update_repository(config, repo, apply=apply) for repo in targets]


def update_repository(config: Config, repo: Path, apply: bool = False) -> UpdateResult:
    repo = repo.resolve()
    if not is_git_worktree(repo):
        return UpdateResult(repo=repo, dirty=True, skipped_reason="not a Git worktree")

    dirty = is_dirty(repo)
    if dirty:
        return UpdateResult(repo=repo, dirty=True, skipped_reason="repository has uncommitted changes")

    actions: list[UpdateAction] = []
    for relative in config.managed_files:
        source = config.template_root / relative
        target = repo / relative
        if not source.exists():
            actions.append(UpdateAction(repo, relative, "error", "template file is missing"))
            continue

        if target.exists() and target.read_bytes() == source.read_bytes():
            actions.append(UpdateAction(repo, relative, "unchanged", "already matches template"))
            continue

        status = "updated" if apply else "would_update"
        message = "copied from template" if apply else "would copy from template"
        if apply:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
        actions.append(UpdateAction(repo, relative, status, message))

    return UpdateResult(repo=repo, dirty=dirty, actions=tuple(actions))


def _check_managed_file(config: Config, repo: Path, relative: str) -> list[CheckIssue]:
    source = config.template_root / relative
    target = repo / relative
    issues: list[CheckIssue] = []
    if not source.exists():
        issues.append(_issue(repo, relative, "missing_template", "Template file is missing."))
        return issues
    if not target.exists():
        issues.append(_issue(repo, relative, "missing_managed_file", "Managed file is missing."))
        return issues
    if not target.is_file():
        issues.append(_issue(repo, relative, "not_file", "Managed path is not a file."))
        return issues
    if target.read_bytes() != source.read_bytes():
        issues.append(_issue(repo, relative, "content_mismatch", "Managed file differs from template."))
    return issues


def _check_checked_file(repo: Path, rule: CheckedFileRule) -> list[CheckIssue]:
    target = repo / rule.path
    issues: list[CheckIssue] = []
    if not target.exists():
        issues.append(_issue(repo, rule.path, "missing_checked_file", "Checked file or directory is missing."))
        return issues

    if rule.kind == "directory":
        if not target.is_dir():
            issues.append(_issue(repo, rule.path, "not_directory", "Expected path is not a directory."))
            return issues
        for pattern in rule.required_globs:
            if not any(fnmatch.fnmatch(path.name, pattern) for path in target.iterdir()):
                issues.append(_issue(repo, rule.path, "missing_required_glob", f"No entry matches glob {pattern!r}."))
        return issues

    if not target.is_file():
        issues.append(_issue(repo, rule.path, "not_file", "Expected path is not a file."))
        return issues

    content = target.read_text(encoding="utf-8", errors="replace")
    for pattern in rule.required_patterns:
        if not re.search(pattern, content, flags=re.MULTILINE):
            issues.append(_issue(repo, rule.path, "missing_required_pattern", f"Missing required pattern {pattern!r}."))
    return issues


def _issue(repo: Path, path: str, code: str, message: str) -> CheckIssue:
    return CheckIssue(repo=str(repo), path=path, code=code, message=message)
