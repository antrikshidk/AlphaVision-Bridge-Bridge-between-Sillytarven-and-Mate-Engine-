"""
engine/brain.py  — AlphaVision Internal State & Cognitive Engine
=================================================================
Maintains a persistent JSON state file tracking user_state, mood_state,
and temporal context.  Implements Spontaneous Volition: evaluating every
60-120 s whether an ambient remark from the AI companion is warranted,
with strict rate-limiting to avoid annoying the user.

Flow:
  PerceptionEngine → snapshot_queue → BrainEngine.ingest_snapshot()
                                             ↓
                            state mutation + Gemini Flash Lite prompt
                                             ↓
                      Dispatcher.push_spontaneous(remark)
"""

from __future__ import annotations

import json
import logging
import os
import random
import re
import time
import threading
import queue
import datetime
from dataclasses import dataclass, asdict
from typing import Optional, List, Dict, Any

import google.generativeai as genai

from engine.perception import PerceptionSnapshot, _WINDOW_TAGS, _tag_window, tag_window, enhance_low_light

logger = logging.getLogger("alphavision.brain")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
_STATE_FILE     = os.path.join(os.path.dirname(__file__), "..", "brain_state.json")
_STATE_FILE     = os.path.normpath(_STATE_FILE)

MIN_SPONTANEOUS_INTERVAL  = 5 * 60     # 5 min absolute floor
MAX_SPONTANEOUS_INTERVAL  = 10 * 60    # 10 min normal ceiling
DRAMA_COOLDOWN            = 90         # seconds; after a dramatic event allow quicker remark
EVAL_TICK                 = 30         # how often the cognitive loop ticks (seconds)
SLEEP_COMMENT_COOLDOWN    = 35 * 60    # 35 min minimum between sleep/bed nagging remarks
SLEEP_KEYWORDS = (
    "sleep", "bed", "zombie", "tired", "rest", "nap", "exhausted",
    "pillow", "yawn", "sleepy", "go to sleep", "go offline",
    "sleep-deprived", "sleep deprived", "goodnight", "nighty",
    "crash", "pass out"
)


# ---------------------------------------------------------------------------
# Output Sanitization & Truncation Filter
# ---------------------------------------------------------------------------
def sanitize_dialogue_text(text: str) -> str:
    """
    Strips characters outside the Basic Multilingual Plane (BMP) (high-surrogate emojis
    and symbols) that render as empty rectangular boxes [ ] [ ] in Unity text renderer.
    """
    if not text or not isinstance(text, str):
        return ""
    return re.sub(r'[\U00010000-\U0010ffff]', '', text).strip()


def clean_thought(raw_text: str) -> Optional[str]:
    """
    Cleans, deduplicates candidates, and enforces strict single-sentence truncation:
      - Strips quotes and common prefixes ('Alpha:', 'Thought:', etc.).
      - If multiple options are separated by pipes ('|'), picks ONLY ONE clean thought.
      - Normalizes whitespace to a single line.
      - Enforces strict truncation to at most 15 words, trimming cleanly at sentence boundary.
      - Ensures spontaneous desktop remarks are a single punchy line (under 12-15 words).
    """
    if not raw_text or not isinstance(raw_text, str):
        return None

    text = raw_text.strip().strip('"\'')
    for prefix in ["Alpha:", "alpha:", "Thought:", "thought:", "Remark:", "remark:"]:
        if text.startswith(prefix):
            text = text[len(prefix):].strip().strip('"\'')

    # Strip characters outside the Basic Multilingual Plane (BMP)
    text = re.sub(r'[\U00010000-\U0010ffff]', '', text).strip()

    # 1. Strip pipe separators and pick ONLY ONE single clean thought
    if "|" in text:
        candidates = [opt.strip().strip('"\'') for opt in text.split("|") if opt.strip()]
        if candidates:
            text = candidates[0]

    # Re-strip characters outside the BMP in case candidate splitting exposed anything
    text = re.sub(r'[\U00010000-\U0010ffff]', '', text).strip()

    # 2. Normalize whitespace into a single line
    text = " ".join(text.split())
    if not text:
        return None

    # 3. Strict truncation: at most 15 words, cleanly trimmed at sentence boundary
    words = text.split()
    if len(words) > 15:
        candidate_sub = " ".join(words[:15])
        last_punct = max(candidate_sub.rfind('.'), candidate_sub.rfind('!'), candidate_sub.rfind('?'))
        if last_punct > 12:
            text = candidate_sub[:last_punct + 1].strip()
        else:
            text = candidate_sub.rstrip(',;:-').strip()
            if not text.endswith(('.', '!', '?')):
                text += "..."

    return text or None


