import argparse
import re
import sys

def valid_path(path: str) -> str:
    """Validating Repository Path: the format is required to look like: username/repository"""
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", path):
        raise ValueError("Wrong Path Format")
    return path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("path")
    args = parser.parse_args(argv)

    try:
        print(valid_path(args.path))
    except ValueError:
        print("Wrong Path", file=sys.stderr)
        return 2

    return 0


if __name__ == "__main__":
    sys.exit(main())
