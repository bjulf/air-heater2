"""Reproduce the simulation-only control study, figures, CSVs and metrics.

Run: python code/demos/control_study.py
Requires the project's analysis extra (numpy, matplotlib). No network or hardware.
"""
import csv
import json
import math
import sys
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np

from airheater import AirHeaterParams, AirHeaterSim, LowPassFilter, PIController
from airheater.analysis import margins, ultimate_point, ziegler_nichols
from airheater.analysis.sampled import sampled_margins, response, phase, poles

CODE = Path(__file__).resolve().parents[1]
ROOT = CODE.parent
OUT = ROOT / 'output' / 'control_theory'
TS = 0.1
TF = 2.0
KP, TI = 0.52, 18.0
P = AirHeaterParams()


def run(kp=KP, ti=TI, tf=TF, duration=350, target=34, noise=0,
        disturbance=False, saturation=False, params=None):
    """Sample before updating, matching P(z)=b*z^-(Nd+1)/(1-a*z^-1)."""
    p = replace(params or P)
    sim = AirHeaterSim(TS, params=p, T_init=28, noise_std=noise, seed=20261115)
    u0 = sim.required_u(28)
    sim.reset(T_init=28, u_init=u0)
    pi = PIController(kp, ti, TS, u_init=u0)
    filt = LowPassFilter(tf, TS, y_init=28)
    rows = []
    for k in range(round(duration/TS)+1):
        t = k*TS
        r = 28 if t < 20 else target
        if saturation:
            r = 45 if 20 <= t < 200 else 28
        if disturbance:
            p.T_env = 18.5 if t >= 150 else 21.5
        y = sim.T_out
        ym = sim.measure()
        yf = filt.update(ym)
        u = pi.update(r, yf)
        rows.append((t, r, y, ym, yf, u, p.T_env))
        sim.step(u)
    return np.asarray(rows)


def save_data(name, data):
    np.savetxt(OUT/'data'/f'{name}.csv', data, delimiter=',',
               header='time_s,setpoint_C,temperature_C,measured_C,filtered_C,control_V,ambient_C',
               comments='', fmt='%.9f')


def save_fig(name, fig):
    fig.tight_layout()
    fig.savefig(OUT/'figures'/f'{name}.png', dpi=170)
    fig.savefig(OUT/'figures'/f'{name}.svg')
    plt.close(fig)


def axes_pair(title):
    fig, ax = plt.subplots(2, 1, sharex=True, figsize=(10, 7))
    ax[0].set_title(title)
    ax[0].set_ylabel('Temperatur [°C]')
    ax[1].set_ylabel('Pådrag [V]')
    ax[1].set_xlabel('Tid [s]')
    ax[1].set_ylim(-0.2, 5.2)
    for a in ax:
        a.grid(alpha=.25)
    return fig, ax


def step_metrics(d, target=34):
    x = d[d[:, 0] >= 20]
    error = x[:, 1]-x[:, 2]
    span = abs(target-28)
    outside = np.flatnonzero(abs(error) > .02*span)
    settle = None
    if not len(outside):
        settle = 0.
    elif outside[-1] < len(x)-1:
        settle = float(x[outside[-1]+1, 0]-20)
    return dict(overshoot_pct=float(max(0, max(x[:, 2])-target)/span*100),
                settling_2pct_s=settle,
                iae_Cs=float(np.sum((abs(error[:-1])+abs(error[1:]))*.5*np.diff(x[:, 0]))),
                final_error_C=float(np.mean(error[-200:])),
                saturation_pct=float(np.mean((x[:, 5] <= 1e-9) |
                                              (x[:, 5] >= 5-1e-9))*100))


