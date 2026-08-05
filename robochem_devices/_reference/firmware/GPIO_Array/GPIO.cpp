/**
 * Utilities for the Arduino UNO GPIO interface.
 *
 * Author: Simone Pilon <s.pilon at uva.nl> - Noël Research Group - 2024
 */

#include "GPIO.h"
#include <Arduino.h>

uint8_t Gpio_pin::error = GPIO_ERROR_OK;

Gpio_pin::Gpio_pin()
{
	pin = 2;
	mode = GPIO_MODE_INPUT;
	output_value = 0;
}

void Gpio_pin::set_mode(uint8_t _mode)
{
	if (_mode == GPIO_MODE_PWM)
	{
		if (pin == 3 ||
				pin == 5 ||
				pin == 6 ||
				pin == 9 ||
				pin == 10 ||
				pin == 11)
		{
			mode = _mode;
			pinMode(pin, OUTPUT);
		}
		else
		{
			error = GPIO_ERROR_NO_PWM;
		}
	}
	else if (_mode == GPIO_MODE_INPUT)
	{
		mode = _mode;
		pinMode(pin, INPUT);
	}
	else if (_mode == GPIO_MODE_OUTPUT)
	{
		mode = _mode;
		pinMode(pin, OUTPUT);
	}
	else
	{
		error = GPIO_ERROR_INVALID_MODE;
	}
}

uint8_t Gpio_pin::read()
{
	if (mode != GPIO_MODE_INPUT)
	{
		error = GPIO_ERROR_READ_FROM_OUTPUT;
		return 0;
	}
	return digitalRead(pin);
}

void Gpio_pin::write(uint8_t value)
{
	if (value >= 2)
	{
		error = GPIO_ERROR_DIGITAL_VALUE;
		return;
	}
	if (mode != GPIO_MODE_OUTPUT)
	{
		error = GPIO_ERROR_WRONG_MODE;
		return;
	}
	digitalWrite(pin, value);
	output_value = value;
}

void Gpio_pin::write_pwm(uint8_t value)
{
	if (mode != GPIO_MODE_PWM)
	{
		error = GPIO_ERROR_WRONG_MODE;
		return;
	}
	if (value > 255)
	{
		error = GPIO_ERROR_PWM_VALUE;
		return;
	}
	analogWrite(pin, value);
	output_value = value;
}

uint8_t Gpio_pin::setvalue_digital()
{
	if (mode != GPIO_MODE_OUTPUT)
	{
		error = GPIO_ERROR_WRONG_MODE;
		return 0;
	}
	return output_value;
}

uint8_t Gpio_pin::setvalue_pwm()
{
	if (mode != GPIO_MODE_PWM)
	{
		error = GPIO_ERROR_WRONG_MODE;
		return 0;
	}
	return output_value;
}

uint8_t Gpio_pin::get_mode()
{
	return mode;
}

Analog_pin::Analog_pin()
{
	pin = 14;
}

int Analog_pin::read()
{
	return analogRead(pin);
}
