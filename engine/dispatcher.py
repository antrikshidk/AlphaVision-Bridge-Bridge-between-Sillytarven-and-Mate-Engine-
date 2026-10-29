"""
engine/dispatcher.py  — AlphaVision Delivery Pipeline
======================================================
Receives spontaneous remarks from brain.py and pushes them into the
MateEngine speech-bubble / lip-sync pipeline via:

  1. POST :8787/api/chat  (primary — triggers ChatBot.DisplayAIResponse in the
                            patched Assembly-CSharp.dll)
  2. Internal spontaneous_thought_queue  (secondary — picked up by the
                            /spontaneous_pop Flask endpoint already in
                            AnimeVision.py so SillyTavern's extension also
                            sees the thought)

Guarantees:
  • Non-blocking: every delivery runs on a short-lived daemon thread.
  • Rate-aware: respects the same min-interval guard as brain.py.
  • Graceful degradation: if MateEngine is unreachable the remark is still
    queued for the /spontaneous_pop endpoint.
  • Thread-safe queue hand-off with the main AnimeVision.py queue object.
"""

from __future__ import annotations

import logging
import queue
import threading
import time
from collections import deque
from typing import Optional, Any

import requests

logger = logging.getLogger("alphavision.dispatcher")

# ---------------------------------------------------------------------------
# Delivery constants
# ---------------------------------------------------------------------------
_MATEENGINE_TIMEOUT = 2.5      # seconds before we give up on the HTTP push
_RETRY_DELAY        = 1.5      # seconds before a single retry on network err
_CHARACTER_NAME     = "Alpha"
_DEFAULT_EMOTION    = "joy"


class ThoughtQueue:
    """
    Thread-safe, high-performance O(1) in-memory bounded queue wrapping collections.deque.
    Provides standard queue and deque interfaces (append, popleft, get, get_nowait, empty).
    Guarantees O(1) instant pops and auto-drops oldest elements if capacity exceeds maxlen.
    """

    def __init__(self, maxlen: int = 10):
        self._deque: deque[str] = deque(maxlen=maxlen)
        self._lock = threading.Lock()

    def append(self, item: str):
        with self._lock:
            if item in self._deque:
                return
            self._deque.append(item)

    def put(self, item: str, *args, **kwargs):
        self.append(item)

    def put_nowait(self, item: str):
        self.append(item)

    def popleft(self) -> Optional[str]:
        with self._lock:
            return self._deque.popleft() if self._deque else None

    def pop(self) -> Optional[str]:
        return self.popleft()

    def get_nowait(self) -> str:
        with self._lock:
            if not self._deque:
                raise queue.Empty()
            return self._deque.popleft()

    def get(self, *args, **kwargs) -> str:
        return self.get_nowait()

    def empty(self) -> bool:
        with self._lock:
            return len(self._deque) == 0

    def full(self) -> bool:
        return False  # Bounded deque auto-evicts oldest, never raises queue.Full

    def __len__(self) -> int:
        with self._lock:
            return len(self._deque)

    def __bool__(self) -> bool:
        with self._lock:
            return len(self._deque) > 0


