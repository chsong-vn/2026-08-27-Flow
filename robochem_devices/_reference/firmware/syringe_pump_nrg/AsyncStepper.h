#ifndef ASYNCSTEPPER_H
#define ASYNCSTEPPER_H

/*
 * Asynchronous driving of a stepper motor.
 * Timer1 is used to generate step signals for the stepper.
 * Note: Timer1 is also used by PWM outputs 9 and 10, using the analogWrite()
 * function on one of these pins will interfere with the stepper signal, and vice-versa.
 * Just including this file will interfere with the PWM function on pins 9 and 10.
 * Additionally, if the encoder wheel is used to check for stall of the motor, this must be connected to pin 2 (INT0).
 * Note: Only 1 motor can be driven with this implementation.
 *
 * Author: Simone Pilon <s.pilon at uva.nl> - Noël Research Group - 2023
 */

#include <Arduino.h>

#define WITH_ENCODER /**< Comment out to skip all the code related to the encoder wheel. */

#define PULSE_WIDTH_US 10    /**< Duration of the step pulse in uS.*/
#define MIN_DELAY_US 30      /**< Minimum delay for steps.*/
#define MAX_DELAY_US 4194240 /**< Maximum delay for steps.*/
#define MAX_STEPS_SINCE_INT 75 /**< Maximum number of steps we can register without signal on INT0 (motor blocekd).*/
#define MAX_STEPS_SINCE_ENDSTOP 6 /**< Number of consecutive steps with endstop activated before movement is stopped.*/

// The following parameters set the acceleration characteristics.
// For the time being, this is not modifiable by the user.
// The resulting acceleration can be derived (in steps/S^2) as:
// 	( 1e6 / (MIN_DELAY_NO_ACC * INITIAL_ACC_STEPS) )^2
// In mL/S^2:
// 	( 60e6 / (MIN_DELAY_NO_ACC * INITIAL_ACC_STEPS) )^2 / stepsperml
// Acceleration kicks in above this speed (in steps/S):
// 	1e6 / MIN_DELAY_NO_ACC
// In mL/S:
// 	60e6 / ( MIN_DELAY_NO_ACC * stepsperml )
#define MIN_DELAY_NO_ACC 300	/**< Minumum delay (uS) set without acceleration.*/
#define ACC_STEPS_INCREMENT 1.0	/**< Number of steps between acceleration increments.*/
#define INITIAL_ACC_STEPS 100		/**< Initial value for acceleration step counter max.*/

#define MOTOR_ERROR_NO_ERROR 0      /**< No errors occurred.*/
#define MOTOR_ERROR_ENDSTOP_POS 1   /**< Unexpected activation of positive direction endstop.*/
#define MOTOR_ERROR_ENDSTOP_NEG 2   /**< Unexpected activation of negative direction endstop.*/
#define MOTOR_ERROR_DELAY_RANGE 3   /**< Tried to set a step delay which is too low or too high.*/
#define MOTOR_ERROR_DISABLED 4      /**< Attempt to move motor while driver is disabled.*/
#define MOTOR_ERROR_BLOCKED 5       /**< The motor is not moving despite the signals being sent to the driver.*/

/**
 * Directions for zeroing the motor.
 */
enum ZeroDirection : uint8_t
{
	POSITIVE, /**< Move towards the positive direction until enstop triggers. */
	NEGATIVE, /**< Move towards the negative direction until enstop triggers. */
	NONE      /**< Only zero the software counter. */
};

/**
 * Stepper motor driver with non-blocking move().
 */
class AsyncStepperDriver
{
	public:
		/**
		 * Constructor.
		 *
		 * @param pin_step Step signal pin
		 * @param pin_dir Direction signal pin
		 * @param pin_enable Enable signal pin
		 * @param pin_endstop_pos Positive endstop pin
		 * @param pin_endstop_neg Negative endstop pin
		 * @param invert Invert direction polarity
		 */
		AsyncStepperDriver(uint8_t pin_enable, uint8_t pin_dir, uint8_t pin_step, uint8_t pin_endstop_pos, uint8_t pin_endstop_neg, bool invert = false);

		/**
		 * Initialize pins.
		 * Call once in setup.
		 */
		void init();

		/**
		 * Enable or disable the stepper motor driver.
		 *
		 * @param enable If true, the motor is enabled and can be moved.
		 */
		void enable(bool enable);

