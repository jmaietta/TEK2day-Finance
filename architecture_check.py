#!/usr/bin/env python3
"""Does architecture.yaml still describe reality? Fail loudly if not.

⚠️ A STALE MAP IS WORSE THAN NO MAP. It is confidently wrong and it gets
believed — the `deploy-jobs.yml` comment that misled an entire outage
investigation on 19 Aug 2026 was accurate when it was written. So the map is
CHECKED rather than trusted.

Verifies, against this repo and against the live Google Cloud project:
  - the Dockerfiles that exist, and which workflow builds each
  - the images that exist in Artifact Registry
  - the Cloud Run jobs, their images, task counts and MAX RETRIES
  - that the Cloud Build triggers stay disabled (one build system, not two)
  - that requirements.txt is fully pinned
  - that the GitHub actions are pinned to commit SHAs

Exits 0 when the map matches, 1 when it drifts, and logs a line beginning
ARCHITECTURE DRIFT so a log-based alert can email on it.

Runs anywhere: uses `gcloud auth print-access-token` locally, or the metadata
server inside Cloud Run. No new dependencies — requests is already pinned.

    python architecture_check.py            # human-readable
    python architecture_check.py --quiet    # only drift
"""
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys

import requests
import yaml

ROOT = pathlib.Path(__file__).parent
TIMEOUT = 30

drift = []
checked = 0


def note(what, detail=""):
    drift.append(f"{what}{(' — ' + detail) if detail else ''}")


def ok(_what):
    global checked
    checked += 1


def token():
    """An access token, from the metadata server or the local gcloud."""
    try:
        r = requests.get(
            "http://metadata.google.internal/computeMetadata/v1/"
            "instance/service-accounts/default/token",
            headers={"Metadata-Flavor": "Google"}, timeout=5)
        if r.ok:
            return r.json()["access_token"]
    except Exception:  # noqa: BLE001 - not on GCP, fall through
        pass
    # On Windows the executable is gcloud.cmd, so a bare "gcloud" is not found.
    exe = shutil.which("gcloud") or shutil.which("gcloud.cmd")
    if not exe:
        print("cannot obtain a Google Cloud token: gcloud not on PATH", file=sys.stderr)
        return None
    try:
        return subprocess.run([exe, "auth", "print-access-token"],
                              capture_output=True, text=True, check=True,
                              timeout=120).stdout.strip()
    except Exception as exc:  # noqa: BLE001
        print(f"cannot obtain a Google Cloud token: {exc}", file=sys.stderr)
        return None


def api(url, tok):
    r = requests.get(url, headers={"Authorization": f"Bearer {tok}"}, timeout=TIMEOUT)
    if not r.ok:
        return None
    return r.json()


# ── repo-side checks: no cloud access needed ────────────────────────────────

def check_dockerfiles(spec):
    declared = set(spec["dockerfiles"])
    actual = {p.name for p in ROOT.glob("Dockerfile.*")}
    for missing in sorted(declared - actual):
        note(f"Dockerfile declared but MISSING from the repo: {missing}")
    for extra in sorted(actual - declared):
        note(f"Dockerfile in the repo but NOT in architecture.yaml: {extra}",
             "add it, with what builds it and what it is for")
    ok("dockerfiles")

    # does the workflow that claims to build it actually name it?
    for name, meta in spec["dockerfiles"].items():
        built_by = meta.get("built_by")
        if not built_by:
            continue
        wf = ROOT / ".github" / "workflows" / built_by
        if not wf.exists():
            note(f"{name} says it is built by {built_by}, which does not exist")
            continue
        if name not in wf.read_text(encoding="utf-8"):
            note(f"{built_by} no longer mentions {name}")
    ok("workflow-builds-dockerfile")


def check_requirements(spec):
    if not spec.get("requirements_must_be_fully_pinned"):
        return
    text = (ROOT / "requirements.txt").read_text(encoding="utf-8")
    loose = [ln.strip() for ln in text.splitlines()
             if ln.strip() and not ln.strip().startswith("#") and "==" not in ln]
    if loose:
        note("requirements.txt is NOT fully pinned", ", ".join(loose[:5]))
    ok("requirements-pinned")


