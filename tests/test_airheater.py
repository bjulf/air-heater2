"""Verification of the air heater simulator, controller and analysis.

Checks the model against values that can be derived by hand from the
continuous model, so a wrong discretisation shows up as a failed test rather
than a plausible-looking plot. The batch simulator is checked against the
scalar one sample by sample, and the stability analysis against the analytic
conditions it claims to solve.

Runs under pytest, or standalone with plain Python:

    python test_airheater.py
    pytest test_airheater.py
"""

# Make the airheater package importable when this script is run directly,
# from any working directory, without installing it first.
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import math

import numpy as np

from airheater import (
    AirHeaterParams,
    AirHeaterSim,
    LowPassFilter,
    PIController,
    SimulatedIO,
    run_control_loop,
)
from airheater import analysis as an
from airheater.simulation.batch import simulate_closed_loop, simulate_open_loop


def test_steady_state_matches_analytic():
    """With constant u the process must settle at K_h*u + T_env."""
    Ts = 0.1
    sim = AirHeaterSim(Ts, T_init=20.0)
    for u in (0.0, 1.0, 2.5, 5.0):
        sim.reset(T_init=20.0)
        for _ in range(int(600 / Ts)):   # ~27 time constants
            sim.step(u)
        expected = 3.5 * u + 21.5
        assert abs(sim.T_out - expected) < 1e-3, (u, sim.T_out, expected)


def test_time_constant():
    """63 % of the step must be reached one time constant after the delay.

    The step has to start from a steady state for that rule to hold, so the
    process starts at ambient - which is where u = 0 leaves it.
    """
    Ts = 0.01   # fine step so the Euler error does not dominate the check
    T_start = AirHeaterParams().T_env
    sim = AirHeaterSim(Ts, T_init=T_start)
    T_final = sim.steady_state(1.0)
    span = T_final - T_start
    T_63 = T_start + (1.0 - math.exp(-1.0)) * span

    target_t = sim.params.theta_d + sim.params.theta_t
    for _ in range(int(target_t / Ts)):
        sim.step(1.0)

    # Euler forward is slightly fast; 0.5 % of the step span is a fair bound.
    assert abs(sim.T_out - T_63) < 0.005 * span, (sim.T_out, T_63)


def test_transport_delay_holds_output_still():
    """Nothing may happen at the outlet until the delay has elapsed."""
    Ts = 0.1
    sim = AirHeaterSim(Ts, T_init=sim_env())
    n_delay = sim.delay_samples
    assert n_delay == 20   # theta_d = 2 s at Ts = 0.1 s

    # Starting at ambient with u = 0 before t = 0, the process is at rest, so
    # the first n_delay samples must not move at all.
    for _ in range(n_delay):
        sim.step(5.0)
    assert abs(sim.T_out - sim_env()) < 1e-12

    sim.step(5.0)
    assert sim.T_out > sim_env()   # the step has now arrived


def sim_env() -> float:
    return AirHeaterParams().T_env


def test_no_delay_responds_immediately():
    params = AirHeaterParams(theta_d=0.0)
    sim = AirHeaterSim(0.1, params=params, T_init=params.T_env)
    assert sim.delay_samples == 0
    sim.step(5.0)
    assert sim.T_out > params.T_env


def test_actuator_saturation_in_model():
    """The model must not respond to a control signal above the heater range."""
    Ts = 0.1
    a = AirHeaterSim(Ts, T_init=20.0)
    b = AirHeaterSim(Ts, T_init=20.0)
    for _ in range(int(300 / Ts)):
        a.step(5.0)
        b.step(50.0)      # far outside the 0-5 V range
    assert abs(a.T_out - b.T_out) < 1e-12


def test_sensor_scaling_roundtrip():
    p = AirHeaterParams()
    assert abs(p.degc_to_volt(20.0) - 1.0) < 1e-12
    assert abs(p.degc_to_volt(50.0) - 5.0) < 1e-12
    assert abs(p.degc_to_volt(35.0) - 3.0) < 1e-12
    for t in (20.0, 28.0, 35.0, 50.0):
        assert abs(p.volt_to_degc(p.degc_to_volt(t)) - t) < 1e-9
    # Out-of-range temperatures clamp to the sensor limits, as a real 1-5 V
    # transmitter does.
    assert p.degc_to_volt(10.0) == p.V_sensor_min
    assert p.degc_to_volt(80.0) == p.V_sensor_max


