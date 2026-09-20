"""Autonomous pull request generator and publisher for Avo CLI (`avo pr` / `/pr`)."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
from collections.abc import Mapping
from pathlib import Path
from typing import TextIO

from avo.config import build_provider_from_env
from avo.models import ModelRequest
from avo.providers.base import ModelProvider


def _get_current_branch(repo_root: Path) -> str:
    res = subprocess.run(
        ["git", "symbolic-ref", "--short", "HEAD"],
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=False,
    )
    return res.stdout.strip() or "HEAD"


def _get_diff(repo_root: Path, base_branch: str) -> str:
    # Try diff against base_branch...HEAD
    res = subprocess.run(
        ["git", "diff", f"{base_branch}...HEAD"],
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=False,
    )
    diff = res.stdout.strip()
    if not diff:
        # Fallback to uncommitted diff or HEAD~1
        res = subprocess.run(
            ["git", "diff", "HEAD~1"],
            cwd=repo_root,
            capture_output=True,
            text=True,
            check=False,
        )
        diff = res.stdout.strip()
    if not diff:
        res = subprocess.run(
            ["git", "diff", "HEAD"],
            cwd=repo_root,
            capture_output=True,
            text=True,
            check=False,
        )
        diff = res.stdout.strip()
    return diff


async def run_cli_pr(
    workspace_root: Path,
    base_branch: str = "main",
    *,
    create: bool = False,
    provider: ModelProvider | None = None,
    environ: Mapping[str, str] | None = None,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
) -> str | None:
    """Generate a GitHub Pull Request description from working tree/branch diff."""
    out = stdout or sys.stdout
    err = stderr or sys.stderr
    env = dict(os.environ if environ is None else environ)
    base_root = workspace_root.resolve()  # noqa: ASYNC240

    current_branch = _get_current_branch(base_root)
    diff = _get_diff(base_root, base_branch)

    if not diff:
        out.write(f"No diff found between {base_branch} and {current_branch}. Nothing to PR.\n")
        out.flush()
        return None

    out.write(
        f"📝 Analyzing diff between {base_branch} and {current_branch} "
        f"({len(diff.splitlines())} lines)...\n"
    )
    out.flush()

    diff_preview = diff
    if len(diff_preview) > 10_000:
        diff_preview = diff_preview[:9_800] + "\n... [diff truncated]"

    prompt = (
        "You are an expert staff software engineer creating a production GitHub PR.\n"
        "Analyze the following git diff and output a clean, professional Pull Request:\n\n"
        "Follow this exact format:\n"
        "Title: <imperative type(scope): description under 72 chars>\n\n"
        "## Summary\n"
        "<1-2 clear paragraphs explaining context, WHY change was made, and WHAT it does>\n\n"
        "## Key Changes\n"
        "- <bullet points detailing technical changes>\n\n"
        "## Test Plan\n"
        "- <bullet points detailing how changes were verified>\n\n"
        f"Git Diff:\n```diff\n{diff_preview}\n```"
    )

    resolved_provider = provider if provider is not None else build_provider_from_env(env)
    req = ModelRequest(
        run_id=f"pr-{int(time.time())}",
        step=1,
        messages=[{"role": "user", "content": prompt}],
    )

    resp = await resolved_provider.generate(req)
    pr_text = (resp.content or "").strip()

    title = ""
    for line in pr_text.splitlines():
        if line.lower().startswith("title:"):
            title = line.partition(":")[2].strip()
            break

    out.write("\n╭─ Pull Request Draft ───────────────────────────────────────────────╮\n")
    for line in pr_text.splitlines():
        out.write(f"│ {line}\n")
    out.write("╰────────────────────────────────────────────────────────────────────╯\n\n")
    out.flush()

    if create:
        gh_bin = shutil.which("gh")
        if not gh_bin:
            err.write(
                "Error: `gh` (GitHub CLI) is not installed. Install `gh` or copy the draft above.\n"
            )
            err.flush()
            return pr_text

        out.write(f"🚀 Creating GitHub Pull Request against base: {base_branch}...\n")
        out.flush()

        body_text = pr_text
        if title:
            # Strip title line from body
            body_text = "\n".join(
                line for line in pr_text.splitlines() if not line.lower().startswith("title:")
            ).strip()

        proc = subprocess.run(  # noqa: ASYNC221
            [
                gh_bin,
                "pr",
                "create",
                "--base",
                base_branch,
                "--title",
                title or "Update",
                "--body",
                body_text,
            ],
            cwd=base_root,
            capture_output=True,
            text=True,
            check=False,
        )
        if proc.returncode == 0:
            out.write(f"✓ Pull Request created successfully!\n{proc.stdout.strip()}\n")
        else:
            err.write(f"Failed to create PR via `gh`: {proc.stderr.strip()}\n")
        out.flush()

    return pr_text
