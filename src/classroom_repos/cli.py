from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated

import typer

from .config import DEFAULT_CONFIG, load_config
from .core import check_repositories, discover_repositories, update_repositories
from .pair_sync import check_pairs, confirm_backward, create_pair, update_pairs
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