def main():
    for folder in ('data', 'figures'):
        (OUT/folder).mkdir(parents=True, exist_ok=True)
    plt.rcParams.update({'font.size': 10, 'axes.spines.top': False,
                         'axes.spines.right': False})
    results = {'configuration': dict(Ts=TS, Tf=TF, Kp=KP, Ti=TI,
                                    noise_std_C=.15, seed=20261115),
               'parameters': vars(P), 'step': {}, 'margins': {}}

    # 1 V open-loop step, with analytic continuous reference.
    sim = AirHeaterSim(TS)
    times = np.arange(1501)*TS
    y = [sim.T_out]
    for _ in times[1:]:
        y.append(sim.step(1))
    y = np.array(y)
    exact = P.T_env+P.K_h*(1-np.exp(-np.maximum(times-P.theta_d, 0)/P.theta_t))
    results['euler_max_error_C'] = float(max(abs(y-exact)))
    np.savetxt(OUT/'data'/'open_loop.csv', np.c_[times, y, exact],
               delimiter=',', header='time_s,euler_C,analytic_C', comments='')
    fig, ax = plt.subplots(figsize=(10, 5))
    ax.plot(times, y, label='Diskret Euler-modell')
    ax.plot(times, exact, '--', label='Analytisk kontinuerlig modell')
    ax.axvline(2, color='gray', ls=':', label='Forsinkelse 2 s')
    ax.scatter([24], [21.5+3.5*(1-math.exp(-1))], color='black',
               label='63,2 % etter forsinkelse + tidskonstant')
    ax.set(xlabel='Tid [s]', ylabel='Temperatur [°C]',
           title='Åpen sløyfe: sprang i pådrag fra 0 til 1 V ved t = 0')
    ax.grid(alpha=.25)
    ax.legend()
    save_fig('01_open_loop', fig)

    zn = ziegler_nichols(P, 'PI')
    ku, pu = ultimate_point(P)
    results['ziegler_nichols'] = dict(Ku=ku, Pu_s=pu, Kp=zn.Kp, Ti_s=zn.Ti)
    candidates = [('ZN uten filter', zn.Kp, zn.Ti, 0),
                  ('ZN med filter', zn.Kp, zn.Ti, TF),
                  ('Valgt PI med filter', KP, TI, TF)]
    fig, ax = axes_pair('Settpunkt 28 → 34 °C ved t = 20 s, uten målestøy')
    for name, kp, ti, tf in candidates:
        d = run(kp, ti, tf)
        save_data(name.replace(' ', '_'), d)
        results['step'][name] = step_metrics(d)
        mm = sampled_margins(kp, ti, TS, tf)
        results['margins'][name] = dict(vars(mm),
                                      max_pole_radius=float(max(abs(poles(kp, ti, TS, tf)))))
        ax[0].plot(d[:, 0], d[:, 2], label=name)
        ax[1].plot(d[:, 0], d[:, 5], label=name)
    ax[0].plot(d[:, 0], d[:, 1], 'k--', label='Settpunkt')
    ax[0].legend()
    save_fig('02_tuning_comparison', fig)

    # Small steps avoid saturation and expose the linear damping directly.
    results['small_step'] = {}
    fig, ax = axes_pair('Lite sprang 28 → 28,2 °C: sammenligning uten metning')
    for name, kp, ti, tf in candidates:
        d = run(kp, ti, tf, target=28.2)
        save_data('small_'+name.replace(' ', '_'), d)
        results['small_step'][name] = step_metrics(d, target=28.2)
        ax[0].plot(d[:, 0], d[:, 2], label=name)
        ax[1].plot(d[:, 0], d[:, 5])
    ax[0].plot(d[:, 0], d[:, 1], 'k--', label='Settpunkt')
    ax[0].legend()
    ax[1].set_ylim(1.2, 2.6)
    save_fig('08_small_step', fig)

    # Bode plots of exactly the implemented sampled loops, filter included.
    w = np.geomspace(.001, math.pi/TS*.999, 6000)
    fig, ax = plt.subplots(2, 1, sharex=True, figsize=(10, 7))
    for name, kp, ti, tf in candidates:
        m = sampled_margins(kp, ti, TS, tf)
        ax[0].semilogx(w, 20*np.log10(abs(response(w, kp, ti, TS, tf))),
                       label=f'{name}: GM {m.gm_db:.1f} dB, PM {m.pm:.1f}°')
        ax[1].semilogx(w, np.degrees(phase(w, kp, ti, TS, tf)))
    ax[0].axhline(0, color='gray', ls='--')
    ax[1].axhline(-180, color='gray', ls='--')
    ax[0].set(title='Frekvensrespons: diskret PI, prosess og målefilter', ylabel='Sløyfeforsterkning [dB]')
    ax[1].set(xlabel='Vinkelfrekvens [rad/s]', ylabel='Fase [grader]', ylim=(-360, 0))
    ax[0].legend(fontsize=9)
    for a in ax:
        a.grid(which='both', alpha=.25)
        a.set_xlim(.001, 3)
    save_fig('03_bode', fig)
    results['continuous_unfiltered_reference'] = vars(margins(KP, TI))

    d = run(target=34, noise=.15, disturbance=True, duration=450)
    save_data('disturbance', d)
    fig, ax = axes_pair('Valgt PI: settpunktendring, målestøy og kaldere innløpsluft')
    ax[0].plot(d[:, 0], d[:, 3], color='.8', lw=.6, label='Rå måling')
    ax[0].plot(d[:, 0], d[:, 2], label='Sann simulert temperatur')
    ax[0].plot(d[:, 0], d[:, 4], label='Filtrert måling', alpha=.8)
    ax[0].plot(d[:, 0], d[:, 1], 'k--', label='Settpunkt')
    ax[0].axvline(150, color='purple', ls=':', label='Innløp: 21,5 → 18,5 °C')
    ax[1].plot(d[:, 0], d[:, 5])
    ax[0].legend(fontsize=9)
    results['disturbance'] = dict(final_error_C=float(np.mean(d[-200:, 1]-d[-200:, 2])),
        peak_deviation_C=float(max(abs(d[d[:, 0] >= 150, 2]-34))))
    save_fig('04_disturbance', fig)

    fig, ax = axes_pair('Målefilter: samme støykilde, konstant settpunkt 28 °C')
    results['noise'] = {}
    for tf in (0, TF):
        d = run(tf=tf, target=28, noise=.15, duration=150)
        save_data(f'noise_Tf_{tf:g}', d)
        v = d[d[:, 0] >= 50]
        name = f'Tf = {tf:g} s'
        results['noise'][name] = dict(control_std_V=float(np.std(v[:, 5])),
            true_temperature_std_C=float(np.std(v[:, 2])),
            measurement_error_std_C=float(np.std(v[:, 3]-v[:, 2])),
            filtered_error_std_C=float(np.std(v[:, 4]-v[:, 2])))
        ax[0].plot(d[:, 0], d[:, 4], label=name, lw=.8)
        ax[1].plot(d[:, 0], d[:, 5], label=name, lw=.8)
    ax[0].legend()
    ax[1].set_ylim(1.4, 2.3)
    save_fig('05_noise_filter', fig)

    d = run(saturation=True, duration=500)
    save_data('saturation', d)
    fig, ax = axes_pair('Metning og retur: uoppnåelig settpunkt 45 °C, deretter 28 °C')
    ax[0].plot(d[:, 0], d[:, 2], label='Temperatur')
    ax[0].plot(d[:, 0], d[:, 1], 'k--', label='Settpunkt')
    ax[0].axhline(39, color='gray', ls=':', label='Maksimal likevekt: 39 °C')
    ax[1].plot(d[:, 0], d[:, 5])
    ax[0].legend()
    results['saturation'] = dict(final_error_C=float(np.mean(d[-200:, 1]-d[-200:, 2])),
                                  min_u_V=float(min(d[:, 5])), max_u_V=float(max(d[:, 5])))
    save_fig('06_saturation', fig)

    m = sampled_margins(KP, TI, TS, TF)
    unstable_kp = 1.2*m.Kc
    fig, ax = axes_pair('Stabilitetsgrense: økt Kp med Ti = 18 s og Tf = 2 s')
    for kp in (KP, unstable_kp):
        d = run(kp=kp, target=28.2, duration=180)
        save_data(f'stability_Kp_{kp:.4f}', d)
        ax[0].plot(d[:, 0], d[:, 2], label=f'Kp = {kp:.3f}')
        ax[1].plot(d[:, 0], d[:, 5])
    ax[0].legend()
    results['stability_demonstration'] = dict(critical_Kp=m.Kc,
        unstable_Kp=unstable_kp, unstable_pole_radius=float(max(abs(poles(unstable_kp, TI, TS, TF)))))
    save_fig('07_stability_boundary', fig)

    # A declared parameter sensitivity study, not experimental validation.
    robustness = []
    for gain_factor in (.8, 1, 1.2):
        for tau_factor in (.8, 1, 1.2):
            for delay in (1, 2, 3):
                p = replace(P, K_h=P.K_h*gain_factor,
                            theta_t=P.theta_t*tau_factor, theta_d=delay)
                mm = sampled_margins(KP, TI, TS, TF, p)
                robustness.append(dict(gain_factor=gain_factor, tau_factor=tau_factor,
                    delay_s=delay, gm=mm.gm, pm_deg=mm.pm,
                    max_pole_radius=float(max(abs(poles(KP, TI, TS, TF, p))))))
    results['robustness'] = robustness
    with (OUT/'data'/'robustness.csv').open('w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=robustness[0].keys())
        writer.writeheader()
        writer.writerows(robustness)
    (OUT/'results.json').write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding='utf-8')
    write_report(results)
    print(json.dumps({k: v for k, v in results.items() if k != 'robustness'}, ensure_ascii=False, indent=2))
    print(f'Artifacts: {OUT}')


