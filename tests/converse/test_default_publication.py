"""Default registration shares the registry's publication and ownership rules."""
from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from korina.converse import (
    ConverseChannel, ConverseResponse, ensure_default_registered,
    get_active_channel_name, get_converse_channel, register_converse_channel,
    reset_default_registered, reset_for_testing, unregister_converse_channel,
)
from korina.converse import korina_channel


class CustomChannel(ConverseChannel):
    name = 'korina'

    async def send(self, req):
        return ConverseResponse(text='custom')

    async def stream(self, req):
        yield await self.send(req)

    async def cancel(self):
        pass


@pytest.fixture(autouse=True)
def clean_registry():
    reset_for_testing()
    yield
    reset_for_testing()


@pytest.mark.parametrize('remove', ['unregister', 'reset_registry'])
def test_ensure_restores_missing_default_without_a_second_reset_hook(remove):
    ensure_default_registered()
    previous = get_converse_channel('korina')
    if remove == 'unregister':
        assert unregister_converse_channel('korina')
    else:
        reset_for_testing()
    ensure_default_registered()
    restored = get_converse_channel('korina')
    assert restored is not previous
    assert get_active_channel_name() == 'korina'


def test_explicit_registration_wins_while_real_default_constructor_is_held(monkeypatch):
    entered, release = threading.Event(), threading.Event()
    original = korina_channel.KorinaConverseChannel

    class HeldDefault(original):
        def __init__(self):
            entered.set()
            assert release.wait(3), 'test must release held default construction'
            super().__init__()

    monkeypatch.setattr(korina_channel, 'KorinaConverseChannel', HeldDefault)
    selected = CustomChannel()
    selected.name = 'selected-custom'
    register_converse_channel(selected)
    replacement = CustomChannel()
    with ThreadPoolExecutor(max_workers=2) as pool:
        initial = pool.submit(ensure_default_registered)
        try:
            assert entered.wait(3)
            # Registration must not wait for construction or its resources.
            pool.submit(register_converse_channel, replacement).result(timeout=2)
        finally:
            release.set()
        initial.result(timeout=2)
    assert get_converse_channel('korina') is replacement
    assert get_converse_channel() is selected


def test_concurrent_initializers_keep_the_first_published_default_identity(monkeypatch):
    entered = [threading.Event(), threading.Event()]
    release = [threading.Event(), threading.Event()]
    lock, count = threading.Lock(), 0
    original = korina_channel.KorinaConverseChannel

    class HeldDefault(original):
        def __init__(self):
            nonlocal count
            with lock:
                index = count
                count += 1
            entered[index].set()
            assert release[index].wait(3), 'test must release both default candidates'
            super().__init__()

    monkeypatch.setattr(korina_channel, 'KorinaConverseChannel', HeldDefault)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(ensure_default_registered)
        try:
            assert entered[0].wait(3)
            second = pool.submit(ensure_default_registered)
            assert entered[1].wait(3)
            release[1].set()
            second.result(timeout=2)
            winner = get_converse_channel('korina')
        finally:
            for event in release:
                event.set()
        first.result(timeout=2)
    assert get_converse_channel('korina') is winner
    assert get_converse_channel() is winner


@pytest.mark.parametrize('falsey', [False, True])
def test_ensure_preserves_existing_explicit_channel_without_constructing_default(monkeypatch, falsey):
    class FalseyChannel(CustomChannel):
        def __bool__(self):
            return False

    explicit = FalseyChannel() if falsey else CustomChannel()
    register_converse_channel(explicit)

    def forbidden():
        pytest.fail('an existing named channel must not construct a default candidate')

    monkeypatch.setattr(korina_channel, 'KorinaConverseChannel', forbidden)
    ensure_default_registered()
    ensure_default_registered()
    assert get_converse_channel('korina') is explicit


def test_legacy_reset_hook_remains_callable_with_registry_reset():
    ensure_default_registered()
    previous = get_converse_channel('korina')
    reset_for_testing()
    reset_default_registered()
    ensure_default_registered()
    assert get_converse_channel('korina') is not previous


def test_explicit_replacement_still_wins_after_default_registration():
    ensure_default_registered()
    first, last = CustomChannel(), CustomChannel()
    register_converse_channel(first)
    register_converse_channel(last)
    ensure_default_registered()
    assert get_converse_channel('korina') is last
