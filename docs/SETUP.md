# Setup guide

This guide installs the NSE Dividend Tracker on macOS, Linux, or Windows.

## 1. Prerequisites

- **Python 3.11, 3.12, or 3.13** — 3.12 is the recommended version.
  Download from <https://www.python.org/downloads/>.
- **Internet access** — needed for dividend data and the first dependency
  install.
- **About 500 MB of free disk space** — mostly the virtual environment.
- No database server, account, or API key is required.

Check your Python version:

```bash
python3 --version        # macOS / Linux
py -3 --version          # Windows
```

If you have several versions, use an explicit one:

```bash
python3.12 run.py
```

## 2. Quick start

### macOS / Linux

```bash
git clone https://github.com/gyaneshgithub/nse-dividend-tracker.git
cd nse-dividend-tracker
./start.sh
```

If `./start.sh` is not executable:

```bash
chmod +x start.sh
./start.sh
```

### Windows

```bat
git clone https://github.com/gyaneshgithub/nse-dividend-tracker.git
cd nse-dividend-tracker
start.bat
```

### Any platform

```bash
python run.py
```

The first run:

1. creates `.venv/` next to the source;
2. installs the pinned packages from `requirements.txt`;
3. opens ChromaDB and restores the JSON mirror if the database is empty;
4. starts the web server.

Open **<http://localhost:8901/Dividend%20tracker>**.

The first install can take several minutes. Later runs start immediately.

## 3. Manual setup (optional)

Use this if you prefer to manage the environment yourself, or if the automatic
bootstrap is blocked by a corporate policy.

### macOS / Linux

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python server.py
```

### Windows PowerShell

```powershell
py -3.12 -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python server.py
```

If PowerShell refuses to run the activation script:

```powershell
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
```

Or skip activation and call the interpreter directly:

```powershell
.venv\Scripts\python.exe server.py
```

### Windows CMD

```bat
py -3.12 -m venv .venv
.venv\Scripts\activate.bat
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python server.py
```

## 4. Verify the installation

```bash
python scripts/check_setup.py
```

It checks:

- Python version;
- Flask, ChromaDB, and python-dotenv;
- required project files;
- that the data and database directories are writable;
- that ChromaDB can create and read a throwaway database.

It does **not** open or modify your real portfolio.

Then run the end-to-end smoke test:

```bash
python scripts/smoke_test.py
```

This starts a temporary server on a free port with temporary storage, exercises
the main API routes, and shuts it down.

## 5. Start and stop

| Platform | Command |
| --- | --- |
| macOS / Linux | `./start.sh` |
| Windows | `start.bat` |
| Any | `python run.py` |
| Existing environment | `python server.py` |

Stop the server with `Ctrl+C`.

The default URL is <http://localhost:8901/Dividend%20tracker>. These aliases
also work: `/Dividend tracker`, `/Dividend Tracker`, `/dividend-tracker`,
`/dividend-tracker.html`, and `/tracker`.

## 6. Configuration

Copy the template and edit it:

```bash
cp .env.example .env        # macOS / Linux
copy .env.example .env      # Windows CMD
```

| Variable | Default | Notes |
| --- | --- | --- |
| `HOST` | `127.0.0.1` | Bind address. Keep loopback for personal use. |
| `PORT` | `8901` | Change if the port is busy. |
| `DIVIDEND_DB_DIR` | `./chroma_db` | ChromaDB directory. Relative paths resolve from the project root. |
| `DIVIDEND_DATA_DIR` | `./data` | JSON mirror directory. |
| `DIVIDEND_CACHE_TTL` | `21600` | Upstream cache lifetime, in seconds (6 hours). |
| `ANONYMIZED_TELEMETRY` | `False` | ChromaDB telemetry opt-out. Leave it off unless you opt in. |

`python run.py` also accepts `--host` and `--port`:

```bash
python run.py --host 127.0.0.1 --port 9000
```

> **Network warning:** setting `HOST=0.0.0.0` exposes the app to your local
> network. It has no login screen. Only do this on a trusted network, and never
> port-forward it to the internet.

## 7. ChromaDB setup

ChromaDB is **embedded** and runs inside the Python process. There is no
separate service to install or start.

On first use the app:

1. creates `chroma_db/` (or `DIVIDEND_DB_DIR`);
2. creates the `holdings`, `payouts`, and `meta` collections;
3. restores `data/portfolio.json` if the database is empty.

If ChromaDB is missing or cannot open its directory, the server exits with a
clear error instead of running without persistence. Fix the dependency or path
and rerun `python scripts/check_setup.py`.

See [CHROMADB.md](CHROMADB.md) for paths, backup, restore, reset, and moving to
another machine.

## 8. Where your data lives

```text
data/portfolio.json     JSON mirror, rewritten after every change
chroma_db/              embedded ChromaDB files
.env                    optional local configuration
```

All three are ignored by git. Keep your own backups.

## 9. Updating

```bash
git pull
python -m pip install -r requirements.txt
```

`python run.py` detects a changed `requirements.txt` and reinstalls
automatically. If you manage the environment yourself, run the `pip install`
command manually.

Your `data/` and `chroma_db/` directories are not touched by `git pull`.

## 10. Docker (optional)

```bash
docker compose up --build
```

Open <http://localhost:8901/Dividend%20tracker>. The compose file bind-mounts
`./data` and `./chroma_db`, so native and container runs share storage.

To use different host directories, edit `docker-compose.yml`:

```yaml
volumes:
  - /path/to/my-data:/app/data
  - /path/to/my-chroma:/app/chroma_db
