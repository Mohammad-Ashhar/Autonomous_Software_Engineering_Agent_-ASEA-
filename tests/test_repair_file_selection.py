from pathlib import Path

from asea.integrations.repair_publisher import (
    get_approved_repair_files,
)


def create_repair_state(
    original_file: Path,
    attempt: int = 1,
    decision: str = "approved",
    status: str = "REPAIRED",
):
    return {
        "repaired_bugs": [
            {
                "bug_key": "BUG-001",
                "status": status,
                "attempts": attempt,
                "file": str(original_file),
            }
        ],
        "review_history": [
            {
                "bug_key": "BUG-001",
                "attempt": attempt,
                "decision": decision,
            }
        ],
    }


def test_select_approved_changed_file(tmp_path):
    original = tmp_path / "original"
    working = tmp_path / "working"

    original.mkdir()
    working.mkdir()

    original_file = original / "logic.py"
    working_file = working / "logic.py"

    original_file.write_text("return True")
    working_file.write_text("return False")

    state = create_repair_state(original_file)

    result = get_approved_repair_files(
        state,
        original,
        working,
    )

    assert result == [Path("logic.py")]


def test_reject_unapproved_file(tmp_path):
    original = tmp_path / "original"
    working = tmp_path / "working"

    original.mkdir()
    working.mkdir()

    original_file = original / "logic.py"
    original_file.write_text("return True")

    (working / "logic.py").write_text("return False")

    state = create_repair_state(
        original_file,
        decision="rejected",
    )

    result = get_approved_repair_files(
        state,
        original,
        working,
    )

    assert result == []


def test_reject_mismatched_attempt(tmp_path):
    original = tmp_path / "original"
    working = tmp_path / "working"

    original.mkdir()
    working.mkdir()

    original_file = original / "logic.py"
    original_file.write_text("return True")

    (working / "logic.py").write_text("return False")

    state = create_repair_state(
        original_file,
        attempt=2,
    )

    state["review_history"][0]["attempt"] = 1

    result = get_approved_repair_files(
        state,
        original,
        working,
    )

    assert result == []


def test_reject_unchanged_file(tmp_path):
    original = tmp_path / "original"
    working = tmp_path / "working"

    original.mkdir()
    working.mkdir()

    original_file = original / "logic.py"
    original_file.write_text("return True")

    (working / "logic.py").write_text("return True")

    state = create_repair_state(original_file)

    result = get_approved_repair_files(
        state,
        original,
        working,
    )

    assert result == []


def test_reject_sensitive_file(tmp_path):
    original = tmp_path / "original"
    working = tmp_path / "working"

    original.mkdir()
    working.mkdir()

    original_file = original / ".env"
    original_file.write_text("SECRET=value")

    (working / ".env").write_text("SECRET=changed")

    state = create_repair_state(original_file)

    result = get_approved_repair_files(
        state,
        original,
        working,
    )

    assert result == []


def test_reject_file_outside_original_repository(tmp_path):
    original = tmp_path / "original"
    working = tmp_path / "working"

    original.mkdir()
    working.mkdir()

    outside_file = tmp_path / "outside.py"
    outside_file.write_text("print('unsafe')")

    state = create_repair_state(outside_file)

    result = get_approved_repair_files(
        state,
        original,
        working,
    )

    assert result == []


def test_reject_missing_working_file(tmp_path):
    original = tmp_path / "original"
    working = tmp_path / "working"

    original.mkdir()
    working.mkdir()

    original_file = original / "logic.py"
    original_file.write_text("return True")

    state = create_repair_state(original_file)

    result = get_approved_repair_files(
        state,
        original,
        working,
    )

    assert result == []