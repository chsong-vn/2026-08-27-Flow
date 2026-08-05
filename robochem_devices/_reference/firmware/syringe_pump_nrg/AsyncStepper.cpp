#include "AsyncStepper.h"

AsyncStepperDriver::AsyncStepperDriver(uint8_t _pin_enable, uint8_t _pin_dir, uint8_t _pin_step, uint8_t _pin_endstop_pos, uint8_t _pin_endstop_neg, bool invert)
{
	active = this;

	pin_step = _pin_step;
	pin_enable = _pin_enable;
	pin_direction = _pin_dir;
	pin_endstop_pos = _pin_endstop_pos;
	pin_endstop_neg = _pin_endstop_neg;

	if (invert)
	{
		positive_direction = LOW;
		negative_direction = HIGH;
	}
	else
	{
		positive_direction = HIGH;
		negative_direction = LOW;
	}

	absolute_position = 0;
	relative_position = 0;
	ctr_endstop = 0;
	target_delay = 50;
	ctr_steps_acc = 0;
	ctr_steps_acc_max = ACC_STEPS_INCREMENT;
	ctr_steps_acc_max_float = ACC_STEPS_INCREMENT;
	zeroing = false;
	moving = false;
	enabled = false;
#ifdef WITH_ENCODER
	steps_since_int = 0;
	ignore_encoder = false;
#endif  // WITH_ENCODER

	timer_counter = 800;

	last_error = MOTOR_ERROR_NO_ERROR;
}

void AsyncStepperDriver::init()
{
	TCCR1A = 0;            // Init Timer1A
	TCCR1B = 0;            // Init Timer1B
	TCCR1B |= B00000001;   // Prescaler = 1
	OCR1A = timer_counter; // Timer Compare1A Register (COMPA)
	TIMSK1 |= B00000010;   // Enable Timer COMPA Interrupt

#ifdef WITH_ENCODER
	EICRA = B00001010;     // External interrupts on Falling edge (both)
	EIMSK |= B00000001;    // Enable External Interrupt INT0 (pin 2)
	pinMode(2, INPUT);
#endif  // WITH_ENCODER

	pinMode(pin_enable, OUTPUT);
	pinMode(pin_direction, OUTPUT);
	pinMode(pin_step, OUTPUT);
	digitalWrite(pin_enable, LOW);
	enabled = false;
	digitalWrite(pin_direction, HIGH);
	digitalWrite(pin_step, LOW);
	pinMode(pin_endstop_pos, INPUT);
	pinMode(pin_endstop_neg, INPUT);
}

void AsyncStepperDriver::enable(bool _enable)
{
	digitalWrite(pin_enable, _enable ? LOW : HIGH);
	enabled = _enable;
}

bool AsyncStepperDriver::enable()
{
	return enabled;
}

void AsyncStepperDriver::move(long steps)
{
	if (steps == 0)
	{
		stop();
		return;
	}
	if (steps > 0)
	{
		digitalWrite(pin_direction, positive_direction);
	}
	else
	{
		digitalWrite(pin_direction, negative_direction);
	}
	start_move(steps);
}

void AsyncStepperDriver::stop()
{
	if (zeroing)
	{
		zeroing = false;
	}
	relative_position = 0;
	moving = false;
}

long AsyncStepperDriver::steps_left()
{
	return relative_position;
}

bool AsyncStepperDriver::ready()
{
	return !moving;
}

long AsyncStepperDriver::steps_abs()
{
	return absolute_position;
}

void AsyncStepperDriver::set_steps_abs(long steps)
{
	absolute_position = steps;
}

void AsyncStepperDriver::zero(ZeroDirection method)
{
	if (method == ZeroDirection::POSITIVE) {
		digitalWrite(pin_direction, positive_direction);
		zeroing = true;
		start_move(10);
	}
	else if (method == ZeroDirection::NEGATIVE)
	{
		digitalWrite(pin_direction, negative_direction);
		zeroing = true;
		start_move(-10);
	}
	else
	{
		absolute_position = 0;
	}
}

void AsyncStepperDriver::set_step_delay(long delay_us)
{
	if (delay_us < MIN_DELAY_US || delay_us > MAX_DELAY_US)
	{
		last_error = MOTOR_ERROR_DELAY_RANGE;
		return;
	}
	target_delay = delay_us;
}

void AsyncStepperDriver::set_step_delay_now(long delay_us)
{
	current_delay = delay_us;
	current_delay_float = float(delay_us);
	long counter = delay_us * 16;
	uint8_t prescaler = 0b101; // 1:1024
	if (delay_us < 4095)
	{
		prescaler = 0b001; // 1:1
	}
	else if (delay_us < 32767)
	{
		prescaler = 0b010; // 1:8
		counter = counter / 8;
	}
	else if (delay_us < 262100)
	{
		prescaler = 0b011; // 1:64
		counter = counter / 64;
	}
	else if (delay_us < 1048500)
	{
		prescaler = 0b100; // 1:256
		counter = counter / 256;
	}
	else
	{
		counter = counter / 1024;
	}
	TIMSK1 &= B11111101;   // Disable Timer COMPA Interrupt
	timer_counter = counter;
	TCCR1B &= 0b11111000;
	TCCR1B |= prescaler;
	TIMSK1 |= B00000010;   // Enable Timer COMPA Interrupt
}

