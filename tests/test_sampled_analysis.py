"""Independent checks of the sampled stability calculation."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import numpy as np

from airheater import AirHeaterParams, AirHeaterSim, LowPassFilter, PIController
from airheater.analysis import margins
from airheater.analysis.sampled import components, poles, sampled_margins


def test_plant_response_matches_time_domain():
    """An actual sine experiment must match the discrete transfer function."""
    dt, w = .1, .3
    sim = AirHeaterSim(dt, T_init=28)
    u0 = sim.required_u(28)
    sim.reset(T_init=28, u_init=u0)
    t = np.arange(12000)*dt
    y = []
    for tk in t:
        y.append(sim.T_out-28)
        sim.step(u0+.1*np.cos(w*tk))
    mask = t > 600
    basis = np.c_[np.cos(w*t[mask]), np.sin(w*t[mask]), np.ones(sum(mask))]
    coef = np.linalg.lstsq(basis, np.array(y)[mask], rcond=None)[0]
    empirical = (coef[0]-1j*coef[1])/.1
    analytical = components(w, .52, 18)[0]
    assert abs(empirical-analytical) < 1e-8


def test_pi_and_filter_match_time_domain():
    dt, w = .1, .3
    pi = PIController(.52, 18, dt, u_init=2.5)
    filt = LowPassFilter(2, dt, y_init=0)
    t = np.arange(12000)*dt
    u, yf = [], []
    for tk in t:
        y = .1*np.cos(w*tk)
        u.append(pi.update(y, 0))
        yf.append(filt.update(y))
    mask = t > 600
    basis = np.c_[np.cos(w*t[mask]), np.sin(w*t[mask]), np.ones(sum(mask))]
    _, c, f = components(w, .52, 18)
    for signal, expected in [(u, c), (yf, f)]:
        coef = np.linalg.lstsq(basis, np.array(signal)[mask], rcond=None)[0]
        empirical = (coef[0]-1j*coef[1])/.1
        assert abs(empirical-expected) < 1e-8


def test_filter_changes_stability_boundary():
    unfiltered = sampled_margins(.52, 18, Tf=0)
    filtered = sampled_margins(.52, 18, Tf=2)
    assert filtered.pm < unfiltered.pm
    # Independent characteristic roots verify the Bode boundary.
    kc = filtered.Kc
    assert max(abs(poles(.99*kc, 18))) < 1
    assert abs(max(abs(poles(kc, 18)))-1) < 1e-8
    assert max(abs(poles(1.01*kc, 18))) > 1


def test_sampled_converges_to_continuous_without_filter():
    continuous = margins(.52, 18)
    sampled = sampled_margins(.52, 18, Ts=.001, Tf=0)
    assert abs(continuous.pm-sampled.pm) < .01
    assert abs(continuous.gm-sampled.gm) < .01


if __name__ == '__main__':
    tests = [v for k, v in list(globals().items()) if k.startswith('test_')]
    for test in tests:
        test()
        print('ok', test.__name__)
    print(f'{len(tests)} passed')
