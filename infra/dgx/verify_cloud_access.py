#!/usr/bin/env python3
"""FP-50 connectivity probe, not an application authorization/storage adapter.

Creates only uniquely named disposable probe records/objects and cleans them up.
Run through with_network_dns.sh using the isolated Google Auth/Requests libraries.
"""
import argparse
import json
import os
from pathlib import Path
import stat
import uuid

from google.auth.transport.requests import AuthorizedSession
from google.oauth2 import service_account
import requests


def private_json(path):
    metadata = path.stat()
    if metadata.st_uid != os.getuid() or stat.S_IMODE(metadata.st_mode) & 0o077:
        raise RuntimeError("Configuration must be owned by this user with mode 0600")
    return json.loads(path.read_text())


def require(response, status, action):
    if response.status_code != status:
        # Never include response bodies, request headers or credentials in diagnostics.
        raise RuntimeError(f"{action}: HTTP {response.status_code}; expected {status}")


def firestore(environment, directory):
    project = f"footylevels-{environment}-20261009"
    key_path = directory / "google-service-account.json"
    key = private_json(key_path)
    if key.get("project_id") != project or key.get("client_email") != f"footylevels-api-{environment}@{project}.iam.gserviceaccount.com":
        raise RuntimeError("Runtime identity does not match the requested environment")
    credentials = service_account.Credentials.from_service_account_info(
        key, scopes=["https://www.googleapis.com/auth/datastore"])
    value = uuid.uuid4().hex
    root = f"https://firestore.googleapis.com/v1/projects/{project}/databases/(default)/documents"
    path = f"{root}/_connectivityChecks/fp50-{value}"
    with AuthorizedSession(credentials) as session:
        try:
            require(session.patch(path, params={"currentDocument.exists": "false"},
                                  json={"fields": {"probe": {"stringValue": value}}}, timeout=30), 200, "Firestore create")
            response = session.get(path, timeout=30)
            require(response, 200, "Firestore read")
            if response.json()["fields"]["probe"]["stringValue"] != value:
                raise RuntimeError("Firestore round-trip content mismatch")
        finally:
            # This ID is unique to this probe; never delete an application record.
            require(session.delete(path, timeout=30), 200, "Firestore cleanup")
        require(session.get(path, timeout=30), 404, "Firestore cleanup verification")
        other = "demo" if environment == "dev" else "dev"
        other_path = path.replace(project, f"footylevels-{other}-20261009")
        require(session.get(other_path, timeout=30), 403, "Cross-environment access denial")
    print(f"{environment}: Firestore create/read/delete passed; cross-environment access denied")


def storage(environment, directory):
    config = private_json(directory / "runtime.json")
    references = {"dev": "bwzxrvkxfubnmuieljoj", "demo": "skzgjwbkadiazfvuhtts"}
    url = f"https://{references[environment]}.supabase.co"
    if config.get("SUPABASE_URL") != url or config.get("FOOTYLEVELS_ENVIRONMENT") != environment:
        raise RuntimeError("Storage configuration does not match the requested environment")
    secret = config["SUPABASE_SECRET_KEY"]
    if not secret.startswith("sb_secret_"):
        raise RuntimeError("Expected a server secret API key")
    root = url + "/storage/v1"
    with requests.Session() as session:
        session.headers.update({"apikey": secret})
        for bucket in ["private-media", "reference-assets"]:
            result = session.get(f"{root}/bucket/{bucket}", timeout=30)
            require(result, 200, "Bucket inspection")
            info = result.json()
            if info.get("public") is not False or info.get("file_size_limit") != 52428800:
                raise RuntimeError("Bucket privacy or file limit does not match the foundation")
            value = uuid.uuid4().hex
            object_path = f"_connectivityChecks/fp50-{value}.json"
            payload = json.dumps({"probe": value}).encode()
            try:
                require(session.post(f"{root}/object/{bucket}/{object_path}", data=payload,
                                     headers={"Content-Type": "application/json", "x-upsert": "false"}, timeout=30), 200, "Storage upload")
                response = session.get(f"{root}/object/authenticated/{bucket}/{object_path}", timeout=30)
                require(response, 200, "Storage download")
                if response.content != payload:
                    raise RuntimeError("Storage round-trip content mismatch")
                public = requests.get(f"{root}/object/public/{bucket}/{object_path}", timeout=30)
                if public.status_code not in [400, 401, 403, 404]:
                    raise RuntimeError("Private object public-route denial was not confirmed")
            finally:
                require(session.delete(f"{root}/object/{bucket}", json={"prefixes": [object_path]}, timeout=30), 200, "Storage cleanup")
            missing = session.get(f"{root}/object/authenticated/{bucket}/{object_path}", timeout=30)
            if missing.status_code not in [400, 404]:
                raise RuntimeError("Storage cleanup verification failed")
            print(f"{environment}: {bucket} private upload/download/delete passed; public access denied")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("environment", choices=["dev", "demo"])
    parser.add_argument("--firestore-only", action="store_true")
    args = parser.parse_args()
    directory = Path.home() / ".config/footylevels" / args.environment
    try:
        firestore(args.environment, directory)
        if not args.firestore_only:
            storage(args.environment, directory)
    except (RuntimeError, FileNotFoundError) as error:
        raise SystemExit(str(error)) from None
    except Exception as error:
        # SDK/transport exceptions may contain credential-bearing request information.
        raise SystemExit(f"Cloud probe failed ({type(error).__name__}); no secret diagnostics displayed") from None


if __name__ == "__main__":
    main()
