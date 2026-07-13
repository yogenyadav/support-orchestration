"""Service entrypoint: python -m support_orchestration [--config PATH] [--mode MODE] [--demo]."""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import logging
import sys
from pathlib import Path

from support_orchestration.runtime import DEFAULT_CONFIG_PATH, Runtime, load_runtime_config


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m support_orchestration",
        description="Agentic warehouse production-support service (watch → diagnose → recommend).",
    )
    parser.add_argument(
        "--config", type=Path, default=DEFAULT_CONFIG_PATH,
        help=f"Runtime YAML (default: {DEFAULT_CONFIG_PATH})",
    )
    parser.add_argument(
        "--mode", choices=["mock", "production"], default=None,
        help="Override the config file's mode",
    )
    parser.add_argument(
        "--demo", action="store_true",
        help="Mock mode only: seed one pre-assigned demo incident into the stub Jira",
    )
    parser.add_argument("--verbose", action="store_true", help="DEBUG logging")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s — %(message)s",
    )

    config = load_runtime_config(args.config)
    if args.mode:
        config["mode"] = args.mode
    if args.demo and config.get("mode") != "mock":
        parser.error("--demo requires mock mode")

    runtime = Runtime(config, demo=args.demo)
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(runtime.run())
    return 0


if __name__ == "__main__":
    sys.exit(main())
