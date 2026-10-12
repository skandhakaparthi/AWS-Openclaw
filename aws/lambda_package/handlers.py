
def planner_handler(event, context):
    task = event.get("task")
    if not task:
        raise ValueError("Missing required field: task")

    return {
        **event,
        "task": task,
        "subtasks": [
            "Inspect the task",
            "Execute the task",
            "Observe the result",
            "Correct errors or finish safely",
        ],
        "iteration": event.get("iteration", 0),
    }


def observer_handler(event, context):
    success = bool(event.get("success", False))
    return {
        **event,
        "success": success,
        "observation": (
            "Execution succeeded"
            if success
            else "Execution failed or needs correction"
        ),
    }


def error_analyzer_handler(event, context):
    error = str(event.get("error", ""))
    can_fix = "ZeroDivisionError" in error

    return {
        **event,
        "can_fix": can_fix,
        "action": "fix" if can_fix else "stop",
    }


def rollback_handler(event, context):
    # This records the request; it does not restore files yet.
    return {
        **event,
        "success": False,
        "rollback_requested": True,
        "message": "Rollback requested; checkpoint restoration is pending.",
    }