class Dispatcher:
    """
    Thread-safe delivery pipeline for spontaneous ambient remarks.

    Usage:
        dispatcher = Dispatcher(
            mateengine_port=8787,
            shared_thought_queue=spontaneous_thought_queue,   # from AnimeVision.py
        )
        dispatcher.push("Your GPU fan sounds like it's filing for bankruptcy.")
    """

    def __init__(
        self,
        mateengine_port: int = 8787,
        shared_thought_queue: Optional[queue.Queue] = None,
        min_interval_sec: float = 15.0,
        dedup_window_sec: float = 120.0,
    ):
        self._port              = mateengine_port
        self._ext_q             = shared_thought_queue    # AnimeVision.py's queue object
        self._min_interval_sec  = min_interval_sec
        self._dedup_window_sec  = dedup_window_sec
        self._lock              = threading.Lock()
        self._last_push: float  = 0.0
        self._last_dispatched_thought: Optional[tuple[str, float]] = None
        self._recent_dispatches: list[tuple[str, float]] = []

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def push(self, remark: str, emotion: str = _DEFAULT_EMOTION, force: bool = False) -> bool:
        """
        Non-blocking: spawns a daemon thread to deliver the remark so the
        brain's cognitive loop is never stalled by network I/O.

        Guards:
          • Strips candidate pipes ('|') and enforces <=15 words single sentence.
          • Rejects identical remark dispatched within `dedup_window_sec` (60s).
          • Rejects any spontaneous remark if dispatched within `min_interval_sec` (15s).

        Returns True if accepted; False if suppressed or empty.
        """
        if not remark or not str(remark).strip():
            return False

        from engine.brain import clean_thought, set_last_ambient_poke
        clean_remark = clean_thought(str(remark))
        if not clean_remark:
            return False

        # Register ambient poke in short-term context memory
        set_last_ambient_poke(clean_remark, "dispatcher")

        norm_key = " ".join(clean_remark.lower().split())
        now = time.time()

        with self._lock:
            # 0. Deduplicate against in-memory last_dispatched_thought cache within 120s sliding window
            if self._last_dispatched_thought is not None:
                last_text, last_ts = self._last_dispatched_thought
                if (now - last_ts) <= self._dedup_window_sec and norm_key == last_text:
                    logger.warning(
                        "[DISPATCHER DEDUP] Suppressed duplicate thought matching last_dispatched_thought within %.1fs: '%s'",
                        self._dedup_window_sec, clean_remark
                    )
                    return False

            # Purge records older than dedup window
            self._recent_dispatches = [
                (text, ts) for text, ts in self._recent_dispatches
                if (now - ts) <= self._dedup_window_sec
            ]

            if not force:
                # 1. Global throttle check: minimum 15s between ANY spontaneous message
                time_since_last = now - self._last_push
                if self._last_push > 0.0 and time_since_last < self._min_interval_sec:
                    logger.warning(
                        "[DISPATCHER DEDUP] Suppressed remark (throttled: %.1fs < %.1fs): '%s'",
                        time_since_last, self._min_interval_sec, clean_remark
                    )
                    return False

                # 2. Dedup check: identical message sent within sliding window
                for past_text, past_ts in self._recent_dispatches:
                    if past_text == norm_key:
                        logger.warning(
                            "[DISPATCHER DEDUP] Suppressed duplicate remark within %.1fs: '%s'",
                            self._dedup_window_sec, clean_remark
                        )
                        return False

            # Passed checks: register dispatch timestamp under lock & update last_dispatched_thought cache
            self._last_push = now
            self._last_dispatched_thought = (norm_key, now)
            self._recent_dispatches.append((norm_key, now))

        t = threading.Thread(
            target=self._deliver,
            args=(clean_remark, emotion),
            daemon=True,
            name="DispatcherDeliver",
        )
        t.start()
        return True

    # ------------------------------------------------------------------
    # Delivery logic
    # ------------------------------------------------------------------
    def _deliver(self, remark: str, emotion: str):
        # 1. Push to the shared spontaneous_thought_queue instantly (O(1))
        #    so /spontaneous_pop (AnimeVision.py / SillyTavern) receives it immediately.
        if self._ext_q is not None:
            try:
                if hasattr(self._ext_q, "append"):
                    self._ext_q.append(remark)
                elif hasattr(self._ext_q, "put_nowait"):
                    self._ext_q.put_nowait(remark)
                elif hasattr(self._ext_q, "put"):
                    self._ext_q.put(remark)
                logger.debug("[DISPATCHER] Queued for /spontaneous_pop: '%s'", remark)
            except Exception as exc:
                logger.warning("[DISPATCHER] Shared queue push failed: %s", exc)

        # 2. Attempt direct POST to MateEngine :8787/api/chat
        #    This triggers ChatBot.DisplayAIResponse and the active speech bubble in
        #    MateEngine's runtime immediately without waiting for SillyTavern to poll.
        payload = {
            "text":            remark,
            "message":         remark,
            "characterName":   _CHARACTER_NAME,
            "sender":          _CHARACTER_NAME,
            "emotion":         emotion,
            "triggerLipSync":  True,
            "showBubble":      True,
            "is_bubble":       True,
            "add_to_chat":     True,
            "spontaneous":     True,
        }

        url = f"http://127.0.0.1:{self._port}/api/chat"
        success = self._http_post(url, payload)

        if not success:
            # Single retry after short delay
            time.sleep(_RETRY_DELAY)
            success = self._http_post(url, payload)

        if success:
            logger.info("[DISPATCHER] ✓ Delivered to MateEngine: '%s'", remark)
            with self._lock:
                self._last_push = time.time()
        else:
            logger.warning(
                "[DISPATCHER] MateEngine unreachable — remark kept in queue only: '%s'", remark
            )

    @staticmethod
    def _http_post(url: str, payload: dict) -> bool:
        try:
            r = requests.post(url, json=payload, timeout=_MATEENGINE_TIMEOUT)
            return r.status_code < 400
        except requests.exceptions.ConnectionError:
            logger.debug("[DISPATCHER] Connection refused on %s", url)
            return False
        except requests.exceptions.Timeout:
            logger.debug("[DISPATCHER] Timeout on %s", url)
            return False
        except Exception as exc:
            logger.debug("[DISPATCHER] HTTP error on %s: %s", url, exc)
            return False

    # ------------------------------------------------------------------
    # Status
    # ------------------------------------------------------------------
    @property
    def last_dispatched_thought(self) -> Optional[tuple[str, float]]:
        with self._lock:
            return self._last_dispatched_thought

    @property
    def last_push_time(self) -> float:
        with self._lock:
            return self._last_push

    def seconds_since_last_push(self) -> float:
        return time.time() - self.last_push_time
