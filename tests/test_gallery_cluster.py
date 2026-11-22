# pyright: reportAny=false, reportUnusedCallResult=false
from __future__ import annotations

import importlib.util
import sys
from types import ModuleType

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
    paths: tuple[str, ...] = ("/data/img/a.avif",),
    gallery_ids: tuple[str, ...] = (),
    tag_ids: tuple[str, ...] = ("99",),
) -> object:
    return stash_api.ImageItem(
        id=image_id,
        title=title,
        code=code,
        date=date,
        details=details,
        organized=organized,
        created_at=created_at,
        paths=paths,
        gallery_ids=gallery_ids,
        tag_ids=tag_ids,
    )


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


def test_parse_config_reads_tags_and_defaults_threshold():
    cfg = cluster.parse_config({"source_tag": " Grouped ", "gallery_tag": "Batches"})
    assert cfg.source_tag == "Grouped"
    assert cfg.gallery_tag == "Batches"
    assert cfg.similarity_threshold == cluster.DEFAULT_SIMILARITY_THRESHOLD

    assert cluster.parse_config({"similarity_threshold": "0.9"}).similarity_threshold == 0.9
    assert cluster.parse_config({"similarity_threshold": "1"}).similarity_threshold == 1.0

    # Stash renders an unset setting as 0, so 0 must fall back to the default.
    assert cluster.parse_config({"similarity_threshold": 0}).similarity_threshold == (
        cluster.DEFAULT_SIMILARITY_THRESHOLD
    )

    with pytest.raises(ValueError, match="similarity_threshold"):
        cluster.parse_config({"similarity_threshold": "1.5"})
    with pytest.raises(ValueError, match="similarity_threshold"):
        cluster.parse_config({"similarity_threshold": "abc"})


def test_select_candidates_requires_source_tag_description_and_ungrouped():
    tagged = make_image("1", details="A portrait under warm sunlight")
    untagged = make_image("2", tag_ids=("7",))
    organized = make_image("3", organized=True)
    no_details = make_image("4", details="")

    selected = cluster.select_candidates([tagged, untagged, organized, no_details], "99")
    assert [img.id for img in selected] == ["1"]


def test_derive_gallery_metadata_uses_earliest_title_and_latest_date():
    early = make_image(
        "10",
        title="2025-09-13 17:34",
        code="1757756051000",
        date="2025-09-13",
    )
    late = make_image(
        "20",
        title="2026-02-04 15:16",
        code="1770189360000",
        date="2026-02-04",
    )
    assert cluster.image_sort_key(early) < cluster.image_sort_key(late)

    title, date, cover = cluster.derive_gallery_metadata([late, early])
    assert title == "2025-09-13 17:34"
    assert date == "2026-02-04"
    assert cover == "10"


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
        tag_id="99",
        threshold=0.85,
        dry_run=True,
    )
    assert stats == {"evaluated": 3, "joined": 1, "created": 2, "updated": 1}


def test_cleanup_empty_galleries_respects_organized():
    g_empty = stash_api.GalleryItem(
        id="1",
        title="2026-01-01 10:00",
        date="2026-01-01",
        details="image description",
        organized=False,
        image_count=0,
        cover_id=None,
        tag_ids=("99",),
    )
    g_locked = stash_api.GalleryItem(
        id="2",
        title="2026-01-01 10:00",
        date="2026-01-01",
        details="image description",
        organized=True,
        image_count=0,
        cover_id=None,
        tag_ids=("99",),
    )
    assert cluster._cleanup_empty_galleries(None, [g_empty, g_locked], dry_run=True) == 1
