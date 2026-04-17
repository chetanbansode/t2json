#!/usr/bin/env python3
"""
Tidal Credits Fetcher

Fetch song credits from Tidal and export them to Kid3-compatible JSON.
"""

import argparse
import json
import os
import re
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from urllib.parse import quote, quote_plus
from urllib.request import urlopen

missing = []
try:
    import tidalapi
except ImportError:
    missing.append("tidalapi")
try:
    from rich import box
    from rich.align import Align
    from rich.console import Console, Group
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
AUDIO_EXTS = {".flac", ".mp3", ".m4a", ".aiff", ".wav", ".ogg"}


@dataclass
class AppSettings:
    show_results_table: bool = True
    show_failure_details: bool = True
    progress_bar_width: int = 36
    save_session_on_exit: bool = True
    credits_save_dir: str = ""
    lastfm_api_key: str = ""
    fetch_genres: bool = False


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
    "Publisher": "Publisher",
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
    "Publisher": "Publisher",
    "Mixing Engineer": "Mixer",
    "Mastering Engineer": "Masterer",
}


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
        progress_bar_width=int(raw.get("progress_bar_width", defaults.progress_bar_width)),
        save_session_on_exit=bool(raw.get("save_session_on_exit", defaults.save_session_on_exit)),
        credits_save_dir=str(raw.get("credits_save_dir", defaults.credits_save_dir)).strip(),
        lastfm_api_key=str(raw.get("lastfm_api_key", defaults.lastfm_api_key)).strip(),
        fetch_genres=bool(raw.get("fetch_genres", defaults.fetch_genres)),
    )
    if not SETTINGS_FILE.exists():
        save_settings(settings)
    return settings


def save_settings(settings):
    save_json_file(SETTINGS_FILE, asdict(settings))


def format_on_off(value):
    return "[green]On[/]" if value else "[red]Off[/]"


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


def prompt_int_setting(label, current, minimum=1, maximum=80):
    answer = console.input(
        f"  [cyan]{label}[/] [[bold]{current}[/], {minimum}-{maximum}, Enter keeps current]: "
    ).strip()
    if not answer:
        return current
    try:
        value = int(answer)
    except ValueError:
        warn(f"Invalid number for {label}. Keeping {current}.")
        return current
    if value < minimum or value > maximum:
        warn(f"{label} must be between {minimum} and {maximum}. Keeping {current}.")
        return current
    return value


def prompt_text_setting(label, current):
    answer = console.input(f"  [cyan]{label}[/] [Enter = current]: ").strip()
    if not answer:
        return current
    return answer


def format_save_dir(value):
    return value if value else "Current folder"


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
    table = Table(
        title="[bold bright_cyan]Saved Settings[/]",
        title_style="not italic",
        box=box.ROUNDED,
        border_style=ACCENT,
        header_style="bold white",
        padding=(0, 1),
    )
    table.add_column("Setting", style=ACCENT)
    table.add_column("Value", style="white")
    table.add_row("Show results table", format_on_off(settings.show_results_table))
    table.add_row("Show failure details", format_on_off(settings.show_failure_details))
    table.add_row("Progress bar width", str(settings.progress_bar_width))
    table.add_row("Save session on exit", format_on_off(settings.save_session_on_exit))
    table.add_row("Credit file's path", format_save_dir(settings.credits_save_dir))
    table.add_row("Fetch genres", format_on_off(settings.fetch_genres))
    table.add_row("Last.fm API key", "Set" if settings.lastfm_api_key else "Not set")
    table.add_row("[dim]Note[/]", "[dim]Genre lookup uses Last.fm and can be slower or limited[/]")
    console.print(table)
    console.print(f"  [dim]Config file:[/] [cyan]{SETTINGS_FILE}[/]")
    console.print()


