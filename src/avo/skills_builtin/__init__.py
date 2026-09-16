"""Built-in prompt-skill files shipped with the wheel (spec §6).

Plain ``<slug>.md`` files so :class:`avo.skills.SkillRegistry` can
walk this directory directly. Presets reference them by slug;
operators may copy a file into ``<workspace>/.avo/skills/`` to make
it available as ``/skill <slug>`` in the chat REPL. Loading helpers
live in :mod:`avo.savers.presets`.
"""
