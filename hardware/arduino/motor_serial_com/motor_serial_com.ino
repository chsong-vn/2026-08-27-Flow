/*
 * Colosseum Fraction Collector - Motor Controller
 * Based on: pachterlab/colosseum (BSD-2-Clause)
 *
 * Protocol: <MODE,MOTOR_ID,arg1,arg2,arg3>
 *   MODE: RUN, STOP, RESUME, PAUSE, SET_SPEED, SET_ACCEL
 *   MOTOR_ID: 3-digit binary (111 = all motors, 100 = X only, etc.)
 *
 * Hardware: Arduino Uno + CNC Shield v3 + DRV8825
 * Baud: 2,000,000
 */

#include <AccelStepper.h>

// Motor constants
#define MOTOR_STEPS 200
#define MICROSTEPS 32
#define TOTAL_STEPS 6400

// Default speeds (steps/sec)
#define X_SPEED 1000
#define Y_SPEED 1000
#define Z_SPEED 1000
float speeds[3] = {X_SPEED, Y_SPEED, Z_SPEED};

// Default accelerations (steps/sec^2)
#define X_ACCEL 5000.0
#define Y_ACCEL 5000.0
#define Z_ACCEL 5000.0
float accels[3] = {X_ACCEL, Y_ACCEL, Z_ACCEL};

// CNC Shield pin mapping
#define EN    8   // stepper enable (LOW = enabled)

#define X_DIR 5
#define Y_DIR 6
#define Z_DIR 7
int dir_pins[3] = {X_DIR, Y_DIR, Z_DIR};

#define X_STP 2
#define Y_STP 3
#define Z_STP 4
int stp_pins[3] = {X_STP, Y_STP, Z_STP};

#define BAUD_RATE 2000000

// Stepper instances
AccelStepper stepper1(AccelStepper::DRIVER, X_STP, X_DIR);
AccelStepper stepper2(AccelStepper::DRIVER, Y_STP, Y_DIR);
AccelStepper stepper3(AccelStepper::DRIVER, Z_STP, Z_DIR);
AccelStepper steppers[3] = {stepper1, stepper2, stepper3};

// LED
const int ledPin = 13;

// Serial buffer
const byte buffSize = 64;
char inputBuffer[buffSize];
const char startMarker = '<';
const char endMarker = '>';

byte bytesRecvd = 0;
boolean readInProgress = false;
boolean newDataFromPC = false;
boolean executeCommand = false;

char messageFromPC[buffSize] = {0};
char mode[buffSize] = {0};
int motors[3] = {0, 0, 0};

float arg_m1 = 0.0;
float arg_m2 = 0.0;
float arg_m3 = 0.0;
float args[3] = {0.0, 0.0, 0.0};

float remainder[3] = {0.0, 0.0, 0.0};

unsigned long curMillis;

// Function pointer type
typedef void (* FreeFunction)();

typedef struct {
  int mode_idx;
  char* mode;
  FreeFunction function;
} FunctionMap;

int array_sum(int * array, int len) {
  int arraySum = 0;
  for (int index = 0; index < len; index++) {
    arraySum += array[index];
  }
  return arraySum;
}

// ============= Command Functions =============

void _run() {
  for (int i = 0; i < 3; i += 1) {
    if (motors[i] == 1) {
      steppers[i].move(args[i]);
    }
  }

  int stepperStatus[3] = {0, 0, 0};

  // Enable steppers
  digitalWrite(EN, LOW);
  while (array_sum(stepperStatus, 3) != array_sum(motors, 3)) {
    for (int i = 0; i < 3; i += 1) {
      if (motors[i] == 1) {
        if (stepperStatus[i] == 0) {
          steppers[i].run();
        }
        if (steppers[i].distanceToGo() == 0) {
          stepperStatus[i] = 1;
        }
      }
    }
    // Check for new commands during motion (allows STOP)
    getDataFromPC();
    if (newDataFromPC) {
      replyToPC();
      break;
    }
  }
  // Disable steppers when idle
  digitalWrite(EN, HIGH);
}

void _stop() {
  for (int i = 0; i < 3; i += 1) {
    steppers[i].stop();
  }
}

