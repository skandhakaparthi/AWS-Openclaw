from tools.filesystem import list_files, read_file


print("========== CODEPILOT AI ==========")

print("\nFiles in workspace:")
print(list_files())

print("\n========== calculator.py ==========")
print(read_file("calculator.py"))