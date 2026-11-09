from pathlib import Path

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
