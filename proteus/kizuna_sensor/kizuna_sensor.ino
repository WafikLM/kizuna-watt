// Carte capteur Kizuna Watt — version pour simulation Proteus (ATmega328P / Arduino Uno)
//
// Dans Proteus, la pince amperemetrique est remplacee par un POTENTIOMETRE sur A0 :
//   0 V -> 0 A, 5 V -> I_MAX ampere (ce que donnerait une pince + convertisseur de courant efficace).
// Un second potentiometre sur A1 simule le courant de fuite : 0 V -> 0 mA, 5 V -> LEAK_MAX mA.
// Tournez les potentiometres PENDANT la simulation : l'application se met a jour en direct.
//
// Sortie serie 9600 bauds, une ligne JSON toutes les 2 s :
//   {"power_w":812.5,"voltage":230.0,"current_a":3.84,"cosphi":0.92,"leak_ma":2.1}
// NON COMPILE NI TESTE dans Proteus par l'auteur : a verifier dans l'IDE Arduino.

const float V_NOM = 230.0;       // tension supposee (pas de capteur de tension)
const float COSPHI = 0.92;       // facteur de puissance suppose
const float I_MAX = 30.0;        // courant pleine echelle du potentiometre (A)
const float LEAK_MAX = 100.0;    // fuite pleine echelle du potentiometre (mA)
const float BREAKER_A = 26.0;    // disjoncteur : 6 kW / 230 V
const unsigned long PERIOD_MS = 2000;

const int PIN_I = A0;
const int PIN_LEAK = A1;
const int PIN_LED = 13;          // LED d'alerte (fuite >= 30 mA ou courant > 80 % du disjoncteur)

float readAvg(int pin) {         // moyenne de 16 lectures pour lisser
  long s = 0;
  for (int i = 0; i < 16; i++) {
    s += analogRead(pin);
    delay(2);
  }
  return s / 16.0;
}

void setup() {
  Serial.begin(9600);
  pinMode(PIN_LED, OUTPUT);
}

void loop() {
  float irms = readAvg(PIN_I) / 1023.0 * I_MAX;
  float leak = readAvg(PIN_LEAK) / 1023.0 * LEAK_MAX;
  float power = irms * V_NOM * COSPHI;

  bool alert = (leak >= 30.0) || (irms > 0.8 * BREAKER_A);
  digitalWrite(PIN_LED, alert ? HIGH : LOW);

  Serial.print(F("{\"power_w\":"));
  Serial.print(power, 1);
  Serial.print(F(",\"voltage\":"));
  Serial.print(V_NOM, 1);
  Serial.print(F(",\"current_a\":"));
  Serial.print(irms, 2);
  Serial.print(F(",\"cosphi\":"));
  Serial.print(COSPHI, 2);
  Serial.print(F(",\"leak_ma\":"));
  Serial.print(leak, 1);
  Serial.println(F("}"));

  delay(PERIOD_MS);
}
