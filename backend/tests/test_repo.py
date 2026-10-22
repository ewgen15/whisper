"""Guards the CI/deploy wiring itself, so a careless edit can't silently disable the gate."""
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
WF = ROOT / ".github" / "workflows"


def _load(name):
    data = yaml.safe_load((WF / name).read_text())
    data["on"] = data.pop(True, data.get("on"))  # PyYAML parses bare `on` as True
    return data


def test_ci_has_required_jobs():
    jobs = _load("ci.yml")["jobs"]
    assert {"backend", "frontend", "docker"} <= set(jobs)


def test_ci_runs_on_pull_requests_and_pushes_and_is_reusable():
    on = _load("ci.yml")["on"]
    assert {"push", "pull_request", "workflow_call"} <= set(on)


def test_docker_job_builds_image_and_smoke_tests_health():
    steps = yaml.safe_dump(_load("ci.yml")["jobs"]["docker"])
    assert "docker build" in steps and "/health" in steps


def test_deploy_is_gated_on_ci():
    deploy = _load("deploy-backend.yml")["jobs"]
    assert deploy["deploy"]["needs"] == "ci"


def test_uptime_workflow_is_scheduled_and_alerts():
    wf = _load("uptime.yml")
    assert "schedule" in wf["on"]
    text = (WF / "uptime.yml").read_text()
    assert "/health" in text and "issues" in text and "exit 1" in text
