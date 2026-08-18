import asyncio


class AppState:
    """Services shared by the webhook and admin apps."""

    def __init__(self, db, client, retry_delays=(1.0, 5.0, 25.0)):
        self.db = db
        self.client = client
        self.retry_delays = retry_delays
        self._tasks = set()

    def spawn(self, coro):
        task = asyncio.create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return task

    async def drain(self):
        # Test helper: wait for spawned dispatches to finish.
        while self._tasks:
            await asyncio.gather(*list(self._tasks))
