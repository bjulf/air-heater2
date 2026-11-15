# PI-regulering på Pi med Azure-telemetri

Status: kode klargjort lokalt; ny kontrollkjede må fortsatt testes på Pi og Azure.
Git brukes for overføring. ZIP-pakken og scp-veiledningen fra den første kladden
skal ikke brukes for denne versjonen.

## Valgt løsning

Pi-en kjører luftvarmermodellen, en PI-regulator og målefilter med faste parametere:
Ts = 0,1 s, Kp = 0,52 V/°C, Ti = 18 s og Tf = 2 s. Filteret brukes lokalt og
filtrert temperatur sendes ikke til skyen. Regulatorparametre kan ikke endres
fra cloud. Cloud brukes til lagring og overvåking av temperatur, settpunkt og pådrag.

Telemetri sendes én gang per sekund. Kontrollsløyfen kjører i egen tråd på 10 Hz;
nettverk og lagring av sekvensnummer avventes ikke av reguleringen. Starttilstanden
er likevekt ved 28 °C, og settpunktet endres til 34 °C etter 20 s. Målestøy har
sigma = 0,15 °C. Kontrollfilteret er inkludert i stabilitetsanalysen.

Nytt JSON-format er airheater.control.v1 og har bare de sju eksisterende basisfeltene
(schema, device_id, sequence_number, timestamp, elapsed_seconds, temperature_c,
heater_voltage), pluss **setpoint_c**. temperature_c er rå temperaturmåling med støy.
heater_voltage er pådraget i det påfølgende intervallet. Gamle simulation.v1-meldinger
støttes fortsatt og har ikke settpunkt. Reguleringsavviket som bruker filtrert måling
beregnes bare lokalt. Et avvik fra rå temperatur kan beregnes i visningen ved behov,
men er ikke nøyaktig det samme signalet regulatoren bruker.

RAM-køen har 300 plasser og én måling under klargjøring/sending. Full kø avviser
nyeste måling. Sekvensnummer reserveres når senderen tar målingen ut av køen;
retry bruker samme JSON og ID. Telleren i .state bevares mellom kjøringer.
Ved normal avslutning forsøkes køen tømt i ti sekunder. Uleverte RAM-målinger
går tapt ved avbrudd eller timeout. sent betyr bekreftet av transporten, ikke
bekreftet lagret i PostgreSQL. Ved stdout betyr det skrevet til konsollen.

## 1. Stopp og oppdater mottakeren på VM

Stopp den manuelle mottakeren med Ctrl-C i terminalen der den kjører. Kontroller:

```bash
pgrep -afu azureuser '[a]pps/azure_receiver.py'
cd /home/azureuser/air-heater2
git status --short
git pull --ff-only
```

Hvis Git rapporterer konflikt med lokale endringer, behold dem og undersøk
før du fortsetter. Ikke bruk reset. Ikke slett .env, .state eller checkpoints.
Det skal bare kjøre én mottaker for consumer group airheater-postgres.
Hvis en systemd-tjeneste faktisk er installert, stopp den før oppdatering.

## 2. Legg til settpunkt i databasen

Åpne **deploy/control_telemetry_migration.sql** i pgAdmin Query Tool, koblet til
**airheater** som administrator, og kjør hele skriptet. Det legger bare til
setpoint_c og en kontroll av gyldige verdier. Eksisterende data bevares og gamle
målinger har NULL som settpunkt. Skriptet kan kjøres flere ganger.

Hvis den gamle omfattende kladdemigreringen allerede er kjørt, fjerner skriptet
kladdens valideringsregel. Ubrukte kolonner beholdes, slik at ingen data slettes.
Ikke kjør init.sql eller azure_schema.sql på nytt mot eksisterende database.

Kjør deretter på VM:

```bash
.venv/bin/python -m unittest discover -s tests -p test_ingestion.py
.venv/bin/python -m unittest discover -s tests -p test_control_publisher.py
.venv/bin/python -u apps/azure_receiver.py
```

La mottakeren kjøre i denne terminalen. Den sjekker at setpoint_c finnes før
mottak starter. Ikke start ny kontrollpublisher mot den gamle mottakeren:
den gamle mottakeren avviser kontrollformatet og kan checkpoint'e det som ugyldig.

## 3. Oppdater Pi-en via Git

Stopp eventuell gammel publisher med Ctrl-C først. På Pi:

```bash
pgrep -afu bjulf '[a]pps/.*publisher.py'
cd /home/bjulf/air-heater2
git status --short
git pull --ff-only
source .venv-azure/bin/activate
python -m pip install -e '.[azure]'
python -m unittest discover -s tests -p test_control_publisher.py
```

Det skal kjøres **8 tester**, ikke 0. Python 3.12-miljøet .venv-azure brukes.
Ingen ZIP-overføring er nødvendig. Kontroller kildefilene hvis Git gir konflikt
eller testfilen mangler. .env og eksisterende enhetscounter skal beholdes.

