# Parallel execution within spaCy project files

## Introduction and scope

This repo documents the design decisions made for a [spaCy PR](https://github.com/explosion/spaCy/pull/10774) that aims to enable the parallel execution of commands within spaCy project workflows. It deals specifically with the mechanism used to manage the parallel execution of subprocesses, as this is the area that has triggered extensive discussion within the team; it does not cover aspects such as the syntax used to specify parallel execution within the project file.

## Requirements

### Functional requirements

1. spaCy project workflows support the definition of dependencies between commands and outputs (files created by dependencies) in a way that ensures commands are not rerun unnecessarily on consecutive workflow executions. All this functionality should continue to work in the same way for each command in a parallel group with respect to the rest of the project file. However, the management of dependencies and outputs within the context of a parallel group is out of scope.
2. It should be possible to specify a command group of size `m` together with a maximum number of parallel processes `n`, where `n<m`. The commands should be assigned to the processes in the group in the order in which they are declared in the project file.
3. If any command within a group returns a non-zero return code, the execution of the other processes in the group should be halted. It must be possible to switch off this feature because there are situations in which non-zero return codes are expected.
4. Output from commands and relating to their execution should be managed in such a way that nothing gets lost. As far as possible, output from separate parallel commands should be displayed separately.

### Non-functional requirements (selected)

101. Consistency: there should be as little difference as possible between the execution of serial and parallel commands. If something works with one execution type, it should also work with the other execution type.
102. Stability: parallel execution should operate in a predictable fashion and be consistent across platforms. As far as possible, this implies using standard APIs as documented and recommended. Hacks are to be avoided.
103. Minimal-invasiveness: because the existing codebase is stable and widely used, changes to it should be minimised.

## Implementation options

### `subprocess` directly

The current, purely serial implementation uses the [subprocess](https://docs.python.org/3/library/subprocess.html) module to transfer control from the workflow code to individual commands. Because the executed commands are executed at the OS level — they are not necessarily Python programs — it seems uncontroversial that this module should also be used for the new parallelisation feature.

Alongside the synchronous `subprocess.run()` command in use in the current implementation, the module contains a lower-level `subprocess.Popen()` method that starts a subprocess asynchronously, i.e. separate from the calling thread. An obvious solution for parallel execution is therefore to start the subprocesses in a group with `subprocess.Popen()` and wait for them to complete; if any subprocess fails (`rc!=0`) any other subprocesses in the group that are still running can be killed.

This approach is exemplified in [subprocess_direct.py](https://github.com/richardpaulhudson/multiprocessing_arch/subprocess.py). It has serious problems:

- The main process has to poll the subprocesses to see whether they have completed. Polling is normally regarded as a clear antipattern: instead, there should be some mechanism for the subprocesses to notify the main process when they complete.
- The fact that the subprocesses run separately from the main process means that there is no communication between their standard pipes and the main process' standard pipes. The `subprocess` documentation advises callers to use the `run()` method where posible so that the subprocess runs in communication with the starting process.
- Only the actual command is run in its own subprocess; there is no parallelisation of tooling within spaCy such as outputting status messages and managing dependencies and outputs. Handling this without parallelisation would be possible but would require major code changes.

### `asyncio-subprocess`

Async IO is a paradigm available within Python that is primarily designed to allow IO-bound tasks to relinquish control within the context of a single process. The library contains a [subprocess module](https://docs.python.org/3/library/asyncio-subprocess.html) which enables several subprocesses to be started from several coroutines within a single main process. Each coroutine can then react to whatever its subprocess returns, killing the other subprocesses if necessary.

This approach is exemplified in [subprocess_direct.py](https://github.com/richardpaulhudson/multiprocessing_arch/async.py). It, too, has serious problems:

- The fact that each subprocess is managed in its own subroutine greatly increases the code complexity and also introduces threading issues that have to be managed extensively with a mutex.
- Because asynchronous programming in Python can only be called from other asynchronous methods, going this route in spacy projects would require major changes that would probably include making methods asynchronous that are not directly relevant to the change. This would make the code hard to understand.
- Perhaps most seriously, the `kill()` command does not have the desired effect: although the main program returns, job 1 still outputs to the console a few seconds later. It is unclear exactly why this occurs, although it may be related to the `await proc.wait()` command. It seems likely that this problem could eventually be solved, but it does not seem worth pursuing it further given the other issues with the architecture.

### `trio`

`trio` is a high-level library designed primarily to support asynchronous IO. It provides a nice layer of abstraction over subprocess creation and management, but using it here would have the following problems:

- Using `trio` requires using asnychronous programming. Because asynchronous programming in Python can only be called from other asynchronous methods, going this route in spacy projects would require major changes that would probably include making methods asynchronous that are not directly relevant to the change. This would make the code hard to understand.
- Using `trio` would mean adding an additional dependency to spaCy, which we normally try and avoid wherever possible. In this case, there seems to be no major advantage over using Async IO directly.
- `trio` does not support Python 3.6, meaning that if we included it we would have to move the bottom Python version peg (this may not actually be that serious an issue, though, as Python 3.6 is not generally supported any more).

### `multiprocessing.Pool`

