from redpitaya_acquisition.observability.metrics import AcquisitionMetrics


def test_fpga_loss_increases_and_reset() -> None:
    metrics = AcquisitionMetrics()
    metrics.observe_loss(1, 4)
    metrics.observe_loss(1, 7)
    metrics.observe_loss(1, 1)  # reconnect reset
    assert metrics.fpga_lost_ch1 == 1
    assert metrics.fpga_loss_deltas_ch1 == 7
