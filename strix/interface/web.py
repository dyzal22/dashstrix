from fastapi import FastAPI, HTTPException, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import asyncio
import os
import threading
from typing import List, Optional
import uvicorn
import logging
from pathlib import Path
from strix.telemetry.tracer import get_global_tracer, Tracer, set_global_tracer
from strix.interface.main import parse_arguments, display_completion_message, validate_environment, warm_up_llm, check_docker_installed, pull_docker_image
from strix.interface.utils import (
    assign_workspace_subdirs,
    infer_target_type,
    collect_local_sources,
    generate_run_name,
    clone_repository
)
from strix.interface.cli import run_cli
import argparse

app = FastAPI(title="DashStrix")

# Enable CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# In-memory storage for scan status (for simplicity in this POC)
class ScanState:
    running = False
    logs = []
    vulnerabilities = []
    status = "idle"
    run_name = None

scan_state = ScanState()

class ScanRequest(BaseModel):
    target: str
    instruction: Optional[str] = None
    api_key: Optional[str] = None
    model: Optional[str] = "gemini/gemini-1.5-pro"

@app.get("/", response_class=HTMLResponse)
async def get_dashboard():
    with open("strix/interface/assets/index.html", "r") as f:
        return f.read()

@app.post("/api/scan/start")
async def start_scan(request: ScanRequest):
    if scan_state.running:
        raise HTTPException(status_code=400, detail="Scan already running")

    scan_state.running = True
    scan_state.logs = []
    scan_state.vulnerabilities = []
    scan_state.status = "running"

    # Start scan in a background thread
    threading.Thread(target=run_strix_scan, args=(request.target, request.instruction, request.api_key, request.model)).start()

    return {"status": "started", "target": request.target}

@app.get("/api/scan/status")
async def get_scan_status():
    tracer = get_global_tracer()
    vulns = []
    activities = []
    tool_executions = []

    if tracer:
        vulns = tracer.vulnerability_reports

        # Extract agent activities
        if hasattr(tracer, 'agents'):
            for agent_id, agent_data in tracer.agents.items():
                activities.append({
                    "agent": agent_data.get("name"),
                    "task": agent_data.get("task"),
                    "status": agent_data.get("status"),
                    "error": agent_data.get("error_message")
                })

        # Extract recent tool executions
        if hasattr(tracer, 'tool_executions'):
            # Sort by execution_id desc and take last 20
            sorted_executions = sorted(tracer.tool_executions.values(), key=lambda x: x['execution_id'], reverse=True)[:20]
            for exec_data in sorted_executions:
                tool_executions.append({
                    "id": exec_data.get("execution_id"),
                    "tool": exec_data.get("tool_name"),
                    "status": exec_data.get("status"),
                    "args": str(exec_data.get("args")),
                    "result": str(exec_data.get("result"))[:100] + "..." if exec_data.get("result") else None
                })

    return {
        "status": scan_state.status,
        "running": scan_state.running,
        "logs": scan_state.logs[-100:],
        "vulnerabilities": vulns,
        "run_name": scan_state.run_name,
        "activities": activities,
        "tool_executions": tool_executions
    }

@app.post("/api/scan/stop")
async def stop_scan():
    # This is a bit tricky as strix doesn't have a clean stop mechanism exposed easily
    # But we can try to signal the tracer or just update state
    scan_state.running = False
    scan_state.status = "stopped"
    return {"status": "stopped"}

def run_strix_scan(target: str, instruction: str | None, api_key: str | None, model: str | None):
    try:
        # Mimic main.py logic

        # Setup arguments
        # We need to construct an argparse Namespace object
        args = argparse.Namespace()
        args.target = [target]
        args.instruction = instruction
        args.instruction_file = None
        args.run_name = None
        args.non_interactive = True # Force non-interactive for web mode

        # Set environment variables for API Key and Model if provided
        if api_key:
            os.environ["LLM_API_KEY"] = api_key
        if model:
            os.environ["STRIX_LLM"] = model

        # Basic validation and setup
        # validate_environment() # Skipping for now to avoid exit(1)
        # check_docker_installed()
        # pull_docker_image()

        # Async setup
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

        # loop.run_until_complete(warm_up_llm())

        # Target processing
        args.targets_info = []
        try:
            target_type, target_dict = infer_target_type(target)
            args.targets_info.append(
                {"type": target_type, "details": target_dict, "original": target}
            )
        except Exception as e:
            scan_state.logs.append(f"Error inferring target: {e}")
            scan_state.running = False
            scan_state.status = "error"
            return

        assign_workspace_subdirs(args.targets_info)

        if not args.run_name:
            args.run_name = generate_run_name(args.targets_info)

        scan_state.run_name = args.run_name

        # Clone repos if needed
        for target_info in args.targets_info:
            if target_info["type"] == "repository":
                repo_url = target_info["details"]["target_repo"]
                dest_name = target_info["details"].get("workspace_subdir")
                cloned_path = clone_repository(repo_url, args.run_name, dest_name)
                target_info["details"]["cloned_repo_path"] = cloned_path

        args.local_sources = collect_local_sources(args.targets_info)

        # Run CLI (headless)
        # We need to capture logs/output.
        # Since run_cli uses tracer, we can poll tracer in the API.
        # But run_cli also prints to stdout. We might want to capture that.

        loop.run_until_complete(run_cli(args))

        scan_state.status = "completed"
    except Exception as e:
        logging.exception("Scan failed")
        scan_state.logs.append(f"Scan failed: {e}")
        scan_state.status = "failed"
    finally:
        scan_state.running = False

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", 8000)))
