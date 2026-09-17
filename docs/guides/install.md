# Install Avo

The fastest way to use Avo is the user-global installer. It creates an
isolated `uv` tool environment, so the `avo` command is available to your user
without changing the system Python or requiring `sudo`/Administrator access.

## Choose your platform

=== "Linux, macOS, or Git Bash"

    ```bash
    curl -fsSL https://avo.faqihhakim.tech/install.sh | bash
    ```

=== "Native Windows PowerShell"

    ```powershell
    irm https://avo.faqihhakim.tech/install.ps1 | iex
    ```

The installer will:

1. Find or install `uv` for your user.
2. Ensure Python 3.13 is available through `uv`.
3. Install the `avo[all]` runtime bundle.
4. Add the `uv` tool directory to your user PATH.

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

If your global provider is Codex or Gemini subscription OAuth, enable that
credential source explicitly:

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

Install a smaller package or choose another supported Python version:

```bash
AVO_PACKAGE='avo[providers]' AVO_PYTHON_VERSION=3.12 bash install.sh
```

The same values can be passed as `--package` and `--python` to `install.sh`.

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
