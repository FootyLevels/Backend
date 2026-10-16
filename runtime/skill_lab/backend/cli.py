import argparse
import json
from pathlib import Path

from . import jobs, store, video, motion
from .providers import capabilities


def main():
    parser = argparse.ArgumentParser(description="Skill Lab video → key frames → SAM 3D Body meshes")
    commands = parser.add_subparsers(dest="command", required=True)
    prepare = commands.add_parser("prepare", help="Extract key frames without needing a GPU")
    prepare.add_argument("video", type=Path)
    prepare.add_argument("--title", default="Football skill")
    prepare.add_argument("--id")
    prepare.add_argument("--count", type=int, choices=range(1, 25), default=6)
    prepare.add_argument("--frames", type=Path, help="JSON array of curated timestamp/label/note/bbox entries")
    run = commands.add_parser("reconstruct", help="Generate missing meshes; rerun to retry failed frames")
    run.add_argument("project_id")
    run.add_argument("--provider", choices=["local", "fal"], default="local")
    motion_parser = commands.add_parser("motion", help="Track and reconstruct every video frame plus football")
    motion_parser.add_argument("project_id")
    commands.add_parser("doctor", help="Show inference configuration (never credentials)")
    args = parser.parse_args()
    if args.command == "prepare":
        curated = json.loads(args.frames.read_text()) if args.frames else None
        project = video.prepare(args.video.resolve(), args.title, args.count, args.id, curated)
        print(json.dumps({"id": project["id"], "frames": len(project["frames"])}))
    elif args.command == "reconstruct":
        jobs.start(args.project_id, args.provider, background=False)
        job = store.read(args.project_id)["job"]
        print(json.dumps(job, indent=2))
        if job["failed"]:
            raise SystemExit(1)
    elif args.command == "motion":
        result = motion.start(args.project_id, background=False)
        print(json.dumps(result, indent=2))
        if result['status'] != 'complete':
            raise SystemExit(1)
    else:
        print(json.dumps(capabilities(), indent=2))


if __name__ == "__main__":
    main()
