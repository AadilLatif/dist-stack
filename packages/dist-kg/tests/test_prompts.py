"""Prompt-level tests: registration + render via the prompt ``fn``."""

from __future__ import annotations

from kg_server.server import create_server


def _prompt(name: str):
    return create_server()._prompt_manager._prompts[name]


class TestProvenancePrompt:
    def test_prompt_registered(self):
        mcp = create_server()
        assert "trace_provenance" in mcp._prompt_manager._prompts

    def test_render_mentions_subject_and_tools(self):
        text = _prompt("trace_provenance").fn(subject="wf_abc123")
        assert "wf_abc123" in text
        assert "query_provenance" in text
        assert "get_provenance_chain" in text
        assert "kg://graph/{node_id}" in text
        assert "search_nodes" in text

    def test_required_argument_is_subject(self):
        import asyncio

        mcp = create_server()
        prompt = next(
            p for p in asyncio.run(mcp.list_prompts()) if p.name == "trace_provenance"
        )
        assert [a.name for a in prompt.arguments] == ["subject"]
        assert prompt.arguments[0].required is True