def test_required_u_inverts_steady_state():
    sim = AirHeaterSim(0.1)
    for target in (25.0, 28.0, 34.0):
        u = sim.required_u(target)
        assert abs(sim.steady_state(u) - target) < 1e-9


def test_rejects_too_slow_sampling():
    try:
        AirHeaterSim(10.0)
    except ValueError:
        return
    raise AssertionError("Ts = 10 s should be rejected for theta_t = 22 s")


def test_noise_is_measurement_only():
    """Noise must appear in measure(), never in the process state."""
    sim = AirHeaterSim(0.1, T_init=25.0, noise_std=0.5, seed=0)
    clean = sim.T_out
    readings = [sim.measure() for _ in range(200)]
    assert sim.T_out == clean                       # state untouched
    assert any(abs(r - clean) > 1e-9 for r in readings)
    mean = sum(readings) / len(readings)
    assert abs(mean - clean) < 0.2                  # zero-mean noise


def test_lowpass_filter_attenuates_noise():
    Ts, Tf = 0.1, 2.0
    lp = LowPassFilter(Tf, Ts)
    sim = AirHeaterSim(Ts, T_init=30.0, noise_std=0.5, seed=7)

    raw, filt = [], []
    for _ in range(2000):
        y = sim.measure()
        raw.append(y)
        filt.append(lp.update(y))

    def spread(xs):
        m = sum(xs) / len(xs)
        return math.sqrt(sum((x - m) ** 2 for x in xs) / len(xs))

    assert spread(filt) < 0.5 * spread(raw), (spread(raw), spread(filt))


def test_lowpass_passes_steady_value():
    lp = LowPassFilter(2.0, 0.1)
    for _ in range(1000):
        y = lp.update(28.0)
    assert abs(y - 28.0) < 1e-6


def test_pi_removes_steady_state_error():
    Ts = 0.1
    sim = AirHeaterSim(Ts, T_init=20.0)
    io = SimulatedIO(sim)
    pi = PIController(Kp=0.52, Ti=18.0, Ts=Ts)
    samples = run_control_loop(io, pi, 28.0, Ts, duration=600.0, realtime=False)

    final = samples[-1]
    assert abs(final.filtered - 28.0) < 0.05, final.filtered
    # And the control signal must sit where the steady state says it should.
    assert abs(final.u - sim.required_u(28.0)) < 0.02, final.u


def test_pi_anti_windup():
    """An unreachable setpoint must not leave the controller wound up.

    With a setpoint above what 5 V can deliver (39 degC), the output saturates.
    When the setpoint drops back to a reachable value, the controller must come
    off the limit promptly rather than sitting there while the integral term
    unwinds.
    """
    Ts = 0.1
    sim = AirHeaterSim(Ts, T_init=20.0)
    io = SimulatedIO(sim)
    pi = PIController(Kp=0.52, Ti=18.0, Ts=Ts)

    def setpoint(t):
        return 60.0 if t < 400.0 else 30.0   # 60 degC is unreachable

    samples = run_control_loop(io, pi, setpoint, Ts, duration=800.0, realtime=False)

    saturated_before = [s for s in samples if s.t < 400.0 and s.saturated]
    assert saturated_before, "expected the output to saturate on the unreachable setpoint"

    # Within 60 s of the setpoint dropping, the output must be off the limit.
    after = [s for s in samples if 400.0 <= s.t < 460.0]
    assert any(not s.saturated for s in after)
    assert abs(samples[-1].filtered - 30.0) < 0.1, samples[-1].filtered


def test_p_only_leaves_offset():
    """Sanity check on the integral term: P alone must leave an offset."""
    Ts = 0.1
    io = SimulatedIO(AirHeaterSim(Ts, T_init=20.0))
    p_only = PIController(Kp=0.52, Ti=None, Ts=Ts)
    samples = run_control_loop(io, p_only, 28.0, Ts, duration=600.0, realtime=False)
    assert abs(samples[-1].filtered - 28.0) > 0.5


def test_controller_sampling_time_must_match_loop():
    io = SimulatedIO(AirHeaterSim(0.1))
    pi = PIController(Kp=0.5, Ti=18.0, Ts=0.5)
    try:
        run_control_loop(io, pi, 28.0, 0.1, duration=1.0, realtime=False)
    except ValueError:
        return
    raise AssertionError("mismatched sampling times should be rejected")


