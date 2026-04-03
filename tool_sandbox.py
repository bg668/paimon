"""
ToolSandbox: registry-based tool execution with safe exception handling.
"""
from typing import Any, Callable

_registry: dict[str, Callable] = {}


def tool(_func: Callable = None, *, name: str = None, desc: str = None):
    """Decorator to register a function as a named tool.

    Supports both @tool and @tool(name="...", desc="...") forms.
    """
    def decorator(func: Callable) -> Callable:
        tool_name = name or func.__name__
        _registry[tool_name] = func
        return func

    if _func is not None:
        # Called as @tool (no arguments)
        return decorator(_func)
    # Called as @tool(...) with keyword arguments
    return decorator


def execute_tool(tool_name: str, kwargs: dict) -> dict[str, Any]:
    """Execute a registered tool by name with the given kwargs.

    Returns a dict with keys: status ("ok"|"failed"), output, error.
    """
    if tool_name not in _registry:
        return {"status": "failed", "output": None, "error": f"Tool '{tool_name}' not found"}
    try:
        result = _registry[tool_name](**kwargs)
        return {"status": "ok", "output": result, "error": None}
    except Exception as exc:
        return {"status": "failed", "output": None, "error": str(exc)}
