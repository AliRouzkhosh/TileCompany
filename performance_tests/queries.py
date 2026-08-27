"""Small, test-side query capture helpers for performance baselines."""

import re
import os
import cProfile
import io
import pstats
from collections import Counter
from dataclasses import dataclass
from time import perf_counter

from django.db import connection
from django.test.utils import CaptureQueriesContext


_LITERAL = re.compile(r"'(?:''|[^'])*'|\b\d+(?:\.\d+)?\b")
_WHITESPACE = re.compile(r"\s+")


def normalize_sql(sql):
    """Collapse values/whitespace so repeated SQL shapes can be counted."""
    return _WHITESPACE.sub(" ", _LITERAL.sub("?", sql)).strip()


@dataclass(frozen=True)
class RouteMeasurement:
    surface: str
    status: int
    query_count: int
    elapsed_ms: float
    duplicate_shapes: tuple[tuple[str, int], ...]
    query_details: tuple[tuple[str, float], ...]


def capture_route(surface, request):
    """Execute a test-client request and emit reproducible diagnostic output."""
    profile = None
    if os.environ.get("PERFORMANCE_PROFILE_SURFACE", "") == surface:
        profile = cProfile.Profile()
    started = perf_counter()
    with CaptureQueriesContext(connection) as captured:
        if profile:
            profile.enable()
        try:
            response = request()
        finally:
            if profile:
                profile.disable()
    elapsed_ms = (perf_counter() - started) * 1000
    shapes = Counter(normalize_sql(item["sql"]) for item in captured.captured_queries)
    duplicates = tuple((shape, count) for shape, count in shapes.items() if count > 1)
    measurement = RouteMeasurement(
        surface=surface,
        status=response.status_code,
        query_count=len(captured),
        elapsed_ms=elapsed_ms,
        duplicate_shapes=duplicates,
        query_details=tuple(
            (item["sql"], float(item.get("time") or 0) * 1000)
            for item in captured.captured_queries
        ),
    )
    print(
        f"PERFORMANCE_MEASUREMENT surface={measurement.surface} "
        f"status={measurement.status} queries={measurement.query_count} "
        f"local_docker_ms={measurement.elapsed_ms:.1f} duplicates={len(duplicates)}"
    )
    if profile:
        output = io.StringIO()
        pstats.Stats(profile, stream=output).strip_dirs().sort_stats("cumulative").print_stats(40)
        print(f"PERFORMANCE_PROFILE surface={surface}\n{output.getvalue()}")
    if os.environ.get("PERFORMANCE_DUPLICATE_DETAILS", "true").lower() not in {"0", "false", "no"}:
        for shape, count in duplicates:
            print(f"PERFORMANCE_DUPLICATE count={count} sql={shape}")
    detail_surface = os.environ.get("PERFORMANCE_QUERY_SURFACE", "")
    if (
        os.environ.get("PERFORMANCE_QUERY_DETAILS", "false").lower() in {"1", "true", "yes"}
        and (not detail_surface or detail_surface == surface)
    ):
        print(
            f"PERFORMANCE_DB_TIMING surface={surface} "
            f"db_total_ms={sum(duration for _sql, duration in measurement.query_details):.3f}"
        )
        for index, (sql, duration_ms) in enumerate(measurement.query_details, start=1):
            print(
                f"PERFORMANCE_QUERY surface={surface} index={index} "
                f"db_ms={duration_ms:.3f} sql={normalize_sql(sql)}"
            )
    return response, measurement


def explain_analyze_buffers(queryset):
    """Manual-only support: callers must restrict this to read-only querysets."""
    return queryset.explain(analyze=True, buffers=True)
