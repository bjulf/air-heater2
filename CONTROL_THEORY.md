# Kontrollteoridelen - kun simulering

Fysisk luftvarmer er ikke nødvendig etter avklaring med lærer, opplyst av studenten.

Start med [rapporten](../output/control_theory/rapport.md). Den inneholder
systemskisse, modell, diskretisering, PI, Ziegler-Nichols, stabilitetsanalyse,
resultater og forslag til muntlig gjennomgang.

Fra LAB 3-mappen:

```powershell
python -m pip install -e "./code[analysis]"
python code/tests/test_airheater.py
python code/tests/test_sampled_analysis.py
python code/demos/control_study.py
```

Fra code-mappen kan du bruke `python demos/control_study.py`.

Resultatene lagres i `../output/control_theory/`:

- `rapport.md`: ferdig norsk fagtekst og resultattabeller.
- `figures/`: åtte figurer i PNG og SVG.
- `data/`: tidsserier og parameterstudie i CSV.
- `results.json`: parametere, stabilitetsmarginer og ytelsesmål.

Rapportteksten vedlikeholdes i `docs/control_report_template.md`.
Resultattabellene settes inn automatisk av `demos/control_study.py`.
Endringer i generert `rapport.md` overskrives ved neste kjøring.

Den nye analysen i `airheater/analysis/sampled.py` inkluderer filter og
samplingstid. De eldre demoene bruker også kontinuerlige marginer uten filter;
bruk den samlede studien og rapporten for dokumentasjon av denne leveransen.
