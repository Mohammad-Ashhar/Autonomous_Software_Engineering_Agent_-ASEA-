"""Read Python source using its declared encoding with a bounded file size."""

import io
import tokenize
from pathlib import Path

MAX_PYTHON_FILE_SIZE_BYTES = 2 * 1024 * 1024


def read_python_source(file_path: str | Path) -> str:
    """Decode a Python source file according to PEP 263.

    Raises OSError for filesystem failures, ValueError for oversized files,
    and SyntaxError or UnicodeDecodeError for invalid encoding declarations
    or source bytes.
    """
    path = Path(file_path)
    if path.stat().st_size > MAX_PYTHON_FILE_SIZE_BYTES:
        raise ValueError("File exceeds the 2 MiB analysis limit.")

    source_code = path.read_bytes()
    if len(source_code) > MAX_PYTHON_FILE_SIZE_BYTES:
        raise ValueError("File exceeds the 2 MiB analysis limit.")

    encoding, _ = tokenize.detect_encoding(
        io.BytesIO(source_code).readline
    )
    return source_code.decode(encoding)
