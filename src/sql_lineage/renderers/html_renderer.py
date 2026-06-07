"""
Interactive HTML renderer for SQL lineage (vis-network + sidebar).
"""

from __future__ import annotations

import json
import os
from typing import Dict

from sql_lineage.models import TableInfo
from sql_lineage.renderers.graphviz_renderer import LAYER_STYLE, get_layer


class HtmlRenderer:
    """Render a self-contained interactive HTML lineage explorer."""

    def __init__(self, tables: Dict[str, TableInfo]) -> None:
        self.tables = tables

    def render(self, output_dir: str = "output") -> str:
        json_data = json.dumps(
            {
                "nodes": self._build_nodes(),
                "edges": self._build_edges(),
            },
            indent=2,
        )
        html = _HTML_TEMPLATE.format(json_data=json_data)
        os.makedirs(output_dir, exist_ok=True)
        path = os.path.join(output_dir, "interactive_viewer.html")
        with open(path, "w", encoding="utf-8") as f:
            f.write(html)
        print(f"  🕸️  Interactive Dashboard → {path}")
        return path

    # ─────────────────────────────────────────────────────────────

    def _build_nodes(self):
        nodes = []
        for name, info in self.tables.items():
            layer = get_layer(name)
            style = LAYER_STYLE.get(layer, LAYER_STYLE["raw"])
            nodes.append(
                {
                    "id": name,
                    "label": f"{style['badge']} {name}",
                    "layer": layer,
                    "color": {
                        "background": "#161b22",
                        "border": style["hdr"],
                        "highlight": {"background": "#0d1117", "border": "#58a6ff"},
                    },
                    "font": {"color": "#c9d1d9", "face": "Helvetica Neue", "size": 16},
                    "shape": "box",
                    "margin": 15,
                    "borderWidth": 2,
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
                                "sources": [f"{s_t}.{s_c}" for s_t, s_c in c.sources],
                            }
                            for c in info.columns
                        ],
                    },
                }
            )
        return nodes

    def _build_edges(self):
        edges = []
        for name, info in self.tables.items():
            edge_color = LAYER_STYLE.get(get_layer(name), LAYER_STYLE["raw"])["hdr"]
            for src in info.source_tables:
                edges.append(
                    {
                        "from": src,
                        "to": name,
                        "color": {"color": edge_color, "highlight": "#58a6ff"},
                        "arrows": "to",
                        "width": 2,
                    }
                )
        return edges


# ─────────────────────────────────────────────────────────────────────────────
# HTML template
# ─────────────────────────────────────────────────────────────────────────────

