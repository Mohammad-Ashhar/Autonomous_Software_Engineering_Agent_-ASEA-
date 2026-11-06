import json
import tempfile
from pathlib import Path
from typing import Any

from langgraph.graph import StateGraph, START, END

from asea.core.state import ASEAState
from asea.tools.analyzer import RepositoryAnalyzer
from asea.tools.test_runner import DockerTestRunner

from asea.agents.bug_detector import bug_detector_node
from asea.agents.patch_generator import PatchGeneratorAgent
from asea.agents.patch_validator import PatchValidatorAgent


# --------------------------------------------------
# 1. REPOSITORY ANALYSIS
# --------------------------------------------------

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


# --------------------------------------------------
# 2. BASELINE TEST EXECUTION
# --------------------------------------------------

def test_execution_node(state: ASEAState) -> dict[str, Any]:
    """Run the original repository tests before repairs."""

    repo_path = Path(state["repo_path"]).resolve()

    runner = DockerTestRunner()
    results = runner.run_tests(repo_path)

    return {
        "test_results": results,
        "current_agent": "test_execution",
        "status": "baseline_tests_completed"
    }


# --------------------------------------------------
# 3. CREATE CUMULATIVE WORKSPACE
# --------------------------------------------------

def prepare_workspace_node(state: ASEAState) -> dict[str, Any]:
    """Create one persistent working copy for all repairs."""

    repo_path = Path(state["repo_path"]).resolve()

    workspace_root = repo_path.parent / "workspaces"
    workspace_root.mkdir(parents=True, exist_ok=True)

    run_directory = Path(
        tempfile.mkdtemp(
            prefix="asea_run_",
            dir=str(workspace_root)
        )
    )

    working_repo = run_directory / "repository"

    validator = PatchValidatorAgent()

    validator.create_temporary_copy(
        repo_path,
        working_repo
    )

    print("ORIGINAL REPOSITORY:", repo_path)
    print("CUMULATIVE WORKSPACE:", working_repo)

    return {
        "working_repo_path": str(working_repo),
        "working_test_results": state.get("test_results", {}),
        "current_agent": "workspace_preparation",
        "status": "workspace_ready"
    }


# --------------------------------------------------
# 4. BUG PRIORITIZATION
# --------------------------------------------------

def get_bug_key(bug: dict[str, Any]) -> str:
    """Generate a stable identifier for each bug."""

    return (
        f"{bug.get('file', 'unknown')}::"
        f"{bug.get('line', 0)}::"
        f"{bug.get('title', 'unknown')}"
    )


def prioritize_bugs_node(state: ASEAState) -> dict[str, Any]:
    """Prioritize confirmed bugs by severity."""

    severity_order = {
        "high": 0,
        "medium": 1,
        "low": 2
    }

    detected_bugs = state.get("detected_bugs", [])

    unique_bugs = {}

    for bug in detected_bugs:

        if bug.get("classification") != "confirmed_bug":
            continue

        unique_bugs[get_bug_key(bug)] = bug

    prioritized_bugs = sorted(
        unique_bugs.values(),
        key=lambda bug: (
            severity_order.get(bug.get("severity"), 3),
            str(bug.get("file", "")),
            bug.get("line", 0)
        )
    )

    print("\nPRIORITIZED BUGS:")

    for index, bug in enumerate(prioritized_bugs, start=1):
        print(
            f"{index}. {bug.get('severity', 'unknown').upper()} - "
            f"{bug.get('title', 'Unknown bug')}"
        )

    return {
        "prioritized_bugs": prioritized_bugs,
        "current_agent": "bug_prioritization",
        "status": "bugs_prioritized"
    }


# --------------------------------------------------
# 5. SELECT NEXT BUG
# --------------------------------------------------

def select_next_bug_node(state: ASEAState) -> dict[str, Any]:
    """Select the next unprocessed bug."""

    prioritized_bugs = state.get("prioritized_bugs", [])
    processed_keys = set(state.get("processed_bug_keys", []))

    for bug in prioritized_bugs:

        bug_key = get_bug_key(bug)

        if bug_key not in processed_keys:

            print("\nSELECTED BUG:", bug.get("title"))

            return {
                "current_bug": bug,
                "current_validation": {},
                "current_agent": "bug_selector",
                "status": "bug_selected"
            }

    return {
        "current_bug": {},
        "current_agent": "bug_selector",
        "status": "all_bugs_processed"
    }


def route_after_selection(state: ASEAState) -> str:

    if state.get("current_bug"):
        return "patch_generation"

    return "finish"


# --------------------------------------------------
# 6. PATCH GENERATION
# --------------------------------------------------

