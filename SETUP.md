# First-Time Setup Guide

This guide walks you through everything needed to run BigEd CC for the first
time. No programming experience required. If you already have Python and Ollama
installed, see [README.md](README.md) instead.

**Estimated time:** 10--15 minutes.

---

## What You Will Install

BigEd CC needs two things to run, plus itself:

- **Python 3.11+** -- the programming language BigEd CC is written in. You will
  not need to write any code. Python just needs to be installed so the
  application can run.
- **Ollama** -- a program that runs AI models on your computer. BigEd CC sends
  work to these models through Ollama.
- **BigEd CC** -- the application itself. It includes a launcher window, a fleet
  of AI workers, and a dashboard to monitor them.

---

## Automated Setup (Recommended)

Setup scripts are included in the repository and handle Python verification,
Ollama installation, model downloads, and dependency installation for you.

**Windows:**

1. Right-click the Start button. Click **Terminal** (or **PowerShell**).
2. Type this command and press Enter:
   ```
   powershell -ExecutionPolicy Bypass -File scripts\setup.ps1
   ```
3. Follow the prompts on screen.

**Linux / macOS:**

1. Open a terminal. On Linux, press **Ctrl+Alt+T**. On macOS, open Spotlight
   (Cmd+Space), type "Terminal", and press Enter.
2. Type this command and press Enter:
   ```
   bash scripts/setup.sh
   ```
3. Follow the prompts on screen.

---

## Manual Setup (Step by Step)

Pick the section that matches your computer.

---

### Windows

#### Step 1 -- Install Python

