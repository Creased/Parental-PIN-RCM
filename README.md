# Nintendo Switch Parental Control PIN Recovery

Parental Control PIN recovery for Nintendo Switch with two methods:
- **RCM Payload**: Integrated into Lockpick_RCM for automatic recovery
- **TegraExplorer Script**: Alternative script-based approach

## What it does
It mounts the SYSTEM partition, searches the `8000000000000100` save file and recover the PIN from it. No manual file management or decryption steps needed.

## Method 1: RCM Payload

### Build
Just run `make`. It uses [Docker](https://www.docker.com/) to build against [devkita64](https://devkitpro.org/wiki/Getting_Started).

Output is `PinRecovery.bin`.

### Installation
Copy `PinRecovery.bin` to `/sdcard/bootloader/payloads/` on your Switch SD card.

### Usage
1. Boot into Hekate
2. Select "Payloads"
3. Select "PinRecovery.bin"
4. Select "Recover Parental PIN"
5. PIN shows up on screen after key derivation

**Alternative**: You can also inject `PinRecovery.bin` directly via RCM (e.g., using TegraRcmGUI).

## Method 2: TegraExplorer Script

### Installation
Copy `recover_pin.te` to `/sdcard/tegraexplorer/scripts/` on your Switch SD card.

### Usage
1. Boot into Hekate
2. Select "Payloads"
3. Select "TegraExplorer.bin"
4. Navigate to "Scripts" menu
5. Select "recover_pin.te"
6. Choose "Recover from SysMMC" or "Recover from EmuMMC"
7. PIN is displayed on screen

**How it works**: The script detects which PIN files exist in your save (handles all firmware versions), reads them in the correct order, and searches for PIN signatures in both JSON and binary formats.

---

Credits: shchmue, CTCaer, saneki for the base payload.

