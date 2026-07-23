"""
SQL AST parsing and column-level lineage extraction.

Responsibilities:
- Parse .sql files with sqlglot
- Resolve CTEs, subqueries, aliases
- Trace column sources through SELECT expressions
- Propagate data types across derived tables
"""

from __future__ import annotations

import sys
from collections import OrderedDict, defaultdict
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

import sqlglot
from sqlglot import exp

from sql_lineage.models import ColumnLineage, TableInfo
from sql_lineage.type_inference import infer_type


class SQLParser:
    """Parse SQL files and populate a table registry with lineage information."""

    def __init__(self, dialect: str = "postgres") -> None:
        self.dialect = dialect
        self.tables: Dict[str, TableInfo] = OrderedDict()

    # ─────────────────────────────────────────────────────────────
    # Public API
    # ─────────────────────────────────────────────────────────────

    def parse_directory(self, sql_dir: str) -> None:
        """Scan *sql_dir* for ``*.sql`` files (sorted by name) and parse each one."""
        sql_files = sorted(Path(sql_dir).glob("*.sql"))
        if not sql_files:
            print(f"⚠  No .sql files found in {sql_dir}")
            return

        print(f"\n📂  Parsing {len(sql_files)} SQL files from {sql_dir}/\n")
        for f in sql_files:
            self._parse_file(f)

        self._resolve_inherited_types()
        print()

    def resolve_to_raw(
        self, table_name: str, col_name: str
    ) -> List[Tuple[str, str]]:
        """Recursively resolve a column all the way back to source tables."""
        info = self.tables.get(table_name)
        if not info or info.is_source:
            return [(table_name, col_name)]

        for col in info.columns:
            if col.name == col_name:
                out: List[Tuple[str, str]] = []
                for st, sc in col.sources:
                    out.extend(self.resolve_to_raw(st, sc))
                return list(dict.fromkeys(out))  # deduplicate, preserve order

        return [(table_name, col_name)]

    # ─────────────────────────────────────────────────────────────
    # Type propagation (second pass)
    # ─────────────────────────────────────────────────────────────

    def _resolve_inherited_types(self) -> None:
        """Second pass: propagate data types forward from source columns to derived ones."""
        cache: Dict[Tuple[str, str], str] = {}

        def _get_type(table_name: str, col_name: str, visited: Set[Tuple[str, str]]) -> str:
            key = (table_name, col_name)
            if key in cache:
                return cache[key]
            if key in visited:  # cycle guard (e.g. recursive CTEs)
                return "UNKNOWN"
            visited.add(key)

            tinfo = self.tables.get(table_name)
            if not tinfo:
                return "UNKNOWN"

            col = next((c for c in tinfo.columns if c.name == col_name), None)
            if not col:
                return "UNKNOWN"

            if col.data_type and col.data_type not in ("INHERITED", "UNKNOWN"):
                cache[key] = col.data_type
                return col.data_type

            inferred = {
                _get_type(st, sc, visited)
                for st, sc in col.sources
                if _get_type(st, sc, visited) not in ("INHERITED", "UNKNOWN", "")
            }
            result = inferred.pop() if len(inferred) == 1 else "UNKNOWN"
            cache[key] = result
            return result

        for tname, tinfo in self.tables.items():
            for col in tinfo.columns:
                if not col.data_type or col.data_type in ("INHERITED", "UNKNOWN"):
                    t = _get_type(tname, col.name, set())
                    if t != "UNKNOWN":
                        col.data_type = t

    # ─────────────────────────────────────────────────────────────
    # File / statement dispatching
    # ─────────────────────────────────────────────────────────────

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
            elif isinstance(stmt, (exp.Select, exp.Union, exp.Intersect, exp.Except)):
                self._analyze_ctas(filepath.stem.lower(), stmt, filepath.name)

    def _analyze_create(self, stmt: exp.Create, filename: str) -> None:
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

    # ─────────────────────────────────────────────────────────────
    # Source tables (plain CREATE TABLE)
    # ─────────────────────────────────────────────────────────────

    def _analyze_source_table(
        self, table_name: str, stmt: exp.Create, filename: str
    ) -> None:
        columns = []
        for cdef in stmt.find_all(exp.ColumnDef):
            n = cdef.name.lower()
            dtype = cdef.kind.sql(dialect=self.dialect).upper() if cdef.kind else ""
            columns.append(
                ColumnLineage(name=n, expression=n, sources=[(table_name, n)], data_type=dtype)
            )

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

    # ─────────────────────────────────────────────────────────────
    # Derived (CTAS) tables
    # ─────────────────────────────────────────────────────────────

    def _analyze_ctas(
        self, table_name: str, ctas_expr: exp.Expression, filename: str
    ) -> None:
        # cte_map is LOCAL to this CTAS: maps short CTE name -> scoped key in
        # self.tables.  Scoping by table_name prevents collisions when two files
        # both define a CTE with the same short name (e.g. "base", "joined").
        cte_map: Dict[str, str] = {}

        # Flat scope used for ALL subquery names produced from this file.
        # Using the filename stem (e.g. "mart_customer_360") instead of the
        # (potentially already-nested) parent_table name prevents the
        # [subquery:[subquery:…] …] explosion for deeply-nested subqueries.
        file_scope = Path(filename).stem.lower()

        for cte_node in ctas_expr.find_all(exp.CTE):
            short_name = (cte_node.alias or "").lower()
            if short_name:
                cte_body = cte_node.this
                if isinstance(cte_body, (exp.Select, exp.Union, exp.Intersect, exp.Except)):
                    # Unique key: [cte:parent_table] short_name
                    full_cte_name = f"[cte:{table_name}] {short_name}"
                    cte_map[short_name] = full_cte_name
                    self._analyze_query(full_cte_name, cte_body, filename, cte_map, file_scope=file_scope)

        main_query = self._find_main_query(ctas_expr)
        if main_query:
            self._analyze_query(table_name, main_query, filename, cte_map, file_scope=file_scope)
    # ─────────────────────────────────────────────────────────────
    # Query analysis (SELECT / UNION)
    # ─────────────────────────────────────────────────────────────

    def _analyze_query(
        self,
        table_name: str,
        query_node: exp.Expression,
        filename: str,
        cte_map: Dict[str, str],
        file_scope: str = "",
    ) -> None:
        branch_tuples = self._get_union_branches(query_node)
        if not branch_tuples:
            return

        all_columns: Dict[str, ColumnLineage] = {}
        all_filters: List[str] = []
        all_joins: List[Dict] = []
        all_sources: Set[str] = set()
        union_branches: List[Dict] = []
        all_group_by: List[str] = []

        for idx, (select_node, operator) in enumerate(branch_tuples):
            alias_map = self._build_alias_map(select_node, filename, cte_map, parent_table=table_name, file_scope=file_scope)
            from_table = self._extract_from_table(select_node, alias_map)

            if len(branch_tuples) > 1 and from_table:
                union_branches.append({"table": from_table, "operator": operator})
                # Ensure every UNION branch source is tracked even when its
                # columns are fully deduplicated by the star-expansion path.
                all_sources.add(from_table)

            prefix = ""
            if len(branch_tuples) > 1:
                if operator:
                    prefix = f"[{operator} branch: {from_table}] "
                else:
                    prefix = f"[Branch {idx + 1}: {from_table}] "

            self._collect_filters(select_node, all_filters, alias_map, cte_map, prefix=prefix)
            self._collect_group_by(select_node, alias_map, all_group_by)
            self._collect_joins(select_node, from_table, alias_map, all_joins)

            pivot_columns = self._extract_pivot_columns(select_node, from_table)

            for sel_expr in select_node.expressions:
                # ── Star expansion: SELECT * or SELECT tbl.* ─────────────────
                is_plain_star = isinstance(sel_expr, exp.Star)
                is_qualified_star = (
                    isinstance(sel_expr, exp.Column)
                    and isinstance(sel_expr.this, exp.Star)
                )
                if is_plain_star or is_qualified_star:
                    # Which tables contribute? Qualified star pins one table;
                    # plain star expands all sources in scope.
                    if is_qualified_star and sel_expr.table:
                        raw_ref = sel_expr.table.lower()
                        real_ref = alias_map.get(raw_ref, raw_ref)
                        if real_ref in cte_map:
                            real_ref = cte_map[real_ref]
                        star_tables = [real_ref]
                    else:
                        # plain * — every source in alias_map
                        star_tables = []
                        for raw, real in alias_map.items():
                            resolved = cte_map.get(real, real)
                            if resolved not in star_tables:
                                star_tables.append(resolved)

                    expanded_any = False
                    for tref in star_tables:
                        src_info = self.tables.get(tref)
                        if src_info:
                            for src_col in src_info.columns:
                                new_src = (tref, src_col.name)
                                if src_col.name not in all_columns:
                                    all_columns[src_col.name] = ColumnLineage(
                                        name=src_col.name,
                                        expression=f"{tref}.{src_col.name}",
                                        sources=[new_src],
                                        data_type="INHERITED",
                                    )
                                else:
                                    # UNION branch: accumulate sources from all branches
                                    existing = all_columns[src_col.name]
                                    if new_src not in existing.sources:
                                        existing.sources.append(new_src)
                                expanded_any = True

                    if not expanded_any:
                        # Source not yet registered (forward reference or unknown).
                        # Emit a placeholder so the table still appears in lineage.
                        if is_qualified_star and sel_expr.table:
                            raw_ref = sel_expr.table.lower()
                            real_ref = alias_map.get(raw_ref, raw_ref)
                            if real_ref in cte_map:
                                real_ref = cte_map[real_ref]
                            placeholder = f"{real_ref}.*"
                            if placeholder not in all_columns:
                                all_columns[placeholder] = ColumnLineage(
                                    name=placeholder,
                                    expression=placeholder,
                                    sources=[(real_ref, "*")],
                                    data_type="UNKNOWN",
                                )
                        else:
                            # Pivot fallback for plain * (original behaviour)
                            if pivot_columns:
                                for pcol, (pexpr, pdtype) in pivot_columns.items():
                                    if pcol not in all_columns:
                                        all_columns[pcol] = ColumnLineage(
                                            name=pcol,
                                            expression=pexpr,
                                            sources=[(from_table, pcol)],
                                            data_type=pdtype,
                                        )
                    continue
                # ── end star expansion ────────────────────────────────────────

                col = self._trace_output_column(sel_expr, alias_map, cte_map)
                if not col:
                    continue

                if col.name in pivot_columns:
                    col.expression = pivot_columns[col.name][0]
                    col.data_type = pivot_columns[col.name][1]
                else:
                    inner = sel_expr.this if isinstance(sel_expr, exp.Alias) else sel_expr
                    col.data_type = infer_type(inner)

                if col.name not in all_columns:
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

        # Also include tables that only appear in JOINs (no column selected from
        # them directly, e.g. `SELECT t2.* FROM a t2 JOIN b t3 ON …`).
        for join_info in all_joins:
            right = join_info.get("right", "")
            if right:
                all_sources.add(right)

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
            group_by=list(all_group_by),
        )
        print(
            f"  ✅ {filename:<35} → parsed node:     {table_name}  "
            f"({len(columns)} cols, {len(branch_tuples)} branches)"
        )

    # ─────────────────────────────────────────────────────────────
    # Per-branch data extraction helpers
    # ─────────────────────────────────────────────────────────────

    def _extract_from_table(
        self, select_node: exp.Select, alias_map: Dict[str, str]
    ) -> str:
        from_clause = select_node.args.get("from_")
        if not from_clause:
            return ""
        if isinstance(from_clause.this, exp.Table):
            raw = from_clause.this.name.lower()
            key = from_clause.this.alias.lower() if from_clause.this.alias else raw
            return alias_map.get(key, raw)
        if isinstance(from_clause.this, exp.Subquery):
            sq = from_clause.this
            # Derive the lookup key using the same logic as _build_alias_map so
            # that alias-less subqueries (whose alias was derived from their
            # inner FROM table name) are resolved correctly.
            if sq.alias:
                sq_alias = sq.alias.lower()
            else:
                # Body may be a Union/Intersect/Except — walk to leftmost Select
                inner = self._leftmost_select(sq.this)
                if (
                    inner is not None
                    and inner.args.get("from_")
                    and isinstance(inner.args["from_"].this, exp.Table)
                ):
                    sq_alias = inner.args["from_"].this.name.lower()
                else:
                    sq_alias = ""
            # alias_map already holds the scoped [subquery:parent] key.
            return alias_map.get(sq_alias, f"[subquery] {sq_alias}")
        return ""

    def _collect_filters(
        self,
        select_node: exp.Select,
        all_filters: List[str],
        alias_map: Dict[str, str],
        cte_map: Dict[str, str],
        prefix: str = "",
    ) -> None:
        for key in ("where", "having"):
            node = select_node.args.get(key)
            if node:
                node_copy = node.copy()
                for col in node_copy.find_all(exp.Column):
                    if col.table:
                        raw_table = col.table.lower()
                        resolved = alias_map.get(raw_table, raw_table)
                    else:
                        resolved = self._guess_table(col.name.lower(), alias_map, cte_map)
                    
                    if resolved in cte_map:
                        resolved = cte_map[resolved]
                    col.set("table", exp.to_identifier(resolved))
                all_filters.append(prefix + node_copy.sql(dialect=self.dialect))

    def _collect_group_by(
        self,
        select_node: exp.Select,
        alias_map: Dict[str, str],
        all_group_by: List[Dict],  # era List[str]
    ) -> None:
        group_node = select_node.args.get("group")
        if not group_node:
            return
        for g_expr in group_node.expressions:
            g_copy = g_expr.copy()
            
            # Collect all table.column sources referenced in this expression
            sources = []
            for col in g_copy.find_all(exp.Column):
                if col.table:
                    resolved = alias_map.get(col.table.lower(), col.table.lower())
                else:
                    resolved = self._guess_table(col.name.lower(), alias_map, {})
                col.set("table", exp.to_identifier(resolved))
                ref = f"{resolved}.{col.name.lower()}"
                if ref not in sources:
                    sources.append(ref)

            entry = {
                "expression": g_copy.sql(dialect=self.dialect),
                "sources": sources,
            }
            if entry not in all_group_by:
                all_group_by.append(entry)

    def _collect_joins(
        self,
        select_node: exp.Select,
        from_table: str,
        alias_map: Dict[str, str],
        all_joins: List[Dict],
    ) -> None:
        joined_tables = [from_table]
        for join in select_node.args.get("joins") or []:
            side = str(join.args.get("side", "") or "").strip().upper()
            kind = str(join.args.get("kind", "") or "").strip().upper()
            join_type = " ".join(p for p in [side, kind, "JOIN"] if p)

            join_src = join.this
            if isinstance(join_src, exp.Subquery):
                # Resolve through alias_map so the key matches the scoped
                # [subquery:parent] name already registered by _build_alias_map.
                if join_src.alias:
                    sq_alias = join_src.alias.lower()
                else:
                    # Body may be Union/Intersect/Except — walk to leftmost Select
                    inner = self._leftmost_select(join_src.this)
                    if (
                        inner is not None
                        and inner.args.get("from_")
                        and isinstance(inner.args["from_"].this, exp.Table)
                    ):
                        sq_alias = inner.args["from_"].this.name.lower()
                    else:
                        sq_alias = f"sq_{id(join_src)}"
                # Prefer the already-scoped name from alias_map (registered by
                # _build_alias_map with the flat [subquery:<file_scope>] prefix);
                # fall back to a bare label only if somehow not present.
                right_name = alias_map.get(sq_alias, f"[subquery] {sq_alias}")
            elif isinstance(join_src, exp.Table):
                raw_r = join_src.name.lower()
                right_name = alias_map.get(
                    join_src.alias.lower() if join_src.alias else raw_r, raw_r
                )
            else:
                right_name = join_src.sql(dialect=self.dialect)

            on_expr = join.args.get("on")
            using_expr = join.args.get("using")
            if on_expr:
                on_copy = on_expr.copy()
                for col in on_copy.find_all(exp.Column):
                    if col.table:
                        raw_table = col.table.lower()
                        resolved = alias_map.get(raw_table, raw_table)
                    else:
                        resolved = self._guess_table(col.name.lower(), alias_map, {})
                    col.set("table", exp.to_identifier(resolved))
                condition = on_copy.sql(dialect=self.dialect)
            elif using_expr:
                condition = "USING (" + ", ".join(
                    u.sql(dialect=self.dialect) for u in using_expr
                ) + ")"
            else:
                condition = ""

            left_str = " + ".join(joined_tables)
            all_joins.append(
                {"left": left_str, "right": right_name, "kind": join_type, "condition": condition}
            )
            joined_tables.append(right_name)

    def _extract_pivot_columns(
        self, select_node: exp.Select, from_table: str
    ) -> Dict[str, Tuple[str, str]]:
        """Return ``{col_name: (expression_text, dtype)}`` for any PIVOT on the FROM clause."""
        pivot_columns: Dict[str, Tuple[str, str]] = {}
        from_clause = select_node.args.get("from_")
        if not (
            from_clause
            and isinstance(from_clause.this, exp.Table)
            and from_clause.this.args.get("pivots")
        ):
            return pivot_columns

        for pivot in from_clause.this.args["pivots"]:
            if pivot.args.get("unpivot"):
                continue

            agg_expr_str = ""
            dtype = "INHERITED"
            if pivot.args.get("expressions"):
                agg_expr = pivot.args["expressions"][0]
                agg_expr_str = agg_expr.sql(dialect=self.dialect)
                dtype = infer_type(agg_expr)

            field_expr = ""
            if pivot.args.get("fields"):
                field_expr = pivot.args["fields"][0].this.sql(dialect=self.dialect)
                for e in pivot.args["fields"][0].expressions:
                    if isinstance(e, exp.PivotAlias):
                        col_alias = e.args.get("alias") or getattr(e, "alias", None)
                        if col_alias:
                            col_name = (
                                col_alias
                                if isinstance(col_alias, str)
                                else getattr(col_alias, "name", str(col_alias))
                            ).lower()
                            val_expr = e.this.sql(dialect=self.dialect)
                            pivot_columns[col_name] = (
                                f"PIVOT: {agg_expr_str} FOR {field_expr} = {val_expr}",
                                dtype,
                            )

            if not pivot_columns and pivot.args.get("columns"):
                for c in pivot.args["columns"]:
                    col_name = c.name.lower()
                    label = f"PIVOT operation on {agg_expr_str}" if agg_expr_str else "PIVOT operation"
                    pivot_columns[col_name] = (label, dtype)

        return pivot_columns

    # ─────────────────────────────────────────────────────────────
    # UNION / set-op flattening
    # ─────────────────────────────────────────────────────────────

    def _get_union_branches(
        self, node: exp.Expression
    ) -> List[Tuple[exp.Select, Optional[str]]]:
        """Flatten a Union/Intersect/Except tree into [(Select, operator_label)]."""
        if isinstance(node, exp.Select):
            return [(node, None)]
        if isinstance(node, (exp.Union, exp.Intersect, exp.Except)):
            if isinstance(node, exp.Intersect):
                op = "INTERSECT"
            elif isinstance(node, exp.Except):
                op = "EXCEPT"
            else:
                op = "UNION" if node.args.get("distinct") else "UNION ALL"

            left = self._get_union_branches(node.this)
            right = self._get_union_branches(node.expression)
            if right:
                right[0] = (right[0][0], op)
            return left + right
        return []

    # ─────────────────────────────────────────────────────────────
    # Alias map
    # ─────────────────────────────────────────────────────────────

    @staticmethod
    def _leftmost_select(node: exp.Expression) -> Optional[exp.Select]:
        """Walk a Union/Intersect/Except tree and return its leftmost Select branch.

        Returns *node* unchanged if it is already an ``exp.Select``, or
        ``None`` if no Select can be found.
        """
        while isinstance(node, (exp.Union, exp.Intersect, exp.Except)):
            node = node.this  # always the left child
        return node if isinstance(node, exp.Select) else None

    def _build_alias_map(
        self,
        select: exp.Select,
        filename: str,
        cte_map: Dict[str, str],
        parent_table: str = "",
        file_scope: str = "",
    ) -> Dict[str, str]:
        """Return ``{alias: real_table_name}`` for the immediate FROM/JOIN sources.

        Subquery nodes are named ``[subquery:<file_scope>] <alias>`` where
        *file_scope* is the SQL file's stem (e.g. ``mart_customer_360``).
        Using the flat file stem instead of the parent-table name prevents
        the ``[subquery:[subquery:…]…]`` explosion for deeply-nested queries.
        Two subqueries in the same file with the same alias are disambiguated
        by a counter suffix (``_2``, ``_3``, …).
        """
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
                for pivot in src.args.get("pivots") or []:
                    if pivot.alias:
                        amap[pivot.alias.lower()] = real
            elif isinstance(src, exp.Subquery):
                # Prefer the explicit alias; if absent, walk to the leftmost
                # Select of the body (handles both plain SELECTs and UNION ALL
                # / set-op bodies) to derive a stable name from its FROM table.
                if src.alias:
                    alias = src.alias.lower()
                else:
                    inner = self._leftmost_select(src.this)
                    if (
                        inner is not None
                        and inner.args.get("from_")
                        and isinstance(inner.args["from_"].this, exp.Table)
                    ):
                        alias = inner.args["from_"].this.name.lower()
                    else:
                        alias = f"sq_{id(src)}"

                # Use the flat file stem as scope so nested subqueries don't
                # inherit the (already-nested) parent_table name.
                scope = file_scope or Path(filename).stem.lower()
                base_sq_name = f"[subquery:{scope}] {alias}"

                # Disambiguate if two subqueries in the same file share an alias.
                full_sq_name = base_sq_name
                counter = 2
                while full_sq_name in self.tables:
                    full_sq_name = f"{base_sq_name}_{counter}"
                    counter += 1

                amap[alias] = full_sq_name
                # Analyse the subquery body — accept both a plain Select and any
                # set-operation (Union / Intersect / Except) at the top level.
                body = src.this
                if isinstance(body, (exp.Select, exp.Union, exp.Intersect, exp.Except)):
                    self._analyze_query(full_sq_name, body, filename, cte_map, file_scope=scope)
                    cte_map[full_sq_name] = full_sq_name

        return amap

    # ─────────────────────────────────────────────────────────────
    # Column tracing
    # ─────────────────────────────────────────────────────────────

    def _trace_output_column(
        self,
        sel_expr: exp.Expression,
        alias_map: Dict[str, str],
        cte_map: Dict[str, str],
    ) -> Optional[ColumnLineage]:
        """Trace one SELECT-list expression back to its direct source columns."""
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

            if real_table in cte_map:
                real_table = cte_map[real_table]

            sources.append((real_table, src_col))

        seen: set = set()
        deduped = [s for s in sources if s not in seen and not seen.add(s)]  # type: ignore[func-returns-value]
        return ColumnLineage(name=out_name, expression=sql_text, sources=deduped)

    def _guess_table(
        self,
        col_name: str,
        alias_map: Dict[str, str],
        cte_map: Dict[str, str],
    ) -> str:
        """Best-effort: which aliased table owns *col_name*?"""
        for real in alias_map.values():
            lookup_name = cte_map.get(real, real)
            info = self.tables.get(lookup_name)
            if info and any(c.name == col_name for c in info.columns):
                return real
        return next(iter(alias_map.values()), "unknown")

    # ─────────────────────────────────────────────────────────────
    # Static helpers
    # ─────────────────────────────────────────────────────────────

    @staticmethod
    def _find_main_query(expr: exp.Expression) -> Optional[exp.Expression]:
        """Unwrap Subquery/With wrappers to reach the main SELECT or set operation."""
        if isinstance(expr, (exp.Select, exp.Union, exp.Intersect, exp.Except)):
            return expr
        if isinstance(expr, exp.Subquery):
            return SQLParser._find_main_query(expr.this)
        u = expr.find((exp.Union, exp.Intersect, exp.Except))
        return u if u else expr.find(exp.Select)