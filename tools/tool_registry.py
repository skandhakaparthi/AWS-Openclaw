from tools.filesystem import read_file, write_file
from tools.executor import run_python_file
from tools.permissions import ToolPermissionManager


class ToolRegistry:
    def __init__(self, workspace=None):
        self.workspace = workspace
        self.permissions = ToolPermissionManager()

    def read_file(self, filename):
        self.permissions.check("read_file")
        return read_file(filename, workspace=self.workspace)

    def write_file(self, filename, content):
        self.permissions.check("write_file")
        return write_file(filename, content, workspace=self.workspace)

    def run_python(self, filename):
        self.permissions.check("run_python")
        if self.workspace is None:
            return run_python_file(filename)
        return run_python_file(filename, workspace=self.workspace)

    def show_permissions(self):
        self.permissions.show_permissions()
