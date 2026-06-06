# SQL Lineage Tracker

SQL Lineage Tracker is a powerful static analysis tool that parses raw SQL files to generate interactive table-level and column-level Data Lineage graphs.

It deeply analyzes your SQL logic to identify Common Table Expressions (CTEs), Subqueries, `JOIN` conditions, and `WHERE` filters, creating an interactive DAG (Directed Acyclic Graph) of your data pipeline.

## Features

- **Column-Level Lineage**: Traces exactly which source columns contribute to the final outputs.
- **Intermediate Node Parsing**: Does not flatten SQL! CTEs and Inline Subqueries are parsed as explicit nodes in the lineage graph.
- **Filter and Join Extraction**: Captures join conditions and filters (WHERE/HAVING) applied to tables.
- **Custom Schema/Function Support**: Accurately tracks dependencies through SQL functions.
- **Interactive UI**: Outputs a beautiful, interactive HTML dashboard (via vis.js) to explore the lineage.
  - Resize panels dynamically
  - Hover to isolate a node's immediate neighborhood
  - Click on column dependencies to jump through the warehouse graph
- **Static Visualizations**: Generates Graphviz static PNG/SVG maps of table and column lineages.

## Installation

This project is packaged with `pyproject.toml` and relies on `uv` or `pip`.

```bash
# Recommended: Install using uv
uv sync

# Or using pip
pip install .
```

## Usage

You can point the tool at any directory containing `.sql` files:

```bash
# Run via uv
uv run sql-lineage examples/sql/ -i

# Or if installed globally/in a venv
sql-lineage examples/sql/ -i
```

### Options

- `sql_dir`: The directory containing your SQL files.
- `-i, --interactive`: Generate the interactive HTML viewer in addition to static images.
- `-t, --target`: Specify the final table to trace back from (defaults to the last parsed file).
- `-o, --output-dir`: Where to save the output graphs (defaults to `output/`).
- `-f, --format`: Static image format: `png`, `svg`, or `pdf` (defaults to `png`).
- `-d, --dialect`: SQL Dialect for parsing (defaults to `postgres`).

## Examples

We've provided a set of example SQL files in the `examples/sql/` directory covering basic staging tables all the way to complex nested queries and financial reports. Run the command above to see the tool in action.

## Built With
- `sqlglot` - Pure Python SQL parser and transpiler
- `graphviz` - Graph visualization
- `vis.js` - Interactive browser networks
