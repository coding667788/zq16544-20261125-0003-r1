from __future__ import annotations

import argparse
import json
import sys
import unittest
from pathlib import Path

from .bound import build_bound_report
from .crosscheck import crosscheck_case
from .errors import FolioImpactError
from .mitigation import optimize
from .model import CaseModel


def _json_default(value):
    if isinstance(value, tuple | set):
        return list(value)
    if isinstance(value, Path):
        return str(value)
    return str(value)


def dumps(payload) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=False, default=_json_default)


def emit(payload, output: str | None) -> int:
    text = dumps(payload)
    if output:
        path = Path(output)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text + "\n", encoding="utf-8", newline="\n")
    else:
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except Exception:
            pass
        sys.stdout.write(text + "\n")
    return 0


def load_case(path: str) -> CaseModel:
    return CaseModel.load_file(path)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="folioimpact",
        description="Offline possible-world segment bounds and 16-action mitigation search.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_bound = sub.add_parser("bound", help="compute CASE min/max independent segment bounds")
    p_bound.add_argument("case", help="UTF-8 JSON CASE file")
    p_bound.add_argument("-o", "--output", help="write UTF-8 JSON here instead of stdout")
    p_bound.add_argument("--no-countermeasures", action="store_true", help="skip countermeasure evaluation")

    p_mit = sub.add_parser("mitigate", help="search up to 16 actions for minimum-cost plan")
    p_mit.add_argument("case", help="UTF-8 JSON CASE file")
    p_mit.add_argument("-o", "--output", help="write UTF-8 JSON here instead of stdout")

    p_check = sub.add_parser("crosscheck", help="compare component, whole-world and segment-wise references")
    p_check.add_argument("case", help="UTF-8 JSON CASE file")
    p_check.add_argument("-o", "--output")
    p_check.add_argument("--max-vars", type=int, default=12, help="maximum global vars for exhaustive reference")

    p_validate = sub.add_parser("validate", help="parse and validate a CASE file")
    p_validate.add_argument("case")

    p_all = sub.add_parser("all", help="emit bound, mitigation and crosscheck in one JSON document")
    p_all.add_argument("case")
    p_all.add_argument("-o", "--output")

    p_test = sub.add_parser("unittest", help="run the bundled unittest suite")
    p_test.add_argument("-v", "--verbose", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except Exception:
            pass
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "unittest":
        loader = unittest.TestLoader()
        suite = loader.discover(str(Path(__file__).resolve().parent.parent / "tests"))
        runner = unittest.TextTestRunner(verbosity=2 if args.verbose else 1)
        result = runner.run(suite)
        return 0 if result.wasSuccessful() else 1

    try:
        case = load_case(args.case)
        if args.command == "validate":
            payload = {
                "ok": True,
                "case_id": case.case_id,
                "groups": case.group_ids,
                "time_slices": list(case.time_slices),
                "ambiguity_components": [
                    {"id": c["id"], "variables": list(c["variables"]), "world_count": c["world_count"]}
                    for c in case.components
                ],
                "actions": len(case.actions),
            }
            return emit(payload, None)
        if args.command == "bound":
            payload = build_bound_report(case, include_countermeasures=not args.no_countermeasures)
            return emit(payload, args.output)
        if args.command == "mitigate":
            payload = optimize(case)
            return emit(payload, args.output)
        if args.command == "crosscheck":
            payload = crosscheck_case(case, max_variables=args.max_vars)
            return emit(payload, args.output)
        if args.command == "all":
            payload = {
                "ok": True,
                "bound": build_bound_report(case),
                "mitigate": optimize(case),
                "crosscheck": crosscheck_case(case),
            }
            return emit(payload, args.output)
        parser.error(f"unknown command {args.command}")
        return 2
    except FolioImpactError as exc:
        error = {"ok": False, "error": {"type": type(exc).__name__, "message": str(exc)}}
        if hasattr(exc, "path") and exc.path:
            error["error"]["path"] = exc.path
        if hasattr(exc, "details") and exc.details:
            error["error"]["details"] = exc.details
        sys.stderr.write(dumps(error) + "\n")
        return 2
    except (OSError, ValueError, KeyError, TypeError) as exc:
        error = {"ok": False, "error": {"type": type(exc).__name__, "message": str(exc)}}
        sys.stderr.write(dumps(error) + "\n")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
