"""Private Unix-socket bridge to the existing serial, warm body estimator.

No HTTP route, cloud credential or second model process. Requests are immutable
per job/attempt/frame and cached so a lost acknowledgement can be replayed.
"""
import base64
from concurrent.futures import Future, TimeoutError
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import socketserver
import struct
import threading
import time

MAX_PACKET = 8 * 1024 * 1024


def receive(stream):
    def exact(size):
        chunks = bytearray()
        while len(chunks) < size:
            part = stream.recv(size - len(chunks))
            if not part:
                raise ConnectionError()
            chunks.extend(part)
        return bytes(chunks)
    size = struct.unpack("!I", exact(4))[0]
    if not 0 < size <= MAX_PACKET:
        raise ValueError("Packet limit")
    return json.loads(exact(size))


def send(stream, value):
    body = json.dumps(value, separators=(",", ":"), allow_nan=False).encode()
    if len(body) > MAX_PACKET:
        raise ValueError("Packet limit")
    stream.sendall(struct.pack("!I", len(body)) + body)


def provenance():
    import subprocess
    import numpy as np
    from .providers.local import LocalProvider
    checkpoint = Path(os.environ["SAM3D_CHECKPOINT"])
    upstream = subprocess.check_output(["git", "-C", os.environ["SAM3D_REPO"], "rev-parse", "HEAD"], text=True).strip()
    # start_runtime.sh validates these weights/source before loading. The export
    # profile additionally pins actual configuration and the mesh transform code.
    from pathlib import Path as FilePath
    config = hashlib.sha256((checkpoint.parent / "model_config.yaml").read_bytes()
        + FilePath(__file__).with_name("mesh.py").read_bytes()
        + FilePath(__file__).parent.joinpath("providers/local.py").read_bytes()
        + b"body-keyframes-v1/full").hexdigest()
    topology = hashlib.sha256(np.asarray(LocalProvider().load().faces, dtype="<u4").tobytes()).hexdigest()
    return {"engine": {"modelName": "Meta SAM 3D Body", "upstreamCommit": upstream,
        "checkpointHash": "b5a2f9d305dd02626b967aa2e86021fba07065df66ce7a7e00ffb9664f150abf",
        "configHash": config}, "topologyVersion": "mhr-" + topology[:24] + "-v1"}


