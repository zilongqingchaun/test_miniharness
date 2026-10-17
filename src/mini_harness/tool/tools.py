from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any

# 1. glob_file (已提供，保留作参考)
class GlobFileInput(BaseModel):
    pattern: str = Field(..., description="The glob pattern to match files, e.g., '**/*.py'")
    # 你的原版是 pass，但实际需要 pattern 才能 glob，这里补全

def glob_file(globfileinput: GlobFileInput):
    pass


# 2. grep_file
class GrepFileInput(BaseModel):
    pattern: str = Field(..., description="The string or regex pattern to search for")
    path: str = Field(default=".", description="The directory or file path to search in")
    include: Optional[str] = Field(None, description="Glob pattern to filter which files to search")

def grep_file(grepfileinput: GrepFileInput):
    pass


# 3. read_file
class ReadFileInput(BaseModel):
    path: str = Field(..., description="The path to the file to read")
    offset: Optional[int] = Field(None, description="Line number to start reading from (0-indexed)")
    limit: Optional[int] = Field(None, description="Maximum number of lines to read")

def read_file(readfileinput: ReadFileInput):
    pass


# 4. write_file
class WriteFileInput(BaseModel):
    path: str = Field(..., description="The path where the new file will be created")
    content: str = Field(..., description="The full content to write into the file")
    overwrite: bool = Field(default=False, description="Set to true to overwrite an existing file (requires prior full read)")

def write_file(writefileinput: WriteFileInput):
    pass


# 5. edit_file
class EditFileInput(BaseModel):
    path: str = Field(..., description="The path to the file to edit")
    old_string: str = Field(..., description="The exact text to be replaced (without line number prefixes)")
    new_string: str = Field(..., description="The new text to replace the old_string with")

def edit_file(editfileinput: EditFileInput):
    pass


# 6. run_bash
class RunBashInput(BaseModel):
    command: str = Field(..., description="The bash command to execute on the host terminal")
    timeout: Optional[int] = Field(default=120, description="Timeout in seconds for the command execution")

def run_bash(runbashinput: RunBashInput):
    pass


# 7. run_sandbox
class RunSandboxInput(BaseModel):
    code: str = Field(..., description="The Python code to execute inside the Docker sandbox")
    # 如果需要在沙箱里执行 shell 命令，可以用 command 字段替换或共存

def run_sandbox(runsandboxinput: RunSandboxInput):
    pass


# 8. run_todo
class TodoItem(BaseModel):
    task: str = Field(..., description="Description of the task")
    status: str = Field(..., description="Status of the task: 'pending', 'in_progress', 'completed'")

class RunTodoInput(BaseModel):
    todos: List[TodoItem] = Field(..., description="The updated list of todo items")

def run_todo(runtodoinput: RunTodoInput):
    pass


# 9. run_subagent
class RunSubAgentInput(BaseModel):
    task_description: str = Field(..., description="A detailed description of the task the subagent needs to accomplish")
    # 可选：限制子代理能用的工具
    allowed_tools: Optional[List[str]] = Field(None, description="List of tool names the subagent is allowed to use")

def run_subagent(runsubagentinput: RunSubAgentInput):
    pass