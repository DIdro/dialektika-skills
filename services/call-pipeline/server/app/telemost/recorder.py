#!/usr/bin/env python3
"""Telemost recorder — joins a Telemost conference, records audio, and
auto-stops when the conference ends. No transcription here (done downstream
via call-pipeline by the control bot).

Usage:
    python -m app.telemost.recorder <telemost_url> [max_seconds]

On stop it prints machine-parseable markers (the control bot greps these):
    RECORDING_SAVED: <abs path to .ogg>
    DURATION_SEC: <int>
    STOP_REASON: silence|ended|empty|manual|maxcap|ffmpeg|error

Auto-stop triggers:
  - SILENCE_LIMIT_SEC of continuous silence after audio was heard (primary,
    CSS-independent — fires when everyone has left), or
  - the meeting UI shows an "ended" screen (host ended the call), or
  - only the bot is left in the call for EMPTY_LIMIT_SEC (DOM, brittle), or
  - max_seconds hard cap, or
  - SIGTERM (manual /stop from the control bot).

Env:
  DISPLAY      — Xvfb display (default :99)
  PULSE_SINK   — null-sink name (default telemost_sink)
  BOT_NAME     — display name in the conference
  OUTPUT_DIR   — where the final .ogg is copied (default $HOME)
"""
import sys
import os
import subprocess
import time
import signal
import tempfile
import shutil
from pathlib import Path
from datetime import datetime

DISPLAY_NUM = os.environ.get("DISPLAY", ":99")
PULSE_SINK  = os.environ.get("PULSE_SINK", "telemost_sink")
BOT_NAME    = os.environ.get("BOT_NAME", "Транскрибатор")
OUTPUT_DIR  = os.environ.get("OUTPUT_DIR", str(Path.home()))

EMPTY_LIMIT_SEC = 60   # stop after this long with only the bot left (DOM signal)
CHECK_INTERVAL  = 5    # DOM poll cadence

# Silence-based auto-stop — the reliable signal, independent of Telemost's
# obfuscated CSS (which Yandex churns, breaking the DOM detector). Once we've
# heard real audio, this many seconds of continuous near-digital silence (the
# null-sink reads ~-91 dB when no one is sending audio) means everyone left.
# The "heard audio first" gate keeps a meeting that starts quiet from stopping.
SILENCE_LIMIT_SEC = int(os.environ.get("SILENCE_LIMIT_SEC", "300"))   # 5 min
SILENCE_DB        = float(os.environ.get("SILENCE_DB", "-80"))        # above = audio
SILENCE_SAMPLE_SEC = 20   # how often to measure the monitor level

_AUTOSTOP_JS = r"""
() => {
  const tiles = [...document.querySelectorAll('[class*="item_NZ2DW"]')];
  const others = tiles.filter(t => !(t.className||'').includes('selfView')).length;
  const txt = (document.body.innerText || '');
  const ended = /(звонок|конференци|встреча)\s+(заверш|оконч)/i.test(txt)
             || /вы вышли|покинули звонок|call ended|meeting ended/i.test(txt);
  const btns = [...document.querySelectorAll('button')].map(b => (b.innerText||'').toLowerCase());
  const rejoin = btns.some(t => t.includes('вернуться к звонку') || t.includes('подключиться снова'));
  return {others, ended, rejoin};
}
"""


