"""Production adapters for the 5 custom MCP tools.

Each adapter implements the ABC from tools/mcp_server.py and wires to a real
external service. Stubs live in mcp_server.py and are used in tests.

Import the factory for the standard way to build adapters from environment variables:
    from support_orchestration.tools.adapters.factory import build_adapters_from_env
"""
