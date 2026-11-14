import time

import ida_auto
import ida_ida
import ida_idaapi

from scripts.mapping.ida_tracer.progress.reporter import ProgressReporter

_REPORT_INTERVAL_SECONDS = 1.0
_PHASE = "auto_analysis"


def auto_wait_analysis(progress_reporter: ProgressReporter) -> None:
    min_ea = ida_ida.inf_get_min_ea()
    max_ea = ida_ida.inf_get_max_ea()

    start_time = time.monotonic()
    last_report_time = start_time

    processed_steps = 0
    consecutive_empty_steps = 0

    progress_reporter.start(
        phase=_PHASE,
        total=100,
        label="Waiting for IDA analysis",
    )

    while not ida_auto.auto_is_ok():
        processed = ida_auto.auto_make_step(0, ida_idaapi.BADADDR)

        if processed:
            processed_steps += 1
            consecutive_empty_steps = 0
        else:
            consecutive_empty_steps += 1

        now = time.monotonic()
        if now - last_report_time < _REPORT_INTERVAL_SECONDS:
            continue

        last_report_time = now
        elapsed = now - start_time

        display = ida_auto.auto_display_t()

        if ida_auto.get_auto_display(display):
            current_ea = display.ea
            current = f"0x{current_ea:X}"

            if min_ea <= current_ea < max_ea:
                progress = (current_ea - min_ea) / (max_ea - min_ea)
                progress_percent = max(0, min(99, int(progress * 100)))
            else:
                progress_percent = 99
        else:
            current = "unknown"
            progress_percent = 99 if processed_steps else 0

        auto_state = ida_auto.get_auto_state()
        if consecutive_empty_steps >= 100_000:
            raise RuntimeError(f"IDA auto-analysis stalled: state={ida_auto.get_auto_state()}")

        label = (
            f"Analyzing {current} "
            f"[{elapsed:.0f}s] "
            f"[steps={processed_steps}, "
            f"empty={consecutive_empty_steps}, "
            f"state={auto_state}]"
        )

        progress_reporter.update(
            phase=_PHASE,
            current=progress_percent,
            total=100,
            label=label,
        )

    progress_reporter.finish(
        phase=_PHASE,
        total=100,
        label="Analysis complete",
    )
