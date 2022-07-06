import subprocess
import sys
from time import sleep

def execute(jobs):
    processes = []
    for job in jobs:
        print("Starting ...")
        processes.append(subprocess.Popen(job))
    while True:
        for process in processes:
            rc = process.poll()
            if rc is not None:
                processes.remove(process)
                if rc != 0:
                    for other_process in processes:
                        other_process.kill()
                    break
        if len(processes) == 0:
            break
        sleep(0.1)


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
