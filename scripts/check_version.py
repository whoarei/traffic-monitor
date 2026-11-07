"""Validate a stable release version and its optional Git tag."""
import argparse
from pathlib import Path
import re
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.version import VERSION


def validate(version, tag=None):
    if not isinstance(version, str) or not re.fullmatch(r"(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)", version):
        raise ValueError("Release version must be MAJOR.MINOR.PATCH without leading zeroes")
    if tag is not None and tag != "v" + version:
        raise ValueError(f"Tag {tag!r} must match app/version.py: v{version}")
    return version


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tag", help="Existing Git tag, for example v1.3.0")
    args = parser.parse_args()
    try:
        print(validate(VERSION, args.tag))
    except ValueError as error:
        parser.exit(1, str(error) + "\n")


if __name__ == "__main__":
    main()
