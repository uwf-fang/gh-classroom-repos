from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .config import Config
from .core import discover_repositories
from .pair_sync import check_pairs, discover_pairs, find_pair


@dataclass(frozen=True)
class RunResult:
    repo: Path
    command: tuple[str, ...]
    status: str
    returncode: int | None = None
    stdout: str = ""
    stderr: str = ""


@dataclass(frozen=True)
class GitStatus:
    repo: Path
    valid: bool
    branch: str | None
    dirty: bool
    staged: int = 0
    modified: int = 0
    untracked: int = 0
    ahead: int = 0
    behind: int = 0
    upstream: str | None = None
    message: str = ""

    @property
    def ok(self) -> bool:
        return self.valid and not self.dirty and self.ahead == 0 and self.behind == 0 and self.upstream is not None


@dataclass(frozen=True)
class CommitResult:
    repo: Path
    status: str
    message: str
    commit_hash: str | None = None


@dataclass(frozen=True)
class PairSummary:
    name: str
    status: str
    issue_count: int
    skipped_reason: str | None = None

    @property
    def ok(self) -> bool:
        return self.status == "ok"


def select_repositories(
    config: Config,
    scope: str = "all",
    pair_name: str | None = None,
    repo: Path | None = None,
) -> list[Path]:
    if repo is not None:
        return [repo.expanduser().resolve()]

    scope = scope.lower()
    if scope == "all":
        return discover_repositories(config.repo_roots)

    if scope == "pair":
        pair = find_pair(config, pair_name)
        return sorted([pair.provided, pair.solution])

    pairs = discover_pairs(config)
    if pair_name is not None:
        pairs = [
            pair
            for pair in pairs
            if pair.name == pair_name or pair.provided.name == pair_name or pair.solution.name == pair_name
        ]
        if not pairs:
            raise ValueError(f"Pair not found: {pair_name}")

    if scope == "provided":
        return sorted(pair.provided for pair in pairs)
    if scope == "solution":
        return sorted(pair.solution for pair in pairs)

    raise ValueError(f"Unsupported scope: {scope}")


def run_command(
    config: Config,
    command: list[str],
    scope: str,
    pair_name: str | None,
    repo: Path | None,
    apply: bool,
) -> list[RunResult]:
    if not command:
        raise ValueError("A command is required after '--'.")

    repos = select_repositories(config, scope=scope, pair_name=pair_name, repo=repo)
    if not apply:
        return [RunResult(repo=target, command=tuple(command), status="would_run") for target in repos]

    results: list[RunResult] = []
    for target in repos:
        completed = subprocess.run(command, cwd=target, check=False, text=True, capture_output=True)
        status = "ok" if completed.returncode == 0 else "failed"
        results.append(
            RunResult(
                repo=target,
                command=tuple(command),
                status=status,
                returncode=completed.returncode,
                stdout=completed.stdout,
                stderr=completed.stderr,
            )
        )
    return results


def git_statuses(config: Config, scope: str, pair_name: str | None, repo: Path | None) -> list[GitStatus]:
    return [git_status(target) for target in select_repositories(config, scope=scope, pair_name=pair_name, repo=repo)]


def git_status(repo: Path) -> GitStatus:
    result = subprocess.run(
        ["git", "-C", str(repo), "status", "--porcelain=v1", "--branch"],
        check=False,
        text=True,
        capture_output=True,
    )
    if result.returncode != 0:
        return GitStatus(repo=repo, valid=False, branch=None, dirty=True, message=result.stderr.strip())

    lines = result.stdout.splitlines()
    if not lines:
        return GitStatus(
            repo=repo,
            valid=True,
            branch=None,
            dirty=False,
            upstream=None,
            message="missing branch status",
        )

    branch, upstream, ahead, behind, message = _parse_branch_line(lines[0])
    staged = modified = untracked = 0
    for line in lines[1:]:
        if line.startswith("??"):
            untracked += 1
            continue
        if len(line) >= 2:
            if line[0] != " ":
                staged += 1
            if line[1] != " ":
                modified += 1

    dirty = staged > 0 or modified > 0 or untracked > 0
    return GitStatus(
        repo=repo,
        valid=True,
        branch=branch,
        dirty=dirty,
        staged=staged,
        modified=modified,
        untracked=untracked,
        ahead=ahead,
        behind=behind,
        upstream=upstream,
        message=message,
    )


def commit_repositories(
    config: Config,
    message: str,
    scope: str,
    pair_name: str | None,
    repo: Path | None,
) -> list[CommitResult]:
    if not message.strip():
        raise ValueError("Commit message must not be empty.")

    results: list[CommitResult] = []
    for target in select_repositories(config, scope=scope, pair_name=pair_name, repo=repo):
        status = git_status(target)
        if not status.valid:
            results.append(CommitResult(repo=target, status="error", message=status.message))
            continue
        if not status.dirty:
            results.append(CommitResult(repo=target, status="skipped", message="clean repository"))
            continue

        add = subprocess.run(["git", "-C", str(target), "add", "-A"], check=False, text=True, capture_output=True)
        if add.returncode != 0:
            results.append(CommitResult(repo=target, status="error", message=add.stderr.strip()))
            continue

        commit = subprocess.run(
            ["git", "-C", str(target), "commit", "-m", message],
            check=False,
            text=True,
            capture_output=True,
        )
        if commit.returncode != 0:
            results.append(
                CommitResult(repo=target, status="error", message=commit.stderr.strip() or commit.stdout.strip())
            )
            continue

        commit_hash = _git_output(target, "rev-parse", "--short", "HEAD")
        results.append(CommitResult(repo=target, status="committed", message=message, commit_hash=commit_hash))

    return results


def pair_summaries(config: Config) -> list[PairSummary]:
    summaries: list[PairSummary] = []
    for result in check_pairs(config):
        summaries.append(
            PairSummary(
                name=result.pair.name,
                status=result.status,
                issue_count=len(result.issues),
                skipped_reason=result.skipped_reason,
            )
        )
    return summaries


def _parse_branch_line(line: str) -> tuple[str | None, str | None, int, int, str]:
    if not line.startswith("## "):
        return None, None, 0, 0, "missing branch header"

    body = line[3:]
    message = ""
    if body.startswith("HEAD"):
        return "HEAD", None, 0, 0, "detached HEAD"

    status_text = ""
    if " [" in body and body.endswith("]"):
        body, status_text = body[:-1].split(" [", 1)

    branch = body
    upstream = None
    if "..." in body:
        branch, upstream = body.split("...", 1)

    ahead = _extract_count(status_text, "ahead")
    behind = _extract_count(status_text, "behind")
    if upstream is None:
        message = "missing upstream"
    return branch, upstream, ahead, behind, message


def _extract_count(text: str, label: str) -> int:
    match = re.search(rf"{label} (\d+)", text)
    return int(match.group(1)) if match else 0


def _git_output(repo: Path, *args: str) -> str:
    result = subprocess.run(["git", "-C", str(repo), *args], check=True, text=True, capture_output=True)
    return result.stdout.strip()
