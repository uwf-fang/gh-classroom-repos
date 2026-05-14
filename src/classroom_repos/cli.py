from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated

import typer

from .config import DEFAULT_CONFIG, load_config
from .core import check_repositories, discover_repositories, update_repositories
from .ops import clean_repositories, commit_repositories, git_statuses, pair_summaries, run_command
from .pair_sync import check_pairs, confirm_backward, create_pair, init_pairs, update_pairs
from .starter import STARTER_CONFIG

app = typer.Typer(help="Manage shared files across local GitHub Classroom repositories.")


ConfigOption = Annotated[
    Path,
    typer.Option("--config", "-c", help="Path to classroom-repos.yml."),
]


@app.command("list")
def list_repos(config: ConfigOption = Path(DEFAULT_CONFIG)) -> None:
    """List discovered Git repositories."""
    cfg = _load_or_exit(config)
    for repo in discover_repositories(cfg.repo_roots):
        typer.echo(repo)


@app.command()
def init(
    output: Annotated[
        Path,
        typer.Option("--output", "-o", help="Configuration file to create."),
    ] = Path(DEFAULT_CONFIG),
    force: Annotated[bool, typer.Option("--force", help="Overwrite an existing configuration file.")] = False,
) -> None:
    """Create a commented starter configuration file in the current directory."""
    output = output.expanduser()
    if output.exists() and not force:
        typer.secho(f"Refusing to overwrite existing file: {output}", fg=typer.colors.RED, err=True)
        raise typer.Exit(1)

    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(STARTER_CONFIG, encoding="utf-8")
    typer.echo(f"Created {output}")
    typer.echo("Edit managed_files, checked_files, and template_root before running check or update.")


@app.command()
def check(
    config: ConfigOption = Path(DEFAULT_CONFIG),
    json_output: Annotated[bool, typer.Option("--json", help="Emit machine-readable JSON.")] = False,
) -> None:
    """Check shared-file integrity across discovered repositories."""
    cfg = _load_or_exit(config)
    results = check_repositories(cfg)

    if json_output:
        typer.echo(json.dumps(_check_payload(results), indent=2))
    else:
        _print_check_results(results)

    if any(not result.ok for result in results):
        raise typer.Exit(1)


@app.command()
def update(
    config: ConfigOption = Path(DEFAULT_CONFIG),
    repo: Annotated[Path | None, typer.Option("--repo", help="Only update one repository.")] = None,
    apply: Annotated[bool, typer.Option("--apply", help="Copy template files into clean repositories.")] = False,
) -> None:
    """Update exact shared files. Dry-run unless --apply is provided."""
    cfg = _load_or_exit(config)
    repos = [repo.expanduser().resolve()] if repo is not None else None
    results = update_repositories(cfg, repos=repos, apply=apply)
    _print_update_results(results, apply=apply)

    if any(result.skipped_reason for result in results):
        raise typer.Exit(1)
    if any(action.status == "error" for result in results for action in result.actions):
        raise typer.Exit(1)


