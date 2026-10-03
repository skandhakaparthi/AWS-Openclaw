import time


class SafetyManager:
    def __init__(
        self,
        max_steps=5,
        max_cost=0.05,
        max_runtime_seconds=60,
        repeated_error_limit=2
    ):
        self.max_steps = max_steps
        self.max_cost = max_cost
        self.max_runtime_seconds = max_runtime_seconds
        self.repeated_error_limit = repeated_error_limit

        self.current_step = 0
        self.current_cost = 0.0
        self.require_approval = False

        self.start_time = None
        self.error_history = []

    def start(self):
        self.start_time = time.time()

    def next_step(self):
        if self.current_step >= self.max_steps:
            raise RuntimeError(
                f"Maximum iteration limit reached: {self.max_steps}"
            )

        self.check_runtime()

        self.current_step += 1
        return self.current_step

    def add_cost(self, amount):
        self.current_cost += amount

        if self.current_cost > self.max_cost:
            raise RuntimeError(
                f"Maximum cost limit exceeded: "
                f"${self.max_cost:.4f}"
            )

    def check_runtime(self):
        if self.start_time is None:
            return

        elapsed = time.time() - self.start_time

        if elapsed > self.max_runtime_seconds:
            raise RuntimeError(
                f"Maximum runtime limit reached: "
                f"{self.max_runtime_seconds} seconds"
            )

    def record_error(self, error):
        error_text = str(error).strip()

        self.error_history.append(error_text)

        recent_errors = self.error_history[-self.repeated_error_limit:]

        if (
            len(recent_errors) == self.repeated_error_limit
            and len(set(recent_errors)) == 1
        ):
            raise RuntimeError(
                "Runaway loop detected: "
                "the same error occurred repeatedly."
            )

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
        self.current_cost = 0.0
        self.start_time = time.time()
        self.error_history = []

    def status(self):
        remaining_steps = self.max_steps - self.current_step
        remaining_cost = self.max_cost - self.current_cost

        return {
            "current_step": self.current_step,
            "max_steps": self.max_steps,
            "remaining_steps": remaining_steps,
            "current_cost": round(self.current_cost, 6),
            "max_cost": self.max_cost,
            "remaining_cost": round(remaining_cost, 6),
            "max_runtime_seconds": self.max_runtime_seconds,
            "repeated_error_limit": self.repeated_error_limit,
            "require_approval": self.require_approval
        }


if __name__ == "__main__":

    safety = SafetyManager(
        max_steps=3,
        max_cost=0.05,
        max_runtime_seconds=60
    )

    safety.reset()

    print("SAFETY MANAGER TEST")
    print("-" * 50)

    for _ in range(3):
        print("Step:", safety.next_step())

    print("\nAdding mock cost...")
    safety.add_cost(0.00)

    print("\nStatus:")
    print(safety.status())

    print("\nRunaway detection test:")

    try:
        safety.record_error("ZeroDivisionError")
        safety.record_error("ZeroDivisionError")
    except RuntimeError as error:
        print("BLOCKED:", error)
