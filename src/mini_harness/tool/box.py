from dataclasses import dataclass
from typing import Callable
from pydantic import BaseModel
from mini_harness.tool.tools import (
    GlobFileInput, glob_file,
    GrepFileInput, grep_file,
    ReadFileInput, read_file,
    WriteFileInput, write_file,
    EditFileInput, edit_file,
    RunBashInput, run_bash,
    RunSandboxInput, run_sandbox,
    RunTodoInput, run_todo,
    RunSubAgentInput, run_subagent
)




@dataclass(frozen = True)
class ToolDefinition:
    name: str
    description: str
    parameters: type[BaseModel]
    function: Callable
    risky: bool
#工具映射表
TOOLS = [
    ToolDefinition('glob_file', 'glob and check the file', GlobFileInput, glob_file, False),
    ToolDefinition('grep_file', 'grep and search for the targeted information', GrepFileInput, grep_file, False),
    ToolDefinition('read_file', 'Read a file. Output uses cat -n style line numbers, which are for reference only and must never be copied into edit_file. Without offset/limit the whole file is returned, truncated with a continuation hint if it is very large.', ReadFileInput, read_file, False),
    ToolDefinition('write_file', 'Create a new file. To replace an existing file entirely you must have read it in full first and pass overwrite=true; for partial changes use edit_file instead.', WriteFileInput, write_file, False),
    ToolDefinition('edit_file', 'Replace a specific string in an existing file. You must have read the file first, and it must not have changed since. old_string must match the file exactly and must not contain line-number prefixes. On success the tool echoes the resulting lines with their real line numbers.', EditFileInput, edit_file, False),
    ToolDefinition('run_bash', 'operate the terminal by using the command', RunBashInput, run_bash, True),
    ToolDefinition('run_sandbox', 'Run code in a disposable Docker Python 3.12 container with no network and resource limits. Only sandbox/ is shared, as /workspace; writes there persist. Requires Docker and a locally pulled python:3.12-slim image. Prefer this for running generated code; run_bash executes on the host.', RunSandboxInput, run_sandbox, True),
    ToolDefinition('run_todo', 'build and update the todo list', RunTodoInput, run_todo, False),
    ToolDefinition('run_subagent', 'build and run the subagent to finish the task', RunSubAgentInput, run_subagent, True)
]
