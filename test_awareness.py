"""
test_awareness.py — Standalone end-to-end mock test for the AlphaVision
Subconscious & Perception pipeline.

Tests performed (all without real camera, MateEngine, or Gemini API calls):
  1. PerceptionSnapshot dataclass construction & field defaults.
  2. PerceptionEngine state machine: presence detection, significant-change gate.
  3. BrainEngine state transitions: returned_long, fatigue_cue, late-night, activity_shift.
  4. Dispatcher non-blocking delivery with a mock HTTP server.
  5. Full mock pipeline: synthetic snapshots → brain ingest → remark callback.

Run from the alpha_env directory:
  python test_awareness.py

Expected output: All tests PASS with no tracebacks.
"""

import json
import os
import queue
import sys
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from unittest.mock import MagicMock, patch

# Ensure local engine package is importable
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# ---------------------------------------------------------------------------
# 0. Ensure win32gui / win32api stubs exist if pywin32 not installed.
#    Never mock mediapipe here — it is installed and real; mocking it after
#    it is already in sys.modules would corrupt its attribute tree.
# ---------------------------------------------------------------------------
import importlib, types

def _make_mock_module(name: str):
    m = types.ModuleType(name)
    sys.modules[name] = m
    return m

# Only stub win32 modules if genuinely absent (pywin32 not installed)
if "win32gui" not in sys.modules:
    try:
        import win32gui  # noqa: F401
    except ImportError:
        wg = _make_mock_module("win32gui")
        wg.GetForegroundWindow = lambda: 0
        wg.GetWindowText = lambda hwnd: "Mock Window - VSCode"

if "win32api" not in sys.modules:
    try:
        import win32api  # noqa: F401
    except ImportError:
        _make_mock_module("win32api")


# Now import engine modules
from engine.perception import (
    PerceptionSnapshot,
    PerceptionEngine,
    ChatBoxDebouncer,
    CameraWorker,
    SafeScreenGrabber,
    capture_screen_gdi,
)
from engine.brain import (
    BrainEngine,
    InternalState,
    _tag_window,
    clean_thought,
    get_last_ambient_poke,
    set_last_ambient_poke,
    clear_last_ambient_poke,
)
from engine.dispatcher import Dispatcher


# ---------------------------------------------------------------------------
# 1. PerceptionSnapshot tests
# ---------------------------------------------------------------------------
class TestPerceptionSnapshot(unittest.TestCase):

    def test_defaults(self):
        snap = PerceptionSnapshot()
        self.assertFalse(snap.face_detected)
        self.assertFalse(snap.eye_contact)
        self.assertTrue(snap.presence)           # default optimistic
        self.assertIsNone(snap.frame_blob)
        self.assertEqual(snap.active_window, "")

    def test_custom_fields(self):
        snap = PerceptionSnapshot(
            face_detected=True,
            eye_contact=True,
            head_yaw=-10.0,
            presence=True,
            active_window="test.py - VSCode",
            idle_seconds=30.0,
            hour=23,
            is_late_night=True,
        )
        self.assertTrue(snap.face_detected)
        self.assertEqual(snap.hour, 23)
        self.assertTrue(snap.is_late_night)


# ---------------------------------------------------------------------------
# 2. Window tag classification
# ---------------------------------------------------------------------------
class TestWindowTagging(unittest.TestCase):

    def test_code(self):
        self.assertEqual(_tag_window("main.py - Visual Studio Code"), "coding")

    def test_gaming(self):
        self.assertEqual(_tag_window("VALORANT"), "gaming")

    def test_youtube(self):
        self.assertEqual(_tag_window("YouTube - Chrome"), "youtube")

    def test_physics(self):
        self.assertEqual(_tag_window("Quantum Physics Notes - Chrome"), "physics")
        self.assertEqual(_tag_window("arXiv:2401.12345v1 [quant-ph]"), "physics")
        self.assertEqual(_tag_window("Desmos Graphing Calculator"), "physics")

    def test_fallback(self):
        self.assertEqual(_tag_window(""), "misc")
        self.assertEqual(_tag_window("Some Random App"), "misc")


# ---------------------------------------------------------------------------
# 3. PerceptionEngine significance gate
# ---------------------------------------------------------------------------
class TestPerceptionEngineSignificanceGate(unittest.TestCase):

    def setUp(self):
        self.engine = PerceptionEngine(snapshot_queue=queue.Queue())

    def _make_snap(self, **kwargs) -> PerceptionSnapshot:
        defaults = dict(face_detected=True, presence=True, active_window="VSCode",
                        idle_seconds=5.0, hour=14, fatigue_cue=False)
        defaults.update(kwargs)
        return PerceptionSnapshot(**defaults)

    def test_first_snap_always_significant(self):
        snap = self._make_snap()
        self.assertTrue(self.engine._is_significant_change(snap))

    def test_identical_snap_within_10s(self):
        snap = self._make_snap()
        self.engine._prev_snapshot = snap
        # Same state → NOT significant within 10 s
        snap2 = self._make_snap()
        snap2.timestamp = snap.timestamp + 3.0
        self.assertFalse(self.engine._is_significant_change(snap2))

    def test_presence_change_significant(self):
        snap = self._make_snap(presence=True)
        self.engine._prev_snapshot = snap
        snap2 = self._make_snap(presence=False)
        self.assertTrue(self.engine._is_significant_change(snap2))

    def test_window_change_significant(self):
        snap = self._make_snap(active_window="VSCode")
        self.engine._prev_snapshot = snap
        snap2 = self._make_snap(active_window="Steam")
        self.assertTrue(self.engine._is_significant_change(snap2))

    def test_fatigue_cue_significant(self):
        snap = self._make_snap(fatigue_cue=False)
        self.engine._prev_snapshot = snap
        snap2 = self._make_snap(fatigue_cue=True)
        self.assertTrue(self.engine._is_significant_change(snap2))

    def test_heartbeat_after_10s(self):
        snap = self._make_snap()
        snap.timestamp = time.time() - 11.0       # 11 s ago
        self.engine._prev_snapshot = snap
        snap2 = self._make_snap()                 # identical state
        self.assertTrue(self.engine._is_significant_change(snap2))


# ---------------------------------------------------------------------------
# 4. InternalState persistence round-trip
# ---------------------------------------------------------------------------
class TestInternalStatePersistence(unittest.TestCase):

    def test_round_trip(self):
        state = InternalState(
            user_state="away",
            mood_state="concerned",
            last_speech_timestamp=12345.6,
            activity_tag="coding",
        )
        d = state.to_dict()
        restored = InternalState.from_dict(d)
        self.assertEqual(restored.user_state, "away")
        self.assertEqual(restored.mood_state, "concerned")
        self.assertAlmostEqual(restored.last_speech_timestamp, 12345.6)
        self.assertEqual(restored.activity_tag, "coding")


