#!/usr/bin/env python3
"""
Tidal Credits Fetcher

Fetch song credits from Tidal and export them to Kid3-compatible JSON.
"""

import argparse
import json
import os
import re
import subprocess
import sys
import threading
import time
try:
    from ctypes import windll
except ImportError:
    windll = None
from dataclasses import asdict, dataclass
from functools import cmp_to_key
from pathlib import Path
from urllib.parse import quote, quote_plus
from urllib.request import urlopen
import webbrowser

if os.name == "nt":
    import msvcrt
else:
    import select
    import termios
    import tty

try:
    from importlib.metadata import version as package_version
except ImportError:
    package_version = None

missing = []
try:
    import tidalapi
except ImportError:
    missing.append("tidalapi")
try:
    from rich import box
    from rich.align import Align
    from rich.console import Console, Group
    from rich.markup import escape
    from rich.panel import Panel
    from rich.progress import BarColumn, Progress, SpinnerColumn, TextColumn, TimeElapsedColumn
    from rich.rule import Rule
    from rich.table import Table
    from rich.text import Text
except ImportError:
    missing.append("rich")

if missing:
    sys.exit(f"Missing libraries. Run: pip install {' '.join(missing)}")

console = Console()

ACCENT = "bright_cyan"
DIM = "grey50"
SUCCESS = "green"
WARN = "yellow"
ERR = "red"

APP_DIR = Path(__file__).resolve().parent
APP_DATA_DIR = Path(os.getenv("APPDATA", str(Path.home()))) / "t2json"
SESSION_FILE = APP_DATA_DIR / "tidal_session.json"
SETTINGS_FILE = APP_DATA_DIR / "tidal_settings.json"
TAGS_CONFIG_FILE = APP_DATA_DIR / "tags.config"
BLACKLIST_CONFIG_FILE = APP_DATA_DIR / "blacklist.conf"
AUDIO_EXTS = {".flac", ".mp3", ".m4a", ".aiff", ".wav", ".ogg"}
LAUNCHER_MAX_WIDTH = 96
LAUNCHER_MIN_WIDTH = 56


def normalize_config_entry(value):
    value = (value or "").casefold()
    value = re.sub(r"[^a-z0-9]+", " ", value)
    return " ".join(value.split())


def get_app_version():
    if package_version:
        try:
            return package_version("t2json")
        except Exception:
            pass

    pyproject = APP_DIR / "pyproject.toml"
    if pyproject.exists():
        match = re.search(r'^version\s*=\s*"([^"]+)"', pyproject.read_text(encoding="utf-8"), re.M)
        if match:
            return match.group(1)
    return "dev"


APP_VERSION = get_app_version()


@dataclass
class AppSettings:
    show_results_table: bool = True
    show_failure_details: bool = True
    credits_save_dir: str = ""
    lastfm_api_key: str = ""
    fetch_genres: bool = False
    custom_tags: bool = False
    blacklisting: bool = False


ROLE_MAP = {
    "Composer": "Composer",
    "Lyricist": "Lyricist",
    "Writer": "Lyricist",
    "Producer": "Producer",
    "Co-Producer": "Co-Producer",
    "CoProducer": "Co-Producer",
    "Executive Producer": "Executive Producer",
    "ExecutiveProducer": "Executive Producer",
    "Mixer": "Mixing Engineer",
    "MixingEngineer": "Mixing Engineer",
    "Mixing Engineer": "Mixing Engineer",
    "Masterer": "Mastering Engineer",
    "MasteringEngineer": "Mastering Engineer",
    "Mastering Engineer": "Mastering Engineer",
    "Engineer": "Engineer",
    "RecordingEngineer": "Recording Engineer",
    "Recording Engineer": "Recording Engineer",
    "Additional Engineer": "Additional Engineer",
    "AdditionalEngineer": "Additional Engineer",
    "Assistant Engineer": "Assistant Engineer",
    "AssistantEngineer": "Assistant Engineer",
    "Guitar": "Guitar",
    "Guitarist": "Guitar",
    "AcousticGuitar": "Acoustic Guitar",
    "Acoustic Guitar": "Acoustic Guitar",
    "ElectricGuitar": "Electric Guitar",
    "Electric Guitar": "Electric Guitar",
    "Bass": "Bass",
    "Bassist": "Bass",
    "Drums": "Drums",
    "Drummer": "Drums",
    "Piano": "Piano",
    "Keyboards": "Keyboards",
    "Keyboard": "Keyboards",
    "Synthesizer": "Synthesizer",
    "Synth": "Synthesizer",
    "Programmer": "Programmer",
    "Programming": "Programmer",
    "Harp": "Harp",
    "Bells": "Bells",
    "Trumpet": "Trumpet",
    "Saxophone": "Saxophone",
    "Violin": "Violin",
    "Cello": "Cello",
    "Celli Cello": "Cello",
    "CelliCello": "Cello",
    "Vocals": "Vocals",
    "Backing Vocals": "Backing Vocals",
    "BackingVocals": "Backing Vocals",
    "Background Vocals": "Backing Vocals",
    "BackgroundVocals": "Backing Vocals",
    "Choir": "Choir",
    "Percussion": "Percussion",
    "String Arranger": "String Arranger",
    "StringArranger": "String Arranger",
    "Strings Arrangement": "String Arranger",
    "StringsArrangement": "String Arranger",
    "Orchestration Strings": "Orchestration Strings",
    "OrchestrationStrings": "Orchestration Strings",
    "Horn Arrangement": "Horn Arrangement",
    "HornArrangement": "Horn Arrangement",
    "Arrangement": "Arranger",
    "Arranger": "Arranger",
    "AssociatedPerformer": "Performer",
    "Associated Performer": "Performer",
    "MainArtist": "Artist",
    "FeaturedArtist": "Featured Artist",
    "Label": "Label",
    "Photography": "Photography",
    "Artwork": "Artwork",
}

DEDICATED = {
    "Composer": "Composer",
    "Lyricist": "Lyricist",
    "Producer": "Producer",
    "Co-Producer": "Producer",
    "Executive Producer": "Producer",
    "Mixing Engineer": "Mixer",
    "Mastering Engineer": "Masterer",
}

DEFAULT_EXPORT_FIELDS = (
    "Title",
    "Artist",
    "Album Artist",
    "Album",
    "Date",
    "Genre",
    "ISRC",
    "Comment",
    "Picture",
)

OPTIONAL_EXPORT_FIELDS = (
    "Track Number",
    "Disc Number",
    "BPM",
    "Copyright",
)

EXPORT_FIELD_ORDER = DEFAULT_EXPORT_FIELDS + OPTIONAL_EXPORT_FIELDS

FIELD_ALIASES = {}
for canonical, aliases in {
    "Title": ("title",),
    "Artist": ("artist",),
    "Album Artist": ("album artist", "albumartist"),
    "Album": ("album",),
    "Date": ("date", "year", "release date"),
    "Genre": ("genre", "genres"),
    "ISRC": ("isrc",),
    "Comment": ("comment",),
    "Picture": ("picture", "cover", "cover art", "picture cover front", "picture cover"),
    "Track Number": ("track number", "tracknumber"),
    "Disc Number": ("disc number", "discnumber"),
    "BPM": ("bpm",),
    "Copyright": ("copyright",),
}.items():
    for alias in aliases:
        FIELD_ALIASES[normalize_config_entry(alias)] = canonical


def load_json_file(path: Path, fallback):
    if not path.exists():
        return fallback
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return fallback


def save_json_file(path: Path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=4, ensure_ascii=False), encoding="utf-8")