class Recorder:
    def __init__(self, url: str, max_seconds: int | None = None):
        self.url = url
        self.max_seconds = max_seconds
        self.running = False
        self.procs: list[subprocess.Popen] = []
        self.tmpdir = tempfile.mkdtemp(prefix="telemost_")
        self.audio_file = os.path.join(self.tmpdir, "recording.ogg")
        self.ffmpeg: subprocess.Popen | None = None
        self.record_start = 0.0
        self.stop_reason = "manual"
        self.sink_module: str | None = None   # pactl module id of our null-sink
        self.silence_wav = os.path.join(self.tmpdir, "silence.wav")
        self.call_frame = None   # Frame hosting the meeting UI (embedded iframe)

    def _setup_xvfb(self):
        n = DISPLAY_NUM.lstrip(":")
        subprocess.run(["bash", "-c",
                        f"rm -f /tmp/.X{n}-lock /tmp/.X11-unix/X{n} 2>/dev/null"],
                       capture_output=True)
        proc = subprocess.Popen(
            ["Xvfb", DISPLAY_NUM, "-screen", "0", "1280x720x24", "-ac"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        self.procs.append(proc)
        time.sleep(1.5)
        os.environ["DISPLAY"] = DISPLAY_NUM
        print(f"[*] Xvfb {DISPLAY_NUM}", flush=True)

    def _setup_pulseaudio(self):
        if subprocess.run(["pulseaudio", "--check"], capture_output=True).returncode != 0:
            p = subprocess.Popen(["pulseaudio", "--start", "--daemonize=no",
                                  "--exit-idle-time=-1", "--log-target=stderr"],
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                 env=os.environ.copy())
            self.procs.append(p)
            time.sleep(2)
        # Per-recording null-sink. We deliberately do NOT set it as the global
        # default — concurrent recorders would race on it. Chromium is launched
        # with PULSE_SINK in its env, which libpulse honours to route this
        # browser's output to THIS sink (validated). Track the module id so we
        # can unload it on cleanup (unique sink names would otherwise leak).
        res = subprocess.run(
            ["pactl", "load-module", "module-null-sink",
             f"sink_name={PULSE_SINK}",
             f"sink_properties=device.description={PULSE_SINK}"],
            capture_output=True, text=True)
        self.sink_module = res.stdout.strip() if res.returncode == 0 else None
        # A capture source for getUserMedia (mic is muted in-UI; a shared null
        # source is fine — it's an input and never mixes into the recording,
        # which is the sink monitor). Load once if absent; route via env.
        srcs = subprocess.run(["pactl", "list", "short", "sources"],
                              capture_output=True, text=True).stdout
        if "rec_null_src" not in srcs:
            subprocess.run(["pactl", "load-module", "module-null-source",
                            "source_name=rec_null_src"], capture_output=True)
        os.environ["PULSE_SOURCE"] = "rec_null_src"
        print(f"[*] PulseAudio готов (sink={PULSE_SINK})", flush=True)

    def _make_silence_wav(self):
        # Chromium's built-in fake microphone emits periodic beeps. The bot must
        # never make a sound, so feed it a silent file instead — even if the
        # in-UI mic toggle fails, nothing audible goes out.
        subprocess.run(["ffmpeg", "-y", "-f", "lavfi", "-i", "anullsrc=r=48000:cl=mono",
                        "-t", "1", self.silence_wav], capture_output=True)

    def _start_ffmpeg(self):
        self.record_start = time.time()
        self.ffmpeg = subprocess.Popen(
            ["ffmpeg", "-y", "-f", "pulse", "-i", f"{PULSE_SINK}.monitor",
             "-ar", "16000", "-ac", "1", "-c:a", "libopus", "-b:a", "32k",
             self.audio_file],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        self.procs.append(self.ffmpeg)
        print(f"[*] Запись → {self.audio_file}", flush=True)

    def _ensure_off(self, target, device: str, timeout_sec: float = 8.0) -> None:
        """Force a lobby/in-call mic or camera toggle into the 'off' state.

        Telemost auto-starts a live mic/camera preview as soon as permission
        is granted (now that a fake device satisfies it), asynchronously and
        with no fixed timing — a single blind click on an unconditional
        selector can just as easily turn it ON as off. Poll the toggle's own
        testid instead: `turn-off-{device}-button` means it's currently on
        (click it), `turn-on-{device}-button` means it's already off (done).

        `target` is whatever exposes `.locator()` — the page or (since the
        2026-09 Telemost redesign moved the whole meeting UI into an
        embedded iframe) the meeting `Frame`.
        """
        deadline = time.time() + timeout_sec
        while time.time() < deadline:
            try:
                on_btn = target.locator(f'[data-testid="turn-off-{device}-button"]').first
                if on_btn.is_visible(timeout=500):
                    on_btn.evaluate("e => e.click()")
                    time.sleep(1.0)   # let the toggle flip before re-checking
                    continue
            except Exception:
                pass
            try:
                off_btn = target.locator(f'[data-testid="turn-on-{device}-button"]').first
                if off_btn.is_visible(timeout=500):
                    return
            except Exception:
                pass

    _DISMISS_OVERLAYS_JS = r"""
() => {
  for (const id of ['telemost-3-onboarding-close', 'telemost-3-onboarding-confirm',
                     'meeting-continue-in-browser-continue']) {
    const el = document.querySelector(`[data-testid="${id}"]`);
    if (el) el.click();
  }
}
"""

    def _get_call_frame(self, page, timeout_sec: float = 20.0):
        """Find the embedded iframe that now hosts the entire lobby/in-call
        UI (`telemost.yandex.ru/private-join/...`) — everything the old
        selectors targeted on `page` directly now lives inside it.

        Telemost's 2026-09 redesign also added two top-level overlays ahead
        of the lobby (an app-vs-browser nag, and a "what's new" onboarding
        card that visually sits ON TOP of the nag and blocks a real click on
        it). Both reappear every run — each recording gets a fresh browser
        context with no persisted cookies/localStorage — and their timing
        varies, so a raw JS click (bypassing Playwright's actionability/
        overlap checks, which the stacked overlay defeats) runs on every
        poll tick until the iframe shows up.
        """
        deadline = time.time() + timeout_sec
        while time.time() < deadline:
            try:
                page.evaluate(self._DISMISS_OVERLAYS_JS)
            except Exception:
                pass
            for f in page.frames:
                if f != page.main_frame and "private-join" in (f.url or ""):
                    return f
            time.sleep(0.5)
        return None

    def _join(self, page):
        page.goto(self.url, wait_until="domcontentloaded", timeout=40_000)
        frame = self._get_call_frame(page)
        if frame is None:
            print("[!] Не найден iframe встречи", flush=True)
            return
        self.call_frame = frame

        for sel in ['[data-testid="orb-textinput-input"]', 'input[type="text"]']:
            try:
                el = frame.locator(sel).first
                if el.is_visible(timeout=3_000):
                    el.fill(BOT_NAME); break
            except Exception:
                pass
        self._ensure_off(frame, "camera")
        self._ensure_off(frame, "mic")
        # Since the 2026-09 redesign a real (mouse) click on the join button is
        # a silent no-op in this automated Chromium; a DOM click() works. So
        # click via JS and confirm the lobby is actually gone before claiming
        # we joined.
        joined = False
        for sel in ['[data-testid="enter-conference-button"]', 'button:has-text("Подключиться")',
                    'button:has-text("Присоединиться")', 'button:has-text("Join")']:
            try:
                el = frame.locator(sel).first
                if not el.is_visible(timeout=3_000):
                    continue
                el.evaluate("e => e.click()")
                deadline = time.time() + 10
                while time.time() < deadline:
                    if not frame.locator('[data-testid="enter-conference-button"]').first.is_visible(timeout=300):
                        joined = True
                        break
                    time.sleep(0.5)
                break
            except Exception:
                pass
        print("[*] Вошёл" if joined else "[!] Не удалось войти: кнопка входа осталась в лобби", flush=True)
        page.wait_for_timeout(5_000)
        self._ensure_off(frame, "camera", timeout_sec=3.0)
        self._ensure_off(frame, "mic", timeout_sec=3.0)
        print("[*] В конференции", flush=True)

    def _monitor_level_db(self) -> float | None:
        """Mean volume (dB) of ~2s sampled from the sink monitor, or None.

        A second short reader on the same Pulse monitor the recording ffmpeg
        uses — harmless, monitors allow multiple readers.
        """
        try:
            p = subprocess.run(
                ["ffmpeg", "-hide_banner", "-nostats",
                 "-f", "pulse", "-i", f"{PULSE_SINK}.monitor",
                 "-t", "2", "-af", "volumedetect", "-f", "null", "-"],
                capture_output=True, text=True, timeout=10)
            for line in p.stderr.splitlines():
                if "mean_volume:" in line:
                    return float(line.split("mean_volume:")[1].split("dB")[0].strip())
        except Exception:
            pass
        return None

    def run(self):
        self._setup_xvfb()
        self._setup_pulseaudio()

        self._make_silence_wav()
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=False,
                args=["--no-sandbox", "--disable-dev-shm-usage",
                      "--use-fake-ui-for-media-stream",
                      "--use-fake-device-for-media-stream",
                      f"--use-file-for-fake-audio-capture={self.silence_wav}",
                      "--disable-blink-features=AutomationControlled",
                      "--window-size=1280,720",
                      "--autoplay-policy=no-user-gesture-required"],
                env=os.environ.copy(),
            )
            ctx = browser.new_context(permissions=["camera", "microphone"],
                                      viewport={"width": 1280, "height": 720})
            page = ctx.new_page()
            page.on("dialog", lambda d: d.dismiss())

            self._join(page)
            self._start_ffmpeg()
            print("[*] RECORDING_STARTED", flush=True)
            print(f"[*] Запись идёт (автостоп: {SILENCE_LIMIT_SEC}s тишины / DOM / SIGTERM)\n",
                  flush=True)

            self.running = True
            saw_others = False
            empty = 0
            heard_audio = False
            silence_sec = 0
            last_sample = 0.0
            try:
                while self.running:
                    time.sleep(CHECK_INTERVAL)
                    if self.ffmpeg and self.ffmpeg.poll() is not None:
                        self.stop_reason = "ffmpeg"; break
                    if self.max_seconds and (time.time() - self.record_start) >= self.max_seconds:
                        self.stop_reason = "maxcap"; break

                    # Silence-based auto-stop (primary, CSS-independent).
                    now = time.time()
                    if now - last_sample >= SILENCE_SAMPLE_SEC:
                        last_sample = now
                        lvl = self._monitor_level_db()
                        if lvl is not None:
                            if lvl > SILENCE_DB:
                                heard_audio = True
                                silence_sec = 0
                            elif heard_audio:
                                silence_sec += SILENCE_SAMPLE_SEC
                                if silence_sec >= SILENCE_LIMIT_SEC:
                                    self.stop_reason = "silence"; break

                    # DOM signals (best-effort fast path; brittle to Yandex CSS churn).
                    # Since 2026-09 the whole meeting UI lives in an embedded
                    # iframe, so this must run in that Frame's context, not page's.
                    if self.call_frame is None:
                        continue
                    try:
                        st = self.call_frame.evaluate(_AUTOSTOP_JS)
                    except Exception:
                        continue
                    others = int(st.get("others", 0))
                    if others > 0:
                        saw_others = True
                    if st.get("ended") or st.get("rejoin"):
                        self.stop_reason = "ended"; break
                    if saw_others and others == 0:
                        empty += CHECK_INTERVAL
                        if empty >= EMPTY_LIMIT_SEC:
                            self.stop_reason = "empty"; break
                    else:
                        empty = 0
            except KeyboardInterrupt:
                self.stop_reason = "manual"

            self.running = False
            try:
                browser.close()
            except Exception:
                pass

        if self.ffmpeg and self.ffmpeg.poll() is None:
            self.ffmpeg.send_signal(signal.SIGINT)
            try:
                self.ffmpeg.wait(timeout=8)
            except subprocess.TimeoutExpired:
                self.ffmpeg.kill()

        duration = int(time.time() - self.record_start)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        Path(OUTPUT_DIR).mkdir(parents=True, exist_ok=True)
        # pid suffix so concurrent recorders never collide on the same second
        out = Path(OUTPUT_DIR) / f"telemost_{ts}_{os.getpid()}.ogg"
        try:
            shutil.copy(self.audio_file, out)
        except Exception as e:
            print(f"[!] copy failed: {e}", flush=True)
            out = Path(self.audio_file)

        self._cleanup()

        print(f"RECORDING_SAVED: {out}", flush=True)
        print(f"DURATION_SEC: {duration}", flush=True)
        print(f"STOP_REASON: {self.stop_reason}", flush=True)

    def _cleanup(self):
        for proc in reversed(self.procs):
            try:
                proc.terminate(); proc.wait(timeout=2)
            except Exception:
                try: proc.kill()
                except Exception: pass
        # unload our per-recording null-sink so concurrent/repeat runs don't leak
        if self.sink_module:
            subprocess.run(["pactl", "unload-module", self.sink_module],
                           capture_output=True)
            self.sink_module = None


def main():
    if len(sys.argv) < 2:
        print("Usage: python -m app.telemost.recorder <url> [max_seconds]")
        sys.exit(1)
    url = sys.argv[1]
    max_s = int(sys.argv[2]) if len(sys.argv) > 2 else None
    rec = Recorder(url, max_s)
    signal.signal(signal.SIGTERM, lambda s, f: setattr(rec, "running", False))
    try:
        rec.run()
    except Exception as e:
        print(f"[!] Ошибка: {e}", flush=True)
        rec._cleanup()
        print("STOP_REASON: error", flush=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
