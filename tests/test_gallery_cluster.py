# pyright: reportAny=false, reportUnusedCallResult=false
from __future__ import annotations

import importlib.util
import sys
from types import ModuleType
from typing import Protocol

import pytest

from catalog import PLUGINS_DIR

PLUGIN = PLUGINS_DIR / "gallery-cluster"


def load(name: str) -> ModuleType:
    sys.path.insert(0, str(PLUGIN))
    try:
        spec = importlib.util.spec_from_file_location(
            f"gallery_cluster_{name}", PLUGIN / f"{name}.py"
        )
        assert spec and spec.loader
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        return module
    finally:
        sys.path.remove(str(PLUGIN))


similarity = load("similarity")
stash_api = load("stash_api")
cluster = load("cluster")


def make_image(
    image_id: str,
    *,
    title: str = "",
    code: str = "",
    date: str = "",
    details: str = "A portrait under warm sunlight",
    organized: bool = False,
    created_at: str = "2026-01-01T00:00:00Z",
    rating100: int | None = None,
    studio_id: str | None = None,
    paths: tuple[str, ...] = ("/data/img/a.avif",),
    gallery_ids: tuple[str, ...] = (),
    tag_ids: tuple[str, ...] = ("99",),
    performer_ids: tuple[str, ...] = (),
) -> object:
    return stash_api.ImageItem(
        id=image_id,
        title=title,
        code=code,
        date=date,
        details=details,
        organized=organized,
        created_at=created_at,
        rating100=rating100,
        studio_id=studio_id,
        paths=paths,
        gallery_ids=gallery_ids,
        tag_ids=tag_ids,
        performer_ids=performer_ids,
    )


def make_settings(**overrides: object) -> object:
    values: dict[str, object] = {
        "gallery_tag_id": "99",
        "source_tag_id": "99",
        "similarity_threshold": cluster.DEFAULT_SIMILARITY_THRESHOLD,
        "rating_step": 20,
    }
    values.update(overrides)
    return cluster.ClusterSettings(**values)


def test_normalize_description_strips_punctuation_whitespace_and_case():
    assert similarity.normalize_description("Hello, World!\u200b  测试。") == "helloworld测试"


def test_description_matcher_exact_and_fuzzy():
    matcher = similarity.DescriptionMatcher(threshold=0.85)
    matcher.add("g1", "Medium shot, eye-level, man sitting by the pool in warm sunlight.")
    matcher.add("g2", "Studio portrait of a cat wearing sunglasses.")

    assert matcher.find_best("Medium shot eye-level man sitting by the pool in warm sunlight") == (
        "g1",
        1.0,
    )
    fuzzy = matcher.find_best(
        "Medium shot, eye-level, man sitting by the pool in bright warm sunlight."
    )
    assert fuzzy is not None
    assert fuzzy[0] == "g1"
    assert fuzzy[1] >= 0.85
    assert matcher.find_best("Completely unrelated landscape with mountains and snow.") is None


PROMPT = (
    "A quiet portrait of a man in a linen shirt, standing by a stone wall at dusk, "
    "holding a camera, with soft rim light and shallow depth of field, shot on 35mm film."
)


def test_matcher_joins_a_prompt_that_dropped_a_clause():
    trimmed = PROMPT.replace("standing by a stone wall at dusk, ", "")
    matcher = similarity.DescriptionMatcher(threshold=0.85)
    matcher.add("g1", PROMPT)

    match = matcher.find_best(trimmed)
    assert match is not None
    assert match[0] == "g1"
    assert 0.65 < match[1] < 0.85


def test_matcher_does_not_let_a_long_prompt_swallow_a_short_one():
    matcher = similarity.DescriptionMatcher(threshold=0.85)
    matcher.add("g1", PROMPT)

    assert matcher.find_best("A quiet portrait of a man") is None


