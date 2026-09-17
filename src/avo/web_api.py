"""JSON API endpoints for the Avo Web UI: status, cost, router, persona,
permissions, and provider selection.

Holds the ``/api/status``, ``/api/persona``, ``/api/permissions``,
``/api/cost``, ``/api/router``, ``/api/router/probe``, ``/api/router/bench``,
and ``/api/provider`` routes plus the :class:`avo.web_ui.AvoWebServer`
router-status helpers they call.

Re-exported from :mod:`avo.web_ui` for backward compatibility.
"""

from __future__ import annotations

import contextlib
import json
import os
import urllib.parse
from typing import Any

from avo import __version__ as AVO_VERSION
from avo.auth import load_all_tokens
from avo.cost import aggregate_costs, report_to_dict
from avo.doctor import run_doctor
from avo.permissions import PermissionMode
from avo.web_http import _LOG, WebHttpMixin


def _mask_secret(secret: str) -> str:
    """Mask a token or API key for dashboard display."""
    clean = secret.strip()
    if len(clean) <= 8:
        return "•" * len(clean)
    return f"{clean[:4]}••••••••{clean[-4:]}"


class WebApiMixin(WebHttpMixin):
    """Core JSON API routes for :class:`avo.web_ui.AvoWebHandler`."""

    def _route_api_get(self, parsed: urllib.parse.ParseResult, path: str) -> bool:
        """Handle the status/persona/permissions/cost/router GET routes."""
        if path == "/api/status":
            doc = run_doctor(os.environ)
            stored = load_all_tokens()
            masked_tokens = {k: _mask_secret(v) for k, v in stored.items()}
            cost_report = aggregate_costs(self.server.database_path)
            router_chain = (
                os.environ.get("AVO_ROUTER_CHAIN", "").strip()
                or os.environ.get("AVO_ROUTER_PROVIDERS", "").strip()
            )
            chain_list = (
                [p.strip() for p in router_chain.split(",") if p.strip()]
                if router_chain
                else ["ollama", "openrouter"]
            )
            self._send_json(
                {
                    "version": AVO_VERSION,
                    "provider": os.environ.get("AVO_PROVIDER", "ollama"),
                    "model": os.environ.get("AVO_MODEL", "auto"),
                    "database": str(self.server.database_path),
                    "tokens": masked_tokens,
                    "cost": {
                        "run_count": cost_report.run_count,
                        "total_tokens": cost_report.total.total_tokens,
                        "input_tokens": cost_report.total.input_tokens,
                        "output_tokens": cost_report.total.output_tokens,
                        "cost_usd": (
                            str(cost_report.cost_usd) if cost_report.cost_usd is not None else None
                        ),
                    },
                    "doctor": {
                        "ok": doc.ok,
                        "endpoint": doc.endpoint,
                        "missing": doc.missing_vars,
                    },
                    "router": {
                        "active": os.environ.get("AVO_PROVIDER") == "router",
                        "strategy": os.environ.get("AVO_ROUTER_STRATEGY", "fallback").lower(),
                        "chain": chain_list,
                        "cooldown_seconds": float(
                            os.environ.get("AVO_ROUTER_COOLDOWN_SECONDS", "30.0") or 30.0
                        ),
                    },
                    "persona": {
                        "active": self.server.persona_manager.active_persona or "default",
                        "instructions_configured": bool(
                            self.server.persona_manager.custom_instructions
                        ),
                        "instructions": self.server.persona_manager.custom_instructions or "",
                        "available": [
                            *self.server.persona_manager.available_personas().keys(),
                            "default",
                        ],
                    },
                    "permissions": {
                        "mode": self.server.permission_mode,
                        "available": [mode.value for mode in PermissionMode],
                    },
                    "env": {
                        "AVO_ROUTER_CHAIN": router_chain,
                        "AVO_ROUTER_PROVIDERS": os.environ.get("AVO_ROUTER_PROVIDERS", ""),
                        "AVO_ROUTER_MODELS": os.environ.get("AVO_ROUTER_MODELS", ""),
                    },
                }
            )
            return True

        if path == "/api/persona":
            self._send_json(
                {
                    "active": self.server.persona_manager.active_persona or "default",
                    "instructions": self.server.persona_manager.custom_instructions or "",
                    "available": [
                        *self.server.persona_manager.available_personas().keys(),
                        "default",
                    ],
                }
            )
            return True

        if path == "/api/permissions":
            self._send_json(
                {
                    "mode": self.server.permission_mode,
                    "available": [mode.value for mode in PermissionMode],
                }
            )
            return True

        if path == "/api/cost":
            cost_report = aggregate_costs(self.server.database_path)
            self._send_json(report_to_dict(cost_report))
            return True

        if path == "/api/router":
            self._send_json(self.server.get_sync_router_status())
            return True

        return False

    def _route_api_post(self, parsed: urllib.parse.ParseResult, path: str) -> bool:
        """Handle the persona/permissions/router/provider POST routes."""
        if path == "/api/persona":
            content_len = int(self.headers.get("Content-Length", 0))
            if content_len <= 0:
                self._send_json({"error": "empty body"}, status=400)
                return True
            try:
                data = json.loads(self.rfile.read(content_len).decode("utf-8"))
            except Exception:
                self._send_json({"error": "invalid JSON body"}, status=400)
                return True

            persona_name = data.get("persona")
            instructions = data.get("instructions")

            register_data = data.get("register")
            if isinstance(register_data, dict):
                from avo.app_tools.workspace import WorkspacePathError

                if not self._require_confirmation(data):
                    return True
                reg_name = str(register_data.get("name", "")).strip()
                reg_prompt = str(register_data.get("prompt", "")).strip()
                try:
                    self.server.persona_manager.register_persona(reg_name, reg_prompt, persist=True)
                except (ValueError, OSError, WorkspacePathError) as exc:
                    self._send_json({"ok": False, "error": str(exc)}, status=400)
                    return True

            if persona_name is not None:
                p_clean = str(persona_name).strip()
                if p_clean in ("default", "clear", ""):
                    self.server.persona_manager.set_persona(None)
                elif p_clean in self.server.persona_manager.available_personas():
                    self.server.persona_manager.set_persona(p_clean)
                else:
                    self._send_json(
                        {"ok": False, "error": f"Unknown persona '{p_clean}'"}, status=400
                    )
                    return True

            if instructions is not None:
                instr_clean = str(instructions).strip()
                if instr_clean in ("clear", ""):
                    self.server.persona_manager.set_custom_instructions(None)
                else:
                    self.server.persona_manager.set_custom_instructions(instr_clean)

            self._send_json(
                {
                    "ok": True,
                    "persona": self.server.persona_manager.active_persona or "default",
                    "instructions": self.server.persona_manager.custom_instructions or "",
                    "available": [
                        *self.server.persona_manager.available_personas().keys(),
                        "default",
                    ],
                }
            )
            return True

        if path == "/api/permissions":
            content_len = int(self.headers.get("Content-Length", 0))
            if content_len <= 0:
                self._send_json({"error": "empty body"}, status=400)
                return True
            try:
                data = json.loads(self.rfile.read(content_len).decode("utf-8"))
            except Exception:
                self._send_json({"error": "invalid JSON body"}, status=400)
                return True

            if not self._require_confirmation(data):
                return True
            mode = str(data.get("mode", "")).strip().lower()
            if mode not in {value.value for value in PermissionMode}:
                self._send_json(
                    {"ok": False, "error": f"Invalid permission mode '{mode}'"}, status=400
                )
                return True

            self.server.permission_mode = mode
            os.environ["AVO_PERMISSION_MODE"] = mode
            self._send_json({"ok": True, "mode": mode})
            return True

        if path == "/api/router/probe":
            self._send_json(self.server.sync_probe_router())
            return True

        if path == "/api/router/bench":
            content_len = int(self.headers.get("Content-Length", 0))
            bench_prompt = "Explain recursion in 10 words."
            if content_len > 0:
                with contextlib.suppress(Exception):
                    loaded = json.loads(self.rfile.read(content_len).decode("utf-8"))
                    if isinstance(loaded, dict) and loaded.get("prompt"):
                        bench_prompt = str(loaded["prompt"]).strip() or bench_prompt
            res = self.server.sync_bench_routes(prompt=bench_prompt)
            self._send_json(res)
            return True

        if path == "/api/provider":
            content_len = int(self.headers.get("Content-Length", 0))
            if content_len <= 0:
                self._send_json({"error": "empty body"}, status=400)
                return True
            try:
                data = json.loads(self.rfile.read(content_len).decode("utf-8"))
            except Exception:
                self._send_json({"error": "invalid JSON body"}, status=400)
                return True

            if not self._require_confirmation(data):
                return True
            provider = str(data.get("provider", "")).strip()
            model = str(data.get("model", "")).strip()
            if not provider:
                self._send_json({"error": "provider is required"}, status=400)
                return True

            os.environ["AVO_PROVIDER"] = provider
            if model:
                os.environ["AVO_MODEL"] = model
            current_model = model or os.environ.get("AVO_MODEL", "")
            self._send_json({"ok": True, "provider": provider, "model": current_model})
            return True

        return False


