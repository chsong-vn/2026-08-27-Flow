#ifndef GPIO_H
#define GPIO_H

/**
 * Utilities for the Arduino UNO GPIO interface.
 *
 * Author: Simone Pilon <s.pilon at uva.nl> - Noël Research Group - 2024
 */

#include <Arduino.h>

#define GPIO_ERROR_OK 0		// No error
#define GPIO_ERROR_NO_PWM 1	// Digital pin has no PWM
#define GPIO_ERROR_DIGITAL_VALUE 2		// Value for digital pin out or range 
#define GPIO_ERROR_PWM_VALUE 3		// Value for pwm pin out or range 
#define GPIO_ERROR_WRITE_TO_INPUT 4	// Attempt to write to an input pin
#define GPIO_ERROR_READ_FROM_OUTPUT 5	// Attempt to read from an output pin
#define GPIO_ERROR_INVALID_MODE 6		// Invalid mode identifier used
#define GPIO_ERROR_WRONG_MODE 7		// Attempt to call a function on a pin which is not set up correctly.

#define GPIO_MODE_OUTPUT 0
#define GPIO_MODE_INPUT 1
#define GPIO_MODE_PWM 2

/**
 * Interface for GPIO pins.
 */
class Gpio_pin
{
	public:

		/**
		 * Constructor.
		 */
		Gpio_pin();

		/**
		 * Set the pin mode.
		 *
		 * @parameter mode Pin mode (input, output, pwm).
		 */
		void set_mode(uint8_t mode);

		/**
		 * Read the digital pin.
		 *
		 * @return The value at the pin.
		 */
		uint8_t read();

		/**
		 * Write to the digital pin.
		 *
		 * @parameter value The value to be written.
		 */
		void write(uint8_t value);

		/**
		 * Write to the digital pin a PWM value.
		 *
		 * @parameter value The value to be written [0-255].
		 */
		void write_pwm(uint8_t value);

		/**
		 * Get the setvalue for a digital output pin.
		 *
		 * @return The value set for this pin.
		 */
		uint8_t setvalue_digital();

		/**
		 * Get the setvalue for a pwm output pin.
		 *
		 * @return The value set for this pin.
		 */
		uint8_t setvalue_pwm();

		/**
		 * Get the mode for this pin.
		 *
		 * @return The pin mode.
		 */
		uint8_t get_mode();


		uint8_t pin;	/**< Pin number tied to this instance. */
		static uint8_t error;	/**< Error code of last operation. */

	protected:

		uint8_t output_value;	/**< Last output value set. */
		uint8_t mode;	/**< Pin mode. */
};

/**
 * Interface for Analog pins.
 */
class Analog_pin
{
	public:

		/**
		 * Constructor.
		 */
		Analog_pin();

		/**
		 * Read the digital pin.
		 *
		 * @return The value at the pin.
		 */
		int read();

		uint8_t pin;	/**< Pin number tied to this instance. */
};

#endif //  GPIO_H