def load_settings():
    defaults = AppSettings()
    raw = load_json_file(SETTINGS_FILE, {})
    settings = AppSettings(
        show_results_table=bool(raw.get("show_results_table", defaults.show_results_table)),
        show_failure_details=bool(raw.get("show_failure_details", defaults.show_failure_details)),
        credits_save_dir=str(raw.get("credits_save_dir", defaults.credits_save_dir)).strip(),
        lastfm_api_key=str(raw.get("lastfm_api_key", defaults.lastfm_api_key)).strip(),
        fetch_genres=bool(raw.get("fetch_genres", defaults.fetch_genres)),
        custom_tags=bool(raw.get("custom_tags", defaults.custom_tags)),
        blacklisting=bool(raw.get("blacklisting", defaults.blacklisting)),
    )
    if not SETTINGS_FILE.exists():
        save_settings(settings)
    return settings


def save_settings(settings):
    save_json_file(SETTINGS_FILE, asdict(settings))


def get_layout_width():
    terminal_width = console.size.width or LAUNCHER_MAX_WIDTH
    usable_width = terminal_width - 4
    return max(LAUNCHER_MIN_WIDTH, min(LAUNCHER_MAX_WIDTH, usable_width))


def get_two_column_widths(total_width):
    item_width = max(18, min(30, int(total_width * 0.38)))
    value_width = max(24, total_width - item_width - 6)
    return item_width, value_width


def format_on_off(value):
    return "[green]On[/]" if value else "[red]Off[/]"


def format_login_status():
    return "[green]Logged in[/]" if SESSION_FILE.exists() else "[red]Not logged in[/]"


def prompt_bool_setting(label, current):
    current_text = "Y" if current else "N"
    answer = console.input(
        f"  [cyan]{label}?[/] [Y, N, Enter = current: [bold]{current_text}[/]]: "
    ).strip().lower()
    if not answer:
        return current
    if answer in {"y", "yes", "1", "true", "on"}:
        return True
    if answer in {"n", "no", "0", "false", "off"}:
        return False
    warn(f"Invalid choice for {label}. Keeping {current_text}.")
    return current


def prompt_text_setting(label, current):
    answer = console.input(f"  [cyan]{label}[/] [Enter = current]: ").strip()
    if not answer:
        return current
    return answer


def format_save_dir(value):
    return value if value else "Current folder"


def format_config_path(path):
    if path.exists():
        return escape(str(path))
    return escape(f"{path.parent}{os.sep} (create {path.name} here)")


def format_custom_tags_status(settings):
    if not settings.custom_tags:
        return "[red]Off[/]"
    if TAGS_CONFIG_FILE.exists():
        return "[green]On[/]"
    return "[yellow]On (config missing)[/]"


def format_blacklisting_status(settings):
    if not settings.blacklisting:
        return "[red]Off[/]"
    if BLACKLIST_CONFIG_FILE.exists():
        return "[green]On[/]"
    return "[yellow]On (config missing)[/]"


def format_fetch_genres_status(settings):
    return f"{format_on_off(settings.fetch_genres)} [dim](Uses Last.fm and can be slower or limited)[/]"


def prompt_path_setting(label, current):
    answer = console.input(f"  [cyan]{label}[/] [Enter = current]: ").strip()
    if not answer:
        return current
    value = normalize_source_text(answer)
    if value in {".", ""}:
        return ""

    path = Path(value).expanduser()
    try:
        if path.exists() and not path.is_dir():
            raise ValueError("not a folder")
        path.mkdir(parents=True, exist_ok=True)
    except Exception:
        warn(f"Invalid folder path for {label}. Using Current folder.")
        return ""

    return str(path)


def show_settings(settings):
    layout_width = get_layout_width()
    setting_width, value_width = get_two_column_widths(layout_width)
    table = Table(
        title="[bold bright_cyan]Saved Settings[/]",
        title_style="not italic",
        box=box.ROUNDED,
        border_style=ACCENT,
        header_style="bold white",
        padding=(0, 1),
        width=layout_width,
        expand=False,
    )
    table.add_column("Setting", style=ACCENT, width=setting_width, no_wrap=False)
    table.add_column("Value", style="white", width=value_width, no_wrap=False)
    table.add_row("Tidal account", format_login_status())
    table.add_row("Show results table", format_on_off(settings.show_results_table))
    table.add_row("Show failure details", format_on_off(settings.show_failure_details))
    table.add_row("Credit file's path", format_save_dir(settings.credits_save_dir))
    table.add_row("Fetch Genres", format_fetch_genres_status(settings))
    table.add_row("Custom Tags", format_custom_tags_status(settings))
    table.add_row("Blacklisting", format_blacklisting_status(settings))
    table.add_row("Last.fm API key", "Set" if settings.lastfm_api_key else "Not set")
    console.print(table)
    console.print(f"  [dim]Config file:[/] [cyan]{escape(str(SETTINGS_FILE))}[/]")
    console.print(f"  [dim]Tags config:[/] [cyan]{format_config_path(TAGS_CONFIG_FILE)}[/]")
    console.print(f"  [dim]Blacklist config:[/] [cyan]{format_config_path(BLACKLIST_CONFIG_FILE)}[/]")
    console.print()


def show_launcher_menu(settings):
    layout_width = get_layout_width()
    item_width, value_width = get_two_column_widths(layout_width)
    menu = Table(
        # title="[bright_cyan]Settings[/]",
        title_style="not italic",
        box=box.ROUNDED,
        border_style=ACCENT,
        header_style="bold gold1",
        padding=(0, 1),
        width=layout_width,
        expand=False,
    )
    menu.add_column("Options", style=ACCENT, width=item_width, no_wrap=False)
    menu.add_column("Functions", style="white", width=value_width, no_wrap=False)
    menu.add_row("exit", "Close the application")
    menu.add_row("logout", "Sign out of the saved Tidal account")
    menu.add_row("settings", "Open settings editor")
    menu.add_row("help", "Show usage examples")
    menu.add_row("URL / folder / file / search", "Fetch credits from the source")
    menu.add_section()
    menu.add_row("[bold gold1]Setting[/]", "[bold gold1]Current value[/]", end_section=True)
    menu.add_row("Tidal account", format_login_status())
    menu.add_row("Show results table", format_on_off(settings.show_results_table))
    menu.add_row("Show failure details", format_on_off(settings.show_failure_details))
    menu.add_row("Credit file's path", format_save_dir(settings.credits_save_dir))
    menu.add_row("Fetch Genres", format_fetch_genres_status(settings))
    menu.add_row("Custom Tags", format_custom_tags_status(settings))
    menu.add_row("Blacklisting", format_blacklisting_status(settings))
    menu.add_row("Last.fm API key", "Set" if settings.lastfm_api_key else "Not set")
    console.print(menu)
    console.print()


def configure_settings(settings):
    show_settings(settings)

    settings.show_results_table = prompt_bool_setting("Show results table", settings.show_results_table)
    settings.show_failure_details = prompt_bool_setting("Show failure details", settings.show_failure_details)
    settings.credits_save_dir = prompt_path_setting("Credit file's path", settings.credits_save_dir)
    settings.fetch_genres = prompt_bool_setting("Fetch Genres", settings.fetch_genres)
    settings.custom_tags = prompt_bool_setting("Custom Tags", settings.custom_tags)
    settings.blacklisting = prompt_bool_setting("Blacklisting", settings.blacklisting)
    settings.lastfm_api_key = prompt_text_setting("Last.fm API key", settings.lastfm_api_key)

    save_settings(settings)
    console.print()
    ok(f"Settings saved to {SETTINGS_FILE.name}")
    console.print()
    show_settings(settings)
    return settings


