#ifndef RUNZEVALVE_h
#define RUNZEVALVE_h

/*
 * Control of a Runze fluidics electronics flow switch valve.
 *
 * Author: Simone Pilon <s.pilon at uva.nl> - Noël Research Group - 2023
 */

#include <Arduino.h>

/**
 * Each instance can interface with an independent switch valve.
 */
class RunzeValve {
	public:
		/**
		 * Constructor
		 *
		 * @param pin_VT pin number for the output pin connected to the 'Valve trigger' terminal.
		 * @param pin_IO1 pin number for the input pin connected to the positional output 1 of the valve.
		 * @param pin_IO2 pin number for the input pin connected to the positional output 2 of the valve.
		 */
		RunzeValve(uint8_t pin_VT, uint8_t pin_IO1, uint8_t pin_IO2);

		/**
		 * Initialize pins.
		 * This needs to be called in the setup function.
		 */
		void init();

		/**
		 * Set the position of the valve.
		 *
		 * @param position either 0 or 1.
		 */
		void set_position(uint8_t position);

		/**
		 * Get the actual position of the valve.
		 *
		 * @return valve position (0 or 1).
		 */
		uint8_t get_position();

		/**
		 * Get the desired position of the valve.
		 *
		 * @return valve setpoint position (0 or 1, or 2 for an error).
		 */
		uint8_t get_setpoint();

		/**
		 * Is the valve at the desired position?
		 *
		 * @return true if the valve has reached the requested position.
		 */
		bool ready();

	protected:
		uint8_t pin_VT;
		uint8_t pin_IO1;
		uint8_t pin_IO2;

		uint8_t setpoint;	/**< Last setting for pin_VT (HIGH or LOW).*/
};

#endif // RUNZEVALVE_h