## 4. Lokal test på Pi

```bash
python -u apps/control_publisher.py --transport stdout --device-id control-local-test --state-dir /tmp/airheater-control-local --duration 5 --step-at 2
```

Forvent omtrent 50 kontrolloppdateringer og fem JSON-meldinger. Denne testidentiteten
bruker sin egen counter og påvirker ikke den ekte IoT Hub-enhetens sekvensnummer.

## 5. Kort Azure-test

Utfør dette først når oppdatert mottaker kjører og databasen har setpoint_c:

```bash
python -u apps/control_publisher.py --transport azure --duration 60
```

Ved fungerende nettverk og normal avslutning forventes 600 samples, 60 generated,
60 sent, 0 dropped og 0 unsent. Bruk eksisterende .env for airheater-sim-1.
Hvis eksisterende forbindelse bruker WebSockets, legg til --websockets.
Dette er en tidsbegrenset kjøring. Ingen automatisk Pi-tjeneste er installert.

I pgAdmin:

```sql
SELECT device_id, sequence_number, measured_at, elapsed_seconds,
       temperature_c, setpoint_c, heater_voltage
FROM public.simulation_measurements
WHERE device_id = 'airheater-sim-1' AND setpoint_c IS NOT NULL
ORDER BY sequence_number DESC
LIMIT 65;
```

Bekreft nye sekvenser fra denne kjøringen, omtrent ett sekund mellom meldingene,
settpunkt 28 til 34 °C etter 20 s, temperatur som følger etter og pådrag mellom
0 og 5 V. Checkpoints skal flytte seg uten å nullstilles. Unike nøkler og commit
før checkpoint er uendret. Gamle åpne målinger vises fortsatt med NULL-settpunkt.

## 6. Grafana og eventuell forstyrrelsestest

Importer **deploy/grafana/simulation-import.json** via
Dashboards → New → Import. Velg din eksisterende PostgreSQL-datakilde i feltet
**Air heater PostgreSQL** og klikk Import. Ved samme dashboard-UID som tidligere
kan du erstatte den eksisterende importen. Valget brukes både av panelene og
Source-variabelen. Datakilden må allerede være konfigurert og bestå Save & test.

Dashboardet viser rå temperatur og
settpunkt sammen, varmepådrag og alder på siste måling. Det viser ingen filtrert
temperatur eller regulatorparametere. simulation.json er varianten for
filprovisionering med fast datakilde-UID airheater-postgres. Den separate
importfilen lar deg velge datakilde under import og trenger ikke dette UID-et.
Datavising i brukerens Grafana er ennå ikke bekreftet etter denne rettingen.

Etter vellykket korttest kan du demonstrere kaldere innløpsluft:

```bash
python -u apps/control_publisher.py --transport azure --duration 300 --disturbance-at 150
```

Ved 150 s endres modellens innløp fra 21,5 til 18,5 °C. Det sendes omtrent 300
meldinger. Parametrene endres ikke fra cloud. Jitter og sendestatistikk rapporteres
lokalt ved avslutning; jitter er målt kontrolltiming, ikke nettverkslatens.

## Kontinuerlig kjøring

Etter oppdatering med git pull kan programmet kjøres til Ctrl-C:

```bash
python -u apps/control_publisher.py --transport azure --continuous --publish-interval 15
```

Kontroll og målefilter kjører fortsatt hvert 0,1 sekund. Bare publiseringen
reduseres til hvert 15. sekund, omtrent 5760 meldinger per døgn. Med standard
publisering hvert sekund blir det 86400 meldinger per døgn. IoT Hub Free har
8000 kvoteenheter per døgn, med 0,5 KB måleenhet; faktisk forbruk avhenger av
meldingsstørrelse og annen trafikk. Se
[Microsofts kvotedokumentasjon](https://learn.microsoft.com/en-us/azure/iot-hub/iot-hub-devguide-quotas-throttling).

Programmet har ingen voksende prøvehistorikk og beholder den avgrensede sendekøen.
Det kjøres i forgrunnen i terminalen; dette installerer ikke en tjeneste eller
automatisk oppstart. --continuous og --duration kan ikke brukes samtidig.
Settpunktet endres én gang etter 20 s og holdes deretter på 34 °C.

## Verifikasjon av kontinuerlig modus

Lokale tester dekker kontroll til serialisering og mottakervalidering,
parameterisert SQL, sekvensbevaring, retry uten ny identitet, full kø, nettverk
som henger, filfeil og opprydding ved avbrudd. Ekte PostgreSQL-migrering, Pi-timing,
Azure-levering og Grafana må fortsatt bekreftes under utrulling. Den tidligere
60-sekunders konsolltesten gjaldt den utvidede kladden; nye tester verifiserer
at den forenklede versjonen bare sender basisfeltene pluss settpunkt.

Analysen og en reproduksjonsveiledning er beskrevet i CONTROL_THEORY.md.