def prompt_for_source(settings, show_home=False):
    while True:
        if show_home:
            console.clear()
            print_header()
            show_launcher_menu(settings)
        value = console.input("  [bold cyan]Enter source[/] [dim](or exit/logout/settings/help)[/]: ").strip()
        console.print()
        if not value:
            warn("Please enter a URL, ID, folder path, input file, or search text.")
            console.print()
            continue
        if value.lower() in {"0", "exit", "quit"}:
            return None
        if value.lower() in {"logout", "signout", "sign-out", "log out"}:
            return "__logout__"
        if value.lower() in {"1", "settings"}:
            settings = configure_settings(settings)
            show_home = True
            continue
        if value.lower() in {"2", "help"}:
            console.clear()
            print_header()
            print_usage()
            console.input("  [dim]Press Enter to go back...[/]")
            console.print()
            show_home = True
            continue
        return value


def normalize_source_text(source):
    source = source.strip()
    if len(source) >= 2 and source[0] == source[-1] and source[0] in {'"', "'"}:
        source = source[1:-1].strip()
    return source


def choose_search_result(tracks):
    visible_tracks = tracks[:5]
    table = Table(box=box.SIMPLE, show_header=True, header_style=f"bold {ACCENT}")
    table.add_column("#", width=4)
    table.add_column("Title", style="white")
    table.add_column("Artist", style=DIM)
    table.add_column("ID", style=DIM)
    for index, track in enumerate(visible_tracks):
        table.add_row(
            str(index),
            track.name,
            ", ".join(artist.name for artist in track.artists),
            str(track.id),
        )
    console.print(table)
    console.print("  [dim]Enter 0-4 to choose, or C to cancel and go back.[/]")

    while True:
        choice = console.input("  [cyan]Choose [0/C]:[/] ").strip()
        if not choice:
            return visible_tracks[0]
        lowered = choice.lower()
        if lowered in {"c", "cancel", "back"}:
            return None
        if not choice.isdigit():
            warn("Please enter a valid number from the list, or C to cancel.")
            continue

        index = int(choice)
        if 0 <= index < len(visible_tracks):
            return visible_tracks[index]

        warn(f"Please enter a number between 0 and {len(visible_tracks) - 1}, or C to cancel.")


def status(message, style=DIM):
    console.print(f"  [dim]>[/] {message}", style=style)


def ok(message):
    console.print(f"  [green]OK[/] {message}")


def warn(message):
    console.print(f"  [yellow]WARN[/] {message}")


def fail(message):
    console.print(f"  [red]ERR[/] {message}")


def handle_interrupt():
    console.print()
    warn("Cancelled by user. Exiting the app.")
    console.print()


class ProcessCancellation(Exception):
    pass


class CancelListener:
    def __init__(self):
        self.cancelled = threading.Event()
        self.stop_event = threading.Event()
        self.thread = None
        self.fd = None
        self.old_term = None

    def __enter__(self):
        if not sys.stdin or not sys.stdin.isatty():
            return self
        if os.name == "nt":
            self.thread = threading.Thread(target=self._windows_loop, daemon=True)
            self.thread.start()
            return self
        try:
            self.fd = sys.stdin.fileno()
            self.old_term = termios.tcgetattr(self.fd)
            tty.setcbreak(self.fd)
            self.thread = threading.Thread(target=self._posix_loop, daemon=True)
            self.thread.start()
        except Exception:
            self.fd = None
            self.old_term = None
        return self

    def __exit__(self, exc_type, exc, tb):
        self.stop_event.set()
        if self.thread and self.thread.is_alive():
            self.thread.join(timeout=0.2)
        if self.fd is not None and self.old_term is not None:
            try:
                termios.tcsetattr(self.fd, termios.TCSADRAIN, self.old_term)
            except Exception:
                pass
        return False

    def _windows_loop(self):
        while not self.stop_event.is_set():
            try:
                if msvcrt.kbhit():
                    key = msvcrt.getwch()
                    if key == " ":
                        self.cancelled.set()
                        return
                    if key in {"\x00", "à"}:
                        msvcrt.getwch()
                else:
                    time.sleep(0.05)
            except Exception:
                return

    def _posix_loop(self):
        while not self.stop_event.is_set():
            try:
                ready, _, _ = select.select([sys.stdin], [], [], 0.05)
                if ready:
                    key = sys.stdin.read(1)
                    if key == " ":
                        self.cancelled.set()
                        return
            except Exception:
                return

    def raise_if_cancelled(self):
        if self.cancelled.is_set():
            raise ProcessCancellation


def print_header():
    layout_width = get_layout_width()
    title = Text("TIDAL CREDITS FETCHER", style=f"bold {ACCENT}")
    subtitle = Text("Tidal song credits to JSON", style=DIM)
    version_line = Text(f"Version {APP_VERSION}  |  By Chetan", style=DIM)
    body = Group(
        Align.center(title),
        Align.center(subtitle),
        Align.center(version_line),
    )
    console.print()
    console.print(Panel(body, border_style=ACCENT, padding=(1, 1), expand=True, width=get_layout_width()))
    console.print()


def print_usage():
    layout_width = get_layout_width()
    flag_width = max(14, min(18, int(layout_width * 0.2)))
    example_width = max(10, min(22, int(layout_width * 0.24)))
    description_width = max(20, layout_width - flag_width - example_width - 8)
    table = Table(
        title="[bold bright_cyan]Available Commands [/]",
        title_style="not italic",
        box=box.ROUNDED,
        border_style=ACCENT,
        header_style="bold white",
        padding=(0, 1),
        width=layout_width,
        expand=False,
    )
    table.add_column("Flag", style=ACCENT, no_wrap=False, width=flag_width)
    table.add_column("Description", style="white", no_wrap=False, width=description_width)
    table.add_column("Example", style=DIM, no_wrap=False, width=example_width)

    rows = [
        ("source", "URL, ID, folder, input file, or search text", "https://tidal.com/browse/album/123"),
        ("--output", "Output filename", "credits.json"),
        ("--logout", "Delete the saved Tidal session and sign out locally", ""),
        ("--settings", "Open saved settings editor", ""),
        ("--reset-settings", "Reset saved settings to defaults", ""),
    ]

    for flag, description, example in rows:
        table.add_row(flag, description, example)

    console.print(table)
    console.print(f"  [dim]Settings file:[/] [cyan]{SETTINGS_FILE.name}[/]")
    console.print()


def save_session(session):
    save_json_file(
        SESSION_FILE,
        {
            "token_type": session.token_type,
            "access_token": session.access_token,
            "refresh_token": session.refresh_token,
            "expiry_time": str(session.expiry_time) if session.expiry_time else None,
        },
    )


def clear_saved_session():
    if not SESSION_FILE.exists():
        return False
    try:
        SESSION_FILE.unlink()
        return True
    except Exception:
        return None


def normalize_browser_url(url):
    url = (url or "").strip()
    if not url:
        return url
    if re.match(r"^[a-z][a-z0-9+.-]*://", url, re.I):
        return url
    if re.match(r"^[a-z0-9.-]+\.[a-z]{2,}([/:?#].*)?$", url, re.I):
        return f"https://{url}"
    return url


def open_in_default_browser(url):
    url = normalize_browser_url(url)

    try:
        if "com.termux" in os.environ.get("PREFIX", "") or os.environ.get("TERMUX_VERSION"):
            subprocess.Popen(["termux-open-url", url])
            return True
        if os.name == "nt":
            os.startfile(url)
            return True
        if sys.platform == "darwin":
            subprocess.Popen(["open", url])
            return True
        subprocess.Popen(["xdg-open", url])
        return True
    except Exception:
        try:
            return bool(webbrowser.open(url))
        except Exception:
            return False


