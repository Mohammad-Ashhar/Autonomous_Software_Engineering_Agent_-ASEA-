import json
from pathlib import Path
from typing import Any

from langgraph.graph import StateGraph, START, END

from asea.core.state import ASEAState
from asea.tools.analyzer import RepositoryAnalyzer
from asea.tools.test_runner import DockerTestRunner

from asea.agents.bug_detector import bug_detector_node
from asea.agents.patch_generator import PatchGeneratorAgent
from asea.agents.patch_validator import PatchValidatorAgent


def repository_analysis_node(state: ASEAState) -> dict[str, Any]:
    """Analyze the repository and generate its code map."""

    repo_path = Path(state["repo_path"]).resolve()

    if not repo_path.is_dir():
        raise FileNotFoundError(f"Repository not found: {repo_path}")

    analyzer = RepositoryAnalyzer()

    result = analyzer.analyze_local_repository(
        folder=str(repo_path)
    )

    output_file = Path(result["output_file"])

    if not output_file.is_absolute():
        output_file = Path.cwd() / output_file

    with output_file.open("r", encoding="utf-8") as file:
        repo_map = json.load(file)

    return {
        "repo_map": repo_map,
        "baseline_results": result,
        "current_agent": "repository_analysis",
        "status": "repository_analysis_completed"
    }


def test_execution_node(state: ASEAState) -> dict[str, Any]:
    """Execute repository tests inside the Docker sandbox."""

    repo_path = Path(state["repo_path"]).resolve()

    if not repo_path.is_dir():
        raise FileNotFoundError(f"Repository not found: {repo_path}")

    runner = DockerTestRunner()
    results = runner.run_tests(repo_path)

    return {
        "test_results": results,
        "current_agent": "test_execution",
        "status": "test_execution_completed"
    }


def route_after_tests(state: ASEAState) -> str:
    """Decide whether patch generation should run."""

    detected_bugs = state.get("detected_bugs", [])

    confirmed_bugs = [
        bug for bug in detected_bugs
        if bug.get("classification") == "confirmed_bug"
    ]

    if confirmed_bugs:
        return "patch_generation"

    return "finish"


def get_bug_key(bug: dict[str, Any]) -> str:
    """Create a stable identifier for retry tracking."""

    return f"{bug.get('file', 'unknown')}::{bug.get('title', 'unknown')}"


def patch_generation_node(state: ASEAState) -> dict[str, Any]:
    """Generate a patch, using previous validation feedback when available."""

    repo_path = Path(state["repo_path"]).resolve()

    detected_bugs = state.get("detected_bugs", [])

    confirmed_bugs = [
        bug for bug in detected_bugs
        if bug.get("classification") == "confirmed_bug"
    ]

    if not confirmed_bugs:
        return {
            "current_agent": "patch_generation",
            "status": "no_confirmed_bugs"
        }

    bug = confirmed_bugs[0]
    bug_key = get_bug_key(bug)

    retry_counts = dict(state.get("retry_counts", {}))
    attempt = retry_counts.get(bug_key, 0) + 1

    max_retries = state.get("max_retries", 3)

    if attempt > max_retries:
        return {
            "current_agent": "patch_generation",
            "status": "retry_limit_reached"
        }

    bug_file = Path(bug["file"])

    if not bug_file.is_absolute():
        bug_file = repo_path / bug_file

    bug_file = bug_file.resolve()

    try:
        bug_file.relative_to(repo_path)
    except ValueError:
        raise ValueError(
            "Detected bug file is outside the repository."
        )

    if not bug_file.is_file():
        raise FileNotFoundError(
            f"Bug file not found: {bug_file}"
        )

    previous_feedback = state.get("current_validation")

    generator = PatchGeneratorAgent()

    try:
        patch_result = generator.generate_patch(
            file_path=bug_file,
            bug=bug,
            previous_feedback=previous_feedback,
            attempt=attempt
        )

        if not isinstance(patch_result, dict):
            raise TypeError(
                "Patch generator must return a dictionary."
            )

        patch_result["attempt"] = attempt

        patches = list(state.get("patches", []))
        patches.append(patch_result)

        retry_counts[bug_key] = attempt

        return {
            "patches": patches,
            "retry_counts": retry_counts,
            "current_agent": "patch_generation",
            "status": "patch_generation_completed"
        }

    except Exception as error:
        retry_counts[bug_key] = attempt

        failed_result = {
            "status": "PATCH_GENERATION_FAILED",
            "patch_applied": False,
            "tests_passed": False,
            "reason": str(error),
            "attempt": attempt
        }

        return {
            "retry_counts": retry_counts,
            "current_validation": failed_result,
            "current_agent": "patch_generation",
            "status": "patch_generation_failed",
            "errors": state.get("errors", []) + [
                f"Patch generation attempt {attempt} failed: {error}"
            ]
        }