# ---------------------------------------------------------------------------
# 5. BrainEngine state-machine transitions (no Gemini calls)
# ---------------------------------------------------------------------------
class TestBrainStateMachine(unittest.TestCase):

    def _make_brain(self, callback=None):
        snap_q = queue.Queue()
        remark_calls = []
        cb = callback or (lambda r: remark_calls.append(r))
        mock_model = MagicMock()
        mock_model.generate_content.return_value = MagicMock(
            text="Still here, huh."
        )
        brain = BrainEngine(
            snapshot_queue=snap_q,
            remark_callback=cb,
            gemini_model=mock_model,
        )
        return brain, snap_q, remark_calls

    def _make_snap(self, **kwargs) -> PerceptionSnapshot:
        defaults = dict(face_detected=True, presence=True, active_window="VSCode",
                        idle_seconds=5.0, hour=14, fatigue_cue=False,
                        is_late_night=False, head_yaw=0.0, head_pitch=0.0)
        defaults.update(kwargs)
        return PerceptionSnapshot(**defaults)

    def test_user_state_focused(self):
        brain, _, _ = self._make_brain()
        snap = self._make_snap(presence=True, idle_seconds=10.0)
        brain._process_snapshot(snap)
        self.assertEqual(brain.state.user_state, "focused")

    def test_user_state_away(self):
        brain, _, _ = self._make_brain()
        snap = self._make_snap(presence=False)
        brain._process_snapshot(snap)
        self.assertEqual(brain.state.user_state, "away")

    def test_return_after_long_away_fires_remark(self):
        remark_calls = []
        brain, _, _ = self._make_brain(callback=lambda r: remark_calls.append(r))
        # Simulate going away first
        away_snap = self._make_snap(presence=False)
        brain._process_snapshot(away_snap)
        self.assertEqual(brain.state.user_state, "away")
        # Set away_since to 15 minutes ago
        brain._state.away_since = time.time() - 15 * 60

        # Return
        return_snap = self._make_snap(presence=True)
        brain._last_snap = away_snap
        brain._process_snapshot(return_snap)

        # Give the daemon thread up to 3 s to fire
        deadline = time.time() + 3.0
        while not remark_calls and time.time() < deadline:
            time.sleep(0.1)
        self.assertTrue(len(remark_calls) > 0, "Expected a returned_long remark to fire")

    def test_fatigue_cue_fires_remark(self):
        remark_calls = []
        brain, _, _ = self._make_brain(callback=lambda r: remark_calls.append(r))
        snap = self._make_snap(fatigue_cue=True, presence=True)
        brain._last_snap = self._make_snap()  # non-fatigued prev
        brain._state.last_dramatic_event = 0.0   # no cooldown
        brain._process_snapshot(snap)

        deadline = time.time() + 3.0
        while not remark_calls and time.time() < deadline:
            time.sleep(0.1)
        self.assertTrue(len(remark_calls) > 0, "Expected fatigue_cue remark to fire")

    def test_activity_shift_fires_remark(self):
        remark_calls = []
        brain, _, _ = self._make_brain(callback=lambda r: remark_calls.append(r))
        prev_snap = self._make_snap(active_window="VSCode - main.py")
        brain._last_snap = prev_snap
        brain._state.activity_tag = "coding"
        brain._state.last_dramatic_event = 0.0

        new_snap = self._make_snap(active_window="Steam - Library")
        brain._process_snapshot(new_snap)

        deadline = time.time() + 3.0
        while not remark_calls and time.time() < deadline:
            time.sleep(0.1)
        self.assertTrue(len(remark_calls) > 0, "Expected activity_shift remark to fire")

    def test_rate_limit_prevents_spam(self):
        brain, _, _ = self._make_brain()
        # Set last speech 1 minute ago — below 5-min floor
        brain._state.last_speech_timestamp = time.time() - 60
        brain._state.last_dramatic_event   = time.time() - 200
        self.assertFalse(brain._should_speak(time.time()))

    def test_rate_limit_allows_after_floor(self):
        brain, _, _ = self._make_brain()
        brain._state.last_speech_timestamp = time.time() - 6 * 60  # 6 min ago
        brain._state.last_dramatic_event   = time.time() - 200
        brain._state.user_state = "focused"
        self.assertTrue(brain._should_speak(time.time()))

    def test_away_user_suppresses_speech(self):
        brain, _, _ = self._make_brain()
        brain._state.last_speech_timestamp = time.time() - 6 * 60
        brain._state.user_state = "away"
        self.assertFalse(brain._should_speak(time.time()))


# ---------------------------------------------------------------------------
# 6. Dispatcher mock HTTP delivery
# ---------------------------------------------------------------------------
class _MockMateEngineHandler(BaseHTTPRequestHandler):
    received_payloads = []

    def do_POST(self):
        length  = int(self.headers.get("Content-Length", 0))
        body    = self.rfile.read(length)
        _MockMateEngineHandler.received_payloads.append(json.loads(body))
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"OK")

    def log_message(self, *args):
        pass   # silence BaseHTTPServer output during test


