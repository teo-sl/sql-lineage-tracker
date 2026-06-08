"""
Graphviz-based rendering for SQL lineage graphs.

Produces:
  - Table-level DAG
  - Column-level DAG (full or scoped to a target table)
  - Deep lineage graph (raw sources → final table only)
"""

from __future__ import annotations

import os
import re
import sys
from collections import defaultdict
from typing import Dict, Optional, Set, Tuple

try:
    import graphviz
except ImportError:
    sys.exit(
        "Missing dependency: pip install graphviz\n"
        "Also install the system package: brew install graphviz  (macOS)"
    )

from sql_lineage.models import TableInfo


# ─────────────────────────────────────────────────────────────────────────────
# Layer / colour helpers
# ─────────────────────────────────────────────────────────────────────────────

LAYER_STYLE = {
    "raw":  {"hdr": "#238636", "border": "#2ea043", "badge": "📦 SOURCE"},
    "stg":  {"hdr": "#8957e5", "border": "#a371f7", "badge": "🔧 STAGING"},
    "int":  {"hdr": "#d29922", "border": "#e3b341", "badge": "⚙️  INTERMEDIATE"},
    "mart": {"hdr": "#da3633", "border": "#58a6ff", "badge": "📊 MART"},
    "cte":  {"hdr": "#0a3069", "border": "#1f6feb", "badge": "🔗 CTE"},
    "sub":  {"hdr": "#4c5258", "border": "#6e7681", "badge": "↪️  SUBQUERY"},
}

_EDGE_PALETTE = ["#f47067", "#d2a8ff", "#ffa657", "#79c0ff", "#7ee787", "#ff9bce"]


def get_layer(name: str) -> str:
    # Match both old "[cte]" and new scoped "[cte:parent]" prefixes
    if re.match(r"^\[cte[:\]]", name):
        return "cte"
    if re.match(r"^\[subquery[:\]]", name):
        return "sub"
    for prefix in ("raw", "stg", "int", "mart"):
        if name.startswith(prefix):
            return prefix
    return "raw"


def _node_id(name: str) -> str:
    """Return a DOT-safe node ID for *name*.

    Graphviz node IDs that contain colons, brackets, or spaces must be
    carefully quoted -- and colons inside an ID still confuse the port
    syntax even when the whole string is double-quoted.  The safest
    approach is to replace every non-alphanumeric character with an
    underscore, which guarantees a valid bare identifier while keeping
    the string recognisably related to the original name.
    """
    return re.sub(r"[^A-Za-z0-9_]", "_", name)


def build_html_table_label(
    info: TableInfo,
    *,
    highlight_cols: Optional[Set[str]] = None,
) -> str:
    """Build an HTML-table label for a Graphviz node."""
    style = LAYER_STYLE.get(get_layer(info.name), LAYER_STYLE["raw"])
    hdr_bg = style["hdr"]
    border = style["border"]
    badge = style["badge"]

    rows = ""
    for c in info.columns:
        hl = highlight_cols and c.name in highlight_cols
        fg = "#58a6ff" if hl else "#c9d1d9"
        marker = " ●" if hl else ""
        rows += (
            f'<TR><TD PORT="{c.name}" ALIGN="LEFT">'
            f'<FONT COLOR="{fg}">{c.name}{marker}</FONT></TD></TR>\n'
        )

    return f"""<
<TABLE BORDER="1" CELLBORDER="0" CELLSPACING="0" CELLPADDING="5"
       BGCOLOR="#161b22" COLOR="{border}" STYLE="ROUNDED">
  <TR><TD BGCOLOR="{hdr_bg}" COLSPAN="1" CELLPADDING="6">
    <FONT COLOR="white" POINT-SIZE="11"><B>{badge}  ·  {info.name}</B></FONT>
  </TD></TR>
  {rows}
</TABLE>>"""


