"""Lossless readable column tables for repeated, already-whitelisted sports data."""

import json
from collections import Counter


def _json(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def _flatten(row, prefix=""):
    flat = {}
    for key, value in row.items():
        if not isinstance(key, str) or "." in key or key.startswith("$"):
            raise ValueError("Non-table field")
        path = prefix + key
        if isinstance(value, dict) and value:
            flat.update(_flatten(value, path + "."))
        else:
            flat[path] = value
    return flat


def _cells(row, columns):
    dense = [None if key not in row else {"$null": True} if row[key] is None else _pack(row[key])
             for key in columns]
    sparse = {str(index): value for index, value in enumerate(dense) if value is not None}
    return sparse if len(_json(sparse)) < len(_json(dense)) else dense


def _pack(value):
    if isinstance(value, dict):
        return {k: _pack(v) for k, v in value.items()}
    if not isinstance(value, list):
        return value
    if value and all(isinstance(item, str) and item in {"W", "D", "L"} for item in value):
        sequence = {"$results": "".join(value)}
        if len(_json(sequence)) < len(_json(value)):
            return sequence
    normal = [_pack(v) for v in value]
    if len(value) < 2 or not all(isinstance(v, dict) for v in value):
        return normal
    try:
        rows = [_flatten(v) for v in value]
    except ValueError:
        return normal
    shared = set.intersection(*(set(row) for row in rows))
    defaults = {key: rows[0][key] for key in sorted(shared)
                if all(_json(row[key]) == _json(rows[0][key]) for row in rows[1:])}
    columns = sorted({key for row in rows for key in row} - defaults.keys())
    packed = {"$table": 1, "columns": columns,
              "rows": [_cells(row, columns) for row in rows]}
    if defaults:
        packed["defaults"] = {key: _pack(value) for key, value in defaults.items()}
    # A sourced context can exist for only part of the batch. Repeating its
    # constants in every populated cell is wasteful; explicit absent rows keep
    # unavailable evidence genuinely absent rather than filling it with facts.
    sparse_defaults, absent = dict(defaults), {}
    for key in sorted({key for row in rows for key in row} - shared):
        present = [row[key] for row in rows if key in row]
        if len(present) >= 2 and all(_json(item) == _json(present[0]) for item in present[1:]):
            sparse_defaults[key] = present[0]
            absent[key] = [index for index, row in enumerate(rows) if key not in row]
    if absent:
        sparse_columns = sorted({key for row in rows for key in row} - sparse_defaults.keys())
        sparse = {"$table": 1, "columns": sparse_columns,
                  "rows": [_cells(row, sparse_columns) for row in rows],
                  "defaults": {key: _pack(value) for key, value in sparse_defaults.items()},
                  "absent": absent}
        if len(_json(sparse)) < len(_json(packed)):
            packed = sparse
    return packed if len(_json(packed)) < len(_json(normal)) else normal


def _wire_packet_v1(packet):
    """Keep small inputs familiar; compact large ones without dropping a value."""
    if len(_json(packet)) <= 75_000:
        return packet
    compact = _pack(packet)
    strings = Counter()

    def visit(value):
        if isinstance(value, dict):
            for key, item in value.items():
                if key != "columns":
                    visit(item)
        elif isinstance(value, list):
            for item in value:
                visit(item)
        elif isinstance(value, str) and len(value) >= 24:
            strings[value] += 1

    visit(compact)
    symbols = {value: f"t{index}" for index, value in enumerate(sorted(
        value for value, count in strings.items() if count >= 3), 1)}

    def replace(value):
        if isinstance(value, dict):
            return {key: item if key == "columns" else replace(item) for key, item in value.items()}
        if isinstance(value, list):
            return [replace(item) for item in value]
        return {"$text": symbols[value]} if isinstance(value, str) and value in symbols else value

    if symbols:
        pooled = replace(compact)
        pooled["shared_text"] = {symbol: value for value, symbol in symbols.items()}
        if len(_json(pooled)) < len(_json(compact)):
            compact = pooled
    # Different fixture IDs often share a long official API endpoint. Preserve
    # the exact URL while sending its fixed prefix only once, without refetching.
    prefixes = Counter()

    def url_prefix(value):
        if isinstance(value, str) and value.startswith("https://") and "?" in value and "=" in value:
            return value.split("=", 1)[0] + "="
        return None

    def count_prefixes(value):
        if isinstance(value, dict):
            for key, item in value.items():
                if key not in {"columns", "shared_text"}:
                    count_prefixes(item)
        elif isinstance(value, list):
            for item in value:
                count_prefixes(item)
        elif (prefix := url_prefix(value)) is not None:
            prefixes[prefix] += 1

    count_prefixes(compact)
    lookup = {prefix: f"u{index}" for index, prefix in enumerate(sorted(
        prefix for prefix, count in prefixes.items() if count >= 3 and (len(prefix) - 24) * count > len(prefix) + 20), 1)}

    def compact_urls(value):
        if isinstance(value, dict):
            return {key: item if key in {"columns", "shared_text"} else compact_urls(item)
                    for key, item in value.items()}
        if isinstance(value, list):
            return [compact_urls(item) for item in value]
        prefix = url_prefix(value)
        return {"$url": [lookup[prefix], value[len(prefix):]]} if prefix in lookup else value

    if lookup:
        pooled = compact_urls(compact)
        pooled["shared_url_prefixes"] = {symbol: prefix for prefix, symbol in lookup.items()}
        if len(_json(pooled)) < len(_json(compact)):
            compact = pooled
    compact["table_encoding"] = (
        "COLUMN_TABLES_V1: $table=1 has columns and rows. Each row supplies values in column order; "
        "A row object instead uses zero-based column indices as keys and omits missing cells. "
        "dots in column names are nested object paths. defaults supplies common path/value fields for EVERY row. "
        "Exception: absent[path] lists zero-based rows where that default field is missing. "
        "null cells mean field absent; {$null:true} is literal null. "
        "{$text:id} means the exact text at shared_text[id]. "
        "{$url:[id,suffix]} means shared_url_prefixes[id] concatenated with suffix, the exact source URL. "
        "{$results:sequence} lists results newest first, one character per match: W=win,D=draw,L=loss. "
        "All data values, source times and numbers are unchanged. Read every row."
    )
    return compact if len(_json(compact)) < len(_json(packet)) else packet


def _expand_tables(value, _texts=None, _url_prefixes=None):
    """Local verification of the exact wire data, never a model/tool executor."""
    if isinstance(value, list):
        return [_expand_tables(v, _texts, _url_prefixes) for v in value]
    if not isinstance(value, dict):
        return value
    if value == {"$null": True}:
        return None
    if _texts is None:
        _texts = value.get("shared_text", {})
    if _url_prefixes is None:
        _url_prefixes = value.get("shared_url_prefixes", {})
    if set(value) == {"$text"}:
        return _texts[value["$text"]]
    if set(value) == {"$url"}:
        reference = value["$url"]
        if not isinstance(reference, list) or len(reference) != 2 or not all(isinstance(v, str) for v in reference):
            raise ValueError("Invalid URL reference")
        return _url_prefixes[reference[0]] + reference[1]
    if set(value) == {"$results"}:
        return list(value["$results"])
    if value.get("$table") == 1 and set(value) in (
            {"$table", "columns", "rows"}, {"$table", "columns", "rows", "defaults"},
            {"$table", "columns", "rows", "defaults", "absent"}):
        output = []
        absent = value.get("absent", {})
        if (not isinstance(absent, dict) or not set(absent) <= set(value.get("defaults", {}))
                or any(not isinstance(indices, list) or any(type(i) is not int or not 0 <= i < len(value["rows"])
                       for i in indices) for indices in absent.values())):
            raise ValueError("Invalid absent table fields")
        for index, values in enumerate(value["rows"]):
            if isinstance(values, dict):
                if any(not isinstance(k, str) or not k.isdecimal() or str(int(k)) != k
                       or not 0 <= int(k) < len(value["columns"]) for k in values):
                    raise ValueError("Invalid sparse table column")
                values = [values.get(str(column)) for column in range(len(value["columns"]))]
            elif not isinstance(values, list) or len(values) != len(value["columns"]):
                raise ValueError("Invalid table width")
            row = {}
            cells = {path: cell for path, cell in zip(value["columns"], values, strict=True)
                     if cell is not None}
            for path, cell in {**value.get("defaults", {}), **cells}.items():
                if index in absent.get(path, []):
                    continue
                parts = path.split(".")
                target = row
                for key in parts[:-1]:
                    target = target.setdefault(key, {})
                target[parts[-1]] = _expand_tables(cell, _texts, _url_prefixes)
            output.append(row)
        return output
    return {k: _expand_tables(v, _texts, _url_prefixes) for k, v in value.items()
            if k not in {"table_encoding", "shared_text", "shared_url_prefixes"}}


def wire_packet(packet):
    basic = _wire_packet_v1(packet)
    if len(_json(basic)) <= 100_000:
        return basic
    from goal_analysis.agents.grok_packet_dense import pack_dense
    dense = pack_dense(packet, _wire_packet_v1)
    return dense if len(_json(dense)) < len(_json(basic)) else basic


def expand_tables(value):
    if isinstance(value, dict) and "dense_encoding" in value:
        from goal_analysis.agents.grok_packet_dense import restore_dense, restore_stats
        return restore_stats(_expand_tables(restore_dense(value)))
    return _expand_tables(value)