def show_launcher_menu(settings):
    menu = Table(
        title="[bright_cyan]Choices + Saved Settings[/]",
        title_style="not italic",
        box=box.ROUNDED,
        border_style=ACCENT,
        header_style="bold white",
        padding=(0, 1),
    )
    menu.add_column("Item", style=ACCENT, no_wrap=True)
    menu.add_column("Value", style="white")
    menu.add_row("exit", "Close the application")
    menu.add_row("settings", "Open settings editor")
    menu.add_row("help", "Show usage examples")
    menu.add_row("URL / ID / folder / file / search", "Fetch credits from the source")
    menu.add_section()
    menu.add_row("[dim]Setting[/]", "[dim]Current value[/]")
    menu.add_row("Show results table", format_on_off(settings.show_results_table))
    menu.add_row("Show failure details", format_on_off(settings.show_failure_details))
    menu.add_row("Progress bar width", str(settings.progress_bar_width))
    menu.add_row("Save session on exit", format_on_off(settings.save_session_on_exit))
    menu.add_row("Credit file's path", format_save_dir(settings.credits_save_dir))
    menu.add_row("Fetch genres", format_on_off(settings.fetch_genres))
    menu.add_row("Last.fm API key", "Set" if settings.lastfm_api_key else "Not set")
    menu.add_row("[dim]Note[/]", "[dim]Genre lookup uses Last.fm and can be slower or limited[/]")
    console.print(menu)
    console.print()


def configure_settings(settings):
    show_settings(settings)

    settings.show_results_table = prompt_bool_setting("Show results table", settings.show_results_table)
    settings.show_failure_details = prompt_bool_setting("Show failure details", settings.show_failure_details)
    settings.progress_bar_width = prompt_int_setting("Progress bar width", settings.progress_bar_width)
    settings.save_session_on_exit = prompt_bool_setting("Save session on exit", settings.save_session_on_exit)
    settings.credits_save_dir = prompt_path_setting("Credit file's path", settings.credits_save_dir)
    settings.fetch_genres = prompt_bool_setting("Fetch genres", settings.fetch_genres)
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
        value = console.input("  [bold cyan]Enter source[/] [dim](or settings/help/exit)[/]: ").strip()
        console.print()
        if not value:
            warn("Please enter a URL, ID, folder path, input file, or search text.")
            console.print()
            continue
        if value.lower() in {"0", "exit", "quit"}:
            return None
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


def print_header():
    title = Text("TIDAL CREDITS FETCHER", style=f"bold {ACCENT}")
    subtitle = Text("Tidal song credits to JSON", style=DIM)
    body = Group(
        Align.center(title),
        Align.center(subtitle),
    )
    console.print()
    console.print(Panel(body, border_style=ACCENT, padding=(1, 6), expand=False))
    console.print()