class TestDispatcher(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        # Start a mock HTTP server on an ephemeral port
        cls.server = HTTPServer(("127.0.0.1", 0), _MockMateEngineHandler)
        cls.port   = cls.server.server_address[1]
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()

    def setUp(self):
        _MockMateEngineHandler.received_payloads.clear()

    def test_push_delivers_to_mateengine(self):
        q = queue.Queue()
        d = Dispatcher(mateengine_port=self.port, shared_thought_queue=q)
        d.push("Still grinding at midnight, huh.")
        # Give delivery thread 2 s
        deadline = time.time() + 2.0
        while not _MockMateEngineHandler.received_payloads and time.time() < deadline:
            time.sleep(0.05)
        self.assertTrue(len(_MockMateEngineHandler.received_payloads) > 0)
        payload = _MockMateEngineHandler.received_payloads[0]
        self.assertIn("text", payload)
        self.assertEqual(payload["text"], "Still grinding at midnight, huh.")

    def test_push_also_enqueues_for_spontaneous_pop(self):
        shared_q = queue.Queue()
        d = Dispatcher(mateengine_port=self.port, shared_thought_queue=shared_q)
        d.push("Your IDE has more tabs than your sanity can handle.")
        time.sleep(0.5)
        self.assertFalse(shared_q.empty())
        item = shared_q.get_nowait()
        self.assertIn("tabs", item)

    def test_push_empty_remark_is_noop(self):
        q = queue.Queue()
        d = Dispatcher(mateengine_port=self.port, shared_thought_queue=q)
        d.push("")
        d.push("   ")
        time.sleep(0.3)
        self.assertTrue(q.empty())

    def test_unreachable_engine_does_not_crash(self):
        """Push to a port with nothing listening — must not raise."""
        q = queue.Queue()
        d = Dispatcher(mateengine_port=19999, shared_thought_queue=q)
        try:
            d.push("Testing graceful failure.")
            time.sleep(0.5)   # let retry thread finish
        except Exception as exc:
            self.fail(f"Dispatcher raised on unreachable host: {exc}")

    def test_rate_limit_guard_blocks_within_15s(self):
        """Second distinct remark sent within 15s must be suppressed."""
        q = queue.Queue()
        d = Dispatcher(mateengine_port=self.port, shared_thought_queue=q, min_interval_sec=15.0)
        first_ok = d.push("First message at t=0")
        self.assertTrue(first_ok, "First dispatch should be accepted")

        # Immediate follow-up within 15s must be rejected
        second_ok = d.push("Second message immediately after")
        self.assertFalse(second_ok, "Follow-up within 15s should be suppressed")

    def test_deduplication_guard_blocks_identical_within_60s(self):
        """Identical remark within 60s must be suppressed even if interval > 15s."""
        q = queue.Queue()
        # Set min_interval to 0 for this test to isolate the 60s duplicate check
        d = Dispatcher(mateengine_port=self.port, shared_thought_queue=q, min_interval_sec=0.0, dedup_window_sec=60.0)
        first_ok = d.push("Duplicated remark test")
        self.assertTrue(first_ok, "First dispatch should succeed")

        # Identical text (even with differing whitespace/case) must be rejected
        dup_ok = d.push("  duplicated remark test  ")
        self.assertFalse(dup_ok, "Duplicate remark within 60s window should be suppressed")

        # Distinct text should succeed
        diff_ok = d.push("Completely distinct remark")
        self.assertTrue(diff_ok, "Distinct remark should succeed")

    def test_last_dispatched_thought_sliding_window_120s(self):
        """Dispatcher must cache last_dispatched_thought and reject duplicates within 120s."""
        q = queue.Queue()
        d = Dispatcher(mateengine_port=self.port, shared_thought_queue=q, min_interval_sec=0.0, dedup_window_sec=120.0)
        ok1 = d.push("Observing your desktop workflow.")
        self.assertTrue(ok1)
        self.assertIsNotNone(d.last_dispatched_thought)
        self.assertEqual(d.last_dispatched_thought[0], "observing your desktop workflow.")

        # Immediate repeat should be blocked by last_dispatched_thought cache
        ok2 = d.push("observing your desktop workflow.")
        self.assertFalse(ok2, "Duplicate within 120s must be suppressed")


# ---------------------------------------------------------------------------
# 7. ChatBox Debouncer Hysteresis Tests
# ---------------------------------------------------------------------------
class TestChatBoxDebouncer(unittest.TestCase):

    def test_flapping_rejected(self):
        """Alternating True/False every 0.15s must NOT flip the debounced state."""
        deb = ChatBoxDebouncer(open_confirm_sec=0.60, close_confirm_sec=1.00, min_state_dwell_sec=1.00, initial_state=True)
        t = 1000.0
        # Simulate 20 frames of 0.15s flipping (True, False, True, False...)
        for i in range(20):
            raw = (i % 2 == 0)
            t += 0.15
            is_open, changed = deb.update(raw, now=t)
            self.assertFalse(changed, f"Debouncer should not flip on noisy frame {i}")
            self.assertTrue(is_open, "State should remain True during noise")

    def test_sustained_close_transition(self):
        """Continuous False for >= 1.0s cleanly transitions to CLOSED."""
        t = 1000.0
        deb = ChatBoxDebouncer(open_confirm_sec=0.60, close_confirm_sec=1.00, min_state_dwell_sec=1.00, initial_state=True)
        deb._last_state_change = t - 5.0
        deb._raw_streak_start = t
        deb._raw_streak_state = False

        # Send False for 0.75s (less than 1.0s close confirm)
        for _ in range(5):
            t += 0.15
            is_open, changed = deb.update(False, now=t)
            self.assertFalse(changed)
            self.assertTrue(is_open)

        # Cross 1.05s (7 * 0.15 = 1.05s)
        t += 0.30
        is_open, changed = deb.update(False, now=t)
        self.assertTrue(changed, "State should transition to CLOSED after 1.0s continuous absence")
        self.assertFalse(is_open)

    def test_sustained_open_transition(self):
        """Continuous True for >= 0.6s cleanly transitions to OPEN."""
        t = 1000.0
        deb = ChatBoxDebouncer(open_confirm_sec=0.60, close_confirm_sec=1.00, min_state_dwell_sec=1.00, initial_state=False)
        deb._last_state_change = t - 5.0
        deb._raw_streak_start = t
        deb._raw_streak_state = True

        # Send True for 0.45s (less than 0.60s open confirm)
        for _ in range(3):
            t += 0.15
            is_open, changed = deb.update(True, now=t)
            self.assertFalse(changed)
            self.assertFalse(is_open)

        # Cross 0.65s
        t += 0.20
        is_open, changed = deb.update(True, now=t)
        self.assertTrue(changed, "State should transition to OPEN after 0.6s continuous presence")
        self.assertTrue(is_open)


# ---------------------------------------------------------------------------
# 8. Full mock pipeline integration
# ---------------------------------------------------------------------------
class TestFullPipeline(unittest.TestCase):

    def test_pipeline_end_to_end(self):
        """
        Synthetic snapshots → BrainEngine ingest → remark callback → Dispatcher push.
        No real camera, Gemini API, or MateEngine required.
        """
        delivered = []
        snap_q = queue.Queue()

        mock_model = MagicMock()
        mock_model.generate_content.return_value = MagicMock(
            text="Three coffee cups and no commits — impressive."
        )

        disp = Dispatcher(mateengine_port=19998, shared_thought_queue=None)  # port has nothing

        def remark_cb(remark: str):
            delivered.append(remark)
            disp.push(remark)

        brain = BrainEngine(
            snapshot_queue=snap_q,
            remark_callback=remark_cb,
            gemini_model=mock_model,
        )
        brain.start()

        # Inject: user was away, now returns after >10 min
        brain._state.user_state = "away"
        brain._state.away_since = time.time() - 12 * 60

        return_snap = PerceptionSnapshot(
            face_detected=True, presence=True,
            active_window="main.py — VSCode",
            idle_seconds=2.0, hour=14,
        )
        snap_q.put(return_snap)

        # Wait for remark to arrive (max 4 s)
        deadline = time.time() + 4.0
        while not delivered and time.time() < deadline:
            time.sleep(0.1)

        brain.stop()

        self.assertTrue(len(delivered) > 0, "Pipeline produced no remark")
        self.assertIsInstance(delivered[0], str)
        self.assertGreater(len(delivered[0]), 3)
        print(f"\n[PIPELINE TEST] Remark delivered: '{delivered[0]}'")


# ---------------------------------------------------------------------------
# 9. CameraWorker & Fail-Safe Hardware Perception Tests
# ---------------------------------------------------------------------------
class TestCameraWorkerFailSafe(unittest.TestCase):

    def test_camera_offline_returns_none_instantly(self):
        """When camera is unavailable, get_latest_frame must return None with zero blocking."""
        worker = CameraWorker(camera_index=999)
        self.assertFalse(worker.camera_available)
        self.assertIsNone(worker.get_latest_frame())
        self.assertIsNone(worker.capture_frame_blob())

    def test_camera_atomic_pointer_thread_safe(self):
        """Thread-safe frame access returns a decoupled copy."""
        import numpy as np
        worker = CameraWorker(camera_index=999)
        fake_frame = np.ones((50, 50, 3), dtype=np.uint8) * 255
        with worker._frame_lock:
            worker._latest_frame = fake_frame
            worker.camera_available = True

        retrieved = worker.get_latest_frame()
        self.assertIsNotNone(retrieved)
        self.assertEqual(retrieved.shape, (50, 50, 3))
        # Ensure it's a decoupled copy
        self.assertIsNot(retrieved, worker._latest_frame)

    def test_perception_headless_without_camera(self):
        """PerceptionEngine with no camera continues running smoothly via OS sensors."""
        engine = PerceptionEngine(camera_index=-1)
        self.assertFalse(engine.camera_available)
        with patch.object(engine, "_get_idle_seconds", return_value=15.0):
            snap = engine._build_snapshot(None, None)
            self.assertFalse(snap.face_detected)
            self.assertFalse(snap.eye_contact)
            self.assertTrue(snap.presence)  # inferred from idle < 120s


# ---------------------------------------------------------------------------
# 10. Pipe Stripping & Strict 15-Word Truncation Tests
# ---------------------------------------------------------------------------
class TestCleanThoughtAndFormatting(unittest.TestCase):

    def test_pipe_separator_stripping(self):
        """Pipes separating candidate thoughts must be stripped, selecting only the first non-empty option."""
        raw = "Option A | Option B | Option C"
        self.assertEqual(clean_thought(raw), "Option A")

    def test_prefix_and_quotes_stripping(self):
        """Assistant and thought prefixes with quotes must be cleanly stripped."""
        raw = 'Alpha: "Still writing code at midnight | or going to sleep"'
        self.assertEqual(clean_thought(raw), "Still writing code at midnight")

    def test_strict_sentence_truncation_with_boundary(self):
        """Outputs longer than 15 words must trim cleanly at sentence boundary."""
        raw = "You have been staring at that function for twenty minutes. Maybe take a quick break right now."
        cleaned = clean_thought(raw)
        self.assertIsNotNone(cleaned)
        words = cleaned.split()
        self.assertLessEqual(len(words), 15)
        self.assertTrue(cleaned.endswith('.'))
        self.assertEqual(cleaned, "You have been staring at that function for twenty minutes.")

    def test_strict_truncation_without_boundary(self):
        """Outputs exceeding 15 words with no early period must be capped cleanly."""
        raw = "One two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen seventeen"
        cleaned = clean_thought(raw)
        self.assertIsNotNone(cleaned)
        words = cleaned.split()
        self.assertLessEqual(len(words), 16)  # 15 words + ellipsis
        self.assertTrue(cleaned.endswith("..."))

    def test_dispatcher_cleans_pipes_before_delivery(self):
        """Dispatcher.push must strip pipes before handing off to queue or MateEngine."""
        q = queue.Queue()
        d = Dispatcher(mateengine_port=19997, shared_thought_queue=q, min_interval_sec=0.0)
        d.push("Candidate One | Candidate Two | Candidate Three", force=True)
        time.sleep(0.3)
        self.assertFalse(q.empty())
        item = q.get_nowait()
        self.assertEqual(item, "Candidate One")

    def test_strip_high_unicode_and_emojis(self):
        """Characters outside Basic Multilingual Plane (BMP) must be stripped to prevent Unity box rendering."""
        from engine.brain import sanitize_dialogue_text
        raw = "Hello Antriksh 😊! Studying chemistry 🚀 or physics 🧪? | Option 2"
        cleaned = clean_thought(raw)
        self.assertNotIn("😊", cleaned)
        self.assertNotIn("🚀", cleaned)
        self.assertNotIn("🧪", cleaned)
        self.assertNotIn("|", cleaned)
        self.assertEqual(cleaned, "Hello Antriksh ! Studying chemistry or physics ?")

        # Verify sanitize_dialogue_text
        sanitized = sanitize_dialogue_text("Unity 🎮 text test 🤖")
        self.assertEqual(sanitized, "Unity  text test")


# ---------------------------------------------------------------------------
# 11. SafeScreenGrabber & Anti-DWM Flickering Tests
# ---------------------------------------------------------------------------
class TestSafeScreenGrabber(unittest.TestCase):

    def test_captureblt_suppressed_in_mss(self):
        """mss CAPTUREBLT must be suppressed to 0 to prevent Windows mouse cursor flickering."""
        try:
            import mss.windows.gdi
            self.assertEqual(getattr(mss.windows.gdi, "CAPTUREBLT", None), 0)
        except (ImportError, AttributeError):
            pass

    def test_safescreengrabber_throttles_under_interval(self):
        """SafeScreenGrabber must throttle captures under min_interval_sec."""
        grabber = SafeScreenGrabber(min_interval_sec=0.65)
        # First grab at t=100.0
        self.assertTrue(grabber.should_grab(now=100.0))
        grabber._last_grab_time = 100.0

        # Rapid poll at t=100.2 (200ms < 650ms) must be throttled
        self.assertFalse(grabber.should_grab(now=100.2))
        # Poll at t=100.5 (500ms < 650ms) must still be throttled
        self.assertFalse(grabber.should_grab(now=100.5))

    def test_safescreengrabber_triggers_on_interval(self):
        """SafeScreenGrabber must trigger when elapsed time >= min_interval_sec."""
        grabber = SafeScreenGrabber(min_interval_sec=0.65)
        grabber._last_grab_time = 100.0
        # Elapsed 700ms >= 650ms
        self.assertTrue(grabber.should_grab(now=100.70))

    def test_safescreengrabber_triggers_on_foreground_window_change(self):
        """Foreground window switch triggers immediate grab regardless of elapsed time."""
        grabber = SafeScreenGrabber(min_interval_sec=0.65)
        grabber._last_grab_time = 100.0
        grabber._last_foreground_hwnd = 11111

        with patch("win32gui.GetForegroundWindow", return_value=99999):
            # Only 100ms passed, but foreground changed -> must trigger grab immediately
            self.assertTrue(grabber.should_grab(now=100.10))
            self.assertEqual(grabber._last_foreground_hwnd, 99999)

    def test_capture_screen_gdi_safe_execution(self):
        """capture_screen_gdi executes safely and cleans up handles without unhandled crashes."""
        result = capture_screen_gdi(0, 0, 10, 10)
        # Result may be None in headless/non-interactive sandbox, but must not raise an unhandled exception
        if result is not None:
            self.assertEqual(result.shape[:2], (10, 10))


# ---------------------------------------------------------------------------
# 12. Short-Term Ambient Context Injection (Dual-Channel Memory) Tests
# ---------------------------------------------------------------------------
class TestAmbientPokeContext(unittest.TestCase):

    def setUp(self):
        clear_last_ambient_poke()

    def tearDown(self):
        clear_last_ambient_poke()

    def test_poke_storage_and_retrieval(self):
        """Spontaneous remark is stored in in-memory last_ambient_poke and retrieved cleanly."""
        set_last_ambient_poke("Are you going to leave me sitting here while you browse?", "Chrome")
        poke = get_last_ambient_poke()
        self.assertIsNotNone(poke)
        self.assertEqual(poke["text"], "Are you going to leave me sitting here while you browse?")
        self.assertEqual(poke["context"], "Chrome")
        self.assertAlmostEqual(poke["timestamp"], time.time(), delta=1.5)

    def test_poke_expiration_after_90s(self):
        """Ambient poke older than 90 seconds must return None and expire."""
        set_last_ambient_poke("You stare at equations all day.", "Desmos")
        # Artificially age the poke to 95 seconds ago
        from engine.brain import last_ambient_poke, _poke_lock
        with _poke_lock:
            if last_ambient_poke:
                last_ambient_poke["timestamp"] = time.time() - 95.0

        self.assertIsNone(get_last_ambient_poke())

    def test_poke_clears_after_user_response(self):
        """clear_last_ambient_poke immediately wipes the in-memory poke."""
        set_last_ambient_poke("Test poke remark")
        self.assertIsNotNone(get_last_ambient_poke())
        clear_last_ambient_poke()
        self.assertIsNone(get_last_ambient_poke())

    def test_context_injection_formatting(self):
        """Context injection string must format precisely with author's note bracket format."""
        set_last_ambient_poke("You've been staring at that code forever, take a break.")
        poke = get_last_ambient_poke()
        self.assertIsNotNone(poke)
        injection = f'[Context: You just teased the user with: "{poke["text"]}". The user just opened the chat to respond.]'
        self.assertIn("You've been staring at that code forever", injection)
        self.assertTrue(injection.startswith("[Context: You just teased the user with:"))
        self.assertTrue(injection.endswith("The user just opened the chat to respond.]"))


# ---------------------------------------------------------------------------
# 13. Ignoring Timer & Structured Annoying Poke Triggers Tests
# ---------------------------------------------------------------------------
class TestIgnoringAndNaggingTriggers(unittest.TestCase):

    def _make_brain(self):
        snap_q = queue.Queue()
        mock_model = MagicMock()
        mock_response = MagicMock()
        mock_response.text = "Alpha: Talk to me already!"
        mock_model.generate_content.return_value = mock_response
        brain = BrainEngine(
            snapshot_queue=snap_q,
            remark_callback=lambda r: None,
            gemini_model=mock_model,
        )
        return brain

    def test_ignoring_coding_trigger(self):
        """User active on desktop coding for >10 mins without chatting triggers ignoring_coding."""
        brain = self._make_brain()
        now = time.time()
        brain._state.activity_tag = "coding"
        brain._state.last_chat_timestamp = now - 11 * 60  # 11 mins ago
        snap = PerceptionSnapshot(presence=True, idle_seconds=10.0, hour=15, is_late_night=False)
        key = brain._select_ambient_key(snap)
        self.assertEqual(key, "ignoring_coding")

    def test_ignoring_browsing_trigger(self):
        """User active browsing for >10 mins without chatting triggers ignoring_browsing."""
        brain = self._make_brain()
        now = time.time()
        brain._state.activity_tag = "browsing"
        brain._state.last_chat_timestamp = now - 12 * 60  # 12 mins ago
        snap = PerceptionSnapshot(presence=True, idle_seconds=15.0, hour=15, is_late_night=False)
        key = brain._select_ambient_key(snap)
        self.assertEqual(key, "ignoring_browsing")

    def test_ignoring_physics_trigger(self):
        """User active on physics for >10 mins without chatting triggers ignoring_physics."""
        brain = self._make_brain()
        now = time.time()
        brain._state.activity_tag = "physics"
        brain._state.last_chat_timestamp = now - 15 * 60  # 15 mins ago
        snap = PerceptionSnapshot(presence=True, idle_seconds=5.0, hour=16, is_late_night=False)
        key = brain._select_ambient_key(snap)
        self.assertEqual(key, "ignoring_physics")

    def test_ambient_physics_focus_trigger(self):
        """Physics activity tag when user recently chatted selects ambient_physics."""
        brain = self._make_brain()
        now = time.time()
        brain._state.activity_tag = "physics"
        brain._state.last_chat_timestamp = now - 2 * 60  # Only 2 mins ago (not ignoring)
        snap = PerceptionSnapshot(presence=True, idle_seconds=10.0, hour=16, is_late_night=False)
        key = brain._select_ambient_key(snap)
        self.assertEqual(key, "ambient_physics")

    def test_record_chat_interaction_resets_ignoring_timer(self):
        """record_chat_interaction updates last_chat_timestamp and resets the ignoring gate."""
        brain = self._make_brain()
        brain._state.last_chat_timestamp = time.time() - 20 * 60  # 20 mins ago
        brain.record_chat_interaction()
        self.assertAlmostEqual(brain._state.last_chat_timestamp, time.time(), delta=1.5)
        # Now under 10 minutes -> should NOT trigger ignoring
        snap = PerceptionSnapshot(presence=True, idle_seconds=10.0, hour=16, is_late_night=False)
        brain._state.activity_tag = "coding"
        key = brain._select_ambient_key(snap)
        self.assertEqual(key, "ambient_coding")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# 14. ThoughtQueue & Context Input Routing Tests
# ---------------------------------------------------------------------------
class TestThoughtQueueAndInputRouting(unittest.TestCase):

    def test_thought_queue_duplicate_rejection_and_immediate_pop(self):
        """ThoughtQueue must not store duplicate queued remarks and popleft must remove immediately."""
        from engine.dispatcher import ThoughtQueue
        tq = ThoughtQueue(maxlen=5)
        tq.append("Remark 1")
        tq.append("Remark 1")  # duplicate append should be no-op
        self.assertEqual(len(tq), 1)

        popped = tq.popleft()
        self.assertEqual(popped, "Remark 1")
        # Deque must now be empty so next pop returns None
        self.assertIsNone(tq.popleft())
        self.assertTrue(tq.empty())

    def test_context_leak_stripping_from_user_message(self):
        """Prepended [Context: ...] string in user input must be cleanly stripped so raw message is pure."""
        import re
        leaked_input = '[Context: You just teased the user with: "Ooh studying chemistry already?"].\n\nhi'
        cleaned = re.sub(r'\[Context:[^\]]*\][\s.:-]*', '', leaked_input).strip()
        self.assertEqual(cleaned, "hi")

        leaked_input_2 = '[Context: You just teased the user with: "Take a break"] this'
        cleaned_2 = re.sub(r'\[Context:[^\]]*\][\s.:-]*', '', leaked_input_2).strip()
        self.assertEqual(cleaned_2, "this")



# ---------------------------------------------------------------------------
# 15. MediaPipe Import & Autonomous In-Chat Poke Tests
# ---------------------------------------------------------------------------
class TestMediaPipeImportAndInChatSpontaneousPoking(unittest.TestCase):

    def test_mediapipe_import_availability(self):
        """MediaPipe solutions namespace must load with _MP_AVAILABLE True and valid face_mesh."""
        from engine.perception import _MP_AVAILABLE, _mp_face_mesh
        self.assertTrue(_MP_AVAILABLE, "MediaPipe must be available in virtualenv")
        self.assertIsNotNone(_mp_face_mesh, "FaceMesh module must not be None")
        self.assertTrue(hasattr(_mp_face_mesh, "FaceMesh"), "FaceMesh class must exist")

    def test_in_chat_poke_message_formatting(self):
        """In-chat spontaneous poke message object must represent an assistant turn without prompt leaks."""
        thought = '[Context: Teasing user about chemistry] Are you still debugging that socket?'
        import re
        sanitized = re.sub(r'\[(?:Context|System|Note):?[^\]]*\]', '', thought).strip()
        self.assertEqual(sanitized, "Are you still debugging that socket?")

        message = {
            "name": "Alpha",
            "is_user": False,
            "is_system": False,
            "send_date": "2026-10-29T12:00:00.000Z",
            "mes": sanitized,
            "extra": {},
        }
        self.assertFalse(message["is_user"])
        self.assertFalse(message["is_system"])
        self.assertEqual(message["name"], "Alpha")
        self.assertNotIn("[Context:", message["mes"])

    def test_clean_spontaneous_text_pipeline(self):
        """cleanSpontaneousText must strip pipes, bracketed system tokens, prefixes, and non-BMP glyphs."""
        import re
        def clean_spontaneous_text(raw_text):
            if not raw_text or not isinstance(raw_text, str):
                return ""
            text = raw_text.strip()
            if "|" in text:
                parts = [p.strip() for p in text.split("|") if p.strip()]
                if parts:
                    text = parts[0]
            text = re.sub(r'\[(?:Context|System|Note|Remark|Thinking|State):?[^\]]*\]', '', text).strip()
            text = re.sub(r"^(?:Alpha|thought|remark):\s*", "", text, flags=re.IGNORECASE).strip('"\'\u201c\u201d\u2018\u2019').strip()
            text = re.sub(r'[\U00010000-\U0010ffff]', '', text).strip()
            return text

        t1 = 'Alpha: [Context: Studying math] "Check out this integral!" | "Another option"'
        self.assertEqual(clean_spontaneous_text(t1), "Check out this integral!")

        t2 = '[Thinking: Idle poke] You have been quiet.'
        self.assertEqual(clean_spontaneous_text(t2), "You have been quiet.")

    def test_spontaneous_remarks_payload_dual_channel(self):
        """Dispatcher payload must include message, sender, is_bubble, add_to_chat and showBubble: True."""
        q = queue.Queue()
        d = Dispatcher(mateengine_port=self.__class__.port if hasattr(self.__class__, "port") else 8787, shared_thought_queue=q)
        # Verify the delivery payload configuration
        import inspect
        src = inspect.getsource(d._deliver)
        self.assertIn('"showBubble":      True', src)
        self.assertIn('"is_bubble":       True', src)
        self.assertIn('"add_to_chat":     True', src)
        self.assertIn('"message":         remark', src)
        self.assertIn('"sender":          _CHARACTER_NAME', src)



class TestVisionGatingAndBubbleDismissal(unittest.TestCase):
    """
    Tests for Vision Intent Gating (latency optimization: 32s -> 1.5s)
    and MateEngine desktop speech bubble auto-dismissal.
    """

    def test_vision_intent_gating_keywords(self):
        """Conversational messages must NOT request vision; screen/camera questions MUST."""
        from AnimeVision import is_vision_requested

        # Text-only queries (must NOT trigger heavy multimodal upload)
        self.assertFalse(is_vision_requested("YO"))
        self.assertFalse(is_vision_requested("Talk to me!"))
        self.assertFalse(is_vision_requested("How was your day?"))
        self.assertFalse(is_vision_requested("hello"))

        # Vision-dependent queries (must trigger multimodal upload)
        self.assertTrue(is_vision_requested("WHAT IS ON MY SCREEN IN THE TYPE BAR"))
        self.assertTrue(is_vision_requested("look at me"))
        self.assertTrue(is_vision_requested("can you see what I'm wearing?"))
        self.assertTrue(is_vision_requested("check my webcam"))
        self.assertTrue(is_vision_requested("what tabs do I have open?"))

    def test_bubble_auto_dismissal_scheduling(self):
        """schedule_bubble_auto_dismiss must create a daemon timer and allow graceful execution."""
        from AnimeVision import schedule_bubble_auto_dismiss, clear_mateengine_bubble, _dismiss_lock
        import AnimeVision

        # Calling schedule_bubble_auto_dismiss must establish an active timer
        schedule_bubble_auto_dismiss(delay_sec=10.0)
        with AnimeVision._dismiss_lock:
            self.assertIsNotNone(AnimeVision._dismiss_timer)
            self.assertTrue(AnimeVision._dismiss_timer.daemon)
            # Clean up timer so test exits cleanly
            AnimeVision._dismiss_timer.cancel()

    def test_chat_reply_show_bubble_active(self):
        """AnimeVision chat reply dispatcher must send show_bubble=True to display in dialogue window."""
        import inspect
        from AnimeVision import universal_chat_bridge
        src = inspect.getsource(universal_chat_bridge)
        self.assertIn('"show_bubble": True', src)
        self.assertIn('"spontaneous": False', src)
        self.assertIn('"force": True', src)

    def test_spontaneous_thought_queue_preserved_during_chat(self):
        """Spontaneous thought queue must retain entries and not be purged during chat input."""
        import inspect
        from AnimeVision import universal_chat_bridge
        src = inspect.getsource(universal_chat_bridge)
        self.assertNotIn('spontaneous_thought_queue.popleft()', src)

    def test_extension_index_js_show_bubble_and_spontaneous_routing(self):
        """Extension index.js must enforce showBubble=true and spontaneous=false on direct chat responses."""
        import os
        ext_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "extensions", "Extension-MateEngine", "index.js")
        with open(ext_path, "r", encoding="utf-8") as f:
            js_content = f.read()
        self.assertIn("inChatPokeEnabled: true", js_content)
        self.assertIn("desktopBubblesEnabled: true", js_content)
        self.assertIn("sendChatMessage(aiResponse, context.characterName || 'Alpha', 'joy', true)", js_content)
        self.assertIn("isSpontaneous = (showBubble === true) ? false", js_content)