def load_session(settings):
    session = tidalapi.Session()
    if SESSION_FILE.exists():
        try:
            data = load_json_file(SESSION_FILE, {})
            loaded = session.load_oauth_session(
                data["token_type"],
                data["access_token"],
                data["refresh_token"],
                data.get("expiry_time"),
            )
            if loaded and session.check_login():
                ok("Tidal session resumed")
                return session
        except Exception:
            pass

    console.print(
        Panel(
            "[white]No saved session found.\n\n"
            "A login link will appear below. Open it in your browser "
            "and log in to Tidal. The script will continue automatically.[/]",
            title="[bold yellow]Login Required[/]",
            border_style=WARN,
            padding=(1, 2),
        )
    )
    login, future = session.login_oauth()
    login_url = normalize_browser_url(login.verification_uri_complete)
    browser_opened = open_in_default_browser(login_url)

    if browser_opened:
        ok("Opened Tidal login in your browser")
        console.print(f"  [dim]If the browser did not open correctly, use:[/] [link]{login_url}[/link]\n")
    else:
        console.print("\n  [bold bright_cyan]Open this URL in your browser:[/]\n")
        console.print(f"  [link]{login_url}[/link]\n")

    with console.status("[yellow]Waiting for login...[/]", spinner="dots"):
        future.result()
    if not session.check_login():
        sys.exit("Login failed.")
    save_session(session)
    ok("Logged in and saved session for next time")
    return session


def extract_track_id(value):
    value = value.strip()
    if value.isdigit():
        return int(value)
    for part in value.replace("?", "/").split("/"):
        part = part.split("?")[0]
        if part.isdigit():
            return int(part)
    return None


def extract_album_id(value):
    value = value.strip()
    if value.isdigit():
        return int(value)
    match = re.search(r"/album/(\d+)", value, re.I)
    if match:
        return int(match.group(1))
    return None


def extract_playlist_id(value):
    match = re.search(
        r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
        value,
        re.I,
    )
    return match.group(0) if match else None


def first_tag_value(audio_file, *keys):
    if audio_file is None:
        return ""

    def clean(value):
        if value is None:
            return ""
        if isinstance(value, list):
            value = value[0] if value else ""
        text = str(value).strip()
        return text

    for key in keys:
        try:
            if key in audio_file:
                value = clean(audio_file[key])
                if value:
                    return value
        except Exception:
            pass

    tags = getattr(audio_file, "tags", None)
    if not tags:
        return ""

    for key in keys:
        try:
            value = tags.get(key)
        except Exception:
            value = None
        value = clean(value)
        if value:
            return value

    return ""


def get_audio_metadata(filepath):
    metadata = {
        "title": "",
        "album": "",
        "artist": "",
        "album_artist": "",
        "year": "",
        "isrc": "",
    }
    try:
        import mutagen

        audio_file = mutagen.File(filepath)
        if audio_file is None:
            return metadata

        metadata["title"] = first_tag_value(audio_file, "title", "TITLE", "TIT2")
        metadata["album"] = first_tag_value(audio_file, "album", "ALBUM", "TALB")
        metadata["artist"] = first_tag_value(audio_file, "artist", "ARTIST", "TPE1")
        metadata["album_artist"] = first_tag_value(
            audio_file,
            "albumartist",
            "ALBUMARTIST",
            "album artist",
            "TPE2",
        )
        metadata["year"] = first_tag_value(
            audio_file,
            "date",
            "DATE",
            "year",
            "YEAR",
            "originaldate",
            "ORIGINALDATE",
            "TDRC",
            "TDOR",
        )
        metadata["isrc"] = first_tag_value(audio_file, "isrc", "ISRC", "TSRC")
    except Exception:
        pass
    return metadata


def get_file_isrc(filepath):
    return get_audio_metadata(filepath).get("isrc") or None


def get_credits(session, track_id):
    items = []
    seen = set()

    def add(role, name):
        role = role.strip()
        name = name.strip()
        if name and (role, name) not in seen:
            seen.add((role, name))
            items.append({"role": role, "name": name})

    try:
        response = session.request.request(
            "GET",
            f"tracks/{track_id}/credits",
            params={
                "countryCode": session.country_code,
                "includeContributorDefaultRoles": "true",
                "limit": 500,
            },
        )
        data = response.json()
        credit_list = data if isinstance(data, list) else data.get("items", [])
        for group in credit_list:
            role = group.get("type", "Unknown")
            for contributor in group.get("contributors", []):
                add(role, contributor.get("name", ""))
    except Exception:
        pass

    try:
        response = session.request.request(
            "GET",
            f"tracks/{track_id}/contributors",
            params={"countryCode": session.country_code, "limit": 500},
        )
        for contributor in response.json().get("items", []):
            add(
                contributor.get("type") or contributor.get("role") or "Unknown",
                contributor.get("name", ""),
            )
    except Exception:
        pass

    return items


def normalise_role(raw):
    if not raw:
        return "Unknown"
    if raw in ROLE_MAP:
        return ROLE_MAP[raw]
    spaced = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", raw)
    return ROLE_MAP.get(spaced, spaced)


def format_credits(raw_items):
    grouped = {}
    for credit in raw_items:
        role = normalise_role(credit.get("role") or credit.get("type") or "Unknown")
        name = credit.get("name", "").strip()
        if not name:
            continue
        names = grouped.setdefault(role, [])
        if name not in names:
            names.append(name)
    return grouped


def credit_role_output_key(role):
    if role in {"Music Publisher", "Publisher"}:
        return None
    return DEDICATED.get(role, role)


def normalise_config_tag(raw):
    normalized = normalize_config_entry(raw)
    field = FIELD_ALIASES.get(normalized)
    if field:
        return field

    role = normalise_role((raw or "").strip())
    if role and role != "Unknown":
        return credit_role_output_key(role)
    return None


def load_tag_name_set(path, allow_bang_prefix=False):
    if not path.exists():
        return False

    allowed_tags = {}
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except Exception:
        return False

    for raw_line in lines:
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("!"):
            if not allow_bang_prefix:
                continue
            line = line[1:].strip()
            if not line:
                continue
        tag = normalise_config_tag(line)
        if not tag:
            continue
        allowed_tags[tag.casefold()] = tag

    return set(allowed_tags.values()) if allowed_tags else False


def load_custom_tags_filter(settings):
    if not settings.custom_tags:
        return None
    return load_tag_name_set(TAGS_CONFIG_FILE)


def load_blacklist_filter(settings):
    if not settings.blacklisting:
        return None
    return load_tag_name_set(BLACKLIST_CONFIG_FILE, allow_bang_prefix=True)


def filter_grouped_credits(grouped, allowed_tags=None, blocked_tags=None):
    if allowed_tags is None and not blocked_tags:
        return grouped

    filtered = {}
    for role, names in grouped.items():
        output_key = credit_role_output_key(role)
        if not output_key:
            continue
        if allowed_tags is not None and output_key not in allowed_tags:
            continue
        if blocked_tags and output_key in blocked_tags:
            continue
        filtered[role] = names
    return filtered


def extract_year_value(value):
    if not value:
        return ""

    if hasattr(value, "year"):
        try:
            year = int(value.year)
            if 1000 <= year <= 9999:
                return str(year)
        except Exception:
            pass

    text = str(value).strip()
    match = re.search(r"\b(19|20)\d{2}\b", text)
    return match.group(0) if match else ""


def normalized_name_tokens(value):
    value = (value or "").casefold()
    value = re.sub(r"[^a-z0-9]+", " ", value)
    return [token for token in value.split() if token]


def get_track_year(track):
    album = getattr(track, "album", None)
    # Match the year shown on TIDAL as closely as possible by trusting the
    # resolved album release date before falling back to track-level dates.
    candidates = []
    if album:
        candidates.extend(
            [
                getattr(album, "available_release_date", None),
                getattr(album, "release_date", None),
                getattr(album, "tidal_release_date", None),
                getattr(album, "stream_start_date", None),
                getattr(album, "year", None),
            ]
        )
    candidates.extend(
        [
            getattr(track, "release_date", None),
            getattr(track, "tidal_release_date", None),
            getattr(track, "stream_start_date", None),
            getattr(track, "year", None),
            getattr(album, "copyright", None) if album else None,
            getattr(track, "copyright", None),
        ]
    )

    for value in candidates:
        year = extract_year_value(value)
        if year:
            return year

    return ""


