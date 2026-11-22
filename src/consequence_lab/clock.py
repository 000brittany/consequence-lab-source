"""Persist active elapsed time; checkpoint downtime does not reset usage."""
import asyncio
import time


class ManualClock:
    kind = "manual"

    def __init__(self, elapsed=0.0):
        self.value = float(elapsed)

    def elapsed(self):
        return self.value

    async def wait(self, delay):
        self.value += max(0, delay)

    def export(self):
        return {"kind": self.kind, "elapsed": self.elapsed()}


class WallClock:
    kind = "wall"

    def __init__(self, elapsed=0.0):
        self.previous = float(elapsed)
        self.start = time.monotonic()

    def elapsed(self):
        return self.previous + time.monotonic() - self.start

    async def wait(self, delay):
        await asyncio.sleep(max(0, delay))

    def export(self):
        return {"kind": self.kind, "elapsed": self.elapsed()}


def restore_clock(saved):
    if saved["kind"] == "manual":
        return ManualClock(saved["elapsed"])
    if saved["kind"] == "wall":
        return WallClock(saved["elapsed"])
    raise ValueError("Unknown clock")
