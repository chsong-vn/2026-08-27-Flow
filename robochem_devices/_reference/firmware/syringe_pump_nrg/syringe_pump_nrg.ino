/*
 Arduino UNO controller for syringe pump.

 Serial Communication:

   Sx=y
   Set variable x to value y. Variable number are integer, values are signed floating point.
   Rx
   Read variable x and print its value to serial.

 Variable list:
   number | acccess    | type        | description
   -------|------------|-------------|---------------------
    0     | RESERVED   |             | Serial.parseInt returns 0 on error.
    1     | READ/WRITE | STRING [20] | Device identifier.
    2     | READ/WRITE | INT[0-1]    | Send acknowledge once movement is complete (syringe movement only). Default is 1.
    3     | READ/WRITE | INT[0-1]    | Valve position setpoint. Sends acknowledge signal when done, if specified.
    4     | READ_ONLY  | INT[0-2]    | Valve position (2 = error).
    5     | READ/WRITE | FLOAT       | Syringe diameter [mm].
    6     | READ/WRITE | FLOAT       | Steps per mL (overridden by setting diameter) [1/mL].
    7     | READ/WRITE | FLOAT       | Syringe pump flow [mL/min].
    8     | READ/WRITE | INT[0-1]    | Enable syringe pump motors (1=enabled).
    9     | COMMAND    | FLOAT       | Pump this amount now [uL]. Sends acknowledge signal when done, if specified.
   10     | READ_ONLY  | FLOAT       | Amount left to pump since last pump command [uL].
   11     | READ/WRITE | FLOAT       | Amount in syringe (based on last zero command) [uL].
   12     | COMMAND    | INT[0-2]    | Zero syringe pump (0->empty, 1->full, 2->do not move).
   13     | READ_ONLY  | INT         | Error register. Resets upon reading.
   14     | COMMAND    | NONE        | Save defaults.
   15     | COMMAND    | NONE        | Factory Reset.
   16     | READ/WRITE | INT[0-1]    | Auxiliary valve position setpoint. Sends acknowledge signal when done, if specified.
   17     | READ_ONLY  | INT[0-2]    | Auxiliary valve position (2 = error).
   18     | READ/WRITE | INT[0-1]    | Enable encoder wheel. Default is 1.
   19     | READ/WRITE | INT[0-1]    | Send acknowledge once movement is complete (main valve only). Default is 1.
   20     | READ/WRITE | INT[0-1]    | Send acknowledge once movement is complete (auxiliary valve only). Default is 1.
   21     | COMMAND    | NONE        | Stop syringe movement in place. Stops both zeroing and pumping commands. Ack is returned if specified.
   22     | READ_ONLY  | FLOAT       | Maximum flowrate achieved without acceleration of the motor in mL/min. Note: ensure the syringe diameter is set properly before reading.
   23     | READ_ONLY  | FLOAT       | Acceleration employed to achieve flowrates higher than the threshold (22) in mL/min^2. Note: ensure the syringe diameter is set properly before reading.

 Error codes:
   type  |  description | value
   ------|--------------|--------------
   0     | No error     | Undefined
   1     | Serial error | Variable number which gave the issue
   2     | Valve error  | 
   3     | Motor error  | See AsyncStepper.h for specific errors.

 Hardware connections (Arduino UNO board):
   Switchable valve (via custom board)
     pin 14 -> valve trigger
     pin 15 <- valve IO pin 1 (position)
     pin 16 <- valve IO pin 2 (position)

   Auxiliary switchable valve (via custom board)
     pin 17 -> valve trigger
     pin 18 <- valve IO pin 1 (position)
     pin 19 <- valve IO pin 2 (position)

   Motor driver
     pin 5 -> Stepper driver ENABLE (active LOW)
     pin 6 -> Stepper driver DIRECTION
     pin 7 -> Stepper driver PULSE (active LOW)
     pin 2 <- Encoder wheel signal

   Endstops
     pin 8 <- Motor endstop (NO) negative direction, active LOW
     pin 9 <- Motor endstop (NO) positive direction, active LOW

 Libraries:
   no external libraries.

 Math:
   A: NEMA 17 stepper: 200 steps/revolution
   B: Motor driver: 8 microsteps
   C: Belt transmission ratio: 32/20 (1.6)
   D: Driver screw pitch: 2 mm/revolution (TR8x2)

   A*B*C/D = 1280 steps/mm
   steps/(pi*r**2)*1000 = steps/mL = 407436.65/r**2

   1.0mL syringe:
     76800 steps/mL
     4.6065886 mm diameter

 by Simone Pilon <s.pilon at uva.nl> - Noël Research Group - 2023
 */

