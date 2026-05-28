"""Orchestrator state machine (5 phases).

Per user insight: Orchestrator is itself an Agent (provider + system_prompt + 3 tools:
decompose_tasks / dispatch_parallel / aggregate_outputs).

This module implements the *runtime* that exposes those tools and runs the
state machine in response to the orchestrator agent's tool calls.
"""