def test_slide_form_differs_from_correct_form():
    """The slides' integral term is Ts times too strong; show the difference."""
    Ts = 0.1
    correct = PIController(Kp=0.52, Ti=18.0, Ts=Ts)
    slides = PIController(Kp=0.52, Ti=18.0, Ts=Ts, slide_form=True)
    for _ in range(10):
        correct.update(28.0, 20.0)
        slides.update(28.0, 20.0)
    # 1/Ts = 10x more integral action in the slide form.
    assert slides.u > correct.u


def test_sample_as_dict_is_json_serialisable():
    import json
    io = SimulatedIO(AirHeaterSim(0.1))
    pi = PIController(Kp=0.5, Ti=18.0, Ts=0.1)
    samples = run_control_loop(io, pi, 28.0, 0.1, duration=1.0, realtime=False)
    payload = json.dumps(samples[-1].as_dict())
    assert "setpoint" in payload and "u" in payload


# -- exact ZOH discretisation --------------------------------------------


def test_zoh_matches_analytic_solution():
    """The ZOH model must reproduce the continuous solution exactly."""
    p = AirHeaterParams(theta_d=0.0)
    for Ts in (0.1, 1.0, 5.0, 20.0):
        sim = AirHeaterSim(Ts, params=p, T_init=p.T_env, method="zoh")
        T_final = sim.steady_state(1.0)
        for k in range(1, 30):
            sim.step(1.0)
            exact = T_final + (p.T_env - T_final) * math.exp(-k * Ts / p.theta_t)
            assert abs(sim.T_out - exact) < 1e-9, (Ts, k, sim.T_out, exact)


def test_zoh_allows_coarse_sampling_euler_rejects():
    AirHeaterSim(10.0, method="zoh")          # must be accepted
    try:
        AirHeaterSim(10.0, method="euler")
    except ValueError:
        return
    raise AssertionError("euler should reject Ts = 10 s for theta_t = 22 s")


def test_euler_and_zoh_agree_at_fine_sampling():
    Ts = 0.01
    a = AirHeaterSim(Ts, T_init=20.0, method="euler")
    b = AirHeaterSim(Ts, T_init=20.0, method="zoh")
    for _ in range(int(200 / Ts)):
        a.step(1.0)
        b.step(1.0)
    assert abs(a.T_out - b.T_out) < 1e-3, (a.T_out, b.T_out)


def test_invalid_method_rejected():
    try:
        AirHeaterSim(0.1, method="tustin")
    except ValueError:
        return
    raise AssertionError("unknown method should be rejected")


# -- batch simulation ----------------------------------------------------


def test_batch_open_loop_matches_scalar_sim():
    """The convolution shortcut must give the same answer as iterating."""
    Ts = 0.1
    for method in ("euler", "zoh"):
        sim = AirHeaterSim(Ts, T_init=20.0, method=method)
        scalar = [sim.T_out]
        for _ in range(1000):
            scalar.append(sim.step(2.0))

        res = simulate_open_loop(2.0, Ts, duration=100.0, T_init=20.0, method=method)
        assert np.allclose(res.T_out, scalar, atol=1e-9), method


def test_batch_open_loop_accepts_input_sequence():
    Ts = 0.1
    u = np.concatenate([np.zeros(200), np.ones(300) * 3.0, np.ones(500)])
    sim = AirHeaterSim(Ts, T_init=20.0)
    scalar = [sim.T_out]
    for uk in u:
        scalar.append(sim.step(float(uk)))

    res = simulate_open_loop(u, Ts, T_init=20.0)
    assert np.allclose(res.T_out, scalar, atol=1e-9)


def test_batch_closed_loop_matches_scalar_loop():
    """Batch and real-time paths must produce identical trajectories."""
    Ts, duration = 0.1, 300.0
    Kp, Ti, Tf = 0.52, 18.0, 2.0

    io = SimulatedIO(AirHeaterSim(Ts, T_init=20.0))
    pi = PIController(Kp=Kp, Ti=Ti, Ts=Ts)
    lp = LowPassFilter(Tf, Ts)
    scalar = run_control_loop(io, pi, 28.0, Ts, duration=duration,
                              measurement_filter=lp, realtime=False)

    res = simulate_closed_loop(28.0, Ts, duration, Kp=Kp, Ti=Ti, Tf=Tf, T_init=20.0)

    u_scalar = np.array([s.u for s in scalar])
    y_scalar = np.array([s.measured for s in scalar])
    assert np.allclose(res.u[:len(u_scalar)], u_scalar, atol=1e-9)
    assert np.allclose(res.measured[:len(y_scalar)], y_scalar, atol=1e-9)