#include <math.h>
#include <EEPROM.h>
#include "Device_id.h"
#include "AsyncStepper.h"
#include "RunzeValve.h"

// Defines for pins
							  // +- Main Valve
#define PIN_VT 14		  // valve trigger
#define PIN_IO1 15	  // valve IO pin 1 (position)
#define PIN_IO2 16	  // valve IO pin 2 (position)
							  // +- Aux Valve
#define PIN_VT_AUX 17  // valve trigger
#define PIN_IO1_AUX 18 // valve IO pin 1 (position)
#define PIN_IO2_AUX 19 // valve IO pin 2 (position)
							  // +- Motor
#define PIN_EN 5		  // motor enable
#define PIN_DIR 6		  // motor direction
#define PIN_STEP 7	  // motor step
#define PIN_ENCODER 2  // motor encoder wheel signal (INT0). Note: cannot be changed easily.
								  // +- Endstops
#define PIN_ENDSTOP_POS 9 // motor endstop (NO) positive direction, active LOW
#define PIN_ENDSTOP_NEG 8 // motor endstop (NO) negative direction, active LOW

// Acknowledgement values
#define ACK_PUMPING 'k'
#define BAD_ACK_PUMPING 'n'
#define ACK_VALVE 'v'
#define ACK_AUX_VALVE 'a'

// Error codes (type)
#define ERROR_NO_ERROR 0	 // No error.
#define ERROR_SERIAL 1      // error value is variable number from error
#define ERROR_VALVE 2       // 
#define ERROR_PUMP_MOTOR 3  // error value is error code from motor

// Other
#define RADIUS_TO_STEPS 407436.65 // Conversion factor from 1/r**2 to steps_per_ml

// EEPROM
#define EEPROM_ADDRESS_DIAMETER 0	// 4 bytes float
#define DEFAULT_DIAMETER 4.60659 // [mm]
#define EEPROM_ADDRESS_STEPSPML 4	// 4 bytes float
#define DEFAULT_STEPSPML 76800 // [1/mL]
#define EEPROM_ADDRESS_FLOWRATE 8	// 4 bytes float
#define DEFAULT_FLOWRATE 5.0 // [mL/min]
#define EEPROM_ADDRESS_CHECK 12	// 1 byte char
#define DEFAULT_CHECK 42
#define EEPROM_ADDRESS_ID 13	// 20 bytes char[]
#define DEFAULT_ID "SYRINGE_PUMP_0"

// Global variables
bool pump_ack_enable = true;
bool valve_ack_enable = true;
bool aux_valve_ack_enable = true;
float syringe_diameter = DEFAULT_DIAMETER; // [mm]
float steps_per_ml = DEFAULT_STEPSPML;	   // [1/mL]
float flowrate = DEFAULT_FLOWRATE;		   // [mL/min]
AsyncStepperDriver motor = AsyncStepperDriver(PIN_EN, PIN_DIR, PIN_STEP, PIN_ENDSTOP_POS, PIN_ENDSTOP_NEG);
RunzeValve valve = RunzeValve(PIN_VT, PIN_IO1, PIN_IO2);
RunzeValve valve_aux = RunzeValve(PIN_VT_AUX, PIN_IO1_AUX, PIN_IO2_AUX);
bool moving_motor = false;
bool moving_valve = false;
bool moving_valve_aux = false;
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
		EEPROM.get(EEPROM_ADDRESS_DIAMETER, syringe_diameter);
		EEPROM.get(EEPROM_ADDRESS_STEPSPML, steps_per_ml);
		EEPROM.get(EEPROM_ADDRESS_FLOWRATE, flowrate);
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
	EEPROM.put(EEPROM_ADDRESS_DIAMETER, syringe_diameter);
	EEPROM.put(EEPROM_ADDRESS_STEPSPML, steps_per_ml);
	EEPROM.put(EEPROM_ADDRESS_FLOWRATE, flowrate);
	eeprom_put_id(EEPROM_ADDRESS_ID);
}