def patch_generation_node(state: ASEAState) -> dict[str, Any]:
    """Generate a patch using the latest accepted source code."""

    repo_path = Path(state["repo_path"]).resolve()
    working_repo = Path(state["working_repo_path"]).resolve()

    bug = state.get("current_bug", {})

    if not bug:
        raise ValueError("No current bug selected.")

    bug_key = get_bug_key(bug)

    retry_counts = dict(state.get("retry_counts", {}))

    attempt = retry_counts.get(bug_key, 0) + 1
    max_retries = state.get("max_retries", 3)

    if attempt > max_retries:
        return {
            "status": "retry_limit_reached"
        }

    original_bug_file = Path(bug["file"]).resolve()

    try:
        relative_path = original_bug_file.relative_to(repo_path)
    except ValueError:
        raise ValueError(
            "Detected bug file is outside the original repository."
        )

    # IMPORTANT:
    # Generate the next patch using the cumulative working copy.
    working_bug_file = (
        working_repo / relative_path
    ).resolve()

    try:
        working_bug_file.relative_to(working_repo)
    except ValueError:
        raise ValueError(
            "Working bug file is outside the repair workspace."
        )

    if not working_bug_file.is_file():
        raise FileNotFoundError(
            f"Working bug file not found: {working_bug_file}"
        )

    previous_feedback = state.get("current_validation")

    generator = PatchGeneratorAgent()

    try:
        patch_result = generator.generate_patch(
            file_path=working_bug_file,
            bug=bug,
            previous_feedback=previous_feedback,
            attempt=attempt
        )

        if not isinstance(patch_result, dict):
            raise TypeError(
                "Patch generator must return a dictionary."
            )

        patch_result["attempt"] = attempt
        patch_result["bug_key"] = bug_key

        patches = list(state.get("patches", []))
        patches.append(patch_result)

        retry_counts[bug_key] = attempt

        return {
            "patches": patches,
            "retry_counts": retry_counts,
            "current_validation": {},
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
            "bug_key": bug_key,
            "attempt": attempt
        }

        errors = list(state.get("errors", []))

        errors.append(
            f"Patch generation attempt {attempt} "
            f"failed for {bug_key}: {error}"
        )

        return {
            "retry_counts": retry_counts,
            "current_validation": failed_result,
            "errors": errors,
            "current_agent": "patch_generation",
            "status": "patch_generation_failed"
        }


def route_after_generation(state: ASEAState) -> str:
    """Route successfully generated patches to human review."""

    if state.get("status") == "patch_generation_completed":
        return "review"

    bug_key = get_bug_key(state.get("current_bug", {}))

    attempts = state.get("retry_counts", {}).get(bug_key, 0)
    max_retries = state.get("max_retries", 3)

    if attempts < max_retries:
        return "retry"

    return "escalate"
def human_patch_review_node(state: ASEAState) -> dict[str, Any]:
    """Pause the workflow and request human approval for a patch."""

    patches = state.get("patches", [])
    bug = state.get("current_bug", {})

    if not patches:
        raise ValueError("No generated patch available for review.")

    patch = patches[-1]

    if patch.get("bug_key") != get_bug_key(bug):
        raise ValueError("The latest patch does not belong to the current bug.")

    print("\n" + "=" * 65)
    print("                 ASEA HUMAN CODE REVIEW")
    print("=" * 65)

    print("\nBUG:", bug.get("title", "Unknown bug"))
    print("SEVERITY:", bug.get("severity", "Unknown").upper())
    print("FILE:", patch.get("file", "Unknown"))
    print("ATTEMPT:", patch.get("attempt", 1))

    print("\nPROPOSED PATCH")
    print("-" * 65)
    print(patch.get("patch", "No patch content available."))
    print("-" * 65)

    while True:
        decision = input(
            "\nApprove this patch? [A]pprove / [R]eject: "
        ).strip().lower()

        if decision in ("a", "approve"):
            decision = "approved"
            feedback = ""
            break

        if decision in ("r", "reject"):
            decision = "rejected"

            feedback = input(
                "Enter the reason for rejection: "
            ).strip()

            if not feedback:
                feedback = "Patch rejected by reviewer without additional feedback."

            break

        print("Invalid choice. Please enter A or R.")

    bug_key = get_bug_key(bug)

    review_record = {
        "bug_key": bug_key,
        "bug_title": bug.get("title", "Unknown bug"),
        "decision": decision,
        "feedback": feedback,
        "attempt": patch.get("attempt", 1)
    }

    review_history = list(state.get("review_history", []))
    review_history.append(review_record)

    audit_log = list(state.get("audit_log", []))
    audit_log.append({
        "agent": "human_reviewer",
        "event": f"patch_{decision}",
        **review_record
    })

    print("\nREVIEW DECISION:", decision.upper())
    updates = {
        "patch_review_decision": decision,
        "patch_review_feedback": feedback,
        "review_history": review_history,
        "audit_log": audit_log,
        "current_agent": "human_reviewer",
        "status": f"patch_{decision}"
    }

    if decision == "rejected":
        updates["current_validation"] = {
            "status": "PATCH_REJECTED",
            "patch_applied": False,
            "tests_passed": False,
            "reason": feedback
        }

    return updates
    
