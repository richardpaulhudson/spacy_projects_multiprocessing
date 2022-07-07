# Parallel execution within spaCy project files

## 1. Introduction and scope

This repo documents the design decisions made for a [spaCy PR](https://github.com/explosion/spaCy/pull/10774) that aims to enable the parallel execution of commands within spaCy project workflows. It deals specifically with the mechanism used to manage the parallel execution of subprocesses, as this is the area that has triggered extensive discussion within the team. It does not cover aspects such as the syntax used to specify parallel execution within the project file.

## 2. Requirements

### 2.1 Functional requirements

1. [spaCy project workflows](https://spacy.io/usage/projects) support the definition of dependencies and outputs (files created by commands) in order to ensure that commands are not re-executed unnecessarily on consecutive workflow runs. All this functionality should continue to work for each command in a parallel group with respect to the rest of the project file in the same way as it does for a serial command. However, the management of dependencies *between* the members of a parallel group is out of scope: the user is responsible for ensuring that no problems occur. 
2. It must be possible to specify a command group of size `m` together with a maximum number of parallel processes `n`, where `n<m`. The commands must be assigned to the processes in the group in the order in which they are declared in the project file.
3. If any command within a group returns a non-zero return code, the execution of the other processes in the group should be halted. It must be possible to switch off this feature because there might be situations in which a non-zero return code is the expected outcome.
4. Output from commands and relating to their execution should be managed in such a way that nothing gets lost. As far as possible, output from separate parallel commands should be displayed separately.

### 2.2 Non-functional requirements (selected)

101. Consistency: there should be as little difference as possible between the execution of serial and parallel commands; if something works with one execution type, it should also work with the other execution type.
102. Stability: parallel execution should operate in a predictable fashion that is consistent across platforms. As far as possible, this implies using standard APIs as documented and recommended; hacks are to be avoided.
103. Minimal-invasiveness: because the existing codebase is stable and widely used, changes to it should be minimised.

## 3. Implementation options

### 3.1 `subprocess` directly

The current, purely serial implementation uses the [subprocess](https://docs.python.org/3/library/subprocess.html) module to transfer control from the workflow code to individual commands. Because the executed commands are executed at the OS level — they are not necessarily Python programs — it seems uncontroversial that this module should also be used in the context of the new parallelisation feature.

Alongside the synchronous `subprocess.run()` function in use in the current implementation, the module offers a lower-level `subprocess.Popen()` function that starts a subprocess asynchronously, i.e. separately from the calling thread. An obvious solution for parallel execution is therefore to start a group of subprocesses with `subprocess.Popen()` and wait for them to complete; if any subprocess fails (`rc!=0`) any other subprocesses in the group that are still running can be killed.

The four `example ... py` scripts in the repository show different ways of achieving the same thing. In all cases:

- two commands are executed in parallel, one of which sleeps for two seconds and the other of which sleeps for five seconds; each logs to the console before and after the sleep.
- the commands are executed again, but this time the two-second command returns a non-zero return code, which should lead to the five-second command being killed.

The direct `subprocess` approach is exemplified in [example_subprocess_direct.py](https://github.com/richardpaulhudson/spacy_multiprocessing_arch/blob/main/example_subprocess_direct.py). However, it has serious problems:

- The main process has to poll the subprocesses to see whether they have completed. Polling is normally regarded as a clear antipattern: instead, there should be some mechanism for the subprocesses to notify the main process when they complete (push as opposed to pull).
- The fact that the subprocesses run separately from the main process means that there is no communication between their standard pipes and the main process' standard pipes. The `subprocess` documentation advises callers to use the `run()` method where possible so that the subprocess runs in communication with the Python process that created it.
- Only the actual commands are run in their own subprocesses; there is no parallelisation of tooling within spaCy projects such as outputting status messages and managing dependencies and outputs. Handling such tooling from a single main process/thread without parallelisation would certainly be possible, but would require major code changes.

To avoid this last problem, it seems sensible to aim for a three-tier architecture:

- the **main process** (Python)
- several **worker processes** (Python) started by the main process that deal with logging, outputs and dependencies; each worker processes starts a single subprocess
- **subprocesses** (OS) 

### 3.2 `asyncio-subprocess`

Async IO is a paradigm available within Python that is primarily designed to allow IO-bound tasks to relinquish control within the context of a single process. The library contains a [subprocess module](https://docs.python.org/3/library/asyncio-subprocess.html) which enables subprocesses to be started from several coroutines within a single main process. Each coroutine can then react to whatever its subprocess returns, killing the other subprocesses if necessary. If we took this route in spaCy projects, the coroutine would play the role of the worker process.

This approach is exemplified in [example_subprocess_async.py](https://github.com/richardpaulhudson/spacy_multiprocessing_arch/blob/main/example_subprocess_async.py). It, too, has serious problems:

- The fact that each subprocess is managed in its own coroutine greatly increases the code complexity and also introduces threading issues that have to be managed extensively with a mutex.
- Because asynchronous functions can only be called from other asynchronous functions or by an asynchronous runner method like `asyncio.run()`, going this route in spacy projects would require major changes that would probably include making methods asynchronous that are not directly relevant to the change. This would make the code hard to understand.

### 3.3 `trio`

[trio](https://trio.readthedocs.io/en/stable/reference-io.html) is a high-level library designed primarily to support asynchronous IO. It provides a nice layer of abstraction over subprocess creation and management, but using it here would have the following problems:

- Using `trio` requires using asynchronous programming. - Because asynchronous functions can only be called from other asynchronous functions or by an asynchronous runner method like `asyncio.run()`, going this route in spacy projects would require major changes that would probably include making methods asynchronous that are not directly relevant to the change. This would make the code hard to understand.
- Using `trio` would mean adding an additional dependency to spaCy, which we normally try and avoid wherever possible. In this case, there seems to be no clear advantage over other architecture variants.
- `trio` does not support Python 3.6, meaning that if we included it we would have to move the bottom Python version peg for spaCy (this may not actually be that serious an issue, though, as Python 3.6 is no longer officially supported in any case).

### 3.4 `multiprocessing.Pool`

At first glance [multiprocessing.Pool](https://docs.python.org/3/library/multiprocessing.html#multiprocessing.pool.Pool) looks like exactly what we need: 

- a pool of processes of a specified size; each process in the pool could play the role of worker process.
- individual jobs are assigned to processes in the pool using the `apply_async()` method, whose parameters are the Python method to be run and optionally a callback method to call when the job is complete.
- the pool has a `terminate()` method that kills all currently running processes.

However, this route would involve the following problems:

- The `terminate()` method kills the worker processes in the pool, but not subprocesses started by those worker processes. 
- In order to allow the subprocesses to be terminated as well, each pool worker process would need to determine its subprocess' PID and the PIDs would need to be maintained centrally by the main process to enable them all to be killed. However, passing the PIDs from the worker processes to the main process for centralised management would be messy at best because:
    - the job passed to `apply_async()` is normally a function.
    - the job can only be a method if that method is specifically made picklable.
    - if the method is picklable, different jobs within the pool may end up accessing different instance variables in different spawned processes.
- In general the reliance on callback methods is likely to result in code that is messy and hard to understand and debug.
- Process pools are primarily intended to perform map-reduce, i.e. to execute the same job multiple times in parallel with different input data. `multiprocessing.Pool` provides a convenient method for collecting the output from multiple parallel processes and returning when they have all completed. This is, however, not what we require here: we need to react to **each** process completing at the moment it happens.

### 3.5 `ProcessPoolExecutor`

[ProcessPoolExecutor](https://docs.python.org/3/library/concurrent.futures.html#concurrent.futures.ProcessPoolExecutor) provides a wrapper around `multiprocessing.Pool` and thus shares the basic problems set out above for `multiprocessing.Pool`. Additionally, it has the following problem:

- It doesn't have anything corresponding to `multiprocessing.Pool.terminate()`. It has a `shutdown()` method, but this only prevents pending jobs from executing; it doesn't terminate jobs that are already running. This means stopping a running parallel group would mean killing the subprocesses and then waiting for each worker process to register that its subprocess was dead and return control to the pool. This seems a very hairy procedure.

### 3.6 `ThreadPoolExecutor`

[ThreadPoolExecutor](https://docs.python.org/3/library/concurrent.futures.html#concurrent.futures.ThreadPoolExecutor) has the same functional interface as `ProcessPoolExecutor`, but using threads within a single process rather than multiple processes. Because most of the work is done by the subprocesses and the work done by the worker processes/worker threads is simple and completely standardised, using worker threads rather than worker processes initially seems a reasonable choice. However:

- `ThreadPoolExecutor` shares all the architectural problems of `ProcessPoolExecutor`, including the lack of a `terminate()` method.
- Even though starting worker processes just to start subprocesses may seem like overkill, the number of jobs in a typical spaCy projects file and the required latency are low enough that it is still probably a better choice to opt for the increased isolation that processes offer over threads.

### 3.7 `multiprocessing.Queue`

All the above variants have the basic problem that each worker process communicates separately with the main process that started it. The main process can only block on one worker process at a time but needs to monitor them all, which means it has to either poll or offer confusing callbacks with threading issues. What is required is instead a mechanism that allows the main process to block on **all** worker processes at once within a **single thread** and be triggered by individual worker processes as and when they are ready.

The standard architectural building block to achieve this is a queue on which the main process listens and to which worker processes can submit messages. Python's multiprocessing library has a standard `Queue` implementation which I have been using it for several years within the [Holmes](https://github.com/explosion/holmes-extractor) library without any problems. Because queue messaging takes place orthogonally to the main flow of control within a program, queues are also ideal for adding functionality to an existing codebase in a minimal-invasive fashion: a queue can be passed down through multiple layers of a program as an optional parameter, and because data is passed back via the queue, there is no need to change the return types of the functions and methods involved.

In [example_queueing.py](https://github.com/richardpaulhudson/spacy_multiprocessing_arch/blob/main/example_queueing.py), a simple bespoke protocol is implemented that fulfils two requirements via a single multiprocessing queue:

- When a worker process starts a subprocess, it sends the PID of the subprocess back to the main process.
- When a subprocess completes, the worker process sends the return code of the subprocess back to the main process.

## 4. Specific issues around the queueing solution

### 4.1 Communication between worker processes and subprocesses

The [subprocess](https://docs.python.org/3/library/subprocess.html) documentation advises using `subprocess.run()` rather the lower-level `subprocess.Popen()` whenever possible. In [example_queueing.py](https://github.com/richardpaulhudson/spacy_multiprocessing_arch/blob/main/example_queueing.py), however, the subprocess is created using `Popen()` to allow the worker process to retrieve the subprocess' PID and send it to the main process via the queue.

Nevertheless, on reflection and further investigation the main issue with calling `Popen()` is that it starts a subprocess that is not in communication with its worker process, meaning that e.g. the standard pipes are not managed automatically. In the proposed solution, communication is established using `communicate()` immediately after the worker process has placed the subprocess' PID in the queue and is maintained until the subprocess completes.

One concern is whether subprocess output could get lost in the split second before communication is established. [popen_with_sleep.py](https://github.com/richardpaulhudson/spacy_multiprocessing_arch/blob/main/popen_with_sleep.py) demonstrates that this is not the case: although communication is established well after the subprocess has written to `stdout`, the relevant output is still correctly piped to the console.

### 4.2 The start method for worker processes

Given that worker processes perform a simple and constant range of tasks, it probably makes little difference which [method](https://docs.python.org/3/library/multiprocessing.html#contexts-and-start-methods) is used to start them. However, in the interests of cross-platform consistency it is probably best if `spawn` is used on all platforms simply because it is the only method that is available on all platforms.

The main disadvantage of `spawn` is that it involves copying memory from the spawning process to the spawned process; this disadvantage is not relevant here because the main process will not have a significant memory footprint.

### 4.3 The termination signal

The `os.kill()` method used to kill subprocesses requires the specification of a termination signal. In the example scripts `SIGTERM (15)` is used and seems an appropriate choice, although it would also be possible to allow the user to specify `SIGKILL (9)` as an option within the project file. These are POSIX signals; experimentation will be necessary on Windows to elicit the appropriate behaviour (see e.g. [here](https://stackoverflow.com/questions/35772001/how-to-handle-a-signal-sigint-on-a-windows-os-machine)).

In general it is worth noting that if subprocess termination fails for some reason on some OS with some type of process, the outcome will probably be that the workflow execution hangs. The outcome is very unlikely to be worse than if the feature had never been implemented: it is worth trying to terminate everything on all OS even if this is not always straightforward.

### 4.4 Managing console output

In the proposed solution, console output from the **worker processes** is managed using a mutex to ensure that sections of output from different worker processes remain separate. On the other hand, the executed **subprocesses** know nothing about the workflow system and any subprocess could log anything to `stdout` or `stderr` at any time. It probably makes more sense for subprocesses to log to separate log files, but we have no way of stipulating this and they are quite at liberty to log to the console whenever they like. There are two possible ways of managing console output from subprocesses, neither of which is ideal:

- Each subprocess logs directly to the console. This ensures console output is displayed in real time; however, console output from different subprocesses can get mixed up.
- Each worker process stores the console output from its subprocess and returns it to the main process together with the return code so the main process can log it. This option is demonstrated by the script [example_queueing_with_output_management.py](https://github.com/richardpaulhudson/spacy_multiprocessing_arch/blob/main/example_queueing_with_output_management.py). It ensures that console output is displayed cleanly and separately for each subprocess, but also means that:

    - console output is not displayed in real time
    - console output is lost for killed commands in a group. This is not ideal, but is not necessarily a big problem because the console output for the command that **actually failed** and led to the other commands being killed — and this is the command that the user will usually want to investigate — will never be lost. I tried out various buffering and streaming options but could not find a way of capturing a process' pipe output before it dies. If somebody has one that is not too complex, though, that would obviously be great.
    
My suggestion is to make the second option the standard for `stdout` and the first option the standard for `stderr`. It must however be possible for users to override this standard in the project file and to choose the first option for `stdout` instead, e.g. because: 

- it is necessary for debugging
- they want to make sure output cannot be lost from commands in a group where another command fails
- a process is generating huge amounts of output that would risk overwhelming main memory or the multiprocessing queue.

### 4.5 Executing serial commands

For consistency's sake, it could be argued that commands executed in the normal, existing serial fashion should also use the new mechanism and that every serial command should be executed as a 1-member parallel group. On balance, though, starting an extra worker process every time a serial command is executed seems like overkill. This change is also much less risky if it only relates to parallel execution in new project files rather than to all commands in old and new project files. It therefore makes sense to leave serial execution as it is.