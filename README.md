# 🏴‍☠️ OpforJellyfin

![OpforJellyfin-logo](img/opforjellyfin.png)

CLI tool to automate download and organisation of [One Pace](https://onepace.net)
episodes for **Jellyfin**.

> ✨ Torrent downloads
> ✨ Placement after Jellyfin standards
> ✨ Matched to metadata shamelessly stolen from [SpykerNZ/one-pace-for-plex](https://github.com/SpykerNZ/one-pace-for-plex)

This is a **Python 3.11** port of the original Go CLI. It targets **Linux**
and exposes the same feature set:

- `opfor list` – browse available One Pace torrents (with `-r`, `-t`, `-q`, `--minquality`, `-s`, `-v`)
- `opfor download <key> [...]` – download one or more torrents (with `--forcekey`)
- `opfor setDir <path>` – set the target/library directory and pull metadata (with `-f`)
- `opfor sync` – update metadata from the metadata Git repo
- `opfor info` – show library status and per-season video/.nfo counts
- `opfor status` – show currently active downloads
- `opfor clear` – clear temporary files / active-download cache
- `opfor logs [-l N]` – tail `debug.log`

## 🔧 Installation

### Prerequisites

- Python 3.11 or newer
- `git` (used to clone the metadata repo)
- A torrent backend, **only required if you want to download** torrents
  (`opfor download`). Listing, setDir, sync, info, etc. all work without
  one. Two backends are supported:
  - **libtorrent** (`python3-libtorrent`) — in-process, no extra service
    required.
  - **qBittorrent Web API** — talks to a running qBittorrent instance over
    HTTP. Recommended when you cannot install `python3-libtorrent` (e.g.
    no `sudo` on the machine).

On Debian/Ubuntu:

```bash
sudo apt install python3-libtorrent git
```

On Arch:

```bash
sudo pacman -S libtorrent-rasterbar python-libtorrent git
```

### Download backend

`opfor download` uses **libtorrent** by default. If `python3-libtorrent`
is not available on your system, point `opfor` at a running qBittorrent
instance instead — no extra Python package is needed.

1. In qBittorrent, enable the Web UI (*Tools → Options → Web UI*) and
   note the URL (e.g. `http://localhost:8080`) plus the username and
   password.
2. Configure `opfor`:

   ```bash
   opfor config qbittorrent \
       --url http://localhost:8080 \
       --username admin --password secret \
       --enable
   ```

   Use `opfor config qbittorrent --show` to inspect the current settings.

If `--enable` is set, the qBittorrent backend is always used. Otherwise,
`opfor` falls back to qBittorrent automatically when libtorrent is not
importable but a `qbittorrent.url` is configured. Files are downloaded
into the same `opfor-tmp-<id>` temp directory used by the libtorrent
backend, so placement and cleanup are unchanged.

> 🔐 The config file (`~/.config/opforjellyfin/config.json`) is written
> with `0600` permissions because the qBittorrent password is stored
> there in plain text. If you'd rather keep the password out of the file
> entirely, leave it unset and export it instead:
>
> ```bash
> export OPFOR_QBT_PASSWORD=secret
> ```
>
> When set, this environment variable overrides the value loaded from
> the config file.

### Install the CLI

From the repository root:

```bash
pip install .
```

Or in editable mode for development:

```bash
pip install -e .[dev]
```

That installs an `opfor` command in your shell.

> ℹ️ `python3-libtorrent` is intentionally **not** in `pyproject.toml`'s
> `dependencies`: the binding is best installed via the system package
> manager. If you install via `pip` in a virtualenv, you may need to use
> `--system-site-packages` to make `libtorrent` visible to the venv.

## 🚀 Usage

```bash
# 1. Choose where your One Pace library lives
opfor setDir "/media/One Piece/One Pace"

# 2. Browse available episodes
opfor list
opfor list -t Wano
opfor list -r 15-20 --minquality 720p

# 3. Download one or several keys
opfor download 15 16 17

# 4. Inspect your library
opfor info -v
opfor status
```

Run `opfor --help` or `opfor <subcommand> --help` for details.

## 📦 Metadata

Metadata lives in a separate repo: [tissla/one-pace-jellyfin](https://github.com/tissla/one-pace-jellyfin).

The `sync` command keeps you up to date with new additions to the
metadata repo.

### Steps to make sure Jellyfin doesn't mess with the metadata

1. Create a library with no metadata-fetchers active just for One Pace. Disable all of them!
2. Make sure the show is **unlocked** for changes.
3. Run `opfor sync` again if Jellyfin messed up your `.nfo` files before this.
4. Rescan library with **unlocked** metadata and _no fetchers active_.

## 🧪 Tests

```bash
pip install -e .[dev]
pytest
```

The test-suite ports the original Go parser tests to validate parity.

## 🗂️ Layout

```
src/opfor/
├── cli.py              # click commands
├── config.py           # JSON config under ~/.config/opforjellyfin
├── parsers.py          # chapter/range/regex parsers
├── fsutils.py          # thread-safe filesystem helpers
├── types.py            # dataclasses
├── downloads.py        # active-download registry
├── scraper.py          # httpx + BeautifulSoup torrent listing
├── metadata.py         # git clone, indexing, cache, status
├── matcher.py          # video file → metadata folder placement
├── torrent_client.py   # libtorrent download orchestration + backend dispatch
├── qbittorrent_client.py # qBittorrent Web API download backend
├── ui.py               # rich-based UI
└── logger.py           # debug.log
tests/
└── test_parsers.py     # parser tests
```

## 🤝 Contributions

All pull requests are welcome. All criticisms are welcome.

## ❤️ Acknowledgements

- SpykerNZ for the metadata
- The One Pace team for their amazing work!

## ⚠️ Disclaimer

This tool is provided **as-is** with no guarantees or warranties. It downloads
torrents and manipulates files – review the source code and test cautiously.
The author is not responsible for any damage to your system, loss of data, or
violation of terms of service related to the use of this software.

This project is not affiliated with One Pace, Jellyfin, or any content
providers. Please respect local laws and copyright regulations.
