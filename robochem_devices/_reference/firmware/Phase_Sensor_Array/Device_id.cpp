/**
 * Definitions for reading and writing custom device ids.
 *
 * Author: Simone Pilon <s.pilon at uva.nl> - Noël Research Group - 2024
 */

#include "Device_id.h"
#include <Arduino.h>
#include <EEPROM.h>

char device_id[DEVICE_ID_SIZE]; /**< Device id: a unique identifier to distinguish this from other serial devices */

void init_id(char id[DEVICE_ID_SIZE])
{
	for (uint8_t i = 0; i < DEVICE_ID_SIZE; i++)
	{
		device_id[i] = id[i];
		if (device_id[i] == '\0')
		{
			break;
		}
	}
}

void eeprom_put_id(uint8_t address)
{
	for (uint8_t i = 0; i < DEVICE_ID_SIZE; i++)
	{
		EEPROM.put(address+i, device_id[i]);
		if (device_id[i] == '\0')
		{
			break;
		}
	}
}

void eeprom_get_id(uint8_t address)
{
	for (uint8_t i = 0; i < DEVICE_ID_SIZE; i++)
	{
		EEPROM.get(address+i, device_id[i]);
		if (device_id[i] == '\0')
		{
			break;
		}
	}
}

void serial_read_id()
{
	uint8_t i = 0;
	char byte_in;
	while (i < DEVICE_ID_SIZE)
	{
		uint8_t timeout = DEVICE_ID_CHAR_TIMEOUT; 
		while (Serial.available() == 0 && timeout != 0)
		{
			delay(1);
			timeout--;
		}
		byte_in = Serial.read();
		if (byte_in == '\n')
		{
			break;
		}
		device_id[i] = byte_in;
		i++;
	}
	device_id[i] = '\0';
}
