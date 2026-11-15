# Mottaker som systemd-tjeneste på Azure-VM

Status 15. november 2026: implementert i repoet; installasjon og live tester
på VM er ikke bekreftet. Bruk én mottaker for `airheater-postgres` på tvers
av alle maskiner. Ingen checkpoints eller publisher-counter skal slettes.

## 1. Hent kode og stopp manuell mottaker

Først commit/push fra PC. Logg så inn med SSH (passord skrives kun i terminalen).
Kontroller VM-ens gjeldende IP i Azure hvis sist kjente IP ikke svarer.

```bash
cd /home/azureuser/air-heater2
git status --short
git pull --ff-only
```

Ved lokale endringer eller pull-konflikt: stopp og undersøk, ikke bruk reset.
Stopp den manuelle mottakeren med Ctrl-C i dens SSH-vindu. Kontroller:

```bash
pgrep -afu azureuser '[a]pps/azure_receiver.py'
```

Fortsett først når ingen mottakerprosess vises. Hvis gammel
`.state/azure_receiver.lock` fortsatt finnes, er dette den gamle eksistenslåsen.
Bare etter at alle gamle mottakere er bekreftet stoppet, fjern denne ene filen:

```bash
rm -f -- /home/azureuser/air-heater2/.state/azure_receiver.lock
```

Dette er et manuelt migrasjonssteg, aldri en ExecStartPre-kommando.
Ny Linux-kode bruker en eksklusiv, ikke-blokkerende `flock` på
`.state/azure_receiver.process.lock`. OS frigjør låsen ved prosessdød.
Filen blir liggende og **skal ikke slettes**, heller ikke ved restart.
Eksistens betyr ikke at prosessen kjører. Gamle kodeversjoner må ikke kjøres
parallelt med nye. Windows beholder den tidligere eksistenslåsen.

## 2. Kontroller og installer

Eksisterende `.env` lastes av Python med override=False og interpolate=False.
Ingen EnvironmentFile brukes; systemd trenger ikke tolke DSN/passord.
Ikke vis `.env` eller connection strings. Kjør som azureuser:

```bash
cd /home/azureuser/air-heater2
chmod 600 .env
.venv/bin/python apps/check_postgres.py
.venv/bin/python -m unittest discover -s tests -p test_ingestion.py
.venv/bin/python -m unittest discover -s tests -p test_receiver_lock.py
sudo install -m 644 deploy/airheater-receiver.service /etc/systemd/system/airheater-receiver.service
sudo systemd-analyze verify /etc/systemd/system/airheater-receiver.service
sudo systemctl daemon-reload
sudo systemctl enable --now airheater-receiver.service
systemctl status airheater-receiver.service --no-pager
journalctl -u airheater-receiver.service -n 40 --no-pager
```

Tjenesten kjører som azureuser, starter ved boot og starter igjen etter feil
med ti sekunders pause. SIGINT gir samme avslutningssignal som Ctrl-C.
Etter 30 sekunder kan systemd tvinge stopp, også under database-retry.
Checkpoint etter commit gjør replay trygt; test dette live før det hevdes bevist.
Oppstartslinjen og active-status alene bekrefter ikke at data lagres.

## 3. Kontrollert restart og innhenting

Kjør følgende SQL i pgAdmin mot airheater før testen og noter resultatene:

```sql
SELECT count(*) AS rows, max(sequence_number) AS latest_sequence
FROM public.simulation_measurements WHERE device_id = 'airheater-sim-1';
SELECT consumer_group, partition_id, event_offset, sequence_number
FROM public.receiver_checkpoints WHERE consumer_group = 'airheater-postgres';
```

På VM: `sudo systemctl stop airheater-receiver`. Bekreft `inactive` med
`systemctl is-active airheater-receiver` og at pgrep fra steg 1 ikke viser noe.
Send deretter en kort kjøring på Pi med eksisterende counter:

```bash
cd /home/bjulf/air-heater2
source .venv-azure/bin/activate
python -u apps/simulator_publisher.py --transport azure --duration 10 --step-at 5
```

Noter antall bekreftet sendte meldinger og sekvensnumre fra publisheren.
Start på VM: `sudo systemctl start airheater-receiver`.
Kjør SQL på nytt og bekreft at alle bekreftet sendte sekvenser nå er lagret,
og at checkpointet på brukt partisjon har flyttet seg. Tillat tid til innhenting.
Tell ikke gamle meldinger som testens nye meldinger. Hull i counter kan være lovlig.

Kjør `sudo systemctl restart airheater-receiver`, kontroller journal og SQL igjen.
Uten ny publisering eller etterslep skal målingsantallet være uendret.
Noter tidspunkt, før/etter-resultater og eventuelle avvik i presentasjonsnotatene.
Dette tester kontrollert restart og etterslep; det beviser ikke krasj midt mellom
commit/checkpoint, databaseutfall eller automatisk oppstart etter VM-reboot.

## Drift og oppdatering

Logger: `journalctl -u airheater-receiver -f` (Ctrl-C stopper bare loggvisningen).
Stopp: `sudo systemctl stop airheater-receiver`.
Fjern boot-oppstart ved behov: `sudo systemctl disable --now airheater-receiver`.
Ved kodeoppdatering: stopp tjenesten, `git pull --ff-only`, start tjenesten.
Ved endret unit: installer unit på nytt og kjør daemon-reload før start.
Ikke kjør manuell mottaker mens tjenesten er aktiv.

Referanser: [Python flock](https://docs.python.org/3/library/fcntl.html#fcntl.flock),
[systemd service](https://www.freedesktop.org/software/systemd/man/latest/systemd.service.html).