def test_parse_config_reads_tags_and_defaults_threshold():
    cfg = cluster.parse_config({"source_tag": " Grouped ", "gallery_tag": "Batches"}, {})
    assert cfg.source_tag == "Grouped"
    assert cfg.gallery_tag == "Batches"
    assert cfg.similarity_threshold == cluster.DEFAULT_SIMILARITY_THRESHOLD
    assert cfg.rating_step == 20

    def threshold(settings: dict[str, object]) -> float:
        return cluster.parse_config(settings, {}).similarity_threshold

    assert threshold({"similarity_threshold": "0.9"}) == 0.9
    assert threshold({"similarity_threshold": "1"}) == 1.0

    # Stash renders an unset setting as 0, so 0 must fall back to the default.
    assert threshold({"similarity_threshold": 0}) == cluster.DEFAULT_SIMILARITY_THRESHOLD

    with pytest.raises(ValueError, match="similarity_threshold"):
        threshold({"similarity_threshold": "1.5"})
    with pytest.raises(ValueError, match="similarity_threshold"):
        threshold({"similarity_threshold": "abc"})


def test_parse_rating_step_follows_the_ui_rating_system():
    def step(options: object) -> int:
        return cluster.parse_rating_step({"ratingSystemOptions": options})

    assert step(None) == 20
    assert step({"type": "stars", "starPrecision": "full"}) == 20
    assert step({"type": "stars", "starPrecision": "half"}) == 10
    assert step({"type": "stars", "starPrecision": "quarter"}) == 5
    assert step({"type": "decimal"}) == 10
    assert step({"type": "percent"}) == 1
    assert step({"type": "unknown"}) == 20


def test_average_rating100_snaps_to_the_rating_step():
    rated = [make_image("1", rating100=60), make_image("2", rating100=40)]
    assert cluster.average_rating100(rated, 20) == 60
    assert cluster.average_rating100([make_image("1", rating100=90)], 20) == 100
    assert cluster.average_rating100([make_image("1", rating100=25)], 10) == 30
    assert cluster.average_rating100([make_image("1", rating100=55)], 1) == 55
    assert cluster.average_rating100([make_image("1"), make_image("2")], 20) == 0

    # Unrated images are ignored instead of counting as zero.
    unrated = [make_image("1", rating100=80), make_image("2")]
    assert cluster.average_rating100(unrated, 20) == 80


def test_select_candidates_requires_source_tag_description_and_ungrouped():
    tagged = make_image("1", details="A portrait under warm sunlight")
    untagged = make_image("2", tag_ids=("7",))
    organized = make_image("3", organized=True)
    no_details = make_image("4", details="")

    selected = cluster.select_candidates([tagged, untagged, organized, no_details], "99")
    assert [img.id for img in selected] == ["1"]


def test_split_tagged_members_drops_images_that_lost_the_source_tag():
    kept = make_image("1", tag_ids=("99", "5"))
    dropped = make_image("2", tag_ids=("5",))

    members, untagged = cluster.split_tagged_members([kept, dropped], "99")
    assert [image.id for image in members] == ["1"]
    assert untagged == ["2"]


def test_derive_gallery_metadata_uses_latest_title_and_earliest_date():
    early = make_image(
        "10",
        title="2025-09-13 17:34",
        code="1757756051000",
        date="2025-09-13",
        details="Early prompt variant",
        rating100=20,
        studio_id="5",
        performer_ids=("7",),
    )
    late = make_image(
        "20",
        title="2026-02-04 15:16",
        code="1770189360000",
        date="2026-02-04",
        details="Latest prompt variant",
        rating100=40,
        studio_id="5",
        performer_ids=("7", "8"),
    )
    assert cluster.image_sort_key(early) < cluster.image_sort_key(late)

    metadata = cluster.derive_gallery_metadata([late, early], rating_step=20)
    assert metadata.title == "2026-02-04 15:16"
    assert metadata.date == "2025-09-13"
    assert metadata.details == "Latest prompt variant"
    assert metadata.cover_image_id == "20"
    assert metadata.performer_ids == ("7", "8")
    assert metadata.studio_id == "5"
    assert metadata.rating100 == 40


