#!/usr/bin/env python3
"""Three-process Lamport/vector-clock simulator for the Lecture 03 lab."""

from __future__ import annotations

import argparse
import queue
import random
import threading
import time
from dataclasses import dataclass
from typing import Sequence


Vector = tuple[int, ...]


@dataclass(frozen=True)
class Message:
    sender: int
    lamport: int
    vector: Vector


def vector_le(left: Sequence[int], right: Sequence[int]) -> bool:
    """Return True exactly when left is component-wise <= right."""
    return all(a <= b for a, b in zip(left, right, strict=True))


def vectors_concurrent(left: Sequence[int], right: Sequence[int]) -> bool:
    """Two vector timestamps are concurrent when neither dominates the other."""
    return not vector_le(left, right) and not vector_le(right, left)


class Worker(threading.Thread):
    """A process whose clock state is private to its worker thread."""

    def __init__(
        self,
        process_id: int,
        inboxes: list[queue.Queue[Message | None]],
        output: queue.Queue[tuple[str, int | None]],
        steps: int,
        seed: int,
        delay: float,
    ) -> None:
        super().__init__(name=f"P{process_id}")
        self.process_id = process_id
        self.inboxes = inboxes
        self.inbox = inboxes[process_id]
        self.output = output
        self.steps = steps
        self.rng = random.Random(seed + process_id)
        self.delay = delay
        self.lamport = 0
        self.vector = [0] * len(inboxes)

    def _emit(self, event: str) -> None:
        line = (
            f"[P{self.process_id}] {event:<24} | "
            f"Lamport: {self.lamport:<3} | Vector: {self.vector}"
        )
        self.output.put((line, None))

    def _tick(self) -> None:
        self.lamport += 1
        self.vector[self.process_id] += 1

    def local_event(self) -> None:
        self._tick()
        self._emit("LOCAL EVENT")

    def send_event(self) -> None:
        targets = [i for i in range(len(self.inboxes)) if i != self.process_id]
        target = self.rng.choice(targets)
        self._tick()
        message = Message(self.process_id, self.lamport, tuple(self.vector))
        self.inboxes[target].put(message)
        self._emit(f"SEND MSG to P{target}")

    def receive_event(self, message: Message) -> None:
        local_before_merge = tuple(self.vector)
        if vectors_concurrent(local_before_merge, message.vector):
            self.output.put(
                (
                    f"[P{self.process_id}] CONFLICT DETECTED with P{message.sender}! "
                    f"Vector {list(local_before_merge)} || {list(message.vector)}",
                    None,
                )
            )

        self.lamport = max(self.lamport, message.lamport) + 1
        self.vector = [
            max(local, remote)
            for local, remote in zip(self.vector, message.vector, strict=True)
        ]
        self.vector[self.process_id] += 1
        self._emit(f"RECV MSG fr P{message.sender}")

    def try_receive(self) -> bool:
        try:
            message = self.inbox.get_nowait()
        except queue.Empty:
            return False
        if message is None:
            # STOP is only sent during the final drain phase.
            self.inbox.task_done()
            return False
        self.receive_event(message)
        self.inbox.task_done()
        return True

    def run(self) -> None:
        # Weighted random event loop. A failed receive becomes a local event.
        for _ in range(self.steps):
            action = self.rng.choices(
                ("local", "send", "receive"), weights=(3, 4, 3), k=1
            )[0]
            if action == "local":
                self.local_event()
            elif action == "send":
                self.send_event()
            elif not self.try_receive():
                self.local_event()
            if self.delay:
                time.sleep(self.rng.random() * self.delay)

        # Tell the coordinator this worker will send no more messages.
        self.output.put(("", self.process_id))

        # Drain all messages queued by other workers before the coordinator's STOP.
        while True:
            message = self.inbox.get()
            if message is None:
                self.inbox.task_done()
                break
            self.receive_event(message)
            self.inbox.task_done()


def run_simulation(steps: int, seed: int, delay: float) -> list[str]:
    process_count = 3
    inboxes: list[queue.Queue[Message | None]] = [
        queue.Queue() for _ in range(process_count)
    ]
    output: queue.Queue[tuple[str, int | None]] = queue.Queue()
    workers = [
        Worker(i, inboxes, output, steps, seed, delay) for i in range(process_count)
    ]
    for worker in workers:
        worker.start()

    lines: list[str] = []
    finished: set[int] = set()
    while len(finished) < process_count:
        line, done_process = output.get()
        if line:
            lines.append(line)
        if done_process is not None:
            finished.add(done_process)
        output.task_done()

    # Every data message was enqueued before its sender emitted DONE. FIFO queues
    # therefore make these sentinels terminate workers only after their drain.
    for inbox in inboxes:
        inbox.put(None)
    for worker in workers:
        worker.join()

    while True:
        try:
            line, _ = output.get_nowait()
        except queue.Empty:
            break
        if line:
            lines.append(line)
        output.task_done()
    return lines


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--steps", type=int, default=10, help="random steps per worker")
    parser.add_argument("--seed", type=int, default=7, help="base random seed")
    parser.add_argument(
        "--delay",
        type=float,
        default=0.01,
        help="maximum random sleep in seconds after an event",
    )
    args = parser.parse_args()
    if args.steps < 1:
        parser.error("--steps must be at least 1")
    if args.delay < 0:
        parser.error("--delay cannot be negative")
    return args


if __name__ == "__main__":
    arguments = parse_args()
    for log_line in run_simulation(arguments.steps, arguments.seed, arguments.delay):
        print(log_line)