def get_album_artist(track):
    album = getattr(track, "album", None)
    if not album:
        return ", ".join(artist.name for artist in track.artists)

    album_artist = getattr(album, "artist", None)
    if album_artist and getattr(album_artist, "name", None):
        return album_artist.name

    album_artists = getattr(album, "artists", None)
    if album_artists:
        names = [artist.name for artist in album_artists if getattr(artist, "name", None)]
        if names:
            return ", ".join(names)

    return ", ".join(artist.name for artist in track.artists)


LASTFM_BLOCKED_TAGS = {
    "seen live",
    "favorites",
    "favourite",
    "favorite",
    "awesome",
    "love",
    "my favorites",
    "under 2000 listeners",
    "under 200 listeners",
    "male vocalists",
    "female vocalists",
    "spotify",
    "apple music",
    "youtube music",
    "soundcloud",
    "tidal",
}

LASTFM_LOCALE_TAGS = {
    "afrikaans",
    "arabic",
    "argentina",
    "argentinian",
    "australia",
    "australian",
    "belgian",
    "brazil",
    "brazilian",
    "british",
    "canada",
    "canadian",
    "chinese",
    "danish",
    "denmark",
    "dutch",
    "english",
    "european",
    "finnish",
    "finland",
    "french",
    "german",
    "greece",
    "greek",
    "hindi",
    "hungarian",
    "india",
    "indian",
    "indonesian",
    "irish",
    "italian",
    "japan",
    "japanese",
    "korean",
    "latin american",
    "mexican",
    "netherlands",
    "norway",
    "norwegian",
    "poland",
    "polish",
    "portugal",
    "portuguese",
    "romanian",
    "russian",
    "spain",
    "spanish",
    "sweden",
    "swedish",
    "turkish",
    "uk",
    "united kingdom",
    "united states",
    "usa",
}

LASTFM_GENRE_HINT_WORDS = {
    "acoustic",
    "alternative",
    "ambient",
    "blues",
    "classical",
    "country",
    "dance",
    "disco",
    "drum",
    "dub",
    "dubstep",
    "edm",
    "electro",
    "electronic",
    "emo",
    "folk",
    "funk",
    "garage",
    "gospel",
    "grime",
    "groove",
    "hardcore",
    "hardstyle",
    "hip",
    "hop",
    "house",
    "indie",
    "jazz",
    "latin",
    "lofi",
    "metal",
    "orchestral",
    "phonk",
    "pop",
    "punk",
    "rap",
    "reggae",
    "retro",
    "rnb",
    "rock",
    "shoegaze",
    "soul",
    "synth",
    "techno",
    "trance",
    "trap",
    "wave",
}

LASTFM_GENRE_HINT_PHRASES = {
    "drum and bass",
    "hip hop",
    "lo fi",
    "r and b",
    "singer songwriter",
}


def is_lastfm_locale_tag(tag):
    normalized = normalize_match_text(tag)
    return bool(normalized and normalized in LASTFM_LOCALE_TAGS)


def score_lastfm_tag(tag):
    normalized = normalize_match_text(tag)
    if not normalized:
        return -100

    score = 0
    if normalized in LASTFM_GENRE_HINT_PHRASES:
        score += 6

    tokens = normalized_name_tokens(normalized)
    if any(token in LASTFM_GENRE_HINT_WORDS for token in tokens):
        score += 5

    if " " in normalized or "-" in tag:
        score += 1

    if normalized in {"pop", "rock", "rap", "jazz", "folk", "country", "house", "techno", "trance", "edm"}:
        score += 2

    return score


def format_lastfm_tag_name(tag):
    formatted = tag.title()
    special = {
        "Edm": "EDM",
        "Idm": "IDM",
        "Rnb": "R&B",
    }
    return special.get(formatted, formatted)


def clean_lastfm_tag(tag, blocked_names=None):
    tag = (tag or "").strip()
    if not tag:
        return ""
    lowered = tag.lower()
    if lowered in LASTFM_BLOCKED_TAGS:
        return ""
    if any(char.isdigit() for char in lowered):
        return ""
    if len(tag) > 24:
        return ""
    if not re.fullmatch(r"[a-zA-Z][a-zA-Z &/+\-]*", tag):
        return ""
    if is_lastfm_locale_tag(tag):
        return ""

    normalized_tag = normalize_match_text(tag)
    if blocked_names:
        tag_tokens = set(normalized_name_tokens(normalized_tag))
        for name in blocked_names:
            normalized_name = normalize_match_text(name)
            if not normalized_name:
                continue
            if normalized_tag == normalized_name:
                return ""

            name_tokens = set(normalized_name_tokens(normalized_name))
            if tag_tokens and name_tokens and tag_tokens.issubset(name_tokens):
                return ""

    return format_lastfm_tag_name(tag)


def pick_best_lastfm_tags(candidates, blocked_names=None):
    ranked = []
    seen = set()
    for index, raw_name in enumerate(candidates):
        name = clean_lastfm_tag(raw_name, blocked_names=blocked_names)
        if not name:
            continue
        key = name.lower()
        if key in seen:
            continue
        seen.add(key)
        ranked.append((score_lastfm_tag(name), index, name))

    if not ranked:
        return []

    if any(score > 0 for score, _, _ in ranked):
        ranked = [item for item in ranked if item[0] > 0]

    ranked.sort(key=lambda item: (-item[0], item[1]))
    return [name for _, _, name in ranked[:2]]


def extract_lastfm_top_tags(payload, blocked_names=None):
    tags = payload.get("toptags", {}).get("tag", [])
    if isinstance(tags, dict):
        tags = [tags]
    return pick_best_lastfm_tags([item.get("name", "") for item in tags], blocked_names=blocked_names)


def fetch_lastfm_top_tags(method, params, api_key, blocked_names=None):
    query = {
        "method": method,
        "api_key": api_key,
        "format": "json",
        "autocorrect": "1",
    }
    query.update({key: value for key, value in params.items() if value})
    url = "https://ws.audioscrobbler.com/2.0/?" + "&".join(
        f"{quote_plus(str(key))}={quote_plus(str(value))}" for key, value in query.items()
    )
    try:
        with urlopen(url, timeout=8) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except Exception:
        return []
    return extract_lastfm_top_tags(payload, blocked_names=blocked_names)


def fetch_lastfm_page_tags(artist, title_or_album, blocked_names=None):
    if not artist or not title_or_album:
        return []

    url = f"https://www.last.fm/music/{quote_plus(artist)}/{quote(title_or_album, safe='')}"
    try:
        with urlopen(url, timeout=8) as response:
            html = response.read().decode("utf-8", errors="ignore")
    except Exception:
        return []

    matches = re.findall(r'href="/tag/([^"]+)"', html, flags=re.IGNORECASE)
    return pick_best_lastfm_tags(
        [raw.replace("+", " ").replace("%20", " ") for raw in matches],
        blocked_names=blocked_names,
    )


def get_track_genres(track, settings):
    api_key = settings.lastfm_api_key.strip()
    if not api_key or not settings.fetch_genres:
        return ""

    artist_names = [artist.name for artist in getattr(track, "artists", []) if getattr(artist, "name", None)]
    primary_artist = artist_names[0] if artist_names else ""
    title = getattr(track, "name", "") or getattr(track, "title", "")
    album_name = track.album.name if getattr(track, "album", None) else ""
    blocked_names = [title, album_name, *artist_names]

    genres = fetch_lastfm_top_tags(
        "track.getTopTags",
        {"artist": primary_artist, "track": title},
        api_key,
        blocked_names=blocked_names,
    )
    if genres:
        return ", ".join(genres)

    # Some Last.fm pages expose tags at the single/album level rather than the track API endpoint.
    genres = fetch_lastfm_top_tags(
        "album.getTopTags",
        {"artist": primary_artist, "album": album_name},
        api_key,
        blocked_names=blocked_names,
    )
    if genres:
        return ", ".join(genres)

    genres = fetch_lastfm_page_tags(primary_artist, title, blocked_names=blocked_names)
    if genres:
        return ", ".join(genres)

    genres = fetch_lastfm_page_tags(primary_artist, album_name, blocked_names=blocked_names)
    if genres:
        return ", ".join(genres)

    return ""


