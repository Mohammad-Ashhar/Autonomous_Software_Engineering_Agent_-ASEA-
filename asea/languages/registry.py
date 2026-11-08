"""Central registry for file-extension to language-adapter resolution."""

from pathlib import Path

from asea.languages.base import LanguageAdapter
from asea.languages.python import PythonAdapter
from asea.languages.web import (
    javascript_adapter,
    jsx_adapter,
    tsx_adapter,
    typescript_adapter,
)


class LanguageRegistry:
    def __init__(self, adapters: list[LanguageAdapter] | None = None):
        self._adapters: dict[str, LanguageAdapter] = {}
        for adapter in adapters or []:
            self.register(adapter)

    def register(self, adapter: LanguageAdapter) -> None:
        for extension in adapter.extensions:
            normalized = extension.lower()
            existing = self._adapters.get(normalized)
            if existing is not None and existing.name != adapter.name:
                raise ValueError(
                    f"Extension {normalized} is already registered "
                    f"for {existing.name}."
                )
            self._adapters[normalized] = adapter

    def resolve(self, file_path: str | Path) -> LanguageAdapter | None:
        return self._adapters.get(Path(file_path).suffix.lower())

    def supported_extensions(self) -> tuple[str, ...]:
        return tuple(sorted(self._adapters))

    def adapters(self) -> list[LanguageAdapter]:
        unique = {adapter.name: adapter for adapter in self._adapters.values()}
        return list(unique.values())


def get_default_registry() -> LanguageRegistry:
    return LanguageRegistry([
        PythonAdapter(),
        javascript_adapter(),
        jsx_adapter(),
        typescript_adapter(),
        tsx_adapter(),
    ])
