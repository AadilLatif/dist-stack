"""Entry point for the dist-workflow-runner MCP server.

Usage:
    python -m workflow_runner [--config servers.yaml]

``--config`` wins over the ``WORKFLOW_RUNNER_CONFIG`` env var, which wins over
the default ``./servers.yaml`` (see ``workflow_runner.config``).
"""

from __future__ import annotations

import argparse
import os

from workflow_runner.config import ENV_CONFIG_VAR


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="workflow-runner",
        description=(
            "MCP workflow-runner for the distribution suite. Spawns and keeps "
            "alive the domain MCP servers configured in servers.yaml."
        ),
    )
    parser.add_argument(
        "--config",
        default=None,
        metavar="PATH",
        help="Path to a servers.yaml config (default: $WORKFLOW_RUNNER_CONFIG or ./servers.yaml)",
    )
    return parser.parse_args(argv)


def main() -> None:
    args = _parse_args()
    if args.config:
        # The lifespan loads config lazily; passing it through the env var lets
        # the resolution order "arg > env > ./servers.yaml" hold everywhere.
        os.environ[ENV_CONFIG_VAR] = args.config

    from workflow_runner.server import create_server

    mcp = create_server()
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