def stringify_tag_value(value):
    if value in (None, ""):
        return ""
    return str(value)


def build_base_row(track, settings):
    album = getattr(track, "album", None)
    return {
        "Title": track.name,
        "Artist": ", ".join(artist.name for artist in track.artists),
        "Album Artist": get_album_artist(track),
        "Album": album.name if album else "",
        "Date": get_track_year(track),
        "Genre": get_track_genres(track, settings),
        "ISRC": getattr(track, "isrc", ""),
        "Comment": "",
        "Picture": "",
        "Track Number": stringify_tag_value(getattr(track, "track_num", "")),
        "Disc Number": stringify_tag_value(getattr(track, "volume_num", "")),
        "BPM": stringify_tag_value(getattr(track, "bpm", "")),
        "Copyright": getattr(track, "copyright", "") or getattr(album, "copyright", "") or "",
    }


def select_base_row_fields(base_row, allowed_tags=None, blocked_tags=None):
    allowed_fields = set(DEFAULT_EXPORT_FIELDS) if allowed_tags is None else allowed_tags
    blocked_fields = blocked_tags or set()

    return {
        field: base_row[field]
        for field in EXPORT_FIELD_ORDER
        if field in allowed_fields and field not in blocked_fields
    }


def build_kid3_row(track, credits_grouped, settings, file_path="", allowed_tags=None, blocked_tags=None):
    row = select_base_row_fields(build_base_row(track, settings), allowed_tags=allowed_tags, blocked_tags=blocked_tags)

    dedicated_used = set()
    for role, names in credits_grouped.items():
        field = DEDICATED.get(role)
        if not field:
            continue
        new_value = ", ".join(names)
        existing = row.get(field, "")
        row[field] = f"{existing} / {new_value}".strip(" /") if existing else new_value
        dedicated_used.add(role)

    for role, names in credits_grouped.items():
        if role not in dedicated_used:
            field = credit_role_output_key(role)
            if field:
                row[field] = ", ".join(names)

    return row


def write_json(rows, output_path):
    if rows:
        save_json_file(output_path, {"data": rows})


def record_success(stats, track, grouped):
    stats["ok"].append(
        {
            "title": track.name,
            "artists": ", ".join(artist.name for artist in track.artists),
            "roles": len(grouped),
            "credits": sum(len(names) for names in grouped.values()),
        }
    )


def fetch_track_label(session, track_id):
    try:
        track = session.track(track_id, with_album=True)
        return track, track.name[:34]
    except Exception:
        return None, f"Track {track_id}"


def make_track_job(track_id, file_path="", track=None):
    return {
        "track_id": int(track_id),
        "file_path": file_path,
        "track": track,
    }


def resolve_numeric_id_source(session, value):
    track_id = extract_track_id(value)
    if track_id is None:
        return None

    try:
        track = session.track(track_id)
        if track and getattr(track, "id", None):
            return {"kind": "track_id", "value": track_id}
    except Exception:
        pass

    try:
        album = session.album(track_id)
        if album and getattr(album, "id", None):
            return {"kind": "album_id", "value": track_id}
    except Exception:
        pass

    return {"kind": "track_id", "value": track_id}


def detect_source(session, source):
    source = normalize_source_text(source)
    if not source:
        return None

    source_path = Path(source)
    if source_path.exists():
        if source_path.is_dir():
            return {"kind": "folder", "value": source}
        if source_path.suffix.lower() in AUDIO_EXTS:
            return {"kind": "audio_file", "value": source}
        return {"kind": "input", "value": source}

    lower_source = source.lower()
    if "tidal.com" in lower_source:
        if "/playlist/" in lower_source:
            playlist_id = extract_playlist_id(source)
            if playlist_id:
                return {"kind": "playlist_url", "value": source}
        if "/album/" in lower_source:
            album_id = extract_album_id(source)
            if album_id:
                return {"kind": "album_id", "value": str(album_id)}
        if "/track/" in lower_source:
            track_id = extract_track_id(source)
            if track_id:
                return {"kind": "track_id", "value": str(track_id)}

    if extract_playlist_id(source):
        return {"kind": "playlist_url", "value": source}

    if source.isdigit():
        detected = resolve_numeric_id_source(session, source)
        if detected:
            return detected

    if Path(source).suffix.lower() == ".txt":
        return {"kind": "input", "value": source}

    return {"kind": "search", "value": source}


def explorer_name_compare(left, right):
    if windll is not None:
        try:
            return windll.shlwapi.StrCmpLogicalW(str(left), str(right))
        except Exception:
            pass

    left_key = str(left).casefold()
    right_key = str(right).casefold()
    if left_key < right_key:
        return -1
    if left_key > right_key:
        return 1
    return 0


def natural_sort_key(value):
    return cmp_to_key(explorer_name_compare)(value)


def normalize_match_text(value):
    value = (value or "").casefold()
    value = re.sub(r"[\[\(].*?[\]\)]", " ", value)
    value = value.replace("&", " and ")
    value = re.sub(r"[^a-z0-9]+", " ", value)
    return " ".join(value.split())


def extract_metadata_year(value):
    return extract_year_value(value)


def title_matches(file_title, tidal_title):
    left = normalize_match_text(file_title)
    right = normalize_match_text(tidal_title)
    if not left or not right:
        return False
    return left == right or left in right or right in left


def artist_overlap(file_metadata, item):
    file_artists = []
    for key in ("artist", "album_artist"):
        raw = normalize_match_text(file_metadata.get(key, ""))
        if raw:
            file_artists.extend(part.strip() for part in re.split(r"\b(?:and|,|;|feat|featuring|ft)\b", raw) if part.strip())

    tidal_artists = []
    for artist in item.get("artists", []):
        name = normalize_match_text(artist.get("name", ""))
        if name:
            tidal_artists.append(name)
    album = item.get("album") or {}
    album_artist = normalize_match_text((album.get("artist") or {}).get("name", ""))
    if album_artist:
        tidal_artists.append(album_artist)

    return bool(file_artists and tidal_artists and any(a and a in b or b in a for a in file_artists for b in tidal_artists))


def score_isrc_match(item, file_metadata):
    score = 0
    album = item.get("album") or {}
    file_album = file_metadata.get("album", "")
    file_title = file_metadata.get("title", "")
    file_year = extract_metadata_year(file_metadata.get("year", ""))

    tidal_album = album.get("title") or album.get("name") or ""
    tidal_title = item.get("title") or item.get("name") or ""
    tidal_year = extract_year_value(
        item.get("releaseDate")
        or item.get("streamStartDate")
        or album.get("releaseDate")
        or album.get("streamStartDate")
        or album.get("year")
    )

    if file_album:
        left = normalize_match_text(file_album)
        right = normalize_match_text(tidal_album)
        if left and right:
            if left == right:
                score += 120
            elif left in right or right in left:
                score += 70

    if file_title and tidal_title:
        if title_matches(file_title, tidal_title):
            score += 60

    if artist_overlap(file_metadata, item):
        score += 35

    if file_year and tidal_year:
        if file_year == tidal_year:
            score += 25
        else:
            try:
                if abs(int(file_year) - int(tidal_year)) == 1:
                    score += 5
            except Exception:
                pass

    if file_album and not normalize_match_text(file_album) == normalize_match_text(tidal_album):
        compilation_words = {"greatest hits", "best of", "essentials", "hits", "mix", "playlist"}
        normalized_album = normalize_match_text(tidal_album)
        if any(word in normalized_album for word in compilation_words):
            score -= 20

    return score


