"""`avo plugin` — install, list, show, remove third-party plugins.

A plugin is a Python package that registers entry points under the
``avo.tools``, ``avo.providers``, or ``avo.notifiers`` groups. The
``install`` command clones a git URL (or copies a local path) into
``~/.avo/plugins/<name>/`` and installs dependencies into a dedicated
virtual environment under ``~/.avo/plugins/.venvs/<name>/``. Avo's own
Python environment is never mutated by plugin installation.

This mirrors Claude Code's ``/plugin install <name-or-url>`` and Codex's
``codex plugin install <pkg>`` — same shape, simpler transport (Python
packaging instead of a marketplace daemon).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import venv
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
    environment: Path | None = None
    source_digest: str | None = None
    active: bool = False

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


def _plugin_environment_root() -> Path:
    return PLUGIN_ROOT / ".venvs"


def _plugin_python(environment: Path) -> Path:
    return environment / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def _plugin_site_packages(environment: Path) -> Path:
    """Return the private site-packages directory for a plugin environment."""

    if os.name == "nt":
        return environment / "Lib" / "site-packages"
    version = f"python{sys.version_info.major}.{sys.version_info.minor}"
    return environment / "lib" / version / "site-packages"


def _pip_install_isolated(target: Path, *, environment: Path, editable: bool) -> None:
    """Install a plugin into its own virtualenv, never into Avo's environment."""

    environment.parent.mkdir(parents=True, exist_ok=True)
    try:
        venv.EnvBuilder(with_pip=True, clear=True).create(environment)
    except (OSError, subprocess.SubprocessError) as exc:
        raise PluginCliError(f"could not create plugin environment {environment}: {exc}") from exc

    cmd: list[str] = [str(_plugin_python(environment)), "-m", "pip", "install"]
    cmd.extend(("--disable-pip-version-check", "--no-input"))
    if editable:
        cmd.append("-e")
    cmd.append(str(target))
    try:
        subprocess.run(cmd, check=True, capture_output=True, text=True)
    except subprocess.CalledProcessError as exc:
        raise PluginCliError(
            f"`{' '.join(cmd)}` failed (exit {exc.returncode}):\n{exc.stderr.strip()}"
        ) from exc


def _source_digest(root: Path) -> str:
    """Return a stable digest of source files used to build a plugin."""

    digest = hashlib.sha256()
    ignored = {".git", ".venv", ".venvs", "__pycache__", "build", "dist"}
    files = (
        path
        for path in root.rglob("*")
        if path.is_file() and not any(part in ignored for part in path.relative_to(root).parts)
    )
    for path in sorted(files, key=lambda item: item.relative_to(root).as_posix()):
        relative = path.relative_to(root).as_posix().encode("utf-8")
        digest.update(len(relative).to_bytes(8, "big"))
        digest.update(relative)
        digest.update(path.read_bytes())
    return f"sha256:{digest.hexdigest()}"