def test_batch_sweep_matches_individual_runs():
    """Simulating M tunings together must equal M separate runs."""
    Ts, duration = 0.1, 200.0
    kps = np.array([0.2, 0.52, 1.5])
    together = simulate_closed_loop(28.0, Ts, duration, Kp=kps, Ti=18.0, T_init=20.0)
    assert together.T_out.shape == (int(duration / Ts) + 1, 3)
    for i, kp in enumerate(kps):
        alone = simulate_closed_loop(28.0, Ts, duration, Kp=float(kp), Ti=18.0,
                                     T_init=20.0)
        assert np.allclose(together.T_out[:, i], alone.T_out, atol=1e-9), kp


def test_batch_closed_loop_removes_steady_state_error():
    res = simulate_closed_loop(28.0, 0.1, 600.0, Kp=0.52, Ti=18.0, T_init=20.0)
    assert abs(res.T_out[-1] - 28.0) < 0.05


def test_iae_prefers_the_better_tuning():
    """A hopelessly slow tuning must score worse than a sensible one."""
    res = simulate_closed_loop(28.0, 0.1, 300.0, Kp=np.array([0.52, 0.02]),
                               Ti=18.0, T_init=20.0)
    iae = res.iae()
    assert iae[0] < iae[1], iae


# -- frequency response and stability ------------------------------------


def test_margins_reproduce_course_values():
    """The course material reports GM = 9.41 and Kc = 4.89 for this tuning."""
    m = an.margins(0.52, 18.0)
    assert abs(m.gm - 9.41) < 0.05, m.gm
    assert abs(m.Kc - 4.89) < 0.05, m.Kc
    assert m.stable


def test_crossover_definitions_hold():
    """|L| = 1 at wc, and the phase is exactly -180 deg at w180."""
    m = an.margins(0.52, 18.0)
    mag_at_wc = abs(an.loop_frequency_response(np.array([m.wc]), 0.52, 18.0)[0])
    assert abs(mag_at_wc - 1.0) < 1e-6, mag_at_wc
    phase_at_w180 = float(an.loop_phase(np.array([m.w180]), 0.52, 18.0)[0])
    assert abs(phase_at_w180 + math.pi) < 1e-6, phase_at_w180


def test_critical_gain_is_the_stability_boundary():
    """Simulating either side of Kc must agree with the analysis.

    Below Kc the oscillation dies out; above it, it persists. Note that it
    persists rather than growing without bound: the heater saturates at 0-5 V,
    so a linearly unstable loop settles into a bounded limit cycle instead of
    diverging. That is the real behaviour, and it is why "unstable" on a real
    process looks like a steady hunting oscillation rather than an explosion.
    """
    Ti = 18.0
    Kc = an.critical_gain(Ti)
    Ts, duration = 0.05, 2000.0

    below = simulate_closed_loop(28.0, Ts, duration, Kp=0.8 * Kc, Ti=Ti, T_init=20.0)
    above = simulate_closed_loop(28.0, Ts, duration, Kp=1.3 * Kc, Ti=Ti, T_init=20.0)

    def spread(res, lo, hi):
        n = len(res.t)
        window = res.T_out[int(lo * n):int(hi * n)] if hi < 1.0 \
            else res.T_out[int(lo * n):]
        return float(window.max() - window.min())

    # Below Kc: settled to a flat line.
    assert spread(below, 0.75, 1.0) < 0.05, spread(below, 0.75, 1.0)

    # Above Kc: still swinging, and not decaying - the amplitude in the last
    # quarter matches the amplitude in the third quarter.
    tail = spread(above, 0.75, 1.0)
    mid = spread(above, 0.5, 0.75)
    assert tail > 0.3, tail
    assert tail > 0.9 * mid, (mid, tail)


