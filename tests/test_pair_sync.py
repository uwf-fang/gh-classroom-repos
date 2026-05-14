from __future__ import annotations

import json
import subprocess
from pathlib import Path

from classroom_repos.config import Config, PairSyncConfig
from classroom_repos.pair_sync import check_pair, create_pair, discover_pairs, update_pair


def test_discover_pairs_from_solution_suffix(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    provided = init_repo(tmp_path / "project")
    solution = init_repo(tmp_path / "project-solution")

    pairs = discover_pairs(config)

    assert [(pair.name, pair.provided, pair.solution) for pair in pairs] == [
        ("project", provided.resolve(), solution.resolve())
    ]


def test_forward_update_copies_configured_files_and_writes_marker(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    provided, solution = make_pair(tmp_path)
    write(solution / "README.md", "solution readme\n")
    write(solution / "Makefile", "main:\n")
    write(solution / "test/test.cpp", "assert(true);\n")
    write(solution / "main.cpp", "secret solution\n")
    write(provided / "README.md", "old readme\n")
    commit_all(solution)
    commit_all(provided)

    result = update_pair(config, discover_pairs(config)[0], apply=True)

    assert result.marker_updated
    assert (provided / "README.md").read_text(encoding="utf-8") == "solution readme\n"
    assert not (provided / "main.cpp").exists()
    marker = json.loads((provided / ".classroom-repos-sync.json").read_text(encoding="utf-8"))
    assert marker["solution_repo"] == "project-solution"
    assert sorted(marker["files"]) == ["Makefile", "README.md", "test/test.cpp"]


def test_pair_check_reports_solution_change_after_marker(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    provided, solution = make_synced_pair(tmp_path, config)
    write(solution / "README.md", "new solution readme\n")
    commit_all(solution)

    result = check_pair(config, discover_pairs(config)[0])

    assert result.status == "needs_forward_sync"
    assert result.issues[0].path == "README.md"


def test_pair_check_reports_provided_newer_after_marker(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    provided, _solution = make_synced_pair(tmp_path, config)
    write(provided / "README.md", "provided edit\n")
    commit_all(provided)

    result = check_pair(config, discover_pairs(config)[0])

    assert result.status == "provided_newer"


def test_pair_check_reports_conflict_when_both_changed_after_marker(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    provided, solution = make_synced_pair(tmp_path, config)
    write(provided / "README.md", "provided edit\n")
    write(solution / "README.md", "solution edit\n")
    commit_all(provided)
    commit_all(solution)

    result = check_pair(config, discover_pairs(config)[0])

    assert result.status == "conflict"


def test_forward_update_deletes_file_removed_from_solution(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    provided, solution = make_synced_pair(tmp_path, config)
    (solution / "test/test.cpp").unlink()
    commit_all(solution)

    result = update_pair(config, discover_pairs(config)[0], apply=True)

    assert any(action.status == "deleted" and action.path == "test/test.cpp" for action in result.actions)
    assert not (provided / "test/test.cpp").exists()


def test_missing_configured_path_in_solution_is_error(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    provided, solution = make_pair(tmp_path)
    write(solution / "README.md", "solution readme\n")
    write(solution / "Makefile", "main:\n")
    commit_all(solution)
    commit_all(provided)

    result = update_pair(config, discover_pairs(config)[0], apply=True)

    assert any(action.status == "error" and action.path == "test/**" for action in result.actions)
    assert not (provided / ".classroom-repos-sync.json").exists()


def test_backward_update_copies_from_provided_to_solution(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    provided, solution = make_synced_pair(tmp_path, config)
    write(provided / "README.md", "provided fix\n")
    commit_all(provided)

    result = update_pair(config, discover_pairs(config)[0], apply=True, backward=True)

    assert any(action.status == "updated" and action.path == "README.md" for action in result.actions)
    assert (solution / "README.md").read_text(encoding="utf-8") == "provided fix\n"


def test_pair_create_creates_local_provided_repo_without_solution_only_files(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    solution = init_repo(tmp_path / "project-solution")
    write(solution / "README.md", "starter readme\n")
    write(solution / "Makefile", "main:\n")
    write(solution / "test/test.cpp", "assert(true);\n")
    write(solution / "main.cpp", "secret solution\n")
    commit_all(solution)

    result = create_pair(config, solution=Path("project-solution"), apply=True)

    provided = tmp_path / "project"
    assert result.marker_written
    assert (provided / "README.md").exists()
    assert not (provided / "main.cpp").exists()
    assert (provided / ".git").exists()
    assert (provided / ".classroom-repos-sync.json").exists()


def test_pair_create_refuses_existing_target(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    solution = init_repo(tmp_path / "project-solution")
    init_repo(tmp_path / "project")
    write(solution / "README.md", "starter readme\n")
    write(solution / "Makefile", "main:\n")
    write(solution / "test/test.cpp", "assert(true);\n")
    commit_all(solution)

    result = create_pair(config, solution=solution, apply=True)

    assert result.skipped_reason == "provided repository already exists"


def make_config(tmp_path: Path) -> Config:
    return Config(
        path=tmp_path / "classroom-repos.yml",
        repo_roots=(tmp_path,),
        template_root=tmp_path / "templates",
        managed_files=(".gitignore",),
        pair_sync=PairSyncConfig(paths=("README.md", "Makefile", "test/**")),
    )


def make_pair(tmp_path: Path) -> tuple[Path, Path]:
    provided = init_repo(tmp_path / "project")
    solution = init_repo(tmp_path / "project-solution")
    return provided, solution


def make_synced_pair(tmp_path: Path, config: Config) -> tuple[Path, Path]:
    provided, solution = make_pair(tmp_path)
    write(solution / "README.md", "solution readme\n")
    write(solution / "Makefile", "main:\n")
    write(solution / "test/test.cpp", "assert(true);\n")
    write(provided / "README.md", "old readme\n")
    commit_all(solution)
    commit_all(provided)
    update_pair(config, discover_pairs(config)[0], apply=True)
    commit_all(provided)
    return provided, solution


def init_repo(path: Path) -> Path:
    path.mkdir(parents=True)
    subprocess.run(["git", "init"], cwd=path, check=True, capture_output=True)
    return path


def commit_all(repo: Path) -> None:
    subprocess.run(["git", "add", "."], cwd=repo, check=True, capture_output=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=Test User",
            "-c",
            "user.email=test@example.com",
            "commit",
            "--allow-empty",
            "-m",
            "commit",
        ],
        cwd=repo,
        check=True,
        capture_output=True,
    )


def write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