def write_report(r):
    def table_steps(key):
        lines = ['| Regulator | Oversving [%] | Innsvingning [s] | IAE [°C s] | Metning [%] |',
                 '|---|---:|---:|---:|---:|']
        for name, v in r[key].items():
            ts = v['settling_2pct_s']
            settled = 'Ikke innen 330 s' if ts is None else f'{ts:.1f}'
            lines.append(f"| {name} | {v['overshoot_pct']:.2f} | {settled} | {v['iae_Cs']:.2f} | {v['saturation_pct']:.2f} |")
        return '\n'.join(lines)

    lines = ['| Regulator | Kp [V/°C] | Ti [s] | Tf [s] | GM [dB] | PM [°] | Største polradius |',
             '|---|---:|---:|---:|---:|---:|---:|']
    zn = r['ziegler_nichols']
    for name, kp, ti, tf in [('ZN uten filter', zn['Kp'], zn['Ti_s'], 0),
                              ('ZN med filter', zn['Kp'], zn['Ti_s'], TF),
                              ('Valgt PI med filter', KP, TI, TF)]:
        m = r['margins'][name]
        lines.append(f"| {name} | {kp:.3f} | {ti:.2f} | {tf:g} | {m['gm_db']:.2f} | {m['pm']:.2f} | {m['max_pole_radius']:.6f} |")
    tokens = {'MARGINS': '\n'.join(lines), 'STEP': table_steps('step'),
              'SMALL_STEP': table_steps('small_step'),
              'KU': f"{zn['Ku']:.4f}", 'PU': f"{zn['Pu_s']:.4f}",
              'EULER_ERROR': f"{r['euler_max_error_C']:.5f}",
              'MIN_GM': f"{min(x['gm'] for x in r['robustness']):.2f}",
              'MIN_PM': f"{min(x['pm_deg'] for x in r['robustness']):.2f}",
              'MAX_RADIUS': f"{max(x['max_pole_radius'] for x in r['robustness']):.6f}"}
    report = (CODE/'docs'/'control_report_template.md').read_text(encoding='utf-8')
    for key, value in tokens.items():
        report = report.replace('{{'+key+'}}', value)
    if '{{' in report:
        raise ValueError('Unresolved report token')
    (OUT/'rapport.md').write_text(report, encoding='utf-8')


if __name__ == '__main__':
    main()
