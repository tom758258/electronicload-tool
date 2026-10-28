"""Help and version entry point for Electronic Load Tool."""

import argparse
from importlib.metadata import version


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="electronicload-tool",
        description="Electronic Load Tool. Instrument control is not available.",
        allow_abbrev=False,
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {version('electronicload-tool')}",
    )
    parser.parse_args(argv)
    parser.print_help()
    return 0
