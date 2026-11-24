"""Weighted pp maths: how many new plays of X pp close the gap to the threshold."""

TARGETS = (500, 550, 600)
CAP = 300


def weighted(pps: list[float]) -> float:
    return sum(p * 0.95 ** i for i, p in enumerate(pps))


def plays_needed(best_pps: list[float], total_pp: float, threshold: float,
                 new_pp: float, cap: int = CAP) -> int | None:
    """Number of distinct new plays of `new_pp` needed to reach threshold, else None.

    The residual (total minus weighted top-100 sum) is held constant. It includes
    bonus pp, contributions below the top 100, and any snapshot mismatch. Each
    new play is inserted in sorted position and the list is truncated to 100.
    """
    pps = sorted(best_pps, reverse=True)[:100]
    residual = total_pp - weighted(pps)
    cur = total_pp
    for n in range(cap + 1):
        if cur >= threshold:
            return n
        pps = sorted(pps + [new_pp], reverse=True)[:100]
        new = residual + weighted(pps)
        if new <= cur + 1e-9:
            return None  # play too low to change anything
        cur = new
    return None


def table(best_pps, total_pp, threshold, targets=TARGETS) -> list[dict]:
    return [{"play_pp": t, "plays_needed": plays_needed(best_pps, total_pp, threshold, t)}
            for t in targets]
