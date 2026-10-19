"""V20 dice: d10 pools against a difficulty, 1s cancel successes, botches."""
from __future__ import annotations

import random
from dataclasses import dataclass, asdict

_rng = random.SystemRandom()


@dataclass
class RollResult:
    pool: int
    difficulty: int
    dice: list[int]
    raw_successes: int
    ones: int
    successes: int
    botch: bool
    specialty: bool
    willpower: bool

    def to_dict(self) -> dict:
        return asdict(self)

    def summary(self) -> str:
        if self.botch:
            return f"BOTCH ({self.ones} one{'s' if self.ones != 1 else ''}, no successes)"
        if self.successes <= 0:
            return "failure (0 net successes)"
        label = {1: "marginal", 2: "moderate", 3: "complete", 4: "exceptional"}.get(
            self.successes, "phenomenal"
        )
        return f"{self.successes} success{'es' if self.successes != 1 else ''} ({label})"


def roll(pool: int, difficulty: int = 6, specialty: bool = False, willpower: bool = False) -> RollResult:
    """Roll a V20 dice pool.

    - Each die >= difficulty is a success.
    - With a specialty, each 10 counts as two successes.
    - Each 1 cancels one success.
    - Botch: no successes at all (before cancellation) and at least one 1.
    - Spending Willpower adds one automatic success that cannot be cancelled,
      and a roll with Willpower spent cannot botch.
    """
    pool = max(0, min(int(pool), 30))
    difficulty = max(2, min(int(difficulty), 10))
    dice = [_rng.randint(1, 10) for _ in range(pool)]
    raw = sum(1 for d in dice if d >= difficulty)
    if specialty:
        raw += sum(1 for d in dice if d == 10)
    ones = dice.count(1)
    botch = raw == 0 and ones > 0 and not willpower
    net = max(0, raw - ones)
    if willpower:
        net += 1
    return RollResult(
        pool=pool,
        difficulty=difficulty,
        dice=dice,
        raw_successes=raw,
        ones=ones,
        successes=net,
        botch=botch,
        specialty=specialty,
        willpower=willpower,
    )
