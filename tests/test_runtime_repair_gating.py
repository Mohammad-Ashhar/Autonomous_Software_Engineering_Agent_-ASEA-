from asea.core.graph import prioritize_bugs_node


def test_mixed_runtime_repository_blocks_all_automatic_repairs():
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
            "status": "UNSUPPORTED",
            "success": False,
        },
    })

    assert result["prioritized_bugs"] == []
    assert len(result["repair_blocked_bugs"]) == 1
    assert (
        result["repair_blocked_bugs"][0]["repair_status"]
        == "unsupported_repository_test_environment"
    )
