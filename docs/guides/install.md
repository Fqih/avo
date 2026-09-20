# Install Avo

The fastest way to use Avo is the user-global installer. It installs a self-contained
standalone binary directly on your system, so the `avo` command is available immediately
**without requiring Python, `uv`, `pip`, or `sudo`/Administrator access**.

## Choose your platform

=== "Linux, macOS, or Git Bash"

    ```bash
    curl -fsSL https://avo.faqihhakim.tech/install.sh | bash
    ```

=== "Native Windows PowerShell"

    ```powershell
    irm https://avo.faqihhakim.tech/install.ps1 | iex
    ```

The standalone installer will:

1. Detect your operating system and CPU architecture.
2. Download the pre-built, self-contained standalone binary from GitHub Releases.
3. Place `avo` in your user binary directory (`~/.local/bin` on POSIX or `%LOCALAPPDATA%\avo\bin` on Windows).
4. Add the directory to your user `PATH` if not already present.

Restart your terminal if `avo` is not found immediately afterward.

## Verify the installation

```bash
avo --version
avo doctor
```

`avo doctor` reports the resolved configuration without making a provider HTTP
request. It is the cheapest way to confirm that the CLI is available.

## Configure a workspace

Installation and configuration are separate on purpose: one user can install
Avo once, then configure different workspaces independently.

```bash
cd path/to/your/project
avo setup
avo doctor
```

The setup wizard configures provider credentials, permission defaults, and the
workspace database path. Secrets are stored in the user credential store with
restricted file permissions; never commit that file.

If you use a Codex, Claude, or Gemini vendor-account OAuth credential, enable
that credential source explicitly:

```bash
avo setup --allow-subscription
avo doctor
```

## Preview or customize the installer

Inspect the plan without downloading anything or changing your machine:

```bash
bash install.sh --dry-run
```

PowerShell:

```powershell
.\install.ps1 -DryRun
```

Install via Python package (uv tool) instead of standalone binary:

```bash
bash install.sh --from-source
# Or customize package spec and python version:
bash install.sh --from-source --package 'avo[providers]' --python 3.12
```

The same options are available on Windows PowerShell via `-FromSource`.

## Contributor installation

Use an editable install when working on Avo itself:

```bash
git clone https://github.com/Fqih/avo.git
cd avo
python -m pip install -e ".[dev,docs,providers,sandbox]"
```

Build and preview the documentation locally:

```bash
mkdocs serve
```
