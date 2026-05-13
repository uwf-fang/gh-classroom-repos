from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated

import typer

from .config import DEFAULT_CONFIG, load_config
from .core import check_repositories, discover_repositories, update_repositories
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
