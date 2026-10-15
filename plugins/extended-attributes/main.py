import re
from dataclasses import dataclass

from stash_api import (
    ENTITIES,
    EntityType,
    Item,
    TagTree,
    bulk_update_tags,
    find_items,
    find_root_tags,
)
from stash_plugin import (
    HookContext,
    PluginInput,
    Progress,
    StashClient,
    as_bool,
    as_list,
    as_object,
    emit_info,
    emit_progress,
    emit_warn,
    run_plugin,
)

ROOT_TAG_NAMES: dict[EntityType, str] = {
    "gallery": "__GALLERY__",
    "group": "__GROUP__",
    "image": "__IMAGE__",
    "scene": "__SCENE__",
    "performer": "__PERFORMER__",
}

MULTI_SELECT_CUSTOM_FIELD = "extended_attributes_multi_select"
WINDOWS_ABSOLUTE_PATH_RE = re.compile(r"^[a-zA-Z]:[\\/]")


@dataclass(frozen=True)
class TagNode:
    id: str
    multi_select: bool
    path_aliases: list[str]
    children: list["TagNode"]
    # The tag itself and every tag below it.
    descendant_ids: frozenset[str]


@dataclass(frozen=True)
class TypeConfig:
    subtypes: list[TagNode]
    subtype_ids: frozenset[str]
    managed_tag_ids: frozenset[str]
    all_nodes: list[TagNode]


@dataclass(frozen=True)
class Rebuild:
    tag_ids: list[str]
    added_branch_ids: list[str]
    kept_branch_ids: list[str]
    changed: bool
    active_subtype_ids: list[str]
    resolved_conflicts: list[dict[str, object]]


def by_id(ids: set[str] | frozenset[str]) -> list[str]:
    return sorted(ids, key=int)


def is_absolute_path_alias(value: str) -> bool:
    path = value.strip()
    return path.startswith(("/", "\\\\", "//")) or bool(WINDOWS_ABSOLUTE_PATH_RE.match(path))


def normalize_path(value: str) -> str:
    normalized = re.sub("/{2,}", "/", value.strip().replace("\\", "/"))
    if len(normalized) > 1:
        normalized = normalized.rstrip("/")
    return normalized.casefold()


def path_matches_alias(path: str, alias: str) -> bool:
    normalized_path = normalize_path(path)
    normalized_alias = normalize_path(alias)
    return normalized_path == normalized_alias or normalized_path.startswith(f"{normalized_alias}/")


def build_tag_node(tag: TagTree) -> TagNode:
    children = [build_tag_node(child) for child in tag.get("children", [])]
    custom_fields = as_object(tag["custom_fields"]) or {}
    return TagNode(
        id=tag["id"],
        multi_select=bool(custom_fields.get(MULTI_SELECT_CUSTOM_FIELD)),
        path_aliases=[alias.strip() for alias in tag["aliases"] if is_absolute_path_alias(alias)],
        children=children,
        descendant_ids=frozenset({tag["id"]}.union(*(child.descendant_ids for child in children))),
    )


def flatten_tag_nodes(nodes: list[TagNode]) -> list[TagNode]:
    return [flat for node in nodes for flat in [node, *flatten_tag_nodes(node.children)]]


def load_type_config(
    roots_by_name: dict[str, TagTree], entity_type: EntityType
) -> TypeConfig | None:
    root_name = ROOT_TAG_NAMES[entity_type]
    root = roots_by_name.get(root_name)
    if not root:
        emit_warn(f"Root tag {root_name} was not found; skipping {entity_type}")
        return None

    subtypes = [build_tag_node(child) for child in root.get("children", [])]
    if not subtypes:
        emit_warn(f"Root tag {root_name} has no subtypes; skipping {entity_type}")
        return None

    all_nodes = flatten_tag_nodes(subtypes)
    emit_info(f"Loaded {len(subtypes)} subtypes for {entity_type}")
    return TypeConfig(
        subtypes=subtypes,
        subtype_ids=frozenset(subtype.id for subtype in subtypes),
        managed_tag_ids=frozenset(node.id for node in all_nodes),
        all_nodes=all_nodes,
    )


def find_path_matched_tag_ids(paths: list[str], nodes: list[TagNode]) -> set[str]:
    return {
        node.id
        for node in nodes
        if any(path_matches_alias(path, alias) for path in paths for alias in node.path_aliases)
    }


