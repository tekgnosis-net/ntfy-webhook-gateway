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
        # Test helper: wait for spawned dispatches to finish. All tasks in a
        # batch complete before any of their exceptions is re-raised.
        while self._tasks:
            results = await asyncio.gather(*list(self._tasks), return_exceptions=True)
            for result in results:
                if isinstance(result, BaseException):
                    raise result