def test_marginal_gain_sustains_oscillation():
    """At Kp = Kc the oscillation neither grows nor dies: marginally stable."""
    Ti = 18.0
    Kc = an.critical_gain(Ti)
    res = simulate_closed_loop(28.0, 0.05, 2000.0, Kp=Kc, Ti=Ti, T_init=20.0)
    n = len(res.t)
    mid = res.T_out[n // 2:3 * n // 4]
    tail = res.T_out[3 * n // 4:]
    spread_mid = float(mid.max() - mid.min())
    spread_tail = float(tail.max() - tail.min())
    assert spread_tail > 0.3, spread_tail
    assert abs(spread_tail - spread_mid) < 0.05 * spread_mid, (spread_mid, spread_tail)


def test_critical_gain_independent_of_evaluation_point():
    """Kc must not depend on which Kp the margins were computed at."""
    values = [an.margins(kp, 18.0).Kc for kp in (0.1, 0.52, 2.0, 4.0)]
    assert max(values) - min(values) < 1e-6, values


def test_margins_detect_an_unstable_loop():
    Kc = an.critical_gain(18.0)
    assert not an.margins(1.5 * Kc, 18.0).stable


def test_ultimate_point_satisfies_its_definition():
    """At wu the process phase is -180 deg and Ku*|Hp| is exactly 1."""
    Ku, Pu = an.ultimate_point()
    wu = 2.0 * math.pi / Pu
    p = AirHeaterParams()
    phase = -math.atan(wu * p.theta_t) - wu * p.theta_d
    assert abs(phase + math.pi) < 1e-6, phase
    mag = abs(an.process_frequency_response(np.array([wu]))[0])
    assert abs(Ku * mag - 1.0) < 1e-9


def test_ziegler_nichols_follows_the_table():
    Ku, Pu = an.ultimate_point()
    assert abs(an.ziegler_nichols(controller="P").Kp - 0.5 * Ku) < 1e-9
    pi = an.ziegler_nichols(controller="PI")
    assert abs(pi.Kp - 0.45 * Ku) < 1e-9
    assert abs(pi.Ti - Pu / 1.2) < 1e-9
    pid = an.ziegler_nichols(controller="PID")
    assert abs(pid.Kp - 0.6 * Ku) < 1e-9
    assert abs(pid.Td - Pu / 8.0) < 1e-9


def test_tuning_rules_give_stable_loops():
    for t in (an.ziegler_nichols(controller="PI"),
              an.skogestad_simc(),
              an.skogestad_simc(Tc=10.0)):
        assert an.margins(t.Kp, t.Ti).stable, t


def test_skogestad_matches_its_formula():
    p = AirHeaterParams()
    t = an.skogestad_simc(Tc=10.0)
    assert abs(t.Kp - p.theta_t / (p.K_h * (10.0 + p.theta_d))) < 1e-9
    assert abs(t.Ti - min(p.theta_t, 4.0 * (10.0 + p.theta_d))) < 1e-9


def test_ziegler_nichols_is_more_aggressive_than_simc():
    zn = an.ziegler_nichols(controller="PI")
    simc = an.skogestad_simc(Tc=10.0)
    assert zn.Kp > simc.Kp
    assert an.margins(zn.Kp, zn.Ti).pm < an.margins(simc.Kp, simc.Ti).pm


# -- Pade approximation ---------------------------------------------------


def test_pade_order_one_is_the_known_form():
    """Pade(1,1) of exp(-theta*s) is (1 - theta*s/2)/(1 + theta*s/2)."""
    num, den = an.pade(2.0, 1)
    assert np.allclose(num, [-1.0, 1.0]), num
    assert np.allclose(den, [1.0, 1.0]), den


def test_pade_approximates_the_delay_at_low_frequency():
    """Pade must match exp(-j*w*theta) well below its breakdown frequency."""
    theta, order = 2.0, 5
    num, den = an.pade(theta, order)
    w = np.logspace(-3, 0, 200)
    jw = 1j * w
    approx = np.polyval(num, jw) / np.polyval(den, jw)
    exact = np.exp(-jw * theta)
    assert np.max(np.abs(approx - exact)) < 1e-3


def test_pade_zero_delay_is_unity():
    num, den = an.pade(0.0, 5)
    assert np.allclose(num, [1.0]) and np.allclose(den, [1.0])


def test_closed_loop_poles_agree_with_margins():
    """The pole map and the margin analysis must agree on stability."""
    Ti = 18.0
    Kc = an.critical_gain(Ti)
    stable = an.closed_loop_poles(0.5 * Kc, Ti)
    unstable = an.closed_loop_poles(1.5 * Kc, Ti)
    assert stable.real.max() < 0.0, stable.real.max()
    assert unstable.real.max() > 0.0, unstable.real.max()


def _run_standalone():
    tests = [(n, o) for n, o in sorted(globals().items())
             if n.startswith("test_") and callable(o)]
    failures = 0
    for name, fn in tests:
        try:
            fn()
        except Exception as exc:
            failures += 1
            print("FAIL  {0}: {1}".format(name, exc))
        else:
            print("ok    {0}".format(name))
    print("\n{0} passed, {1} failed, {2} total".format(
        len(tests) - failures, failures, len(tests)))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(_run_standalone())
