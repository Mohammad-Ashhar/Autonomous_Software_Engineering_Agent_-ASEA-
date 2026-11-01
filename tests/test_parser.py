
from asea.tools.parser import PythonRepositoryParser


def test_parser_extracts_functions_and_classes(tmp_path):

    source = """
class Calculator:
    def divide(self, a, b):
        return a / b

def calculate_average(numbers):
    return sum(numbers) / len(numbers)

import math
"""

    file_path = tmp_path / "calculator.py"
    file_path.write_text(source)

    parser = PythonRepositoryParser()
    result = parser.parse_file(file_path)

    assert len(result["classes"]) == 1
    assert result["classes"][0]["name"] == "Calculator"

    assert len(result["functions"]) == 2
    assert result["functions"][0]["name"] == "divide"
    assert result["functions"][1]["name"] == "calculate_average"

    assert "import math" in result["imports"]
