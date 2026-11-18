from pathlib import Path

from asea.tools.dependency_analyzer import PythonDependencyAnalyzer


def _write_module(root: Path, relative: str, source: str) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(source, encoding="utf-8")


def test_detects_too_many_arguments_across_relative_import(tmp_path):
    _write_module(tmp_path, "sample/__init__.py", "")
    _write_module(
        tmp_path,
        "sample/api.py",
        "def send(user, retries=1):\n    return user\n",
    )
    _write_module(
        tmp_path,
        "sample/service.py",
        "from .api import send as dispatch\n"
        "def run():\n"
        "    return dispatch('alice', 2, 3)\n",
    )

    result = PythonDependencyAnalyzer().analyze(tmp_path)

    assert result["module_count"] == 3
    assert result["dependency_map"]["sample.service"] == ["sample.api"]
    finding = result["cross_file_findings"][0]
    assert finding["title"] == "Cross-file function call signature mismatch"
    assert finding["line"] == 3
    assert finding["related_file"].endswith("sample/api.py")
    assert finding["related_line"] == 1
    assert "at most 2" in finding["description"]


def test_detects_missing_required_argument_through_module_alias(tmp_path):
    _write_module(tmp_path, "sample/__init__.py", "")
    _write_module(
        tmp_path,
        "sample/api.py",
        "def send(user, channel):\n    return user, channel\n",
    )
    _write_module(
        tmp_path,
        "sample/service.py",
        "import sample.api as api\n"
        "def run():\n"
        "    return api.send('alice')\n",
    )

    result = PythonDependencyAnalyzer().analyze(tmp_path)

    assert len(result["cross_file_findings"]) == 1
    assert "missing required argument" in (
        result["cross_file_findings"][0]["description"]
    )


def test_keyword_arguments_are_checked_against_target_signature(tmp_path):
    _write_module(tmp_path, "sample/__init__.py", "")
    _write_module(
        tmp_path,
        "sample/api.py",
        "def send(user):\n    return user\n",
    )
    _write_module(
        tmp_path,
        "sample/service.py",
        "from sample.api import send\n"
        "def run():\n"
        "    return send('alice', channel='email')\n",
    )

    result = PythonDependencyAnalyzer().analyze(tmp_path)

    assert len(result["cross_file_findings"]) == 1
    assert "unexpected keyword" in (
        result["cross_file_findings"][0]["description"]
    )


def test_valid_cross_file_calls_are_not_reported(tmp_path):
    _write_module(tmp_path, "sample/__init__.py", "")
    _write_module(
        tmp_path,
        "sample/api.py",
        "def send(user, channel='email'):\n    return user, channel\n",
    )
    _write_module(
        tmp_path,
        "sample/service.py",
        "from .api import send\n"
        "def run():\n"
        "    return send('alice', channel='sms')\n",
    )

    result = PythonDependencyAnalyzer().analyze(tmp_path)

    assert result["cross_file_findings"] == []


def test_starred_arguments_are_not_guessed(tmp_path):
    _write_module(tmp_path, "sample/__init__.py", "")
    _write_module(
        tmp_path,
        "sample/api.py",
        "def send(user):\n    return user\n",
    )
    _write_module(
        tmp_path,
        "sample/service.py",
        "from .api import send\n"
        "def run(args):\n"
        "    return send(*args)\n",
    )

    result = PythonDependencyAnalyzer().analyze(tmp_path)

    assert result["cross_file_findings"] == []


def test_external_imports_do_not_create_project_dependencies(tmp_path):
    _write_module(
        tmp_path,
        "sample.py",
        "import json\n"
        "def serialize(value):\n"
        "    return json.dumps(value)\n",
    )

    result = PythonDependencyAnalyzer().analyze(tmp_path)

    assert result["dependency_map"] == {"sample": []}
    assert result["cross_file_findings"] == []


def test_detects_duplicate_values_for_imported_function(tmp_path):
    _write_module(tmp_path, "sample/__init__.py", "")
    _write_module(
        tmp_path,
        "sample/api.py",
        "def send(user):\n    return user\n",
    )
    _write_module(
        tmp_path,
        "sample/service.py",
        "from .api import send\n"
        "def run():\n"
        "    return send('alice', user='bob')\n",
    )

    result = PythonDependencyAnalyzer().analyze(tmp_path)

    assert len(result["cross_file_findings"]) == 1
    assert "multiple values" in result["cross_file_findings"][0]["description"]


def test_detects_positional_only_argument_passed_as_keyword(tmp_path):
    _write_module(tmp_path, "sample/__init__.py", "")
    _write_module(
        tmp_path,
        "sample/api.py",
        "def send(user, /):\n    return user\n",
    )
    _write_module(
        tmp_path,
        "sample/service.py",
        "from .api import send\n"
        "def run():\n"
        "    return send(user='alice')\n",
    )

    result = PythonDependencyAnalyzer().analyze(tmp_path)

    assert len(result["cross_file_findings"]) == 1
    assert "missing required argument" in (
        result["cross_file_findings"][0]["description"]
    )


def test_resolves_unaliased_dotted_module_import(tmp_path):
    _write_module(tmp_path, "sample/__init__.py", "")
    _write_module(
        tmp_path,
        "sample/api.py",
        "def send(user):\n    return user\n",
    )
    _write_module(
        tmp_path,
        "sample/service.py",
        "import sample.api\n"
        "def run():\n"
        "    return sample.api.send('alice', 'email')\n",
    )

    result = PythonDependencyAnalyzer().analyze(tmp_path)

    assert len(result["cross_file_findings"]) == 1
    assert result["cross_file_findings"][0]["dependency"] == "sample.api"

def test_dependency_analysis_skips_python_symlinks_outside_repository(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    (project / "main.py").write_text(
        "def run():\n    return 1\n",
        encoding="utf-8",
    )
    outside = tmp_path / "outside.py"
    outside.write_text("def broken(:\n", encoding="utf-8")
    (project / "linked.py").symlink_to(outside)

    result = PythonDependencyAnalyzer().analyze(project)

    assert result["module_count"] == 1
    assert result["parse_errors"] == []
    assert result["dependency_map"] == {"main": []}

def test_dependency_analyzer_respects_declared_latin1_encoding(tmp_path):
    _write_module(tmp_path, "sample/__init__.py", "")
    legacy = tmp_path / "sample" / "api.py"
    legacy.write_bytes(
        b"# coding: latin-1\\n"
        b"def caf\\xe9(user):\\n"
        b"    return 'ol\\xe9' + user\\n"
    )
    _write_module(
        tmp_path,
        "sample/service.py",
        "from .api import café\\n"
        "def run():\\n"
        "    return café()\\n",
    )

    result = PythonDependencyAnalyzer().analyze(tmp_path)

    assert result["parse_errors"] == []
    assert result["module_count"] == 3
    assert result["dependency_map"]["sample.service"] == ["sample.api"]


def test_dependency_analyzer_reports_unknown_source_encoding(tmp_path):
    _write_module(tmp_path, "sample/__init__.py", "")
    invalid = tmp_path / "sample" / "legacy.py"
    invalid.write_bytes(
        b"# coding: made-up-encoding\\n"
        b"def example():\\n"
        b"    return 1\\n"
    )

    result = PythonDependencyAnalyzer().analyze(tmp_path)

    assert result["module_count"] == 1
    assert len(result["parse_errors"]) == 1
    assert result["parse_errors"][0]["file"] == "sample/legacy.py"
