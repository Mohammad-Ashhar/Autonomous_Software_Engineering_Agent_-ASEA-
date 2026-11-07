import pytest

from asea.integrations.repair_publisher import (
    publish_validated_repairs,
)


def test_block_publishing_when_workflow_incomplete():
    state = {
        "status": "COMPLETED_WITH_REMAINING_FAILURES",
    }

    with pytest.raises(
        RuntimeError,
        match="workflow did not complete successfully",
    ):
        publish_validated_repairs(state)


def test_block_publishing_when_final_tests_fail():
    state = {
        "status": "COMPLETED",
        "working_test_results": {
            "success": False,
        },
    }

    with pytest.raises(
        RuntimeError,
        match="final tests did not pass",
    ):
        publish_validated_repairs(state)


def test_block_publishing_when_test_results_missing():
    state = {
        "status": "COMPLETED",
    }

    with pytest.raises(
        RuntimeError,
        match="final tests did not pass",
    ):
        publish_validated_repairs(state)