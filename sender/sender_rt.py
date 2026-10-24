"""Sender: low-latency mode for the phone (needs doas / root; without privileges it logs and carries on unchanged).

Ping spikes and input delays of a few ms on a phone do not come from the network (Ethernet: < 1 ms) but from the
CPU's power saving:
  cluster sleep      with no pad or network traffic the cores and the whole cluster fall into deep sleep; waking up
                     takes milliseconds (Snapdragon 845: leaving "cluster sleep" ~6.5 ms) - that is how long a pong or
                     a pad report waits. PM QoS (/dev/cpu_dma_latency) forbids idle states whose wake-up is longer
                     than cpu_latency_us for as long as the file stays open (the kernel drops the request by itself
                     when the process exits - also after a crash).
  little cores       Python on a "LITTLE" core at a low clock needs several times longer to process an event; the
                     process is pinned to the fastest cores (largest cpu_capacity in sysfs).
  scheduler          SCHED_FIFO (rt_priority): the sender thread does not wait in the run queue behind other
                     processes. With the RESET_ON_FORK flag - programs it launches (bluetoothctl) get a normal priority.
"""
from __future__ import annotations

import errno
import glob
import os
import sys
import struct
from typing import BinaryIO, Final

SWITCH_INTERVAL_S: Final = 0.0005     # default is 0.005
CPU_DMA_LATENCY: Final = "/dev/cpu_dma_latency"   # PM QoS interface: the request lives as long as the file stays open
RUNTIME_NICE: Final = -10         # a little above normal priority; works without the real-time policy as well
MIN_DISTINCT_CAPACITIES: Final = 2    # fewer distinct cpu_capacity values = identical cores, nothing to pin to
CPU_DIR_PREFIX_LEN: Final = len("cpu")   # "cpu7" -> "7"
ENOSYS_FALLBACK_NR: Final = 119   # sched_setscheduler in the asm-generic syscall table (aarch64, riscv64)

_qos: BinaryIO | None = None      # the open /dev/cpu_dma_latency - kept open until the process ends


def hold_cpu_latency(us: int, path: str = CPU_DMA_LATENCY) -> str | None:
    """Cap the CPU wake-up latency at ``us`` microseconds with PM QoS.

    The kernel honours the request only while the file descriptor stays open, so the file is stored in a module
    global instead of being closed. If the request is made again, the new descriptor replaces (and closes) the old one.

    Returns:
        A description, or None (no permission / no file).
    """
    global _qos
    f: BinaryIO | None = None
    try:
        f = open(path, "wb", buffering=0)  # noqa: SIM115 - kept open until the process ends
        f.write(struct.pack("i", us))
    except OSError as e:
        if f is not None:
            f.close()
        print(f"Low latency: CPU idle limit unavailable ({e.strerror}) - run with doas")
        return None
    if _qos is not None:
        _qos.close()
    _qos = f
    return f"CPU wake-up latency <= {us} us"


def fast_cpus(sys_root: str = "/sys") -> set[int]:
    """Return the set of CPUs with the highest capacity (sysfs cpu_capacity).

    On big.LITTLE SoCs every core reports a relative capacity; the cores sharing the maximum value are the "big" ones.
    The result is empty when all cores are identical or the data is unavailable, because then pinning would be pointless.
    """
    cap: dict[int, int] = {}
    for path in glob.glob(f"{sys_root}/devices/system/cpu/cpu[0-9]*/cpu_capacity"):
        try:
            with open(path) as f:
                cap[int(os.path.basename(os.path.dirname(path))[CPU_DIR_PREFIX_LEN:])] = int(f.read())
        except (OSError, ValueError):
            continue              # a core that went offline or a file in a format we do not know: leave that core out
    if len(set(cap.values())) < MIN_DISTINCT_CAPACITIES:
        return set()
    top = max(cap.values())
    return {cpu for cpu, c in cap.items() if c == top}


