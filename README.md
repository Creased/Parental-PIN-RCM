# Lockpick_RCM PIN Recovery

Parental Control PIN recovery for Nintendo Switch, integrated into Lockpick_RCM.

### Build
Just run `make`. It uses docker to build against devkita64.
Output is `PinRecovery.bin`.

### Usage
- Inject via RCM
- Select "Recover Parental PIN"
- PIN shows up on screen after key derivation

### What it does
It derives BIS keys to mount the SYSTEM partition and searches the `8000000000000100` save file for the PIN. No manual file management or decryption steps needed.

Credits: shchmue, CTCaer, saneki for the base payload.