class ApiServerMixin:
    """Router status/probe/bench queries for :class:`avo.web_ui.AvoWebServer`."""

    def get_sync_router_status(self) -> dict[str, Any]:
        """Query router routes, health status, and fallback chains."""
        from avo.config import build_provider_from_env
        from avo.providers.router import BaseRouterProvider

        provider_name = os.environ.get("AVO_PROVIDER", "ollama")
        model_name = os.environ.get("AVO_MODEL", "default")
        router_chain = os.environ.get("AVO_ROUTER_CHAIN", "")
        router_providers = os.environ.get("AVO_ROUTER_PROVIDERS", "")
        router_models = os.environ.get("AVO_ROUTER_MODELS", "")

        routes_info: list[dict[str, Any]] = []
        is_router = False
        health_status: dict[str, Any] = {}

        try:
            prov = build_provider_from_env(dict(os.environ))
            if isinstance(prov, BaseRouterProvider):
                is_router = True
                health_status = prov.get_health_status()
                for name, p in prov.routes:
                    h = health_status.get(name, {})
                    routes_info.append(
                        {
                            "name": name,
                            "provider": getattr(p, "name", name),
                            "model": getattr(p, "model", "default"),
                            "healthy": h.get("healthy", True),
                            "in_cooldown": h.get("in_cooldown", False),
                            "cooldown_remaining_seconds": h.get("cooldown_remaining_seconds", 0.0),
                            "consecutive_failures": h.get("consecutive_failures", 0),
                            "last_error": h.get("last_error"),
                            "last_latency_ms": h.get("last_latency_ms"),
                        }
                    )
            else:
                routes_info.append(
                    {
                        "name": provider_name,
                        "provider": getattr(prov, "name", provider_name),
                        "model": getattr(prov, "model", model_name),
                        "healthy": True,
                        "in_cooldown": False,
                        "cooldown_remaining_seconds": 0.0,
                        "consecutive_failures": 0,
                        "last_error": None,
                        "last_latency_ms": None,
                    }
                )
        except Exception as exc:
            _LOG.warning("Could not build provider for router status: %s", exc)

        return {
            "is_router": is_router,
            "active_provider": provider_name,
            "active_model": model_name,
            "router_chain": router_chain,
            "router_providers": router_providers,
            "router_models": router_models,
            "routes": routes_info,
            "health": health_status,
        }

    def sync_probe_router(self) -> dict[str, Any]:
        """Run health probe on router endpoints synchronously."""
        import asyncio

        from avo.config import build_provider_from_env
        from avo.providers.router import BaseRouterProvider

        try:
            prov = build_provider_from_env(dict(os.environ))
            if isinstance(prov, BaseRouterProvider):
                outcomes = asyncio.run(prov.probe_all())
                return {"ok": True, "outcomes": outcomes, "health": prov.get_health_status()}
            active_p = os.environ.get("AVO_PROVIDER", "ollama")
            return {"ok": True, "outcomes": {active_p: True}, "health": {}}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    def sync_bench_routes(self, prompt: str = "Explain recursion in 10 words.") -> dict[str, Any]:
        """Run speed and latency benchmark on configured routes."""
        import asyncio

        from avo.bench import benchmark_all_routes, benchmark_route
        from avo.config import build_provider_from_env
        from avo.providers.router import BaseRouterProvider

        try:
            prov = build_provider_from_env(dict(os.environ))
            if isinstance(prov, BaseRouterProvider):
                results = asyncio.run(benchmark_all_routes(prov.routes, prompt=prompt))
                return {
                    "ok": True,
                    "prompt": prompt,
                    "results": [r.as_dict() for r in results],
                }
            active_p = os.environ.get("AVO_PROVIDER", "ollama")
            res = asyncio.run(benchmark_route(active_p, prov, prompt=prompt))
            return {
                "ok": True,
                "prompt": prompt,
                "results": [res.as_dict()],
            }
        except Exception as exc:
            return {"ok": False, "error": str(exc), "results": []}


__all__ = ["ApiServerMixin", "WebApiMixin", "_mask_secret"]