/**
 * Reset stored values to original defaults
 */
void factory_reset()
{
	syringe_diameter = DEFAULT_DIAMETER;
	EEPROM.put(EEPROM_ADDRESS_DIAMETER, syringe_diameter);
	steps_per_ml = DEFAULT_STEPSPML;
	EEPROM.put(EEPROM_ADDRESS_STEPSPML, steps_per_ml);
	flowrate = DEFAULT_FLOWRATE;
	EEPROM.put(EEPROM_ADDRESS_FLOWRATE, flowrate);
	uint8_t charvalue = DEFAULT_CHECK;
	EEPROM.put(EEPROM_ADDRESS_CHECK, charvalue);
	init_id(DEFAULT_ID);
	eeprom_put_id(EEPROM_ADDRESS_ID);
}

/**
 * Set the delay for the pump motor based on desired flowrate and steps_per_ml
 *
 * @param flowrate Flowrate in mL/min
 * @param steps_per_ml Steps per mL 
 */
bool set_motor_speed(float flowrate, float steps_per_ml)
{
	motor.last_error = 0;
	motor.set_step_delay(long(60000000.0 / (flowrate * steps_per_ml)));
	if (motor.last_error != 0)
	{
		error_type = ERROR_PUMP_MOTOR;
		error_value = motor.last_error;
		motor.last_error = 0;
		return false;
	}
	return true;
}

/**
 * Checks serial line for commands and executes them.
 */