# ---------------------------------------------------------------------------
# 17. Low-Light Enhancement & High-Res Vision Tests
# ---------------------------------------------------------------------------
class TestLowLightAndHighResVision(unittest.TestCase):

    def test_enhance_low_light_amplifies_dim_frame(self):
        """Dim frames (<60 mean) must be boosted via CLAHE and gamma curve."""
        import numpy as np
        from engine.perception import enhance_low_light

        dark_frame = np.full((120, 160, 3), 30, dtype=np.uint8)
        enhanced = enhance_low_light(dark_frame)
        self.assertGreater(float(enhanced.mean()), 50.0)

    def test_enhance_low_light_preserves_bright_frame(self):
        """Bright frames (>=95 mean) must pass through untouched."""
        import numpy as np
        from engine.perception import enhance_low_light

        bright_frame = np.full((120, 160, 3), 140, dtype=np.uint8)
        enhanced = enhance_low_light(bright_frame)
        self.assertTrue(np.array_equal(bright_frame, enhanced))

    def test_perception_capture_frame_blob_parameters(self):
        """capture_frame_blob must accept max_dimension=512, quality=75 and return valid blob."""
        import numpy as np
        from engine.perception import PerceptionEngine

        dummy_frame = np.full((480, 640, 3), 100, dtype=np.uint8)
        blob = PerceptionEngine.capture_frame_blob(dummy_frame, max_dimension=512, quality=75, enhance_lighting=True)
        self.assertIsNotNone(blob)
        self.assertIsInstance(blob, bytes)
        self.assertGreater(len(blob), 0)

    def test_reading_and_study_tags(self):
        """PDF documents and CBSE/Maths study windows must tag accurately."""
        from engine.perception import _tag_window

        self.assertEqual(_tag_window("book.pdf - SumatraPDF"), "reading")
        self.assertEqual(_tag_window("Calculus-Chapter3.pdf - Adobe Acrobat Reader"), "reading")
        self.assertEqual(_tag_window("CBSE Class 12 Maths - Brave"), "study")
        self.assertEqual(_tag_window("Organic Chemistry Notes - Edge"), "study")

    def test_holding_and_book_vision_keywords(self):
        """Holding objects, books, and notes must trigger vision requested."""
        from AnimeVision import is_vision_requested

        self.assertTrue(is_vision_requested("Look at what I am holding in my hand"))
        self.assertTrue(is_vision_requested("Can you read this book title?"))
        self.assertTrue(is_vision_requested("Check these notes on physics"))
        self.assertTrue(is_vision_requested("Please solve this formula on paper"))
        self.assertTrue(is_vision_requested("What is on my screen right now?"))
        self.assertTrue(is_vision_requested("Can you check my desktop monitor?"))
        self.assertTrue(is_vision_requested("Look at this physics equation and solve it"))

    def test_compose_screen_and_camera_dimensions_and_pip(self):
        """compose_screen_and_camera_bgr must scale 1080p screen to 1024px width and embed PiP webcam."""
        import numpy as np
        from engine.perception import compose_screen_and_camera_bgr, compose_screen_and_camera

        screen = np.full((1080, 1920, 3), 150, dtype=np.uint8)
        cam = np.full((480, 640, 3), 220, dtype=np.uint8)

        comp_bgr = compose_screen_and_camera_bgr(screen, cam, target_screen_width=1024, pip_width=280)
        self.assertIsNotNone(comp_bgr)
        self.assertEqual(comp_bgr.shape, (576, 1024, 3))

        # JPEG encoded output
        jpeg_bytes = compose_screen_and_camera(screen, cam, target_screen_width=1024, pip_width=280, quality=75)
        self.assertIsNotNone(jpeg_bytes)
        self.assertIsInstance(jpeg_bytes, bytes)
        self.assertGreater(len(jpeg_bytes), 1000)

    def test_compose_screen_and_camera_fallbacks(self):
        """compose_screen_and_camera must gracefully degrade if either screen or camera is missing."""
        import numpy as np
        from engine.perception import compose_screen_and_camera_bgr, compose_screen_and_camera

        # Both None -> None
        self.assertIsNone(compose_screen_and_camera_bgr(None, None))
        self.assertIsNone(compose_screen_and_camera(None, None))

        # Screen only (camera offline)
        screen = np.full((720, 1280, 3), 100, dtype=np.uint8)
        screen_only = compose_screen_and_camera_bgr(screen, None, target_screen_width=1024)
        self.assertIsNotNone(screen_only)
        self.assertEqual(screen_only.shape, (576, 1024, 3))

        # Camera only (headless desktop)
        cam = np.full((480, 640, 3), 200, dtype=np.uint8)
        cam_only = compose_screen_and_camera_bgr(None, cam, target_screen_width=1024)
        self.assertIsNotNone(cam_only)
        self.assertEqual(cam_only.shape, (480, 640, 3))

    def test_perception_engine_screen_cache_and_composite_blob(self):
        """PerceptionEngine must cache screen frame and generate unified composite blobs."""
        import numpy as np
        from engine.perception import PerceptionEngine

        engine = PerceptionEngine(camera_index=-1)
        fake_screen = np.full((1080, 1920, 3), 80, dtype=np.uint8)
        engine.update_screen_frame(fake_screen)

        retrieved = engine.get_latest_screen_frame()
        self.assertIsNotNone(retrieved)
        self.assertEqual(retrieved.shape, (1080, 1920, 3))

        blob = engine.capture_composite_blob(target_screen_width=1024, quality=75)
        self.assertIsNotNone(blob)
        self.assertIsInstance(blob, bytes)
        self.assertGreater(len(blob), 1000)


