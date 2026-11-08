@echo off
rem Contract-suite stand-in for a provider CLI: records the call next to itself and fails.
echo %~n0 %*>>"%~dp0spawned.log"
echo contract suite: provider CLIs are faked, %~n0 was not expected to run 1>&2
exit /b 97
