"""dist-workflow-runner: MCP workflow-runner for the NREL distribution suite.

Runs versioned JSON workflow templates (``workflows/*.json``) across the domain
MCP servers (gdm, gdm_flow, erad, ditto, shift), recording every execution in a
shared runstore (`dist_stack.runstore`) and persisting execution-graph artifact
sidecars for provenance queries.
"""

__version__ = "0.1.0"
