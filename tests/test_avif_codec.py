import importlib.util
from collections.abc import Callable
from typing import cast

import pytest

from catalog import PLUGINS_DIR

BestFit = Callable[[Callable[[int], bytes], int], "tuple[int, bytes] | None"]


def load_best_fit() -> BestFit:
    path = PLUGINS_DIR / "compress-performer-covers" / "avif_codec.py"
    spec = importlib.util.spec_from_file_location("avif_codec", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return cast(BestFit, module.best_fit)


best_fit = load_best_fit()
TARGET = 100 * 1024


@pytest.mark.parametrize("full_size", [80_000, 120_000, 250_000, 600_000, 2_000_000])
@pytest.mark.parametrize("ratio", [0.9, 0.94, 0.97])
def test_finds_the_lowest_crf_that_fits_in_few_encodes(full_size: int, ratio: float):
    calls: list[int] = []

    def encode(crf: int) -> bytes:
        calls.append(crf)
        return bytes(round(full_size * ratio**crf))

    fit = best_fit(encode, TARGET)

    fitting = [crf for crf in range(26) if round(full_size * ratio**crf) <= TARGET]
    assert (fit[0] if fit else None) == (min(fitting) if fitting else None)
    assert len(calls) <= 4