void parse_serial()
{
	while (Serial.available() > 0)
	{
		char command = Serial.read();
		int variable_number = 0;

		if (command == 'R')
		{
			// Read variable
			variable_number = Serial.parseInt();
			switch (variable_number)
			{
				case 1:
					// Device identifier
					Serial.println(device_id);
					break;
				case 2:
					// Acknowledge pump
					Serial.println(pump_ack_enable ? 1 : 0);
					break;
				case 3:
					// Valve setpoint
					Serial.println(valve.get_setpoint());
					break;
				case 4:
					// Valve position
					Serial.println(valve.get_position());
					break;
				case 5:
					// Syringe diameter [mm]
					Serial.println(syringe_diameter);
					break;
				case 6:
					// Steps per mL [1/mL]
					Serial.println(steps_per_ml);
					break;
				case 7:
					// Syringe pump flow [mL/min]
					Serial.println(flowrate, 4);
					break;
				case 8:
					// Motor enable
					Serial.println(motor.enable() ? 1 : 0);
					break;
				case 10:
					// Amount left to pump [uL]
					Serial.println(float(motor.steps_left()) * 1000 / steps_per_ml);
					break;
				case 11:
					// Amount in syringe based on zero [uL]
					Serial.println(float(motor.steps_abs()) * -1000 / steps_per_ml);
					break;
				case 13:
					// Error register
					Serial.print(error_type);
					Serial.print("-");
					Serial.println(error_value);
					error_type = 0;
					error_value = 0;
					break;
				case 16:
					// Auxiliary Valve setpoint
					Serial.println(valve_aux.get_setpoint());
					break;
				case 17:
					// Auxiliary Valve position
					Serial.println(valve_aux.get_position());
					break;
#ifdef WITH_ENCODER
				case 18:
					// Use encoder wheel
					Serial.println(motor.ignore_encoder ? 0 : 1);
					break;
#endif  // WITH_ENCODER
				case 19:
					// Acknowledge main valve 
					Serial.println(valve_ack_enable ? 1 : 0);
					break;
				case 20:
					// Acknowledge aux valve 
					Serial.println(aux_valve_ack_enable ? 1 : 0);
					break;
				case 22:
					// Maximum flowrate without acceleration
					Serial.println(60.0e6 / ( MIN_DELAY_NO_ACC * steps_per_ml ));
					break;
				case 23:
					// Acceleration
					Serial.println(pow( 60.0e6 / (MIN_DELAY_NO_ACC * INITIAL_ACC_STEPS) , 2) / steps_per_ml );
					break;
        case 24:
          // steps since int
          Serial.println(motor.steps_since_int);
          break;
				default:
					// Syntax error
					error_type = ERROR_SERIAL;
					error_value = variable_number;
					break;
			}
			if (motor.last_error != MOTOR_ERROR_NO_ERROR)
			{
				error_type = ERROR_PUMP_MOTOR;
				error_value = motor.last_error;
				motor.last_error = MOTOR_ERROR_NO_ERROR;
			}
		}
		else if (command == 'S')
		{
			// Write variable
			variable_number = Serial.parseInt();
			Serial.read();
			int variable_value_int = 0;
			float variable_value_float = 0;
			float variable_value_float_2 = 0;
			bool success = true;
			switch (variable_number)
			{
				case 1:
					// Device identifier
					serial_read_id();
					break;
				case 2:
					// Acknowledge pump
					variable_value_int = Serial.parseInt();
					pump_ack_enable = variable_value_int == 1 ? true : false;
					break;
				case 3:
					// Valve setpoint
					variable_value_int = Serial.parseInt();
					if (variable_value_int == 0 || variable_value_int == 1)
					{
						valve.set_position(variable_value_int);
						moving_valve = true;
					}
					else
					{
						error_type = ERROR_SERIAL;
						error_value = variable_number;
					}
					break;
				case 5:
					// Syringe diameter [mm]
					variable_value_float = Serial.parseFloat();
					if (variable_value_float > 0)
					{
						variable_value_float_2 = RADIUS_TO_STEPS / pow(variable_value_float / 2, 2.0);
						if (set_motor_speed(flowrate, variable_value_float_2))
						{
							steps_per_ml = variable_value_float_2;
							syringe_diameter = variable_value_float;
						}
					}
					else
					{
						error_type = ERROR_SERIAL;
						error_value = variable_number;
					}
					break;
				case 6:
					// Steps per mL [1/mL]
					variable_value_float = Serial.parseFloat();
					if (variable_value_float > 0)
					{
						if (set_motor_speed(flowrate, variable_value_float))
						{
							steps_per_ml = variable_value_float;
							syringe_diameter = pow(RADIUS_TO_STEPS / steps_per_ml, 0.5) * 2;
						}
					}
					else
					{
						error_type = ERROR_SERIAL;
						error_value = variable_number;
					}
					break;
				case 7:
					// Syringe pump flow [mL/min]
					variable_value_float = Serial.parseFloat();
					if (variable_value_float > 0)
					{
						if (set_motor_speed(variable_value_float, steps_per_ml))
						{
							flowrate = variable_value_float;
						}
					}
					else
					{
						error_type = ERROR_SERIAL;
						error_value = variable_number;
					}
					break;
				case 8:
					// Motor enable
					variable_value_int = Serial.parseInt();
					motor.enable(variable_value_int == 1 ? true : false);
					digitalWrite(LED_BUILTIN, variable_value_int == 1 ? HIGH : LOW);
					break;
				case 9:
					// Pump this much [uL]
					if (!motor.enable()) {
						error_type = ERROR_PUMP_MOTOR;
						error_value = MOTOR_ERROR_DISABLED;
						if (pump_ack_enable) {
							Serial.println(BAD_ACK_PUMPING);
						}
						break;
					}
					variable_value_float = Serial.parseFloat();
					motor.move(long(variable_value_float * steps_per_ml / 1000));
					moving_motor = true;
					break;
				case 11:
					// Amount in syringe based on zero [uL]
					variable_value_float = Serial.parseFloat();
					motor.set_steps_abs(long(variable_value_float * -steps_per_ml / 1000));
					break;
				case 12:
					// Zero syringe pump
					variable_value_int = Serial.parseInt();
					if (!motor.enable() && variable_value_int != 2) {
						error_type = ERROR_PUMP_MOTOR;
						error_value = MOTOR_ERROR_DISABLED;
						if (pump_ack_enable) {
							Serial.println(BAD_ACK_PUMPING);
						}
						break;
					}
					if (variable_value_int == 0)
					{
						motor.zero(ZeroDirection::POSITIVE);
						moving_motor = true;
					}
					else if (variable_value_int == 1)
					{
						motor.zero(ZeroDirection::NEGATIVE);
						moving_motor = true;
					}
					else if (variable_value_int == 2)
					{
						motor.zero(ZeroDirection::NONE);
						if (pump_ack_enable) {
							Serial.println(ACK_PUMPING);
						}
					}
					else
					{
						error_type = ERROR_SERIAL;
						error_value = variable_number;
						if (pump_ack_enable) {
							Serial.println(BAD_ACK_PUMPING);
						}
					}
					break;
				case 14:
					// Save defaults to eeprom
					store_defaults();
					break;
				case 15:
					// Factory reset
					factory_reset();
					break;
				case 16:
					// Auxiliary Valve setpoint
					variable_value_int = Serial.parseInt();
					if (variable_value_int == 0 || variable_value_int == 1)
					{
						valve_aux.set_position(variable_value_int);
						moving_valve_aux = true;
					}
					else
					{
						error_type = ERROR_SERIAL;
						error_value = variable_number;
					}
					break;
#ifdef WITH_ENCODER
				case 18:
					// Use encoder wheel
					variable_value_int = Serial.parseInt();
					motor.ignore_encoder = variable_value_int == 1 ? false : true;
					break;
#endif  // WITH_ENCODER
				case 19:
					// Acknowledge valve
					variable_value_int = Serial.parseInt();
					valve_ack_enable = variable_value_int == 1 ? true : false;
					break;
				case 20:
					// Acknowledge aux valve
					variable_value_int = Serial.parseInt();
					aux_valve_ack_enable = variable_value_int == 1 ? true : false;
					break;
				case 21:
					// Stop syringe movement
					if (!moving_motor)
					{
						if (pump_ack_enable)
						{
							Serial.println(ACK_PUMPING);
						}
						break;
					}
					motor.stop();
					break;
        case 24:
          // steps since int
          motor.steps_since_int = Serial.parseInt();
          break;
				default:
					// Syntax error
					error_type = ERROR_SERIAL;
					error_value = variable_number;
					break;
			}
			if (motor.last_error != MOTOR_ERROR_NO_ERROR)
			{
				error_type = ERROR_PUMP_MOTOR;
				error_value = motor.last_error;
				motor.last_error = MOTOR_ERROR_NO_ERROR;
			}
		}
	}
}