def find_selected_leaf_ids(selected_ids: set[str], attribute: TagNode) -> set[str]:
    """The most specific selected values of an attribute: those with no selected tag below."""
    specific_ids = selected_ids & (attribute.descendant_ids - {attribute.id})
    nodes_by_id = {node.id: node for node in flatten_tag_nodes(attribute.children)}
    return {
        tag_id
        for tag_id in specific_ids
        if not (nodes_by_id[tag_id].descendant_ids & specific_ids - {tag_id})
    }


def choose_single_value(value_ids: set[str], preferred_tag_ids: list[str] | None) -> str:
    for tag_id in reversed(preferred_tag_ids or []):
        if tag_id in value_ids:
            return tag_id
    return min(value_ids, key=int)


def rebuild_attr_tag_ids(
    item: Item,
    type_config: TypeConfig,
    preferred_tag_ids: list[str] | None = None,
    strict: bool = True,
) -> Rebuild:
    current_tag_ids = set(item.tag_ids)
    path_matched_ids = find_path_matched_tag_ids(item.paths, type_config.all_nodes)

    # An explicit subtype overrides directory defaults. Each root permits one subtype only.
    explicit_subtype_ids = type_config.subtype_ids & current_tag_ids
    path_subtype_ids = type_config.subtype_ids & path_matched_ids
    candidate_subtype_ids = set(explicit_subtype_ids or path_subtype_ids)
    if not candidate_subtype_ids:
        return Rebuild(by_id(current_tag_ids), [], [], False, [], [])

    active_subtype_id = choose_single_value(candidate_subtype_ids, preferred_tag_ids)
    next_tag_ids = current_tag_ids - (type_config.subtype_ids - {active_subtype_id})
    next_tag_ids.add(active_subtype_id)
    added_branch_ids: list[str] = []
    kept_branch_ids: list[str] = []
    resolved_conflicts: list[dict[str, object]] = []

    if len(candidate_subtype_ids) > 1:
        resolved_conflicts.append(
            {
                "kind": "subtype",
                "kept_subtype_id": active_subtype_id,
                "removed_subtype_ids": by_id(candidate_subtype_ids - {active_subtype_id}),
            }
        )

    active_subtype = next(
        subtype for subtype in type_config.subtypes if subtype.id == active_subtype_id
    )
    if strict:
        next_tag_ids -= type_config.managed_tag_ids - active_subtype.descendant_ids
    next_tag_ids |= path_matched_ids & active_subtype.descendant_ids

    for attribute in active_subtype.children:
        branch_ids = next_tag_ids & attribute.descendant_ids
        value_ids = find_selected_leaf_ids(branch_ids, attribute)
        next_tag_ids -= branch_ids

        if value_ids and attribute.multi_select:
            next_tag_ids |= value_ids
            kept_branch_ids.append(attribute.id)
        elif value_ids:
            kept_value_id = choose_single_value(value_ids, preferred_tag_ids)
            next_tag_ids.add(kept_value_id)
            kept_branch_ids.append(attribute.id)
            if len(value_ids) > 1:
                resolved_conflicts.append(
                    {
                        "kind": "attribute",
                        "attribute_id": attribute.id,
                        "kept_value_id": kept_value_id,
                        "removed_value_ids": by_id(value_ids - {kept_value_id}),
                    }
                )
        else:
            next_tag_ids.add(attribute.id)
            if attribute.id not in branch_ids:
                added_branch_ids.append(attribute.id)

    return Rebuild(
        tag_ids=by_id(next_tag_ids),
        added_branch_ids=added_branch_ids,
        kept_branch_ids=kept_branch_ids,
        changed=current_tag_ids != next_tag_ids,
        active_subtype_ids=[active_subtype_id],
        resolved_conflicts=resolved_conflicts,
    )


