#include "pin_recovery.h"
#include <string.h>
#include <libs/fatfs/ff.h>
#include <gfx_utils.h>
#include <mem/heap.h>
#include <utils/sprintf.h>
#include "storage/nx_emmc.h"
#include "storage/nx_emmc_bis.h"
#include "storage/emummc.h"
#include "keys/keys.h"
#include "config.h"

extern hekate_config h_cfg;
extern emummc_cfg_t emu_cfg;
extern FATFS emmc_fs;

#define CHUNK_SIZE (32 * 1024)
#define OVERLAP 64
#define PIN_KEYWORD "pinCode"

// Strategy 1: JSON Format - Used in FW 20.x, 21.x
// Looks for "pinCode":"XXXX" pattern
static int try_extract_json(const char *buffer, size_t size, char *out_pin) {
    const char *keyword = "\"pinCode\"";
    size_t keyword_len = strlen(keyword);
    
    for (size_t idx = 0; idx <= size - keyword_len; idx++) {
        if (memcmp(buffer + idx, keyword, keyword_len) == 0) {
            size_t offset = idx + keyword_len;
            int digit_count = 0;
            int quote_count = 0;
            int done = 0;
            
            // Read a chunk for processing (max 60 chars lookahead)
            for (size_t i = 0; i < 60 && (offset + i) < size && !done; i++) {
                char b = buffer[offset + i];
                
                if (b == 0x22) { // '"' Quote
                    quote_count++;
                    if (quote_count >= 2) {
                        done = 1; // Closing quote -> Stop
                    }
                } else {
                    if (quote_count == 1) {
                        // Inside the value string
                        if (b >= '0' && b <= '9' && digit_count < 8) { // Is digit 0-9
                            out_pin[digit_count++] = b;
                        }
                    }
                }
            }
            
            if (done && digit_count > 0) {
                out_pin[digit_count] = '\0';
                return 1;
            }
        }
    }
    return 0;
}

// Strategy 2: Binary Signature Format
// This parser looks for the known binary structure used by the system:
// - PIN stored as 4-8 ASCII digits in the first 8 bytes
// - Fixed meta bytes appear at +10 (0x06) and +12 (0x02)
static int try_extract_binary(const char *buffer, size_t size, char *out_pin) {
    if (size < 16) return 0;

    // Scan the buffer for the metadata signature
    for (size_t i = 0; i <= size - 16; i++) {
        // Optimized: check markers first
        if (buffer[i + 10] == 0x06 && buffer[i + 12] == 0x02) {
            int digit_count = 0;
            int valid = 1;
            
            // Verify the PIN bytes (0..7) are strictly digits or null padding
            for (int k = 0; k < 8 && valid; k++) {
                char cb = buffer[i + k];
                if (cb >= '0' && cb <= '9') {
                    digit_count++;
                } else {
                    if (cb != 0x00) {
                        valid = 0;
                    }
                }
            }
            
            if (valid && digit_count >= 4 && digit_count <= 8) {
                for (int k = 0; k < digit_count; k++) {
                    out_pin[k] = buffer[i + k];
                }
                out_pin[digit_count] = '\0';
                return 1;
            }
        }
    }
    return 0;
}

static int extract_pin(const char *buffer, size_t size, char *out_pin) {
    // Try Binary Format first (more specific signature)
    if (try_extract_binary(buffer, size, out_pin)) {
        return 1;
    }
    
    // Fallback to JSON Format
    if (try_extract_json(buffer, size, out_pin)) {
        return 1;
    }
    
    return 0;
}

static int scan_file(const char *path, char *out_pin) {
    FIL fp;
    UINT br;
    int found = 0;
    
    if (f_open(&fp, path, FA_READ) != FR_OK)
        return 0;
    
    char *buf = (char*)malloc(CHUNK_SIZE + OVERLAP + 1);
    if (!buf) {
        f_close(&fp);
        return 0;
    }
    
    if (f_read(&fp, buf, CHUNK_SIZE, &br) != FR_OK) {
        goto cleanup;
    }
    
    buf[br] = '\0';
    if (extract_pin(buf, br, out_pin)) {
        found = 1;
        goto cleanup;
    }
    
    while (br == CHUNK_SIZE) {
        memcpy(buf, buf + CHUNK_SIZE - OVERLAP, OVERLAP);
        
        if (f_read(&fp, buf + OVERLAP, CHUNK_SIZE, &br) != FR_OK)
            break;
        
        if (br == 0) break;
        
        buf[OVERLAP + br] = '\0';
        if (extract_pin(buf, OVERLAP + br, out_pin)) {
            found = 1;
            break;
        }
    }

cleanup:
    free(buf);
    f_close(&fp);
    return found;
}

int recover_parental_pin(char *out_pin) {
    int result = 0;
    char path[64];
    
    gfx_printf("Setting up keys...\n");

    // Init keys
    h_cfg.emummc_force_disable = true;
    emu_cfg.enabled = false;
    derive_keys_silent();
    
    gfx_printf("Mounting SYSTEM...\n");
    
    if (!emummc_storage_set_mmc_partition(EMMC_GPP)) {
        gfx_printf("%kErr: EMMC_GPP\n", 0xFFFF0000);
        return 0;
    }
    
    LIST_INIT(gpt);
    nx_emmc_gpt_parse(&gpt, &emmc_storage);
    
    emmc_part_t *system_part = nx_emmc_part_find(&gpt, "SYSTEM");
    if (!system_part) {
        gfx_printf("%kErr: SYSTEM part\n", 0xFFFF0000);
        nx_emmc_gpt_free(&gpt);
        return 0;
    }
    
    nx_emmc_bis_init(system_part);
    
    if (f_mount(&emmc_fs, "bis:", 1)) {
        gfx_printf("%kErr: bis mount\n", 0xFFFF0000);
        nx_emmc_gpt_free(&gpt);
        return 0;
    }
    
    s_printf(path, "bis:/save/8000000000000100");
    
    FILINFO fno;
    if (f_stat(path, &fno) == FR_OK) {
        gfx_printf("%kFound pin save\n", 0xFF00FF00);
        result = scan_file(path, out_pin);
    }
    
    if (!result) {
        gfx_printf("%kPIN not found\n", 0xFFFF0000);
    }
    
    f_mount(NULL, "bis:", 1);
    nx_emmc_gpt_free(&gpt);
    return result;
}
