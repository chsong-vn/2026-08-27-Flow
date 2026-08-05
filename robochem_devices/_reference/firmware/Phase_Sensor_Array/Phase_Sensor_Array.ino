/*
 Arduino UNO controller for Phase Sensor Array (OCB350 from TTElectronics)
 Up to 4 Phase sensors can be connected and individually controlled from one Arduino UNO board.
 Supports asynchronous sensor monitoring.

 Serial Communication:

   Sx=y
   Set variable x to value y. Variable number are integer, values are signed floating point.
   Rx
   Read variable x and print its value to serial.

 Variable list:
   number | acccess     | type        | description
   -------|-------------|-------------|--------------------------
    0     | RESERVED    |             | Serial.parseInt returns 0 on error.
    1     | READ/WRITE  | STRING [20] | Device identifier.
    2     | READ_ONLY   | INT[0-254]  | Number of available sensors.
    3     | READ_ONLY   | [INT[0-3]]  | Read All sensors.
    4     | READ_ONLY   | INT-INT     | Error register 'x-y' x = error type, y = error value. Is reset upon reading.
    5     | COMMAND     | NONE        | Save defaults.
    6     | COMMAND     | NONE        | Factory Reset.

 Array Variable list (repeats for each sensor):
   Variable number = 10+10*sensor_id+index
   index | acccess     | type        | description
   ------|-------------|-------------|-------------------------
    0    | READ_ONLY   | INT[0-3]    | Read sensor value (0:error (no sensor), 1:clear liquid, 2:opaque liquid, 3:gas). See note on calibration!
    1    | READ_ONLY   | INT[0-1023] | Read analog sensor value.
    2    | READ/WRITE  | INT[0-2]    | Generate message on sensor state change (0: no, 1: one-time, 2: continuously).
         |             |             | Message: 'x=y\n' x = sensor_id, y = current state.
    3    | COMMAND     | NONE        | Perform sensor calibration.

 Error codes:
   type  |  description | value
   ------|--------------|--------------
   0     | No error     | Undefined
   1     | Serial error | Variable number which gave the issue

 Hardware connections (Arduino UNO board):
   Phase Sensor 0
     repeat from pin 2 to pin 13:
       pin 2 -> OCB350 Calibrate
       pin 3 <- OCB350 OUT B
       pin 4 <- OCB350 OUT A
     repeat from pin 14 to 19:
       pin 14 <- OCB350 Analog Out

 OCB350 board pinout:
   Pin    | Function    | Color
   -------|-------------|--------
   1      | Vcc         | Red
   2      | Logic Out A | Orange
   3      | Logic Out B | Blue
   4      | Calibrate   | Green
   5      | Analog Out  | White
   6      | Ground      | Black

 Note on Calibration:
   The meaning of the output values depends on how the sensor is calibrated. If the sensor is calibrated differently,
	the output values will change meaning completely.
   This is the calibration procedure which is assumed here:
     Place the tubing in the device with no liquid. Run the calibration (takes a second or so).
     If liquid is present during the calibration, the meaning of the outputs is different.

 Libraries:
   no external libraries.

 by Simone Pilon <s.pilon at uva.nl> - Noël Research Group - 2024
*/

#include <math.h>
#include <EEPROM.h>
#include "Device_id.h"
#include "OCB350.h"

// Defines for pins
// The pins for successive units repeat for A, B and C from the next available.
// The analog pins are assigned to successive units.
#define PIN_C_0 2		  // Pin calibrate
#define PIN_B_0 3		  // Pin logical B
#define PIN_A_0 4		  // Pin logical A
#define PIN_An_0 14	  // Pin analog
#define DIGITAL_PINS 3 // Number of digital pins.

// Error codes (type)
#define ERROR_NO_ERROR 0	 // No error.
#define ERROR_SERIAL 1      // error value is variable number from error.

// Other
// todo check if using arduino mega, then have more sensors.
#define MAX_DEVICES 4
#define SERIAL_ARRAY_BEGIN 10  // First array command in serial
#define SERIAL_ARRAY_PERIOD 10 // Number of serial commands per sensor

// EEPROM
#define EEPROM_ADDRESS_CHECK 0	// 1 byte char
#define DEFAULT_CHECK 42
#define EEPROM_ADDRESS_ID 1	// 20 bytes char[]
#define DEFAULT_ID "PHASE_ARRAY_0"

// Global variables
OCB350 sensors[MAX_DEVICES];
uint8_t error_type = 0;
uint8_t error_value = 0;

/**
 * Load stored values from EEPROM to system
 */
void load_defaults()
{
	uint8_t charvalue = 0;
	EEPROM.get(EEPROM_ADDRESS_CHECK, charvalue);
	if (charvalue == DEFAULT_CHECK)
	{
		eeprom_get_id(EEPROM_ADDRESS_ID);
	}
	else
	{
		factory_reset();
	}
}

/**
 * Store values from system to EEPROM as defaults
 */
void store_defaults()
{
	eeprom_put_id(EEPROM_ADDRESS_ID);
}

