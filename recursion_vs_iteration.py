"""FIFO allocation written twice: as a loop and as recursion (Unit IV).

Same inputs, same answers — the point is the comparison. The course rule
of thumb says recursion fits nested structures of unknown depth, while
iteration fits flat sequences. A list of lots is as flat as data gets,
which is why fermentario/core.py allocates with a loop in production;
this file exists to make that design choice concrete and defensible.

Run me (no server, no data files needed):  python recursion_vs_iteration.py
"""


def allocate_fifo_iterative(lots, requested):
    """Assign `requested` bottles to the oldest lots first.

    lots: oldest-first list of (lot_number, available) pairs.
    Returns (allocation, missing): the (lot, taken) picks, plus how many
    bottles could not be filled (0 means fully satisfied).
    """
    allocation = []
    remaining = requested
    for lot_number, available in lots:
        if remaining == 0:      # already satisfied: no need to look further
            break
        taken = min(available, remaining)
        if taken:               # a drained lot contributes nothing
            allocation.append((lot_number, taken))
            remaining -= taken
    return allocation, remaining


def allocate_fifo_recursive(lots, requested):
    """The same contract, phrased recursively: "take what you can from the
    oldest lot, then solve the smaller problem that remains."
    """
    # Base cases — the conditions that stop the recursion:
    if requested == 0 or not lots:      # nothing left to fill, or to take from
        return [], requested

    (lot_number, available), remaining_lots = lots[0], lots[1:]
    taken = min(available, requested)

    # Recursive case: the same problem, one lot shorter and `taken` smaller.
    # Each pending call waits on the call stack until its child returns;
    # Python caps that stack near 1000 frames (RecursionError beyond) and
    # does not optimize tail calls — two practical reasons the production
    # version in core.py iterates instead.
    allocation, missing = allocate_fifo_recursive(remaining_lots,
                                                  requested - taken)
    if taken:
        # Prepend so the oldest lot stays first in the final answer.
        allocation = [(lot_number, taken)] + allocation
    return allocation, missing


SAMPLE_LOTS = [("L-001", 100), ("L-002", 80), ("L-003", 50)]

if __name__ == "__main__":
    print(f"Lots (oldest first): {SAMPLE_LOTS}\n")
    for requested in (30, 120, 300):
        result_loop = allocate_fifo_iterative(SAMPLE_LOTS, requested)
        result_rec = allocate_fifo_recursive(SAMPLE_LOTS, requested)
        # Two strategies, one truth: if they ever disagree, stop everything.
        assert result_loop == result_rec, "both strategies must agree"

        allocation, missing = result_loop
        display = ", ".join(f"{lot} ({n})" for lot, n in allocation)
        line = f"request {requested:>3} -> {display or '(nothing)'}"
        if missing:
            line += f"  [{missing} missing: insufficient stock]"
        print(line)
