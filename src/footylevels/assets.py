"""Private Supabase assets. Credentials never leave this server boundary."""
import hashlib
from pathlib import Path
import re
from urllib.parse import quote, urlsplit, parse_qs

import requests

from .records import AssetRef
from .repositories import RecordConflict

FILE_LIMIT = 52_428_800


class StorageUnavailable(Exception):
    """Sanitized temporary storage failure; never stringify transport exceptions."""


class SupabaseAssets:
    def __init__(self, url: str, secret: str):
        if not re.fullmatch(r"https://[a-z]{20}\.supabase\.co", url) or not secret.startswith("sb_secret_"):
            raise ValueError("Explicit Supabase project URL and server secret required")
        self.root = url + "/storage/v1"
        self.url = url
        self.tus_url = url.replace(".supabase.co", ".storage.supabase.co") + "/storage/v1/upload/resumable"
        self._secret = secret

    def _request(self, method, path, **kwargs):
        # A fresh session avoids shared mutable transport state between API threads.
        try:
            return requests.request(method, self.root + path, headers={"apikey": self._secret, **kwargs.pop("headers", {})},
                                    timeout=(10, 60), allow_redirects=False, **kwargs)
        except requests.RequestException:
            raise StorageUnavailable() from None

    @staticmethod
    def _path(asset):
        return quote(asset.bucket, safe="") + "/" + quote(asset.object_key, safe="/")

    def signed_upload(self, asset: AssetRef):
        with self._request("POST", "/object/upload/sign/" + self._path(asset), json={}) as response:
            if response.status_code != 200:
                raise StorageUnavailable()
            try:
                raw = response.json()["url"]
                # Never accept a redirect or a URL supplied by client metadata.
                parsed = urlsplit(raw)
                expected = "/object/upload/sign/" + self._path(asset)
                if parsed.scheme or parsed.netloc or parsed.path != expected:
                    raise ValueError()
                token = parse_qs(parsed.query)["token"]
                if len(token) != 1:
                    raise ValueError()
                return {"protocol": "tus", "endpoint": self.tus_url,
                        "headers": {"x-signature": token[0], "x-upsert": "false"},
                        "metadata": {"bucketName": asset.bucket, "objectName": asset.object_key,
                                     "contentType": asset.content_type, "cacheControl": "0"},
                        "chunkSize": 6 * 1024 * 1024}
            except (KeyError, ValueError, TypeError):
                raise StorageUnavailable() from None

    def signed_read(self, asset: AssetRef, seconds=300):
        if not 1 <= seconds <= 300:
            raise ValueError("Signed reads are bounded to five minutes")
        with self._request("POST", "/object/sign/" + self._path(asset), json={"expiresIn": seconds}) as response:
            if response.status_code != 200:
                raise StorageUnavailable()
            try:
                raw = response.json()["signedURL"]
                parsed = urlsplit(raw)
                if parsed.scheme or parsed.netloc or parsed.path != "/object/sign/" + self._path(asset) or not parsed.query:
                    raise ValueError()
                return self.root + raw
            except (KeyError, ValueError, TypeError):
                raise StorageUnavailable() from None

    def verify(self, asset: AssetRef):
        # Independently stream bytes; never trust client checksums, ETags or file metadata.
        with self._request("GET", "/object/authenticated/" + self._path(asset), stream=True) as response:
            if response.status_code in (400, 404):
                raise RecordConflict("Upload is not available")
            if response.status_code != 200:
                raise StorageUnavailable()
            digest, size = hashlib.sha256(), 0
            try:
                for chunk in response.iter_content(1024 * 1024):
                    size += len(chunk)
                    if size > asset.byte_size or size > FILE_LIMIT:
                        raise RecordConflict("Uploaded size does not match")
                    digest.update(chunk)
            except requests.RequestException:
                raise StorageUnavailable() from None
            if size != asset.byte_size or digest.hexdigest() != asset.sha256:
                raise RecordConflict("Uploaded size/checksum does not match")

    def put_file(self, asset: AssetRef, filename: Path):
        # For FP-39's trusted runner, never exposed as a client-selected path.
        digest = hashlib.sha256()
        with filename.open("rb") as source:
            for chunk in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(chunk)
        if filename.stat().st_size != asset.byte_size or digest.hexdigest() != asset.sha256:
            raise RecordConflict("Local artifact does not match its reference")
        with filename.open("rb") as source:
            with self._request("POST", "/object/" + self._path(asset), data=source,
                               headers={"Content-Type": asset.content_type, "x-upsert": "false", "cache-control": "no-store"}) as response:
                if response.status_code != 200:
                    # Ambiguous retry or another winner: only identical bytes are acceptable.
                    if response.status_code not in (400, 409):
                        raise StorageUnavailable()
        self.verify(asset)

    def delete_exact(self, asset: AssetRef):
        with self._request("DELETE", "/object/" + asset.bucket, json={"prefixes": [asset.object_key]}) as response:
            if response.status_code != 200:
                raise StorageUnavailable()