# ---------------------------------------------------------------------------
# Short-Term Ambient Context Injection State (Dual-Channel Memory)
# ---------------------------------------------------------------------------
last_ambient_poke: Optional[Dict[str, Any]] = None
_poke_lock = threading.Lock()


def get_last_ambient_poke() -> Optional[Dict[str, Any]]:
    """
    Returns the most recent spontaneous ambient poke if it occurred within 90 seconds.
    Returns None if expired (>90s) or no poke recorded.
    """
    global last_ambient_poke
    with _poke_lock:
        if last_ambient_poke is None:
            return None
        if time.time() - last_ambient_poke.get("timestamp", 0) > 90.0:
            last_ambient_poke = None
            return None
        return dict(last_ambient_poke)


def set_last_ambient_poke(text: str, context: str = ""):
    """
    Records a spontaneous ambient poke in-memory with text, timestamp, and context.
    """
    global last_ambient_poke
    clean_text = clean_thought(text)
    if not clean_text:
        return
    with _poke_lock:
        last_ambient_poke = {
            "text": clean_text,
            "timestamp": time.time(),
            "context": context or "desktop",
        }


def clear_last_ambient_poke():
    """
    Clears the recorded ambient poke after the user responds or upon explicit expiration.
    """
    global last_ambient_poke
    with _poke_lock:
        last_ambient_poke = None


# ---------------------------------------------------------------------------
# State dataclass — persisted to JSON
# ---------------------------------------------------------------------------
@dataclass
class InternalState:
    user_state: str              = "focused"     # focused | chilling | away | returned | sleep_deprived
    mood_state: str              = "curious"     # curious | playful | concerned | bored
    last_speech_timestamp: float = 0.0
    last_dramatic_event: float   = 0.0
    away_since: float            = 0.0
    activity_tag: str            = "misc"
    consecutive_late_nights: int = 0
    session_start: float         = 0.0
    last_chat_timestamp: float   = 0.0           # When user last opened/chatted in companion chat
    last_active_timestamp: float = 0.0           # When user was last actively using desktop
    last_sleep_comment_timestamp: float = 0.0     # When a sleep/bed remark last fired (rate-limited to 35m)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @staticmethod
    def from_dict(d: Dict[str, Any]) -> "InternalState":
        valid = {k: v for k, v in d.items() if k in InternalState.__dataclass_fields__}
        return InternalState(**valid)


# ---------------------------------------------------------------------------
# Persona prompt templates (lean & fast for <800ms Flash Lite roundtrips)
# ---------------------------------------------------------------------------
_PERSONA_HEADER = (
    "You are Alpha, a sharp-eyed, observant desktop anime companion with dry wit sitting on Antriksh's desktop.\n"
    "Observe what he is doing and make ONE short, candid remark (max 15 words) glancing over his shoulder.\n"
    "OBSERVATION PRIORITIES:\n"
    "  1. The specific window title, document, or app: remark on what he's reading (e.g. PDF), studying (math/physics), coding, or watching.\n"
    "  2. Visible physical actions: posture, holding up books/items to camera, facial expressions, deep concentration.\n"
    "  3. Banter & Wit: playful teasing, curious observations, or witty commentary rather than defaulting to nagging about sleep.\n"
    "STRICT CONSTRAINTS:\n"
    "  - Never sound like an assistant or customer service. No help offers ('How can I help?').\n"
    "  - Do NOT default to nagging about sleep, telling him to go to bed, or calling him a zombie.\n"
    "  - No emojis. Output ONLY the single spoken sentence.\n\n"
)

