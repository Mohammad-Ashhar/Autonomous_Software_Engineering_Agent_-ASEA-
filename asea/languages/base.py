"""Contracts shared by language-specific analysis and repair adapters."""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol


@dataclass
class ParsedSource:
    path: Path
    language: str
    functions: list[dict] = field(default_factory=list)
    classes: list[dict] = field(default_factory=list)
    imports: list[str] = field(default_factory=list)
    syntax_valid: bool = True
    syntax_error: str | None = None


class LanguageAdapter(Protocol):
    name: str
    extensions: tuple[str, ...]
    supports_repair: bool

    def parse(self, file_path: Path, source: str | None = None) -> ParsedSource:
        """Parse one source file and return normalized symbol information."""

    def validate_source(self, source: str, file_path: Path) -> None:
        """Raise ValueError when generated source is syntactically invalid."""

    def repair_instructions(self) -> str:
        """Return language-specific instructions for patch generation."""
