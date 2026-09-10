"""Read-only deployment rollback receipt. Does not print environment variables."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess


def read(args, fields):
    cmd = ["gcloud.cmd", *args, "--account=jmaietta@ceorater.com", "--project=yfinance-cli", "--format=json(" + fields + ")"]
    return json.loads(subprocess.run(cmd, check=True, capture_output=True, text=True).stdout)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("Use a new receipt path")
    receipt = {"captured_at": datetime.now(timezone.utc).isoformat(), "project": "yfinance-cli", "region": "us-central1"}
    receipt["api"] = read(["run", "services", "describe", "tek2day-api", "--region=us-central1"],
                          "status.traffic,status.latestReadyRevisionName,spec.template.spec.containers[0].image")
    revision = receipt["api"]["status"]["latestReadyRevisionName"]
    receipt["api_revision"] = read(["run", "revisions", "describe", revision, "--region=us-central1"], "status.imageDigest")
    receipt["jobs"] = {}
    for name in ["daily-price-pull", "weekly-estimate-pull", "quarterly-financial-pull", "architecture-check"]:
        receipt["jobs"][name] = read(["run", "jobs", "describe", name, "--region=us-central1"],
                                     "spec.template.spec.taskCount,spec.template.spec.template.spec.maxRetries,spec.template.spec.template.spec.containers[0].image")
    receipt["images"] = {}
    for image in ["daily-prices", "weekly-estimates", "quarterly-financials"]:
        tag = f"us-central1-docker.pkg.dev/yfinance-cli/tek2day/{image}:latest"
        receipt["images"][tag] = read(["artifacts", "docker", "images", "describe", tag], "image_summary.digest,image_summary.fully_qualified_digest")
    with args.output.open("x", encoding="utf-8") as handle:
        json.dump(receipt, handle, sort_keys=True, indent=2)
    print("Saved rollback receipt", args.output, "API", revision)


if __name__ == "__main__":
    main()