1. Go to [python.org/downloads](https://www.python.org/downloads/).
2. Click the big yellow button that says **Download Python 3.12** (or any
   version 3.11 or newer).
3. Open the downloaded file to start the installer.
4. **Important:** At the bottom of the first installer screen, check the box
   that says **Add Python to PATH**. If you skip this, commands will not work
   later.
5. Click **Install Now**. Wait for it to finish.
6. To verify it worked:
   - Right-click the Start button. Click **Terminal** (or **Command Prompt**).
   - Type `python --version` and press Enter.
   - You should see something like `Python 3.12.x`. If you see an error, go
     back and reinstall with the PATH checkbox checked.

#### Step 2 -- Install Ollama

1. Go to [ollama.com/download](https://ollama.com/download).
2. Click **Download for Windows**.
3. Open the downloaded file and follow the installer.
4. To verify it worked:
   - Open Terminal (or Command Prompt).
   - Type `ollama --version` and press Enter.
   - You should see a version number.

#### Step 3 -- Download an AI Model

Ollama needs a model to run. BigEd CC uses one called `qwen3:8b` by default.

1. Open Terminal (or Command Prompt).
2. Type `ollama pull qwen3:8b` and press Enter.
3. Wait for the download to finish. It is about 5 GB, so it may take a few
   minutes depending on your internet speed.

If your computer has less than 16 GB of RAM, use a smaller model instead:

```
ollama pull qwen3:4b
```

#### Step 4 -- Download BigEd CC

**Option A -- Download the installer (easiest):**

1. Go to the [BigEd CC releases page](https://github.com/SwiftWing21/BigEd-CC/releases).
2. Download the latest `.exe` file.
3. Run it and follow the installer.

**Option B -- Download the source code:**

If you want to run from source, or if no installer is available yet:

1. Go to [github.com/SwiftWing21/BigEd-CC](https://github.com/SwiftWing21/BigEd-CC).
2. Click the green **Code** button, then click **Download ZIP**.
3. Extract the ZIP file to a folder you will remember (for example, your
   Desktop or Documents folder).
4. Open Terminal. Type the following commands one at a time, pressing Enter
   after each:
   ```
   cd path\to\BigEd-CC
   pip install -r BigEd/launcher/requirements.txt
   ```
   Replace `path\to\BigEd-CC` with the actual path to the folder you extracted.
   For example: `cd C:\Users\YourName\Desktop\BigEd-CC`
5. The `pip install` command downloads the libraries BigEd CC needs. Wait for
   it to finish.

#### Step 5 -- Launch BigEd CC

- **If you used the installer:** Double-click the BigEd CC shortcut or `.exe`
  file.
- **If you downloaded the source code:** Open Terminal, go to the folder, and
  type:
  ```
  python BigEd/launcher/launcher.py
  ```

A window will appear. Follow the on-screen walkthrough.

---

### Linux (Ubuntu, Linux Mint, Debian)

#### Step 1 -- Install Python

Python is usually already installed on Linux. Check first:

1. Open a terminal. Press **Ctrl+Alt+T**.
2. Type `python3 --version` and press Enter.
3. If you see `Python 3.11` or newer, skip to Step 2.
4. If Python is not installed or is too old, type these commands:
   ```
   sudo apt update
   sudo apt install python3 python3-pip python3-tk
   ```
   When asked for your password, type it and press Enter. The password will not
   appear on screen as you type -- that is normal.

#### Step 2 -- Install Ollama

1. In the terminal, type this command and press Enter:
   ```
   curl -fsSL https://ollama.com/install.sh | sh
   ```
2. To verify it worked, type `ollama --version` and press Enter.

#### Step 3 -- Download an AI Model

1. In the terminal, type:
   ```
   ollama pull qwen3:8b
   ```
2. Wait for the download to finish (about 5 GB).

If your computer has less than 16 GB of RAM, use `ollama pull qwen3:4b` instead.

#### Step 4 -- Download and Run BigEd CC

**Option A -- AppImage (easiest):**

1. Go to the [BigEd CC releases page](https://github.com/SwiftWing21/BigEd-CC/releases).
2. Download the `.AppImage` file.
3. In the terminal, make it runnable:
   ```
   chmod +x BigEdCC.AppImage
   ```
4. Double-click the file, or run it from the terminal:
   ```
   ./BigEdCC.AppImage
   ```

**Option B -- Run from source:**

1. In the terminal, type these commands one at a time:
   ```
   git clone https://github.com/SwiftWing21/BigEd-CC.git
   cd BigEd-CC
   pip3 install -r BigEd/launcher/requirements.txt
   python3 BigEd/launcher/launcher.py
   ```
2. If `git` is not installed, install it first:
   ```
   sudo apt install git
   ```

---

### Linux (SteamOS / Steam Deck / Arch)

Follow the same steps as Ubuntu above, with these differences:

- **Desktop Mode is required.** Hold the Power button on your Steam Deck and
  choose **Desktop Mode**.
- **Use `pacman` instead of `apt`** for installing packages:
  ```
  sudo pacman -S python python-pip tk git
  ```
- Ollama installs the same way:
  ```
  curl -fsSL https://ollama.com/install.sh | sh
  ```
- Everything else (downloading a model, cloning the repo, running the app) is
  the same as the Ubuntu instructions above.

---

### macOS

#### Step 1 -- Install Python

macOS does not include a recent version of Python. The easiest way to install
it is through Homebrew, a package manager for macOS.

1. Open Spotlight (press **Cmd+Space**), type **Terminal**, and press Enter.
2. Install Homebrew by typing this command and pressing Enter:
   ```
   /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
   ```
   Follow any prompts that appear. This may take a few minutes.
3. Install Python:
   ```
   brew install python@3.12 python-tk@3.12
   ```
4. Verify: type `python3 --version` and press Enter. You should see 3.12 or
   newer.

#### Step 2 -- Install Ollama

1. Go to [ollama.com/download](https://ollama.com/download) and click
   **Download for macOS**. Open the downloaded file and drag it to your
   Applications folder.
2. Or install from the terminal:
   ```
   brew install ollama
   ```
3. Verify: type `ollama --version` and press Enter.

#### Step 3 -- Download an AI Model

1. In the terminal, type:
   ```
   ollama pull qwen3:8b
   ```
2. Wait for the download to finish (about 5 GB).

If your Mac has 8 GB of RAM, use `ollama pull qwen3:4b` instead.

#### Step 4 -- Download and Run BigEd CC

**Option A -- DMG installer (easiest):**

1. Go to the [BigEd CC releases page](https://github.com/SwiftWing21/BigEd-CC/releases).
2. Download the `.dmg` file.
3. Open it and drag BigEd CC to your Applications folder.

**Option B -- Run from source:**

1. In the terminal, type these commands one at a time:
   ```
   git clone https://github.com/SwiftWing21/BigEd-CC.git
   cd BigEd-CC
   pip3 install -r BigEd/launcher/requirements.txt
   python3 BigEd/launcher/launcher.py
   ```

---

## Troubleshooting

If something goes wrong, find your problem below and try the fix.

### "python is not recognized" or "python: command not found"

Python was not added to your system PATH during installation.

- **Windows:** Uninstall Python, then reinstall it. This time, check the **Add
  Python to PATH** box on the first screen of the installer.
- **Linux:** Use `python3` instead of `python`. They are different commands on
  Linux.
- **macOS:** Use `python3` instead of `python`.

### "No module named tkinter"

Tkinter is the library that draws the launcher window. It is included with
Python on Windows and macOS, but Linux sometimes needs it installed separately.

- **Ubuntu / Mint / Debian:** `sudo apt install python3-tk`
- **Arch / SteamOS:** `sudo pacman -S tk`

### "No module named customtkinter" (or any other missing module)

You need to install the project dependencies. Open a terminal, go to the
BigEd CC folder, and run:

```
pip install -r BigEd/launcher/requirements.txt
```

On Linux or macOS, use `pip3` instead of `pip`.

### "Ollama connection refused" or "Could not connect to Ollama"

Ollama is not running. It needs to be running in the background for BigEd CC to
work.

- **Windows:** Look for the Ollama icon in the system tray (bottom-right corner
  of the screen, near the clock). If it is not there, open Ollama from the
  Start menu.
- **Linux:** Open a terminal and type `ollama serve`. Leave that terminal
  window open.
- **macOS:** Open Ollama from the Applications folder.

### "Model not found" or "model qwen3:8b not found"

The AI model has not been downloaded yet. Open a terminal and type:

```
ollama pull qwen3:8b
```

Wait for it to finish before trying again.

### The app opens but the fleet does not start

1. Make sure Ollama is running (see "Ollama connection refused" above).
2. Make sure you downloaded a model (see "Model not found" above).
3. Close BigEd CC and open it again.

### SteamOS: "Cannot install packages" or "Read-only filesystem"

You must be in **Desktop Mode** to install software. Hold the Power button on
your Steam Deck and choose **Desktop Mode**.

---

## What Happens on First Launch

When you open BigEd CC for the first time:

1. It checks that Ollama is installed and running.
2. It checks that you have downloaded an AI model.
3. A walkthrough guides you through initial setup in a few steps.
4. The fleet (the group of AI workers) boots automatically.
5. You will see agents come online in the status panel on the left side of the
   window.

After the first launch, BigEd CC will skip the walkthrough and go straight to
the main screen.

---

## Hardware Notes

BigEd CC runs on most computers, but performance depends on your hardware:

| Setup | What to expect |
|-------|---------------|
| 8 GB RAM, no GPU | Works with `qwen3:4b` or `qwen3:0.6b`. Slower responses. Fewer workers. |
| 16 GB RAM, no GPU | Works with `qwen3:8b`. Reasonable speed on CPU. |
| 32 GB RAM, dedicated GPU (6+ GB VRAM) | Full fleet, fast responses, marathon sessions. |

You do not need a GPU. The AI models can run on your CPU. A GPU just makes
things faster.

---

## Next Steps

- **README.md** -- Overview of features and architecture.
- **CONTRIBUTING.md** -- How to contribute code or report bugs.
- **CROSS_PLATFORM.md** -- Platform-specific details and compatibility notes.
