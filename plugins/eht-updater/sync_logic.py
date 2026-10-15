import re
from dataclasses import dataclass, field
from pathlib import Path

from ehentai_api import fetch_ehentai
from stash_api import (
    Gallery,
    TagTree,
    UpdatedGallery,
    create_tag,
    find_galleries,
    search_tags,
    update_gallery,
)
from stash_plugin import (
    FAILURES,
    PluginInput,
    StashClient,
    as_bool,
    as_str,
    emit_info,
    emit_progress,
    emit_warn,
)

AUDIENCE_ROOT_TAG_NAME = "Audience"
AUDIENCE_TAG_NAME_GAY = "Gay"
AUDIENCE_TAG_NAME_STRAIGHT = "Straight"
LANGUAGE_ROOT_TAG_NAME = "Language"

GALLERY_DIR_RE = re.compile(r"^(?P<gid>\d+)_(?P<token>[a-f0-9]+)$")


def normalize_tag_text(text: str) -> str:
    return text.strip().lower()


def to_title_case(text: str) -> str:
    return " ".join(part[:1].upper() + part[1:] for part in re.split(r"[\s-]+", text) if part)


@dataclass
class Tag:
    id: str
    name: str
    aliases: list[str]
    children: list["Tag"] = field(default_factory=list)

    @classmethod
    def from_tree(cls, tree: TagTree) -> "Tag":
        children = [cls.from_tree(child) for child in tree.get("children", [])]
        return cls(tree["id"], tree["name"], tree["aliases"], children)

    def names(self) -> list[str]:
        return [normalize_tag_text(name) for name in [self.name, *self.aliases]]

    def matches(self, name: str) -> bool:
        return normalize_tag_text(name) in self.names()


@dataclass
class Language:
    tag: Tag
    subtitle: Tag | None
    # The tags below the language, the subtitle among them.
    descendants: list[Tag]

    @classmethod
    def from_tag(cls, tag: Tag) -> "Language":
        descendants = [Tag(child.id, child.name, child.aliases) for child in tag.children]
        subtitle = next(
            (
                child
                for child in descendants
                if any(name.endswith(" subtitle") for name in child.names())
            ),
            None,
        )
        return cls(tag, subtitle, descendants)


@dataclass
class Taxonomy:
    """The Audience and Language tag trees, which grow as galleries need new tags."""

    client: StashClient
    dry_run: bool
    audience_root: Tag
    language_root: Tag
    languages: list[Language]

    @classmethod
    def load(cls, client: StashClient, dry_run: bool) -> "Taxonomy":
        audience_root = ensure_root_tag(client, AUDIENCE_ROOT_TAG_NAME, dry_run)
        for name in (AUDIENCE_TAG_NAME_GAY, AUDIENCE_TAG_NAME_STRAIGHT):
            _ = ensure_child_tag(client, audience_root, name, dry_run)
        language_root = ensure_root_tag(client, LANGUAGE_ROOT_TAG_NAME, dry_run)
        languages = [Language.from_tag(child) for child in language_root.children]
        return cls(client, dry_run, audience_root, language_root, languages)

    def ensure_child_tag(self, parent: Tag, child_name: str) -> Tag:
        return ensure_child_tag(self.client, parent, child_name, self.dry_run)

    def language_tag_ids(self) -> tuple[set[str], set[str]]:
        """The IDs of every language tag, and of those below a language."""
        descendant_ids = {tag.id for language in self.languages for tag in language.descendants}
        return {language.tag.id for language in self.languages} | descendant_ids, descendant_ids

    def audience_tag_id(self, tags: list[str], logs: list[str]) -> str:
        gay = any(normalize_tag_text(tag).endswith("males only") for tag in tags)
        target_name = AUDIENCE_TAG_NAME_GAY if gay else AUDIENCE_TAG_NAME_STRAIGHT
        logs.append(f"Audience tag resolved to {target_name}")
        return self.ensure_child_tag(self.audience_root, target_name).id

    def language_subtitle_tag_id(self, tags: list[str], logs: list[str]) -> str | None:
        language_name = extract_language_name(tags)
        if not language_name:
            return None

        subtitle_alias = f"{to_title_case(language_name)} Subtitle"
        for language in self.languages:
            if language.subtitle and language.subtitle.matches(subtitle_alias):
                logs.append(f"Matched existing language subtitle tag: {language.subtitle.name}")
                return language.subtitle.id

        language = next((item for item in self.languages if item.tag.matches(language_name)), None)
        if language:
            logs.append(f"Matched existing language parent tag: {language.tag.name}")
        else:
            parent_name = to_title_case(language_name)
            logs.append(f"Creating language parent tag: {parent_name}")
            parent = self.ensure_child_tag(self.language_root, parent_name)
            # The subtitle is created below, so any tag already under the parent is not one.
            language = Language(parent, None, Language.from_tag(parent).descendants)
            self.languages.append(language)

        logs.append(f"Creating language subtitle tag: {subtitle_alias}")
        if self.dry_run:
            return None
        created = create_tag(self.client, subtitle_alias, [language.tag.id])
        language.subtitle = Tag(created["id"], created["name"], created["aliases"])
        language.descendants.append(language.subtitle)
        return created["id"]


def ensure_root_tag(client: StashClient, name: str, dry_run: bool) -> Tag:
    tags = (Tag.from_tree(tag) for tag in search_tags(client, name))
    if existing := next((tag for tag in tags if tag.matches(name)), None):
        return existing
    if dry_run:
        raise RuntimeError(f"Root tag not found and cannot be created in dry-run mode: {name}")
    return Tag.from_tree(create_tag(client, name, []))


