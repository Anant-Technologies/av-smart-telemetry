"""fitvid CLI — inspect and compile."""

from __future__ import annotations

import json
import logging
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.logging import RichHandler

from fitvid.compile import CompileConfig, compile_video
from fitvid.events import EventEmitter
from fitvid.fit_parser import parse_fit
from fitvid.inspect import format_inventory, inspect_payload
from fitvid.media import group_media_by_device, probe_device_identity
from fitvid.selectors.manual import ManualMarker
from fitvid.timeutil import parse_datetime

app = typer.Typer(
    name="fitvid",
    help="FIT-driven video compiler",
    no_args_is_help=True,
)
console = Console(stderr=True)


def _setup_logging(verbose: bool, *, json_events: bool = False) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    # When emitting NDJSON on stdout, keep logs on stderr only
    handlers: list[logging.Handler]
    if json_events:
        handlers = [logging.StreamHandler(sys.stderr)]
    else:
        handlers = [RichHandler(console=console, rich_tracebacks=True)]
    logging.basicConfig(level=level, format="%(message)s", handlers=handlers, force=True)


def _parse_dt(value: str | None) -> datetime | None:
    return parse_datetime(value, assume_utc_if_naive=False)


@app.command("inspect")
def inspect_cmd(
    fit: Path = typer.Argument(..., exists=True, help="Path to .fit activity file"),
    session: int = typer.Option(0, help="Session index for multi-session files"),
    format: str = typer.Option(
        "text", "--format", help="Output format: text | json"
    ),
    json_out: Optional[Path] = typer.Option(
        None, "--json", help="Also dump full Activity JSON to this path"
    ),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """List every populated record field with min/max and units."""
    _setup_logging(verbose)
    activities = parse_fit(fit)
    if not activities:
        if format == "json":
            print(json.dumps({"error": "No activities found in FIT file"}))
        else:
            console.print("[red]No activities found in FIT file[/red]")
        raise typer.Exit(1)
    if session >= len(activities):
        msg = (
            f"Session {session} out of range "
            f"(file has {len(activities)} session(s))"
        )
        if format == "json":
            print(json.dumps({"error": msg}))
        else:
            console.print(f"[red]{msg}[/red]")
        raise typer.Exit(1)

    activity = activities[session]
    if format == "json":
        payload = inspect_payload(activity, session_index=session)
        payload["session_count"] = len(activities)
        print(json.dumps(payload, indent=2))
    else:
        if len(activities) > 1:
            console.print(
                f"[yellow]Multi-session file: {len(activities)} sessions "
                f"(showing session {session})[/yellow]\n"
            )
        console.print(format_inventory(activity))

    if json_out:
        data = activity.to_dict()
        if len(activity.records) > 5000:
            data["records"] = data["records"][:100]
            data["records_truncated"] = True
        json_out.write_text(json.dumps(data, indent=2, default=str))
        if format != "json":
            console.print(f"\nWrote Activity JSON → {json_out}")


@app.command("compile")
def compile_cmd(
    fit: Path = typer.Option(..., "--fit", exists=True, help="Activity .fit file"),
    video: list[Path] = typer.Option(
        ..., "--video", exists=True, help="Video file(s); repeatable"
    ),
    audio: list[Path] = typer.Option(
        [], "--audio", exists=True, help="Standalone audio file(s); repeatable"
    ),
    select: str = typer.Option(
        "laps",
        "--select",
        help="laps | videos | manual | path to select YAML",
    ),
    overlay: Optional[Path] = typer.Option(
        None, "--overlay", exists=True, help="Overlay YAML config"
    ),
    out: Path = typer.Option(Path("highlight.mp4"), "--out", "-o"),
    pad_before: float = typer.Option(0.0, "--pad-before"),
    pad_after: float = typer.Option(0.0, "--pad-after"),
    min_duration: float = typer.Option(0.0, "--min-duration"),
    video_start: list[str] = typer.Option(
        [], "--video-start", help="ISO start time per video (host-local; repeatable)"
    ),
    audio_start: list[str] = typer.Option(
        [], "--audio-start", help="ISO start time per audio (host-local; repeatable)"
    ),
    video_duration: list[float] = typer.Option(
        [], "--video-duration", help="Duration seconds per video (repeatable)"
    ),
    audio_duration: list[float] = typer.Option(
        [], "--audio-duration", help="Duration seconds per audio (repeatable)"
    ),
    marker: list[str] = typer.Option(
        [],
        "--marker",
        help="Manual marker: offset_seconds[:label[:before:after]] or ISO time",
    ),
    sync: str = typer.Option(
        "auto", "--sync", help="auto | none (manual offsets via --video-start)"
    ),
    session: int = typer.Option(0, "--session"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Print ranges only"),
    multi_camera: bool = typer.Option(False, "--multi-camera"),
    json_events: bool = typer.Option(
        False,
        "--json-events",
        help="Emit NDJSON progress events on stdout (for native UIs)",
    ),
    sync_config: Optional[Path] = typer.Option(
        None,
        "--sync-config",
        exists=True,
        help="YAML with FIT generator + camera/audio device clock sync",
    ),
    unit_system: Optional[str] = typer.Option(
        None,
        "--unit-system",
        help="Overlay units: fps (US customary, default in YAML) | metric. "
        "Overrides unit_system in --overlay when set.",
    ),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Select, cut, mux, overlay, and concatenate highlight video."""
    _setup_logging(verbose, json_events=json_events)
    events = EventEmitter(enabled=json_events)

    markers: list[ManualMarker] = []
    for raw in marker:
        parts = raw.split(":")
        if "T" in raw or (
            "-" in parts[0] and not parts[0].replace(".", "").isdigit()
        ):
            try:
                dt = _parse_dt(raw)
                markers.append(ManualMarker(dt, "manual"))  # type: ignore[arg-type]
                continue
            except ValueError:
                pass
        try:
            offset = float(parts[0])
            label = parts[1] if len(parts) > 1 else "manual"
            before = float(parts[2]) if len(parts) > 2 else 5.0
            after = float(parts[3]) if len(parts) > 3 else 5.0
            markers.append(ManualMarker(offset, label, before, after))
        except ValueError as exc:
            msg = f"Bad --marker '{raw}': {exc}"
            if json_events:
                events.emit("error", message=msg)
            else:
                console.print(f"[red]{msg}[/red]")
            raise typer.Exit(1)

    if select == "manual" and not markers:
        msg = "--select manual requires at least one --marker"
        if json_events:
            events.emit("error", message=msg)
        else:
            console.print(f"[red]{msg}[/red]")
        raise typer.Exit(1)

    v_starts = [_parse_dt(s) for s in video_start]
    a_starts = [_parse_dt(s) for s in audio_start]
    v_durs: list[float | None] = [float(d) for d in video_duration]
    a_durs: list[float | None] = [float(d) for d in audio_duration]

    cfg = CompileConfig(
        fit_path=fit,
        video_paths=list(video),
        audio_paths=list(audio),
        select=select,
        overlay_path=overlay,
        out_path=out,
        pad_before=pad_before,
        pad_after=pad_after,
        min_duration=min_duration,
        video_starts=v_starts,
        audio_starts=a_starts,
        video_durations=v_durs,
        audio_durations=a_durs,
        manual_markers=markers,
        multi_camera=multi_camera,
        sync_mode=sync,
        sync_config_path=sync_config,
        dry_run=dry_run,
        activity_index=session,
        events=events if json_events else None,
        unit_system=unit_system,
    )

    try:
        result = compile_video(cfg)
    except Exception as exc:
        logging.getLogger(__name__).exception("Compile failed")
        if json_events:
            events.emit("error", message=str(exc))
        else:
            console.print(f"[red]Compile failed: {exc}[/red]")
        raise typer.Exit(1)

    if json_events:
        return

    if result.assumed_zero_drift:
        console.print(
            "[yellow]Note: assuming zero clock drift "
            "(only one sync event / none). Flagged in manifest.[/yellow]"
        )

    console.print(f"Selected {len(result.ranges)} range(s):")
    for start, end, reason in result.ranges:
        dur = (end - start).total_seconds()
        console.print(
            f"  {start.isoformat()} → {end.isoformat()} "
            f"({dur:.1f}s)  [{reason}]"
        )

    if result.gaps:
        console.print("[yellow]Gaps:[/yellow]")
        for g in result.gaps:
            console.print(f"  {g}")

    if dry_run:
        console.print(
            f"\n[cyan]Dry run[/cyan]: {len(result.clips)} clip(s) would be cut. "
            "No media written."
        )
        return

    console.print(f"\n[green]Wrote[/green] {result.out_path}")
    if result.manifest_path:
        console.print(f"[green]Manifest[/green] {result.manifest_path}")


@app.command("probe-media")
def probe_media_cmd(
    paths: list[Path] = typer.Argument(..., exists=True),
    group: bool = typer.Option(True, "--group/--no-group", help="Group by device id"),
) -> None:
    """Probe media files and print device identity JSON (for native UIs)."""
    if group:
        groups = group_media_by_device(list(paths))
        print(json.dumps({"devices": list(groups.values())}, indent=2, default=str))
    else:
        print(
            json.dumps(
                {"files": [probe_device_identity(p) for p in paths]},
                indent=2,
                default=str,
            )
        )


def main() -> None:
    app()


if __name__ == "__main__":
    main()
