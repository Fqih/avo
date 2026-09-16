"""Token savers — deterministic request-time compression and presets.

Opt-in subsystem: without ``AVO_SAVER`` or ``saver.json`` nothing in
this package runs. The pipeline stages here never call an LLM and
never mutate their input; see the design spec at
``docs/superpowers/specs/2026-09-16-token-savers-design.md``.
"""