def _base_graph_attrs(**extra) -> dict:
    return {
        "bgcolor": "#0d1117",
        "fontname": "Helvetica Neue",
        "pad": "0.8",
        "nodesep": "0.7",
        "dpi": "150",
        **extra,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Renderers
# ─────────────────────────────────────────────────────────────────────────────

class GraphvizRenderer:
    """Render static lineage diagrams from a table registry."""

    def __init__(self, tables: Dict[str, TableInfo]) -> None:
        self.tables = tables

    def render_table_lineage(
        self, output_dir: str = "output", fmt: str = "png"
    ) -> str:
        """Render a table-level DAG (one node per table, edges = dependency)."""
        dot = graphviz.Digraph("Table Lineage", format=fmt)
        dot.attr(
            rankdir="LR",
            ranksep="1.6",
            label="TABLE-LEVEL  LINEAGE",
            labelloc="t",
            fontcolor="#58a6ff",
            fontsize="18",
            **_base_graph_attrs(),
        )
        dot.attr("node", fontname="Helvetica Neue", shape="plain")
        dot.attr("edge", color="#30363d", penwidth="2", arrowsize="0.8")

        for name, info in self.tables.items():
            dot.node(_node_id(name), label=build_html_table_label(info))

        for name, info in self.tables.items():
            edge_color = LAYER_STYLE.get(get_layer(name), LAYER_STYLE["raw"])["hdr"]
            for src in info.source_tables:
                dot.edge(_node_id(src), _node_id(name), color=edge_color, penwidth="2.2")

        return self._save(dot, output_dir, "table_lineage", "📊 Table lineage")

    def render_column_lineage(
        self,
        output_dir: str = "output",
        fmt: str = "png",
        target: Optional[str] = None,
        resolve_to_raw_fn=None,
    ) -> str:
        """Render column-level edges between all tables, or just upstream of *target*."""
        edges: Set[Tuple[str, str, str, str]] = set()
        tables_needed: Set[str] = set()

        if target:
            self._collect_edges_recursive(target, edges, tables_needed)
            graph_title = f"COLUMN-LEVEL  LINEAGE  →  {target}"
            filename = f"column_lineage_{target}"
        else:
            for tname, tinfo in self.tables.items():
                if tinfo.is_source:
                    continue
                tables_needed.add(tname)
                for col in tinfo.columns:
                    for st, sc in col.sources:
                        edges.add((st, sc, tname, col.name))
                        tables_needed.add(st)
            graph_title = "COLUMN-LEVEL  LINEAGE  (ALL)"
            filename = "column_lineage_full"

        dot = graphviz.Digraph(graph_title, format=fmt)
        dot.attr(
            rankdir="LR",
            ranksep="2.2",
            label=graph_title,
            labelloc="t",
            fontcolor="#58a6ff",
            fontsize="16",
            **_base_graph_attrs(),
        )
        dot.attr("node", fontname="Helvetica Neue", shape="plain")
        dot.attr("edge", penwidth="1.4", arrowsize="0.55")

        table_cols: Dict[str, Set[str]] = defaultdict(set)
        for st, sc, tt, tc in edges:
            table_cols[st].add(sc)
            table_cols[tt].add(tc)

        for tname in sorted(tables_needed):
            info = self.tables.get(tname)
            if not info:
                continue
            dot.node(_node_id(tname), label=build_html_table_label(info, highlight_cols=table_cols.get(tname)))

        for i, (st, sc, tt, tc) in enumerate(sorted(edges)):
            dot.edge(f"{_node_id(st)}:{sc}:e", f"{_node_id(tt)}:{tc}:w", color=_EDGE_PALETTE[i % len(_EDGE_PALETTE)])

        label = f"📊 Column lineage ({target or 'ALL'})"
        return self._save(dot, output_dir, filename, label)

    def render_deep_lineage(
        self,
        target: str,
        output_dir: str = "output",
        fmt: str = "png",
        resolve_to_raw_fn=None,
    ) -> str:
        """Show raw-source → final-table column edges, skipping intermediate tables."""
        info = self.tables.get(target)
        if not info:
            print(f"  ⚠  Table '{target}' not found")
            return ""

        if resolve_to_raw_fn is None:
            raise ValueError("resolve_to_raw_fn is required for render_deep_lineage")

        dot = graphviz.Digraph(f"Deep Lineage: {target}", format=fmt)
        dot.attr(
            rankdir="LR",
            ranksep="2.0",
            label=f"DEEP  LINEAGE  →  {target}   (traced to raw sources)",
            labelloc="t",
            fontcolor="#58a6ff",
            fontsize="16",
            **_base_graph_attrs(nodesep="0.6"),
        )
        dot.attr("node", fontname="Helvetica Neue", shape="plain")
        dot.attr("edge", penwidth="1.2", arrowsize="0.5")

        edges: Set[Tuple[str, str, str]] = set()
        tables_needed: Set[str] = {target}

        for col in info.columns:
            for st, sc in resolve_to_raw_fn(target, col.name):
                edges.add((st, sc, col.name))
                tables_needed.add(st)

        table_cols: Dict[str, Set[str]] = defaultdict(set)
        for st, sc, tc in edges:
            table_cols[st].add(sc)
            table_cols[target].add(tc)

        for tname in sorted(tables_needed):
            tinfo = self.tables.get(tname)
            if not tinfo:
                continue
            dot.node(_node_id(tname), label=build_html_table_label(tinfo, highlight_cols=table_cols.get(tname)))

        for i, (st, sc, tc) in enumerate(sorted(edges)):
            dot.edge(f"{_node_id(st)}:{sc}:e", f"{_node_id(target)}:{tc}:w", color=_EDGE_PALETTE[i % len(_EDGE_PALETTE)])

        return self._save(dot, output_dir, f"deep_lineage_{target}", "📊 Deep lineage")

    # ─────────────────────────────────────────────────────────────
    # Internal helpers
    # ─────────────────────────────────────────────────────────────

    def _collect_edges_recursive(
        self,
        table_name: str,
        edges: Set[Tuple[str, str, str, str]],
        tables_needed: Set[str],
    ) -> None:
        if table_name in tables_needed:
            return
        tables_needed.add(table_name)
        info = self.tables.get(table_name)
        if not info or info.is_source:
            return
        for col in info.columns:
            for st, sc in col.sources:
                edges.add((st, sc, table_name, col.name))
                self._collect_edges_recursive(st, edges, tables_needed)

    @staticmethod
    def _save(dot: graphviz.Digraph, output_dir: str, filename: str, label: str) -> str:
        os.makedirs(output_dir, exist_ok=True)
        path = os.path.join(output_dir, filename)
        rendered = dot.render(path, cleanup=True)
        print(f"  {label:<32} → {rendered}")
        return rendered