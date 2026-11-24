"""Chrome on a dedicated profile, attached over CDP.

Chrome >= 136 refuses remote debugging on the default profile, so the tool
keeps its own profile under ``~/.cache/mtga-collection/`` (outside the
repository; it holds nothing but the Untapped.gg login, which is never
printed).  ``login`` starts that profile as a plain Chrome so Google /
Discord / Twitch sign-in works; every other live command starts the same
binary on the same profile with ``--remote-debugging-port=0`` and attaches
Playwright over CDP.  A session always closes the Chrome it started.
"""

from __future__ import annotations

import contextlib
import os
import re
import signal
import subprocess  # Chrome and /bin/ps only, no shell  # nosec B404
import sys
import time
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterator

    from playwright.sync_api import BrowserContext, Page

DEFAULT_BINARY = Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
DEFAULT_STATE_DIR = Path("~/.cache/mtga-collection").expanduser()
#: seconds to wait for Chrome to quit, open its DevTools port, show a page
QUIT_GRACE = 20.0
QUIT_TIMEOUT = 30.0
DEVTOOLS_TIMEOUT = 40.0
PAGE_WAIT = 15.0
#: Playwright's timeouts, in milliseconds
CONNECT_TIMEOUT_MS = 30_000
PAGE_TIMEOUT_MS = 60_000
#: the exit status of a process ended by SIGTERM
SIGTERM_STATUS = 143


class ChromeConfig:
    """Where the Chrome binary is and where the tool keeps its state."""

    def __init__(
        self,
        binary: Path | str = DEFAULT_BINARY,
        state_dir: Path | str = DEFAULT_STATE_DIR,
        profile_dir: Path | str | None = None,
    ) -> None:
        """Expand the paths; the profile defaults to ``<state_dir>/chrome-profile``."""
        self.binary = Path(binary).expanduser()
        self.state_dir = Path(state_dir).expanduser()
        self.profile_dir = (
            Path(profile_dir).expanduser()
            if profile_dir
            else self.state_dir / "chrome-profile"
        )

    @property
    def pid_file(self) -> Path:
        """Return the file recording the Chrome this tool started."""
        return self.state_dir / "chrome.pid"

    def check(self) -> None:
        """Stop unless the Chrome binary exists."""
        if not self.binary.exists():
            msg = f"Chrome binary not found: {self.binary} (pass --chrome-binary)"
            raise SystemExit(msg)


# ------------------------------------------------------------------ processes


def _pid_alive(pid: int) -> bool:
    """Tell whether a process with *pid* exists."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _cmdline(pid: int) -> str:
    """Return the command line of *pid*, or an empty string."""
    try:
        return subprocess.run(  # noqa: S603 - fixed argv, no shell  # nosec B603
            ["/bin/ps", "-o", "command=", "-p", str(pid)],
            capture_output=True,
            text=True,
            check=False,
        ).stdout
    except OSError:
        return ""


def _profile_owner(profile: Path) -> int | None:
    """Return the PID of a live Chrome holding the profile's SingletonLock."""
    try:
        target = str((profile / "SingletonLock").readlink())
    except OSError:
        return None
    match = re.search(r"-(\d+)$", target)
    if not match:
        return None
    pid = int(match.group(1))
    return pid if _pid_alive(pid) else None


def _terminate_child(proc: subprocess.Popen[bytes], grace: float = QUIT_GRACE) -> None:
    """SIGTERM our own Chrome and reap it.

    A zombie would otherwise look alive to ``os.kill(pid, 0)``.
    """
    if proc.poll() is not None:
        return
    proc.terminate()
    try:
        proc.wait(timeout=grace)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=5)


def recorded_chrome(cfg: ChromeConfig) -> int | None:
    """Return the PID of the Chrome a previous run recorded, if still on our profile."""
    if not cfg.pid_file.exists():
        return None
    try:
        pid = int(cfg.pid_file.read_text(encoding="utf-8").strip())
    except ValueError:
        pid = 0
    if pid and _pid_alive(pid) and str(cfg.profile_dir) in _cmdline(pid):
        return pid
    cfg.pid_file.unlink(missing_ok=True)
    return None


def profile_owner(cfg: ChromeConfig) -> int | None:
    """Return the PID of a live Chrome on the tool's profile, if any."""
    return _profile_owner(cfg.profile_dir)


def ensure_profile_free(cfg: ChromeConfig) -> None:
    """Stop when a Chrome still holds the tool's profile.

    Chrome writes cookies to disk on a ~30 s timer or on a clean quit, never
    on SIGTERM, so a lingering ``login`` window is not killed: the user is
    asked to quit it, which keeps a sign-in completed shortly before.
    """
    owner = profile_owner(cfg) or recorded_chrome(cfg)
    if owner:
        msg = (
            f"A Chrome window (pid {owner}) is still open on the tool's profile "
            f"{cfg.profile_dir}.\nQuit it with Cmd+Q in that window (so Chrome saves "
            "the login), then retry."
        )
        raise SystemExit(msg)