def find_best_tidal_match_by_isrc(session, isrc, file_metadata):
    results = session.request.request(
        "GET",
        "tracks",
        params={"isrc": isrc, "countryCode": session.country_code, "limit": 50},
    ).json()
    items = results.get("items", [])
    if not items:
        return None

    scored = []
    for index, item in enumerate(items):
        scored.append((score_isrc_match(item, file_metadata), index, item))

    scored.sort(key=lambda row: (-row[0], row[1]))
    return scored[0][2]


def build_track_jobs(args, session, stats):
    track_jobs = []

    if args.track_id:
        track_jobs.append(make_track_job(args.track_id, args.file_path))
        return track_jobs

    if args.track_url:
        track_id = extract_track_id(args.track_url)
        if not track_id:
            sys.exit(f"Could not parse track ID from: {args.track_url}")
        track_jobs.append(make_track_job(track_id, args.file_path))
        return track_jobs

    if args.album_id:
        with console.status(f"[cyan]Fetching album {args.album_id}...[/]"):
            album = session.album(int(args.album_id))
            tracks = list(album.tracks())
        ok(f"Album: [bold]{album.name}[/] ({len(tracks)} tracks)")
        return [make_track_job(track.id, track=track) for track in tracks]

    if args.playlist_url:
        playlist_id = extract_playlist_id(args.playlist_url)
        if not playlist_id:
            sys.exit(f"Could not parse playlist ID from: {args.playlist_url}")
        with console.status("[cyan]Fetching playlist...[/]"):
            try:
                playlist = session.playlist(playlist_id)
                tracks = list(playlist.tracks())
            except Exception as exc:
                sys.exit(f"Could not fetch playlist: {exc}")
        ok(f"Playlist: [bold]{playlist.name}[/] ({len(tracks)} tracks)")
        return [make_track_job(track.id, track=track) for track in tracks]

    if args.folder:
        folder = Path(args.folder)
        if not folder.exists():
            sys.exit(f"Folder not found: {folder}")
        try:
            import mutagen  # noqa: F401
        except ImportError:
            sys.exit("Folder mode needs mutagen. Run: pip install mutagen")

        files = [
            path
            for path in sorted(folder.iterdir(), key=lambda item: natural_sort_key(item.name))
            if path.suffix.lower() in AUDIO_EXTS
        ]
        if not files:
            sys.exit(f"No audio files found in: {folder}")

        ok(f"Found [bold]{len(files)}[/] audio files in [cyan]{folder.name}[/]")
        console.print()

        for file_path in files:
            file_metadata = get_audio_metadata(file_path)
            isrc = file_metadata.get("isrc")
            if not isrc:
                warn(f"{file_path.name} - no ISRC tag, skipping")
                stats["failed"].append(f"{file_path.name} - no ISRC tag")
                continue
            try:
                match = find_best_tidal_match_by_isrc(session, isrc, file_metadata)
                if not match:
                    warn(f"{file_path.name} - no Tidal match for ISRC {isrc}")
                    stats["failed"].append(f"{file_path.name} - no Tidal match")
                    continue
                track_jobs.append(make_track_job(match["id"], str(file_path.resolve())))
            except Exception as exc:
                warn(f"{file_path.name} - ISRC lookup failed: {exc}")
                stats["failed"].append(f"{file_path.name} - {exc}")
        return track_jobs

    if args.file_path and Path(args.file_path).exists() and Path(args.file_path).suffix.lower() in AUDIO_EXTS:
        file_path = Path(args.file_path)
        file_metadata = get_audio_metadata(file_path)
        isrc = file_metadata.get("isrc")
        if not isrc:
            sys.exit(f"{file_path.name} has no ISRC tag.")
        try:
            match = find_best_tidal_match_by_isrc(session, isrc, file_metadata)
            if not match:
                sys.exit(f"No Tidal match found for ISRC {isrc}.")
            track_jobs.append(make_track_job(match["id"], str(file_path.resolve())))
        except Exception as exc:
            sys.exit(f"ISRC lookup failed for {file_path.name}: {exc}")
        return track_jobs

    if args.search:
        with console.status(f"[cyan]Searching: {args.search}...[/]"):
            results = session.search(args.search, [tidalapi.Track])
            tracks = results.get("tracks", [])
        if not tracks:
            sys.exit("No results found.")
        if len(tracks) > 1:
            track = choose_search_result(tracks)
            if track is None:
                warn("Search cancelled.")
                return track_jobs
        else:
            track = tracks[0]
        track_jobs.append(make_track_job(track.id, args.file_path))
        return track_jobs

    if args.input:
        lines = Path(args.input).read_text(encoding="utf-8").splitlines()
        ids = [extract_track_id(line) for line in lines if line.strip()]
        ids = [track_id for track_id in ids if track_id]
        ok(f"Loaded [bold]{len(ids)}[/] track IDs from {args.input}")
        return [make_track_job(track_id) for track_id in ids]

    if args.source:
        detected = detect_source(session, args.source)
        if not detected:
            return track_jobs

        kind = detected["kind"]
        value = detected["value"]
        status(f"Detected source type: {kind}")

        if kind == "track_id":
            track_jobs.append(make_track_job(value, args.file_path))
            return track_jobs

        if kind == "album_id":
            with console.status(f"[cyan]Fetching album {value}...[/]"):
                album = session.album(int(value))
                tracks = list(album.tracks())
            ok(f"Album: [bold]{album.name}[/] ({len(tracks)} tracks)")
            return [make_track_job(track.id, track=track) for track in tracks]

        if kind == "playlist_url":
            with console.status("[cyan]Fetching playlist...[/]"):
                try:
                    playlist = session.playlist(extract_playlist_id(value))
                    tracks = list(playlist.tracks())
                except Exception as exc:
                    sys.exit(f"Could not fetch playlist: {exc}")
            ok(f"Playlist: [bold]{playlist.name}[/] ({len(tracks)} tracks)")
            return [make_track_job(track.id, track=track) for track in tracks]

        if kind == "folder":
            args.folder = value
            return build_track_jobs(args, session, stats)

        if kind == "audio_file":
            args.file_path = value
            return build_track_jobs(args, session, stats)

        if kind == "input":
            args.input = value
            return build_track_jobs(args, session, stats)

        if kind == "search":
            args.search = value
            return build_track_jobs(args, session, stats)

    return track_jobs


def process_tracks(session, track_jobs, settings, stats, allowed_tags=None, blocked_tags=None):
    rows = []
    console.print("  [bold bright_cyan]Press Space to cancel and go back to menu.[/]")
    with CancelListener() as cancel_listener:
        with Progress(
            SpinnerColumn(style=ACCENT),
            TextColumn("[bold white]{task.description}"),
            BarColumn(bar_width=40, style="cyan", complete_style=ACCENT),
            TextColumn("[cyan]{task.completed}[/][dim]/{task.total}[/]"),
            TimeElapsedColumn(),
            console=console,
            transient=False,
        ) as progress:
            task = progress.add_task("Fetching credits", total=len(track_jobs))
            for job in track_jobs:
                cancel_listener.raise_if_cancelled()
                track_id = job["track_id"]
                file_path = job["file_path"]
                track = job.get("track")
                if track:
                    label = track.name[:34]
                else:
                    track, label = fetch_track_label(session, track_id)
                progress.update(task, description=f"[white]{label:<36}[/]")

                cancel_listener.raise_if_cancelled()
                try:
                    track = session.track(track_id, with_album=True)
                except Exception:
                    pass

                if not track:
                    stats["failed"].append(f"ID {track_id} - could not fetch")
                    progress.advance(task)
                    continue

                grouped = format_credits(get_credits(session, track_id))
                grouped = filter_grouped_credits(grouped, allowed_tags=allowed_tags, blocked_tags=blocked_tags)
                cancel_listener.raise_if_cancelled()
                rows.append(
                    build_kid3_row(
                        track,
                        grouped,
                        settings,
                        file_path=file_path,
                        allowed_tags=allowed_tags,
                        blocked_tags=blocked_tags,
                    )
                )
                record_success(stats, track, grouped)
                progress.advance(task)

    return rows

