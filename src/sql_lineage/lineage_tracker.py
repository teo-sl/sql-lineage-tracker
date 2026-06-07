#!/usr/bin/env python3
"""
SQL Lineage Tracker
===================
Parse a directory of SQL files (CREATE TABLE / CREATE TABLE AS SELECT)
and produce beautiful table-level and column-level lineage DAGs
via Graphviz.

Requirements
------------
    pip install sqlglot graphviz
    brew install graphviz          # macOS system dependency

Usage
-----
    python lineage_tracker.py sql/
    python lineage_tracker.py sql/ --target mart_customer_360
    python lineage_tracker.py sql/ -o output/ -f svg
"""

from __future__ import annotations

import argparse
import os
import sys
from collections import OrderedDict, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

import sqlglot
from sqlglot import exp

try:
    import graphviz
except ImportError:
    sys.exit(
        "Missing dependency: pip install graphviz\n"
        "Also install the system package: brew install graphviz  (macOS)"
    )



# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Data models
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


@dataclass
class ColumnLineage:
    """One output column and the source columns it is derived from."""

    name: str
    expression: str  # original SQL expression text
    sources: List[Tuple[str, str]]  # [(source_table, source_col), ...]
    data_type: str = ""   # SQL data type (from DDL or inferred)


@dataclass
class TableInfo:
    """Everything we know about a parsed table."""

    name: str
    columns: List[ColumnLineage]
    is_source: bool  # True for plain CREATE TABLE (raw / source data)
    source_tables: Set[str] = field(default_factory=set)
    sql_file: str = ""
    filters: List[str] = field(default_factory=list)
    joins: List[Dict] = field(default_factory=list)  # [{left, right, condition, kind}]
    is_union: bool = False
    union_branches: List[Dict] = field(default_factory=list)  # [{table, operator}]
    group_by: List[str] = field(default_factory=list)



# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Tracker
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class SQLLineageTracker:
    """Parse SQL files and extract table + column lineage."""

    def __init__(self, dialect: str = "postgres"):
        self.dialect = dialect
        self.tables: Dict[str, TableInfo] = OrderedDict()

    # ── public API ───────────────────────────────────────────────

    def parse_directory(self, sql_dir: str) -> None:
        """Scan *sql_dir* for ``*.sql`` files (sorted by name) and parse them."""
        sql_files = sorted(Path(sql_dir).glob("*.sql"))
        if not sql_files:
            print(f"⚠  No .sql files found in {sql_dir}")
            return

        print(f"\n📂  Parsing {len(sql_files)} SQL files from {sql_dir}/\n")
        for f in sql_files:
            self._parse_file(f)
        
        self.resolve_inherited_types()
        print()

    # ── parsing internals ────────────────────────────────────────

    def resolve_inherited_types(self) -> None:
        """Second pass: propagate data types forward from source columns to derived columns."""
        resolved_cache = {}

        def _get_type(table_name: str, col_name: str, visited: Set[Tuple[str, str]]) -> str:
            key = (table_name, col_name)
            if key in resolved_cache:
                return resolved_cache[key]
            
            if key in visited:  # Prevent infinite loops in recursive CTEs
                return "UNKNOWN"
            visited.add(key)

            tinfo = self.tables.get(table_name)
            if not tinfo:
                return "UNKNOWN"
            
            # Find the column
            col = next((c for c in tinfo.columns if c.name == col_name), None)
            if not col:
                return "UNKNOWN"
            
            # If it has a concrete type already, we are good
            if col.data_type and col.data_type not in ("INHERITED", "UNKNOWN"):
                resolved_cache[key] = col.data_type
                return col.data_type
            
            # Otherwise, resolve from sources
            inferred_types = set()
            for src_t, src_c in col.sources:
                t = _get_type(src_t, src_c, visited)
                if t not in ("INHERITED", "UNKNOWN", ""):
                    inferred_types.add(t)
            
            # If we found exactly one consistent type, use it. If mixed or none, UNKNOWN.
            if len(inferred_types) == 1:
                res = inferred_types.pop()
            else:
                res = "UNKNOWN"
                
            resolved_cache[key] = res
            return res

        for tname, tinfo in self.tables.items():
            for col in tinfo.columns:
                if not col.data_type or col.data_type in ("INHERITED", "UNKNOWN"):
                    t = _get_type(tname, col.name, set())
                    if t != "UNKNOWN":
                        col.data_type = t


    def _parse_file(self, filepath: Path) -> None:
        sql = filepath.read_text()
        try:
            stmts = sqlglot.parse(sql, dialect=self.dialect)
        except Exception as exc:
            print(f"  ⚠  Parse error in {filepath.name}: {exc}")
            return
        for stmt in stmts:
            if not stmt:
                continue
            if isinstance(stmt, exp.Create):
                self._analyze_create(stmt, filepath.name)
            elif isinstance(stmt, (exp.Select, exp.Union)):
                table_name = filepath.stem.lower()
                self._analyze_ctas(table_name, stmt, filepath.name)

    def _analyze_create(self, stmt: exp.Create, filename: str) -> None:
        # Table name lives inside the Schema wrapper
        schema = stmt.this
        table_node = schema.this if isinstance(schema, exp.Schema) else stmt.find(exp.Table)
        if not table_node:
            return
        table_name = table_node.name.lower()

        ctas_expr = stmt.expression  # non-None for CTAS
        if ctas_expr is not None:
            self._analyze_ctas(table_name, ctas_expr, filename)
        elif stmt.find(exp.ColumnDef):
            self._analyze_source_table(table_name, stmt, filename)

    # ── source (raw) tables ──────────────────────────────────────

    def _analyze_source_table(
        self, table_name: str, stmt: exp.Create, filename: str
    ) -> None:
        columns = []
        for cdef in stmt.find_all(exp.ColumnDef):
            n = cdef.name.lower()
            # Extract declared SQL type from DDL, e.g. VARCHAR(255), INTEGER, TIMESTAMP
            dtype = cdef.kind.sql(dialect=self.dialect).upper() if cdef.kind else ""
            columns.append(ColumnLineage(
                name=n,
                expression=n,
                sources=[(table_name, n)],
                data_type=dtype,
            ))
        self.tables[table_name] = TableInfo(
            name=table_name,
            columns=columns,
            is_source=True,
            sql_file=filename,
        )
        print(
            f"  ✅ {filename:<35} → source table:  {table_name}  "
            f"({len(columns)} cols)"
        )

    # ── derived (CTAS) tables ────────────────────────────────────

    def _analyze_ctas(
        self, table_name: str, ctas_expr: exp.Expression, filename: str
    ) -> None:
        cte_map: Dict[str, str] = {}

        for cte_node in ctas_expr.find_all(exp.CTE):
            short_name = (cte_node.alias or "").lower()
            if short_name:
                cte_body = cte_node.this
                if isinstance(cte_body, (exp.Select, exp.Union)):
                    full_cte_name = f"[cte] {short_name}"
                    cte_map[short_name] = full_cte_name
                    self._analyze_query(full_cte_name, cte_body, filename, cte_map)

        main_query = self._find_main_query(ctas_expr)
        if main_query:
            self._analyze_query(table_name, main_query, filename, cte_map)

    def _get_union_branches(self, node: exp.Expression) -> List[Tuple]:
        """Recursively flatten a Union/Intersect/Except tree into [(Select, operator_label)].
        The first branch has operator=None; each subsequent branch has the operator
        type that connects it to the previous one (e.g. 'UNION ALL', 'INTERSECT')."""
        if isinstance(node, exp.Select):
            return [(node, None)]
        if isinstance(node, (exp.Union, exp.Intersect, exp.Except)):
            # Determine operator type
            if isinstance(node, exp.Intersect):
                op = "INTERSECT"
            elif isinstance(node, exp.Except):
                op = "EXCEPT"
            else:
                # exp.Union: distinct=True means UNION (dedup), False means UNION ALL
                distinct = node.args.get("distinct")
                op = "UNION" if distinct else "UNION ALL"

            left = self._get_union_branches(node.this)
            right = self._get_union_branches(node.expression)
            # Tag the first item of the right side with the operator
            if right:
                right[0] = (right[0][0], op)
            return left + right
        return []

    def _analyze_query(
        self, table_name: str, query_node: exp.Expression, filename: str, cte_map: Dict[str, str]
    ) -> None:
        branch_tuples = self._get_union_branches(query_node)
        if not branch_tuples:
            return

        all_columns: Dict[str, ColumnLineage] = {}
        all_filters = []
        all_joins: List[Dict] = []
        all_sources: Set[str] = set()
        union_branches: List[Dict] = []  # [{table, operator}]
        all_group_by: List[str] = []
        
        for select_node, operator in branch_tuples:
            alias_map = self._build_alias_map(select_node, filename, cte_map)
            
            # Resolve FROM table name
            from_clause = select_node.args.get("from_")
            from_table = ""
            if from_clause and isinstance(from_clause.this, exp.Table):
                raw = from_clause.this.name.lower()
                from_table = alias_map.get(from_clause.this.alias.lower() if from_clause.this.alias else raw, raw)
            elif from_clause and isinstance(from_clause.this, exp.Subquery):
                sq_alias = (from_clause.this.alias or "").lower()
                from_table = alias_map.get(sq_alias, f"[subquery] {sq_alias}")

            # Track union branch with operator label
            if len(branch_tuples) > 1 and from_table:
                union_branches.append({"table": from_table, "operator": operator})

            if select_node.args.get("where"):
                all_filters.append(select_node.args["where"].sql(dialect=self.dialect))
            if select_node.args.get("having"):
                all_filters.append(select_node.args["having"].sql(dialect=self.dialect))

            # GROUP BY columns
            group_node = select_node.args.get("group")
            if group_node:
                for g_expr in group_node.expressions:
                    g_copy = g_expr.copy()
                    for col in g_copy.find_all(exp.Column):
                        if col.table:
                            t_lower = col.table.lower()
                            resolved = alias_map.get(t_lower)
                            if resolved:
                                col.set("table", exp.to_identifier(resolved))
                    
                    g_sql = g_copy.sql(dialect=self.dialect)
                    if g_sql not in all_group_by:
                        all_group_by.append(g_sql)
                
            for join in select_node.args.get("joins") or []:
                # Build full join type: [side] [kind] JOIN  e.g. LEFT JOIN, INNER JOIN, CROSS JOIN
                side = str(join.args.get("side", "") or "").strip().upper()
                kind = str(join.args.get("kind", "") or "").strip().upper()
                parts = [p for p in [side, kind, "JOIN"] if p]
                join_type = " ".join(parts)

                join_src = join.this
                if isinstance(join_src, exp.Subquery):
                    right_name = (join_src.alias or f"sq_{id(join_src)}").lower()
                    right_name = f"[subquery] {right_name}"
                elif isinstance(join_src, exp.Table):
                    raw_r = join_src.name.lower()
                    right_name = alias_map.get(join_src.alias.lower() if join_src.alias else raw_r, raw_r)
                else:
                    right_name = join_src.sql(dialect=self.dialect)

                # Extract ON / USING condition text
                on_expr = join.args.get("on")
                using_expr = join.args.get("using")
                if on_expr:
                    condition = on_expr.sql(dialect=self.dialect)
                elif using_expr:
                    condition = "USING (" + ", ".join(u.sql(dialect=self.dialect) for u in using_expr) + ")"
                else:
                    condition = ""

                all_joins.append({
                    "left": from_table,
                    "right": right_name,
                    "kind": join_type,
                    "condition": condition
                })
                
            # If the FROM table has a PIVOT, extract the pivoted columns and their generation logic
            pivot_columns = {}
            if from_clause and isinstance(from_clause.this, exp.Table) and from_clause.this.args.get("pivots"):
                for pivot in from_clause.this.args["pivots"]:
                    if not pivot.args.get("unpivot"):
                        # Try to construct a readable PIVOT expression and infer its type
                        agg_expr_str = ""
                        dtype = "INHERITED"
                        if pivot.args.get("expressions"):
                            agg_expr = pivot.args["expressions"][0]
                            agg_expr_str = agg_expr.sql(dialect=self.dialect)
                            dtype = self._infer_type(agg_expr)
                        
                        field_expr = ""
                        if pivot.args.get("fields"):
                            field_expr = pivot.args["fields"][0].this.sql(dialect=self.dialect)
                            for e in pivot.args["fields"][0].expressions:
                                if isinstance(e, exp.PivotAlias):
                                    col_alias = e.args.get("alias")
                                    if col_alias is None:
                                        col_alias = getattr(e, "alias", None)
                                    if col_alias:
                                        col_name = col_alias if isinstance(col_alias, str) else getattr(col_alias, "name", str(col_alias))
                                        col_name = col_name.lower()
                                        val_expr = e.this.sql(dialect=self.dialect)
                                        pivot_columns[col_name] = (f"PIVOT: {agg_expr_str} FOR {field_expr} = {val_expr}", dtype)
                        
                        # Fallback for columns
                        if not pivot_columns and pivot.args.get("columns"):
                            for c in pivot.args["columns"]:
                                col_name = c.name.lower()
                                pivot_columns[col_name] = (f"PIVOT operation on {agg_expr_str}" if agg_expr_str else "PIVOT operation", dtype)

            for sel_expr in select_node.expressions:
                if isinstance(sel_expr, exp.Star) and pivot_columns:
                    # Expand * into the pivoted columns manually
                    for pcol, (pexpr, pdtype) in pivot_columns.items():
                        c = ColumnLineage(
                            name=pcol,
                            expression=pexpr,
                            sources=[(from_table, pcol)],
                            data_type=pdtype
                        )
                        if c.name not in all_columns:
                            all_columns[c.name] = c
                    continue

                col = self._trace_output_column_shallow(sel_expr, alias_map, cte_map)
                if col:
                    if col.name in pivot_columns:
                        col.expression = pivot_columns[col.name][0]
                        dtype = pivot_columns[col.name][1]
                    else:
                        # Infer data type from the expression
                        inner_expr = sel_expr.this if isinstance(sel_expr, exp.Alias) else sel_expr
                        dtype = self._infer_type(inner_expr)
                        
                    if col.name not in all_columns:
                        col.data_type = dtype
                        all_columns[col.name] = col
                    else:
                        existing = all_columns[col.name]
                        for src in col.sources:
                            if src not in existing.sources:
                                existing.sources.append(src)

        columns = list(all_columns.values())
        for c in columns:
            for st, _ in c.sources:
                all_sources.add(st)
                
        self.tables[table_name] = TableInfo(
            name=table_name,
            columns=columns,
            is_source=False,
            source_tables=all_sources,
            sql_file=filename,
            filters=all_filters,
            joins=all_joins,
            is_union=len(branch_tuples) > 1,
            union_branches=union_branches,
            group_by=list(all_group_by)
        )
        print(
            f"  ✅ {filename:<35} → parsed node:     {table_name}  "
            f"({len(columns)} cols, {len(branch_tuples)} branches)"
        )

    # ── AST helpers ──────────────────────────────────────────────

    @staticmethod
    def _infer_type(expr: exp.Expression) -> str:
        """Best-effort type inference from a SQL expression."""
        # Unwrap alias
        if isinstance(expr, exp.Alias):
            return SQLLineageTracker._infer_type(expr.this)
        # Explicit CAST
        if isinstance(expr, exp.Cast):
            return expr.to.sql().upper()
        # TryCast
        if isinstance(expr, exp.TryCast):
            return expr.to.sql().upper()
        # Lag / Lead / First / Last → inherit (we can't know without source)
        _inherit_fns = []
        for cls_name in ("Lag", "Lead", "First", "Last", "NthValue"):
            cls = getattr(exp, cls_name, None)
            if cls:
                _inherit_fns.append(cls)
        if _inherit_fns and isinstance(expr, tuple(_inherit_fns)):
            return "INHERITED"
        # Date/time functions
        _date_fns = []
        for cls_name in ("DateTrunc", "TsOrDsToDate", "CurrentDate", "Date",
                         "DateFromParts", "ToDate", "DateAdd", "DateSub",
                         "TimestampTrunc"):
            cls = getattr(exp, cls_name, None)
            if cls:
                _date_fns.append(cls)
        if _date_fns and isinstance(expr, tuple(_date_fns)):
            return "DATE"
        _ts_fns = []
        for cls_name in ("CurrentTimestamp", "Now", "TimeToStr", "StrToTime",
                         "CurrentTime", "UnixToTime", "TimeAdd"):
            cls = getattr(exp, cls_name, None)
            if cls:
                _ts_fns.append(cls)
        if _ts_fns and isinstance(expr, tuple(_ts_fns)):
            return "TIMESTAMP"
        # String functions
        _str_fns = []
        for cls_name in ("Lower", "Upper", "Trim", "Concat", "Substring",
                         "RegexpExtract", "ToString", "ToChar", "LTrim",
                         "RTrim", "Left", "Right", "Length", "CharLength",
                         "Replace", "Initcap", "SafeConcat", "Repeat"):
            cls = getattr(exp, cls_name, None)
            if cls:
                _str_fns.append(cls)
        if _str_fns and isinstance(expr, tuple(_str_fns)):
            return "TEXT"
        # Arithmetic → numeric
        _arith_fns = []
        for cls_name in ("Add", "Sub", "Mul", "Div", "Paren", "Round",
                         "Floor", "Ceil", "Abs", "Pow", "Mod", "Neg",
                         "Greatest", "Least", "Sqrt", "Exp", "Ln", "Log"):
            cls = getattr(exp, cls_name, None)
            if cls:
                _arith_fns.append(cls)
        if _arith_fns and isinstance(expr, tuple(_arith_fns)):
            return "NUMERIC"
        # Boolean / comparison
        _bool_fns = []
        for cls_name in ("EQ", "NEQ", "GT", "GTE", "LT", "LTE",
                         "In", "Like", "Is", "Not", "And", "Or", "Between",
                         "ILike", "RegexpLike"):
            cls = getattr(exp, cls_name, None)
            if cls:
                _bool_fns.append(cls)
        if _bool_fns and isinstance(expr, tuple(_bool_fns)):
            return "BOOLEAN"
        # Aggregations → numeric
        _numeric_aggs = []
        for cls_name in ("Sum", "Avg", "Stddev", "Variance", "Min", "Max",
                         "ApproxDistinct", "ApproxQuantile", "Corr", "CovarPop",
                         "CovarSamp", "StddevPop", "StddevSamp", "VarPop", "VarSamp"):
            cls = getattr(exp, cls_name, None)
            if cls:
                _numeric_aggs.append(cls)
        if _numeric_aggs and isinstance(expr, tuple(_numeric_aggs)):
            return "NUMERIC"
        # Count / ranking → integer
        _int_aggs = []
        for cls_name in ("Count", "Rank", "DenseRank", "RowNumber", "Ntile",
                         "CumeDist", "PercentRank"):
            cls = getattr(exp, cls_name, None)
            if cls:
                _int_aggs.append(cls)
        if _int_aggs and isinstance(expr, tuple(_int_aggs)):
            return "BIGINT"
        # Literal values
        if isinstance(expr, exp.Literal):
            if expr.is_number:
                return "NUMERIC"
            if expr.is_string:
                return "TEXT"
        # Passthrough column reference — type inherited from source
        if isinstance(expr, exp.Column):
            return "INHERITED"
        # CASE WHEN: try to infer from first THEN branch
        if isinstance(expr, exp.Case):
            for when in expr.args.get("ifs", []):
                t = SQLLineageTracker._infer_type(when.args.get("true", when))
                if t not in ("", "UNKNOWN", "INHERITED"):
                    return t
            return "UNKNOWN"
        # Window function wrapper — infer from the underlying function
        if isinstance(expr, exp.Window):
            return SQLLineageTracker._infer_type(expr.this)
        return "UNKNOWN"

    @staticmethod
    def _find_main_query(expr: exp.Expression) -> Optional[exp.Expression]:
        """Unwrap Subquery / With wrappers to get the main SELECT or UNION."""
        if isinstance(expr, (exp.Select, exp.Union)):
            return expr
        if isinstance(expr, exp.Subquery):
            return SQLLineageTracker._find_main_query(expr.this)
        # Try to find a Union first, since Union wraps Selects
        u = expr.find(exp.Union)
        if u:
            return u
        return expr.find(exp.Select)

    def _build_alias_map(self, select: exp.Select, filename: str, cte_map: Dict[str, str]) -> Dict[str, str]:
        """Return ``{alias: real_table_name}`` for the immediate FROM/JOIN."""
        if not isinstance(select, exp.Select):
            return {}

        amap: Dict[str, str] = {}
        sources = []
        
        from_clause = select.args.get("from_")
        if from_clause:
            sources.append(from_clause.this)
            
        for join in select.args.get("joins") or []:
            sources.append(join.this)
            
        for src in sources:
            if isinstance(src, exp.Table):
                real = src.name.lower()
                alias = src.alias.lower() if src.alias else real
                amap[alias] = real
                
                # Also register pivot aliases to point to the base table
                for pivot in src.args.get("pivots") or []:
                    if pivot.alias:
                        amap[pivot.alias.lower()] = real
            elif isinstance(src, exp.Subquery):
                alias = (src.alias or f"sq_{id(src)}").lower()
                full_sq_name = f"[subquery] {alias}"
                amap[alias] = full_sq_name
                
                if isinstance(src.this, exp.Select):
                    self._analyze_query(full_sq_name, src.this, filename, cte_map)
                    # Register subquery itself in the CTE map so guess_table knows it's a valid local node
                    cte_map[full_sq_name] = full_sq_name

        return amap

    # ── column-level tracing ─────────────────────────────────────

    def _trace_output_column_shallow(
        self,
        sel_expr: exp.Expression,
        alias_map: Dict[str, str],
        cte_map: Dict[str, str],
    ) -> Optional[ColumnLineage]:
        """Trace one SELECT-list expression back to direct source columns."""

        if isinstance(sel_expr, exp.Alias):
            out_name = sel_expr.alias.lower()
            inner = sel_expr.this
        elif isinstance(sel_expr, exp.Column):
            out_name = sel_expr.name.lower()
            inner = sel_expr
        else:
            out_name = getattr(sel_expr, "alias_or_name", str(sel_expr)).lower()
            inner = sel_expr

        sql_text = inner.sql(dialect="postgres")

        col_refs = list(inner.find_all(exp.Column))
        if isinstance(inner, exp.Column) and not col_refs:
            col_refs = [inner]

        sources: List[Tuple[str, str]] = []
        for cref in col_refs:
            src_col = cref.name.lower()
            if cref.table:
                raw_table = cref.table.lower()
                real_table = alias_map.get(raw_table, raw_table)
            else:
                real_table = self._guess_table(src_col, alias_map, cte_map)
                
            # If the resolved table is actually a CTE/Subquery, map it to its full node name
            if real_table in cte_map:
                real_table = cte_map[real_table]
                
            sources.append((real_table, src_col))

        seen: set = set()
        deduped = [s for s in sources if s not in seen and not seen.add(s)]

        return ColumnLineage(name=out_name, expression=sql_text, sources=deduped)

    def _guess_table(self, col_name: str, alias_map: Dict[str, str], cte_map: Dict[str, str]) -> str:
        """Best-effort: which table owns *col_name*?"""
        for real in alias_map.values():
            lookup_name = cte_map.get(real, real)
            info = self.tables.get(lookup_name)
            if info:
                if any(c.name == col_name for c in info.columns):
                    return real
        if alias_map:
            return next(iter(alias_map.values()))
        return "unknown"

    # ── deep (transitive) lineage ────────────────────────────────

    def resolve_to_raw(
        self, table_name: str, col_name: str
    ) -> List[Tuple[str, str]]:
        """Recursively resolve a column all the way back to source tables."""
        info = self.tables.get(table_name)
        if not info or info.is_source:
            return [(table_name, col_name)]

        for c in info.columns:
            if c.name == col_name:
                out: List[Tuple[str, str]] = []
                for st, sc in c.sources:
                    out.extend(self.resolve_to_raw(st, sc))
                return list(dict.fromkeys(out))
        return [(table_name, col_name)]

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # Visualisation  (Graphviz)
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    # colour palette keyed by naming convention prefix
    _LAYER_STYLE = {
        "raw":  {"hdr": "#238636", "border": "#2ea043", "badge": "📦 SOURCE"},
        "stg":  {"hdr": "#8957e5", "border": "#a371f7", "badge": "🔧 STAGING"},
        "int":  {"hdr": "#d29922", "border": "#e3b341", "badge": "⚙️  INTERMEDIATE"},
        "mart": {"hdr": "#da3633", "border": "#58a6ff", "badge": "📊 MART"},
        "cte":  {"hdr": "#0a3069", "border": "#1f6feb", "badge": "🔗 CTE"},
        "sub":  {"hdr": "#4c5258", "border": "#6e7681", "badge": "↪️  SUBQUERY"},
    }

    @staticmethod
    def _layer(name: str) -> str:
        if name.startswith("[cte]"): return "cte"
        if name.startswith("[subquery]"): return "sub"
        for prefix in ("raw", "stg", "int", "mart"):
            if name.startswith(prefix):
                return prefix
        return "raw"

    def _html_table_label(
        self,
        info: TableInfo,
        *,
        highlight_cols: Optional[Set[str]] = None,
        port_side: str = "",
    ) -> str:
        """Build an HTML-table label for a Graphviz node."""
        style = self._LAYER_STYLE.get(self._layer(info.name), self._LAYER_STYLE["raw"])
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

    # ── 1. table-level DAG ───────────────────────────────────────

    def render_table_lineage(
        self, output_dir: str = "output", fmt: str = "png"
    ) -> str:
        dot = graphviz.Digraph("Table Lineage", format=fmt)
        dot.attr(
            rankdir="LR",
            bgcolor="#0d1117",
            fontname="Helvetica Neue",
            pad="0.8",
            nodesep="0.7",
            ranksep="1.6",
            dpi="150",
            label="TABLE-LEVEL  LINEAGE",
            labelloc="t",
            fontcolor="#58a6ff",
            fontsize="18",
        )
        dot.attr("node", fontname="Helvetica Neue", shape="plain")
        dot.attr("edge", color="#30363d", penwidth="2", arrowsize="0.8")

        for name, info in self.tables.items():
            dot.node(name, label=self._html_table_label(info))

        for name, info in self.tables.items():
            for src in info.source_tables:
                layer = self._layer(name)
                edge_color = self._LAYER_STYLE.get(layer, self._LAYER_STYLE["raw"])["hdr"]
                dot.edge(src, name, color=edge_color, penwidth="2.2")

        os.makedirs(output_dir, exist_ok=True)
        path = os.path.join(output_dir, "table_lineage")
        rendered = dot.render(path, cleanup=True)
        print(f"  📊 Table lineage      → {rendered}")
        return rendered

    # ── 2. column-level DAG (full, all tables) ───────────────────

    def render_column_lineage(
        self,
        output_dir: str = "output",
        fmt: str = "png",
        target: Optional[str] = None,
    ) -> str:
        """Render column-level edges between all tables (or up to *target*)."""

        # Collect edges: (src_table, src_col, tgt_table, tgt_col)
        edges: Set[Tuple[str, str, str, str]] = set()
        tables_needed: Set[str] = set()

        if target:
            # Only tables in the ancestry of *target*
            self._collect_edges_recursive(target, edges, tables_needed)
            graph_title = f"COLUMN-LEVEL  LINEAGE  →  {target}"
            filename = f"column_lineage_{target}"
        else:
            # All derived tables
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
            bgcolor="#0d1117",
            fontname="Helvetica Neue",
            pad="0.8",
            nodesep="0.7",
            ranksep="2.2",
            dpi="150",
            label=graph_title,
            labelloc="t",
            fontcolor="#58a6ff",
            fontsize="16",
        )
        dot.attr("node", fontname="Helvetica Neue", shape="plain")
        dot.attr("edge", penwidth="1.4", arrowsize="0.55")

        # Determine which columns per table participate in edges
        table_cols: Dict[str, Set[str]] = defaultdict(set)
        for st, sc, tt, tc in edges:
            table_cols[st].add(sc)
            table_cols[tt].add(tc)

        # Nodes — each table is one Graphviz node with port-per-column
        for tname in sorted(tables_needed):
            info = self.tables.get(tname)
            if not info:
                continue
            dot.node(
                tname,
                label=self._html_table_label(
                    info, highlight_cols=table_cols.get(tname)
                ),
            )

        # Edge colours per target-layer pair
        palette = ["#f47067", "#d2a8ff", "#ffa657", "#79c0ff", "#7ee787", "#ff9bce"]
        for i, (st, sc, tt, tc) in enumerate(sorted(edges)):
            color = palette[i % len(palette)]
            dot.edge(f"{st}:{sc}:e", f"{tt}:{tc}:w", color=color)

        os.makedirs(output_dir, exist_ok=True)
        path = os.path.join(output_dir, filename)
        rendered = dot.render(path, cleanup=True)
        print(f"  📊 Column lineage     → {rendered}")
        return rendered

    def _collect_edges_recursive(
        self,
        table_name: str,
        edges: Set[Tuple[str, str, str, str]],
        tables_needed: Set[str],
    ) -> None:
        if table_name in tables_needed:
            return  # already visited
        tables_needed.add(table_name)

        info = self.tables.get(table_name)
        if not info or info.is_source:
            return

        for col in info.columns:
            for st, sc in col.sources:
                edges.add((st, sc, table_name, col.name))
                self._collect_edges_recursive(st, edges, tables_needed)

    # ── 3. deep lineage graph (raw sources only) ─────────────────

    def render_deep_lineage(
        self,
        target: str,
        output_dir: str = "output",
        fmt: str = "png",
    ) -> str:
        """Show direct raw-source → final-table column edges (skip intermediates)."""
        info = self.tables.get(target)
        if not info:
            print(f"  ⚠  Table '{target}' not found")
            return ""

        dot = graphviz.Digraph(f"Deep Lineage: {target}", format=fmt)
        dot.attr(
            rankdir="LR",
            bgcolor="#0d1117",
            fontname="Helvetica Neue",
            pad="0.8",
            nodesep="0.6",
            ranksep="2.0",
            dpi="150",
            label=f"DEEP  LINEAGE  →  {target}   (traced to raw sources)",
            labelloc="t",
            fontcolor="#58a6ff",
            fontsize="16",
        )
        dot.attr("node", fontname="Helvetica Neue", shape="plain")
        dot.attr("edge", penwidth="1.2", arrowsize="0.5")

        # edges: raw_table.col → target.col
        edges: Set[Tuple[str, str, str]] = set()  # (src_table, src_col, tgt_col)
        tables_needed: Set[str] = {target}

        for col in info.columns:
            raw_sources = self.resolve_to_raw(target, col.name)
            for st, sc in raw_sources:
                edges.add((st, sc, col.name))
                tables_needed.add(st)

        # Nodes
        table_cols: Dict[str, Set[str]] = defaultdict(set)
        for st, sc, tc in edges:
            table_cols[st].add(sc)
            table_cols[target].add(tc)

        for tname in sorted(tables_needed):
            tinfo = self.tables.get(tname)
            if not tinfo:
                continue
            dot.node(
                tname,
                label=self._html_table_label(
                    tinfo, highlight_cols=table_cols.get(tname)
                ),
            )

        palette = ["#f47067", "#d2a8ff", "#ffa657", "#79c0ff", "#7ee787", "#ff9bce"]
        for i, (st, sc, tc) in enumerate(sorted(edges)):
            color = palette[i % len(palette)]
            dot.edge(f"{st}:{sc}:e", f"{target}:{tc}:w", color=color)

        os.makedirs(output_dir, exist_ok=True)
        path = os.path.join(output_dir, f"deep_lineage_{target}")
        rendered = dot.render(path, cleanup=True)
        print(f"  📊 Deep lineage       → {rendered}")
        return rendered

    # ── 4. interactive html graph (vis-network + sidebar) ────────

    def render_interactive_html(
        self, output_dir: str = "output"
    ) -> str:
        """Render a clean table-level DAG with an interactive column sidebar."""
        import json
        
        # Prepare nodes
        nodes = []
        for name, info in self.tables.items():
            layer = self._layer(name)
            style = self._LAYER_STYLE.get(layer, self._LAYER_STYLE["raw"])
            nodes.append({
                "id": name,
                "label": f"{style['badge']} {name}",
                "layer": layer,
                "color": {
                    "background": "#161b22",
                    "border": style["hdr"],
                    "highlight": {"background": "#0d1117", "border": "#58a6ff"}
                },
                "font": {"color": "#c9d1d9", "face": "Helvetica Neue", "size": 16},
                "shape": "box",
                "margin": 15,
                "borderWidth": 2,
                # Store full data for the sidebar
                "table_info": {
                    "sql_file": info.sql_file,
                    "filters": info.filters,
                    "joins": info.joins,
                    "is_union": info.is_union,
                    "union_branches": info.union_branches,
                    "group_by": info.group_by,
                    "columns": [
                        {
                            "name": c.name,
                            "data_type": c.data_type,
                            "expression": c.expression.strip(),
                            "sources": [f"{s_t}.{s_c}" for s_t, s_c in c.sources]
                        } for c in info.columns
                    ]
                }
            })
            
        # Prepare edges
        edges = []
        for name, info in self.tables.items():
            layer = self._layer(name)
            edge_color = self._LAYER_STYLE.get(layer, self._LAYER_STYLE["raw"])["hdr"]
            for src in info.source_tables:
                edges.append({
                    "from": src,
                    "to": name,
                    "color": {"color": edge_color, "highlight": "#58a6ff"},
                    "arrows": "to",
                    "width": 2
                })

        json_data = json.dumps({"nodes": nodes, "edges": edges}, indent=2)
        
        html_template = f"""<!DOCTYPE html>
<html>
<head>
    <title>SQL Lineage Explorer</title>
    <script type="text/javascript" src="https://unpkg.com/vis-network/standalone/umd/vis-network.min.js"></script>
    <style>
        body {{
            margin: 0;
            padding: 0;
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif;
            background-color: #0d1117;
            color: #c9d1d9;
            display: flex;
            height: 100vh;
            overflow: hidden;
        }}
        #network-container {{
            flex-grow: 1;
            height: 100%;
        }}
        #network {{
            width: 100%;
            height: 100%;
        }}
        #sidebar {{
            width: 450px;
            background-color: #161b22;
            border-left: 1px solid #30363d;
            padding: 25px;
            overflow-y: auto;
            box-shadow: -5px 0 15px rgba(0,0,0,0.5);
            display: flex;
            flex-direction: column;
            z-index: 10;
        }}
        #resizer {{
            width: 6px;
            background-color: #30363d;
            cursor: col-resize;
            z-index: 20;
            transition: background-color 0.2s;
        }}
        #resizer:hover, #resizer.active {{
            background-color: #58a6ff;
        }}
        .hidden {{ display: none !important; }}
        h2 {{ margin-top: 0; color: #58a6ff; font-size: 22px; border-bottom: 1px solid #30363d; padding-bottom: 12px; }}
        .badge {{
            display: inline-block;
            padding: 4px 10px;
            border-radius: 12px;
            font-size: 12px;
            font-weight: bold;
            margin-bottom: 20px;
            border: 1px solid #30363d;
            background: #21262d;
            color: #e6edf3;
        }}
        .col-card {{
            background-color: #0d1117;
            border: 1px solid #30363d;
            border-radius: 8px;
            padding: 16px;
            margin-bottom: 16px;
        }}
        .col-name {{
            font-weight: 600;
            color: #79c0ff;
            font-size: 15px;
            margin-bottom: 10px;
        }}
        .col-label {{
            font-size: 11px;
            color: #8b949e;
            text-transform: uppercase;
            letter-spacing: 0.8px;
            margin-top: 12px;
            margin-bottom: 6px;
            font-weight: 600;
        }}
        .code-block {{
            background-color: #161b22;
            padding: 10px;
            border-radius: 6px;
            font-family: ui-monospace, SFMono-Regular, "SF Mono", Menlo, Consolas, "Liberation Mono", monospace;
            font-size: 13px;
            color: #e6edf3;
            overflow-x: auto;
            white-space: pre-wrap;
            border: 1px solid #21262d;
            line-height: 1.4;
        }}
        .source-tag {{
            display: inline-block;
            background: #238636;
            color: white;
            padding: 3px 10px;
            border-radius: 12px;
            font-size: 12px;
            margin-right: 6px;
            margin-top: 6px;
            font-family: ui-monospace, monospace;
        }}
        .empty-state {{
            text-align: center;
            color: #8b949e;
            margin-top: 50%;
            font-size: 16px;
            line-height: 1.5;
        }}
    </style>
</head>
<body>
    <div id="network-container">
        <div id="network"></div>
    </div>
    <div id="resizer"></div>
    <div id="sidebar">
        <div style="margin-bottom: 20px;">
            <label for="table-filter" style="font-size: 14px; font-weight: bold; color: #8b949e; display: block; margin-bottom: 8px;">TARGET TABLE LINEAGE FILTER</label>
            <select id="table-filter" style="width: 100%; padding: 10px; background: #0d1117; color: #c9d1d9; border: 1px solid #30363d; border-radius: 6px; font-size: 14px;">
                <option value="ALL">Show Entire Warehouse Lineage</option>
            </select>
            <label id="hide-cte-toggle" style="display: flex; align-items: center; gap: 8px; margin-top: 10px; cursor: pointer; user-select: none; color: #8b949e; font-size: 12px;">
                <input type="checkbox" id="hide-intermediates" checked style="width: 14px; height: 14px; accent-color: #58a6ff; cursor: pointer;">
                Hide CTEs &amp; subqueries from list
            </label>
        </div>
        
        <div id="empty-state" class="empty-state">
            <div style="font-size: 40px; margin-bottom: 20px;">👈</div>
            Click on a table in the graph<br>to view column transformations.
        </div>
        <div id="table-details" class="hidden">
            <h2 id="table-name">Table Name</h2>
            <div id="table-badge" class="badge">file.sql</div>
            <div id="union-badge" class="badge" style="background: #a371f7; display: none; margin-left: 5px;">UNION</div>
            
            <div id="joins-container" style="margin-bottom: 20px;"></div>
            <div id="filters-container" style="margin-bottom: 20px;"></div>
            
            <div id="columns-container"></div>
        </div>
    </div>

    <script type="text/javascript">
        const resizer = document.getElementById('resizer');
        const sidebar = document.getElementById('sidebar');
        let isResizing = false;

        resizer.addEventListener('mousedown', (e) => {{
            isResizing = true;
            resizer.classList.add('active');
            document.body.style.cursor = 'col-resize';
            e.preventDefault();
        }});

        document.addEventListener('mousemove', (e) => {{
            if (!isResizing) return;
            const newWidth = window.innerWidth - e.clientX;
            if (newWidth > 200 && newWidth < window.innerWidth - 300) {{
                sidebar.style.width = newWidth + 'px';
            }}
        }});

        document.addEventListener('mouseup', () => {{
            if (isResizing) {{
                isResizing = false;
                resizer.classList.remove('active');
                document.body.style.cursor = 'default';
            }}
        }});

        const graphData = {json_data};
        
        // Populate filter dropdown
        const filterSelect = document.getElementById('table-filter');
        const hideIntermediates = document.getElementById('hide-intermediates');
        const sortedNodes = [...graphData.nodes].sort((a, b) => a.id.localeCompare(b.id));
        
        function isIntermediate(nodeId) {{
            return nodeId.startsWith('[cte]') || nodeId.startsWith('[subquery]');
        }}
        
        function populateDropdown() {{
            const hideChecked = hideIntermediates.checked;
            // Keep the current selection if possible
            const currentVal = filterSelect.value;
            // Remove all options except the first ("Show Entire...")
            while (filterSelect.options.length > 1) {{
                filterSelect.remove(1);
            }}
            sortedNodes.forEach(n => {{
                if (hideChecked && isIntermediate(n.id)) return;
                const opt = document.createElement('option');
                opt.value = n.id;
                opt.textContent = n.id;
                filterSelect.appendChild(opt);
            }});
            // Restore selection if it's still in the list, else reset to ALL
            if ([...filterSelect.options].some(o => o.value === currentVal)) {{
                filterSelect.value = currentVal;
            }} else {{
                filterSelect.value = 'ALL';
            }}
        }}
        
        populateDropdown();
        hideIntermediates.addEventListener('change', populateDropdown);
        
        // Setup vis.js network
        const container = document.getElementById('network');
        const data = {{
            nodes: new vis.DataSet(graphData.nodes),
            edges: new vis.DataSet(graphData.edges)
        }};
        
        const options = {{
            layout: {{
                hierarchical: {{
                    enabled: true,
                    direction: 'LR',
                    sortMethod: 'directed',
                    nodeSpacing: 100,
                    levelSeparation: 350
                }}
            }},
            physics: false,
            interaction: {{
                hover: true,
                navigationButtons: true,
                keyboard: true,
                zoomView: true,
                dragView: true
            }}
        }};
        
        const network = new vis.Network(container, data, options);
        
        // Filter logic
        filterSelect.addEventListener('change', function(e) {{
            const target = e.target.value;
            hideTableDetails();
            network.unselectAll();
            
            if (target === 'ALL') {{
                data.nodes.clear();
                data.edges.clear();
                data.nodes.add(graphData.nodes);
                data.edges.add(graphData.edges);
                network.fit({{animation: true}});
                return;
            }}
            
            // Find upstream lineage (ancestors)
            const visibleNodes = new Set([target]);
            const toProcess = [target];
            
            while (toProcess.length > 0) {{
                const current = toProcess.pop();
                graphData.edges.forEach(edge => {{
                    if (edge.to === current && !visibleNodes.has(edge.from)) {{
                        visibleNodes.add(edge.from);
                        toProcess.push(edge.from);
                    }}
                }});
            }}
            
            const filteredNodes = graphData.nodes.filter(n => visibleNodes.has(n.id));
            const filteredEdges = graphData.edges.filter(e => visibleNodes.has(e.from) && visibleNodes.has(e.to));
            
            data.nodes.clear();
            data.edges.clear();
            data.nodes.add(filteredNodes);
            data.edges.add(filteredEdges);
            
            // Highlight the target node
            network.selectNodes([target]);
            showTableDetails(data.nodes.get(target));
            
            // Wait for redraw then zoom
            setTimeout(() => {{ network.fit({{animation: true}}); }}, 100);
        }});
        
        network.on("click", function (params) {{
            if (params.nodes.length > 0) {{
                const nodeId = params.nodes[0];
                const node = data.nodes.get(nodeId);
                showTableDetails(node);
            }} else {{
                hideTableDetails();
            }}
        }});
        
        network.on("hoverNode", function (params) {{
            const hoveredNode = params.node;
            const connectedNodes = network.getConnectedNodes(hoveredNode);
            const connectedSet = new Set(connectedNodes);
            connectedSet.add(hoveredNode);
            
            const nodesUpdate = [];
            data.nodes.forEach(node => {{
                if (!connectedSet.has(node.id)) {{
                    nodesUpdate.push({{id: node.id, opacity: 0.15}});
                }} else {{
                    nodesUpdate.push({{id: node.id, opacity: 1.0}});
                }}
            }});
            data.nodes.update(nodesUpdate);
            
            const edgesUpdate = [];
            data.edges.forEach(edge => {{
                if (edge.from === hoveredNode || edge.to === hoveredNode) {{
                    edgesUpdate.push({{id: edge.id, color: {{opacity: 1.0}}}});
                }} else {{
                    edgesUpdate.push({{id: edge.id, color: {{opacity: 0.1}}}});
                }}
            }});
            data.edges.update(edgesUpdate);
        }});

        network.on("blurNode", function (params) {{
            const nodesUpdate = [];
            data.nodes.forEach(node => {{
                nodesUpdate.push({{id: node.id, opacity: 1.0}});
            }});
            data.nodes.update(nodesUpdate);
            
            const edgesUpdate = [];
            data.edges.forEach(edge => {{
                edgesUpdate.push({{id: edge.id, color: {{opacity: 1.0}}}});
            }});
            data.edges.update(edgesUpdate);
        }});
        
        function hideTableDetails() {{
            document.getElementById('empty-state').classList.remove('hidden');
            document.getElementById('table-details').classList.add('hidden');
        }}
        
        function showTableDetails(node) {{
            document.getElementById('empty-state').classList.add('hidden');
            document.getElementById('table-details').classList.remove('hidden');
            
            document.getElementById('table-name').textContent = node.id;
            document.getElementById('table-badge').textContent = '📄 ' + node.table_info.sql_file;
            document.getElementById('union-badge').style.display = node.table_info.is_union ? 'inline-block' : 'none';
            
            // ── Union branches ────────────────────────────────────────────
            const joinsContainer = document.getElementById('joins-container');
            joinsContainer.innerHTML = '';

            if (node.table_info.is_union && node.table_info.union_branches && node.table_info.union_branches.length > 0) {{
                const uLabel = document.createElement('div');
                uLabel.className = 'col-label';
                uLabel.textContent = 'Combined Tables';
                joinsContainer.appendChild(uLabel);

                const uWrap = document.createElement('div');
                uWrap.style.cssText = 'display:flex; flex-wrap:wrap; gap:6px; align-items:center; margin-bottom:14px;';
                node.table_info.union_branches.forEach((branch, i) => {{
                    // Show operator badge BEFORE the table (except for the first branch)
                    if (branch.operator) {{
                        const sep = document.createElement('span');
                        sep.textContent = branch.operator;
                        // Color by operator type
                        const opColors = {{
                            'UNION ALL':  '#1f6feb',
                            'UNION':      '#388bfd',
                            'INTERSECT':  '#2ea043',
                            'EXCEPT':     '#da3633'
                        }};
                        sep.style.cssText = `background:${{opColors[branch.operator] || '#6e40c9'}}; color:#fff;` +
                            `padding:2px 8px; border-radius:6px; font-size:11px; font-weight:bold; letter-spacing:0.5px; white-space:nowrap;`;
                        uWrap.appendChild(sep);
                    }}
                    const tag = document.createElement('span');
                    tag.className = 'source-tag';
                    tag.style.background = '#6e40c9';
                    tag.textContent = branch.table;
                    uWrap.appendChild(tag);
                }});
                joinsContainer.appendChild(uWrap);
            }}

            // ── Join logic ────────────────────────────────────────────────
            if (node.table_info.joins && node.table_info.joins.length > 0) {{
                const jLabel = document.createElement('div');
                jLabel.className = 'col-label';
                jLabel.textContent = 'Join Logic';
                joinsContainer.appendChild(jLabel);
                
                node.table_info.joins.forEach(j => {{
                    const card = document.createElement('div');
                    card.style.cssText = 'background:#161b22; border:1px solid #a371f7; border-radius:8px; padding:12px; margin-bottom:10px;';

                    // Header row: LEFT ← KIND → RIGHT
                    const header = document.createElement('div');
                    header.style.cssText = 'display:flex; align-items:center; gap:8px; margin-bottom:8px; flex-wrap:wrap;';

                    const leftTag = document.createElement('span');
                    leftTag.textContent = j.left || '?';
                    leftTag.style.cssText = 'background:#1f6feb; color:#fff; padding:3px 10px; border-radius:12px; font-size:12px; font-family:monospace;';

                    const kindTag = document.createElement('span');
                    kindTag.textContent = j.kind || 'JOIN';
                    kindTag.style.cssText = 'background:#a371f7; color:#fff; padding:3px 8px; border-radius:6px; font-size:11px; font-weight:bold; letter-spacing:0.5px;';

                    const rightTag = document.createElement('span');
                    rightTag.textContent = j.right || '?';
                    rightTag.style.cssText = 'background:#1f6feb; color:#fff; padding:3px 10px; border-radius:12px; font-size:12px; font-family:monospace;';

                    header.appendChild(leftTag);
                    header.appendChild(kindTag);
                    header.appendChild(rightTag);
                    card.appendChild(header);

                    // Condition
                    if (j.condition) {{
                        const cond = document.createElement('div');
                        cond.style.cssText = 'font-family:monospace; font-size:12px; color:#8b949e; white-space:pre-wrap; word-break:break-all;';
                        cond.textContent = j.condition;
                        card.appendChild(cond);
                    }}

                    joinsContainer.appendChild(card);
                }});
            }}
            
            const filtersContainer = document.getElementById('filters-container');
            filtersContainer.innerHTML = '';
            if (node.table_info.filters && node.table_info.filters.length > 0) {{
                const fLabel = document.createElement('div');
                fLabel.className = 'col-label';
                fLabel.textContent = 'Table Filters (WHERE/HAVING)';
                filtersContainer.appendChild(fLabel);
                
                node.table_info.filters.forEach(f => {{
                    const code = document.createElement('div');
                    code.className = 'code-block';
                    code.style.borderColor = '#1f6feb';
                    code.textContent = f;
                    filtersContainer.appendChild(code);
                }});
            }}

            // ── GROUP BY ──────────────────────────────────────────────────
            if (node.table_info.group_by && node.table_info.group_by.length > 0) {{
                const gbLabel = document.createElement('div');
                gbLabel.className = 'col-label';
                gbLabel.textContent = 'GROUP BY';
                filtersContainer.appendChild(gbLabel);

                const gbWrap = document.createElement('div');
                gbWrap.style.cssText = 'display:flex; flex-wrap:wrap; gap:6px; margin-bottom:14px;';
                node.table_info.group_by.forEach(g => {{
                    const tag = document.createElement('span');
                    tag.style.cssText = 'font-family:monospace; font-size:12px; background:#1c2128; border:1px solid #2ea043;' +
                        'color:#7ee787; padding:3px 10px; border-radius:8px; white-space:nowrap;';
                    tag.textContent = g;
                    gbWrap.appendChild(tag);
                }});
                filtersContainer.appendChild(gbWrap);
            }}

            const colsContainer = document.getElementById('columns-container');
            colsContainer.innerHTML = '';
            
            node.table_info.columns.forEach(col => {{
                const card = document.createElement('div');
                card.className = 'col-card';
                
                // Name + data type badge
                const nameDiv = document.createElement('div');
                nameDiv.className = 'col-name';
                nameDiv.style.cssText = 'display:flex; align-items:center; gap:8px; flex-wrap:wrap;';

                const nameText = document.createElement('span');
                nameText.textContent = '🔑 ' + col.name;
                nameDiv.appendChild(nameText);

                if (col.data_type && col.data_type !== 'INHERITED' && col.data_type !== 'UNKNOWN' && col.data_type !== '') {{
                    const typeBadge = document.createElement('span');
                    typeBadge.textContent = col.data_type;
                    const typeColors = {{
                        'BIGINT': '#1f6feb', 'INTEGER': '#1f6feb', 'INT': '#1f6feb', 'SMALLINT': '#1f6feb',
                        'NUMERIC': '#a371f7', 'FLOAT': '#a371f7', 'DOUBLE': '#a371f7', 'DECIMAL': '#a371f7', 'REAL': '#a371f7',
                        'TEXT': '#2ea043', 'VARCHAR': '#2ea043', 'CHAR': '#2ea043',
                        'DATE': '#d2a8ff', 'TIMESTAMP': '#d2a8ff', 'TIMESTAMPTZ': '#d2a8ff', 'TIME': '#d2a8ff',
                        'BOOLEAN': '#f47067', 'BOOL': '#f47067',
                    }};
                    // Match prefix for VARCHAR(n), NUMERIC(p,s), etc.
                    const baseType = col.data_type.replace(/\(.*\)/, '').trim();
                    const bgColor = typeColors[baseType] || '#30363d';
                    typeBadge.style.cssText = `background:${{bgColor}}22; color:${{bgColor}}; border:1px solid ${{bgColor}}55;` +
                        `font-size:10px; font-weight:bold; font-family:monospace; padding:1px 7px; border-radius:6px; letter-spacing:0.5px;`;
                    nameDiv.appendChild(typeBadge);
                }}

                card.appendChild(nameDiv);
                
                // Operation
                const isPassthrough = (col.expression.toLowerCase() === col.name.toLowerCase() || col.expression === '');
                
                const opLabel = document.createElement('div');
                opLabel.className = 'col-label';
                opLabel.textContent = isPassthrough ? 'Passed Through Directly' : 'SQL Transformation';
                card.appendChild(opLabel);
                
                if (!isPassthrough) {{
                    const code = document.createElement('div');
                    code.className = 'code-block';
                    code.textContent = col.expression;
                    card.appendChild(code);
                }}
                
                // Sources
                if (col.sources && col.sources.length > 0) {{
                    const srcLabel = document.createElement('div');
                    srcLabel.className = 'col-label';
                    srcLabel.textContent = 'Depends On Columns';
                    card.appendChild(srcLabel);
                    
                    const tagsDiv = document.createElement('div');
                    col.sources.forEach(src => {{
                        const tag = document.createElement('span');
                        tag.className = 'source-tag';
                        tag.textContent = src;
                        
                        // Parse out the table name (everything before the last dot)
                        const dotIndex = src.lastIndexOf('.');
                        const tableName = dotIndex > 0 ? src.substring(0, dotIndex) : src;
                        
                        // Make it clickable if the node exists in our data
                        if (data.nodes.get(tableName)) {{
                            tag.style.cursor = 'pointer';
                            tag.title = 'Click to jump to ' + tableName;
                            
                            tag.addEventListener('mouseenter', () => {{
                                tag.style.opacity = '0.7';
                            }});
                            tag.addEventListener('mouseleave', () => {{
                                tag.style.opacity = '1';
                            }});
                            
                            tag.addEventListener('click', (e) => {{
                                e.stopPropagation();
                                const targetNode = data.nodes.get(tableName);
                                
                                // Update network selection
                                network.selectNodes([tableName]);
                                
                                // Focus camera on the node
                                network.focus(tableName, {{
                                    scale: 1.2,
                                    animation: {{
                                        duration: 600,
                                        easingFunction: 'easeInOutQuad'
                                    }}
                                }});
                                
                                // Update sidebar
                                showTableDetails(targetNode);
                            }});
                        }}
                        
                        tagsDiv.appendChild(tag);
                    }});
                    card.appendChild(tagsDiv);
                }}
                
                colsContainer.appendChild(card);
            }});
        }}
        
        // Select last node by default if available
        network.once("afterDrawing", function() {{
            if (graphData.nodes.length > 0 && data.nodes.length === graphData.nodes.length) {{
                network.fit({{animation: true}});
            }}
        }});
    </script>
</body>
</html>
"""
        os.makedirs(output_dir, exist_ok=True)
        path = os.path.join(output_dir, "interactive_viewer.html")
        with open(path, "w", encoding="utf-8") as f:
            f.write(html_template)
            
        print(f"  🕸️  Interactive Dashboard → {path}")
        return path

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # Text report
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    def print_report(self) -> None:
        """Print a human-readable lineage report to stdout."""
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

        # deep lineage for the last (final) table
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


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# CLI
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def main() -> None:
    ap = argparse.ArgumentParser(
        description="SQL Lineage Tracker — table & column lineage from SQL files"
    )
    ap.add_argument("sql_dir", help="directory containing .sql files")
    ap.add_argument(
        "--target",
        "-t",
        default=None,
        help="target table for column lineage (default: last table parsed)",
    )
    ap.add_argument(
        "--output-dir", "-o", default="output", help="output directory (default: output/)"
    )
    ap.add_argument(
        "--format",
        "-f",
        default="png",
        choices=("png", "svg", "pdf"),
        help="graph output format (default: png)",
    )
    ap.add_argument(
        "--dialect",
        "-d",
        default="postgres",
        help="SQL dialect (default: postgres)",
    )
    ap.add_argument(
        "--interactive",
        "-i",
        action="store_true",
        help="Generate an interactive HTML graph in addition to static images",
    )
    args = ap.parse_args()

    tracker = SQLLineageTracker(dialect=args.dialect)
    tracker.parse_directory(args.sql_dir)
    tracker.print_report()

    target = args.target or list(tracker.tables.keys())[-1]

    print("🎨  Generating visualisations …\n")
    tracker.render_table_lineage(output_dir=args.output_dir, fmt=args.format)
    tracker.render_column_lineage(output_dir=args.output_dir, fmt=args.format)
    tracker.render_column_lineage(
        output_dir=args.output_dir, fmt=args.format, target=target
    )
    tracker.render_deep_lineage(
        target=target, output_dir=args.output_dir, fmt=args.format
    )
    if args.interactive:
        tracker.render_interactive_html(output_dir=args.output_dir)
    
    print(f"\n✨  Done! Check the '{args.output_dir}/' directory.\n")


if __name__ == "__main__":
    main()
