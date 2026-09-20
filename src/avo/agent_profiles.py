"""Named agent profiles and safe ``@mention`` parsing for chat."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Final

from avo.exceptions import AvoError

_SLUG_RE: Final[re.Pattern[str]] = re.compile(r"^[a-z][a-z0-9-]{0,31}$")
_MENTION_RE: Final[re.Pattern[str]] = re.compile(
    r"^\s*@(?P<name>[a-z][a-z0-9-]{0,31})(?P<rest>\s+.*|\s*)$",
    re.DOTALL,
)
_DEFAULT_MAX_PROFILE_BYTES: Final[int] = 32 * 1024


class AgentProfileError(AvoError):
    """Raised when an explicit agent delegation request is malformed."""


class AgentCapability(StrEnum):
    """Tool capability assigned to an agent profile."""

    READ_ONLY = "read_only"
    INHERITED = "inherited"


@dataclass(frozen=True)
class AgentProfile:
    """Immutable model-facing profile definition."""

    name: str
    description: str
    system_prompt: str
    capability: str
    builtin: bool = False


@dataclass(frozen=True)
class AgentMention:
    """One parsed agent name and its task text."""

    agent: AgentProfile
    prompt: str


@dataclass(frozen=True)
class DelegationRequest:
    """One or more agent mentions from a chat prompt."""

    parts: tuple[AgentMention, ...]
    is_pipeline: bool = False

    @property
    def is_parallel(self) -> bool:
        """Whether the prompt contains more than one delegated child."""

        return len(self.parts) > 1 and not self.is_pipeline


def _builtin_profiles() -> tuple[AgentProfile, ...]:
    return (
        AgentProfile(
            name="coder",
            description="Implement changes in the workspace.",
            system_prompt=(
                "You are Avo's coding agent. Inspect the workspace, make the requested "
                "change carefully, and report the files and verification you used."
            ),
            capability=AgentCapability.INHERITED,
            builtin=True,
        ),
        AgentProfile(
            name="explore",
            description="Inspect and summarize the workspace without changing it.",
            system_prompt=(
                "You are Avo's exploration agent. Read and search the workspace to answer "
                "the task. Do not modify files, run mutating commands, or commit changes."
            ),
            capability=AgentCapability.READ_ONLY,
            builtin=True,
        ),
        AgentProfile(
            name="reviewer",
            description="Review code, tests, and diffs without changing the workspace.",
            system_prompt=(
                "You are Avo's review agent. Examine the requested code or diff, identify "
                "specific correctness and test risks, and suggest precise fixes. Do not "
                "modify files, run mutating commands, or commit changes."
            ),
            capability=AgentCapability.READ_ONLY,
            builtin=True,
        ),
    )


def _split_top_level_delimiters(text: str) -> tuple[tuple[str, ...], bool]:
    """Split unquoted '->' (pipeline) or '|' (parallel) while preserving segment text."""

    has_arrow = False
    quote: str | None = None
    escaped = False
    i = 0
    while i < len(text):
        char = text[i]
        if escaped:
            escaped = False
            i += 1
            continue
        if char == "\\" and quote is not None:
            escaped = True
            i += 1
            continue
        if quote is not None:
            if char == quote:
                quote = None
            i += 1
            continue
        if char in {"'", '"'}:
            quote = char
            i += 1
            continue
        if i + 1 < len(text) and text[i : i + 2] == "->":
            has_arrow = True
            break
        i += 1

    delim = "->" if has_arrow else "|"
    delim_len = len(delim)
    segments: list[str] = []
    start = 0
    quote = None
    escaped = False
    i = 0
    while i < len(text):
        char = text[i]
        if escaped:
            escaped = False
            i += 1
            continue
        if char == "\\" and quote is not None:
            escaped = True
            i += 1
            continue
        if quote is not None:
            if char == quote:
                quote = None
            i += 1
            continue
        if char in {"'", '"'}:
            quote = char
            i += 1
            continue
        if text[i : i + delim_len] == delim:
            segments.append(text[start:i])
            start = i + delim_len
            i += delim_len
            continue
        i += 1

    if quote is not None:
        raise AgentProfileError("unterminated quote in agent delegation")
    segments.append(text[start:])
    return tuple(segments), has_arrow


def _split_top_level_pipes(text: str) -> tuple[str, ...]:
    """Split unquoted pipes while preserving the original segment text."""
    segments, _ = _split_top_level_delimiters(text)
    return segments


class AgentProfileRegistry:
    """Merge safe built-in profiles with workspace-local Markdown profiles."""

    def __init__(self, workspace_root: Path, *, max_profile_bytes: int = 32 * 1024) -> None:
        if max_profile_bytes < 1:
            raise ValueError("max_profile_bytes must be positive")
        self.workspace_root = Path(workspace_root).resolve()
        self.max_profile_bytes = max_profile_bytes
        self.warnings: tuple[str, ...] = ()
        self._profiles: dict[str, AgentProfile] = {}
        self.load()

    @property
    def agents_root(self) -> Path:
        """Return the workspace-local profile directory."""

        return self.workspace_root / ".avo" / "agents"

    def load(self) -> tuple[AgentProfile, ...]:
        """Reload built-ins and valid workspace profiles."""

        profiles = {profile.name: profile for profile in _builtin_profiles()}
        warnings: list[str] = []
        root = self.agents_root
        try:
            resolved_root = root.resolve(strict=True)
        except OSError:
            self._profiles = profiles
            self.warnings = ()
            return self.list()
        try:
            resolved_root.relative_to(self.workspace_root)
        except ValueError:
            self._profiles = profiles
            self.warnings = ("agent profile directory escapes the workspace; ignored",)
            return self.list()

        try:
            candidates = sorted(resolved_root.glob("*.md"), key=lambda path: path.name)
        except OSError as exc:
            self._profiles = profiles
            self.warnings = (f"could not list agent profiles: {type(exc).__name__}",)
            return self.list()

        for candidate in candidates:
            name = candidate.stem
            if _SLUG_RE.fullmatch(name) is None:
                warnings.append(f"invalid agent profile name {name!r}; ignored")
                continue
            try:
                resolved = candidate.resolve(strict=True)
                resolved.relative_to(self.workspace_root)
                if not resolved.is_file():
                    continue
                if resolved.stat().st_size > self.max_profile_bytes:
                    warnings.append(f"agent profile {name!r} exceeds size limit; ignored")
                    continue
                raw = resolved.read_text(encoding="utf-8")
            except UnicodeError as exc:
                warnings.append(f"agent profile {name!r} could not be loaded: {type(exc).__name__}")
                continue
            except ValueError:
                warnings.append(f"agent profile {name!r} escapes the workspace; ignored")
                continue
            except OSError as exc:
                warnings.append(f"agent profile {name!r} could not be loaded: {type(exc).__name__}")
                continue
            lines = raw.splitlines()
            nonempty = next((index for index, line in enumerate(lines) if line.strip()), None)
            if nonempty is None:
                warnings.append(f"agent profile {name!r} is empty; ignored")
                continue
            description = lines[nonempty].strip()
            prompt = "\n".join(lines[nonempty + 1 :]).strip()
            if not prompt:
                warnings.append(f"agent profile {name!r} has no system prompt; ignored")
                continue
            existing = profiles.get(
                name,
                AgentProfile(name, "", "", AgentCapability.INHERITED),
            )
            capability = existing.capability
            profiles[name] = AgentProfile(
                name=name,
                description=description,
                system_prompt=prompt,
                capability=capability,
                builtin=False,
            )
        self._profiles = profiles
        self.warnings = tuple(warnings)
        return self.list()

    def get(self, name: str) -> AgentProfile | None:
        """Return one profile by exact slug."""

        return self._profiles.get(name)

    def list(self) -> tuple[AgentProfile, ...]:
        """Return profiles in deterministic name order."""

        return tuple(self._profiles[name] for name in sorted(self._profiles))

    def create(
        self,
        name: str,
        description: str,
        *,
        system_prompt: str | None = None,
    ) -> AgentProfile:
        """Create one workspace profile and reload the registry."""

        if _SLUG_RE.fullmatch(name) is None:
            raise AgentProfileError(
                "agent name must start with a lowercase letter and contain only "
                "lowercase letters, numbers, or hyphens"
            )
        if self.get(name) is not None:
            raise AgentProfileError(f"agent @{name} already exists")
        clean_description = description.strip()
        if not clean_description:
            raise AgentProfileError("agent description must not be empty")
        clean_prompt = (
            system_prompt or f"You are the workspace agent @{name}. {clean_description}"
        ).strip()
        if not clean_prompt:
            raise AgentProfileError("agent system prompt must not be empty")
        root = self.agents_root
        root.mkdir(parents=True, exist_ok=True)
        target = root / f"{name}.md"
        target.write_text(f"{clean_description}\n\n{clean_prompt}\n", encoding="utf-8")
        self.load()
        profile = self.get(name)
        if profile is None:
            raise AgentProfileError(f"created agent @{name} could not be loaded")
        return profile

    def parse_prompt(self, text: str) -> DelegationRequest | None:
        """Parse a leading registered mention and optional parallel/pipeline segments."""

        if not text.strip():
            return None
        segments, is_pipeline = _split_top_level_delimiters(text)
        first_match = _MENTION_RE.match(segments[0])
        if len(segments) == 1 and first_match is None:
            return None
        if first_match is None:
            return None
        if any(not segment.strip() for segment in segments):
            raise AgentProfileError("empty delegation segment")
        if len(segments) > 1 and any(_MENTION_RE.match(segment) is None for segment in segments):
            raise AgentProfileError("each delegation segment must begin with a registered @agent")

        mentions: list[AgentMention] = []
        for segment in segments:
            if not segment.strip():
                raise AgentProfileError("empty delegation segment")
            match = _MENTION_RE.match(segment)
            if match is None:
                raise AgentProfileError(
                    "each delegation segment must begin with a registered @agent"
                )
            name = match.group("name")
            profile = self.get(name)
            if profile is None:
                if len(segments) == 1:
                    return None
                raise AgentProfileError(f"unknown agent @{name}")
            prompt = match.group("rest").strip()
            if not prompt:
                raise AgentProfileError(f"agent @{name} requires a prompt")
            mentions.append(AgentMention(agent=profile, prompt=prompt))
        return DelegationRequest(parts=tuple(mentions), is_pipeline=is_pipeline)


__all__ = [
    "AgentCapability",
    "AgentMention",
    "AgentProfile",
    "AgentProfileError",
    "AgentProfileRegistry",
    "DelegationRequest",
]