def print_usage():
    table = Table(
        title="[bold bright_cyan]Available Commands [/]",
        title_style="not italic",
        box=box.ROUNDED,
        border_style=ACCENT,
        header_style="bold white",
        padding=(0, 1),
    )
    table.add_column("Flag", style=ACCENT, no_wrap=True)
    table.add_column("Description", style="white")
    table.add_column("Example", style=DIM)

    rows = [
        ("source", "URL, ID, folder, input file, or search text", "https://tidal.com/browse/album/123"),
        ("--output", "Output filename", "credits.json"),
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


def load_session():
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
    console.print("\n  [bold bright_cyan]Open this URL in your browser:[/]\n")
    console.print(f"  [link]{login.verification_uri_complete}[/link]\n")
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


def get_file_isrc(filepath):
    try:
        import mutagen

        audio_file = mutagen.File(filepath)
        if audio_file is None:
            return None
        for key in ("isrc", "ISRC"):
            if key in audio_file:
                value = audio_file[key]
                return value[0] if isinstance(value, list) else str(value)
        if hasattr(audio_file, "tags") and audio_file.tags:
            tsrc = audio_file.tags.get("TSRC")
            if tsrc:
                return str(tsrc)
    except Exception:
        pass
    return None


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


def get_track_year(track):
    candidates = [
        getattr(track, "year", None),
        getattr(track, "release_date", None),
        getattr(track, "stream_start_date", None),
    ]

    album = getattr(track, "album", None)
    if album:
        candidates.extend(
            [
                getattr(album, "year", None),
                getattr(album, "release_date", None),
                getattr(album, "stream_start_date", None),
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
        return "; ".join(artist.name for artist in track.artists)

    album_artist = getattr(album, "artist", None)
    if album_artist and getattr(album_artist, "name", None):
        return album_artist.name

    album_artists = getattr(album, "artists", None)
    if album_artists:
        names = [artist.name for artist in album_artists if getattr(artist, "name", None)]
        if names:
            return "; ".join(names)

    return "; ".join(artist.name for artist in track.artists)


def clean_lastfm_tag(tag):
    tag = (tag or "").strip()
    if not tag:
        return ""
    lowered = tag.lower()
    blocked = {
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
    }
    if lowered in blocked:
        return ""
    if any(char.isdigit() for char in lowered):
        return ""
    if len(tag) > 24:
        return ""
    if not re.fullmatch(r"[a-zA-Z][a-zA-Z &/+\-]*", tag):
        return ""
    return tag.title()


def extract_lastfm_top_tags(payload):
    tags = payload.get("toptags", {}).get("tag", [])
    if isinstance(tags, dict):
        tags = [tags]

    genres = []
    seen = set()
    for item in tags:
        name = clean_lastfm_tag(item.get("name", ""))
        if not name:
            continue
        key = name.lower()
        if key in seen:
            continue
        seen.add(key)
        genres.append(name)
        if len(genres) == 2:
            break
    return genres


def fetch_lastfm_top_tags(method, params, api_key):
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
    return extract_lastfm_top_tags(payload)


def fetch_lastfm_page_tags(artist, title_or_album):
    if not artist or not title_or_album:
        return []

    url = f"https://www.last.fm/music/{quote_plus(artist)}/{quote(title_or_album, safe='')}"
    try:
        with urlopen(url, timeout=8) as response:
            html = response.read().decode("utf-8", errors="ignore")
    except Exception:
        return []

    matches = re.findall(r'href="/tag/([^"]+)"', html, flags=re.IGNORECASE)
    genres = []
    seen = set()
    for raw in matches:
        name = clean_lastfm_tag(raw.replace("+", " ").replace("%20", " "))
        if not name:
            continue
        key = name.lower()
        if key in seen:
            continue
        seen.add(key)
        genres.append(name)
        if len(genres) == 2:
            break
    return genres


def get_track_genres(track, settings):
    api_key = settings.lastfm_api_key.strip()
    if not api_key or not settings.fetch_genres:
        return ""

    artist_names = [artist.name for artist in getattr(track, "artists", []) if getattr(artist, "name", None)]
    primary_artist = artist_names[0] if artist_names else ""
    title = getattr(track, "name", "") or getattr(track, "title", "")
    album_name = track.album.name if getattr(track, "album", None) else ""

    genres = fetch_lastfm_top_tags("track.getTopTags", {"artist": primary_artist, "track": title}, api_key)
    if genres:
        return ", ".join(genres)

    # Some Last.fm pages expose tags at the single/album level rather than the track API endpoint.
    genres = fetch_lastfm_top_tags("album.getTopTags", {"artist": primary_artist, "album": album_name}, api_key)
    if genres:
        return ", ".join(genres)

    genres = fetch_lastfm_page_tags(primary_artist, title)
    if genres:
        return ", ".join(genres)

    genres = fetch_lastfm_page_tags(primary_artist, album_name)
    if genres:
        return ", ".join(genres)

    return ""


def build_kid3_row(track, credits_grouped, settings, file_path=""):
    row = {
        "FILE PATH": file_path,
        "Title": track.name,
        "Artist": "; ".join(artist.name for artist in track.artists),
        "Album Artist": get_album_artist(track),
        "Album": track.album.name if track.album else "",
        "Year": get_track_year(track),
        "Genre": get_track_genres(track, settings),
        "ISRC": getattr(track, "isrc", ""),
        "Comment": "",
        "Picture": "",
    }

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
            row[role] = ", ".join(names)

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
        track = session.track(track_id)
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

        files = [path for path in sorted(folder.iterdir()) if path.suffix.lower() in AUDIO_EXTS]
        if not files:
            sys.exit(f"No audio files found in: {folder}")

        ok(f"Found [bold]{len(files)}[/] audio files in [cyan]{folder.name}[/]")
        console.print()

        for file_path in files:
            isrc = get_file_isrc(file_path)
            if not isrc:
                warn(f"{file_path.name} - no ISRC tag, skipping")
                stats["failed"].append(f"{file_path.name} - no ISRC tag")
                continue
            try:
                results = session.request.request(
                    "GET",
                    "tracks",
                    params={"isrc": isrc, "countryCode": session.country_code, "limit": 1},
                ).json()
                items = results.get("items", [])
                if not items:
                    warn(f"{file_path.name} - no Tidal match for ISRC {isrc}")
                    stats["failed"].append(f"{file_path.name} - no Tidal match")
                    continue
                track_jobs.append(make_track_job(items[0]["id"], str(file_path.resolve())))
            except Exception as exc:
                warn(f"{file_path.name} - ISRC lookup failed: {exc}")
                stats["failed"].append(f"{file_path.name} - {exc}")
        return track_jobs

    if args.file_path and Path(args.file_path).exists() and Path(args.file_path).suffix.lower() in AUDIO_EXTS:
        file_path = Path(args.file_path)
        isrc = get_file_isrc(file_path)
        if not isrc:
            sys.exit(f"{file_path.name} has no ISRC tag.")
        try:
            results = session.request.request(
                "GET",
                "tracks",
                params={"isrc": isrc, "countryCode": session.country_code, "limit": 1},
            ).json()
            items = results.get("items", [])
            if not items:
                sys.exit(f"No Tidal match found for ISRC {isrc}.")
            track_jobs.append(make_track_job(items[0]["id"], str(file_path.resolve())))
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


def process_tracks(session, track_jobs, settings, stats):
    rows = []
    with Progress(
        SpinnerColumn(style=ACCENT),
        TextColumn("[bold white]{task.description}"),
        BarColumn(bar_width=settings.progress_bar_width, style="cyan", complete_style=ACCENT),
        TextColumn("[cyan]{task.completed}[/][dim]/{task.total}[/]"),
        TimeElapsedColumn(),
        console=console,
        transient=False,
    ) as progress:
        task = progress.add_task("Fetching credits", total=len(track_jobs))

        for job in track_jobs:
            track_id = job["track_id"]
            file_path = job["file_path"]
            track = job.get("track")
            if track:
                label = track.name[:34]
            else:
                track, label = fetch_track_label(session, track_id)
            progress.update(task, description=f"[white]{label:<36}[/]")

            if not track:
                stats["failed"].append(f"ID {track_id} - could not fetch")
                progress.advance(task)
                continue

            grouped = format_credits(get_credits(session, track_id))
            rows.append(build_kid3_row(track, grouped, settings, file_path=file_path))
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

    console.print()
    rows = process_tracks(session, track_jobs, settings, stats)

    write_json(rows, output_path)
    if settings.save_session_on_exit:
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
        return

    no_source = not has_source
    if args.help:
        print_header()
        print_usage()
        return

    interactive_mode = no_source
    session = None

    if interactive_mode:
        show_home = True
        while True:
            clear_source_args(args)
            source = prompt_for_source(settings, show_home=show_home)
            if not source:
                return
            args.source = source
            show_home = False

            if session is None:
                session = load_session()
                console.print()

            run_fetch_job(args, settings, session, interactive_mode=True)
    else:
        print_header()
        settings = apply_settings_actions(args, settings)
        session = load_session()
        console.print()
        run_fetch_job(args, settings, session, interactive_mode=False)


if __name__ == "__main__":
    main()
