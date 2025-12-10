import asyncio
import logging
import os
import subprocess
import sys
import uuid
from typing import Any

import requests
from tenacity import retry, stop_after_attempt, wait_fixed

from strix.runtime.runtime import AbstractRuntime, SandboxInfo

logger = logging.getLogger(__name__)


class LocalRuntime(AbstractRuntime):
    def __init__(self) -> None:
        self.tool_server_process: subprocess.Popen[bytes] | None = None
        self.tool_server_port = 8080
        self.auth_token = str(uuid.uuid4())

    async def create_sandbox(
        self,
        agent_id: str,
        existing_token: str | None = None,
        local_sources: list[dict[str, str]] | None = None,
    ) -> SandboxInfo:
        if existing_token:
            self.auth_token = existing_token

        # Check if already running
        if self.tool_server_process is None:
            self._start_tool_server()

        await self._wait_for_server()

        # In local mode, we don't really 'copy' sources like Docker,
        # we assume the agent runs in the current environment or workspace.
        # Ideally, we would set up a temporary directory, but for now we run in-place.

        return {
            "workspace_id": "local",
            "api_url": f"http://localhost:{self.tool_server_port}",
            "auth_token": self.auth_token,
            "tool_server_port": self.tool_server_port,
            "agent_id": agent_id,
        }

    def _start_tool_server(self) -> None:
        # We need to run the tool server as a subprocess
        cmd = [
            sys.executable,
            "-m",
            "strix.runtime.tool_server",
            "--port",
            str(self.tool_server_port),
            "--token",
            self.auth_token,
        ]

        env = os.environ.copy()
        env["STRIX_SANDBOX_MODE"] = "true"

        logger.info(f"Starting local tool server: {' '.join(cmd)}")
        self.tool_server_process = subprocess.Popen(
            cmd,
            env=env,
            stdout=sys.stdout, # redirect to main stdout for now
            stderr=sys.stderr,
        )

    @retry(stop=stop_after_attempt(10), wait=wait_fixed(1))
    async def _wait_for_server(self) -> None:
        url = f"http://localhost:{self.tool_server_port}/health"
        try:
            response = requests.get(url, timeout=2)
            if response.status_code == 200:
                logger.info("Local tool server is ready")
                return
        except requests.RequestException:
            pass

        if self.tool_server_process and self.tool_server_process.poll() is not None:
             raise RuntimeError(f"Tool server process exited unexpectedly with code {self.tool_server_process.returncode}")

        raise RuntimeError("Waiting for tool server timed out")

    async def get_sandbox_url(self, container_id: str, port: int) -> str:
        # In local mode, ports are just local ports
        return f"http://localhost:{port}"

    async def destroy_sandbox(self, container_id: str) -> None:
        if self.tool_server_process:
            logger.info("Stopping local tool server...")
            self.tool_server_process.terminate()
            try:
                self.tool_server_process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.tool_server_process.kill()
            self.tool_server_process = None
