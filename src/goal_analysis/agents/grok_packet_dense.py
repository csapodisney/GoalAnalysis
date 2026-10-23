"""Readable dictionaries for unusually large batches; no lossy numerical compression."""

import json
from collections import Counter
from copy import deepcopy


def _json(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def _factor_stats(packet):
    packet = deepcopy(packet)
    sources, groups, source_ids, group_ids, fact_ids = [], [], {}, {}, {}

    def source_ref(source):
        key = _json(source)
        if key not in source_ids:
            source_ids[key] = len(sources)
            sources.append(source)
        return source_ids[key]

    for fixture in packet.get("fixtures", []):
        context = fixture.get("the_stats_api", {})
        mapping = context.get("mapping", {})
        if isinstance(mapping.get("source"), dict):
            mapping["source_ref"] = source_ref(mapping.pop("source"))
        if "facts" not in context:
            continue
        refs = []
        for original in context.pop("facts"):
            fact = deepcopy(original)
            key = (fact.pop("category"), fact.pop("status"))
            if key not in group_ids:
                group_ids[key] = len(groups)
                groups.append({"category": key[0], "status": key[1], "facts": []})
            group = group_ids[key]
            if isinstance(fact.get("source"), dict):
                fact["source_ref"] = source_ref(fact.pop("source"))
            identity = (group, _json(fact))
            if identity not in fact_ids:
                fact_ids[identity] = len(groups[group]["facts"])
                groups[group]["facts"].append(fact)
            refs.append([group, fact_ids[identity]])
        context["fact_refs"] = refs
    packet["stats_sources"] = sources
    packet["stats_fact_groups"] = groups
    return packet


def pack_dense(packet, pack):
    """Factor source records and field labels; preserve every scalar exactly."""
    value = pack(_factor_stats(packet))

    def shorten(item):
        if isinstance(item, dict):
            if set(item) == {"$text"}:
                return "~t:" + item["$text"]
            if set(item) == {"$url"}:
                return "~u:" + item["$url"][0] + ":" + item["$url"][1]
            if set(item) == {"$results"}:
                return "~r:" + item["$results"]
            return {key: shorten(child) for key, child in item.items()}
        if isinstance(item, list):
            return [shorten(child) for child in item]
        return "~s:" + item if isinstance(item, str) and item.startswith("~") else item

    value = shorten(value)
    counts = Counter()

    def count_text(item):
        if isinstance(item, dict):
            for key, child in item.items():
                if key not in {"columns", "shared_text", "table_encoding"}:
                    count_text(child)
        elif isinstance(item, list):
            for child in item:
                count_text(child)
        elif isinstance(item, str) and len(item) > 12 and not item.startswith("~"):
            counts[item] += 1

    count_text(value)
    texts = {text: str(index) for index, (text, count) in enumerate(counts.items())
             if count >= 2 and (len(text) - 10) * count > len(text) + 14}

    def text_refs(item):
        if isinstance(item, dict):
            return {key: child if key in {"columns", "shared_text", "table_encoding"}
                    else text_refs(child) for key, child in item.items()}
        if isinstance(item, list):
            return [text_refs(child) for child in item]
        return "~x:" + texts[item] if isinstance(item, str) and item in texts else item

    value = text_refs(value)
    value["shared_values"] = {symbol: text for text, symbol in texts.items()}
    fields = Counter()

    def count_fields(item):
        if isinstance(item, dict):
            for key, child in item.items():
                fields.update(key.split("."))
                if key == "columns":
                    for name in child:
                        fields.update(name.split("."))
                else:
                    count_fields(child)
        elif isinstance(item, list):
            for child in item:
                count_fields(child)

    count_fields(value)
    names = {name: "^" + str(index) for index, (name, count) in enumerate(sorted(
        fields.items(), key=lambda pair: -(len(pair[0]) - 3) * pair[1]))
        if len(name) > 5 and count > 1 and (len(name) - 4) * count > len(name) + 8}
    # Existing caret-prefixed fields cannot be mistaken for dictionary aliases.
    if any(name.startswith("^") for name in fields):
        return pack(packet)

    def field_name(name):
        return ".".join(names.get(part, part) for part in name.split("."))

    def field_refs(item):
        if isinstance(item, dict):
            return {field_name(key): [field_name(name) for name in child] if key == "columns"
                    else field_refs(child) for key, child in item.items()}
        if isinstance(item, list):
            return [field_refs(child) for child in item]
        return item

    value = field_refs(value)
    value["field_names"] = {symbol: name for name, symbol in names.items()}
    value["dense_encoding"] = (
        "DICTIONARIES_V1: In object keys and table column paths, replace each ^number segment "
        "using field_names. Values ~t:id use shared_text[id]; ~x:id use shared_values[id]; "
        "~u:id:suffix uses shared_url_prefixes[id]+suffix; ~r:WDL is newest-first results; "
        "~s:text is literal text. Then apply table_encoding. Stats source_ref is a zero-based "
        "stats_sources index. Each fixture's fact_refs [group,row] selects stats_fact_groups[group].facts[row], "
        "inheriting that group's category/status. All rows, figures, dates and sources are unchanged."
    )
    return value


def restore_dense(value):
    names = value["field_names"]

    def fields(item):
        if isinstance(item, dict):
            output = {}
            for key, child in item.items():
                if key in {"field_names", "dense_encoding"}:
                    continue
                key = ".".join(names.get(part, part) for part in key.split("."))
                output[key] = ([".".join(names.get(part, part) for part in name.split("."))
                                for name in child] if key == "columns" else fields(child))
            return output
        return [fields(child) for child in item] if isinstance(item, list) else item

    value = fields(value)
    texts = value.get("shared_values", {})

    def strings(item):
        if isinstance(item, dict):
            return {key: strings(child) for key, child in item.items() if key != "shared_values"}
        if isinstance(item, list):
            return [strings(child) for child in item]
        if not isinstance(item, str):
            return item
        if item.startswith("~s:"):
            return item[3:]
        if item.startswith("~t:"):
            return {"$text": item[3:]}
        if item.startswith("~x:"):
            return texts[item[3:]]
        if item.startswith("~r:"):
            return {"$results": item[3:]}
        if item.startswith("~u:"):
            return {"$url": item[3:].split(":", 1)}
        return item

    return strings(value)


def restore_stats(packet):
    sources = packet.pop("stats_sources")
    groups = packet.pop("stats_fact_groups")
    for fixture in packet.get("fixtures", []):
        context = fixture.get("the_stats_api", {})
        mapping = context.get("mapping", {})
        if "source_ref" in mapping:
            mapping["source"] = deepcopy(sources[mapping.pop("source_ref")])
        if "fact_refs" not in context:
            continue
        facts = []
        for group_index, row_index in context.pop("fact_refs"):
            group = groups[group_index]
            fact = deepcopy(group["facts"][row_index])
            fact.update(category=group["category"], status=group["status"])
            if "source_ref" in fact:
                fact["source"] = deepcopy(sources[fact.pop("source_ref")])
            facts.append(fact)
        context["facts"] = facts
    return packet
