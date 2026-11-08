#!/bin/sh
# Contract-suite stand-in for a provider CLI: records the call next to itself and fails.
echo "$(basename "$0") $*" >> "$(dirname "$0")/spawned.log"
echo "contract suite: provider CLIs are faked, $(basename "$0") was not expected to run" >&2
exit 97
