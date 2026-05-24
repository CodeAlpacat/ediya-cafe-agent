"""SessionStore 단위 테스트."""
from __future__ import annotations

import time

import pytest

from app.services.session_store import SessionStore, MAX_SESSIONS


def test_get_or_create_returns_same_state_on_repeat():
    store = SessionStore()
    s1 = store.get_or_create("a")
    s2 = store.get_or_create("a")
    assert s1 is s2


def test_get_or_create_distinct_for_different_ids():
    store = SessionStore()
    a = store.get_or_create("a")
    b = store.get_or_create("b")
    assert a is not b


def test_has_returns_true_only_after_creation():
    store = SessionStore()
    assert store.has("x") is False
    store.get_or_create("x")
    assert store.has("x") is True


def test_reset_removes_session_and_reports_existed():
    store = SessionStore()
    store.get_or_create("a")
    assert store.reset("a") is True
    assert store.has("a") is False
    assert store.reset("a") is False


def test_reset_all_returns_count():
    store = SessionStore()
    store.get_or_create("a")
    store.get_or_create("b")
    store.get_or_create("c")
    n = store.reset_all()
    assert n == 3
    assert len(store) == 0


def test_cleanup_expired_removes_only_old_sessions():
    store = SessionStore()
    state_old = store.get_or_create("old")
    state_old.last_activity = time.time() - 7200  # 2시간 전
    store.get_or_create("fresh")
    removed = store.cleanup_expired(timeout=3600)
    assert removed == 1
    assert store.has("old") is False
    assert store.has("fresh") is True


def test_evicts_oldest_when_max_sessions_reached():
    store = SessionStore()
    # MAX_SESSIONS 까지 채우고 +1 추가 시 가장 오래된 게 제거되는지
    for i in range(MAX_SESSIONS):
        st = store.get_or_create(f"s{i}")
        st.last_activity = 1000 + i  # s0이 가장 오래됨
    assert len(store) == MAX_SESSIONS
    store.get_or_create("new")
    assert len(store) == MAX_SESSIONS
    assert store.has("s0") is False
    assert store.has("new") is True


def test_cart_state_persists_across_get_or_create():
    store = SessionStore()
    state = store.get_or_create("a")
    state.cart.add(menu="아이스아메리카노", quantity=2, options=[])
    again = store.get_or_create("a")
    assert len(again.cart.snapshot()) == 1
    assert again.cart.snapshot()[0]["quantity"] == 2