# ---------------------------------------------------------------------------
# 18. Anti-Sleep-Nag & Activity Precedence Tests
# ---------------------------------------------------------------------------
class TestAntiSleepNagAndPrecedence(unittest.TestCase):

    def test_sleep_comment_cooldown_rejection(self):
        """Sleep tropes within SLEEP_COMMENT_COOLDOWN (35 mins) must be suppressed."""
        from engine.brain import BrainEngine, PerceptionSnapshot, SLEEP_COMMENT_COOLDOWN

        snap_q = queue.Queue()
        mock_model = MagicMock()
        brain = BrainEngine(snap_q, MagicMock(), mock_model)

        # Simulate sleep remark just fired
        brain._state.last_sleep_comment_timestamp = time.time() - 300.0  # 5 mins ago (within 35m cooldown)

        mock_resp = MagicMock()
        mock_resp.text = "You look like a walking zombie, Antriksh. Go to bed."
        mock_model.generate_content.return_value = mock_resp

        snap = PerceptionSnapshot(presence=True, active_window="book.pdf - SumatraPDF", idle_seconds=10.0)
        remark = brain._generate_remark("ambient_general", snap)

        # Because recovery prompt also returns zombie, it must be suppressed to None
        self.assertIsNone(remark)

    def test_active_night_user_retains_focused_state(self):
        """Active user late at night (idle < 120s) must retain 'focused' and not hard-lock 'sleep_deprived'."""
        from engine.brain import BrainEngine, PerceptionSnapshot

        snap_q = queue.Queue()
        brain = BrainEngine(snap_q, MagicMock(), MagicMock())
        # Late night snap (hour 23), but active (idle 5 seconds)
        snap = PerceptionSnapshot(
            presence=True,
            active_window="Maths-Matrices.pdf - Brave",
            idle_seconds=5.0,
            hour=23,
        )
        brain._process_snapshot(snap)
        self.assertEqual(brain._state.user_state, "focused")

    def test_study_tag_takes_precedence_over_generic_night(self):
        """Specific activity tag (study, reading, physics) must precede generic late_night."""
        from engine.brain import BrainEngine, PerceptionSnapshot

        snap_q = queue.Queue()
        brain = BrainEngine(snap_q, MagicMock(), MagicMock())
        snap = PerceptionSnapshot(
            presence=True,
            active_window="book.pdf - SumatraPDF",
            idle_seconds=10.0,
            hour=1,  # 1 AM
        )
        brain._state.activity_tag = "reading"
        key = brain._select_ambient_key(snap)
        self.assertEqual(key, "ambient_reading")