def route_after_review(state: ASEAState) -> str:
    """Route the workflow based on the human review decision."""

    decision = state.get("patch_review_decision")

    if decision == "approved":
        return "validate"

    if decision == "rejected":
        bug_key = get_bug_key(state.get("current_bug", {}))

        attempts = state.get("retry_counts", {}).get(bug_key, 0)
        max_retries = state.get("max_retries", 3)

        if attempts < max_retries:
            return "retry"

        return "escalate"

    raise ValueError(f"Invalid patch review decision: {decision}")

# --------------------------------------------------
# 7. CUMULATIVE PATCH VALIDATION
# --------------------------------------------------

def patch_validation_node(state: ASEAState) -> dict[str, Any]:
    """Validate and preserve accepted cumulative patches."""

    repo_path = Path(state["repo_path"]).resolve()

    working_repo = Path(
        state["working_repo_path"]
    ).resolve()

    bug = state.get("current_bug", {})
    bug_key = get_bug_key(bug)

    patches = state.get("patches", [])

    if not patches or patches[-1].get("bug_key") != bug_key:

        validation_result = {
            "status": "PATCH_REJECTED",
            "patch_applied": False,
            "tests_passed": False,
            "regression_free": False,
            "reason": "No patch was generated for the current bug."
        }

    else:

        patch_result = patches[-1]

        validator = PatchValidatorAgent()

        try:
            validation_result = validator.validate_patch(
                repository_path=repo_path,
                patch_result=patch_result,
                working_repo_path=working_repo,
                baseline_test_results=state.get("working_test_results", {})
            )

        except Exception as error:

            validation_result = {
                "status": "PATCH_REJECTED",
                "patch_applied": False,
                "tests_passed": False,
                "regression_free": False,
                "reason": str(error)
            }

        validation_result["attempt"] = patch_result.get(
            "attempt", 1
        )

    validation_result["bug_key"] = bug_key
    validation_result["bug_title"] = bug.get("title", "Unknown bug")

    validation_results = list(
        state.get("validation_results", [])
    )

    validation_results.append(validation_result)

    updates = {
        "validation_results": validation_results,
        "current_validation": validation_result,
        "current_agent": "patch_validation",
        "status": validation_result.get(
            "status",
            "patch_validation_completed"
        )
    }

    if validation_result.get("status") == "PATCH_VALIDATED":

        processed_keys = list(
            state.get("processed_bug_keys", [])
        )

        repaired_bugs = list(
            state.get("repaired_bugs", [])
        )

        audit_log = list(state.get("audit_log", []))

        if bug_key not in processed_keys:

            processed_keys.append(bug_key)

            repaired_bugs.append({
                "bug_key": bug_key,
                "title": bug.get("title"),
                "severity": bug.get("severity"),
                "file": bug.get("file"),
                "status": "REPAIRED",
                "attempts": validation_result.get("attempt", 1)
            })

            audit_log.append({
                "agent": "patch_validation",
                "event": "bug_repaired",
                "bug_key": bug_key
            })

        updates.update({
            "processed_bug_keys": processed_keys,
            "repaired_bugs": repaired_bugs,
            "working_test_results": validation_result.get(
                "test_results", {}
            ),
            "audit_log": audit_log
        })

    return updates


def route_after_validation(state: ASEAState) -> str:
    """Decide whether to retry, escalate or select the next bug."""

    validation = state.get("current_validation", {})

    if validation.get("status") == "PATCH_VALIDATED":
        return "next_bug"

    bug_key = get_bug_key(state.get("current_bug", {}))

    attempts = state.get("retry_counts", {}).get(bug_key, 0)
    max_retries = state.get("max_retries", 3)

    if attempts < max_retries:
        return "retry"

    return "escalate"


# --------------------------------------------------
# 8. ESCALATION
# --------------------------------------------------

