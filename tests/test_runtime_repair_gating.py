from asea.core.graph import prioritize_bugs_node


def test_mixed_runtime_repository_allows_repair_with_combined_baseline():
    finding = {
        "file": "src/service.js",
        "line": 4,
        "title": "Incorrect calculation",
        "severity": "high",
        "classification": "confirmed_bug",
    }

    result = prioritize_bugs_node({
        "detected_bugs": [finding],
        "test_results": {
            "status": "PASSED",
            "success": True,
            "runtimes": {
                "python": {"status": "PASSED", "success": True},
                "javascript": {"status": "PASSED", "success": True},
            },
        },
    })

    assert result["prioritized_bugs"] == [finding]
    assert result["repair_blocked_bugs"] == []


def test_mixed_runtime_repository_blocks_repair_if_any_baseline_fails():
    finding = {
        "file": "src/service.js",
        "line": 4,
        "title": "Incorrect calculation",
        "severity": "high",
        "classification": "confirmed_bug",
    }

    result = prioritize_bugs_node({
        "detected_bugs": [finding],
        "test_results": {
            "status": "FAILED",
            "success": False,
            "runtimes": {
                "python": {"status": "PASSED", "success": True},
                "javascript": {"status": "NO_TESTS", "success": False},
            },
        },
    })

    assert result["prioritized_bugs"] == []
    assert len(result["repair_blocked_bugs"]) == 1
    assert (
        result["repair_blocked_bugs"][0]["repair_status"]
        == "unsupported_repository_test_environment"
    )
