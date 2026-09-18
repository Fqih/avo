"""`avo plugin` — install, list, show, remove third-party plugins.

A plugin is a Python package that registers entry points under the
``avo.tools``, ``avo.providers``, or ``avo.notifiers`` groups. The
``install`` command clones a git URL (or copies a local path) into
``~/.avo/plugins/<name>/`` and ``pip install -e``'s it into the active
Python so entry-point discovery picks it up immediately.

This mirrors Claude Code's ``/plugin install <name-or-url>`` and Codex's
``codex plugin install <pkg>`` — same shape, simpler transport (Python
packaging instead of a marketplace daemon).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from avo.exceptions import AvoError
from avo.plugin_policy import inspect_plugin_source

PLUGIN_ROOT = Path.home() / ".avo" / "plugins"
PLUGIN_INDEX = PLUGIN_ROOT / "index.json"


class PluginCliError(AvoError):
    """User-facing failure in `avo plugin`."""


@dataclass(frozen=True)
class InstalledPlugin:
    """One entry from the on-disk plugin index."""

    name: str
    source: str  # git URL or local path
    path: Path  # resolved on-disk path
    editable: bool
    groups: tuple[str, ...] = ()
    version: str = "unknown"

    @property
    def description(self) -> str:
        return _description_for(self.path)


def _read_index() -> dict[str, dict[str, object]]:
    if not PLUGIN_INDEX.exists():
        return {}
    try:
        loaded = json.loads(PLUGIN_INDEX.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise PluginCliError(f"corrupt plugin index at {PLUGIN_INDEX}: {exc}") from exc
    if not isinstance(loaded, dict):
        raise PluginCliError(f"plugin index at {PLUGIN_INDEX} must be a JSON object.")
    return loaded


def _write_index(index: dict[str, dict[str, object]]) -> None:
    PLUGIN_ROOT.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(index, indent=2, sort_keys=True) + "\n"
    fd, temporary = tempfile.mkstemp(prefix="index.", suffix=".json", dir=PLUGIN_ROOT)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, PLUGIN_INDEX)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _validate_name(name: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", name):
        raise PluginCliError(f"invalid plugin name {name!r}; use letters, digits, '.', '-', '_'.")
    return name


def _description_for(path: Path) -> str:
    pyproject = path / "pyproject.toml"
    if not pyproject.exists():
        return ""
    try:
        import tomllib
    except ImportError:  # pragma: no cover — Python < 3.11
        return ""
    data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    project = data.get("project", {})
    description = project.get("description", "")
    return description if isinstance(description, str) else ""


def _pip_install(target: Path, *, editable: bool) -> None:
    cmd: list[str] = [sys.executable, "-m", "pip", "install"]
    if editable:
        cmd.append("-e")
    cmd.append(str(target))
    try:
        subprocess.run(cmd, check=True, capture_output=True, text=True)
    except subprocess.CalledProcessError as exc:
        raise PluginCliError(
            f"`{' '.join(cmd)}` failed (exit {exc.returncode}):\n{exc.stderr.strip()}"
        ) from exc


def install(
    source: str,
    *,
    name: str | None = None,
    editable: bool = False,
    confirm: bool = False,
) -> InstalledPlugin:
    """Install a plugin from a git URL or local path.

    Git URLs are shallow-cloned into ``~/.avo/plugins/<name>``. Local
    paths are symlinked (or copied when symlinks are not supported).
    Once on disk, the plugin is ``pip install``'d so the entry-point
    metadata is immediately discoverable.
    """

    manifest = inspect_plugin_source(source, name=name)
    if not confirm:
        raise PluginCliError(
            f"plugin preview: {manifest.name} v{manifest.version}; "
            f"groups={','.join(manifest.groups) or '(none)'}; "
            "installation requires explicit operator confirmation"
        )
    if source.startswith(("git@", "git+", "https://", "http://", "ssh://")) or source.endswith(
        ".git"
    ):
        if name is None:
            slug = source.rstrip("/").rsplit("/", 1)[-1]
            if slug.endswith(".git"):
                slug = slug[:-4]
            name = _validate_name(slug)
        else:
            name = _validate_name(name)
        destination = PLUGIN_ROOT / name
        if destination.exists():
            raise PluginCliError(
                f"plugin {name!r} already installed at {destination}; "
                f"`avo plugin remove {name}` first."
            )
        PLUGIN_ROOT.mkdir(parents=True, exist_ok=True)
        clone_cmd = ["git", "clone", "--depth", "1", source, str(destination)]
        try:
            subprocess.run(clone_cmd, check=True, capture_output=True, text=True)
        except FileNotFoundError as exc:
            raise PluginCliError("`git` binary not found on PATH; cannot clone plugins.") from exc
        except subprocess.CalledProcessError as exc:
            raise PluginCliError(
                f"`{' '.join(clone_cmd)}` failed: {exc.stderr.strip() or exc.stdout.strip()}"
            ) from exc
        kind = "git"
    else:
        src = Path(source).expanduser().resolve()
        if not src.exists():
            raise PluginCliError(f"plugin source path does not exist: {src}")
        name = _validate_name(src.name) if name is None else _validate_name(name)
        destination = PLUGIN_ROOT / name
        if destination.exists():
            raise PluginCliError(
                f"plugin {name!r} already installed at {destination}; "
                f"`avo plugin remove {name}` first."
            )
        PLUGIN_ROOT.mkdir(parents=True, exist_ok=True)
        try:
            destination.symlink_to(src, target_is_directory=True)
        except OSError:
            shutil.copytree(src, destination)
        kind = "local"

    _pip_install(destination, editable=editable)

    index = _read_index()
    index[name] = {
        "source": source,
        "path": str(destination),
        "editable": editable,
        "kind": kind,
        "version": manifest.version,
        "groups": list(manifest.groups),
        "active": False,
    }
    _write_index(index)
    return _entry_to_plugin(name, index[name])


def remove(name: str, *, uninstall: bool = True) -> None:
    """Remove a plugin by name, optionally uninstalling it from pip."""

    name = _validate_name(name)
    index = _read_index()
    if name not in index:
        raise PluginCliError(f"plugin {name!r} is not installed.")
    record = index[name]
    target = Path(str(record.get("path", "")))
    if uninstall:
        subprocess.run(
            [sys.executable, "-m", "pip", "uninstall", "-y", name],
            check=False,
            capture_output=True,
            text=True,
        )
    if target.is_symlink():
        target.unlink()
    elif target.exists() and target.is_dir():
        shutil.rmtree(target)
    index.pop(name, None)
    _write_index(index)


def list_installed() -> tuple[InstalledPlugin, ...]:
    """Return all installed plugins (sorted by name)."""

    return tuple(_entry_to_plugin(name, entry) for name, entry in sorted(_read_index().items()))


def show(name: str) -> InstalledPlugin:
    """Return one plugin by name."""

    name = _validate_name(name)
    index = _read_index()
    if name not in index:
        raise PluginCliError(f"plugin {name!r} is not installed.")
    return _entry_to_plugin(name, index[name])


def _entry_to_plugin(name: str, entry: dict[str, object]) -> InstalledPlugin:
    raw_path = entry.get("path")
    if not isinstance(raw_path, str):
        raise PluginCliError(f"plugin {name!r} index entry has no path.")
    raw_source = entry.get("source")
    source = raw_source if isinstance(raw_source, str) else "<unknown>"
    editable = bool(entry.get("editable"))
    raw_groups = entry.get("groups", ())
    groups = (
        tuple(item for item in raw_groups if isinstance(item, str))
        if isinstance(raw_groups, list)
        else ()
    )
    version = entry.get("version")
    return InstalledPlugin(
        name=name,
        source=source,
        path=Path(raw_path),
        editable=editable,
        groups=groups,
        version=version if isinstance(version, str) else "unknown",
    )


# ---------------------------------------------------------------------------
# Plugin scaffold (init)
# ---------------------------------------------------------------------------


PLUGIN_TEMPLATE = '''"""Plugin entrypoint for {package}."""

from __future__ import annotations

from pydantic import BaseModel, Field

from avo.tools import FunctionTool


class EchoArgs(BaseModel):
    """Echo the user-supplied message back to the agent."""

    message: str = Field(..., description="The message to echo back.")


async def echo(arguments: EchoArgs) -> dict[str, str]:
    """A trivial sample tool — replace with your own."""

    return {{"echoed": arguments.message}}


def register() -> tuple[FunctionTool[EchoArgs, dict[str, str]], ...]:
    """Return the tools this plugin exposes.

    The avo runtime calls ``register()`` on entry-point discovery; every
    tool returned here becomes available to the agent alongside the
    built-ins. The package's ``pyproject.toml`` declares this function
    as the ``avo.tools`` entry point.
    """

    tool = FunctionTool(
        name="plugin_{slug}_echo",
        description="Echo the supplied message (sample tool from the {package} plugin).",
        arguments_model=EchoArgs,
        function=echo,
    )
    return (tool,)


__all__ = ["register"]
'''


PLUGIN_PYPROJECT_TEMPLATE = """[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[project]
name = "{package}"
version = "0.1.0"
description = "Avo plugin: {description}"
readme = "README.md"
requires-python = ">=3.11"
license = {{ text = "MIT" }}
authors = [{{ name = "{author}" }}]
dependencies = [
    "avo",
    "pydantic>=2",
]

[project.entry-points."avo.tools"]
{package} = "{module}:register"

[tool.hatch.build.targets.wheel]
packages = ["{module_dir}"]
"""


PLUGIN_README_TEMPLATE = """# {package}

Scaffolded by `avo plugin init`. Fill in the description and ship it.

## Quick start

```bash
# from this directory
avo plugin install .

# exercise the bundled sample tool
avo chat
# then in the REPL:
#   use plugin_{slug}_echo with message="hello"
```

## Entry points

| group       | object                          |
|-------------|---------------------------------|
| avo.tools   | `{package} = {module}:register` |

Add new `FunctionTool` instances inside `register()` and they will be
discovered on the next `avo chat` start (or after `avo plugin install`).
"""


PLUGIN_GITIGNORE = """__pycache__/
*.egg-info/
.pytest_cache/
.venv/
build/
dist/
"""


def init_scaffold(
    name: str | None = None,
    *,
    directory: Path | str = Path("."),
    force: bool = False,
    author: str = "Avo Plugin Author",
    description: str = "a fresh avo plugin",
) -> Path:
    """Scaffold a new avo plugin directory.

    Creates ``<directory>/<name>/`` with a ``pyproject.toml`` that
    registers an ``avo.tools`` entry point, a ``register()`` stub that
    ships a sample ``echo`` tool, a ``README.md``, and a ``.gitignore``.

    Returns the resolved plugin directory path.
    """

    parent = Path(directory).expanduser().resolve()
    pkg_name = _validate_name(name) if name else _validate_name(parent.name)
    module_name = pkg_name.replace("-", "_")
    plugin_dir = parent / pkg_name
    if plugin_dir.exists() and not force:
        raise PluginCliError(
            f"plugin directory {plugin_dir} already exists; pass --force to overwrite."
        )
    plugin_dir.mkdir(parents=True, exist_ok=True)

    module_dir = plugin_dir / module_name
    module_dir.mkdir(parents=True, exist_ok=True)
    (module_dir / "__init__.py").write_text(
        PLUGIN_TEMPLATE.format(package=pkg_name, slug=module_name, module=module_name),
        encoding="utf-8",
    )

    (plugin_dir / "pyproject.toml").write_text(
        PLUGIN_PYPROJECT_TEMPLATE.format(
            package=pkg_name,
            description=description,
            author=author,
            module=module_name,
            module_dir=module_name,
        ),
        encoding="utf-8",
    )

    (plugin_dir / "README.md").write_text(
        PLUGIN_README_TEMPLATE.format(package=pkg_name, slug=module_name, module=module_name),
        encoding="utf-8",
    )

    (plugin_dir / ".gitignore").write_text(PLUGIN_GITIGNORE, encoding="utf-8")
    return plugin_dir


# ---------------------------------------------------------------------------
# CLI wiring
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="avo plugin",
        description="Install, list, and remove avo plugins.",
    )
    sub = parser.add_subparsers(dest="plugin_command", required=True)

    install_p = sub.add_parser("install", help="Install a plugin from a git URL or local path.")
    install_p.add_argument("source", help="git URL or local path")
    install_p.add_argument(
        "--name",
        default=None,
        help="Override the plugin name (default: derived from source).",
    )
    install_p.add_argument(
        "--no-editable",
        action="store_true",
        help="Deprecated alias; installs are non-editable by default.",
    )
    install_p.add_argument(
        "--editable",
        action="store_true",
        help="Request editable installation (still requires explicit confirmation).",
    )
    install_p.add_argument(
        "--confirm",
        action="store_true",
        help="Confirm the exact previewed install action without a prompt.",
    )

    sub.add_parser("list", help="List installed plugins.")

    show_p = sub.add_parser("show", help="Show one installed plugin.")
    show_p.add_argument("name")

    remove_p = sub.add_parser("remove", help="Remove an installed plugin.")
    remove_p.add_argument("name")
    remove_p.add_argument(
        "--keep-install",
        action="store_true",
        help="Do not pip uninstall — only remove the on-disk copy.",
    )
    remove_p.add_argument(
        "--yes",
        "-y",
        action="store_true",
        help="Skip the interactive confirmation prompt.",
    )

    init_p = sub.add_parser(
        "init",
        help="Scaffold a new avo plugin directory (see `avo plugin init --help`).",
    )
    init_p.add_argument(
        "name",
        nargs="?",
        default=None,
        help="Plugin package name (default: derived from the current directory name).",
    )
    init_p.add_argument(
        "--directory",
        "-d",
        type=Path,
        default=Path("."),
        help="Parent directory to write the plugin into (default: current directory).",
    )
    init_p.add_argument(
        "--force",
        action="store_true",
        help="Overwrite an existing plugin directory with the same name.",
    )

    return parser


def _confirm(prompt: str, *, assume_yes: bool) -> bool:
    """Print ``prompt`` and read a y/N answer from stdin."""

    if assume_yes:
        return True
    print(prompt, end="", flush=True)
    try:
        answer = input().strip().lower()
    except EOFError:
        print()
        return False
    return answer in ("y", "yes")


def _confirm_exact(action: str, *, assume_yes: bool) -> bool:
    """Require the operator to type the previewed action exactly."""

    expected = f"CONFIRM {action}"
    if assume_yes:
        return True
    try:
        answer = input(f"Type '{expected}' to continue: ")
    except EOFError:
        print()
        return False
    return answer.strip() == expected


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if args.plugin_command == "install":
        manifest = inspect_plugin_source(args.source, name=args.name)
        action = f"install {manifest.name}"
        print(
            f"Plugin preview: {manifest.name} v{manifest.version} — "
            f"groups={','.join(manifest.groups) or '(none)'}"
        )
        if not _confirm_exact(action, assume_yes=args.confirm):
            print("Aborted.")
            return 1
        plugin = install(
            args.source,
            name=args.name,
            editable=args.editable and not args.no_editable,
            confirm=True,
        )
        print(f"Installed plugin {plugin.name!r} from {plugin.source} → {plugin.path}")
        if plugin.description:
            print(f"  {plugin.description}")
        return 0

    if args.plugin_command == "list":
        plugins = list_installed()
        if not plugins:
            print("No plugins installed. Try: avo plugin install <git-url>")
            return 0
        print(f"{'NAME':24}  {'SOURCE':48}  PATH")
        for plugin in plugins:
            source = plugin.source if len(plugin.source) <= 48 else plugin.source[:45] + "..."
            print(f"{plugin.name:24}  {source:48}  {plugin.path}")
        return 0

    if args.plugin_command == "show":
        plugin = show(args.name)
        print(f"name        : {plugin.name}")
        print(f"source      : {plugin.source}")
        print(f"path        : {plugin.path}")
        print(f"editable    : {plugin.editable}")
        if plugin.description:
            print(f"description : {plugin.description}")
        return 0

    if args.plugin_command == "remove":
        if not _confirm(
            f"Remove plugin {args.name!r}? This deletes the on-disk copy and uninstalls it. [y/N] ",
            assume_yes=args.yes,
        ):
            print("Aborted.")
            return 1
        remove(args.name, uninstall=not args.keep_install)
        print(f"Removed plugin {args.name!r}.")
        return 0

    if args.plugin_command == "init":
        path = init_scaffold(
            args.name,
            directory=args.directory,
            force=args.force,
        )
        print(f"Scaffolded plugin at {path}")
        print("Next: `avo plugin install .` from inside the new directory.")
        return 0

    raise PluginCliError(f"unknown plugin subcommand: {args.plugin_command}")


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "PLUGIN_INDEX",
    "PLUGIN_ROOT",
    "InstalledPlugin",
    "PluginCliError",
    "init_scaffold",
    "install",
    "list_installed",
    "main",
    "remove",
    "show",
]
