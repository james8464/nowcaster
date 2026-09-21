"""Disabled extension boundary; no signal path imports this module.

Any future adapter requires a separately designed, authorized integration.
There is deliberately no connection, credential or execution operation here.
"""

from typing import Literal, Never, Protocol


class BrokerExecutionAdapter(Protocol):
    @property
    def disabled(self) -> Literal[True]: ...

    def capabilities(self) -> Never: ...


class DisabledBrokerExecutionAdapter:
    """The sole concrete boundary exposes no usable capabilities."""

    __slots__ = ()

    @property
    def disabled(self) -> Literal[True]:
        return True

    def capabilities(self) -> Never:
        raise RuntimeError("broker adapter is disabled")


__all__ = ["BrokerExecutionAdapter", "DisabledBrokerExecutionAdapter"]