def pin_fast_cpus(sys_root: str = "/sys") -> str | None:
    """Restrict this process to the fast cores that it is allowed to use; returns a description or None.

    The fast set is intersected with the current affinity mask, so cgroup/cpuset limits are respected. Nothing is
    changed when there is no distinction between cores or the affinity call fails.
    """
    cpus = fast_cpus(sys_root) & os.sched_getaffinity(0)
    if not cpus:
        return None
    try:
        os.sched_setaffinity(0, cpus)
    except OSError as e:
        print(f"Low latency: cannot pin to fast cores ({e.strerror})")
        return None
    return "fast cores " + ",".join(map(str, sorted(cpus)))


# system call number of sched_setscheduler (asm-generic: aarch64, riscv64; other architectures listed separately)
_SYS_SCHED_SETSCHEDULER: Final = {"x86_64": 144, "i686": 156, "i386": 156, "armv7l": 156, "armv6l": 156, "armv8l": 156}


def _sched_syscall(policy: int, priority: int) -> None:
    """Call sched_setscheduler directly through the raw syscall.

    musl (Alpine, postmarketOS) deliberately does not implement this libc function (it always returns ENOSYS), so
    os.sched_setscheduler never works there, even as root. The kernel itself supports the call, so we skip libc and
    invoke it by its syscall number.
    """
    import ctypes
    import platform
    libc = ctypes.CDLL(None, use_errno=True)
    nr = _SYS_SCHED_SETSCHEDULER.get(platform.machine(), ENOSYS_FALLBACK_NR)
    param = ctypes.c_int(priority)                  # struct sched_param { int sched_priority; }
    if libc.syscall(ctypes.c_long(nr), 0, policy, ctypes.byref(param)) != 0:
        err = ctypes.get_errno()
        raise OSError(err, os.strerror(err))


def realtime(priority: int) -> str | None:
    """Switch this process to the SCHED_FIFO real-time policy with the given priority; returns a description or None.

    Priority 0 means "disabled". The standard library call is tried first; if libc answers ENOSYS (musl), the raw
    syscall is used instead. SCHED_RESET_ON_FORK makes child processes (bluetoothctl) fall back to a normal
    priority. Failures (typically a missing root privilege) are logged and are not fatal.
    """
    if not priority:
        return None
    policy = os.SCHED_FIFO | getattr(os, "SCHED_RESET_ON_FORK", 0)
    try:
        try:
            os.sched_setscheduler(0, policy, os.sched_param(priority))
        except OSError as e:
            if e.errno != errno.ENOSYS:
                raise
            _sched_syscall(policy, priority)     # musl: the libc function does not exist, the kernel handles the call normally
    except (OSError, AttributeError) as e:
        hint = " - run with doas" if getattr(e, "errno", None) in (errno.EPERM, errno.EACCES) else ""
        print(f"Low latency: realtime scheduling unavailable ({e}){hint}")
        return None
    return f"SCHED_FIFO {priority}"


def short_switch_interval() -> str:
    """Shorten the GIL switch interval so the event loop is not kept waiting by a worker thread running Python code.

    With the default 5 ms a thread that wants the GIL (the loop, after a pad report arrived) may wait that long for the
    running one to give it up. Worker threads here (sysfs, BlueZ calls) are short, so more frequent switching costs little.
    """
    sys.setswitchinterval(SWITCH_INTERVAL_S)
    return f"GIL switch interval {SWITCH_INTERVAL_S * 1000:g} ms"


def setup(low_latency: bool, cpu_latency_us: int, rt_priority: int) -> None:
    """Apply every latency tweak once at sender startup; each step is independent, so one failing does not block the rest.

    A small negative nice value and (if configured) real-time scheduling are always attempted; the PM QoS request
    and the fast-core pinning only run when low_latency is enabled. The steps that worked are summarised in one log line.
    """
    try:
        os.nice(RUNTIME_NICE)
    except OSError:
        pass                  # without privileges the priority simply stays normal
    results = [realtime(rt_priority)]            # each step returns a description of what it set, or None
    if low_latency:
        results += [hold_cpu_latency(cpu_latency_us), pin_fast_cpus(), short_switch_interval()]
    done = [r for r in results if r]
    if done:
        print("Low latency: " + ", ".join(done))