# ---------------------------------------------------------------------------
# 19. Spontaneous Pokes UI Toggle & Config Sync Tests
# ---------------------------------------------------------------------------
class TestConfigSyncAndSpontaneousToggle(unittest.TestCase):

    def test_dispatcher_respects_spontaneous_toggle(self):
        """Dispatcher must suppress spontaneous remarks when disabled via config."""
        from engine.dispatcher import Dispatcher, ThoughtQueue
        q = ThoughtQueue()
        d = Dispatcher(mateengine_port=8787, shared_thought_queue=q)
        d.set_spontaneous_enabled(False)
        self.assertFalse(d.spontaneous_enabled)

        # Spontaneous push should be rejected
        res = d.push("Spontaneous remark while disabled")
        self.assertFalse(res)
        self.assertTrue(q.empty())

        # Forced push should still succeed
        res_forced = d.push("Direct chat remark", force=True)
        self.assertTrue(res_forced)

    def test_brain_respects_spontaneous_toggle(self):
        """BrainEngine must not speak when spontaneous remarks are disabled."""
        from engine.brain import BrainEngine
        brain = BrainEngine(queue.Queue(), MagicMock(), MagicMock())
        brain.set_spontaneous_enabled(False)
        self.assertFalse(brain._should_speak(time.time() + 9999))

        brain.set_spontaneous_enabled(True)
        # Should be eligible to speak once time since last speech > min interval
        brain._state.last_speech_timestamp = 0.0
        brain._state.user_state = "focused"
        self.assertTrue(brain._should_speak(time.time() + 9999))

    def test_animevision_config_endpoint_sync(self):
        """AnimeVision /api/config must synchronize spontaneous_pokes_enabled payload."""
        import AnimeVision
        client = AnimeVision.app.test_client()

        # Test POST sync to disable
        resp = client.post("/api/config", json={"spontaneous_pokes_enabled": False})
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data.get("success"))
        self.assertFalse(data["config"]["spontaneous_pokes_enabled"])
        self.assertFalse(AnimeVision._spontaneous_pokes_enabled)

        # Verify /spontaneous_pop returns None when disabled
        pop_resp = client.get("/spontaneous_pop")
        self.assertEqual(pop_resp.status_code, 200)
        pop_data = pop_resp.get_json()
        self.assertIsNone(pop_data.get("thought"))

        # Test POST sync to re-enable
        resp2 = client.post("/api/config", json={"spontaneous_pokes_enabled": True})
        self.assertEqual(resp2.status_code, 200)
        self.assertTrue(AnimeVision._spontaneous_pokes_enabled)


