"""
Command-line interface for SQL Lineage Tracker.

Usage
-----
    uv run sql-lineage examples/sql/
    uv run sql-lineage examples/sql/ --target mart_customer_360
    uv run sql-lineage examples/sql/ -o output/ -f svg -i
"""

from __future__ import annotations

import argparse
import sys

from sql_lineage.tracker import SQLLineageTracker


def build_arg_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="sql-lineage",
        description="SQL Lineage Tracker — table & column lineage from SQL files",
    )
    ap.add_argument("sql_dir", help="directory containing .sql files")
    ap.add_argument(
        "--target", "-t",
        default=None,
        help="target table for column / deep lineage (default: last table parsed)",
    )
    ap.add_argument(
        "--output-dir", "-o",
        default="output",
        help="output directory (default: output/)",
    )
    ap.add_argument(
        "--format", "-f",
        default="png",
        choices=("png", "svg", "pdf"),
        help="graph output format (default: png)",
    )
    ap.add_argument(
        "--dialect", "-d",
        default="postgres",
        help="SQL dialect passed to sqlglot (default: postgres)",
    )
    ap.add_argument(
        "--interactive", "-i",
        action="store_true",
        help="also generate an interactive HTML graph",
    )
    return ap


def main(argv=None) -> int:
    args = build_arg_parser().parse_args(argv)

    tracker = SQLLineageTracker(dialect=args.dialect)
    tracker.parse_directory(args.sql_dir)

    if not tracker.tables:
        print("No tables parsed — nothing to render.")
        return 1

    tracker.print_report()

    target = args.target or list(tracker.tables.keys())[-1]

    print("🎨  Generating visualisations …\n")
    tracker.render_table_lineage(output_dir=args.output_dir, fmt=args.format)
    tracker.render_column_lineage(output_dir=args.output_dir, fmt=args.format)
    tracker.render_column_lineage(output_dir=args.output_dir, fmt=args.format, target=target)
    tracker.render_deep_lineage(target=target, output_dir=args.output_dir, fmt=args.format)

    if args.interactive:
        tracker.render_interactive_html(output_dir=args.output_dir)

    print(f"\n✨  Done! Check the '{args.output_dir}/' directory.\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
