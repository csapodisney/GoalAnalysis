"""Readable, lossless fallback for oversized shared Grok packets.

Numerical evidence stays literal JSON numbers. Only opaque provenance hex uses
base64; a semantic checksum binds the exact expanded fields and values.
"""

import base64
import hashlib
import json
import re
from collections import Counter
from copy import deepcopy

from goal_analysis.agents.grok_packet_dense import _factor_stats, restore_stats
from goal_analysis.agents.grok_packet_tables import _expand_tables, _pack

ALPHABET = "0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"


def symbol(n):
    result = ""
    while True:
        result = ALPHABET[n % 62] + result
        n //= 62
        if not n:
            return result


def dumps(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def opaque_provenance(packet):
    values = set()

    def visit(value):
        if isinstance(value, dict):
            for key, item in value.items():
                if (
                    isinstance(item, str)
                    and (
                        key.endswith(("_hash", "_sha256"))
                        or key in {"http_receipt_id", "http_observation_id"}
                    )
                    and len(item) in {32, 64}
                    and re.fullmatch("[0-9a-f]+", item)
                ):
                    values.add(item)
                else:
                    visit(item)
        elif isinstance(value, list):
            for item in value:
                visit(item)

    visit(packet)
    return values


ENCODING = (
    "READABLE_TABLES_V1: Replace dot-separated ^IDs in keys/columns using fields. "
    "First replace {$shared:ID} using objects[ID], then string @ID refers once to texts[ID]. !JID:a,b interleaves templates[ID] parts with literal "
    "comma-separated provider IDs/dates; !Htext is unpadded base64 of opaque hex provenance bytes "
    "(not numerical evidence); !Stext is literal text. Decode strings, then vector tables. "
    "A $vectors object has n rows, columns and vectors in column order. A vector list gives "
    "each row directly; {s:{row:value}} means null for other rows; {r:[[value,count],...]} "
    "is consecutive repeated values. Rebuild COLUMN_TABLES_V1: $table,columns,rows,defaults,absent. "
    "Null table cells mean absent; {$null:true} means literal null. Dots are nested paths. "
    "Defaults apply to all rows except absent[path] row indices. {$results:WDL} is newest-first form. "
    "Finally source_ref indexes stats_sources; fixture fact_refs [group,row] picks "
    "stats_fact_groups[group].facts[row], inheriting category/status. Every row, numerical "
    "literal, timestamp, identity and source is preserved; no omission or rounding."
)


def pack_readable(packet):
    """Return a lossless transport candidate; the caller still enforces its ceiling."""
    opaque_values = opaque_provenance(packet)
    original = _pack(_factor_stats(deepcopy(packet)))
    fields = Counter()
    counts = Counter()
    proof = Counter()

    def field_count(x):
        if isinstance(x, dict):
            for k, v in x.items():
                fields.update(k.split("."))
                if k == "columns":
                    for name in v:
                        fields.update(name.split("."))
                else:
                    field_count(v)
        elif isinstance(x, list):
            for v in x:
                field_count(v)

    field_count(original)
    if any(f.startswith("^") for f in fields):
        raise ValueError("Reserved field")
    aliases = {}
    names = {}
    for f, n in sorted(fields.items(), key=lambda row: -(len(row[0]) - 3) * row[1]):
        key = "^" + symbol(len(aliases))
        if len(f) > len(key) and n * (len(f) - len(key)) > len(f) + len(key) + 5:
            aliases[f] = key
            names[key] = f

    def name(x):
        return ".".join(aliases.get(f, f) for f in x.split("."))

    def alias(x):
        if isinstance(x, dict):
            return {
                name(k): [name(c) for c in v] if k == "columns" else alias(v) for k, v in x.items()
            }
        if isinstance(x, list):
            return [alias(v) for v in x]
        return x

    original = alias(original)
    templates = {}
    template_ids = {}
    variable = re.compile(r"(?:mt|pl|tm|sn|comp)_\d+|\d{4}-\d{2}-\d{2}")

    def prepare(x):
        if isinstance(x, dict):
            return {k: prepare(v) for k, v in x.items()}
        if isinstance(x, list):
            return [prepare(v) for v in x]
        if not isinstance(x, str):
            return x
        if x in opaque_values:
            proof["opaque_provenance_values"] += 1
            return "!H" + base64.b64encode(bytes.fromhex(x)).decode().rstrip("=")
        stamp = re.fullmatch(
            r"(\d{4}-\d{2}-\d{2}T)(\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2}))", x
        )
        if stamp:
            parts = [stamp[1], ""]
            key = dumps(parts)
            if key not in template_ids:
                i = symbol(len(template_ids))
                template_ids[key] = i
                templates[i] = parts
            proof["templated_timestamps"] += 1
            return "!J" + template_ids[key] + ":" + stamp[2]
        if x.startswith("https://") and len(x) > 35:
            matches = list(variable.finditer(x))
            if matches:
                parts = []
                end = 0
                values = []
                for m in matches:
                    parts.append(x[end : m.start()])
                    values.append(m.group())
                    end = m.end()
                parts.append(x[end:])
                key = dumps(parts)
                if key not in template_ids:
                    i = symbol(len(template_ids))
                    template_ids[key] = i
                    templates[i] = parts
                proof["templated_source_strings"] += 1
                return "!J" + template_ids[key] + ":" + ",".join(values)
        return "!S" + x if x.startswith(("!", "@")) else x

    original = prepare(original)

    def count(x):
        if isinstance(x, dict):
            for k, v in x.items():
                if k != name("columns"):
                    count(v)
        elif isinstance(x, list):
            for v in x:
                count(v)
        elif isinstance(x, str):
            counts[x] += 1

    count(original)
    texts = {}
    refs = {}
    for x, n in sorted(counts.items(), key=lambda row: (-row[1], row[0])):
        i = symbol(len(texts))
        ref = "@" + i
        if n > 1 and n * (len(dumps(x)) - len(dumps(ref))) > len(dumps(x)) + len(i) + 5:
            texts[i] = x
            refs[x] = ref

    def pool(x):
        if isinstance(x, dict):
            return {k: v if k == name("columns") else pool(v) for k, v in x.items()}
        if isinstance(x, list):
            return [pool(v) for v in x]
        if isinstance(x, str) and x in refs:
            proof["shared_string_uses"] += 1
            return refs[x]
        return x

    value = pool(original)
    table, columns, rows = name("$table"), name("columns"), name("rows")

    def vector(v):
        choices = [v, {"s": {str(i): item for i, item in enumerate(v) if item is not None}}]
        runs = []
        for item in v:
            if runs and dumps(runs[-1][0]) == dumps(item):
                runs[-1][1] += 1
            else:
                runs.append([item, 1])
        choices.append({"r": runs})
        return min(choices, key=lambda c: len(dumps(c)))

    def vectors(x):
        if isinstance(x, dict):
            clean = {k: vectors(v) for k, v in x.items()}
            if clean.get(table) == 1 and columns in clean and rows in clean:
                width = len(clean[columns])
                rr = clean[rows]
                rr = [
                    [row.get(str(i)) for i in range(width)] if isinstance(row, dict) else row
                    for row in rr
                ]
                trans = {k: v for k, v in clean.items() if k not in {table, rows}}
                trans["$vectors"] = len(rr)
                trans["vectors"] = [vector(list(v)) for v in zip(*rr)] if width else []
                if len(dumps(trans)) < len(dumps(clean)):
                    proof["vector_tables"] += 1
                    return trans
            return clean
        if isinstance(x, list):
            return [vectors(v) for v in x]
        return x

    body = vectors(value)
    container_counts = Counter()

    def count_containers(x):
        if isinstance(x, (dict, list)):
            key = dumps(x)
            if len(key) > 30:
                container_counts[key] += 1
            for v in x.values() if isinstance(x, dict) else x:
                count_containers(v)

    count_containers(body)
    objects = {}
    object_ids = {}

    def share(x):
        if isinstance(x, (dict, list)):
            key = dumps(x)
            n = container_counts[key]
            if n > 1 and n * (len(key) - 18) > len(key) + 8:
                if key not in object_ids:
                    i = symbol(len(objects))
                    object_ids[key] = i
                    objects[i] = x
                proof["shared_container_uses"] += 1
                return {"$shared": object_ids[key]}
        if isinstance(x, dict):
            return {k: share(v) for k, v in x.items()}
        if isinstance(x, list):
            return [share(v) for v in x]
        return x

    body = share(body)
    digest = hashlib.sha256(
        json.dumps(
            packet, ensure_ascii=False, separators=(",", ":"), sort_keys=True, allow_nan=False
        ).encode()
    ).hexdigest()
    return {
        "encoding": ENCODING,
        "body": body,
        "objects": objects,
        "texts": texts,
        "templates": templates,
        "fields": names,
        "packet_sha256": digest,
        "compression_counts": dict(proof),
    }


def restore_readable(p):
    """Expand only this exact codec version and reject damaged or oversized structures."""
    if not isinstance(p, dict) or p.get("encoding") != ENCODING:
        raise ValueError("Unknown readable packet encoding")
    if len(dumps(p)) > 2_000_000:
        raise ValueError("Readable packet representation too large")
    expected = {
        "encoding",
        "body",
        "objects",
        "texts",
        "templates",
        "fields",
        "packet_sha256",
        "compression_counts",
    }
    if set(p) != expected or not re.fullmatch("[0-9a-f]{64}", str(p["packet_sha256"])):
        raise ValueError("Invalid readable packet envelope")
    for key in ("objects", "texts", "templates", "fields", "compression_counts"):
        if not isinstance(p[key], dict) or len(p[key]) > 20_000:
            raise ValueError("Invalid readable packet dictionary")
    if any(not isinstance(k, str) or not isinstance(v, str) for k, v in p["texts"].items()):
        raise ValueError("Invalid shared text")
    if any(
        not isinstance(k, str)
        or not isinstance(v, list)
        or not 2 <= len(v) <= 50
        or any(not isinstance(part, str) for part in v)
        for k, v in p["templates"].items()
    ):
        raise ValueError("Invalid source template")
    if any(
        not re.fullmatch(r"\^[0-9A-Za-z]+", str(k))
        or not isinstance(v, str)
        or v.startswith("^")
        or "." in v
        for k, v in p["fields"].items()
    ):
        raise ValueError("Invalid field dictionary")
    if any(type(v) is not int or not 0 <= v <= 200_000 for v in p["compression_counts"].values()):
        raise ValueError("Invalid compression counters")
    names = p["fields"]

    def name(x):
        return ".".join(names.get(f, f) for f in x.split("."))

    def strings(x):
        if isinstance(x, dict):
            return {k: strings(v) for k, v in x.items()}
        if isinstance(x, list):
            return [strings(v) for v in x]
        if not isinstance(x, str):
            return x
        if x.startswith("@"):
            x = p["texts"][x[1:]]
        if x.startswith("!H"):
            text = x[2:]
            raw = base64.b64decode(text + "=" * ((-len(text)) % 4), validate=True)
            if len(raw) not in {16, 32} or base64.b64encode(raw).decode().rstrip("=") != text:
                raise ValueError("Invalid opaque provenance encoding")
            return raw.hex()
        if x.startswith("!J"):
            i, raw = x[2:].split(":", 1)
            parts = p["templates"][i]
            values = raw.split(",")
            if len(parts) != len(values) + 1:
                raise ValueError("Wrong fragment count")
            return "".join(a + b for a, b in zip(parts, values)) + parts[-1]
        if x.startswith("!S"):
            return x[2:]
        return x

    def fields(x):
        if isinstance(x, dict):
            return {
                name(k): [name(c) for c in v] if name(k) == "columns" else fields(v)
                for k, v in x.items()
            }
        if isinstance(x, list):
            return [fields(v) for v in x]
        return x

    def tables(x):
        if isinstance(x, dict):
            if "$vectors" in x:
                n = x["$vectors"]
                cols = []
                if (
                    type(n) is not int
                    or not 0 <= n <= 20_000
                    or not isinstance(x.get("columns"), list)
                    or not isinstance(x.get("vectors"), list)
                    or len(x["columns"]) != len(x["vectors"])
                    or n * len(x["columns"]) > 100_000
                ):
                    raise ValueError("Invalid vector table shape")
                for v in x["vectors"]:
                    if isinstance(v, list):
                        col = v
                    elif isinstance(v, dict) and set(v) == {"s"}:
                        if not isinstance(v["s"], dict) or any(
                            not isinstance(k, str)
                            or not k.isdecimal()
                            or str(int(k)) != k
                            or not 0 <= int(k) < n
                            for k in v["s"]
                        ):
                            raise ValueError("Invalid sparse vector index")
                        col = [v["s"].get(str(i)) for i in range(n)]
                    elif isinstance(v, dict) and set(v) == {"r"}:
                        runs = v["r"]
                        if (
                            not isinstance(runs, list)
                            or any(
                                not isinstance(run, list)
                                or len(run) != 2
                                or type(run[1]) is not int
                                or not 0 < run[1] <= n
                                for run in runs
                            )
                            or sum(run[1] for run in runs) != n
                        ):
                            raise ValueError("Invalid vector runs")
                        if sum(len(dumps(item)) * count for item, count in runs) > 4_000_000:
                            raise ValueError("Expanded vector too large")
                        col = [item for item, count in runs for _ in range(count)]
                    else:
                        raise ValueError("Invalid vector")
                    if len(col) != n:
                        raise ValueError("Invalid vector length")
                    cols.append(col)
                x = {k: v for k, v in x.items() if k not in {"$vectors", "vectors"}}
                x["$table"] = 1
                x["rows"] = [list(row) for row in zip(*cols)] if cols else [[] for _ in range(n)]
            return {k: tables(v) for k, v in x.items()}
        if isinstance(x, list):
            return [tables(v) for v in x]
        return x

    def contains_reference(value):
        if isinstance(value, dict):
            return set(value) == {"$shared"} or any(contains_reference(v) for v in value.values())
        return isinstance(value, list) and any(contains_reference(v) for v in value)

    shared_characters = 0

    def unshare(x):
        nonlocal shared_characters
        if isinstance(x, dict) and set(x) == {"$shared"}:
            v = p["objects"][x["$shared"]]
            shared_characters += len(dumps(v))
            if shared_characters > 4_000_000:
                raise ValueError("Shared expansion too large")
            v = deepcopy(v)
            if not isinstance(v, (dict, list)) or contains_reference(v):
                raise ValueError("Nested or recursive shared structure")
            return v
        if isinstance(x, dict):
            return {k: unshare(v) for k, v in x.items()}
        if isinstance(x, list):
            return [unshare(v) for v in x]
        return x

    packet = restore_stats(_expand_tables(tables(fields(strings(unshare(p["body"]))))))
    digest = hashlib.sha256(
        json.dumps(
            packet, ensure_ascii=False, separators=(",", ":"), sort_keys=True, allow_nan=False
        ).encode()
    ).hexdigest()
    if digest != p["packet_sha256"]:
        raise ValueError("Packet semantic digest mismatch")
    return packet
