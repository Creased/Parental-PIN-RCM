# PIN Recovery Payload
# Based on Lockpick_RCM with integrated PIN recovery

PWD_CMD := $(CURDIR)

.PHONY: all clean build clone patch

all: build

clean:
	@echo "[-] Cleaning up..."
	@rm -rf Lockpick_RCM PinRecovery.bin

clone:
	@echo "[-] Checking for Lockpick_RCM..."
	@if [ ! -d "Lockpick_RCM" ]; then \
		echo "[-] Cloning Lockpick_RCM..."; \
		git clone https://github.com/saneki/Lockpick_RCM.git; \
	fi

patch: clone
	@echo "[-] Applying patches..."
	@cp -f source/pin_recovery.h Lockpick_RCM/source/pin_recovery.h
	@cp -f source/pin_recovery.c Lockpick_RCM/source/pin_recovery.c
	@cd Lockpick_RCM && patch -p1 < ../patches/main.c.patch || true
	@cd Lockpick_RCM && patch -p1 < ../patches/keys.h.patch || true
	@cd Lockpick_RCM && patch -p1 < ../patches/keys.c.patch || true

build: patch
	@echo "[-] Building with Docker..."
	@docker run --rm -v "$(PWD_CMD)/Lockpick_RCM:/work" -w /work devkitpro/devkita64:latest make
	@echo "[-] Copying output..."
	@cp Lockpick_RCM/output/Lockpick_RCM.bin ./PinRecovery.bin
	@echo ""
	@echo "[+] Build Complete!"
	@echo "[+] Output: PinRecovery.bin"
