from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class AcquisitionMetrics:
    sequence: int = 0
    packets_received: int = 0
    frames_published: int = 0
    malformed_packets: int = 0
    service_dropped_frames: int = 0
    packet_id_gaps: int = 0
    fpga_lost_ch1: int = 0
    fpga_lost_ch2: int = 0
    fpga_loss_deltas_ch1: int = 0
    fpga_loss_deltas_ch2: int = 0

    def observe_loss(self, channel: int, current: int) -> None:
        if channel == 1:
            previous = self.fpga_lost_ch1
            self.fpga_lost_ch1 = current
            if current >= previous:
                self.fpga_loss_deltas_ch1 += current - previous
        else:
            previous = self.fpga_lost_ch2
            self.fpga_lost_ch2 = current
            if current >= previous:
                self.fpga_loss_deltas_ch2 += current - previous
