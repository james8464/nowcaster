from __future__ import annotations

import asyncio
import io
import os
import stat
from collections.abc import AsyncIterator
from typing import BinaryIO, TextIO

BOOTSTRAP_LIMIT = 64 * 1024
CONTROL_LIMIT = 4 * 1024


def _unbuffered_input(stream: TextIO) -> BinaryIO | TextIO:
    # Never let bootstrap text buffering consume controls intended for the pipe reader.
    buffer = getattr(stream, "buffer", None)
    return getattr(buffer, "raw", stream)


def _checked_line(line: bytes | str, *, limit: int, kind: str) -> str:
    encoded = line.encode("utf-8") if isinstance(line, str) else line
    if len(encoded) > limit:
        raise ValueError(f"monitor {kind} exceeds maximum size")
    return encoded.decode("utf-8")


def read_bootstrap_line(stream: TextIO) -> str:
    return _checked_line(
        _unbuffered_input(stream).readline(BOOTSTRAP_LIMIT + 1), limit=BOOTSTRAP_LIMIT, kind="bootstrap"
    )


async def control_lines(stream: TextIO) -> AsyncIterator[str]:
    try:
        descriptor = stream.fileno()
    except (AttributeError, io.UnsupportedOperation):
        descriptor = None
    if descriptor is None or stat.S_ISREG(os.fstat(descriptor).st_mode):
        # In-memory CLI tests and finite redirected files cannot leave an executor read blocked.
        source = _unbuffered_input(stream)
        while line := source.readline(CONTROL_LIMIT + 1):
            yield _checked_line(line, limit=CONTROL_LIMIT, kind="control")
            await asyncio.sleep(0)
        return

    reader = asyncio.StreamReader(limit=CONTROL_LIMIT)
    protocol = asyncio.StreamReaderProtocol(reader)
    pipe = os.fdopen(os.dup(descriptor), "rb", buffering=0)
    try:
        transport, _protocol = await asyncio.get_running_loop().connect_read_pipe(lambda: protocol, pipe)
    except BaseException:
        pipe.close()
        raise
    try:
        while True:
            try:
                line = await reader.readline()
            except ValueError as error:
                raise ValueError("monitor control exceeds maximum size") from error
            if not line:
                return
            yield _checked_line(line, limit=CONTROL_LIMIT, kind="control")
    finally:
        transport.close()
