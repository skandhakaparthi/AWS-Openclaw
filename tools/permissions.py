class ToolPermissionManager:
    ALLOWED_TOOLS = {
        "read_file",
        "write_file",
        "run_python"
    }

    def is_allowed(self, tool_name):
        return tool_name in self.ALLOWED_TOOLS

    def check(self, tool_name):
        if not self.is_allowed(tool_name):
            raise PermissionError(
                f"Tool '{tool_name}' is not permitted."
            )
        return True

    def show_permissions(self):
        print("\nTOOL PERMISSIONS")
        print("-" * 40)

        for tool in sorted(self.ALLOWED_TOOLS):
            print(f"OK {tool}")

        print("-" * 40)


if __name__ == "__main__":
    manager = ToolPermissionManager()

    manager.show_permissions()

    print("\nTesting allowed tool:")
    print(manager.check("read_file"))

    print("\nTesting blocked tool:")

    try:
        manager.check("delete_system_files")
    except PermissionError as error:
        print(error)
