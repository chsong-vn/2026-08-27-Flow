#ifndef DEVICE_ID_H
#define DEVICE_ID_H

/**
 * Definitions for reading and writing custom device ids.
 *
 * Author: Simone Pilon <s.pilon at uva.nl> - Noël Research Group - 2024
 */

#include <Arduino.h>

#define DEVICE_ID_SIZE 20 // Max number of characters in device id string.
#define DEVICE_ID_CHAR_TIMEOUT 20 // Max timeout in mS per char.

extern char device_id[DEVICE_ID_SIZE]; /**< Device id: a unique identifier to distinguish this from other serial devices */

/**
 * Initialize device id.
 *
 * @param id The desired id.
 */
void init_id(char id[DEVICE_ID_SIZE]);

/**
 * Save the current id to EEPROM
 *
 * @param address EEPROM address to store (first byte).
 */
void eeprom_put_id(uint8_t address);

/**
 * Load the current id from EEPROM
 *
 * @param address EEPROM address of the id (first byte).
 */
void eeprom_get_id(uint8_t address);

/**
 * Read a C-style string from Serial and store it in device_id.
 */
void serial_read_id();

#endif // DEVICE_ID_H
