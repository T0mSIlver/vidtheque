#!/usr/bin/env python3
"""Hold VRAM hostage, so admission control has something to refuse.

A co-tenant on the card (llama.cpp, on the box this was written for) is the
condition the lifecycle manager's eviction path exists for, and it is the one
condition a single-process test cannot create: torch inside the worker would
just be the worker's own memory. So this allocates a slab in a *separate*
process and sits on it.

    uv run --no-sync python bench/ballast.py --mb 14000        # until killed
    uv run --no-sync python bench/ballast.py --mb 14000 --hold 120

Prints ``ready <mb> <device_used_mb>`` on stdout once the allocation is
resident, so a driver can wait for it instead of sleeping and hoping. Not
stdlib-only — it needs torch — which is why it is a separate script rather
than part of ``harness.py``.
"""

from __future__ import annotations

import argparse
import sys
import time


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--mb", type=int, required=True, help="MiB to allocate")
    parser.add_argument("--device", default="cuda:0")
    parser.add_argument(
        "--hold",
        type=float,
        default=0.0,
        help="seconds to hold before exiting; 0 = until killed",
    )
    args = parser.parse_args()

    import torch

    if not torch.cuda.is_available():
        print("no CUDA device", file=sys.stderr)
        return 2

    # uint8 so MiB maps to elements one-to-one, and one big tensor rather than
    # many so the caching allocator's own fragmentation does not inflate it.
    torch.zeros(args.mb * 1024 * 1024, dtype=torch.uint8, device=args.device)
    torch.cuda.synchronize()
    used = torch.cuda.mem_get_info(0)
    device_used_mb = (used[1] - used[0]) // (1024 * 1024)
    print(f"ready {args.mb} {device_used_mb}", flush=True)

    if args.hold:
        time.sleep(args.hold)
    else:
        while True:
            time.sleep(3600)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