def print_results_table(stats):
    table = Table(
        box=box.SIMPLE_HEAD,
        border_style=ACCENT,
        header_style=f"bold {ACCENT}",
        show_edge=False,
        padding=(0, 1),
    )
    table.add_column("#", style=DIM, width=4, justify="right")
    table.add_column("Title", style="white", min_width=20)
    table.add_column("Artist", style=DIM, min_width=16)
    table.add_column("Roles", style=ACCENT, width=7, justify="center")
    table.add_column("Credits", style=SUCCESS, width=9, justify="center")

    for index, row in enumerate(stats["ok"], start=1):
        table.add_row(
            str(index),
            row["title"],
            row["artists"],
            str(row["roles"]),
            str(row["credits"]),
        )

    console.print(table)
    console.print()


def print_summary(stats, output_path, elapsed, settings):
    console.print()

    if stats["ok"] and settings.show_results_table:
        console.print("[bold bright_cyan]Results[/]")
        console.print()
        print_results_table(stats)

    if stats["failed"] and settings.show_failure_details:
        console.print(f"  [bold red]Failed ({len(stats['failed'])}):[/]")
        for message in stats["failed"]:
            console.print(f"    [red]-[/] {message}")
        console.print()

    total = len(stats["ok"]) + len(stats["failed"])
    success = len(stats["ok"])
    failed = len(stats["failed"])
    minutes, seconds = divmod(int(elapsed), 60)
    time_str = f"{minutes}m {seconds}s" if minutes else f"{seconds}s"

    summary = Table.grid(padding=(0, 3))
    summary.add_column(justify="center")
    summary.add_column(justify="center")
    summary.add_column(justify="center")
    summary.add_column(justify="center")
    summary.add_row(
        Text.assemble(("Total\n", DIM), (str(total), "bold white")),
        Text.assemble(("Found\n", DIM), (str(success), f"bold {SUCCESS}")),
        Text.assemble(("Failed\n", DIM), (str(failed), f"bold {ERR if failed else DIM}")),
        Text.assemble(("Time\n", DIM), (time_str, f"bold {ACCENT}")),
    )
    console.print(Panel(Align.center(summary), border_style=ACCENT, padding=(1, 4), expand=False))
    console.print()
    console.print(f"  File Saved to [bold cyan]{output_path}[/]")
    # console.print(f"  [dim]In Kid3: File -> Import -> select {output_path.name}[/]")
    console.print()


def clear_source_args(args):
    for attr in ["source", "track_id", "track_url", "album_id", "playlist_url", "folder", "search", "input"]:
        setattr(args, attr, None)
    args.file_path = ""


def run_fetch_job(args, settings, session, interactive_mode=False):
    stats = {"ok": [], "failed": []}
    start_time = time.time()
    output_path = Path(args.output)
    if not output_path.is_absolute():
        base_dir = Path.cwd()
        if settings.credits_save_dir:
            try:
                configured_dir = Path(settings.credits_save_dir).expanduser()
                configured_dir.mkdir(parents=True, exist_ok=True)
                base_dir = configured_dir
            except Exception:
                warn("Configured Credit file's path is invalid. Falling back to Current folder.")
                settings.credits_save_dir = ""
                save_settings(settings)
        output_path = base_dir / output_path

    track_jobs = build_track_jobs(args, session, stats)
    if not track_jobs:
        if not interactive_mode:
            warn("No tracks to process.")
        return False

    allowed_tags = load_custom_tags_filter(settings)
    if settings.custom_tags and allowed_tags is False:
        warn("Custom Tags is enabled, but tags.config was not found or has no valid tags.")
        warn("Add the exact fields and credit roles you want to export, then run again.")
        console.print()
        return False

    blocked_tags = load_blacklist_filter(settings)
    if settings.blacklisting and blocked_tags is False:
        warn("Blacklisting is enabled, but blacklist.conf was not found or has no valid tags.")
        warn("Continuing without blacklist rules for this run.")
        console.print()
        blocked_tags = None

    console.print()
    try:
        rows = process_tracks(
            session,
            track_jobs,
            settings,
            stats,
            allowed_tags=allowed_tags,
            blocked_tags=blocked_tags,
        )
    except ProcessCancellation:
        console.print()
        warn("Process cancelled. Returning to menu.")
        console.print()
        return False

    write_json(rows, output_path)
    save_session(session)
    print_summary(stats, output_path, time.time() - start_time, settings)
    return True


def build_parser():
    parser = argparse.ArgumentParser(
        description="Tidal Credits Fetcher - exports to Kid3 JSON",
        add_help=False,
    )
    parser.add_argument("source", nargs="?")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--track-id")
    group.add_argument("--track-url")
    group.add_argument("--album-id")
    group.add_argument("--playlist-url")
    group.add_argument("--folder")
    group.add_argument("--search")
    group.add_argument("--input")

    parser.add_argument("--output", default="tidal_credits.json")
    parser.add_argument("--file-path", default="")
    parser.add_argument("--logout", action="store_true")
    parser.add_argument("--settings", action="store_true")
    parser.add_argument("--reset-settings", action="store_true")
    parser.add_argument("-h", "--help", action="store_true")
    return parser


def apply_settings_actions(args, settings):
    if args.reset_settings:
        settings = AppSettings()
        save_settings(settings)
        ok(f"Settings reset to defaults in {SETTINGS_FILE.name}")
    if args.settings:
        settings = configure_settings(settings)
    return settings


def main(argv=None):
    try:
        parser = build_parser()
        args = parser.parse_args(argv)

        settings = load_settings()
        has_source = any(
            [
                args.source,
                args.track_id,
                args.track_url,
                args.album_id,
                args.playlist_url,
                args.folder,
                args.search,
                args.input,
            ]
        )

        if (args.settings or args.reset_settings) and not has_source and not args.help:
            print_header()
            apply_settings_actions(args, settings)
            return 0

        if args.logout and not has_source and not args.help:
            print_header()
            logout_result = clear_saved_session()
            if logout_result is True:
                ok("Saved Tidal session removed. You will be asked to log in next time.")
            elif logout_result is False:
                warn("No saved Tidal session was found.")
            else:
                fail(f"Could not remove saved session: {SESSION_FILE}")
            console.print()
            return 0

        no_source = not has_source
        if args.help:
            print_header()
            print_usage()
            return 0

        interactive_mode = no_source
        session = None

        if interactive_mode:
            show_home = True
            while True:
                clear_source_args(args)
                source = prompt_for_source(settings, show_home=show_home)
                if not source:
                    return 0
                if source == "__logout__":
                    logout_result = clear_saved_session()
                    if logout_result is True:
                        ok("Saved Tidal session removed. You will be asked to log in again.")
                        session = None
                    elif logout_result is False:
                        warn("No saved Tidal session was found.")
                    else:
                        fail(f"Could not remove saved session: {SESSION_FILE}")
                    console.print()
                    show_home = True
                    continue
                args.source = source
                show_home = False

                if session is None:
                    session = load_session(settings)
                    console.print()

                run_fetch_job(args, settings, session, interactive_mode=True)
        else:
            print_header()
            settings = apply_settings_actions(args, settings)
            session = load_session(settings)
            console.print()
            run_fetch_job(args, settings, session, interactive_mode=False)
        return 0
    except KeyboardInterrupt:
        handle_interrupt()
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
