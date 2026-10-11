#!/usr/bin/env python3
"""Private operator controls; no public admin route or new cloud service."""
import argparse
import json
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
from cloud_config import load


def inspect(jobs, max_scan=1000):
    """Bounded paged scan using existing indexes; report truncation honestly."""
    from footylevels.records import JobRecord, utc_now
    cursor, pending, scanned = None, [], 0
    exhausted = False
    while scanned < max_scan:
        snapshots = jobs.candidates(limit=min(50, max_scan - scanned), after=cursor)
        if not snapshots:
            exhausted = True
            break
        for snapshot in snapshots:
            job = JobRecord.model_validate(snapshot.to_dict())
            if job.kind != "prepareMedia":
                pending.append(job)
        scanned += len(snapshots)
        cursor = snapshots[-1].id
    pending.sort(key=lambda job: (job.created_at, job.id))
    now = utc_now()
    return {"scanned": scanned, "truncated": not exhausted, "reconstructionJobs": len(pending),
        "oldest": [{"id": job.id, "status": job.status, "stage": job.stage,
            "ageSeconds": max(0, int((now - job.created_at).total_seconds())),
            "attemptCount": job.attempt_count, "leaseExpired": bool(job.status == "running" and
                (not job.lease_expires_at or job.lease_expires_at <= now))} for job in pending[:20]]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("environment", choices=["dev", "demo"])
    parser.add_argument("action", choices=["inspect", "pause", "resume"])
    parser.add_argument("--max-scan", type=int, default=1000, choices=range(1, 1001), metavar="1..1000")
    args = parser.parse_args()
    load(args.environment)
    from google.cloud import firestore
    from footylevels.records import utc_now
    from footylevels.reconstruction_jobs import FirestoreReconstructionJobs
    client = firestore.Client.from_service_account_json(os.environ["GOOGLE_APPLICATION_CREDENTIALS"],
                                                       project=os.environ["FIREBASE_PROJECT_ID"])
    try:
        jobs = FirestoreReconstructionJobs(client)
        if args.action == "inspect":
            print(json.dumps(inspect(jobs, args.max_scan)))
        else:
            enabled = args.action == "resume"
            jobs._reconstruction_control().set({"acceptingRequests": enabled, "updatedAt": utc_now()})
            print(json.dumps({"acceptingRequests": enabled, "existingJobs": "continue"}))
    finally:
        client.close()


if __name__ == "__main__":
    main()
