"""Advance two owned native platform loops to the same logical round."""

import asyncio
from contextlib import asynccontextmanager
from dataclasses import dataclass

from oasis.social_platform.channel import Channel

from .native_recording_contracts import RecordingError


PLATFORMS = ("twitter", "reddit")


@dataclass
class _WriterFence:
    writer: asyncio.Task
    parked: asyncio.Future
    release: asyncio.Event


class PairedWriterChannel(Channel):
    """Let the same native writer acknowledge a drain and then park it."""

    def __init__(self):
        super().__init__()
        self._fence = None

    async def write_to_receive_queue(self, action_info):
        if self._fence is not None:
            raise RecordingError()
        return await super().write_to_receive_queue(action_info)

    async def receive_from(self):
        while True:
            key, action = await super().receive_from()
            if type(action) is not _WriterFence:
                return key, action
            if asyncio.current_task() is not action.writer:
                raise RecordingError()
            action.parked.set_result(None)
            await action.release.wait()

    @asynccontextmanager
    async def hold(self, writer):
        if self._fence is not None or writer.done():
            raise RecordingError()
        fence = _WriterFence(writer, asyncio.get_running_loop().create_future(), asyncio.Event())
        self._fence = fence
        try:
            await super().write_to_receive_queue(fence)
            completed, _ = await asyncio.wait(
                (writer, fence.parked), return_when=asyncio.FIRST_COMPLETED,
            )
            if writer in completed or not self.receive_queue.empty() or await self.send_dict.keys():
                raise RecordingError()
            yield
        finally:
            self._fence = None
            fence.release.set()


@dataclass
class PairedPlatformState:
    result: object
    agent_names: dict
    db_path: str
    action_logger: object
    cursor: int
    total_actions: int
    logical_rounds: int
    minutes_per_round: int
    total_rounds: int
    completed_round: int


class PairedRoundCoordinator:
    """Keep both runners parked at each cut and order their next actions.

    Initialize Twitter before Reddit. At each subsequent round, let Twitter
    finish before granting Reddit its turn. The owner can inspect both states
    after both turn acknowledgements, without either runner advancing.
    """

    def __init__(self, *, start_round=0):
        if type(start_round) is not int or start_round < 0:
            raise RecordingError()
        self.start_round = start_round
        self._reports = {platform: asyncio.Queue(maxsize=1) for platform in PLATFORMS}
        self._tasks = {}
        self._states = {}
        self._gates = {}

    def _require_owner(self, platform):
        if (platform not in self._tasks
                or asyncio.current_task() is not self._tasks[platform]):
            raise RecordingError()

    async def _report(self, platform):
        permission = asyncio.get_running_loop().create_future()
        self._reports[platform].put_nowait(permission)
        await permission

    async def ready(self, platform, result, agent_names, db_path, action_logger,
                    cursor, total_actions, logical_rounds, minutes_per_round,
                    total_rounds):
        self._require_owner(platform)
        numbers = (cursor, total_actions, logical_rounds, minutes_per_round, total_rounds)
        if (platform in self._states or any(type(number) is not int for number in numbers)
                or cursor < 0 or total_actions < 0 or minutes_per_round < 1
                or not self.start_round < total_rounds <= logical_rounds):
            raise RecordingError()
        self._states[platform] = PairedPlatformState(
            result, agent_names, db_path, action_logger, cursor, total_actions,
            logical_rounds, minutes_per_round, total_rounds, self.start_round,
        )
        await self._report(platform)

    async def after_round(self, platform, completed_round, cursor, total_actions):
        self._require_owner(platform)
        state = self._states.get(platform)
        if (state is None or type(completed_round) is not int
                or completed_round != state.completed_round + 1
                or completed_round > state.total_rounds
                or type(cursor) is not int or cursor < state.cursor
                or type(total_actions) is not int or total_actions < state.total_actions):
            raise RecordingError()
        state.completed_round = completed_round
        state.cursor = cursor
        state.total_actions = total_actions
        await self._report(platform)

    async def _acknowledgement(self, platform):
        task = self._tasks[platform]
        report = asyncio.create_task(self._reports[platform].get())
        try:
            complete, _ = await asyncio.wait((task, report), return_when=asyncio.FIRST_COMPLETED)
            if task in complete:
                task.result()
                raise RecordingError()
            self._gates[platform] = report.result()
        finally:
            if not report.done():
                report.cancel()
                await asyncio.gather(report, return_exceptions=True)

    def _release(self, platform):
        self._gates.pop(platform).set_result(None)

    async def run(self, start_platform, *, before_turn=None, at_cut=None):
        if self._tasks:
            raise RecordingError()
        try:
            for platform in PLATFORMS:
                self._tasks[platform] = asyncio.create_task(start_platform(platform))
                await self._acknowledgement(platform)
            twitter, reddit = (self._states[platform] for platform in PLATFORMS)
            if (twitter.logical_rounds, twitter.minutes_per_round, twitter.total_rounds) != (
                    reddit.logical_rounds, reddit.minutes_per_round, reddit.total_rounds):
                raise RecordingError()
            for completed_round in range(self.start_round + 1, twitter.total_rounds + 1):
                for platform in PLATFORMS:
                    if before_turn is not None:
                        before_turn(platform, self._states[platform])
                    self._release(platform)
                    await self._acknowledgement(platform)
                    if self._states[platform].completed_round != completed_round:
                        raise RecordingError()
                if at_cut is not None:
                    await at_cut(completed_round, twitter, reddit)
            for platform in PLATFORMS:
                self._release(platform)
            return await asyncio.gather(*(self._tasks[platform] for platform in PLATFORMS))
        finally:
            for task in self._tasks.values():
                if not task.done():
                    task.cancel()
            await asyncio.gather(*self._tasks.values(), return_exceptions=True)
