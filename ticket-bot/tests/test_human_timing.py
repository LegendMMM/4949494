from __future__ import annotations

import random

from ticket_bot.human import click_delay, scroll_pattern, think_delay, typing_delays


class StubRandom:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def random(self) -> float:
        self.calls.append("random")
        return 0.5

    def uniform(self, a: float, b: float) -> float:
        self.calls.append(f"uniform:{a}:{b}")
        return (a + b) / 2

    def gauss(self, mu: float, sigma: float) -> float:
        self.calls.append(f"gauss:{mu}:{sigma}")
        return mu

    def lognormvariate(self, mu: float, sigma: float) -> float:
        self.calls.append(f"lognorm:{mu}:{sigma}")
        return 1.23

    def randint(self, a: int, b: int) -> int:
        self.calls.append(f"randint:{a}:{b}")
        return a


def test_think_delay_uses_rng():
    rng = StubRandom()
    assert think_delay(rng) == 1.23
    assert rng.calls == ["lognorm:-0.2231435513142097:0.4"]


def test_click_delay_has_floor():
    rng = StubRandom()
    assert click_delay(rng) == 0.2


def test_scroll_pattern_is_shape_stable():
    rng = StubRandom()
    pattern = scroll_pattern(rng)
    assert len(pattern) >= 3
    assert all("dy" in step and "pause_ms" in step for step in pattern)


def test_typing_delays_length_matches_text():
    rng = StubRandom()
    delays = typing_delays("Tea time!", rng)
    assert len(delays) == len("Tea time!")
    assert all(delay >= 0.01 for delay in delays)


def test_typing_delays_are_deterministic_with_real_seed():
    rng = random.Random(0)
    delays_1 = typing_delays("hello", rng)
    rng = random.Random(0)
    delays_2 = typing_delays("hello", rng)
    assert delays_1 == delays_2
