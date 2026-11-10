
import re
import time
from math import isfinite
from pathlib import Path

import docker
from docker.errors import DockerException


class DockerTestRunner:

    def __init__(
        self,
        image="asea-sandbox:py312",
        timeout=120,
        memory_limit="512m"
    ):
        if not isinstance(image, str) or not image.strip():
            raise ValueError("Docker image must be a non-empty string.")
        if (
            isinstance(timeout, bool)
            or not isinstance(timeout, (int, float))
            or not isfinite(timeout)
            or not 1 <= timeout <= 3600
        ):
            raise ValueError("Test timeout must be between 1 and 3600 seconds.")
        if not isinstance(memory_limit, str) or not re.fullmatch(
            r"\d+(?:[bBkKmMgG])?", memory_limit.strip()
        ):
            raise ValueError(
                "Memory limit must be a positive byte value with an optional unit."
            )

        memory_value = re.fullmatch(
            r"(\d+)([bBkKmMgG]?)", memory_limit.strip()
        )
        amount = int(memory_value.group(1))
        unit = memory_value.group(2).lower()
        multiplier = {"": 1, "b": 1, "k": 1024, "m": 1024**2, "g": 1024**3}[unit]
        memory_bytes = amount * multiplier
        if not 16 * 1024**2 <= memory_bytes <= 8 * 1024**3:
            raise ValueError("Memory limit must be between 16 MiB and 8 GiB.")

        self.client = docker.from_env()
        self.image = image.strip()
        self.timeout = float(timeout)
        self.memory_limit = memory_limit.strip().lower()

    def parse_results(self, output, exit_code):
        """Parse pytest's concise summary, including singular error counts."""
        counts = {
            "passed": 0,
            "failed": 0,
            "errors": 0,
            "skipped": 0,
            "xfailed": 0,
            "xpassed": 0,
        }
        summary_pattern = re.compile(
            r"\b(\d+)\s+(passed|failed|errors?|skipped|xfailed|xpassed)\b",
            re.IGNORECASE,
        )

        # Pytest may print arbitrary captured output containing phrases such
        # as "2 passed". Count only its final duration-bearing summary line.
        summary_lines = [
            line.strip()
            for line in output.splitlines()
            if re.search(r"\\bin\\s+\\d+(?:\\.\\d+)?s\\s*$", line.strip())
        ]
        summary = summary_lines[-1] if summary_lines else ""

        for match in summary_pattern.finditer(summary):
            label = match.group(2).lower()
            key = "errors" if label in {"error", "errors"} else label
            counts[key] += int(match.group(1))

        failed_tests = re.findall(
            r"^FAILED\s+(.+?)(?:\s+-|$)",
            output,
            re.MULTILINE,
        )

        total = sum(counts.values())
        if exit_code == 5:
            status = "NO_TESTS"
        elif exit_code == 0:
            status = "PASSED"
        else:
            status = "FAILED"

        return {
            "status": status,
            "total": total,
            **counts,
            "failed_tests": failed_tests,
        }

    def run_tests(self, repository_path):

        repo_path = Path(repository_path).resolve()

        if not repo_path.is_dir():
            raise NotADirectoryError(repo_path)

        container = None
        start_time = time.monotonic()

        try:
            container = self.client.containers.create(
                image=self.image,
                command=[
                    "python", "-m", "pytest",
                    "-q", "-p", "no:cacheprovider", "-rA"
                ],
                working_dir="/workspace",
                volumes={
                    str(repo_path): {
                        "bind": "/workspace",
                        "mode": "ro"
                    }
                },
                network_disabled=True,
                read_only=True,
                user="65532:65532",
                mem_limit=self.memory_limit,
                nano_cpus=1_000_000_000,
                pids_limit=128,
                cap_drop=["ALL"],
                security_opt=["no-new-privileges:true"],
                tmpfs={
                    "/tmp": (
                        "rw,noexec,nosuid,size=64m,"
                        "uid=65532,gid=65532"
                    )
                },
                environment={
                    "PYTHONDONTWRITEBYTECODE": "1",
                    "PYTEST_ADDOPTS": "",
                    "HOME": "/tmp",
                    "TMPDIR": "/tmp"
                },
                detach=True
            )

            container.start()

            timed_out = False

            while True:
                container.reload()

                if container.status == "exited":
                    break

                if time.monotonic() - start_time >= self.timeout:
                    timed_out = True
                    container.kill()
                    break

                time.sleep(0.5)

            output = container.logs(
                stdout=True,
                stderr=True
            ).decode("utf-8", errors="replace")

            duration = round(
                time.monotonic() - start_time, 2
            )

            if timed_out:
                return {
                    "status": "TIMEOUT",
                    "success": False,
                    "timed_out": True,
                    "duration": duration,
                    "output": output
                }

            exit_code = container.attrs["State"]["ExitCode"]

            results = self.parse_results(
                output,
                exit_code
            )

            return {
                **results,
                "success": exit_code == 0,
                "timed_out": False,
                "exit_code": exit_code,
                "duration": duration,
                "output": output
            }

        except DockerException as error:
            raise RuntimeError(
                f"Docker execution failed: {error}"
            ) from error

        finally:
            if container is not None:
                try:
                    container.remove(force=True)
                except DockerException:
                    pass
