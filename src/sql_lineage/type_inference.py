"""
Best-effort SQL expression type inference.
"""

from __future__ import annotations

import sqlglot.expressions as exp


def infer_type(expr: exp.Expression) -> str:
    """Best-effort type inference from a SQL expression node."""
    # Unwrap alias
    if isinstance(expr, exp.Alias):
        return infer_type(expr.this)

    # Explicit CAST / TryCast
    if isinstance(expr, (exp.Cast, exp.TryCast)):
        return expr.to.sql().upper()

    # Window function wrapper — delegate to the inner function
    if isinstance(expr, exp.Window):
        return infer_type(expr.this)

    # CASE WHEN — try to infer from the first THEN branch
    if isinstance(expr, exp.Case):
        for when in expr.args.get("ifs", []):
            t = infer_type(when.args.get("true", when))
            if t not in ("", "UNKNOWN", "INHERITED"):
                return t
        return "UNKNOWN"

    # Passthrough column reference — type must be inherited from source
    if isinstance(expr, exp.Column):
        return "INHERITED"

    # Literal values
    if isinstance(expr, exp.Literal):
        if expr.is_number:
            return "NUMERIC"
        if expr.is_string:
            return "TEXT"

    # Helper: check membership in a list of class names (tolerant of missing ones)
    def _is_instance_of_names(node: exp.Expression, *names: str) -> bool:
        classes = [getattr(exp, n) for n in names if hasattr(exp, n)]
        return bool(classes) and isinstance(node, tuple(classes))

    # Lag/Lead/NthValue/First/Last → inherit source type
    if _is_instance_of_names(expr, "Lag", "Lead", "First", "Last", "NthValue"):
        return "INHERITED"

    # Date/time functions
    if _is_instance_of_names(
        expr,
        "DateTrunc", "TsOrDsToDate", "CurrentDate", "Date",
        "DateFromParts", "ToDate", "DateAdd", "DateSub", "TimestampTrunc",
    ):
        return "DATE"

    # Timestamp functions
    if _is_instance_of_names(
        expr,
        "CurrentTimestamp", "Now", "TimeToStr", "StrToTime",
        "CurrentTime", "UnixToTime", "TimeAdd",
    ):
        return "TIMESTAMP"

    # String functions
    if _is_instance_of_names(
        expr,
        "Lower", "Upper", "Trim", "Concat", "Substring",
        "RegexpExtract", "ToString", "ToChar", "LTrim",
        "RTrim", "Left", "Right", "Length", "CharLength",
        "Replace", "Initcap", "SafeConcat", "Repeat",
    ):
        return "TEXT"

    # Arithmetic / numeric functions
    if _is_instance_of_names(
        expr,
        "Add", "Sub", "Mul", "Div", "Paren", "Round",
        "Floor", "Ceil", "Abs", "Pow", "Mod", "Neg",
        "Greatest", "Least", "Sqrt", "Exp", "Ln", "Log",
    ):
        return "NUMERIC"

    # Boolean / comparison
    if _is_instance_of_names(
        expr,
        "EQ", "NEQ", "GT", "GTE", "LT", "LTE",
        "In", "Like", "Is", "Not", "And", "Or", "Between",
        "ILike", "RegexpLike",
    ):
        return "BOOLEAN"

    # Numeric aggregate functions
    if _is_instance_of_names(
        expr,
        "Sum", "Avg", "Stddev", "Variance", "Min", "Max",
        "ApproxDistinct", "ApproxQuantile", "Corr", "CovarPop",
        "CovarSamp", "StddevPop", "StddevSamp", "VarPop", "VarSamp",
    ):
        return "NUMERIC"

    # Integer aggregate / ranking functions
    if _is_instance_of_names(
        expr,
        "Count", "Rank", "DenseRank", "RowNumber", "Ntile",
        "CumeDist", "PercentRank",
    ):
        return "BIGINT"

    return "UNKNOWN"