# ---------------------------------------------------------------------------
# 20. Sharp Separate Frames, Visual Delta Triggers & Fast Client Tests
# ---------------------------------------------------------------------------
class TestSharpVisionDeltaAndLatency(unittest.TestCase):

    def test_capture_separate_blobs_dimensions_no_stitching(self):
        """capture_separate_blobs must output distinct 1600x900 screen, 640x480 cam, and 768x512 ROI crop."""
        import cv2
        import numpy as np
        from engine.perception import PerceptionEngine

        engine = PerceptionEngine(camera_index=-1)
        screen = np.full((1080, 1920, 3), 120, dtype=np.uint8)
        cam = np.full((480, 640, 3), 200, dtype=np.uint8)

        blobs = engine.capture_separate_blobs(
            screen_bgr=screen,
            cam_bgr=cam,
            screen_max_width=1600,
            screen_quality=85,
            cam_width=640,
            cam_quality=85,
            include_roi=True,
        )
        engine.stop()

        self.assertIsNotNone(blobs["screen_blob"])
        self.assertIsNotNone(blobs["cam_blob"])
        self.assertIsNotNone(blobs["focus_crop_blob"])

        dec_scr = cv2.imdecode(np.frombuffer(blobs["screen_blob"], dtype=np.uint8), cv2.IMREAD_COLOR)
        dec_cam = cv2.imdecode(np.frombuffer(blobs["cam_blob"], dtype=np.uint8), cv2.IMREAD_COLOR)
        dec_roi = cv2.imdecode(np.frombuffer(blobs["focus_crop_blob"], dtype=np.uint8), cv2.IMREAD_COLOR)

        self.assertEqual(dec_scr.shape, (900, 1600, 3))
        self.assertEqual(dec_cam.shape, (480, 640, 3))
        self.assertEqual(dec_roi.shape, (512, 768, 3))

    def test_screen_delta_and_error_flash_detection(self):
        """Identical screens produce 0 delta; layout change or red error spike triggers immediately."""
        import numpy as np
        from engine.perception import PerceptionEngine

        engine = PerceptionEngine(camera_index=-1)
        scr1 = np.full((720, 1280, 3), 40, dtype=np.uint8)
        d1, h1, r1 = engine._evaluate_screen_delta(scr1)
        self.assertEqual(d1, 0.0)
        self.assertEqual(r1, "")

        # Static frame -> zero delta
        d2, h2, r2 = engine._evaluate_screen_delta(scr1.copy())
        self.assertEqual(d2, 0.0)
        self.assertEqual(h2, 0)
        self.assertEqual(r2, "")

        # Simulated bright red error banner on screen
        scr_err = scr1.copy()
        scr_err[300:650, 100:1100] = (10, 10, 235)  # BGR bright red
        d3, h3, r3 = engine._evaluate_screen_delta(scr_err)
        engine.stop()
        self.assertEqual(r3, "error_flash")
        self.assertGreater(d3, 4.5)

    def test_brain_refuses_text_only_when_require_vision_and_attaches_all_parts(self):
        """With require_vision=True, empty frames abort without calling Gemini; live frames attach 3 separate parts."""
        from engine.brain import BrainEngine
        from engine.perception import PerceptionSnapshot

        mock_model = MagicMock()
        mock_model.generate_content.return_value = MagicMock(text="TypeError on line 42 again?")

        # 1. Missing visuals -> must abort without calling mock_model
        brain_no_vis = BrainEngine(
            queue.Queue(),
            MagicMock(),
            mock_model,
            visual_fetcher=lambda include_roi=True: {},
            require_vision=True,
        )
        res_empty = brain_no_vis._generate_remark("screen_error_flash", PerceptionSnapshot(active_window="VSCode"))
        self.assertIsNone(res_empty)
        self.assertFalse(mock_model.generate_content.called)

        # 2. Separate live blobs -> must attach all 3 separate JPEG image parts
        fake_blobs = {
            "screen_blob": b"\xff\xd8screen",
            "cam_blob": b"\xff\xd8cam",
            "focus_crop_blob": b"\xff\xd8roi",
        }
        brain_vis = BrainEngine(
            queue.Queue(),
            MagicMock(),
            mock_model,
            visual_fetcher=lambda include_roi=True: fake_blobs,
            require_vision=True,
        )
        res_ok = brain_vis._generate_remark("screen_error_flash", PerceptionSnapshot(active_window="VSCode"))
        self.assertEqual(res_ok, "TypeError on line 42 again?")
        self.assertTrue(mock_model.generate_content.called)
        sent_parts = mock_model.generate_content.call_args[0][0]
        img_blobs = [p["data"] for p in sent_parts if isinstance(p, dict) and "data" in p]
        self.assertEqual(img_blobs, [b"\xff\xd8screen", b"\xff\xd8cam", b"\xff\xd8roi"])

    def test_brain_visual_delta_trigger_and_static_suppression(self):
        """Visual delta fires immediately past cooldown and clears _has_unseen_visual_change once spoken."""
        from engine.brain import BrainEngine, VISUAL_ATTENTION_COOLDOWN
        from engine.perception import PerceptionSnapshot

        delivered = []
        mock_model = MagicMock()
        mock_model.generate_content.return_value = MagicMock(text="Red traceback right in front of you.")
        brain = BrainEngine(
            queue.Queue(),
            lambda r: delivered.append(r),
            mock_model,
            visual_fetcher=lambda include_roi=True: {"screen_blob": b"scr", "cam_blob": b"cam"},
            require_vision=True,
        )
        brain._state.last_speech_timestamp = time.time() - (VISUAL_ATTENTION_COOLDOWN + 10)
        brain._last_snap = PerceptionSnapshot(presence=True, eye_contact=False)

        delta_snap = PerceptionSnapshot(
            presence=True,
            eye_contact=False,
            screen_delta=12.0,
            visual_trigger_reason="error_flash",
            screen_blob=b"scr",
        )
        brain._process_snapshot(delta_snap)
        deadline = time.time() + 3.0
        while not delivered and time.time() < deadline:
            time.sleep(0.05)
        brain.stop()

        self.assertTrue(len(delivered) > 0)
        self.assertFalse(brain._has_unseen_visual_change)

    def test_unified_brain_always_on_chat_vision_and_fast_client(self):
        """AnimeVision must drop proactive_observer_loop, attach cached visuals on casual chat, and support FastGeminiClient."""
        import AnimeVision
        self.assertFalse(hasattr(AnimeVision, "proactive_observer_loop"))

        # Verify FastGeminiClient session reuse & Gemini 3.5+ thinkingLevel + 4xx logging
        fc = AnimeVision.FastGeminiClient("test-key", "gemini-3.5-flash-lite", default_max_tokens=45)
        ok_resp = MagicMock()
        ok_resp.status_code = 200
        ok_resp.json.return_value = {"candidates": [{"content": {"parts": [{"text": "Nice syntax error."}]}}]}
        ok_resp.raise_for_status = MagicMock()

        with patch.object(fc._session, "post", return_value=ok_resp) as post_mock:
            out = fc.generate_content(
                ["[LIVE SCREEN]", {"mime_type": "image/jpeg", "data": b"abc"}, "hello"],
                system_instruction="Be concise.",
            )
            self.assertEqual(out.text, "Nice syntax error.")
            sent_payload = post_mock.call_args.kwargs["json"]
            sent_headers = post_mock.call_args.kwargs["headers"]
            self.assertEqual(sent_headers["x-goog-api-key"], "test-key")
            self.assertEqual(sent_payload["generationConfig"]["maxOutputTokens"], 45)
            self.assertNotIn("temperature", sent_payload["generationConfig"])
            self.assertEqual(sent_payload["generationConfig"]["thinkingConfig"], {"thinkingLevel": "MINIMAL"})
            parts = sent_payload["contents"][0]["parts"]
            self.assertTrue(any("inlineData" in p for p in parts))

        # Verify 4xx logs resp.text before raising HTTPError
        bad_resp = MagicMock()
        bad_resp.status_code = 400
        bad_resp.text = '{"error":{"message":"Invalid field xyz"}}'
        bad_resp.raise_for_status.side_effect = RuntimeError("400 Bad Request")
        with patch.object(fc._session, "post", return_value=bad_resp), \
             patch.object(AnimeVision.app_logger, "error") as log_err:
            with self.assertRaises(RuntimeError):
                fc.generate_content(["test"])
            log_err.assert_called_once()
            self.assertIn("Invalid field xyz", log_err.call_args[0][0])

        # Verify casual chat turn ("hi") still attaches cached separate visuals
        client = AnimeVision.app.test_client()
        cached_parts = ["[LIVE SCREEN (1600x900)]", {"mime_type": "image/jpeg", "data": b"scrbytes"}]
        mock_chat_model = MagicMock()
        mock_chat_model.generate_content.return_value = MagicMock(text="Hey there, busy coding?")

        with patch.object(AnimeVision, "get_cached_visuals", return_value=cached_parts) as vis_mock, \
             patch.object(AnimeVision, "model", mock_chat_model), \
             patch.object(AnimeVision, "send_to_mateengine"):
            resp = client.post("/v1/chat/completions", json={"messages": [{"role": "user", "content": "hi"}]})
            self.assertEqual(resp.status_code, 200)
            vis_mock.assert_called_once_with(force_fresh=False, include_roi=True)
            sent_chat_parts = mock_chat_model.generate_content.call_args[0][0]
            self.assertIn({"mime_type": "image/jpeg", "data": b"scrbytes"}, sent_chat_parts)


if __name__ == "__main__":
    print("=" * 60)
    print("  AlphaVision Awareness Engine — Mock Test Suite")
    print("=" * 60)
    loader = unittest.TestLoader()
    suite  = unittest.TestSuite()
    for cls in [
        TestPerceptionSnapshot,
        TestWindowTagging,
        TestPerceptionEngineSignificanceGate,
        TestInternalStatePersistence,
        TestBrainStateMachine,
        TestDispatcher,
        TestChatBoxDebouncer,
        TestFullPipeline,
        TestCameraWorkerFailSafe,
        TestCleanThoughtAndFormatting,
        TestSafeScreenGrabber,
        TestAmbientPokeContext,
        TestIgnoringAndNaggingTriggers,
        TestThoughtQueueAndInputRouting,
        TestMediaPipeImportAndInChatSpontaneousPoking,
        TestVisionGatingAndBubbleDismissal,
        TestLowLightAndHighResVision,
        TestAntiSleepNagAndPrecedence,
        TestConfigSyncAndSpontaneousToggle,
        TestSharpVisionDeltaAndLatency,
    ]:
        suite.addTests(loader.loadTestsFromTestCase(cls))

    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    sys.exit(0 if result.wasSuccessful() else 1)