def patch_validation_node(state: ASEAState) -> dict[str, Any]:
    """Validate the generated patch in a temporary Docker sandbox."""

    repo_path = Path(state["repo_path"]).resolve()
    patches = state.get("patches", [])

    if not patches:
        return {
            "current_agent": "patch_validation",
            "current_validation": {
                "status": "PATCH_REJECTED",
                "patch_applied": False,
                "tests_passed": False,
                "reason": "No patch was generated."
            },
            "status": "patch_validation_failed"
        }

    patch_result = patches[-1]
    validator = PatchValidatorAgent()

    try:
        validation_result = validator.validate_patch(
            repository_path=repo_path,
            patch_result=patch_result
        )

    except Exception as error:
        validation_result = {
            "status": "PATCH_REJECTED",
            "patch_applied": False,
            "tests_passed": False,
            "reason": str(error)
        }

    validation_result["attempt"] = patch_result.get("attempt", 1)

    validation_results = list(
        state.get("validation_results", [])
    )
    validation_results.append(validation_result)

    return {
        "validation_results": validation_results,
        "current_validation": validation_result,
        "current_agent": "patch_validation",
        "status": validation_result.get(
            "status",
            "patch_validation_completed"
        )
    }


def route_after_validation(state: ASEAState) -> str:
    """Retry failed patches or finish after successful validation."""

    validation = state.get("current_validation", {})

    if validation.get("status") == "PATCH_VALIDATED":
        return "finish"

    detected_bugs = state.get("detected_bugs", [])

    confirmed_bugs = [
        bug for bug in detected_bugs
        if bug.get("classification") == "confirmed_bug"
    ]

    if not confirmed_bugs:
        return "escalate"

    bug_key = get_bug_key(confirmed_bugs[0])

    retry_counts = state.get("retry_counts", {})
    attempts = retry_counts.get(bug_key, 0)
    max_retries = state.get("max_retries", 3)

    if attempts < max_retries:
        return "retry"

    return "escalate"


def escalation_node(state: ASEAState) -> dict[str, Any]:
    """Escalate a bug when all patch attempts have failed."""

    validation = state.get("current_validation", {})

    message = (
        "ASEA could not validate a successful patch within "
        f"{state.get('max_retries', 3)} attempts. "
        f"Last failure: {validation.get('reason', 'Tests or patch validation failed.')}"
    )

    errors = list(state.get("errors", []))
    errors.append(message)

    audit_log = list(state.get("audit_log", []))
    audit_log.append({
        "agent": "escalation",
        "event": "retry_limit_reached",
        "message": message
    })

    return {
        "errors": errors,
        "audit_log": audit_log,
        "current_agent": "escalation",
        "status": "escalated"
    }


def build_asea_graph():
    """Build and compile the ASEA multi-agent workflow."""

    workflow = StateGraph(ASEAState)

    workflow.add_node(
        "repository_analysis",
        repository_analysis_node
    )

    workflow.add_node(
        "bug_detector",
        bug_detector_node
    )

    workflow.add_node(
        "test_execution",
        test_execution_node
    )

    workflow.add_node(
        "patch_generation",
        patch_generation_node
    )

    workflow.add_node(
        "patch_validation",
        patch_validation_node
    )

    workflow.add_node(
        "escalation",
        escalation_node
    )

    workflow.add_edge(START, "repository_analysis")
    workflow.add_edge("repository_analysis", "bug_detector")
    workflow.add_edge("bug_detector", "test_execution")

    workflow.add_conditional_edges(
        "test_execution",
        route_after_tests,
        {
            "patch_generation": "patch_generation",
            "finish": END
        }
    )

    workflow.add_edge(
        "patch_generation",
        "patch_validation"
    )

    workflow.add_conditional_edges(
        "patch_validation",
        route_after_validation,
        {
            "retry": "patch_generation",
            "finish": END,
            "escalate": "escalation"
        }
    )

    workflow.add_conditional_edges(
        "patch_generation",
        lambda state: (
            "validate"
            if state.get("status") == "patch_generation_completed"
            else "retry"
        ),
        {
            "validate": "patch_validation",
            "retry": "patch_validation"
        }
    )

    workflow.add_edge("escalation", END)

    return workflow.compile()


def run_asea(repo_path: str):
    """Execute the complete ASEA workflow."""

    app = build_asea_graph()

    initial_state: ASEAState = {
        "repo_path": repo_path,
        "max_retries": 3,
        "retry_counts": {},
        "errors": [],
        "audit_log": [],
        "patches": [],
        "validation_results": [],
        "status": "initialized"
    }

    return app.invoke(initial_state)