#include <Servo.h>

#define POSITION_UP 150
#define POSITION_DOWN 75
#define DELAY 5

#define PIN_SERVO 9
#define PIN_INPUT 2
#define PIN_LED 13

Servo axis_driver;
int current = POSITION_UP;
int target = POSITION_UP;

void setup()
{
  axis_driver.attach(PIN_SERVO);
  axis_driver.write(POSITION_UP);
  pinMode(PIN_INPUT, INPUT);
  pinMode(PIN_LED, OUTPUT);
  digitalWrite(PIN_LED, LOW);
}

void loop()
{
  if (digitalRead(PIN_INPUT) == HIGH)
  {
    target = POSITION_DOWN;
    digitalWrite(PIN_LED, HIGH);
  }
  else
  {
    target = POSITION_UP;
    digitalWrite(PIN_LED, LOW);
  }
  if (target > current)
  {
    current++;
  }
  else if (current > target)
  {
    current--;
  }
  axis_driver.write(current);
  delay(DELAY);
}