		/**
		 * Motor enable status.
		 *
		 * @return Returns true if the motor is enabled.
		 */
		bool enable();

		/**
		 * Move by requested number of steps.
		 * This function returns immediately, to check progress call steps_left().
		 *
		 * @param steps The number of steps to move by, negative numbers invert direction.
		 */
		void move(long steps);

		/**
		 * Stop any motor movement.
		 */
		void stop();

		/**
		 * How many steps are left since the last move() call?
		 *
		 * @return The number of steps left to perform.
		 */
		long steps_left();

		/**
		 * Is the motor moving?
		 *
		 * @return True if it is not moving.
		 */
		bool ready();

		/**
		 * Absolute number of steps since zero() call.
		 * This keeps track of the absolute position of the motor.
		 *
		 * @return The absolute motor position in steps relative to the zero position.
		 */
		long steps_abs();

		/**
		 * Absolute number of steps since zero() call.
		 * This keeps track of the absolute position of the motor.
		 *
		 * @param The absolute motor position in steps relative to the zero position.
		 */
		void set_steps_abs(long steps);

		/**
		 * Motor position zeroing.
		 *
		 * @param method Select between zeroing on positive direction, negative or without moving.
		 */
		void zero(ZeroDirection method);

		/**
		 * Set the delay between steps in uS.
		 * This sets the speed of the motor.
		 * The speed is reached with acceleration.
		 *
		 * @param delay_us The delay between step signals in uS.
		 */
		void set_step_delay(long delay_us);

		/**
		 * Set the delay between steps in uS.
		 * This sets the speed of the motor.
		 * The speed is set immediately.
		 *
		 * @param delay_us The delay between step signals in uS.
		 */
		void set_step_delay_now(long delay_us);

		/**
		 * Called by the ISR for the timer.
		 */
		void timer_isr();

#ifdef WITH_ENCODER
		/**
		 * Called by the ISR for the external interrupt pin 0.
		 */
		void int_isr();

		bool ignore_encoder;
#endif  // WITH_ENCODER

		static AsyncStepperDriver* active;  /**< Points to the active object of this class. This implementation supports only one object.*/

		uint8_t last_error; /**< Stores the last error condition.*/
    
#ifdef WITH_ENCODER
		int steps_since_int;    /**< Steps since last int signal. Keeps track of lost steps.*/
#endif  // WITH_ENCODER

	protected:
		uint8_t pin_step;
		uint8_t pin_enable;
		uint8_t pin_direction;
		uint8_t pin_endstop_pos;
		uint8_t pin_endstop_neg;

		uint8_t ctr_endstop;	/**< Keeps track of how many consecutive times the endstop was found to be activated.*/

		uint8_t positive_direction; /**< Defines value on direction pin for positive step numbers.*/
		uint8_t negative_direction; /**< Defines value on direction pin for negative step numbers.*/

		long absolute_position; /**< Position relative to zero in steps.*/
		long relative_position; /**< Position relative to destination of last move.*/
		bool zeroing;           /**< True if motor is moving to zero axis.*/
		bool moving;            /**< True if motor is moving (also for zeroing).*/
		bool enabled;           /**< Keep track of motor enable status.*/


		unsigned int timer_counter; /**< Compared to Timer1 to set the step delay.*/

		long target_delay;	/**< Delay for requested target speed.*/
		long current_delay;	/**< Delay for currently set speed.*/
		float current_delay_float;	/**< Delay for currently set speed as a float.*/
		long ctr_steps_acc;	/**< Steps counter for acceleration.*/
		long ctr_steps_acc_max; /**< Maximum value for acceleration steps couter.*/
		float ctr_steps_acc_max_float; /**< Maximum value for acceleration steps counter as a float.*/

		/**
		 * Prepare variables to start moving from lower speed to target speed.
		 */
		void start_move( long steps);

		/**
		 * Manage acceleration.
		 */
		void accelerate();

		/**
		 * Stop movement after an endstop was hit.
		 * Sets absolute position to 0.
		 */
		void endstop_stop();

		/**
		 * Routine to ensure endstop 
		 *
		 * @return True the motor was stopped.
		 */
		bool check_endstop(uint8_t endstop_pin);
};


#endif // ASYNCSTEPPER_H
