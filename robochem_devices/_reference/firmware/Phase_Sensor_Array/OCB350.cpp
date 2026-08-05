/**
 * Interface for OCB350 phase sensor from TTElectronics (with calibration board).
 *
 * Author: Simone Pilon <s.pilon at uva.nl> - Noël Research Group - 2024
 */

#include "OCB350.h"
#include <Arduino.h>

OCB350::OCB350(uint8_t _pin_A, uint8_t _pin_B, uint8_t _pin_Calibration, uint8_t _pin_Analog)
{
	pin_A = _pin_A;
	pin_B = _pin_B;
	pin_Cal = _pin_Calibration;
	pin_Analog = _pin_Analog;
	monitor_change = Monitor::NO;
}

OCB350::OCB350()
{
	pin_A = 0;
	pin_B = 1;
	pin_Cal = 2;
	pin_Analog = 14;
	monitor_change = Monitor::NO;
}

void OCB350::init()
{
	pinMode(pin_A, INPUT);
	pinMode(pin_B, INPUT);
	digitalWrite(pin_Cal, HIGH);
	pinMode(pin_Cal, OUTPUT);
	digitalWrite(pin_Cal, HIGH);
	pinMode(pin_Analog, INPUT);
}

Phase OCB350::read()
{
	last_value = static_cast<Phase>(digitalRead(pin_A)+digitalRead(pin_B)*2);
	return last_value;
}

int OCB350::analog_read()
{
	return analogRead(pin_Analog);
}

void OCB350::calibrate()
{
	digitalWrite(pin_Cal, LOW);
	delay(100);
	digitalWrite(pin_Cal, HIGH);
}

void OCB350::monitor(Monitor mode)
{
	monitor_change = mode;
	if (monitor_change != Monitor::NO)
	{
		monitor_value = read();
		last_change = millis();
	}
}

Monitor OCB350::monitor()
{
	return monitor_change;
}

bool OCB350::has_changed()
{
	if (monitor_change != Monitor::NO)
	{
		unsigned long time = millis();
		Phase last = last_value;
		bool new_change = last != read();
		bool changed = monitor_value != last_value;
		if (changed)
		{
			if (new_change)
			{
				last_change = time;
			}
			else
			{
				if ((time - last_change) >= OCB_MIN_TIME)
				{
					monitor_value = last_value;
					last_change = time;
					if (monitor_change == Monitor::ONCE)
					{
						monitor_change = Monitor::NO;
					}
					return true;
				}
			}
		}

	}
	return false;
}