# ------------------------------------------------------------- launch / attach


def launch_chrome(
    cfg: ChromeConfig, url: str, *, debug: bool
) -> subprocess.Popen[bytes]:
    """Start Chrome on the tool's profile at *url*; with *debug*, on a CDP port."""
    cfg.check()
    cfg.state_dir.mkdir(parents=True, exist_ok=True)
    cfg.state_dir.chmod(0o700)
    cfg.profile_dir.mkdir(parents=True, exist_ok=True)
    cfg.profile_dir.chmod(0o700)
    args = [
        str(cfg.binary),
        f"--user-data-dir={cfg.profile_dir}",
        "--no-first-run",
        "--no-default-browser-check",
        "--window-size=1280,900",
        "--new-window",
    ]
    if debug:
        (cfg.profile_dir / "DevToolsActivePort").unlink(missing_ok=True)
        args.append("--remote-debugging-port=0")
    args.append(url)
    proc = subprocess.Popen(  # noqa: S603 - the configured Chrome binary, no shell  # nosec B603
        args,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    cfg.pid_file.write_text(str(proc.pid), encoding="utf-8")
    return proc


def wait_for_devtools(
    cfg: ChromeConfig,
    proc: subprocess.Popen[bytes],
    timeout: float = DEVTOOLS_TIMEOUT,
) -> str:
    """Return the DevTools websocket address Chrome writes into its profile."""
    port_file = cfg.profile_dir / "DevToolsActivePort"
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            msg = (
                "Chrome exited right away. Another Chrome is probably already "
                f"running on the tool's profile ({cfg.profile_dir}); quit it and retry."
            )
            raise SystemExit(msg)
        if port_file.exists():
            match port_file.read_text(encoding="utf-8").splitlines():
                case [port, path, *_] if port.strip().isdigit():
                    return f"ws://127.0.0.1:{port.strip()}{path.strip()}"
        time.sleep(0.2)
    msg = "Chrome did not open its DevTools port in time"
    raise SystemExit(msg)


def stop_chrome(cfg: ChromeConfig, proc: subprocess.Popen[bytes]) -> None:
    """End the Chrome this run started and forget its PID."""
    _terminate_child(proc)
    cfg.pid_file.unlink(missing_ok=True)


def quit_chrome(
    context: BrowserContext,
    page: Page,
    proc: subprocess.Popen[bytes],
    timeout: float = QUIT_TIMEOUT,
) -> None:
    """Ask Chrome to quit like the user did (CDP ``Browser.close``), else SIGTERM it.

    Chrome writes its cookie store to disk on a ~30 s timer and on a clean
    application quit, but not on SIGTERM (measured 2026-10-01 with Chrome
    154: a cookie set 20 s before SIGTERM was lost, one set right before
    ``Browser.close`` survived).  Without this, a login completed shortly
    before the window is closed is gone on the next start.
    """
    with contextlib.suppress(Exception):  # Chrome may already be gone
        context.new_cdp_session(page).send("Browser.close")
    try:
        proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        _terminate_child(proc)


def _first_page(context: BrowserContext) -> Page:
    """Return the page Chrome opened at start, or a new one."""
    deadline = time.monotonic() + PAGE_WAIT
    while not context.pages and time.monotonic() < deadline:
        time.sleep(0.2)
    return context.pages[0] if context.pages else context.new_page()


def _exit_on_sigterm(*_: object) -> None:
    """Leave through SystemExit, so the session's cleanup closes Chrome."""
    sys.exit(SIGTERM_STATUS)


@contextmanager
def chrome_session(cfg: ChromeConfig, url: str) -> Iterator[Page]:
    """Yield a Playwright page on the tool's Chrome; always close Chrome after."""
    # Playwright is imported here, not at the top, so the offline commands,
    # the tests and the generator run without it
    from playwright.sync_api import sync_playwright  # noqa: PLC0415

    ensure_profile_free(cfg)
    proc = launch_chrome(cfg, url, debug=True)
    signal.signal(signal.SIGTERM, _exit_on_sigterm)
    try:
        endpoint = wait_for_devtools(cfg, proc)
        with sync_playwright() as playwright:
            browser = playwright.chromium.connect_over_cdp(
                endpoint,
                timeout=CONNECT_TIMEOUT_MS,
            )
            try:
                context = (
                    browser.contexts[0] if browser.contexts else browser.new_context()
                )
                page = _first_page(context)
                page.set_default_timeout(PAGE_TIMEOUT_MS)
                try:
                    yield page
                finally:
                    quit_chrome(context, page, proc)
            finally:
                with contextlib.suppress(Exception):  # Chrome has already quit
                    browser.close()  # disconnects only
    finally:
        stop_chrome(cfg, proc)
