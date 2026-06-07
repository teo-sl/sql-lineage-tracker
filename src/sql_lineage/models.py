"""
Data models for SQL Lineage Tracker.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Set, Tuple


@dataclass
class ColumnLineage:
    """One output column and the source columns it is derived from."""

    name: str
    expression: str  # original SQL expression text
    sources: List[Tuple[str, str]]  # [(source_table, source_col), ...]
    data_type: str = ""  # SQL data type (from DDL or inferred)


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
