# Lazy on purpose: the agy PreToolUse hook imports agentic.command_safety
# (through action_risk) on every gated tool call, and an eager
# `from .agent_runner import ...` here pulled in every provider SDK first
# (3.3 s measured, see agy_step_gate).


def __getattr__(name):
    if name in ("AgentRunner", "AgentEvent"):
        from . import agent_runner
        return getattr(agent_runner, name)
    raise AttributeError(f"module 'agentic' has no attribute {name!r}")