class Bridge:
    def __init__(self, root, executor, infer, metadata, *, inference_seconds=180, fatal=None, motion=None):
        self.root, self.executor, self.infer, self.metadata = Path(root), executor, infer, metadata
        self.inference_seconds = inference_seconds
        self.fatal = fatal or (lambda: os._exit(1))
        self.lock, self.tasks = threading.RLock(), {}
        self.calls = 0
        self.motion = motion
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.root.chmod(0o700)
        self.cache = self.root / "cache"
        self.cache.mkdir(exist_ok=True, mode=0o700)
        self.cache.chmod(0o700)

    def validate(self, request):
        expected = {"jobId", "attempt", "frameIndex", "inputHash", "profileVersion", "bbox", "imageBase64", "imageSha256"}
        if not isinstance(request, dict) or set(request) != expected:
            raise ValueError("Request schema")
        if (not isinstance(request["jobId"], str) or not re.fullmatch(r"[a-f0-9]{64}", request["jobId"])
                or type(request["attempt"]) is not int or not 1 <= request["attempt"] <= 5
                or type(request["frameIndex"]) is not int or not 0 <= request["frameIndex"] < 1802
                or request["profileVersion"] != "body-keyframes-v1"):
            raise ValueError("Request identity")
        for field in ["inputHash", "imageSha256"]:
            if not isinstance(request[field], str) or not re.fullmatch(r"[a-f0-9]{64}", request[field]):
                raise ValueError("Request hash")
        bbox = request["bbox"]
        if (not isinstance(bbox, list) or len(bbox) != 4
                or not all(type(n) in (int, float) and math.isfinite(n) and 0 <= n <= 1 for n in bbox)
                or bbox[2]-bbox[0] < .03 or bbox[3]-bbox[1] < .03):
            raise ValueError("Request box")
        image = base64.b64decode(request["imageBase64"], validate=True)
        if not 0 < len(image) <= 2*1024*1024 or not image.startswith(b"\xff\xd8"):
            raise ValueError("Request image")
        if hashlib.sha256(image).hexdigest() != request["imageSha256"]:
            raise ValueError("Request image hash")
        # Read JPEG dimensions without allocating the decoded pixel buffer.
        from PIL import Image
        from io import BytesIO
        with Image.open(BytesIO(image)) as header:
            width, height = header.size
            if header.format != "JPEG" or width > 1280 or height > 720 or min(width, height) < 1:
                raise ValueError("Request image dimensions")
        identity = {key: value for key, value in request.items() if key != "imageBase64"}
        fingerprint = hashlib.sha256(json.dumps({"request": identity, "model": self.metadata},
            sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()
        return image, fingerprint

    def prune(self):
        directories = sorted((p for p in self.cache.iterdir() if p.is_dir() and not p.is_symlink()
                              and re.fullmatch(r"[a-f0-9]{64}", p.name)), key=lambda p: p.stat().st_mtime)
        remaining = len(directories)
        for path in directories:
            task = self.tasks.get(path.name)
            if task and not task.done():
                continue
            if remaining < 100 and path.stat().st_mtime > time.time()-86400:
                continue
            shutil.rmtree(path)
            self.tasks.pop(path.name, None)
            remaining -= 1

    def response(self, directory):
        manifest = json.loads((directory / "result.json").read_text())
        if manifest.get("error"):
            return manifest
        payload = (directory / "body.glb").read_bytes()
        if hashlib.sha256(payload).hexdigest() != manifest["sha256"] or len(payload) != manifest["byteSize"]:
            raise ValueError("Cached artifact changed")
        return {**manifest, "glbBase64": base64.b64encode(payload).decode()}

    def submit(self, request):
        image, fingerprint = self.validate(request)
        with self.lock:
            future = self.tasks.get(fingerprint)
            if future and not future.cancelled():
                return future
            directory = self.cache / fingerprint
            if (directory / "result.json").is_file():
                future = Future()
                future.set_result(self.response(directory))
            else:
                self.prune()
                directory.mkdir(exist_ok=True, mode=0o700)
                # Any partial file here was created by this digest. A surviving
                # process owns our file lock; restart occurs only after it exits.
                (directory / "image.jpg").write_bytes(image)
                (directory / "request.json").write_text(json.dumps({key: value for key, value in request.items()
                    if key != "imageBase64"}, allow_nan=False))
                future = self.executor.submit(self.run, request, directory)
            self.tasks[fingerprint] = future
            return future

    def run(self, request, directory):
        deadline = threading.Timer(self.inference_seconds, self.fatal)
        deadline.daemon = True
        deadline.start()
        try:
            with self.lock:
                self.calls += 1
            info = self.infer(directory / "image.jpg", request["bbox"], directory / "body.glb")
            payload = (directory / "body.glb").read_bytes()
            if not 12 <= len(payload) <= 4*1024*1024 or payload[:4] != b"glTF":
                raise ValueError("Invalid generated mesh")
            manifest = {"status": "ready", **self.metadata, "info": info,
                "sha256": hashlib.sha256(payload).hexdigest(), "byteSize": len(payload)}
        except Exception:
            # Never return source paths, exceptions, upstream dumps or keys.
            manifest = {"error": "model_failed"}
        finally:
            deadline.cancel()
            (directory / "image.jpg").unlink(missing_ok=True)
        temporary = directory / "result.tmp"
        temporary.write_text(json.dumps(manifest, allow_nan=False))
        temporary.replace(directory / "result.json")
        return self.response(directory)

    def start(self):
        self.file_lock = (self.root / "bridge.lock").open("a")
        try:
            fcntl.flock(self.file_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            self.file_lock.close()
            raise RuntimeError("Another owned model bridge is active") from None
        bridge = self
        class Handler(socketserver.BaseRequestHandler):
            def handle(self):
                self.request.settimeout(10)
                try:
                    request = receive(self.request)
                    if request == {"op": "health"}:
                        with bridge.lock:
                            send(self.request, {"status": "ok", "inferenceCalls": bridge.calls,
                                "activeRequests": sum(not task.done() for task in bridge.tasks.values())
                                    + (bridge.motion.active() if bridge.motion else 0),
                                "motionCalls": bridge.motion.calls if bridge.motion else 0,
                                "motionEnabled": bridge.motion is not None, **bridge.metadata})
                        return
                    motion = bridge.motion if isinstance(request, dict) and request.get('op') == 'motion' else None
                    future = motion.submit(request) if motion else bridge.submit(request)
                    while True:
                        try:
                            response = future.result(timeout=.1)
                            break
                        except TimeoutError:
                            import select, socket
                            if select.select([self.request], [], [], 0)[0] and not self.request.recv(1, socket.MSG_PEEK):
                                # Queued work can be cancelled; an in-flight GPU
                                # call finishes into private cache only. The same
                                # serial executor prevents overlapping attempts.
                                if motion: motion.cancel(future)
                                else: future.cancel()
                                return
                    send(self.request, response)
                except (ConnectionError, OSError):
                    pass
                except Exception:
                    try: send(self.request, {"error": "invalid_request"})
                    except OSError: pass
        class Server(socketserver.ThreadingUnixStreamServer):
            daemon_threads = True
            request_queue_size = 4
            def process_request(self, request, address):
                if not self.slots.acquire(blocking=False):
                    request.close()
                    return
                super().process_request(request, address)
            def process_request_thread(self, request, address):
                try: super().process_request_thread(request, address)
                finally: self.slots.release()
        self.socket_path = self.root / "inference.sock"
        self.socket_path.unlink(missing_ok=True)
        try:
            self.server = Server(str(self.socket_path), Handler)
            self.server.slots = threading.BoundedSemaphore(4)
            self.socket_path.chmod(0o600)
            self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
            self.thread.start()
        except Exception:
            self.file_lock.close()
            raise

    def close(self):
        if self.motion:
            self.motion.close()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
        self.socket_path.unlink(missing_ok=True)
        self.file_lock.close()


def start_owned_bridge():
    from . import jobs
    from .providers.local import LocalProvider
    root = Path.home() / ".local/share/footylevels/model-bridge"
    metadata = provenance()
    motion = None
    if os.getenv('FOOTYLEVELS_MOTION_BRIDGE') == '1':
        from .motion_bridge import start_tasks
        motion = start_tasks(root, jobs.EXECUTOR, metadata)
    bridge = Bridge(root, jobs.EXECUTOR, LocalProvider().reconstruct, metadata, motion=motion)
    bridge.start()
    return bridge