def test_common_studio_id_requires_every_image_to_agree():
    assert cluster.common_studio_id([make_image("1", studio_id="5")]) == "5"
    assert (
        cluster.common_studio_id([make_image("1", studio_id="5"), make_image("2", studio_id="5")])
        == "5"
    )
    assert (
        cluster.common_studio_id([make_image("1", studio_id="5"), make_image("2", studio_id="6")])
        is None
    )
    assert cluster.common_studio_id([make_image("1"), make_image("2")]) is None


def test_assign_images_dry_run_creates_single_and_merges_similar():
    img1 = make_image(
        "1",
        title="2026-01-01 10:00",
        code="1767232800000",
        date="2026-01-01",
        details="Close-up portrait of a bearded man in a dim jazz bar with warm tungsten light.",
    )
    img2 = make_image(
        "2",
        title="2026-01-02 12:00",
        code="1767326400000",
        date="2026-01-02",
        details="Close-up portrait of a bearded man in a dim jazz bar with warm tungsten lighting.",
    )
    img3 = make_image(
        "3",
        title="2026-01-03 14:00",
        code="1767420000000",
        date="2026-01-03",
        details="Snowy mountain peak at sunrise with pine trees in the foreground.",
    )
    locked = make_image(
        "4",
        title="2026-01-04 16:00",
        code="1767513600000",
        date="2026-01-04",
        details="Snowy mountain peak at sunrise with pine trees in the foreground.",
        organized=True,
    )

    stats = cluster._assign_images_to_clusters(
        client=None,
        candidates=cluster.select_candidates([img1, img2, img3, locked], "99"),
        managed_galleries=[],
        settings=make_settings(),
        dry_run=True,
    )
    assert stats == {"evaluated": 3, "joined": 1, "created": 2, "updated": 1}


def test_cluster_groups_ignores_existing_gallery_membership():
    already_grouped = make_image("1", details=PROMPT, gallery_ids=("g-old",))
    elsewhere = make_image("2", details=PROMPT, gallery_ids=("g-other",))
    unrelated = make_image("3", details="A snowy mountain peak at sunrise.")

    groups = cluster._cluster_groups([already_grouped, elsewhere, unrelated], make_settings())

    assert [sorted(img.id for img in group) for group in groups] == [["1", "2"], ["3"]]


def test_cleanup_empty_galleries_respects_organized():
    g_empty = stash_api.GalleryItem(
        id="1",
        title="2026-01-01 10:00",
        date="2026-01-01",
        details="image description",
        organized=False,
        image_count=0,
        rating100=None,
        studio_id=None,
        cover_id=None,
        tag_ids=("99",),
        performer_ids=(),
    )
    g_locked = stash_api.GalleryItem(
        id="2",
        title="2026-01-01 10:00",
        date="2026-01-01",
        details="image description",
        organized=True,
        image_count=0,
        rating100=None,
        studio_id=None,
        cover_id=None,
        tag_ids=("99",),
        performer_ids=(),
    )
    assert cluster._cleanup_empty_galleries(None, [g_empty, g_locked], dry_run=True) == 1


class DocumentedOperation(Protocol):
    document: str


class FakeClient:
    """Serves the gallery members and records the mutations the sync pass issues."""

    def __init__(self, members: list[dict[str, object]]) -> None:
        self.members: list[dict[str, object]] = members
        self.calls: list[tuple[str, dict[str, object]]] = []

    def execute(
        self, operation: DocumentedOperation, variables: dict[str, object]
    ) -> dict[str, object]:
        document = operation.document
        self.calls.append((document, variables))
        if "findImages" in document:
            return {"findImages": {"images": self.members}}
        return {}

    def called(self, mutation: str) -> list[dict[str, object]]:
        return [variables for document, variables in self.calls if mutation in document]


def make_raw_member(
    image_id: str,
    *,
    details: str,
    tags: tuple[str, ...],
    gallery_id: str,
    created_at: str = "2026-01-01T00:00:00Z",
) -> dict[str, object]:
    return {
        "id": image_id,
        "title": image_id,
        "code": "",
        "date": "",
        "details": details,
        "organized": False,
        "created_at": created_at,
        "rating100": None,
        "visual_files": [],
        "galleries": [{"id": gallery_id}],
        "tags": [{"id": tag} for tag in tags],
        "performers": [],
        "studio": None,
    }


