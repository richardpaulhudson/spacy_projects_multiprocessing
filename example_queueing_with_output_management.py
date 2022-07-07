import subprocess
import sys
import os
import signal
from multiprocessing import Queue, Process, Lock


def run(status_queue, lock, subprocess_args):
    with lock:
        print("Starting ...")
    sp = subprocess.Popen(
        subprocess_args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT
    )
    status_queue.put(("start", sp.pid))
    output, _ = sp.communicate()
    rc = sp.returncode
    status_queue.put(("end", sp.pid, rc, output))


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
        print(status_info[3].decode("UTF-8"))
        pids.remove(status_info[1])
        if status_info[2] != 0:
            for other_pid in pids[:]:
                os.kill(other_pid, signal.SIGTERM)


# good case
execute(
    [
        ((sys.executable, "-u", "./job.py", "1", "0", "5")),
        ((sys.executable, "-u", "./job.py", "2", "0", "2")),
    ]
)

# bad case
execute(
    [
        ((sys.executable, "-u", "./job.py", "1", "1", "5")),
        ((sys.executable, "-u", "./job.py", "2", "2", "2")),
    ]
)
