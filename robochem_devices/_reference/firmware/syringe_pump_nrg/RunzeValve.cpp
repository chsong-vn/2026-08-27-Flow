#include "RunzeValve.h"
#include <Arduino.h>

RunzeValve::RunzeValve(uint8_t _pin_VT, uint8_t _pin_IO1, uint8_t _pin_IO2) {
	pin_VT = _pin_VT;
	pin_IO1 = _pin_IO1;
	pin_IO2 = _pin_IO2;

	setpoint = LOW;
}

void RunzeValve::init() {
	pinMode(pin_VT, OUTPUT);
	pinMode(pin_IO1, INPUT);
	pinMode(pin_IO2, INPUT);

	digitalWrite(pin_VT, setpoint);
}

void RunzeValve::set_position(uint8_t position) {
	setpoint = position==1?HIGH:LOW;
	digitalWrite(pin_VT, setpoint);
}

uint8_t RunzeValve::get_position() {
	uint8_t position = 0;
	position += digitalRead(pin_IO1)==HIGH?1:0;
	position += digitalRead(pin_IO2)==HIGH?2:0;
	if (position == 2) {
		return 1;
	}
	else if (position == 1) {
		return 0;
	}
	else {
		return 2;
	}
}

uint8_t RunzeValve::get_setpoint() {
	return setpoint==HIGH?1:0;
}

bool RunzeValve::ready() {
	return digitalRead(pin_IO1)==!setpoint && digitalRead(pin_IO2)==setpoint;
}