def check_action_pins(spec):
    if not spec.get("github_actions_must_be_sha_pinned"):
        return
    for wf in sorted((ROOT / ".github" / "workflows").glob("*.yml")):
        for lineno, line in enumerate(wf.read_text(encoding="utf-8").splitlines(), 1):
            m = re.search(r"uses:\s*([^\s#]+)", line)
            if not m:
                continue
            ref = m.group(1)
            if "@" not in ref or not re.fullmatch(r"[0-9a-f]{40}", ref.split("@", 1)[1]):
                note(f"{wf.name}:{lineno} action is not SHA-pinned: {ref}",
                     "a moving tag can be repointed by its maintainer")
    ok("actions-sha-pinned")


# ── cloud-side checks ───────────────────────────────────────────────────────

def check_jobs(spec, tok):
    project, region = spec["project"], spec["region"]
    base = f"https://run.googleapis.com/v2/projects/{project}/locations/{region}/jobs"
    data = api(base, tok)
    if data is None:
        note("could not list Cloud Run jobs", "permissions or API availability")
        return
    live = {j["name"].rsplit("/", 1)[-1]: j for j in (data.get("jobs") or [])}
    declared = spec["cloud_run_jobs"]

    for missing in sorted(set(declared) - set(live)):
        note(f"Cloud Run job declared but MISSING: {missing}")
    for extra in sorted(set(live) - set(declared)):
        note(f"Cloud Run job exists but is NOT in architecture.yaml: {extra}")

    for name, want in declared.items():
        job = live.get(name)
        if not job:
            continue
        tmpl = (job.get("template") or {}).get("template") or {}
        containers = tmpl.get("containers") or [{}]
        image = containers[0].get("image", "")
        want_ref = f"{spec['registry']}/{want['image']}:{want['tag']}"
        if image != want_ref:
            note(f"{name} runs {image}", f"architecture.yaml says {want_ref}")
        if "task_count" in want:
            actual = (job.get("template") or {}).get("taskCount")
            if actual is not None and int(actual) != int(want["task_count"]):
                note(f"{name} taskCount is {actual}", f"expected {want['task_count']}")
        if "max_retries" in want:
            actual = tmpl.get("maxRetries", 0)
            if int(actual) != int(want["max_retries"]):
                extra = ("  ⚠️ 0 IS DELIBERATE: retries would make a failing night "
                         "hit Yahoo twice." if want["max_retries"] == 0 else "")
                note(f"{name} maxRetries is {actual}",
                     f"expected {want['max_retries']}.{extra}")
    ok("cloud-run-jobs")


def check_triggers(spec, tok):
    names = spec.get("cloud_build_triggers_must_be_disabled") or []
    if not names:
        return
    data = api(f"https://cloudbuild.googleapis.com/v1/projects/{spec['project']}/triggers", tok)
    if data is None:
        note("could not list Cloud Build triggers")
        return
    live = {t.get("name"): t for t in (data.get("triggers") or [])}
    for name in names:
        t = live.get(name)
        if t is None:
            continue  # deleted is fine; it cannot race if it does not exist
        if not t.get("disabled"):
            note(f"Cloud Build trigger '{name}' is ENABLED again",
                 "two build systems would race for the :latest tag; the data jobs "
                 "resolve :latest at execution time")
    ok("cloud-build-triggers-disabled")


def check_images(spec, tok):
    project, region = spec["project"], spec["region"]
    url = (f"https://artifactregistry.googleapis.com/v1/projects/{project}"
           f"/locations/{region}/repositories/tek2day/packages")
    data = api(url, tok)
    if data is None:
        note("could not list Artifact Registry packages")
        return
    live = {p["name"].rsplit("/", 1)[-1] for p in (data.get("packages") or [])}
    for meta in spec["dockerfiles"].values():
        image = meta.get("image")
        if image and image not in live:
            note(f"image '{image}' is declared but not in the registry")
    ok("registry-images")


def main():
    quiet = "--quiet" in sys.argv
    spec = yaml.safe_load((ROOT / "architecture.yaml").read_text(encoding="utf-8"))

    check_dockerfiles(spec)
    check_requirements(spec)
    check_action_pins(spec)

    tok = token()
    if tok:
        check_jobs(spec, tok)
        check_triggers(spec, tok)
        check_images(spec, tok)
    else:
        note("skipped all cloud checks", "no Google Cloud token available")

    if drift:
        # ⚠️ This exact prefix is what the Cloud Monitoring alert matches on.
        print(f"ARCHITECTURE DRIFT: {len(drift)} difference(s) between "
              f"architecture.yaml and reality")
        for d in drift:
            print(f"  - {d}")
        print("\nEither reality changed and the map was not updated, or something "
              "changed that nobody intended. Both are worth knowing.")
        return 1

    if not quiet:
        print(f"architecture.yaml matches reality ({checked} checks)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
