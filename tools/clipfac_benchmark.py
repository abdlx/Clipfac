"""Run a bounded source -> analysis -> plan -> render -> review benchmark.

No publishing calls are made. Supply your own authorized sources and provider
configuration. This command uses your configured model/render resources.
"""
import argparse
import json
import os
import time
import uuid
from pathlib import Path

import httpx


def post(client, path, payload):
    # Keys stay identical if an HTTP response is lost.
    for attempt in range(2):
        try:
            response = client.post(path, json=payload)
            response.raise_for_status()
            return response.json()
        except httpx.TransportError:
            if attempt:
                raise
    raise RuntimeError("Request failed")


def wait(client, submission, timeout):
    identity = submission["id"]
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        response = client.get("/api/clipfac/productions/" + identity)
        response.raise_for_status()
        record = response.json()
        if record["state"] in ("completed", "failed", "blocked", "uncertain", "unavailable"):
            if record["state"] != "completed":
                raise RuntimeError("Production ended as " + record["state"])
            return record
        if record["state"] == "submitting":
            raise RuntimeError("Ambiguous/in-progress submission requires reconciliation, not a new key")
        time.sleep(5)
    raise TimeoutError("Production exceeded benchmark time budget")


def run_case(client, case, index, max_clips, timeout):
    started = time.monotonic()
    result = {"name": case.get("name", f"source-{index + 1}"), "status": "failed",
              "human_quality_rating": None}
    try:
        payload = {
            "source_url": case["source_url"], "confirm_rights": case["confirm_rights"],
            "style": case.get("style", "auto"), "campaign": case.get("campaign", {}),
            "minimum_score": case.get("minimum_score", 70),
            "min_seconds": case.get("min_seconds", 15), "max_seconds": case.get("max_seconds", 60),
            "max_clips": max_clips, "idempotency_key": str(uuid.uuid4()),
        }
        analysis = wait(client, post(client, "/api/clipfac/analyses", payload), timeout)
        analysis_id = analysis["response"]["job_id"]
        result["analysis_job_id"] = analysis_id
        result["chosen_style"] = analysis["analysis"]["profile"]["style"]
        result["director_fallback"] = analysis["analysis"]["profile"]["fallback"]
        if case.get("expected_style"):
            result["style_reference_agreement"] = result["chosen_style"] == case["expected_style"]
        plan = post(client, "/api/clipfac/plans", {
            "analysis_job_id": analysis_id, "minimum_score": payload["minimum_score"],
        })
        result["plan_id"] = plan["id"]
        result["plan_digest"] = plan["digest"]
        render = wait(client, post(client, "/api/clipfac/plans/render", {
            "plan_id": plan["id"], "expected_revision": plan["revision"],
            "idempotency_key": str(uuid.uuid4()),
        }), timeout)
        result["render_job_id"] = render["response"]["job_id"]
        clips = render.get("selection", {}).get("accepted", [])
        result["rendered_clips"] = len(clips)
        result["reviews"] = []
        for item in clips:
            reviewed = post(client, "/api/clipfac/reviews", {
                "job_id": result["render_job_id"], "clip_index": item["index"],
            })
            result["reviews"].append({
                "id": reviewed["id"], "state": reviewed["state"],
                "decision": (reviewed.get("report") or {}).get("decision"),
                "technical_issues": ((reviewed.get("report") or {}).get("technical") or {}).get("issues"),
            })
        result["status"] = "completed"
    except Exception as exc:
        # Never emit request headers, model secrets, or full source URLs.
        result["error_type"] = type(exc).__name__
        result["error"] = str(exc).split("\n")[0][:200] if not isinstance(exc, httpx.HTTPError) else "HTTP/API failure"
    result["elapsed_seconds"] = round(time.monotonic() - started, 2)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--base-url", default="http://localhost:8080")
    parser.add_argument("--output", default=Path("benchmark-results.json"), type=Path)
    parser.add_argument("--max-clips", type=int, default=3, choices=range(1, 6))
    parser.add_argument("--timeout", type=int, default=1800)
    args = parser.parse_args()
    cases = json.loads(args.manifest.read_text())
    if not isinstance(cases, list) or not 1 <= len(cases) <= 30:
        parser.error("Manifest must contain 1–30 cases")
    if any(not case.get("source_url") or case.get("confirm_rights") is not True for case in cases):
        parser.error("Every case requires source_url and confirm_rights=true")
    token = os.environ.get("CLIPFAC_AGENT_TOKEN")
    if not token:
        parser.error("Set CLIPFAC_AGENT_TOKEN to a read/process/edit role token")
    report = {"schema_version": 1, "cases": [], "notice": "Sampled review is not human-rated quality or predicted reach."}
    with httpx.Client(base_url=args.base_url.rstrip("/"), headers={"Authorization": "Bearer " + token},
                      timeout=300, follow_redirects=False) as client:
        for index, case in enumerate(cases):
            result = run_case(client, case, index, args.max_clips, args.timeout)
            report["cases"].append(result)
            # Checkpoint after every case; a lost session does not lose the run.
            args.output.write_text(json.dumps(report, indent=2))
            print(f"{index + 1}/{len(cases)} {result['name']}: {result['status']} ({result['elapsed_seconds']}s)", flush=True)
    completed = [case for case in report["cases"] if case["status"] == "completed"]
    reviews = [item for case in completed for item in case.get("reviews", [])]
    passed = sum(item["decision"] == "sampled_checks_passed" for item in reviews)
    report["summary"] = {
        "sources": len(cases), "completed_sources": len(completed),
        "execution_success_rate": len(completed) / len(cases),
        "reviewed_clips": len(reviews), "sampled_review_passes": passed,
        "sampled_review_pass_rate": passed / len(reviews) if reviews else None,
        "total_elapsed_seconds": round(sum(case["elapsed_seconds"] for case in report["cases"]), 2),
        "human_quality_rating": None, "cost_usd": None,
    }
    args.output.write_text(json.dumps(report, indent=2))
    print(json.dumps(report["summary"], indent=2))


if __name__ == "__main__":
    main()
