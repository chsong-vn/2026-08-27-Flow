#ifndef OCB350_H
#define OCB350_H

/**
 * Interface for OCB350 phase sensor from TTElectronics (with calibration board).
 *
 * Author: Simone Pilon <s.pilon at uva.nl> - Noël Research Group - 2024
 */

#include <Arduino.h>

#define OCB_MIN_TIME 10  // Filter out transients by checking that changes are stable for this many mS. Note that adds latency to the feedback.

/**
 * Possible phases detected by sensor.
 */
enum Phase : uint8_t
{
	ERROR = 0,	// Most likely no sensor is connected.
	CLEARLIQUID = 1,
	OPAQUE_LIQUID = 2,
	GAS = 3
};

/**
 * Monitoring options for sensor.
 */
enum Monitor : uint8_t
{
	NO = 0,		// Do not generate messages for this sensor [default].
	ONCE = 1,	// Wait for state change, then stop monitoring.
	ALWAYS = 2	// Generate a message every time the sensor changes state.
};

/**
 * Interface for OCB350 phase sensor from TTElectronics (with calibration board).
 */
class OCB350
{
	public:
		/**
		 * Constructor.
		 *
		 * @param pin_A Logic output A of calibration board (Orange wire).
		 * @param pin_B Logic output B of calibration board (Blue wire).
		 * @param pin_Calibration Calibrate pin of calibration board (Green wire).
		 * @param pin_Analog Analog output of calibration board (White wire). Note: this must be an analog input pin.
		 */
		OCB350(uint8_t pin_A, uint8_t pin_B, uint8_t pin_Calibration, uint8_t pin_Analog); 

		/**
		 * Constructor.
		 */
		OCB350();

		/**
		 * Initialize pins.
		 * Call once in setup.
		 */
		void init();

		/**
		 * Read the digital output of the sensor.
		 *
		 * @return Phase corresponding to the values read.
		 */
		Phase read();

		/**
		 * Read the analog output of the sensor.
		 *
		 * @return A value between 0 and 1023 representing the analog output of the sensor.
		 */
		int analog_read();

		/**
		 * Calibrate the sensor. Read main file docs for more info (tube should have no liquid).
		 */
		void calibrate();

		/**
		 * Begin monitoring this sensor for changes.
		 * Changes will be picked up by calling has_changed().
		 *
		 * @param mode Select in which mode the sensor will be monitored.
		 */
		void monitor(Monitor mode);

		/**
		 * The monitoring state for this sensor.
		 *
		 * @return The current monitoring mode.
		 */
		Monitor monitor();

		/**
		 * Check if the value (digital) has changed since the last read and if this should be monitored.
		 * Depends on the value of monitor_change and affects it.
		 *
		 * @return Returns true if the current read() returned a different value than the last.
		 */
		bool has_changed();

		Phase last_value;     /**< Last digital value read. */
	
	protected:
		uint8_t pin_A;
		uint8_t pin_B;
		uint8_t pin_Cal;
		uint8_t pin_Analog;

		Monitor monitor_change;    /**< Select how the state of the sensor should be monitored. */
		Phase monitor_value;			/**< Value monitored for change. */
		unsigned long last_change;	/**< Millis() on last detected change. */

};

#endif //  OCB350_H
