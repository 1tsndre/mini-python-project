"""Repository mocks in the manner of gomock: a call the test did not expect fails it, and so does an
expected call that never happens.
"""

import inspect
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any
from unittest.mock import create_autospec


@dataclass
class _Expectation:
    args: tuple[Any, ...] | None
    returns: Any
    raises: BaseException | None
    does: Callable[..., Any] | None
    times: int | None
    calls: int = 0

    def accepts(self, args: tuple[Any, ...]) -> bool:
        has_room = self.times is None or self.calls < self.times
        return has_room and (self.args is None or self.args == args)


class Controller:
    def __init__(self) -> None:
        self._names: dict[int, str] = {}
        self._expected: dict[int, list[_Expectation]] = {}
        self._unexpected: list[str] = []

    def mock[T](self, cls: type[T]) -> T:
        """A mock of cls whose async methods all fail unless expected."""
        mock = create_autospec(cls, instance=True)
        for name, _ in inspect.getmembers(cls, inspect.iscoroutinefunction):
            if name.startswith("_"):
                continue
            method = getattr(mock, name)
            self._names[id(method)] = f"{cls.__name__}.{name}"
            method.side_effect = self._dispatcher(id(method))
        return mock

    def expect(
        self,
        method: Any,
        *args: Any,
        returns: Any = None,
        raises: BaseException | None = None,
        does: Callable[..., Any] | None = None,
        times: int | None = 1,
    ) -> None:
        """Expects calls of a mocked method, like gomock's EXPECT(). With args, only a call with equal
        positional arguments matches (unittest.mock.ANY matches anything). The call returns returns,
        raises raises, or returns what does gives for the call's arguments. times=None allows any
        number of calls, including none.
        """
        key = id(method)
        if key not in self._names:
            raise TypeError("expect() takes a method of a mock made by Controller.mock()")
        self._expected.setdefault(key, []).append(
            _Expectation(args=args or None, returns=returns, raises=raises, does=does, times=times)
        )

    def finish(self) -> None:
        problems = list(self._unexpected)
        for key, expectations in self._expected.items():
            for exp in expectations:
                if exp.times is not None and exp.calls != exp.times:
                    args = "" if exp.args is None else repr(exp.args)
                    problems.append(f"{self._names[key]}{args}: expected {exp.times} call(s), got {exp.calls}")
        assert not problems, "mock expectations not met:\n  " + "\n  ".join(problems)

    def _dispatcher(self, key: int) -> Callable[..., Any]:
        def call(*args: Any, **kwargs: Any) -> Any:
            if not kwargs:
                for exp in self._expected.get(key, []):
                    if exp.accepts(args):
                        exp.calls += 1
                        if exp.raises is not None:
                            raise exp.raises
                        if exp.does is not None:
                            return exp.does(*args)
                        return exp.returns
            # Recorded as well as raised: the code under test may swallow the exception.
            message = f"unexpected call {self._names[key]}{args!r}" + (f" {kwargs!r}" if kwargs else "")
            self._unexpected.append(message)
            raise AssertionError(message)

        return call