/**
 * Reset stored values to original defaults
 */
void factory_reset()
{
	uint8_t charvalue = DEFAULT_CHECK;
	EEPROM.put(EEPROM_ADDRESS_CHECK, charvalue);
	init_id(DEFAULT_ID);
	eeprom_put_id(EEPROM_ADDRESS_ID);
}

/**
 * Convert variable number to sensor_id and sensor_command.
 *
 * @param variable_number Takes the variable number as input and stores the sensor variable number as output.
 * @param sensor_id Outputs the number of the sensor.
 */
void parse_array_command(int &variable_number, int &sensor_id)
{
	variable_number -= SERIAL_ARRAY_BEGIN;
	sensor_id = variable_number / SERIAL_ARRAY_PERIOD;
	variable_number = variable_number % SERIAL_ARRAY_PERIOD;
}

/**
 * Checks serial line for commands and executes them.
 */
void parse_serial()
{
	while (Serial.available() > 0)
	{
		char command = Serial.read();
		int og_variable_number = 0;
		int variable_number = 0;
		int sensor_id = 0;

		if (command == 'R')
		{
			// Read variable
			variable_number = Serial.parseInt();
			og_variable_number = variable_number;

			if (variable_number >= SERIAL_ARRAY_BEGIN)
			{
				// Read sensor variable
				parse_array_command(variable_number, sensor_id);
				switch (variable_number)
				{
					case 0:
						// Read sensor state
						Serial.println(static_cast<uint8_t>(sensors[sensor_id].read()));
						break;
					case 1:
						// Read analog sensor state
						Serial.println(sensors[sensor_id].analog_read());
						break;
					case 2:
						// Read sensor monitoring status
						Serial.println(static_cast<uint8_t>(sensors[sensor_id].monitor()));
						break;
					default:
						// Syntax error
						error_type = ERROR_SERIAL;
						error_value = og_variable_number;
						break;
				}
			}
			else
			{
				// Read device-wide variable
				switch (variable_number)
				{
					case 1:
						// Device identifier
						Serial.println(device_id);
						break;
					case 2:
						// Number of sensors
						Serial.println(MAX_DEVICES);
						break;
					case 3:
						// Read all sensors
						for (uint8_t i=0; i<MAX_DEVICES; i++)
						{
							Serial.print(static_cast<uint8_t>(sensors[i].read()));
						}
						Serial.println(';');
						break;
					case 4:
						// Error register
						Serial.print(error_type);
						Serial.print('-');
						Serial.println(error_value);
						error_type = ERROR_NO_ERROR;
						error_value = 0;
						break;
					default:
						// Syntax error
						error_type = ERROR_SERIAL;
						error_value = og_variable_number;
						break;
				}
			}
		}
		else if (command == 'S')
		{
			// Write variable
			variable_number = Serial.parseInt();
			og_variable_number = variable_number;
			Serial.read();
			int variable_value_int = 0;
			float variable_value_float = 0;

			if (variable_number >= SERIAL_ARRAY_BEGIN)
			{
				// Write sensor variable
				parse_array_command(variable_number, sensor_id);
				switch (variable_number)
				{
					case 2:
						// Write sensor monitoring status
						variable_value_int = Serial.parseInt();
						if (variable_value_int >= 0 && variable_value_int <= 2)
						{
							sensors[sensor_id].monitor(static_cast<Monitor>(variable_value_int));
						}
						else
						{
							error_type = ERROR_SERIAL;
							error_value = og_variable_number;
						}
						break;
					case 3:
						// Calibrate sensor
						sensors[sensor_id].calibrate();
						break;
					default:
						// Syntax error
						error_type = ERROR_SERIAL;
						error_value = og_variable_number;
						break;
				}
			}
			else
			{
				// Write device-wide variable
				switch (variable_number)
				{
					case 1:
						// Device identifier
						serial_read_id();
						break;
          case 5:
						// Save defaults
						store_defaults();
						store_defaults();
						break;
					case 6:
						// Factory reset
						factory_reset();
						break;
					default:
						// Syntax error
						error_type = ERROR_SERIAL;
						error_value = og_variable_number;
						break;
				}
			}
		}
	}
}

void setup()
{
	// Load stored values from EEPROM
	load_defaults();
	
	// Initialize sensors
	for (uint8_t i=0; i<MAX_DEVICES; i++)
	{
		uint8_t base = i*DIGITAL_PINS;
		sensors[i] = OCB350( PIN_A_0 + base,
				PIN_B_0 + base,
				PIN_C_0 + base,
				PIN_An_0 + i);
		sensors[i].init();
	}

	// Initialize Serial interface
	Serial.begin(9600);
}

void loop()
{
	while (true)
	{
		parse_serial();
		for (uint8_t i=0; i<MAX_DEVICES; i++)
		{
			if (sensors[i].has_changed())
			{
				Serial.print(i);
				Serial.print('=');
				Serial.println(sensors[i].last_value);
			}
		}
	}
}
