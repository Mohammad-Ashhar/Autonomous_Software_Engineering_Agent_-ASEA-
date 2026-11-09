from asea.api.workspace_cleanup import cleanup_repair_workspace


def test_cleanup_removes_only_the_job_workspace(tmp_path):
    repository = tmp_path / "source"
    repository.mkdir()
    run_directory = tmp_path / "workspaces" / "asea_run_test"
    working_repo = run_directory / "repository"
    working_repo.mkdir(parents=True)
    (working_repo / "source.py").write_text("print('ok')", encoding="utf-8")

    assert cleanup_repair_workspace(
        repository,
        {"working_repo_path": str(working_repo)},
    )
    assert not run_directory.exists()
    assert repository.is_dir()


def test_cleanup_rejects_paths_outside_workspace(tmp_path):
    repository = tmp_path / "source"
    repository.mkdir()
    external = tmp_path / "important"
    external.mkdir()

    assert not cleanup_repair_workspace(
        repository,
        {"working_repo_path": str(external)},
    )
    assert external.is_dir()


def test_cleanup_ignores_missing_workspace_state(tmp_path):
    repository = tmp_path / "source"
    repository.mkdir()

    assert not cleanup_repair_workspace(repository, {})
