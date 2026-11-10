from asea.tools.test_runner import DockerTestRunner


def make_runner():
    return DockerTestRunner.__new__(DockerTestRunner)


def test_parse_results_counts_singular_collection_error():
    result = make_runner().parse_results(
        "ERROR collecting tests/test_sample.py\\n"
        "1 error in 0.02s",
        2,
    )

    assert result["status"] == "FAILED"
    assert result["errors"] == 1
    assert result["total"] == 1


def test_parse_results_counts_all_pytest_summary_categories():
    output = (
        "2 passed, 1 failed, 1 error, 1 skipped, "
        "1 xfailed, 1 xpassed in 0.20s\\n"
        "FAILED tests/test_math.py::test_subtract - AssertionError"
    )

    result = make_runner().parse_results(output, 1)

    assert result["status"] == "FAILED"
    assert result["passed"] == 2
    assert result["failed"] == 1
    assert result["errors"] == 1
    assert result["skipped"] == 1
    assert result["xfailed"] == 1
    assert result["xpassed"] == 1
    assert result["total"] == 7
    assert result["failed_tests"] == [
        "tests/test_math.py::test_subtract"
    ]


def test_parse_results_marks_empty_suite():
    result = make_runner().parse_results("no tests ran", 5)

    assert result["status"] == "NO_TESTS"
    assert result["total"] == 0
    assert result["errors"] == 0
