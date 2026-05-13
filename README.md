# classroom-repos

`classroom-repos` is a local-first CLI for checking and updating shared files
across cloned GitHub Classroom programming-project repositories.

Repository: <https://github.com/uwf-fang/gh-classroom-repos>

## Install

Install it as a command with `uv`:

```bash
uv tool install .
```

After installation, run it from the directory that contains the student
repository directories:

```bash
cd /path/to/cloned/classroom/repos
classroom-repos init
classroom-repos check
classroom-repos update
classroom-repos update --apply
```

## Install for development

```bash
uv run --extra dev pytest
uv run --extra dev ruff check .
uv run --extra dev ruff format --check .
uv run classroom-repos --help
```

To format code locally:

```bash
uv run --extra dev ruff format .
```

## Configuration

Create a commented starter `classroom-repos.yml` file:

```bash
classroom-repos init
```

The starter file looks like this:

```yaml
# classroom-repos configuration
# Put this file in the directory that contains all student repository directories.
# When repo_roots is omitted, classroom-repos scans the current working directory.

template_root: ./templates

managed_files:
  - .gitignore
  - .gitattributes
  - .github/workflows/classroom.yml

checked_files:
  - path: .github/classroom/autograding.json
    required_patterns:
      - '"tests"'
  - path: README.md
    required_patterns:
      - "## Grading Information:"
      - "## Submission Requirements:"
      - "## Project Requirements:"
  - path: Makefile
    required_patterns:
      - "^main:"
      - "^test-all:"
      - "^test-mem:"
  - path: test
    kind: directory
    required_globs:
      - "*.cpp"
```

By default, repositories are discovered under the current working directory.
Add `repo_roots` only if you want the config to point somewhere else:

```yaml
repo_roots:
  - /path/to/cloned/classroom/repos
```

Canonical files are read from a `templates/` directory beside the config file
unless `template_root` is set.

## Commands

```bash
classroom-repos list
classroom-repos init
classroom-repos check
classroom-repos check --json
classroom-repos update
classroom-repos update --apply
classroom-repos update --repo /path/to/repo --apply
```

`update` is a dry run unless `--apply` is supplied. Repositories with
uncommitted changes are skipped.