def ensure_child_tag(client: StashClient, parent: Tag, child_name: str, dry_run: bool) -> Tag:
    for child in parent.children:
        if child.matches(child_name):
            return child
    if dry_run:
        raise RuntimeError(
            "Child tag not found and cannot be created in dry-run mode: "
            f"{child_name} (parent: {parent.name})"
        )
    child = Tag.from_tree(create_tag(client, child_name, [parent.id]))
    parent.children.append(child)
    return child


def extract_language_name(tags: list[str]) -> str | None:
    language_tag = next((tag for tag in tags if tag.startswith("language:")), None)
    if not language_tag:
        return None
    return normalize_tag_text(language_tag.split(":", 1)[1]) or None


def parse_gallery_dir(path: str | None) -> tuple[int, str] | None:
    match = GALLERY_DIR_RE.match(Path(path).name) if path else None
    return (int(match["gid"]), match["token"]) if match else None


def folder_path(gallery: Gallery) -> str | None:
    return gallery["folder"]["path"] if gallery["folder"] else None


def gallery_log_prefix(gallery: Gallery, gid: int | None = None, token: str | None = None) -> str:
    folder = folder_path(gallery) or "<no-folder>"
    gid_token = f" gid_token={gid}_{token}" if gid is not None and token is not None else ""
    return f"gallery_id={gallery['id'] or '<no-id>'} folder={folder}{gid_token}"


@dataclass(frozen=True)
class Target:
    gallery: Gallery
    gid: int
    token: str

    @property
    def log_prefix(self) -> str:
        return gallery_log_prefix(self.gallery, self.gid, self.token)


def sync_gallery_metadata(taxonomy: Taxonomy, target: Target) -> dict[str, object]:
    gallery, gid, token, dry_run = target.gallery, target.gid, target.token, taxonomy.dry_run
    emit_info(f"{target.log_prefix} Starting metadata sync")
    meta, logs = fetch_ehentai(gid, token)
    language_tag_ids, language_leaf_tag_ids = taxonomy.language_tag_ids()
    audience_tag_ids = {child.id for child in taxonomy.audience_root.children}
    # Audience and language tags are replaced; the tags below a language, like subtitles, stay.
    next_tag_ids = {
        tag["id"]
        for tag in gallery["tags"]
        if tag["id"] not in audience_tag_ids
        and (tag["id"] not in language_tag_ids or tag["id"] in language_leaf_tag_ids)
    }

    next_tag_ids.add(taxonomy.audience_tag_id(meta.tags, logs))
    if language_tag_id := taxonomy.language_subtitle_tag_id(meta.tags, logs):
        next_tag_ids.add(language_tag_id)
        logs.append(f"Language subtitle tag resolved to id={language_tag_id}")
    elif not any(tag.startswith("language:") for tag in meta.tags):
        logs.append("No language tag matched; the gallery will still be marked organized")

    payload: dict[str, object] = {
        "title": meta.title,
        "date": meta.date,
        "rating100": meta.rating100,
        "url": f"https://e-hentai.org/g/{gid}/{token}",
        "organized": True,
        "tag_ids": sorted(next_tag_ids),
    }
    updated: UpdatedGallery | None = None
    if not dry_run:
        updated = update_gallery(taxonomy.client, gallery["id"], payload)
    for message in logs:
        emit_info(f"{target.log_prefix} {message}")
    action = "Prepared dry-run update for" if dry_run else "Updated"
    emit_info(
        f"{target.log_prefix} {action} title={meta.title} date={meta.date} "
        f"rating100={meta.rating100} tags={len(next_tag_ids)}"
    )
    return {
        "id": gallery["id"],
        "path": folder_path(gallery),
        "title": meta.title,
        "dry_run": dry_run,
        "payload": payload,
        "logs": logs,
        "updated": updated,
    }


def select_target_galleries(client: StashClient, plugin_input: PluginInput) -> list[Gallery]:
    args = plugin_input.args
    path_contains = as_str(args.get("path_contains")) or None
    return find_galleries(client, path_contains, as_bool(args.get("skip_organized"), True))


def collect_targets(galleries: list[Gallery]) -> tuple[list[Target], list[dict[str, object]]]:
    targets: list[Target] = []
    skipped: list[dict[str, object]] = []
    for gallery in galleries:
        if parsed := parse_gallery_dir(folder_path(gallery)):
            targets.append(Target(gallery, *parsed))
        else:
            skipped.append(
                {
                    "id": gallery["id"],
                    "path": folder_path(gallery),
                    "reason": "folder name is not gid_token",
                }
            )
    return targets, skipped


def process_targets(
    taxonomy: Taxonomy, targets: list[Target]
) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    results: list[dict[str, object]] = []
    failed: list[dict[str, object]] = []
    if not targets:
        emit_progress(1.0)
        emit_info("No eligible galleries to process")
        return results, failed

    emit_progress(0.0)
    for index, target in enumerate(targets, start=1):
        try:
            results.append(sync_gallery_metadata(taxonomy, target))
        except FAILURES as exc:
            emit_warn(f"{target.log_prefix} Metadata sync failed: {exc}")
            failed.append(
                {
                    "id": target.gallery["id"],
                    "path": folder_path(target.gallery),
                    "reason": str(exc),
                }
            )
        emit_progress(index / len(targets))
    return results, failed