def install(
    source: str,
    *,
    name: str | None = None,
    editable: bool = False,
    confirm: bool = False,
    allow_editable: bool | None = None,
) -> InstalledPlugin:
    """Install a plugin from a git URL or local path.

    Git URLs are shallow-cloned into ``~/.avo/plugins/<name>``. Local
    paths are symlinked (or copied when symlinks are not supported).
    Once on disk, the plugin is installed into a private virtualenv. Editable
    installs are disabled unless explicitly enabled with
    ``AVO_PLUGIN_EDITABLE=1`` or ``allow_editable=True``.
    """

    manifest = inspect_plugin_source(source, name=name)
    if not confirm:
        raise PluginCliError(
            f"plugin preview: {manifest.name} v{manifest.version}; "
            f"groups={','.join(manifest.groups) or '(none)'}; "
            "installation requires explicit operator confirmation"
        )
    if editable and not (
        allow_editable
        if allow_editable is not None
        else os.environ.get("AVO_PLUGIN_EDITABLE", "").strip().lower() in {"1", "true", "yes"}
    ):
        raise PluginCliError(
            "editable plugin installs are disabled by policy; set AVO_PLUGIN_EDITABLE=1 "
            "only for a trusted development plugin"
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

    environment = _plugin_environment_root() / name
    try:
        _pip_install_isolated(destination, environment=environment, editable=editable)
    except Exception:
        if destination.is_symlink():
            destination.unlink()
        elif destination.exists() and destination.is_dir():
            shutil.rmtree(destination)
        if environment.exists():
            shutil.rmtree(environment)
        raise

    source_digest = _source_digest(destination.resolve())

    index = _read_index()
    index[name] = {
        "source": source,
        "path": str(destination),
        "editable": editable,
        "kind": kind,
        "version": manifest.version,
        "groups": list(manifest.groups),
        "environment": str(environment),
        "source_digest": source_digest,
        "active": False,
    }
    _write_index(index)
    return _entry_to_plugin(name, index[name])


def remove(name: str, *, uninstall: bool = True) -> None:
    """Remove a plugin and its private environment."""

    name = _validate_name(name)
    index = _read_index()
    if name not in index:
        raise PluginCliError(f"plugin {name!r} is not installed.")
    record = index[name]
    target = Path(str(record.get("path", "")))
    del uninstall  # Compatibility argument; active Avo dependencies are never uninstalled.
    raw_environment = record.get("environment")
    if isinstance(raw_environment, str):
        environment = Path(raw_environment).expanduser()
        environment_root = _plugin_environment_root().resolve()
        try:
            environment.resolve().relative_to(environment_root)
        except ValueError as exc:
            raise PluginCliError(
                f"refusing to remove plugin environment outside {environment_root}: {environment}"
            ) from exc
        if environment.exists():
            shutil.rmtree(environment)
    if target.is_symlink():
        target.unlink()
    elif target.exists() and target.is_dir():
        shutil.rmtree(target)
    index.pop(name, None)
    _write_index(index)


def list_installed() -> tuple[InstalledPlugin, ...]:
    """Return all installed plugins (sorted by name)."""

    return tuple(_entry_to_plugin(name, entry) for name, entry in sorted(_read_index().items()))


def active_plugin_site_packages() -> tuple[Path, ...]:
    """Return private import paths for explicitly activated plugins.

    Legacy plugin records are intentionally ignored: they must be reinstalled
    so Avo can guarantee that dependencies live in a private environment.
    """

    environment_root = _plugin_environment_root().resolve()
    paths: list[Path] = []
    for plugin in list_installed():
        if not plugin.active or plugin.environment is None:
            continue
        environment = plugin.environment.expanduser()
        try:
            environment.resolve().relative_to(environment_root)
        except ValueError:
            continue
        paths.append(_plugin_site_packages(environment))
    return tuple(paths)


def set_active(name: str, *, active: bool) -> InstalledPlugin:
    """Explicitly activate or deactivate one installed plugin."""

    name = _validate_name(name)
    index = _read_index()
    entry = index.get(name)
    if entry is None:
        raise PluginCliError(f"plugin {name!r} is not installed.")
    raw_environment = entry.get("environment")
    raw_digest = entry.get("source_digest")
    if not isinstance(raw_environment, str) or not isinstance(raw_digest, str):
        raise PluginCliError(
            f"plugin {name!r} uses a legacy install record; reinstall it before activation."
        )
    environment = Path(raw_environment).expanduser()
    environment_root = _plugin_environment_root().resolve()
    try:
        environment.resolve().relative_to(environment_root)
    except ValueError as exc:
        raise PluginCliError(
            f"refusing to activate plugin environment outside {environment_root}: {environment}"
        ) from exc
    if not environment.exists():
        raise PluginCliError(
            f"plugin environment is missing for {name!r}; reinstall the plugin before activation."
        )
    entry["active"] = bool(active)
    _write_index(index)
    return _entry_to_plugin(name, entry)


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
    raw_environment = entry.get("environment")
    environment = Path(raw_environment) if isinstance(raw_environment, str) else None
    source_digest = entry.get("source_digest")
    active = bool(entry.get("active"))
    return InstalledPlugin(
        name=name,
        source=source,
        path=Path(raw_path),
        editable=editable,
        groups=groups,
        version=version if isinstance(version, str) else "unknown",
        environment=environment,
        source_digest=source_digest if isinstance(source_digest, str) else None,
        active=active,
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
avo plugin activate {package}
AVO_PLUGIN_ACTIVATION=1 avo
# then in the REPL:
#   use plugin_{slug}_echo with message="hello"
```

## Entry points

| group       | object                          |
|-------------|---------------------------------|
| avo.tools   | `{package} = {module}:register` |

Add new `FunctionTool` instances inside `register()`. After installation,
explicitly enable the plugin with `avo plugin activate {package}` and opt into
loading with `AVO_PLUGIN_ACTIVATION=1`; Avo will then discover it in the next
chat process.
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

    activate_p = sub.add_parser("activate", help="Activate an installed plugin explicitly.")
    activate_p.add_argument("name")

    deactivate_p = sub.add_parser("deactivate", help="Deactivate an installed plugin.")
    deactivate_p.add_argument("name")

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
        print(f"  environment: {plugin.environment}")
        print(f"  source digest: {plugin.source_digest}")
        print(f"  activation: inactive (run `avo plugin activate {plugin.name}`)")
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
            state = "active" if plugin.active else "inactive"
            print(f"{plugin.name:24}  {state:8}  {source:48}  {plugin.path}")
        return 0

    if args.plugin_command == "show":
        plugin = show(args.name)
        print(f"name        : {plugin.name}")
        print(f"source      : {plugin.source}")
        print(f"path        : {plugin.path}")
        print(f"editable    : {plugin.editable}")
        print(f"active      : {plugin.active}")
        print(f"environment : {plugin.environment or '(legacy/unknown)'}")
        print(f"source hash : {plugin.source_digest or '(legacy/unknown)'}")
        if plugin.description:
            print(f"description : {plugin.description}")
        return 0

    if args.plugin_command in {"activate", "deactivate"}:
        active = args.plugin_command == "activate"
        plugin = set_active(args.name, active=active)
        state = "Activated" if active else "Deactivated"
        print(f"{state} plugin {plugin.name!r}.")
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
    "_pip_install_isolated",
    "active_plugin_site_packages",
    "init_scaffold",
    "install",
    "list_installed",
    "main",
    "remove",
    "set_active",
    "show",
]
