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
extern sdmmc_storage_t emmc_storage;
extern FATFS emmc_fs;

#define CHUNK_SIZE (32 * 1024)
#define OVERLAP 64
#define PIN_KEYWORD "pinCode"

// Strategy 1: JSON Format - Used in FW 20.x, 21.x
// Looks for "pinCode": "XXXX"
static int try_extract_json(const char *buffer, size_t size, char *out_pin) {
    const char *keyword = "\"pinCode\"";
    size_t keyword_len = strlen(keyword);
    
    // Simple substring search
    const char *pos = NULL;
    for (size_t i = 0; i <= size - keyword_len; i++) {
        if (memcmp(buffer + i, keyword, keyword_len) == 0) {
            pos = buffer + i + keyword_len;
            break;
        }
    }
    
    if (!pos) return 0;
    
    // Skip non-digits (finding the value)
    while (pos < buffer + size && (*pos < '0' || *pos > '9')) pos++;
    
    // Extract digits
    int i = 0;
    while (pos < buffer + size && *pos >= '0' && *pos <= '9' && i < 8) {
        out_pin[i++] = *pos++;
    }
    out_pin[i] = '\0';
    
    // Validation: 4-8 digits
    return (i >= 4 && i <= 8);
}

// Strategy 2: Binary Signature Format - Observed in FW 13.x
// The PIN is stored as a raw ASCII string in the 8 bytes immediately preceding
// the signature: 03 0C 06 07
static int try_extract_binary(const char *buffer, size_t size, char *out_pin) {
    const char sig[] = {0x03, 0x0C, 0x06, 0x07};
    size_t sig_len = sizeof(sig);
    
    // We need at least 8 bytes (data) + 4 bytes (sig)
    if (size < 8 + sig_len) return 0;

    for (size_t i = 8; i <= size - sig_len; i++) {
        if (memcmp(buffer + i, sig, sig_len) == 0) {
            // Found signature. PIN is in [i-8 .. i]
            const char *pin_buf = buffer + i - 8;
            
            // Extract printable digits from the buffer.
            // The buffer might be null-padded (e.g., "0000\0\0\0\0")
            int p_idx = 0;
            for (int k = 0; k < 8; k++) {
                char c = pin_buf[k];
                if (c >= '0' && c <= '9') {
                    out_pin[p_idx++] = c;
                } else if (c == 0x00) {
                    // Padding is expected and ignored
                } else {
                    // Unexpected garbage? If strict, we could fail here.
                    // For now, ignore non-digit/non-null bytes to be robust.
                }
            }
            out_pin[p_idx] = '\0';
            
            // Validation: 4-8 digits
            if (p_idx >= 4 && p_idx <= 8) {
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
