class SafetyManager:

    def __init__(self, max_steps=5, require_approval=False):
        self.max_steps = max_steps
        self.current_step = 0
        self.require_approval = require_approval

    def next_step(self):
        if self.current_step >= self.max_steps:
            raise RuntimeError(
                f"Maximum iteration limit reached: {self.max_steps}"
            )

        self.current_step += 1
        return self.current_step

    def check_approval(self, action):
        if not self.require_approval:
            return True

        answer = input(
            f"\nHuman approval required for: {action}\n"
            "Allow? (y/n): "
        )

        if answer.lower() != "y":
            raise PermissionError(
                "Action rejected by human approval."
            )

        return True

    def reset(self):
        self.current_step = 0

    def status(self):
        return {
            "current_step": self.current_step,
            "max_steps": self.max_steps,
            "remaining_steps": self.max_steps - self.current_step,
            "require_approval": self.require_approval
        }


if __name__ == "__main__":
    safety = SafetyManager(max_steps=3)

    print("Safety Manager Test")
    print("-" * 40)

    for _ in range(3):
        print("Step:", safety.next_step())

    print("\nStatus:")
    print(safety.status())
