import subprocess
from time import sleep


proc = subprocess.Popen(("echo", "hello world"), stdout=subprocess.PIPE)
sleep(1)
print(proc.communicate()[0].decode("UTF-8"))
