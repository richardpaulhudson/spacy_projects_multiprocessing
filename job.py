import sys
from time import sleep

_, name, rc, waittime = sys.argv
print("Job " +  name + ": 0s")
sleep(int(waittime))
print("Job " +  name + ": " +  waittime + "s")
exit(int(rc))
