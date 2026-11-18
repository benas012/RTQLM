import pytest
from quantum_light_engine.devices.gate_timeline import GateObservation, high_sample_ranges


def ranges(levels, *, times=None, uncertainty=0):
    times = times or [i*1_000_000 for i in range(len(levels))]
    return high_sample_ranges([GateObservation(t,t,h) for t,h in zip(times,levels)],
        first_sample_ns=0,sample_count=312500,sample_rate_hz=62500000,
        clock_uncertainty_ns=uncertainty)


def test_high_low_high_never_bridges_disabled_interval():
    assert ranges([True,True,False,True,True,True]) == [(0,62500),(187500,312500)]


def test_wait_for_confirming_observation_and_verified_clock():
    assert ranges([True]) == []
    assert ranges([True,True],uncertainty=None) == []
    assert ranges([False,False,False]) == []


def test_uncertainty_erodes_run_edges_without_inventing_internal_gaps():
    assert ranges([True]*6,uncertainty=100000) == [(6250,306250)]


def test_stale_poll_does_not_enable_samples_during_unobserved_interval():
    assert ranges([True]*4,times=[0,1000000,4000000,5000000]) == [(0,62500),(250000,312500)]


def test_failed_read_breaks_high_run():
    observations=[GateObservation(i*1000000,i*1000000,True,i!=2) for i in range(6)]
    assert high_sample_ranges(observations,first_sample_ns=0,sample_count=312500,
        sample_rate_hz=62500000,clock_uncertainty_ns=0) == [(0,62500),(187500,312500)]


def test_out_of_order_observations_are_rejected():
    with pytest.raises(ValueError): ranges([True,True],times=[1000000,0])