def escalation_node(state: ASEAState) -> dict[str, Any]:
    """Escalate a bug after all repair attempts fail."""

    bug = state.get("current_bug", {})
    bug_key = get_bug_key(bug)

    validation = state.get("current_validation", {})

    message = (
        f"ASEA could not repair bug: {bug.get('title', 'Unknown bug')}. "
        f"Maximum attempts: {state.get('max_retries', 3)}. "
        f"Last failure: {validation.get('reason', 'Validation failed.')}"
    )

    errors = list(state.get("errors", []))
    errors.append(message)

    escalated_bugs = list(
    state.get("escalated_bugs", [])
    )

    already_escalated = any(
        item.get("bug_key") == bug_key
        for item in escalated_bugs
    )

    if not already_escalated:
        escalated_bugs.append({
            "bug_key": bug_key,
            "title": bug.get("title"),
            "severity": bug.get("severity"),
            "file": bug.get("file"),
            "status": "ESCALATED",
            "reason": validation.get("reason", "Validation failed.")
        })
    processed_keys = list(
        state.get("processed_bug_keys", [])
    )

    if bug_key not in processed_keys:
        processed_keys.append(bug_key)

    audit_log = list(state.get("audit_log", []))

    audit_log.append({
        "agent": "escalation",
        "event": "bug_escalated",
        "bug_key": bug_key,
        "message": message
    })

    return {
        "errors": errors,
        "escalated_bugs": escalated_bugs,
        "processed_bug_keys": processed_keys,
        "audit_log": audit_log,
        "current_agent": "escalation",
        "status": "bug_escalated"
    }


# --------------------------------------------------
# 9. FINAL VALIDATION
# --------------------------------------------------

def final_validation_node(state: ASEAState) -> dict[str, Any]:
    """Run the complete test suite on the final working repository."""

    working_repo = Path(
        state["working_repo_path"]
    ).resolve()

    runner = DockerTestRunner()

    results = runner.run_tests(working_repo)

    final_status = (
        "COMPLETED"
        if results.get("success", False)
        else "COMPLETED_WITH_REMAINING_FAILURES"
    )

    return {
        "working_test_results": results,
        "current_agent": "final_validation",
        "status": final_status
    }


# --------------------------------------------------
# 10. BUILD LANGGRAPH
# --------------------------------------------------

def build_asea_graph():
    """Build and compile the complete ASEA workflow."""

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
        "workspace_preparation",
        prepare_workspace_node
    )

    workflow.add_node(
        "bug_prioritization",
        prioritize_bugs_node
    )

    workflow.add_node(
        "bug_selector",
        select_next_bug_node
    )

    workflow.add_node(
        "patch_generation",
        patch_generation_node
    )

    workflow.add_node(
        "human_patch_review",
        human_patch_review_node
    )

    workflow.add_node(
        "patch_validation",
        patch_validation_node
    )

    workflow.add_node(
        "escalation",
        escalation_node
    )

    workflow.add_node(
        "final_validation",
        final_validation_node
    )

    # Initial workflow.
    workflow.add_edge(START, "repository_analysis")
    workflow.add_edge("repository_analysis", "bug_detector")
    workflow.add_edge("bug_detector", "test_execution")
    workflow.add_edge("test_execution", "workspace_preparation")
    workflow.add_edge("workspace_preparation", "bug_prioritization")
    workflow.add_edge("bug_prioritization", "bug_selector")

    # Select the next bug.
    workflow.add_conditional_edges(
        "bug_selector",
        route_after_selection,
        {
            "patch_generation": "patch_generation",
            "finish": "final_validation"
        }
    )

        # Generate patches and request human review.
    workflow.add_conditional_edges(
        "patch_generation",
        route_after_generation,
        {
            "review": "human_patch_review",
            "retry": "patch_generation",
            "escalate": "escalation"
        }
    )

    # Route according to the human review decision.
    workflow.add_conditional_edges(
        "human_patch_review",
        route_after_review,
        {
            "validate": "patch_validation",
            "retry": "patch_generation",
            "escalate": "escalation"
        }
    )

    # Validate patches.
    workflow.add_conditional_edges(
        "patch_validation",
        route_after_validation,
        {
            "retry": "patch_generation",
            "next_bug": "bug_selector",
            "escalate": "escalation"
        }
    )

    # Continue after escalation.
    workflow.add_edge("escalation", "bug_selector")

    # Finish after all bugs have been processed.
    workflow.add_edge("final_validation", END)

    return workflow.compile()


# --------------------------------------------------
# 11. RUN ASEA
# --------------------------------------------------

def run_asea(repo_path: str):
    """Execute the complete multi-bug ASEA workflow."""

    app = build_asea_graph()

    initial_state: ASEAState = {
        "repo_path": repo_path,

        "max_retries": 3,
        "retry_counts": {},

        "prioritized_bugs": [],
        "current_bug": {},
        "processed_bug_keys": [],

        "repaired_bugs": [],
        "escalated_bugs": [],

        "working_repo_path": "",
        "working_test_results": {},

        "errors": [],
        "audit_log": [],

        "patches": [],
        "validation_results": [],

        "status": "initialized"
    }

    return app.invoke(
        initial_state,
        config={"recursion_limit": 1000}
    )