_SITUATION_PROMPTS: Dict[str, str] = {
    "returned_long": (
        "Antriksh just came back after being away for {away_min:.0f} minutes. "
        "Make a dry, amused comment about his return — something between relief and mild sarcasm."
    ),
    "returned_short": (
        "Antriksh stepped away briefly and just returned. "
        "One quick offhand comment. Don't make a big deal of it."
    ),
    "late_night_coding": (
        "It is {hour}:00 and Antriksh is writing code in '{active_window}'. "
        "Make a clever, witty companion remark about his coding focus at this quiet hour."
    ),
    "late_night_gaming": (
        "It is {hour}:00 and Antriksh is gaming in '{active_window}'. "
        "Make a playful remark teasing his gameplay or late-night gaming focus."
    ),
    "late_night_browsing": (
        "It is {hour}:00 and Antriksh is working on '{active_window}'. "
        "Make a sharp, observational comment about what he is browsing or reading."
    ),
    "fatigue_cue": (
        "Antriksh rubbed his eyes or paused while working on '{active_window}'. "
        "Make a brief, witty observation about his concentration without nagging him to sleep."
    ),
    "long_idle": (
        "Antriksh has been idle for {idle_min:.0f} minutes but the screen is on. "
        "He's probably spacing out. One dry comment."
    ),
    "activity_shift": (
        "Antriksh just switched from {prev_tag} to {curr_tag}. "
        "One casual, observational comment about the context switch."
    ),
    "ambient_coding": (
        "Antriksh is coding in '{active_window}'. "
        "Make a dry, clever companion observation about his programming workflow."
    ),
    "ambient_gaming": (
        "Antriksh is gaming in '{active_window}'. A short playful or teasing remark."
    ),
    "ambient_physics": (
        "Antriksh is studying physics or solving science formulas in '{active_window}'. "
        "Make one playful, teasing companion remark about physics and his big brain."
    ),
    "ambient_reading": (
        "Antriksh is reading a document or PDF ('{active_window}'). "
        "Make a witty, curious remark about the reading material or how intently he is staring at the page."
    ),
    "ambient_study": (
        "Antriksh is studying or solving formulas in '{active_window}'. "
        "Make a sharp, playful companion comment about his study session or academic grind."
    ),
    "ignoring_coding": (
        "Antriksh has been staring at code for {ignoring_min:.0f} minutes without talking to you or opening chat. "
        "Make a dry, teasing poke telling him to take a break and talk to you."
    ),
    "ignoring_browsing": (
        "Antriksh has been browsing the web for {ignoring_min:.0f} minutes without talking to you. "
        "Ask if he is just going to leave you sitting here while he browses, teasingly."
    ),
    "ignoring_physics": (
        "Antriksh has been studying physics equations for {ignoring_min:.0f} minutes without talking to you. "
        "Tease him playfully about his physics obsession and demand he take a break to talk to you."
    ),
    "ignoring_study": (
        "Antriksh has been deep in study material in '{active_window}' for {ignoring_min:.0f} minutes without talking to you. "
        "Playfully demand that he take a quick breather to talk to you."
    ),
    "ignoring_gaming": (
        "Antriksh has been gaming for {ignoring_min:.0f} minutes completely ignoring you. "
        "Make a playful, teasing remark asking if his game is more interesting than you."
    ),
    "ignoring_nag": (
        "Antriksh has been active on his PC for {ignoring_min:.0f} minutes without opening chat to talk to you. "
        "Make an attention-seeking, nagging companion comment demanding he talk to you."
    ),
    "ambient_general": (
        "Antriksh is at his computer doing {activity_tag} ('{active_window}'). "
        "Make one short, spontaneous companion-style comment."
    ),
}


