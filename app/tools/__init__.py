"""Importing this package registers every tool."""
from app.tools import (approval_tools, fee_tools, memory_tools, policy_tools, scholarship_tools, student_tools, ticket_tools)  # noqa: F401
from app.tools.registry import REGISTRY, ToolContext, ToolError, run_tool, tool_specs  # noqa: F401
