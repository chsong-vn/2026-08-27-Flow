/*
 Arduino UNO controller for general IO operations.
 Gives direct access to the GPIO ports of the arduino UNO and supports reading, writing, PWM and analog signals.
 Meant to be used to drive solenoid valves, read switches and other simple devices.

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
    2     | READ_ONLY   | INT[0-254]  | Number of available GPIOs.
    3     | READ_ONLY   | INT[0-254]  | Number of available Analog inputs.
    4     | READ_ONLY   | INT-INT     | Error register 'x-y' x = error type, y = error value. Is reset upon reading.
    5     | COMMAND     | NONE        | Save defaults.
    6     | COMMAND     | NONE        | Factory Reset.

 Array Variable list (repeats for each input/output):
   gpio_id = pin_number - 2 (start from pin 2 at id 10, ends with pin 12 at id 110)
   Variable number = 10+10*gpio_id+index
   index | acccess     | type        | description
   ------|-------------|-------------|-------------------------
    0    | READ/WRITE  | INT[0-2]    | Set pin mode: input (1), output (0) or pwm output (2).
    1    | READ_ONLY   | INT[0-1]    | Read gpio digital value.
    2    | READ/WRITE  | INT[0-1]    | Write gpio digital value (reads setvalue).
    3    | READ/WRITE  | INT[0-255]  | Write gpio PWM value (reads setvalue). Only for PWM pins.

 Array Variable list (repeats for each analog input):
   gpio_id = analog_pin_number + 11 (start from pin A0 at id 120, ends with pin A5 at id 170)
   Variable number = 10+10*gpio_id+index
   index | acccess     | type        | description
   ------|-------------|-------------|-------------------------
    0    | READ_ONLY   | INT[0-1023] | Read analog sensor value.

 Error codes:
   type  |  description | value
   ------|--------------|--------------
   0     | No error     | Undefined
   1     | Serial error | Variable number which gave the issue
   2     | Pin error    | See Gpio.h

 Hardware connections (Arduino UNO board):
   None

 Libraries:
   no external libraries.

 by Simone Pilon <s.pilon at uva.nl> - Noël Research Group - 2024
*/

#include <math.h>
#include <EEPROM.h>
#include "Device_id.h"
#include "GPIO.h"

// Error codes (type)
#define ERROR_NO_ERROR 0	 // No error.
#define ERROR_SERIAL 1      // error value is variable number from error.
#define ERROR_PIN 2      	 // See pin.h

// Other
#define MAX_GPIO 10
#define MAX_ANALOG 6
#define FIRST_GPIO_PIN 2	// Do not use pins 0 and 1 (serial). Also pin 13 is not used (LED).
#define FIRST_ANALOG_PIN 14
#define SERIAL_ARRAY_BEGIN 10  // First array command in serial
#define SERIAL_ARRAY_PERIOD 10 // Number of serial commands per sensor

// EEPROM
#define EEPROM_ADDRESS_CHECK 0	// 1 byte char
#define DEFAULT_CHECK 42
#define EEPROM_ADDRESS_ID 1	// 20 bytes char[]
#define DEFAULT_ID "GPIO_0"

// Global variables
uint8_t error_type = 0;
uint8_t error_value = 0;
Gpio_pin gpios[MAX_GPIO];
Analog_pin analogs[MAX_ANALOG];

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
		int gpio_id = 0;

		if (command == 'R')
		{
			// Read variable
			variable_number = Serial.parseInt();
			og_variable_number = variable_number;

			if (variable_number >= SERIAL_ARRAY_BEGIN)
			{
				// Read sensor variable
				parse_array_command(variable_number, gpio_id);
				if (gpio_id < MAX_GPIO)
				{
					// Read GPIO variable
					switch (variable_number)
					{
						case 0:
							// Pin mode
							Serial.println(gpios[gpio_id].get_mode());
							break;
						case 1:
							// Read gpio value
							Serial.println(gpios[gpio_id].read());
							break;
						case 2:
							// Read gpio setvalue
							Serial.println(gpios[gpio_id].setvalue_digital());
							break;
						case 3:
							// Read pwm setvalue
							Serial.println(gpios[gpio_id].setvalue_pwm());
							break;
						default:
							// Syntax error
							error_type = ERROR_SERIAL;
							error_value = og_variable_number;
							break;
					}
				}
				else if (gpio_id < MAX_GPIO + MAX_ANALOG)
				{
					// Read Analog variable
					gpio_id = gpio_id - MAX_GPIO;
					switch (variable_number)
					{
						case 0:
							// Analog read
							Serial.println(analogs[gpio_id].read());
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
					// Sensor index out of range
					error_type = ERROR_SERIAL;
					error_value = og_variable_number;
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
						// Number of GPIOs
						Serial.println(MAX_GPIO);
						break;
					case 3:
						// Number of Analog inputs
						Serial.println(MAX_ANALOG);
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
			if (Gpio_pin::error != GPIO_ERROR_OK)
			{
				error_type = ERROR_PIN;
				error_value = Gpio_pin::error;
				Gpio_pin::error = GPIO_ERROR_OK;
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
				parse_array_command(variable_number, gpio_id);
				if (gpio_id < MAX_GPIO)
				{
					// Write GPIO variable
					switch (variable_number)
					{
						case 0:
							// Pin mode
							variable_value_int = Serial.parseInt();
							gpios[gpio_id].set_mode(variable_value_int);
							break;
						case 2:
							// Write gpio setvalue
							variable_value_int = Serial.parseInt();
							gpios[gpio_id].write(variable_value_int);
							break;
						case 3:
							// Write pwm setvalue
							variable_value_int = Serial.parseInt();
							gpios[gpio_id].write_pwm(variable_value_int);
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
					// Sensor index out of range
					error_type = ERROR_SERIAL;
					error_value = og_variable_number;
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
			if (Gpio_pin::error != GPIO_ERROR_OK)
			{
				error_type = ERROR_PIN;
				error_value = Gpio_pin::error;
				Gpio_pin::error = GPIO_ERROR_OK;
			}
		}
	}
}

void setup()
{
	// Load stored values from EEPROM
	load_defaults();
	
	// Initialize sensors
	for (uint8_t i=0; i<MAX_GPIO; i++)
	{
		gpios[i].pin = FIRST_GPIO_PIN + i;
		gpios[i].set_mode(GPIO_MODE_INPUT);
	}
	for (uint8_t i=0; i<MAX_ANALOG; i++)
	{
		analogs[i].pin = FIRST_ANALOG_PIN + i;
		pinMode(analogs[i].pin, INPUT);
	}

	// Initialize Serial interface
	Serial.begin(9600);
}

void loop()
{
	while (true)
	{
		parse_serial();
	}
}
