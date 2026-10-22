"""Tool registry."""

from .agent import AgentTool
from .bash import BashTool
from .coverage_tool import CoverageTool
from .edit import EditFileTool
from .glob_tool import GlobTool
from .grep import GrepTool
from .read import ReadFileTool
from .run_tests import RunTestsTool
from .todo import TodoWriteTool
from .write import WriteFileTool

ALL_TOOLS = [
    BashTool(),
    ReadFileTool(),
    WriteFileTool(),
    EditFileTool(),
    GlobTool(),
    GrepTool(),
    RunTestsTool(),
    CoverageTool(),
    TodoWriteTool(),
    AgentTool(),
]