void setup()
{
	// Load stored values from EEPROM
	load_defaults();

	// Initialize components
	valve.init();
	valve_aux.init();
	motor.init();
	motor.set_step_delay(long(60000000.0 / (flowrate * steps_per_ml)));

	pinMode(LED_BUILTIN, OUTPUT);
	digitalWrite(LED_BUILTIN, LOW);

	// Initialize Serial interface
	Serial.begin(9600);
}

void loop()
{
	while (true)
	{
		parse_serial();
		if (moving_valve && valve.ready())
		{
			moving_valve = false;
			if (valve_ack_enable)
			{
				Serial.println(ACK_VALVE);
			}
		}
		if (moving_valve_aux && valve_aux.ready())
		{
			moving_valve_aux = false;
			if (aux_valve_ack_enable)
			{
				Serial.println(ACK_AUX_VALVE);
			}
		}
		if (moving_motor && motor.ready())
		{
			moving_motor = false;
			if (motor.last_error != MOTOR_ERROR_NO_ERROR) {
				error_type = ERROR_PUMP_MOTOR;
				error_value = motor.last_error;
				motor.last_error = MOTOR_ERROR_NO_ERROR;
				if (pump_ack_enable)
				{
					Serial.println(BAD_ACK_PUMPING);
				}
			}
			else {
				if (pump_ack_enable)
				{
					Serial.println(ACK_PUMPING);
				}
			}
		}
	}
}
