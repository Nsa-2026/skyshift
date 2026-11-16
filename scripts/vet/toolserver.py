"""A vet.py worker that stays loaded, so a tool call does not pay for starting Python and re-reading the tables.

Started by api_round.py (ToolPool), one per parallel tool call. Protocol on its standard input and output, one JSON
object per line:  {"argv": ["info", "<id>"]}  ->  {"out": "<what the tool printed>", "err": "<its error stream>", "code": 0}.
The first line it writes is {"ready": true}. Each worker runs one call at a time (matplotlib is not thread-safe).
"""
from __future__ import annotations

import contextlib
import io
import json
import os
import sys


def main():
    proto = os.fdopen(os.dup(1), "w")  # the real standard output carries the protocol only
    os.dup2(2, 1)                      # anything else that writes to descriptor 1 lands in the error stream
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import vet  # the slow part: pandas, astropy, matplotlib, once

    proto.write(json.dumps({"ready": True}) + "\n"); proto.flush()
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        out, err = io.StringIO(), io.StringIO()
        try:
            argv = json.loads(line)["argv"]
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                code = vet.run([str(x) for x in argv])
        except Exception as e:  # noqa: BLE001  (a broken request must not kill the worker)
            code = 2
            out.write(f"TOOL ERROR: {type(e).__name__}: {e}")
        proto.write(json.dumps({"out": out.getvalue(), "err": err.getvalue()[-600:], "code": code}) + "\n"); proto.flush()


if __name__ == "__main__":
    main()
