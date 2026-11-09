import pytest

from asea.agents.patch_validator import PatchValidatorAgent


def test_apply_patch_updates_multiple_files_atomically(tmp_path):
    (tmp_path / "first.py").write_text("value = 1\n", encoding="utf-8")
    (tmp_path / "second.py").write_text("value = 10\n", encoding="utf-8")

    patch = """--- a/first.py
+++ b/first.py
@@ -1 +1 @@
-value = 1
+value = 2
--- a/second.py
+++ b/second.py
@@ -1 +1 @@
-value = 10
+value = 20
"""

    PatchValidatorAgent().apply_patch(tmp_path, patch)

    assert (tmp_path / "first.py").read_text(encoding="utf-8") == "value = 2\n"
    assert (tmp_path / "second.py").read_text(encoding="utf-8") == "value = 20\n"


def test_apply_patch_rejects_path_traversal_without_modifying_files(tmp_path):
    target = tmp_path / "safe.py"
    target.write_text("value = 1\n", encoding="utf-8")
    patch = """--- a/../safe.py
+++ b/../safe.py
@@ -1 +1 @@
-value = 1
+value = 2
"""

    with pytest.raises(ValueError, match="outside the repository"):
        PatchValidatorAgent().apply_patch(tmp_path, patch)

    assert target.read_text(encoding="utf-8") == "value = 1\n"


def test_apply_patch_rejects_new_file_creation_without_modification(tmp_path):
    target = tmp_path / "safe.py"
    target.write_text("value = 1\n", encoding="utf-8")
    patch = """--- /dev/null
+++ b/new.py
@@ -0,0 +1 @@
+value = 2
"""

    with pytest.raises(ValueError, match="Creating or deleting"):
        PatchValidatorAgent().apply_patch(tmp_path, patch)

    assert target.read_text(encoding="utf-8") == "value = 1\n"


def test_apply_patch_rejects_malformed_diff_headers(tmp_path):
    (tmp_path / "safe.py").write_text("value = 1\n", encoding="utf-8")

    with pytest.raises(ValueError, match="Malformed unified diff"):
        PatchValidatorAgent().apply_patch(
            tmp_path,
            "--- a/safe.py\n@@ -1 +1 @@\n-value = 1\n+value = 2\n",
        )



def test_failed_second_file_keeps_entire_workspace_unchanged(tmp_path):
    first = tmp_path / "first.py"
    second = tmp_path / "second.py"
    first.write_bytes(b"value = 1\r\n")
    second.write_bytes(b"value = 10\r\n")
    before = {
        first.name: first.read_bytes(),
        second.name: second.read_bytes(),
    }
    patch = """--- a/first.py
+++ b/first.py
@@ -1 +1 @@
-value = 1
+value = 2
--- a/second.py
+++ b/second.py
@@ -1 +1 @@
-value = DOES_NOT_MATCH
+value = 20
"""

    with pytest.raises(ValueError, match="Patch cannot be applied"):
        PatchValidatorAgent().apply_patch(tmp_path, patch)

    assert first.read_bytes() == before[first.name]
    assert second.read_bytes() == before[second.name]


def test_crlf_file_is_normalized_only_after_successful_patch(tmp_path):
    target = tmp_path / "legacy.py"
    target.write_bytes(b"value = 1\r\n")
    patch = """--- a/legacy.py
+++ b/legacy.py
@@ -1 +1 @@
-value = 1
+value = 2
"""

    PatchValidatorAgent().apply_patch(tmp_path, patch)

    assert target.read_bytes() == b"value = 2\n"


def test_symlink_target_cannot_escape_repository(tmp_path):
    outside = tmp_path.parent / "outside_asea.py"
    outside.write_text("value = 1\n", encoding="utf-8")
    link = tmp_path / "linked.py"
    try:
        link.symlink_to(outside)
    except (OSError, NotImplementedError):
        pytest.skip("Symlinks are unavailable on this platform")

    patch = """--- a/linked.py
+++ b/linked.py
@@ -1 +1 @@
-value = 1
+value = 2
"""

    with pytest.raises(ValueError, match="outside the repository"):
        PatchValidatorAgent().apply_patch(tmp_path, patch)

    assert outside.read_text(encoding="utf-8") == "value = 1\n"
