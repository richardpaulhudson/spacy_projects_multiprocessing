import subprocess
import sys
import os
import signal
from multiprocessing import Queue, Process, Lock


def run(status_queue, lock, subprocess_args):
    with lock:
        print("Starting ...")
    sp = subprocess.Popen(subprocess_args)
    status_queue.put(("start", sp.pid))
    sp.communicate()
    rc = sp.returncode
    status_queue.put(("end", sp.pid, rc))


def execute(jobs):

    status_queue = Queue()
    lock = Lock()
    for job in jobs:
        process = Process(target=run, args=(status_queue, lock, job))
        process.start()
    pids = []
    for _ in range(2):
        status_info = status_queue.get()
        assert status_info[0] == "start"
        pids.append(status_info[1])

    for _ in range(2):
        status_info = status_queue.get()
        assert status_info[0] == "end"
        pids.remove(status_info[1])
        if status_info[2] != 0:
            for other_pid in pids[:]:
                os.kill(other_pid, signal.SIGTERM)
                pids.remove(other_pid)
            break


# good case
execute(
    [
        ((sys.executable, "./job.py", "1", "0", "5")),
        ((sys.executable, "./job.py", "2", "0", "2")),
    ]
)

# bad case
execute(
    [
        ((sys.executable, "./job.py", "1", "1", "5")),
        ((sys.executable, "./job.py", "2", "2", "2")),
    ]
)
