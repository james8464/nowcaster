from __future__ import annotations

import asyncio
import io
import os

import pytest


def test_bootstrap_does_not_prefetch_early_pipe_controls() -> None:
    from src.live_monitor.control_input import control_lines, read_bootstrap_line

    read_fd, write_fd = os.pipe()
    with os.fdopen(read_fd, "r") as stream:
        os.write(write_fd, b'{"schema_version":1}\n{"schema_version":1,"command":"shutdown"}\n')
        os.close(write_fd)
        assert read_bootstrap_line(stream) == '{"schema_version":1}\n'

        async def read_controls():
            return [line async for line in control_lines(stream)]

        assert asyncio.run(read_controls()) == ['{"schema_version":1,"command":"shutdown"}\n']


def test_pipe_control_read_can_cancel_without_waiting_for_writer_eof() -> None:
    from src.live_monitor.control_input import control_lines

    read_fd, write_fd = os.pipe()
    try:
        with os.fdopen(read_fd, "r") as stream:

            async def cancel_read():
                lines = control_lines(stream)
                pending = asyncio.create_task(anext(lines))
                await asyncio.sleep(0.01)
                pending.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await asyncio.wait_for(pending, timeout=0.5)
                await lines.aclose()

            asyncio.run(cancel_read())
    finally:
        os.close(write_fd)


def test_private_input_rejects_oversized_lines_without_unbounded_reads() -> None:
    from src.live_monitor.control_input import control_lines, read_bootstrap_line

    with pytest.raises(ValueError, match="bootstrap"):
        read_bootstrap_line(io.StringIO("x" * (64 * 1024 + 1)))

    async def oversized_control():
        async for _line in control_lines(io.StringIO("x" * (4 * 1024 + 1))):
            pass

    with pytest.raises(ValueError, match="control"):
        asyncio.run(oversized_control())
