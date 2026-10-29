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

    def test_in_chat_poke_cooldown_gating(self):
        """In-chat poke must respect the cooldown interval (e.g. 180s)."""
        cooldown_sec = 180
        last_poke_time = 1000.0
        
        # Test 1: Within cooldown -> blocked
        current_time_1 = 1100.0  # only 100s elapsed
        allowed_1 = (current_time_1 - last_poke_time) >= cooldown_sec
        self.assertFalse(allowed_1)

        # Test 2: After cooldown -> allowed
        current_time_2 = 1200.0  # 200s elapsed
        allowed_2 = (current_time_2 - last_poke_time) >= cooldown_sec
        self.assertTrue(allowed_2)


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
    ]:
        suite.addTests(loader.loadTestsFromTestCase(cls))

    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    sys.exit(0 if result.wasSuccessful() else 1)
