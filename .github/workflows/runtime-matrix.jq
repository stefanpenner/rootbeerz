# Input: the slurped runtimes/*/manifest.json. Output: a GitHub Actions matrix.
# --arg scope all gives every version. --arg scope pr gives the oldest and the newest of each runtime.
# tests/runtimes_test.py runs this filter.

def number: [scan("[0-9]+") | tonumber];

def hosted:
  if test("^(ubuntu|macos)-(latest|[0-9][a-z0-9.-]*)$") then .
  else error("not a GitHub-hosted runner: \(.)") end;

def picked:
  if $scope == "pr"
  then [min_by(.version | number), max_by(.version | number)] | unique_by(.line)
  else . end;

{include: [
  .[] as $manifest
  | ($manifest.versions | picked)[] as $version
  | ($manifest.os[] | hosted) as $os
  | $version
  | {runtime: $manifest.runtime, line, version, target, test, system_ca_test, artifact, os: $os}
]}
