"""Offline cohort planner: reviewed bindings + saved native/SEC evidence -> exact plans."""
import argparse
from datetime import datetime
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.run_sec_repair import read, save
from sec_cohort import prepare, public_manifest
from security_identity import digest, require


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--bundle', type=Path, required=True)
    p.add_argument('--private-plan', type=Path, required=True)
    p.add_argument('--manifest', type=Path, required=True)
    args = p.parse_args()
    require(not args.private_plan.resolve().is_relative_to(ROOT), 'Private plan must stay outside Git')
    require(not args.private_plan.exists() and not args.manifest.exists(), 'Use new output paths')
    bundle = read(args.bundle)
    cohort = prepare(bundle['bindings'], bundle['captures'], bundle['sources'], datetime.fromisoformat(bundle['as_of']))
    save(args.private_plan, cohort)
    save(args.manifest, public_manifest(cohort))
    print({'cohort_sha256': digest(cohort), 'securities': len(cohort['items']),
           'blocked': cohort['blocked_symbols'], 'financial_writes': cohort['financial_write_count']})


if __name__ == '__main__':
    main()