def make_gallery(gallery_id: str = "g1", *, image_count: int = 1) -> object:
    return stash_api.GalleryItem(
        id=gallery_id,
        title="2026-01-01 10:00",
        date="2026-01-01",
        details="image description",
        organized=False,
        image_count=image_count,
        rating100=None,
        studio_id=None,
        cover_id="1",
        tag_ids=("99",),
        performer_ids=(),
    )


def test_find_images_in_galleries_chunks_the_gallery_ids():
    client = FakeClient([make_raw_member("1", details="Prompt", tags=("99",), gallery_id="g1")])

    images = stash_api.find_images_in_galleries(client, [str(i) for i in range(1200)])

    assert [image.id for image in images] == ["1"]
    # 1200 gallery IDs are asked for in chunks of 500, so nothing loads Stash with one
    # oversized query.
    assert len(client.called("findImages")) == 3


def test_sync_removes_members_that_lost_the_source_tag():
    client = FakeClient(
        [
            make_raw_member("1", details="Kept prompt", tags=("99",), gallery_id="g1"),
            make_raw_member("2", details="Dropped prompt", tags=("7",), gallery_id="g1"),
        ]
    )

    updated, removed, destroyed = cluster._sync_all_managed_galleries(
        client, [make_gallery(image_count=2)], make_settings(), dry_run=False
    )

    assert removed == 1
    assert destroyed == 0
    assert updated == 1
    removals = client.called("removeGalleryImages")
    assert [call["input"] for call in removals] == [{"gallery_id": "g1", "image_ids": ["2"]}]


def test_sync_destroys_a_gallery_whose_members_all_lost_the_source_tag():
    client = FakeClient(
        [make_raw_member("1", details="Dropped prompt", tags=("7",), gallery_id="g1")]
    )

    updated, removed, destroyed = cluster._sync_all_managed_galleries(
        client, [make_gallery(image_count=1)], make_settings(), dry_run=False
    )

    assert (updated, removed, destroyed) == (0, 1, 1)
    assert len(client.called("removeGalleryImages")) == 1
    assert len(client.called("galleryDestroy")) == 1


def test_apply_groups_keeps_the_gallery_that_already_holds_the_images():
    client = FakeClient([make_raw_member("1", details="Prompt", tags=("99",), gallery_id="g1")])

    stats = cluster._apply_groups(
        client, [[make_image("1")]], [make_gallery()], make_settings(), dry_run=False
    )

    assert stats["reused"] == 1
    assert stats["created"] == 0
    assert client.called("addGalleryImages") == []
    assert client.called("removeGalleryImages") == []
    assert client.called("galleryDestroy") == []


def test_apply_groups_moves_images_into_the_reused_gallery():
    client = FakeClient([make_raw_member("1", details="Prompt", tags=("99",), gallery_id="g1")])

    cluster._apply_groups(
        client,
        [[make_image("1"), make_image("2")]],
        [make_gallery()],
        make_settings(),
        dry_run=False,
    )

    assert [call["input"] for call in client.called("addGalleryImages")] == [
        {"gallery_id": "g1", "image_ids": ["2"]}
    ]


def test_apply_groups_deletes_galleries_no_group_wants():
    client = FakeClient(
        [
            make_raw_member("1", details="Prompt", tags=("99",), gallery_id="g1"),
            make_raw_member("2", details="Prompt", tags=("99",), gallery_id="g2"),
        ]
    )

    stats = cluster._apply_groups(
        client,
        [[make_image("1")]],
        [make_gallery(), make_gallery("g2")],
        make_settings(),
        dry_run=False,
    )

    assert stats["destroyed"] == 1
    assert [call["input"] for call in client.called("galleryDestroy")] == [
        {
            "ids": ["g2"],
            "delete_file": False,
            "delete_generated": False,
            "destroy_file_entry": False,
        }
    ]
