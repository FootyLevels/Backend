#!/usr/bin/env python3
"""Independent authenticated API on loopback, until FP-39 integrates model routes."""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
from cloud_config import load

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("environment", choices=["dev", "demo"])
    parser.add_argument("--port", type=int, default=8011)
    args = parser.parse_args()
    load(args.environment)
    import uvicorn
    uvicorn.run("api.cloud:app_factory", factory=True, host="127.0.0.1", port=args.port, workers=1, access_log=False)
