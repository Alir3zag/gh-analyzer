from cli import parse_and_validate_args

def main(argv: list[str] | None = None) -> int:
    repo = parse_and_validate_args(argv)
    print(repo)
    return 0

if __name__ == "__main__":
    import sys
    sys.exit(main())