void _pause() {
  for (int i = 0; i < 3; i += 1) {
    remainder[i] = steppers[i].distanceToGo();
  }
  _stop();
}

void _resume() {
  for (int i = 0; i < 3; i += 1) {
    args[i] = remainder[i];
  }
  _run();
}

void _set_speed() {
  for (int i = 0; i < 3; i += 1) {
    if (motors[i] == 1) {
      steppers[i].setCurrentPosition(0.0);
      steppers[i].setMaxSpeed(args[i]);
      steppers[i].setSpeed(args[i]);
    }
  }
}

void _set_accel() {
  for (int i = 0; i < 3; i += 1) {
    if (motors[i] == 1) {
      steppers[i].setAcceleration(args[i]);
    }
  }
}

const int function_count = 6;
const FunctionMap functions[function_count] {
  {0, "RUN", _run},
  {1, "STOP", _stop},
  {2, "RESUME", _resume},
  {3, "PAUSE", _pause},
  {4, "SET_SPEED", _set_speed},
  {5, "SET_ACCEL", _set_accel}
};

// ============= Setup & Loop =============

void setup() {
  Serial.begin(BAUD_RATE);

  pinMode(EN, OUTPUT);
  digitalWrite(EN, HIGH);

  // LED flash to indicate boot
  pinMode(ledPin, OUTPUT);
  digitalWrite(ledPin, HIGH);
  delay(500);
  digitalWrite(ledPin, LOW);
  delay(500);

  // Initialize all steppers
  for (int i = 0; i < 3; i += 1) {
    steppers[i].setMaxSpeed(speeds[i]);
    steppers[i].setSpeed(speeds[i]);
    steppers[i].setAcceleration(accels[i]);
    steppers[i].setCurrentPosition(0.0);
  }

  Serial.println("<Arduino is ready>");
}

void loop() {
  curMillis = millis();
  getDataFromPC();
  replyToPC();
  execute();
}

// ============= Serial Communication =============

void getDataFromPC() {
  if (Serial.available() > 0) {
    digitalWrite(ledPin, HIGH);
    char x = Serial.read();

    if (x == endMarker) {
      readInProgress = false;
      newDataFromPC = true;
      inputBuffer[bytesRecvd] = 0;
      return parseData();
    }

    if (readInProgress) {
      inputBuffer[bytesRecvd] = x;
      bytesRecvd++;
      if (bytesRecvd == buffSize) {
        bytesRecvd = buffSize - 1;
      }
    }

    if (x == startMarker) {
      bytesRecvd = 0;
      readInProgress = true;
    }
    digitalWrite(ledPin, LOW);
  }
}

void parseData() {
  char * strtokIndx;

  strtokIndx = strtok(inputBuffer, ",");
  strcpy(mode, strtokIndx);

  strtokIndx = strtok(NULL, ",");
  String motorstr(strtokIndx);

  motors[0] = motorstr[0] - '0';
  motors[1] = motorstr[1] - '0';
  motors[2] = motorstr[2] - '0';

  strtokIndx = strtok(NULL, ",");
  arg_m1 = atof(strtokIndx);
  strtokIndx = strtok(NULL, ",");
  arg_m2 = atof(strtokIndx);
  strtokIndx = strtok(NULL, ",");
  arg_m3 = atof(strtokIndx);

  args[0] = arg_m1;
  args[1] = arg_m2;
  args[2] = arg_m3;

  newDataFromPC = true;
}

void replyToPC() {
  if (newDataFromPC) {
    newDataFromPC = false;
    Serial.print("<");
    Serial.print(mode);
    Serial.print(",");
    Serial.print(String(motors[0]));
    Serial.print(String(motors[1]));
    Serial.print(String(motors[2]));
    Serial.print(",");
    Serial.print(args[0]);
    Serial.print(",");
    Serial.print(args[1]);
    Serial.print(",");
    Serial.print(args[2]);
    Serial.println(">");

    executeCommand = true;
  }
}

void execute() {
  if (executeCommand) {
    executeCommand = false;
    for (int i = 0; i < function_count; i++) {
      if (strcmp(mode, functions[i].mode) == 0) {
        return (functions[i].function)();
      }
    }
    return;
  }
}
