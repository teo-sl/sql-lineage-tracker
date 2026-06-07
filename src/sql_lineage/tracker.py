"""
SQLLineageTracker — public façade that wires the parser and renderers together.

This is the primary entry point for library users:

    tracker = SQLLineageTracker(dialect="postgres")
    tracker.parse_directory("sql/")
    tracker.print_report()
    tracker.render_table_lineage(output_dir="output")
    tracker.render_interactive_html(output_dir="output")
"""

from __future__ import annotations

from collections import OrderedDict
from typing import Dict, List, Optional, Tuple

from sql_lineage.models import ColumnLineage, TableInfo
from sql_lineage.parser import SQLParser
from sql_lineage.renderers import GraphvizRenderer, HtmlRenderer


class SQLLineageTracker:
    """Parse SQL files and produce table / column lineage graphs."""

    def __init__(self, dialect: str = "postgres") -> None:
        self.dialect = dialect
        self._parser = SQLParser(dialect=dialect)

    # ─────────────────────────────────────────────────────────────
    # Convenience property — expose the parsed table registry
    # ─────────────────────────────────────────────────────────────

    @property
    def tables(self) -> Dict[str, TableInfo]:
        return self._parser.tables

    # ─────────────────────────────────────────────────────────────
    # Parsing
    # ─────────────────────────────────────────────────────────────

    def parse_directory(self, sql_dir: str) -> None:
        """Scan *sql_dir* for ``*.sql`` files and extract lineage."""
        self._parser.parse_directory(sql_dir)

    # ─────────────────────────────────────────────────────────────
    # Deep (transitive) lineage query
    # ─────────────────────────────────────────────────────────────

    def resolve_to_raw(
        self, table_name: str, col_name: str
    ) -> List[Tuple[str, str]]:
        """Recursively resolve a column all the way back to source tables."""
        return self._parser.resolve_to_raw(table_name, col_name)

    # ─────────────────────────────────────────────────────────────
    # Renderers
    # ─────────────────────────────────────────────────────────────

    def render_table_lineage(
        self, output_dir: str = "output", fmt: str = "png"
    ) -> str:
        return GraphvizRenderer(self.tables).render_table_lineage(
            output_dir=output_dir, fmt=fmt
        )

    def render_column_lineage(
        self,
        output_dir: str = "output",
        fmt: str = "png",
        target: Optional[str] = None,
    ) -> str:
        return GraphvizRenderer(self.tables).render_column_lineage(
            output_dir=output_dir, fmt=fmt, target=target
        )

    def render_deep_lineage(
        self,
        target: str,
        output_dir: str = "output",
        fmt: str = "png",
    ) -> str:
        return GraphvizRenderer(self.tables).render_deep_lineage(
            target=target,
            output_dir=output_dir,
            fmt=fmt,
            resolve_to_raw_fn=self.resolve_to_raw,
        )

    def render_interactive_html(self, output_dir: str = "output") -> str:
        return HtmlRenderer(self.tables).render(output_dir=output_dir)

    # ─────────────────────────────────────────────────────────────
    # Text report
    # ─────────────────────────────────────────────────────────────

    def print_report(self) -> None:
        """Print a human-readable lineage summary to stdout."""
        bar = "═" * 72
        thin = "─" * 68

        print(f"\n{bar}")
        print("  LINEAGE REPORT")
        print(bar)

        for name, info in self.tables.items():
            kind = "SOURCE" if info.is_source else "DERIVED"
            print(f"\n┌─ {kind}: {name}  ({info.sql_file})")
            if info.source_tables:
                print(f"│  Depends on: {', '.join(sorted(info.source_tables))}")
            print(f"│  {thin}")
            for col in info.columns:
                if info.is_source:
                    print(f"│   {col.name}")
                else:
                    srcs = ", ".join(f"{t}.{c}" for t, c in col.sources)
                    print(f"│   {col.name:<32} ← {srcs}")
            print("└" + "─" * 71)

        last_name = list(self.tables.keys())[-1]
        last_info = self.tables[last_name]
        if not last_info.is_source:
            print(f"\n{bar}")
            print(f"  DEEP LINEAGE: {last_name}  →  raw sources")
            print(bar)
            for col in last_info.columns:
                raw = self.resolve_to_raw(last_name, col.name)
                srcs = ", ".join(f"{t}.{c}" for t, c in raw)
                print(f"  {col.name:<32} ← {srcs}")
            print()