# ---------------------------------------------------------------------------
# Brain Engine
# ---------------------------------------------------------------------------
class BrainEngine:
    """
    Consumes PerceptionSnapshots, updates internal state, and schedules
    spontaneous Gemini-powered ambient remarks via the Dispatcher.
    """

    def __init__(
        self,
        snapshot_queue: queue.Queue,
        remark_callback,        # callable(remark: str) — sends to Dispatcher
        gemini_model,           # configured genai.GenerativeModel
    ):
        self._snap_q   = snapshot_queue
        self._remark_cb = remark_callback
        self._model    = gemini_model
        self._stop     = threading.Event()
        self._lock     = threading.Lock()

        self._state    = self._load_state()
        self._state.session_start = time.time()

        self._last_snap: Optional[PerceptionSnapshot] = None
        self._threads: List[threading.Thread] = []

        # Async non-blocking LLM task queue (maxsize=5) & dedicated worker
        self._llm_queue: queue.Queue = queue.Queue(maxsize=5)
        self._llm_thread: Optional[threading.Thread] = None

    def _ensure_llm_worker(self):
        with self._lock:
            if self._llm_thread is None or not self._llm_thread.is_alive():
                self._llm_thread = threading.Thread(
                    target=self._llm_worker_loop, daemon=True, name="BrainLLMWorker"
                )
                self._llm_thread.start()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    def start(self):
        self._ensure_llm_worker()
        ingest = threading.Thread(target=self._ingest_loop, daemon=True, name="BrainIngest")
        cogni  = threading.Thread(target=self._cognitive_loop, daemon=True, name="BrainCognition")
        ingest.start()
        cogni.start()
        self._threads = [ingest, cogni]
        logger.info("[BRAIN] Cognitive engine online.")

    def stop(self):
        self._stop.set()
        for t in self._threads:
            t.join(timeout=5.0)
        if self._llm_thread and self._llm_thread.is_alive():
            self._llm_thread.join(timeout=2.0)
        self._save_state()
        logger.info("[BRAIN] State persisted, engine stopped.")

    # ------------------------------------------------------------------
    # Ingest loop — consumes snapshots and mutates state
    # ------------------------------------------------------------------
    def _ingest_loop(self):
        while not self._stop.is_set():
            try:
                snap: PerceptionSnapshot = self._snap_q.get(timeout=2.0)
                self._process_snapshot(snap)
            except queue.Empty:
                continue
            except Exception as exc:
                logger.exception("[BRAIN] Ingest error: %s", exc)

    def _process_snapshot(self, snap: PerceptionSnapshot):
        prev_snap = self._last_snap
        prev_tag  = self._state.activity_tag
        prev_user_state = self._state.user_state
        now       = time.time()

        # ---- Activity tag ----
        curr_tag = _tag_window(snap.active_window)
        self._state.activity_tag = curr_tag

        # ---- user_state transitions ----
        if not snap.presence:
            if self._state.user_state != "away":
                self._state.user_state  = "away"
                self._state.away_since  = now
        else:
            was_away = self._state.user_state == "away"
            if was_away:
                away_dur = now - self._state.away_since
                self._state.user_state = "returned"
                self._state.last_dramatic_event = now
                # Immediate remark if absent >10 min
                if away_dur > 10 * 60:
                    self._fire_remark("returned_long", snap, away_min=away_dur / 60)
                else:
                    self._fire_remark("returned_short", snap)
            elif snap.idle_seconds > 8 * 60:
                self._state.user_state = "chilling"
            else:
                self._state.user_state = "focused"

        # ---- State transition logging ----
        if prev_user_state != self._state.user_state:
            logger.info("[BRAIN STATE] User State: %s -> %s (App: '%s')", prev_user_state, self._state.user_state, snap.active_window[:35])
        elif prev_tag != curr_tag and curr_tag != "misc":
            logger.info("[BRAIN STATE] Context: %s -> %s ('%s')", prev_tag, curr_tag, snap.active_window[:35])

        if snap.presence and snap.idle_seconds < 180:
            self._state.last_active_timestamp = now

        # ---- Fatigue cue → dramatic event ----
        if snap.fatigue_cue and (now - self._state.last_dramatic_event > DRAMA_COOLDOWN):
            self._state.last_dramatic_event = now
            self._fire_remark("fatigue_cue", snap)

        # ---- Late night flag ----
        if snap.is_late_night:
            self._state.mood_state = "concerned"
        elif curr_tag == "gaming":
            self._state.mood_state = "playful"
        elif curr_tag in ("coding", "physics"):
            self._state.mood_state = "curious"
        else:
            self._state.mood_state = "bored" if snap.idle_seconds > 300 else "curious"

        # ---- Sleep-deprived inference (only when inactive / spaced out) ----


        if snap.is_late_night and snap.fatigue_cue and snap.idle_seconds > 180:


            self._state.user_state = "sleep_deprived"

        # ---- Activity shift remark ----
        if (prev_tag != curr_tag and prev_snap is not None and
                curr_tag != "misc" and prev_tag != "misc" and
                now - self._state.last_dramatic_event > DRAMA_COOLDOWN):
            self._state.last_dramatic_event = now
            self._fire_remark("activity_shift", snap, prev_tag=prev_tag, curr_tag=curr_tag)

        self._last_snap = snap

    # ------------------------------------------------------------------
    # Cognitive loop — periodic volition evaluation
    # ------------------------------------------------------------------
    def _cognitive_loop(self):
        """
        Ticks every EVAL_TICK seconds, decides probabilistically whether
        to fire a spontaneous ambient remark.
        """
        next_eval = time.time() + random.uniform(60, 120)

        while not self._stop.is_set():
            now = time.time()
            if now < next_eval:
                time.sleep(EVAL_TICK)
                continue

            next_eval = now + random.uniform(60, 120)
            if not self._should_speak(now):
                continue

            snap = self._last_snap
            if snap is None:
                continue

            # Pick the most contextually relevant situation key
            key = self._select_ambient_key(snap)
            self._fire_remark(key, snap)

    def _should_speak(self, now: float) -> bool:
        """Rate-limit gate."""
        elapsed = now - self._state.last_speech_timestamp
        min_gap = MIN_SPONTANEOUS_INTERVAL
        # After a dramatic event allow slightly shorter gap
        if now - self._state.last_dramatic_event < 120:
            min_gap = 90
        if elapsed < min_gap:
            return False
        if self._state.user_state == "away":
            return False
        return True

    def _select_ambient_key(self, snap: PerceptionSnapshot) -> str:
        tag  = self._state.activity_tag
        hour = snap.hour
        now  = time.time()

        # 1. Idle / Ignoring Timer: user active on desktop but hasn't chatted for >10-15 mins
        chat_ref = self._state.last_chat_timestamp if self._state.last_chat_timestamp > 0 else self._state.session_start
        if snap.presence and snap.idle_seconds < 180 and chat_ref > 0 and (now - chat_ref) >= 10 * 60:
            if tag == "coding":
                return "ignoring_coding"
            elif tag == "physics":
                return "ignoring_physics"
            elif tag in ("reading", "study"):
                return "ignoring_study"
            elif tag in ("browsing", "youtube"):
                return "ignoring_browsing"
            elif tag == "gaming":
                return "ignoring_gaming"
            return "ignoring_nag"

        # 2. Contextual pokes for specific activities (prioritized over generic late-night)
        if tag == "physics": return "ambient_physics"
        if tag == "reading": return "ambient_reading"
        if tag == "study":   return "ambient_study"
        if tag == "coding":  return "ambient_coding"
        if tag == "gaming":  return "ambient_gaming"

        # 3. Idle space-out
        if snap.idle_seconds > 8 * 60:
            return "long_idle"

        # 4. Late night specific context (only for generic/browsing activities)
        if snap.is_late_night:
            return "late_night_browsing"

        return "ambient_general"

    # ------------------------------------------------------------------
    # Remark generation & Async Non-Blocking LLM Queue
    # ------------------------------------------------------------------
    def _fire_remark(self, situation_key: str, snap: PerceptionSnapshot, **kwargs):
        """Asynchronously enqueues an LLM generation task without blocking sensory loops."""
        self._ensure_llm_worker()
        try:
            self._llm_queue.put_nowait((situation_key, snap, kwargs))
        except queue.Full:
            logger.debug("[BRAIN] LLM task queue saturated; dropping remark request.")

    def _llm_worker_loop(self):
        """Dedicated single worker thread for stream-less, low-token LLM inference."""
        while not self._stop.is_set():
            try:
                item = self._llm_queue.get(timeout=1.0)
            except queue.Empty:
                continue

            situation_key, snap, kwargs = item
            try:
                remark = self._generate_remark(situation_key, snap, **kwargs)
                if remark:
                    self._state.last_speech_timestamp = time.time()
                    self._save_state()
                    logger.info("[BRAIN REMARK] '%s'", remark)
                    # Short-term ambient context injection (Dual-Channel Memory)
                    set_last_ambient_poke(remark, snap.active_window)
                    self._remark_cb(remark)
            except Exception as exc:
                logger.error("[BRAIN] Remark generation failed: %s", exc)

    def _generate_remark(
        self, situation_key: str, snap: PerceptionSnapshot, **kwargs
    ) -> Optional[str]:
        template = _SITUATION_PROMPTS.get(situation_key, _SITUATION_PROMPTS["ambient_general"])

        # Fill template variables
        now = time.time()
        chat_ref = self._state.last_chat_timestamp if self._state.last_chat_timestamp > 0 else self._state.session_start
        ignoring_min = max(0.0, (now - chat_ref) / 60.0) if chat_ref > 0 else 0.0

        now = time.time()
        sleep_on_cooldown = (now - self._state.last_sleep_comment_timestamp) < SLEEP_COMMENT_COOLDOWN

        active_win = snap.active_window or "his screen"
        fmt_vars = {
            "hour":          snap.hour,
            "away_min":      kwargs.get("away_min", 0),
            "idle_min":      snap.idle_seconds / 60,
            "ignoring_min":  kwargs.get("ignoring_min", ignoring_min),
            "prev_tag":      kwargs.get("prev_tag", "something"),
            "curr_tag":      kwargs.get("curr_tag", self._state.activity_tag),
            "activity_tag":  self._state.activity_tag,
            "active_window": active_win[:60],
        }
        try:
            situation_desc = template.format(**fmt_vars)
        except KeyError:
            situation_desc = template

        neg_clause = ""
        if sleep_on_cooldown:
            neg_clause = (
                "\nNEGATIVE CONSTRAINT: Do NOT mention sleep, bed, bedtime, tiredness, resting, or zombies. "
                "Talk strictly about the document, active application, visible actions, or his work."
            )

        prompt = (
            f"{_PERSONA_HEADER}"
            f"SITUATION: {situation_desc}\n"
            f"STATE: {self._state.user_state} | APP: {active_win[:60]}{neg_clause}\n"
            f"Alpha:"
        )

        # Optional: include a frame snapshot blob for visual context
        parts: List[Any] = [prompt]
        if snap.frame_blob:
            parts = [{"mime_type": "image/jpeg", "data": snap.frame_blob}, prompt]

        # Low-token (<50 tokens), stream-less generation configuration (<800ms round-trip)
        try:
            response = self._model.generate_content(
                parts,
                generation_config={"temperature": 0.7, "max_output_tokens": 50}
            )
        except TypeError:
            response = self._model.generate_content(parts)

        raw_text = response.text.strip() if (response and hasattr(response, "text") and response.text) else ""
        cleaned = clean_thought(raw_text)
        if not cleaned:
            return None

        # Check for sleep tropes
        contains_sleep_trope = any(
            re.search(rf"\b{re.escape(kw)}\b", cleaned, re.IGNORECASE) for kw in SLEEP_KEYWORDS
        )

        now = time.time()
        if contains_sleep_trope:
            if (now - self._state.last_sleep_comment_timestamp) < SLEEP_COMMENT_COOLDOWN:
                logger.info(
                    "[BRAIN DEDUP] Suppressed sleep remark on cooldown (%.1fs < %ds): '%s'",
                    now - self._state.last_sleep_comment_timestamp,
                    SLEEP_COMMENT_COOLDOWN,
                    cleaned,
                )
                # Attempt one fast recovery prompt focused strictly on the active window
                try:
                    recovery_prompt = (
                        f"{_PERSONA_HEADER}"
                        f"SITUATION: Antriksh is working on '{active_win[:50]}'. "
                        f"Make ONE short, witty remark about his current document or app. "
                        f"Do NOT mention sleep, bed, or being tired under any circumstance.\n"
                        f"Alpha:"
                    )
                    rec_resp = self._model.generate_content(
                        [recovery_prompt],
                        generation_config={"temperature": 0.7, "max_output_tokens": 40}
                    )
                    rec_text = rec_resp.text.strip() if (rec_resp and hasattr(rec_resp, "text")) else ""
                    rec_cleaned = clean_thought(rec_text)
                    if rec_cleaned and not any(re.search(rf"\b{re.escape(kw)}\b", rec_cleaned, re.IGNORECASE) for kw in SLEEP_KEYWORDS):
                        return rec_cleaned
                except Exception as rec_err:
                    logger.debug("[BRAIN] Recovery prompt error: %s", rec_err)
                return None
            else:
                self._state.last_sleep_comment_timestamp = now
        return cleaned

    def record_chat_interaction(self, timestamp: Optional[float] = None):
        """Resets the ignoring/nagging timer when user interacts with Alpha in chat."""
        with self._lock:
            self._state.last_chat_timestamp = timestamp or time.time()
            self._save_state()

    # ------------------------------------------------------------------
    # State persistence
    # ------------------------------------------------------------------
    def _save_state(self):
        try:
            with open(_STATE_FILE, "w", encoding="utf-8") as f:
                json.dump(self._state.to_dict(), f, indent=2)
        except Exception as exc:
            logger.warning("[BRAIN] State save failed: %s", exc)

    def _load_state(self) -> InternalState:
        try:
            if os.path.exists(_STATE_FILE):
                with open(_STATE_FILE, "r", encoding="utf-8") as f:
                    return InternalState.from_dict(json.load(f))
        except Exception as exc:
            logger.warning("[BRAIN] State load failed (using defaults): %s", exc)
        return InternalState()

    # ------------------------------------------------------------------
    # Public read-only access for external modules
    # ------------------------------------------------------------------
    @property
    def state(self) -> InternalState:
        return self._state

    def get_state_dict(self) -> Dict[str, Any]:
        return self._state.to_dict()
