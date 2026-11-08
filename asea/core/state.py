from typing import Any, TypedDict


class ASEAState(TypedDict, total=False):

    # Repository information
    repo_path: str
    repo_url: str
    repo_map: dict[str, Any]
    repo_inventory: dict[str, Any]

    # Cumulative repair workspace
    working_repo_path: str
    working_test_results: dict[str, Any]

    # Initial analysis
    baseline_results: dict[str, Any]
    static_findings: list[dict[str, Any]]

    # AI bug detection
    detected_bugs: list[dict[str, Any]]
    analysis_status: str
    analysis_summary: dict[str, Any]
    analysis_errors: list[dict[str, Any]]
    file_analysis_results: list[dict[str, Any]]

    # Multi-bug prioritization and tracking
    prioritized_bugs: list[dict[str, Any]]
    repair_blocked_bugs: list[dict[str, Any]]
    current_bug: dict[str, Any]
    processed_bug_keys: list[str]
    repaired_bugs: list[dict[str, Any]]
    escalated_bugs: list[dict[str, Any]]

    # Patch generation
    patches: list[dict[str, Any]]

    # Human review and approval
    review_mode: str
    patch_review_decision: str
    patch_review_feedback: str
    review_history: list[dict[str, Any]]

    # Test execution
    test_results: dict[str, Any]

    # Patch validation
    validation_results: list[dict[str, Any]]
    current_validation: dict[str, Any]

    # Retry management
    retry_counts: dict[str, int]
    max_retries: int

    # Workflow management
    status: str
    current_agent: str
    errors: list[str]

    # Audit information
    audit_log: list[dict[str, Any]]