import asyncio
import sys

class AsyncExecutor:

    def __init__(self, jobs):
        self.procs = []
        self.tasks = [asyncio.create_task(self.run_job(job)) for job in jobs]
        self.lock = asyncio.Lock()

    async def run_job(self, job):
        async with self.lock:
            print("Starting ...")
        proc = await asyncio.create_subprocess_shell(" ".join(job))
        async with self.lock:
            self.procs.append(proc)
        rc = await proc.wait()
        async with self.lock:
            self.procs.remove(proc)
            if rc != 0:
                for proc in self.procs:
                    proc.kill()

    async def execute(self):
        for task in self.tasks:
            await task

async def good_case():
    executor = AsyncExecutor (
        [
            ((sys.executable, "./job.py", "1", "0", "5")),
            ((sys.executable, "./job.py", "2", "0", "2")),
        ]
    )
    await executor.execute()

async def bad_case():
    executor = AsyncExecutor (
        [
            ((sys.executable, "./job.py", "1", "1", "5")),
            ((sys.executable, "./job.py", "2", "2", "2")),
        ]
    )
    await executor.execute()

asyncio.run(good_case())
asyncio.run(bad_case())