@app.command("pair-check")
def pair_check(
    config: ConfigOption = Path(DEFAULT_CONFIG),
    pair: Annotated[str | None, typer.Option("--pair", help="Only check one provided repository pair.")] = None,
    json_output: Annotated[bool, typer.Option("--json", help="Emit machine-readable JSON.")] = False,
) -> None:
    """Check agreement between provided and solution repository pairs."""
    cfg = _load_or_exit(config)
    try:
        results = check_pairs(cfg, pair_name=pair)
    except Exception as exc:
        typer.secho(f"Pair check error: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(2) from exc

    if json_output:
        typer.echo(json.dumps(_pair_check_payload(results), indent=2))
    else:
        _print_pair_check_results(results)

    if any(not result.ok for result in results):
        raise typer.Exit(1)


@app.command("pair-update")
def pair_update(
    config: ConfigOption = Path(DEFAULT_CONFIG),
    pair: Annotated[str | None, typer.Option("--pair", help="Only update one provided repository pair.")] = None,
    apply: Annotated[bool, typer.Option("--apply", help="Apply pair synchronization changes.")] = False,
    backward: Annotated[
        bool,
        typer.Option("--backward", help="DANGEROUS: copy configured files from provided repo to solution repo."),
    ] = False,
) -> None:
    """Update provided repositories from paired solution repositories."""
    cfg = _load_or_exit(config)

    if backward and not apply:
        typer.secho("Backward update requires --apply.", fg=typer.colors.RED, err=True)
        raise typer.Exit(2)
    if backward and pair is None:
        typer.secho("Backward update requires --pair.", fg=typer.colors.RED, err=True)
        raise typer.Exit(2)
    if backward and not confirm_backward(pair):
        typer.secho("Backward update cancelled.", fg=typer.colors.RED, err=True)
        raise typer.Exit(1)

    try:
        results = update_pairs(cfg, pair_name=pair, apply=apply, backward=backward)
    except Exception as exc:
        typer.secho(f"Pair update error: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(2) from exc

    _print_pair_update_results(results, apply=apply)
    if any(result.skipped_reason for result in results):
        raise typer.Exit(1)
    if any(action.status in {"error", "conflict"} for result in results for action in result.actions):
        raise typer.Exit(1)


@app.command("pair-init")
def pair_init(
    config: ConfigOption = Path(DEFAULT_CONFIG),
    pair: Annotated[str | None, typer.Option("--pair", help="Only initialize one provided repository pair.")] = None,
    apply: Annotated[bool, typer.Option("--apply", help="Write sync marker files.")] = False,
    reset_marker: Annotated[
        bool,
        typer.Option("--reset-marker", help="Drop marker entries not matched by current pair_sync.paths."),
    ] = False,
) -> None:
    """Initialize pair sync markers without copying files."""
    cfg = _load_or_exit(config)
    try:
        results = init_pairs(cfg, pair_name=pair, apply=apply, reset_marker=reset_marker)
    except Exception as exc:
        typer.secho(f"Pair init error: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(2) from exc

    _print_pair_init_results(results, apply=apply)
    if any(result.skipped_reason for result in results):
        raise typer.Exit(1)
    if any(action.status == "error" for result in results for action in result.actions):
        raise typer.Exit(1)


@app.command("pair-create")
def pair_create(
    solution: Annotated[Path, typer.Option("--solution", help="Solution repository path or directory name.")],
    config: ConfigOption = Path(DEFAULT_CONFIG),
    apply: Annotated[bool, typer.Option("--apply", help="Create the provided repository.")] = False,
) -> None:
    """Create a local provided repository from a solution repository."""
    cfg = _load_or_exit(config)
    try:
        result = create_pair(cfg, solution=solution, apply=apply)
    except Exception as exc:
        typer.secho(f"Pair create error: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(2) from exc

    _print_pair_create_result(result, apply=apply)
    if result.skipped_reason or any(action.status == "error" for action in result.actions):
        raise typer.Exit(1)


@app.command(context_settings={"allow_extra_args": True, "ignore_unknown_options": True})
def run(
    ctx: typer.Context,
    config: ConfigOption = Path(DEFAULT_CONFIG),
    scope: Annotated[str, typer.Option("--scope", help="Repo scope: all, provided, solution, or pair.")] = "all",
    pair: Annotated[str | None, typer.Option("--pair", help="Pair name for pair/provided/solution scopes.")] = None,
    repo: Annotated[Path | None, typer.Option("--repo", help="Only run in one repository.")] = None,
    apply: Annotated[bool, typer.Option("--apply", help="Execute the command.")] = False,
    summary: Annotated[bool, typer.Option("--summary", help="Only show a per-repo exit-code table.")] = False,
) -> None:
    """Run a command across selected repositories. Dry-run unless --apply is provided."""
    cfg = _load_or_exit(config)
    try:
        results = run_command(cfg, list(ctx.args), scope=scope, pair_name=pair, repo=repo, apply=apply)
    except Exception as exc:
        typer.secho(f"Run error: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(2) from exc

    _print_run_results(results, apply=apply, summary=summary)
    if any(result.returncode not in {None, 0} for result in results):
        raise typer.Exit(1)


@app.command("git-status")
def git_status(
    config: ConfigOption = Path(DEFAULT_CONFIG),
    scope: Annotated[str, typer.Option("--scope", help="Repo scope: all, provided, solution, or pair.")] = "all",
    pair: Annotated[str | None, typer.Option("--pair", help="Pair name for pair/provided/solution scopes.")] = None,
    repo: Annotated[Path | None, typer.Option("--repo", help="Only inspect one repository.")] = None,
    json_output: Annotated[bool, typer.Option("--json", help="Emit machine-readable JSON.")] = False,
) -> None:
    """Summarize Git state across selected repositories."""
    cfg = _load_or_exit(config)
    try:
        results = git_statuses(cfg, scope=scope, pair_name=pair, repo=repo)
    except Exception as exc:
        typer.secho(f"Git status error: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(2) from exc

    if json_output:
        typer.echo(json.dumps(_git_status_payload(results), indent=2))
    else:
        _print_git_statuses(results)

    if any(not result.ok for result in results):
        raise typer.Exit(1)


@app.command("git-commit")
def git_commit(
    message: Annotated[str, typer.Option("--message", "-m", help="Commit message.")],
    config: ConfigOption = Path(DEFAULT_CONFIG),
    scope: Annotated[str, typer.Option("--scope", help="Repo scope: all, provided, solution, or pair.")] = "all",
    pair: Annotated[str | None, typer.Option("--pair", help="Pair name for pair/provided/solution scopes.")] = None,
    repo: Annotated[Path | None, typer.Option("--repo", help="Only commit one repository.")] = None,
) -> None:
    """Commit all changes in selected repositories."""
    cfg = _load_or_exit(config)
    try:
        results = commit_repositories(cfg, message=message, scope=scope, pair_name=pair, repo=repo)
    except Exception as exc:
        typer.secho(f"Git commit error: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(2) from exc

    _print_commit_results(results)
    if any(result.status == "error" for result in results):
        raise typer.Exit(1)


@app.command("pair-status")
def pair_status(
    config: ConfigOption = Path(DEFAULT_CONFIG),
    json_output: Annotated[bool, typer.Option("--json", help="Emit machine-readable JSON.")] = False,
) -> None:
    """Show compact pair agreement status."""
    cfg = _load_or_exit(config)
    try:
        results = pair_summaries(cfg)
    except Exception as exc:
        typer.secho(f"Pair status error: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(2) from exc

    if json_output:
        typer.echo(json.dumps(_pair_status_payload(results), indent=2))
    else:
        _print_pair_status(results)
    if any(not result.ok for result in results):
        raise typer.Exit(1)


@app.command()
def clean(
    config: ConfigOption = Path(DEFAULT_CONFIG),
    scope: Annotated[str, typer.Option("--scope", help="Repo scope: all, provided, solution, or pair.")] = "all",
    pair: Annotated[str | None, typer.Option("--pair", help="Pair name for pair/provided/solution scopes.")] = None,
    repo: Annotated[Path | None, typer.Option("--repo", help="Only clean one repository.")] = None,
    apply: Annotated[bool, typer.Option("--apply", help="Remove matching redundant files.")] = False,
) -> None:
    """Remove common OS and build artifacts. Dry-run unless --apply is provided."""
    cfg = _load_or_exit(config)
    try:
        results = clean_repositories(cfg, scope=scope, pair_name=pair, repo=repo, apply=apply)
    except Exception as exc:
        typer.secho(f"Clean error: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(2) from exc

    _print_clean_results(results, apply=apply)


def _load_or_exit(config: Path):
    try:
        return load_config(config, default_repo_root=Path.cwd())
    except Exception as exc:
        typer.secho(f"Config error: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(2) from exc


def _print_check_results(results) -> None:
    if not results:
        typer.echo("No repositories discovered.")
        return

    for result in results:
        status = "OK" if result.ok else "FAIL"
        dirty = " dirty" if result.dirty else ""
        repo_name = result.repo.name
        typer.echo(f"{status} {repo_name} ({result.repo}){dirty}")
        for issue in result.issues:
            typer.echo(f"  - {repo_name}/{issue.path}: {issue.code}: {issue.message}")


def _print_update_results(results, apply: bool) -> None:
    mode = "apply" if apply else "dry-run"
    typer.echo(f"Update mode: {mode}")
    if not results:
        typer.echo("No repositories discovered.")
        return

    for result in results:
        if result.skipped_reason:
            typer.echo(f"SKIP {result.repo}: {result.skipped_reason}")
            continue
        typer.echo(f"REPO {result.repo}")
        for action in result.actions:
            typer.echo(f"  - {action.path}: {action.status}: {action.message}")


def _check_payload(results) -> dict:
    return {
        "repositories": [
            {
                "name": result.repo.name,
                "path": str(result.repo),
                "dirty": result.dirty,
                "ok": result.ok,
                "issues": [
                    {
                        "path": issue.path,
                        "code": issue.code,
                        "message": issue.message,
                    }
                    for issue in result.issues
                ],
            }
            for result in results
        ]
    }


def _print_pair_check_results(results) -> None:
    if not results:
        typer.echo("No repository pairs discovered.")
        return

    for result in results:
        status = "OK" if result.ok else result.status.upper()
        typer.echo(f"{status} {result.pair.name}")
        typer.echo(f"  provided: {result.pair.provided}")
        typer.echo(f"  solution: {result.pair.solution}")
        if result.skipped_reason:
            typer.echo(f"  - skipped: {result.skipped_reason}")
        for issue in result.issues:
            typer.echo(f"  - {issue.path}: {issue.status}: {issue.message}")


def _print_pair_update_results(results, apply: bool) -> None:
    mode = "apply" if apply else "dry-run"
    typer.echo(f"Pair update mode: {mode}")
    for result in results:
        typer.echo(f"PAIR {result.pair.name} ({result.direction})")
        if result.skipped_reason:
            typer.echo(f"  - skipped: {result.skipped_reason}")
            continue
        for action in result.actions:
            typer.echo(f"  - {action.path}: {action.status}: {action.message}")
        if result.marker_updated:
            typer.echo("  - marker: updated")


def _print_pair_create_result(result, apply: bool) -> None:
    mode = "apply" if apply else "dry-run"
    typer.echo(f"Pair create mode: {mode}")
    typer.echo(f"PAIR {result.pair.name}")
    typer.echo(f"  provided: {result.pair.provided}")
    typer.echo(f"  solution: {result.pair.solution}")
    if result.skipped_reason:
        typer.echo(f"  - skipped: {result.skipped_reason}")
    for action in result.actions:
        typer.echo(f"  - {action.path}: {action.status}: {action.message}")
    if result.marker_written:
        typer.echo("  - marker: written")


def _print_pair_init_results(results, apply: bool) -> None:
    mode = "apply" if apply else "dry-run"
    typer.echo(f"Pair init mode: {mode}")
    for result in results:
        typer.echo(f"PAIR {result.pair.name}")
        if result.skipped_reason:
            typer.echo(f"  - skipped: {result.skipped_reason}")
        for action in result.actions:
            typer.echo(f"  - {action.path}: {action.status}: {action.message}")
        if result.marker_written:
            typer.echo("  - marker: written")


def _pair_check_payload(results) -> dict:
    return {
        "pairs": [
            {
                "name": result.pair.name,
                "provided": str(result.pair.provided),
                "solution": str(result.pair.solution),
                "status": result.status,
                "dirty": result.dirty,
                "skipped_reason": result.skipped_reason,
                "issues": [
                    {
                        "path": issue.path,
                        "status": issue.status,
                        "message": issue.message,
                    }
                    for issue in result.issues
                ],
            }
            for result in results
        ]
    }


def _print_run_results(results, apply: bool, summary: bool = False) -> None:
    mode = "apply" if apply else "dry-run"
    typer.echo(f"Run mode: {mode}")
    if summary:
        typer.echo(f"{'repo':40} {'status':12} {'exit':>4} command")
        for result in results:
            command = " ".join(result.command)
            exit_code = "-" if result.returncode is None else str(result.returncode)
            typer.echo(f"{result.repo.name:40} {result.status:12} {exit_code:>4} {command}")
        return

    for result in results:
        command = " ".join(result.command)
        if result.status == "would_run":
            typer.echo(f"WOULD RUN {result.repo.name}: {command}")
            continue
        typer.echo(f"RUN {result.repo.name}: exit {result.returncode}: {command}")
        if result.stdout:
            typer.echo(result.stdout.rstrip())
        if result.stderr:
            typer.echo(result.stderr.rstrip())


def _print_git_statuses(results) -> None:
    if not results:
        typer.echo("No repositories discovered.")
        return
    for result in results:
        if not result.valid:
            typer.echo(f"invalid {result.repo.name}: {result.message}")
            continue
        state = "clean" if result.ok else "needs_attention"
        counts = f"staged {result.staged}, modified {result.modified}, untracked {result.untracked}"
        remote = f"ahead {result.ahead}, behind {result.behind}" if result.upstream else result.message
        typer.echo(f"{state:15} {result.repo.name:40} {result.branch or '-'}  {counts}  {remote}")


def _print_commit_results(results) -> None:
    for result in results:
        detail = f" {result.commit_hash}" if result.commit_hash else ""
        typer.echo(f"{result.status:10} {result.repo.name}{detail}: {result.message}")


def _print_pair_status(results) -> None:
    if not results:
        typer.echo("No repository pairs discovered.")
        return
    for result in results:
        detail = result.skipped_reason or f"{result.issue_count} files"
        typer.echo(f"{result.status:20} {result.name:40} {detail}")


def _git_status_payload(results) -> dict:
    return {
        "repositories": [
            {
                "name": result.repo.name,
                "path": str(result.repo),
                "valid": result.valid,
                "branch": result.branch,
                "upstream": result.upstream,
                "dirty": result.dirty,
                "staged": result.staged,
                "modified": result.modified,
                "untracked": result.untracked,
                "ahead": result.ahead,
                "behind": result.behind,
                "message": result.message,
                "ok": result.ok,
            }
            for result in results
        ]
    }


def _pair_status_payload(results) -> dict:
    return {
        "pairs": [
            {
                "name": result.name,
                "status": result.status,
                "issue_count": result.issue_count,
                "skipped_reason": result.skipped_reason,
                "ok": result.ok,
            }
            for result in results
        ]
    }


def _print_clean_results(results, apply: bool) -> None:
    mode = "apply" if apply else "dry-run"
    typer.echo(f"Clean mode: {mode}")
    for result in results:
        if result.status == "clean":
            typer.echo(f"CLEAN {result.repo.name}: {result.message}")
            continue
        typer.echo(f"{result.status.upper()} {result.repo.name}/{result.path}: {result.message}")
