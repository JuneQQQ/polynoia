"""Polynoia MCP server — unified tool surface for all backend adapters.

Spawned as a stdio subprocess by Claude Code / OpenCode (via ACP) / Codex.
Each spawn binds to one (conv_id, agent_id) pair via env vars
``POLYNOIA_CONV_ID`` and ``POLYNOIA_AGENT_ID``.
"""
