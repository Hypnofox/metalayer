"""eBPF network monitor loader using BCC."""
from __future__ import annotations

import ctypes
import ipaddress
import logging
import time
from pathlib import Path
from typing import Any, Callable, Dict, Optional

logger = logging.getLogger(__name__)


class FlowEvent(ctypes.Structure):
    """Flow event layout emitted by network_monitor.bpf.c."""

    _fields_ = [
        ("ts_ns", ctypes.c_ulonglong),
        ("saddr", ctypes.c_uint32),
        ("daddr", ctypes.c_uint32),
        ("sport", ctypes.c_uint16),
        ("dport", ctypes.c_uint16),
    ]


class EBPFMonitor:
    """Loads and streams flow events from an eBPF kprobe."""

    def __init__(self, on_event: Callable[[Dict[str, Any]], None], program_path: Optional[Path] = None):
        self.on_event = on_event
        self.program_path = program_path or Path(__file__).parent / "network_monitor.bpf.c"
        self._running = False
        self._bpf = None

    def _to_ip(self, raw: int) -> str:
        return str(ipaddress.ip_address(raw.to_bytes(4, byteorder="little")))

    def _handle_flow_event(self, _cpu: int, data: int, _size: int):
        event = ctypes.cast(data, ctypes.POINTER(FlowEvent)).contents
        flow = {
            "source_ip": self._to_ip(event.saddr),
            "dest_ip": self._to_ip(event.daddr),
            "source_port": int(event.sport),
            "dest_port": int(event.dport),
            "protocol": "TCP",
            "timestamp": event.ts_ns / 1_000_000_000,
            "bytes": 0,
        }
        self.on_event(flow)

    def start_tracking(self):
        """Start the eBPF listener loop."""
        try:
            from bcc import BPF
        except ImportError as exc:
            raise RuntimeError(
                "BCC is required for eBPF mode. Install python3-bcc/bcc-tools and retry."
            ) from exc

        logger.info("Starting eBPF monitor with program: %s", self.program_path)
        self._bpf = BPF(src_file=str(self.program_path))
        self._bpf.attach_kprobe(event="tcp_v4_connect", fn_name="trace_tcp_v4_connect")
        self._bpf["flow_events"].open_perf_buffer(self._handle_flow_event)

        self._running = True
        try:
            while self._running:
                self._bpf.perf_buffer_poll(timeout=1000)
        except KeyboardInterrupt:
            logger.info("Received KeyboardInterrupt; stopping eBPF monitor")
        finally:
            self.stop_tracking()

    def stop_tracking(self):
        """Stop the eBPF listener loop."""
        if not self._running:
            return
        self._running = False
        # BCC detaches probes when BPF object is garbage collected.
        self._bpf = None
        time.sleep(0.01)
        logger.info("eBPF monitor stopped")
