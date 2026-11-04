from typing import TypedDict, Any


class ASEAState(TypedDict, total=False):

    # Repository information
    repo_path: str
    repo_url: str
    repo_map: dict[str, Any]

    # Initial analysis
    baseline_results: dict[str, Any]
    static_findings: list[dict[str, Any]]

    # AI bug detection
    detected_bugs: list[dict[str, Any]]

    # Patch generation
    patches: list[dict[str, Any]]

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