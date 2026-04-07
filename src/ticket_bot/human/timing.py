"""Human-like timing helpers.

The helpers accept an optional RNG object that implements the subset of the
``random.Random`` API used here. This keeps the functions easy to test with a
deterministic random source.
"""

from __future__ import annotations

from dataclasses import dataclass
import math
import random
from typing import Protocol


class _RandomLike(Protocol):
    def random(self) -> float: ...

    def uniform(self, a: float, b: float) -> float: ...

    def gauss(self, mu: float, sigma: float) -> float: ...

    def lognormvariate(self, mu: float, sigma: float) -> float: ...

    def randint(self, a: int, b: int) -> int: ...


def _rng(rng: _RandomLike | None = None) -> _RandomLike:
    return rng if rng is not None else random.Random()


def jitter(value: float, spread: float = 0.05, rng: _RandomLike | None = None) -> float:
    """Apply bounded symmetric jitter around ``value``.

    The helper is intentionally tiny so tests can patch the RNG and get stable
    output.
    """

    generator = _rng(rng)
    return max(0.0, value + generator.uniform(-spread, spread))


def think_delay(rng: _RandomLike | None = None) -> float:
    """Return a human-like thinking delay in seconds."""

    generator = _rng(rng)
    # Median around 0.8s with a right tail for occasional longer pauses.
    return max(0.05, generator.lognormvariate(math.log(0.8), 0.4))


def click_delay(rng: _RandomLike | None = None) -> float:
    """Return a short reaction delay before clicking."""

    generator = _rng(rng)
    return max(0.05, generator.gauss(0.2, 0.05))


def scroll_pattern(rng: _RandomLike | None = None) -> list[dict[str, float | int]]:
    """Generate a small, human-like scrolling pattern.

    Each step is a dictionary so the caller can map it to DOM scroll events or
    CDP input. The values are intentionally simple:

    - ``dy``: scroll amount
    - ``pause_ms``: pause before the next step
    """

    generator = _rng(rng)
    steps: list[dict[str, float | int]] = []
    total = generator.randint(3, 6)
    current = generator.randint(180, 420)

    for index in range(total):
        if index == 0:
            dy = current
        else:
            dy = int(round(current * generator.uniform(0.55, 0.9)))
        pause = int(round(generator.uniform(45, 220)))
        steps.append({"dy": dy, "pause_ms": pause})
        current = max(80, int(round(current * generator.uniform(0.6, 0.88))))

    if total >= 4 and generator.random() < 0.6:
        steps.append({"dy": -int(round(generator.uniform(20, 60))), "pause_ms": int(round(generator.uniform(60, 180)))})

    return steps


def typing_delays(text: str, rng: _RandomLike | None = None) -> list[float]:
    """Generate a per-character typing delay profile.

    The output length matches ``text`` exactly. The pattern is human-ish, not
    a biometric model:

    - short bursts for common digraphs
    - slightly longer pauses at punctuation and whitespace
    - occasional hesitation on the first character
    """

    generator = _rng(rng)
    delays: list[float] = []
    lowered = text.lower()

    for index, char in enumerate(text):
        base = generator.gauss(0.075, 0.025)
        if char.isspace():
            base += 0.05
        elif char in ",.;:!?":
            base += 0.09

        if index > 0:
            pair = lowered[index - 1 : index + 1]
            if pair in {"th", "he", "er", "in", "ng", "ed", "an", "re", "to"}:
                base *= 0.72

        if index == 0 and text:
            base += generator.uniform(0.02, 0.12)

        if generator.random() < 0.04:
            base += generator.uniform(0.12, 0.35)

        delays.append(max(0.01, base))

    return delays
