"""Single-thread pure-Python CPU benchmark for X-gy host-vs-runner comparison.

Fixed work, no I/O: Decimal arithmetic, dict/list churn, string formatting and
function calls -- the shapes app code spends its CPU on.  Prints the MIN of N
repetitions (the least-disturbed run) and every repetition.
"""
import sys
import time
from decimal import Decimal, ROUND_HALF_UP


def work():
    total = Decimal("0")
    rate = Decimal("0.0425") / Decimal("12")
    bal = Decimal("250000.00")
    rows = {}
    for i in range(400000):
        interest = (bal * rate).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        bal = bal + interest - Decimal("1229.85")
        if bal < 0:
            bal = Decimal("250000.00")
        key = f"p{i % 997}:{i % 13}"
        rows.setdefault(key, []).append(interest)
        total += interest
    s = sorted(rows.items(), key=lambda kv: (len(kv[1]), kv[0]))
    return total, len(s)


def main(reps):
    times = []
    for _ in range(reps):
        t0 = time.perf_counter()
        work()
        times.append(time.perf_counter() - t0)
    print(f"cpu_bench min={min(times):.4f}s reps=" + " ".join(f"{t:.4f}" for t in times))


if __name__ == "__main__":
    main(int(sys.argv[1]) if len(sys.argv) > 1 else 7)