void AsyncStepperDriver::timer_isr()
{
	OCR1A += timer_counter;
	if (!enabled)
	{
		return;
	}
	if (relative_position > 0)
	{
		if (check_endstop(pin_endstop_pos))
		{
			return;
		}
		digitalWrite(pin_step, HIGH);
		delayMicroseconds(PULSE_WIDTH_US);
		digitalWrite(pin_step, LOW);
		if (!zeroing)
		{
			relative_position--;
		}
		absolute_position++;
		accelerate();
#ifdef WITH_ENCODER
		steps_since_int++;
		if (!ignore_encoder && steps_since_int > MAX_STEPS_SINCE_INT)
		{
			relative_position = 0;
			if (zeroing)
			{
				zeroing = false;
			}
			last_error = MOTOR_ERROR_BLOCKED;
		}
#endif  // WITH_ENCODER
		if (relative_position == 0)
		{
			moving = false;
		}
	}
	else if (relative_position < 0)
	{
		if (check_endstop(pin_endstop_neg))
		{
			return;
		}
		digitalWrite(pin_step, HIGH);
		delayMicroseconds(PULSE_WIDTH_US);
		digitalWrite(pin_step, LOW);
		if (!zeroing)
		{
			relative_position++;
		}
		absolute_position--;
		accelerate();
#ifdef WITH_ENCODER
		steps_since_int--;
		if (!ignore_encoder && steps_since_int < -MAX_STEPS_SINCE_INT)
		{
			relative_position = 0;
			if (zeroing)
			{
				zeroing = false;
			}
			last_error = MOTOR_ERROR_BLOCKED;
		}
#endif  // WITH_ENCODER
		if (relative_position == 0)
		{
			moving = false;
		}
	}
	// do nothing if relative_position == 0
}


#ifdef WITH_ENCODER
void AsyncStepperDriver::int_isr()
{
	steps_since_int = 0;
}
#endif  // WITH_ENCODER

void AsyncStepperDriver::start_move(long steps)
{
	ctr_steps_acc = 0;
	ctr_steps_acc_max = INITIAL_ACC_STEPS;
	ctr_steps_acc_max_float = INITIAL_ACC_STEPS;
	if (target_delay >= MIN_DELAY_NO_ACC)
	{
		current_delay = target_delay;
		current_delay_float = float(target_delay);
		set_step_delay_now(target_delay);
	}
	else
	{
		current_delay = MIN_DELAY_NO_ACC;
		current_delay_float = float(MIN_DELAY_NO_ACC);
		set_step_delay_now(MIN_DELAY_NO_ACC);
	}
	moving = true;
	relative_position = steps;
}

void AsyncStepperDriver::accelerate()
{
	long delta_delay = current_delay - target_delay;
	if (delta_delay > 0)
	{
		ctr_steps_acc++;
		if (ctr_steps_acc >= ctr_steps_acc_max)
		{
			ctr_steps_acc = 0;
			ctr_steps_acc_max_float += ACC_STEPS_INCREMENT;
			current_delay_float *= float(ctr_steps_acc_max) / ctr_steps_acc_max_float;
			ctr_steps_acc_max = long(ctr_steps_acc_max_float);
			current_delay = long(current_delay_float);
			if (current_delay < target_delay)
			{
				set_step_delay_now(target_delay);
			}
			else
			{
				set_step_delay_now(current_delay);
			}
		}
	}
}

void AsyncStepperDriver::endstop_stop()
{
	relative_position = 0;
	moving = false;
	if (zeroing)
	{
		zeroing = false;
		absolute_position = 0;
	}
	else
	{
		last_error = MOTOR_ERROR_ENDSTOP_NEG;
	}
}

bool AsyncStepperDriver::check_endstop(uint8_t pin)
{
	if (digitalRead(pin) == LOW)
	{
		ctr_endstop++;
	}
	else
	{
		ctr_endstop = 0;
	}
	if (ctr_endstop >= MAX_STEPS_SINCE_ENDSTOP)
	{
		ctr_endstop = 0;
		endstop_stop();
		return true;
	}
	return false;
}

AsyncStepperDriver *AsyncStepperDriver::active = nullptr;

ISR(TIMER1_COMPA_vect)
{
	if (AsyncStepperDriver::active != nullptr)
	{
		AsyncStepperDriver::active->timer_isr();
	}
}

#ifdef WITH_ENCODER
ISR(INT0_vect)
{
	if (AsyncStepperDriver::active != nullptr)
	{
		AsyncStepperDriver::active->int_isr();
	}
}
#endif  // WITH_ENCODER
