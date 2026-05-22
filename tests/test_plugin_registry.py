import pytest

from src.agent.registry import AgentRegistry, DuplicateCapabilityError


class TestPluginRegistry:
    def setup_method(self):
        self.registry = AgentRegistry()

    def test_registers_and_resolves_unique_plugin_capabilities(self):
        plugin_id = self.registry.register_plugin(
            "core-tools",
            ["summarize", "search"],
            metadata={"token": "private-runtime-data"},
        )

        resolved = self.registry.resolve_capability("SUMMARIZE")

        assert resolved["id"] == plugin_id
        assert resolved["name"] == "core-tools"
        assert resolved["capabilities"] == ["summarize", "search"]
        assert self.registry.plugin_registration_metrics[
            "plugin_registration.accepted"
        ] == 1

    def test_rejects_duplicate_names_within_same_plugin(self):
        with pytest.raises(DuplicateCapabilityError):
            self.registry.register_plugin(
                "duplicate-plugin",
                ["summarize", "summarize"],
                metadata={"token": "do-not-audit"},
            )

        assert self.registry.list_plugins() == []
        assert self.registry.resolve_capability("summarize") is None
        assert self.registry.plugin_registration_metrics[
            "plugin_registration.rejected.duplicate_capability_names"
        ] == 1
        assert "do-not-audit" not in str(
            self.registry.plugin_registration_audit
        )

    def test_rejects_cross_plugin_capability_collision(self):
        first_id = self.registry.register_plugin(
            "first-plugin",
            ["dispatch", "route"],
        )

        with pytest.raises(DuplicateCapabilityError):
            self.registry.register_plugin(
                "second-plugin",
                ["route", "audit"],
                metadata={"secret": "private"},
            )

        assert len(self.registry.list_plugins()) == 1
        assert self.registry.resolve_capability("route")["id"] == first_id
        assert self.registry.resolve_capability("audit") is None
        assert self.registry.plugin_registration_metrics[
            "plugin_registration.rejected.capability_already_registered"
        ] == 1

    def test_cache_is_invalidated_after_safe_registration_change(self):
        plugin_id = self.registry.register_plugin(
            "dispatch-plugin",
            ["dispatch"],
        )
        assert self.registry.resolve_capability("dispatch")["id"] == plugin_id

        assert self.registry.unregister_plugin(plugin_id)

        assert self.registry.resolve_capability("dispatch") is None

    def test_audit_records_are_sanitized_and_explain_decisions(self):
        self.registry.register_plugin(
            "safe-plugin",
            ["audit"],
            metadata={"runtime_context": "private"},
        )
        with pytest.raises(DuplicateCapabilityError):
            self.registry.register_plugin(
                "unsafe-plugin",
                ["audit"],
                metadata={"runtime_context": "private"},
            )

        audit = self.registry.plugin_registration_audit
        assert audit[-1]["decision"] == "capability_already_registered"
        assert audit[-1]["capability_count"] == 1
        assert "private" not in str(audit)