_HTML_TEMPLATE = """\
<!DOCTYPE html>
<html>
<head>
    <title>SQL Lineage Explorer</title>
    <script type="text/javascript" src="https://unpkg.com/vis-network/standalone/umd/vis-network.min.js"></script>
    <style>
        body {{
            margin: 0; padding: 0;
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif;
            background-color: #0d1117; color: #c9d1d9;
            display: flex; height: 100vh; overflow: hidden;
        }}
        #network-container {{ flex-grow: 1; height: 100%; }}
        #network {{ width: 100%; height: 100%; }}
        #sidebar {{
            width: 450px; background-color: #161b22;
            border-left: 1px solid #30363d; padding: 25px;
            overflow-y: auto; box-shadow: -5px 0 15px rgba(0,0,0,0.5);
            display: flex; flex-direction: column; z-index: 10;
        }}
        #resizer {{
            width: 6px; background-color: #30363d;
            cursor: col-resize; z-index: 20; transition: background-color 0.2s;
        }}
        #resizer:hover, #resizer.active {{ background-color: #58a6ff; }}
        .hidden {{ display: none !important; }}
        h2 {{ margin-top: 0; color: #58a6ff; font-size: 22px; border-bottom: 1px solid #30363d; padding-bottom: 12px; }}
        .badge {{
            display: inline-block; padding: 4px 10px; border-radius: 12px;
            font-size: 12px; font-weight: bold; margin-bottom: 20px;
            border: 1px solid #30363d; background: #21262d; color: #e6edf3;
        }}
        .col-card {{
            background-color: #0d1117; border: 1px solid #30363d;
            border-radius: 8px; padding: 16px; margin-bottom: 16px;
        }}
        .col-name {{ font-weight: 600; color: #79c0ff; font-size: 15px; margin-bottom: 10px; }}
        .col-label {{
            font-size: 11px; color: #8b949e; text-transform: uppercase;
            letter-spacing: 0.8px; margin-top: 12px; margin-bottom: 6px; font-weight: 600;
        }}
        .code-block {{
            background-color: #161b22; padding: 10px; border-radius: 6px;
            font-family: ui-monospace, SFMono-Regular, "SF Mono", Menlo, Consolas, monospace;
            font-size: 13px; color: #e6edf3; overflow-x: auto; white-space: pre-wrap;
            border: 1px solid #21262d; line-height: 1.4;
        }}
        .source-tag {{
            display: inline-block; background: #238636; color: white;
            padding: 3px 10px; border-radius: 12px; font-size: 12px;
            margin-right: 6px; margin-top: 6px;
            font-family: ui-monospace, monospace;
        }}
        .empty-state {{
            text-align: center; color: #8b949e; margin-top: 50%;
            font-size: 16px; line-height: 1.5;
        }}
    </style>
</head>
<body>
    <div id="network-container"><div id="network"></div></div>
    <div id="resizer"></div>
    <div id="sidebar">
        <div style="margin-bottom: 20px;">
            <label for="table-filter" style="font-size:14px;font-weight:bold;color:#8b949e;display:block;margin-bottom:8px;">TARGET TABLE LINEAGE FILTER</label>
            <select id="table-filter" style="width:100%;padding:10px;background:#0d1117;color:#c9d1d9;border:1px solid #30363d;border-radius:6px;font-size:14px;">
                <option value="ALL">Show Entire Warehouse Lineage</option>
            </select>
            <label id="hide-cte-toggle" style="display:flex;align-items:center;gap:8px;margin-top:10px;cursor:pointer;user-select:none;color:#8b949e;font-size:12px;">
                <input type="checkbox" id="hide-intermediates" checked style="width:14px;height:14px;accent-color:#58a6ff;cursor:pointer;">
                Hide CTEs &amp; subqueries from list
            </label>
        </div>
        <div id="empty-state" class="empty-state">
            <div style="font-size:40px;margin-bottom:20px;">👈</div>
            Click on a table in the graph<br>to view column transformations.
        </div>
        <div id="table-details" class="hidden">
            <h2 id="table-name">Table Name</h2>
            <div id="table-badge" class="badge">file.sql</div>
            <div id="union-badge" class="badge" style="background:#a371f7;display:none;margin-left:5px;">UNION</div>
            <div id="joins-container" style="margin-bottom:20px;"></div>
            <div id="filters-container" style="margin-bottom:20px;"></div>
            <div id="columns-container"></div>
        </div>
    </div>

    <script type="text/javascript">
        // ── Resize handle ─────────────────────────────────────────────────
        const resizer = document.getElementById('resizer');
        const sidebar = document.getElementById('sidebar');
        let isResizing = false;
        resizer.addEventListener('mousedown', e => {{ isResizing = true; resizer.classList.add('active'); document.body.style.cursor = 'col-resize'; e.preventDefault(); }});
        document.addEventListener('mousemove', e => {{ if (!isResizing) return; const w = window.innerWidth - e.clientX; if (w > 200 && w < window.innerWidth - 300) sidebar.style.width = w + 'px'; }});
        document.addEventListener('mouseup', () => {{ if (isResizing) {{ isResizing = false; resizer.classList.remove('active'); document.body.style.cursor = 'default'; }} }});

        const graphData = {json_data};

        // ── Dropdown ──────────────────────────────────────────────────────
        const filterSelect = document.getElementById('table-filter');
        const hideIntermediates = document.getElementById('hide-intermediates');
        const sortedNodes = [...graphData.nodes].sort((a, b) => a.id.localeCompare(b.id));

        function isIntermediate(id) {{ return id.startsWith('[cte]') || id.startsWith('[subquery]'); }}

        function populateDropdown() {{
            const hide = hideIntermediates.checked;
            const cur = filterSelect.value;
            while (filterSelect.options.length > 1) filterSelect.remove(1);
            sortedNodes.forEach(n => {{
                if (hide && isIntermediate(n.id)) return;
                const o = document.createElement('option');
                o.value = n.id; o.textContent = n.id;
                filterSelect.appendChild(o);
            }});
            filterSelect.value = [...filterSelect.options].some(o => o.value === cur) ? cur : 'ALL';
        }}
        populateDropdown();
        hideIntermediates.addEventListener('change', populateDropdown);

        // ── vis-network setup ─────────────────────────────────────────────
        const container = document.getElementById('network');
        const data = {{ nodes: new vis.DataSet(graphData.nodes), edges: new vis.DataSet(graphData.edges) }};
        const network = new vis.Network(container, data, {{
            layout: {{ hierarchical: {{ enabled: true, direction: 'LR', sortMethod: 'directed', nodeSpacing: 100, levelSeparation: 350 }} }},
            physics: false,
            interaction: {{ hover: true, navigationButtons: true, keyboard: true, zoomView: true, dragView: true }}
        }});

        // ── Filter ────────────────────────────────────────────────────────
        filterSelect.addEventListener('change', e => {{
            const target = e.target.value;
            hideTableDetails(); network.unselectAll();
            if (target === 'ALL') {{
                data.nodes.clear(); data.edges.clear();
                data.nodes.add(graphData.nodes); data.edges.add(graphData.edges);
                network.fit({{animation: true}}); return;
            }}
            const visible = new Set([target]);
            const queue = [target];
            while (queue.length) {{
                const cur = queue.pop();
                graphData.edges.forEach(e => {{ if (e.to === cur && !visible.has(e.from)) {{ visible.add(e.from); queue.push(e.from); }} }});
            }}
            data.nodes.clear(); data.edges.clear();
            data.nodes.add(graphData.nodes.filter(n => visible.has(n.id)));
            data.edges.add(graphData.edges.filter(e => visible.has(e.from) && visible.has(e.to)));
            network.selectNodes([target]);
            showTableDetails(data.nodes.get(target));
            setTimeout(() => network.fit({{animation: true}}), 100);
        }});

        // ── Click / hover ─────────────────────────────────────────────────
        network.on('click', p => {{ if (p.nodes.length > 0) showTableDetails(data.nodes.get(p.nodes[0])); else hideTableDetails(); }});

        network.on('hoverNode', p => {{
            const conn = new Set(network.getConnectedNodes(p.node)); conn.add(p.node);
            data.nodes.update(data.nodes.map(n => ({{id: n.id, opacity: conn.has(n.id) ? 1 : 0.15}})));
            data.edges.update(data.edges.map(e => ({{id: e.id, color: {{opacity: (e.from === p.node || e.to === p.node) ? 1 : 0.1}}}})));
        }});
        network.on('blurNode', () => {{
            data.nodes.update(data.nodes.map(n => ({{id: n.id, opacity: 1}})));
            data.edges.update(data.edges.map(e => ({{id: e.id, color: {{opacity: 1}}}})));
        }});

        // ── Sidebar helpers ───────────────────────────────────────────────
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

            const joinsContainer = document.getElementById('joins-container');
            joinsContainer.innerHTML = '';

            // Union branches
            if (node.table_info.is_union && node.table_info.union_branches?.length > 0) {{
                const lbl = document.createElement('div'); lbl.className = 'col-label'; lbl.textContent = 'Combined Tables'; joinsContainer.appendChild(lbl);
                const wrap = document.createElement('div'); wrap.style.cssText = 'display:flex;flex-wrap:wrap;gap:6px;align-items:center;margin-bottom:14px;';
                const opColors = {{'UNION ALL':'#1f6feb','UNION':'#388bfd','INTERSECT':'#2ea043','EXCEPT':'#da3633'}};
                node.table_info.union_branches.forEach(b => {{
                    if (b.operator) {{
                        const sep = document.createElement('span');
                        sep.textContent = b.operator;
                        sep.style.cssText = `background:${{opColors[b.operator]||'#6e40c9'}};color:#fff;padding:2px 8px;border-radius:6px;font-size:11px;font-weight:bold;letter-spacing:.5px;white-space:nowrap;`;
                        wrap.appendChild(sep);
                    }}
                    const tag = document.createElement('span'); tag.className = 'source-tag'; tag.style.background = '#6e40c9'; tag.textContent = b.table; wrap.appendChild(tag);
                }});
                joinsContainer.appendChild(wrap);
            }}

            // Joins
            if (node.table_info.joins?.length > 0) {{
                const lbl = document.createElement('div'); lbl.className = 'col-label'; lbl.textContent = 'Join Logic'; joinsContainer.appendChild(lbl);
                node.table_info.joins.forEach(j => {{
                    const card = document.createElement('div');
                    card.style.cssText = 'background:#161b22;border:1px solid #a371f7;border-radius:8px;padding:12px;margin-bottom:10px;';
                    const hdr = document.createElement('div'); hdr.style.cssText = 'display:flex;align-items:center;gap:8px;margin-bottom:8px;flex-wrap:wrap;';
                    const mk = (txt, bg) => {{ const s = document.createElement('span'); s.textContent = txt; s.style.cssText = `background:${{bg}};color:#fff;padding:3px 10px;border-radius:12px;font-size:12px;font-family:monospace;`; return s; }};
                    hdr.appendChild(mk(j.left||'?','#1f6feb'));
                    const kind = document.createElement('span'); kind.textContent = j.kind||'JOIN'; kind.style.cssText = 'background:#a371f7;color:#fff;padding:3px 8px;border-radius:6px;font-size:11px;font-weight:bold;letter-spacing:.5px;'; hdr.appendChild(kind);
                    hdr.appendChild(mk(j.right||'?','#1f6feb'));
                    card.appendChild(hdr);
                    if (j.condition) {{ const cond = document.createElement('div'); cond.style.cssText = 'font-family:monospace;font-size:12px;color:#8b949e;white-space:pre-wrap;word-break:break-all;'; cond.textContent = j.condition; card.appendChild(cond); }}
                    joinsContainer.appendChild(card);
                }});
            }}

            // Filters + GROUP BY
            const filtersContainer = document.getElementById('filters-container');
            filtersContainer.innerHTML = '';
            if (node.table_info.filters?.length > 0) {{
                const lbl = document.createElement('div'); lbl.className = 'col-label'; lbl.textContent = 'Table Filters (WHERE/HAVING)'; filtersContainer.appendChild(lbl);
                node.table_info.filters.forEach(f => {{ const code = document.createElement('div'); code.className = 'code-block'; code.style.borderColor = '#1f6feb'; code.textContent = f; filtersContainer.appendChild(code); }});
            }}
            if (node.table_info.group_by?.length > 0) {{
                const lbl = document.createElement('div'); lbl.className = 'col-label'; lbl.textContent = 'GROUP BY'; filtersContainer.appendChild(lbl);
                const wrap = document.createElement('div'); wrap.style.cssText = 'display:flex;flex-wrap:wrap;gap:6px;margin-bottom:14px;';
                node.table_info.group_by.forEach(g => {{ const tag = document.createElement('span'); tag.style.cssText = 'font-family:monospace;font-size:12px;background:#1c2128;border:1px solid #2ea043;color:#7ee787;padding:3px 10px;border-radius:8px;white-space:nowrap;'; tag.textContent = g; wrap.appendChild(tag); }});
                filtersContainer.appendChild(wrap);
            }}

            // Columns
            const colsContainer = document.getElementById('columns-container');
            colsContainer.innerHTML = '';
            const typeColors = {{'BIGINT':'#1f6feb','INTEGER':'#1f6feb','INT':'#1f6feb','SMALLINT':'#1f6feb','NUMERIC':'#a371f7','FLOAT':'#a371f7','DOUBLE':'#a371f7','DECIMAL':'#a371f7','REAL':'#a371f7','TEXT':'#2ea043','VARCHAR':'#2ea043','CHAR':'#2ea043','DATE':'#d2a8ff','TIMESTAMP':'#d2a8ff','TIMESTAMPTZ':'#d2a8ff','TIME':'#d2a8ff','BOOLEAN':'#f47067','BOOL':'#f47067'}};

            node.table_info.columns.forEach(col => {{
                const card = document.createElement('div'); card.className = 'col-card';
                const nameDiv = document.createElement('div'); nameDiv.className = 'col-name'; nameDiv.style.cssText = 'display:flex;align-items:center;gap:8px;flex-wrap:wrap;';
                const nameText = document.createElement('span'); nameText.textContent = '🔑 ' + col.name; nameDiv.appendChild(nameText);
                if (col.data_type && !['INHERITED','UNKNOWN',''].includes(col.data_type)) {{
                    const badge = document.createElement('span');
                    const base = col.data_type.replace(/\\(.*\\)/, '').trim();
                    const bg = typeColors[base] || '#30363d';
                    badge.textContent = col.data_type;
                    badge.style.cssText = `background:${{bg}}22;color:${{bg}};border:1px solid ${{bg}}55;font-size:10px;font-weight:bold;font-family:monospace;padding:1px 7px;border-radius:6px;letter-spacing:.5px;`;
                    nameDiv.appendChild(badge);
                }}
                card.appendChild(nameDiv);

                const isPassthrough = (col.expression.toLowerCase() === col.name.toLowerCase() || col.expression === '');
                const opLbl = document.createElement('div'); opLbl.className = 'col-label'; opLbl.textContent = isPassthrough ? 'Passed Through Directly' : 'SQL Transformation'; card.appendChild(opLbl);
                if (!isPassthrough) {{ const code = document.createElement('div'); code.className = 'code-block'; code.textContent = col.expression; card.appendChild(code); }}

                if (col.sources?.length > 0) {{
                    const srcLbl = document.createElement('div'); srcLbl.className = 'col-label'; srcLbl.textContent = 'Depends On Columns'; card.appendChild(srcLbl);
                    const tagsDiv = document.createElement('div');
                    col.sources.forEach(src => {{
                        const tag = document.createElement('span'); tag.className = 'source-tag'; tag.textContent = src;
                        const dotIdx = src.lastIndexOf('.');
                        const tableName = dotIdx > 0 ? src.substring(0, dotIdx) : src;
                        if (data.nodes.get(tableName)) {{
                            tag.style.cursor = 'pointer'; tag.title = 'Click to jump to ' + tableName;
                            tag.addEventListener('mouseenter', () => tag.style.opacity = '0.7');
                            tag.addEventListener('mouseleave', () => tag.style.opacity = '1');
                            tag.addEventListener('click', e => {{
                                e.stopPropagation();
                                network.selectNodes([tableName]);
                                network.focus(tableName, {{scale:1.2, animation:{{duration:600, easingFunction:'easeInOutQuad'}}}});
                                showTableDetails(data.nodes.get(tableName));
                            }});
                        }}
                        tagsDiv.appendChild(tag);
                    }});
                    card.appendChild(tagsDiv);
                }}
                colsContainer.appendChild(card);
            }});
        }}

        network.once('afterDrawing', () => {{ if (graphData.nodes.length > 0) network.fit({{animation: true}}); }});
    </script>
</body>
</html>
"""
