"""Run real read-only knowledge conversations through the authenticated HTTP API."""
import argparse
import json
from pathlib import Path
import statistics
import time
from uuid import uuid4

import httpx

from app.core.config import get_settings


QUESTION = (
    "Please search the verified historical experience for the VideoHub project using experience.search: "
    "backend service startup failures, database connection failures, and past troubleshooting lessons. "
    "Summarize only the records actually retrieved and cite them. If no verified records exist, say so. "
    "This is a historical knowledge lookup only; do not inspect live runtime state, run shell commands, "
    "or propose or execute changes. Answer in Simplified Chinese."
)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--project-id", type=int, default=1)
    parser.add_argument("--environment-id", type=int, default=1)
    parser.add_argument("--output", default="/tmp/ops-v2-benchmark.json")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--question", default=QUESTION)
    args = parser.parse_args()
    settings = get_settings()
    reports = []
    with httpx.Client(base_url=args.base_url, timeout=30) as client:
        response = client.post("/api/auth/login", json={"username": settings.admin_username, "password": settings.admin_password})
        response.raise_for_status()
        client.headers["Authorization"] = "Bearer " + response.json()["access_token"]
        try:
            for index in range(args.runs):
                response = client.post(f"/api/projects/{args.project_id}/chat-sessions", json={
                    "title": f"V2 knowledge benchmark {index + 1}", "environment_id": args.environment_id})
                response.raise_for_status()
                session_id = response.json()["id"]
                start = time.perf_counter()
                response = client.post(f"/api/chat-sessions/{session_id}/agent-runs", json={
                    "content": args.question, "client_request_id": str(uuid4())})
                response.raise_for_status()
                run_id = response.json()["run_summary"]["id"]
                print(json.dumps({"run_id": run_id, "sample": index + 1}), flush=True)
                deadline = time.monotonic() + 360
                while time.monotonic() < deadline:
                    response = client.get(f"/api/chat-sessions/{session_id}/messages")
                    response.raise_for_status()
                    answers = [item for item in response.json() if item["role"] == "assistant"
                               and item["metadata_json"].get("run_id") == run_id]
                    if answers:
                        observed_ms = (time.perf_counter() - start) * 1000
                        break
                    time.sleep(0.8)
                else:
                    raise RuntimeError(f"Observation timed out: {run_id}")
                for _ in range(40):
                    response = client.get(f"/api/agent-runs/{run_id}/profile")
                    response.raise_for_status()
                    report = response.json()
                    if report["profile_status"] == "recorded":
                        break
                    time.sleep(0.25)
                run = client.get(f"/api/agent-runs/{run_id}").json()
                report["experiment"] = {"question": args.question, "session_id": session_id,
                                        "observed_ms": round(observed_ms, 3),
                                        "observation": "HTTP 800ms polling, not browser render timing",
                                        "path": run["plan_json"].get("request_path"),
                                        "answer": answers[-1]["content"]}
                reports.append(report)
                Path(args.output).write_text(json.dumps(reports, ensure_ascii=False, indent=2) + "\n")
                print(json.dumps({"run_id": run_id, "status": report["status"],
                                  "server_ms": report["server_latency_ms"], "observed_ms": observed_ms,
                                  "path": report["experiment"]["path"]}), flush=True)
        finally:
            client.post("/api/auth/logout")
    print(json.dumps({"runs": len(reports), "median_server_ms": statistics.median(
        item["server_latency_ms"] for item in reports if item["server_latency_ms"] is not None)}))


if __name__ == "__main__":
    main()
