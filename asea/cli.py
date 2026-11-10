from pathlib import Path
from time import perf_counter

import typer
from rich.console import Console
from rich.table import Table

from asea.core.graph import run_asea
from asea.integrations.repair_publisher import publish_validated_repairs
from asea.tools.analyzer import RepositoryAnalyzer
from asea.tools.repository import RepositoryManager

app = typer.Typer(
    name="asea",
    help="Autonomous Software Engineering Agent",
    no_args_is_help=True,
)
console = Console()


def resolve_repository(repository: str) -> Path:
    """Resolve a local repository or clone an HTTPS GitHub repository."""
    manager = RepositoryManager()
    if repository.startswith("https://"):
        console.print("[cyan]Cloning GitHub repository...[/cyan]")
        return manager.clone_repository(repository)
    return manager.load_local_repository(repository)


@app.command()
def analyze(
    repository: str = typer.Argument(
        ...,
        help="Local repository path or HTTPS GitHub repository URL.",
    ),
):
    """Analyze a repository and generate its Python code map."""
    started = perf_counter()
    try:
        repo_path = resolve_repository(repository)
        analyzer = RepositoryAnalyzer()
        if repository.startswith("https://"):
            result = analyzer.analyze_cloned_repository(repo_path)
        else:
            result = analyzer.analyze_local_repository(repo_path)

        table = Table(title="ASEA Repository Analysis")
        table.add_column("Metric", style="cyan")
        table.add_column("Result", style="green")
        table.add_row("Repository", result["repository"])
        table.add_row("Path", result["path"])
        table.add_row("Python files", str(result["total_files"]))
        table.add_row("Functions", str(result["total_functions"]))
        table.add_row("Classes", str(result["total_classes"]))
        table.add_row("Imports", str(result["total_imports"]))
        table.add_row("Repository map", result["output_file"])
        table.add_row("Elapsed time", f"{perf_counter() - started:.2f}s")
        console.print(table)
        console.print("[bold green]Analysis completed successfully.[/bold green]")
    except Exception as error:  # noqa: BLE001
        console.print(f"[bold red]Analysis failed:[/bold red] {error}")
        raise typer.Exit(code=1) from error


@app.command()
def repair(
    repository: str = typer.Argument(
        ...,
        help="Local repository path or HTTPS GitHub repository URL.",
    ),
    publish: bool = typer.Option(
        False,
        "--publish",
        help="Publish a successfully validated repair through a GitHub PR.",
    ),
):
    """Detect bugs, generate patches, review and validate repairs."""
    started = perf_counter()
    try:
        repo_path = resolve_repository(repository)
        console.print("\n[bold cyan]Starting ASEA repair workflow...[/bold cyan]")
        console.print(f"Repository: {repo_path}\n")
        state = run_asea(str(repo_path))
        status = state.get("status", "UNKNOWN")

        table = Table(title="ASEA Workflow Summary")
        table.add_column("Metric", style="cyan")
        table.add_column("Result", style="green")
        table.add_row("Status", status)
        table.add_row("Detected bugs", str(len(state.get("detected_bugs", []))))
        table.add_row("Repaired bugs", str(len(state.get("repaired_bugs", []))))
        table.add_row("Escalated bugs", str(len(state.get("escalated_bugs", []))))
        table.add_row("Patch attempts", str(len(state.get("patches", []))))
        test_results = state.get("working_test_results") or {}
        table.add_row(
            "Final tests",
            f"{test_results.get('passed', 0)} passed, "
            f"{test_results.get('failed', 0)} failed",
        )
        table.add_row("Elapsed time", f"{perf_counter() - started:.2f}s")
        console.print(table)

        if status != "COMPLETED":
            console.print(
                "[bold yellow]Repair is not fully validated. "
                "Publishing is disabled.[/bold yellow]"
            )
            raise typer.Exit(code=2)

        console.print(
            "[bold green]All final validation checks passed.[/bold green]"
        )
        if not publish:
            console.print(
                "[green]Validated changes remain in the ASEA workspace. "
                "No GitHub pull request was requested.[/green]"
            )
            return

        console.print("\n[bold cyan]Publishing validated repair...[/bold cyan]")
        result = publish_validated_repairs(state)
        console.print("[bold green]Publishing completed.[/bold green]")
        console.print(f"Branch: {result.get('branch')}")
        console.print(f"Commit: {result.get('commit')}")
        pull_request = result.get("pull_request", {})
        console.print(f"Pull Request: {pull_request.get('url', 'Not available')}")
    except typer.Exit:
        raise
    except Exception as error:  # noqa: BLE001
        console.print(f"\n[bold red]Repair workflow failed:[/bold red] {error}")
        raise typer.Exit(code=1) from error


@app.command()
def status():
    """Display ASEA version and required integration configuration."""
    from importlib.metadata import PackageNotFoundError, version

    from asea.core.config import get_settings

    settings = get_settings()
    try:
        installed_version = version("asea")
    except PackageNotFoundError:
        installed_version = "source checkout"

    table = Table(title="ASEA Environment Status")
    table.add_column("Configuration", style="cyan")
    table.add_column("Value", style="green")
    table.add_row("ASEA version", installed_version)
    table.add_row("Python requirement", "3.12+")
    table.add_row("LLM model", settings.ollama_model)
    table.add_row("LLM endpoint", settings.ollama_base_url)
    table.add_row(
        "GitHub repository",
        settings.github_repository or "Not configured",
    )
    table.add_row(
        "GitHub token",
        "Configured" if settings.github_token else "Not configured",
    )
    console.print(table)
    console.print(
        "[dim]Repair execution also requires Docker and a reachable "
        "OpenAI-compatible LLM endpoint.[/dim]"
    )


if __name__ == "__main__":
    app()
