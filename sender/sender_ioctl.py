"""Sender: Linux ioctl request numbers (the ``_IOC`` macro of asm-generic/ioctl.h).

The request number of an ioctl packs the transfer direction, the buffer size, a "magic" type letter of the driver family
and a command number into one 32-bit value; the sender needs two of them (EVIOCGMTSLOTS for evdev, HIDIOCGFEATURE for hidraw)
and Python has no ready-made helper.
"""
from typing import Final

IOC_WRITE: Final = 1        # the kernel reads the buffer
IOC_READ: Final = 2         # the kernel writes the buffer
IOC_TYPE_SHIFT: Final = 8
IOC_SIZE_SHIFT: Final = 16
IOC_DIR_SHIFT: Final = 30


def ioc(direction: int, magic: str, number: int, size: int) -> int:
    """Build an ioctl request number the way the ``_IOC`` macro does.

    Args:
        direction: ``IOC_READ``, ``IOC_WRITE`` or both ORed together.
        magic: The one-letter type of the driver family (``"E"`` for evdev, ``"H"`` for HID).
        number: The command number within that family.
        size: The size of the argument buffer in bytes.

    Returns:
        The request number to pass to ``fcntl.ioctl``.
    """
    return (direction << IOC_DIR_SHIFT) | (size << IOC_SIZE_SHIFT) | (ord(magic) << IOC_TYPE_SHIFT) | number