def apply_item_updates(
    client: StashClient,
    entity_type: EntityType,
    items: list[Item],
    type_config: TypeConfig,
    dry_run: bool,
    progress: Progress,
    preferred_tag_ids: list[str] | None = None,
) -> dict[str, object]:
    results: list[dict[str, object]] = []
    # Items that end up with the same tags are written together in one bulk update.
    ids_by_tag_ids: dict[tuple[str, ...], list[str]] = {}
    skipped = 0

    for item in items:
        rebuild = rebuild_attr_tag_ids(item, type_config, preferred_tag_ids)
        if not rebuild.active_subtype_ids or not rebuild.changed:
            skipped += 1
        else:
            ids_by_tag_ids.setdefault(tuple(rebuild.tag_ids), []).append(item.id)
            results.append(
                {
                    "id": item.id,
                    "active_subtype_ids": rebuild.active_subtype_ids,
                    "added_branch_ids": rebuild.added_branch_ids,
                    "kept_branch_ids": rebuild.kept_branch_ids,
                    "resolved_conflicts": rebuild.resolved_conflicts,
                    "paths": item.paths,
                    "dry_run": dry_run,
                }
            )
            if dry_run:
                emit_info(
                    f"{entity_type} id={item.id} subtypes={rebuild.active_subtype_ids} "
                    f"added_count={len(rebuild.added_branch_ids)}"
                )
        progress.step()

    if not dry_run:
        for tag_ids, item_ids in ids_by_tag_ids.items():
            bulk_update_tags(client, entity_type, item_ids, list(tag_ids))
            emit_info(f"{entity_type} bulk update items={len(item_ids)} tag_count={len(tag_ids)}")

    return {
        "checked": len(items),
        "updated": len(results),
        "skipped": skipped,
        "results": results,
        "bulk_groups": 0 if dry_run else len(ids_by_tag_ids),
    }


def resolve_hook_item_ids(hook: HookContext) -> list[str]:
    if hook.id is None:
        return []

    # Stash fires one hook per entity in a bulk update, and each input carries the whole batch
    # of ids. Only the first hook processes the batch; the rest return immediately.
    batch_ids = [str(batch_id) for batch_id in as_list((hook.input or {}).get("ids"))]
    if not batch_ids:
        return [hook.id]
    return batch_ids if hook.id == batch_ids[0] else []


def resolve_preferred_tag_ids(hook: HookContext | None) -> list[str] | None:
    if not hook or "tag_ids" not in hook.input_fields:
        return None

    tag_ids = (hook.input or {})["tag_ids"]
    # A bulk update's tag_ids looks like {"ids": [...], "mode": "SET" | "ADD" | "REMOVE"}.
    if bulk := as_object(tag_ids):
        tag_ids = bulk["ids"] if bulk["mode"] != "REMOVE" else None
    return [str(tag_id) for tag_id in as_list(tag_ids)] or None


def parse_entity_type(value: object) -> EntityType | None:
    requested = str(value or "").strip().lower()
    if not requested:
        return None
    for entity_type in ENTITIES:
        if entity_type == requested:
            return entity_type
    raise ValueError(f"Unsupported entity type: {requested}")


def run(plugin_input: PluginInput) -> object:
    args, hook = plugin_input.args, plugin_input.hook
    dry_run = as_bool(args.get("dry_run"), False)
    item_ids = resolve_hook_item_ids(hook) if hook else None
    if hook and not item_ids:
        emit_info("Skipped: this hook is covered by the first hook of the same batch")
        return {"dry_run": dry_run, "skipped_batch_member": True}
    preferred_tag_ids = resolve_preferred_tag_ids(hook)
    requested_entity_type = parse_entity_type(args.get("entity_type"))

    client = StashClient.connect(plugin_input)
    entity_types: list[EntityType] = (
        [requested_entity_type] if requested_entity_type else list(ENTITIES)
    )
    roots_by_name = {tag["name"]: tag for tag in find_root_tags(client, [*ROOT_TAG_NAMES.values()])}
    type_configs = {
        entity_type: config
        for entity_type in entity_types
        if (config := load_type_config(roots_by_name, entity_type)) is not None
    }
    output: dict[str, object] = {"dry_run": dry_run}
    items_by_entity: dict[EntityType, list[Item]] = {}

    for entity_type in entity_types:
        if entity_type not in type_configs:
            output[entity_type] = {
                "checked": 0,
                "updated": 0,
                "skipped": 0,
                "results": [],
                "reason": f"Missing {ROOT_TAG_NAMES[entity_type]} or its subtypes",
            }
            continue
        items = find_items(client, entity_type, item_ids)
        items_by_entity[entity_type] = items
        emit_info(f"Loaded {len(items)} {entity_type} items")

    progress = Progress(total=max(sum(map(len, items_by_entity.values())), 1))
    emit_progress(0.0)
    for entity_type, items in items_by_entity.items():
        output[entity_type] = apply_item_updates(
            client=client,
            entity_type=entity_type,
            items=items,
            type_config=type_configs[entity_type],
            dry_run=dry_run,
            progress=progress,
            preferred_tag_ids=preferred_tag_ids,
        )

    emit_progress(1.0)
    return output


if __name__ == "__main__":
    run_plugin(run)