```

Do not run the native app and the container against the same database at the
same time. Embedded ChromaDB expects one process.

## 11. Troubleshooting

### `python3: command not found`

Install Python 3.11+ from <https://www.python.org/downloads/>. On Windows, use
the `py` launcher instead of `python`.

### `No module named flask` / `No module named chromadb`

You are using the wrong interpreter. Either use the launcher:

```bash
python run.py
```

or activate the environment first:

```bash
source .venv/bin/activate        # macOS / Linux
.venv\Scripts\activate           # Windows
python -m pip install -r requirements.txt
```

### `ChromaDB storage unavailable`

- Confirm the package is installed: `python -m pip show chromadb`.
- Run `python scripts/check_setup.py`.
- Make sure `DIVIDEND_DB_DIR` is writable and is not a file.
- Do not point two running processes at the same directory.
- On Linux, if `onnxruntime` fails to load, install the OpenMP runtime:
  `sudo apt-get install -y libgomp1`.

### Port already in use

```bash
python run.py --port 9000
```

Find the process using the old port:

```bash
lsof -i :8901                    # macOS / Linux
netstat -ano | findstr :8901     # Windows
```

### `Address already in use` after closing the terminal

The old server is still running. Stop it with `Ctrl+C`, or kill the PID shown by
the command above.

### Dividend refresh fails

- The upstream source is <https://stockanalysis.com>; it may be blocked by a
  proxy, firewall, or regional network.
- The app still works offline with stored data.
- Some tickers legitimately return errors upstream (for example, a company with
  no dividend page). The row shows a red status dot; use **Refresh** later.
- Corporate TLS inspection can break Python's certificate check. Configure your
  organization's certificate bundle or use a network that does not intercept
  TLS.

### Windows: install fails with a long-path error

Enable long paths:

```powershell
Set-ItemProperty -Path 'HKLM:\SYSTEM\CurrentControlSet\Control\FileSystem' `
  -Name LongPathsEnabled -Value 1
```

Then reboot, or clone to a short path such as `C:\src\dividend-tracker`.

### Windows: OneDrive-synced folder causes database locks

Move the project and its data outside OneDrive (for example `C:\src`), or pause
OneDrive while the app runs. Embedded databases do not like file sync clients
holding locks.

### Reset the application

See [CHROMADB.md](CHROMADB.md#reset-the-database). Delete `chroma_db/` only when
the app is stopped. Keep `data/portfolio.json` if you want to restore from it.

### Uninstall

1. Stop the server.
2. Delete the project directory (including `.venv/`).
3. If you used custom `DIVIDEND_DB_DIR` or `DIVIDEND_DATA_DIR` paths, delete
   those directories separately.

Your data is never uploaded by this project.
