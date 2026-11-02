package main

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
)

const denoManifest = `{
  "runtime": "deno",
  "upstream": {"kind": "github-release", "repo": "denoland/deno"},
  "os": ["macos-14", "ubuntu-latest"],
  "versions": [
    {"line": "2", "version": "2.9.7", "sha256": "21069d2f4dd65b6832e3f5c373c24a43a8d35cb3d68d3841e15d0582bed39ea8", "target": "//runtimes/deno:deno_2", "test": "//runtimes/deno/tests:hermetic_2_test", "artifact": "bazel-bin/runtimes/deno/deno_2"}
  ]
}
`

const moduleText = `node.version(
    major = "26",
    version = "26.12.1",
    sha256 = "aa",
)

node.version(
    major = "24",
    version = "24.22.2",
    sha256 = "bb",
    old_v8 = True,
)
`

func writeFile(t *testing.T, root, name, text string) {
	t.Helper()
	path := filepath.Join(root, name)
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(path, []byte(text), 0o644); err != nil {
		t.Fatal(err)
	}
}

func TestReadRuntimesWithoutManifests(t *testing.T) {
	root := t.TempDir()
	writeFile(t, root, "MODULE.bazel", moduleText)

	runtimes, err := readRuntimes(root)
	if err != nil {
		t.Fatalf("a missing runtimes directory is no error: %v", err)
	}
	if len(runtimes) != 1 || runtimes[0].Name != "node" || runtimes[0].Kind != kindNode {
		t.Fatalf("runtimes = %+v", runtimes)
	}
	pins := runtimes[0].Pins
	if len(pins) != 2 || pins[0] != (pin{Line: "26", Version: "26.12.1", SHA256: "aa"}) || pins[1].Line != "24" {
		t.Fatalf("pins = %+v", pins)
	}
}

func TestReadRuntimesReadsEachManifest(t *testing.T) {
	root := t.TempDir()
	writeFile(t, root, "MODULE.bazel", moduleText)
	writeFile(t, root, "runtimes/deno/manifest.json", denoManifest)

	runtimes, err := readRuntimes(root)
	if err != nil {
		t.Fatal(err)
	}
	if len(runtimes) != 2 {
		t.Fatalf("runtimes = %+v", runtimes)
	}
	deno := runtimes[1]
	if deno.Name != "deno" || deno.Kind != kindGitHubRelease || deno.Repo != "denoland/deno" || deno.File != "runtimes/deno/manifest.json" {
		t.Fatalf("deno = %+v", deno)
	}
	if deno.Pins[0].Version != "2.9.7" {
		t.Fatalf("pins = %+v", deno.Pins)
	}
}

// PR #30 adds runtimes/node/manifest.json. Node must stay one runtime, or each Node update opens twice.
func TestANodeManifestIsNotASecondRuntime(t *testing.T) {
	root := t.TempDir()
	writeFile(t, root, "MODULE.bazel", moduleText)
	writeFile(t, root, "runtimes/node/manifest.json", `{"runtime":"node","upstream":{"kind":"nodejs-dist"},"versions":[{"line":"26","version":"26.12.1","sha256":"aa"}]}`)

	runtimes, err := readRuntimes(root)
	if err != nil {
		t.Fatal(err)
	}
	if len(runtimes) != 1 || runtimes[0].File != "MODULE.bazel" {
		t.Fatalf("runtimes = %+v", runtimes)
	}
}

func TestReadsTheRealModuleFile(t *testing.T) {
	text, err := os.ReadFile("../../MODULE.bazel")
	if err != nil {
		t.Fatal(err)
	}
	pins, err := nodePins(string(text))
	if err != nil {
		t.Fatal(err)
	}
	if len(pins) < 2 {
		t.Fatalf("pins = %+v", pins)
	}
	for _, p := range pins {
		if !validVersion(p.Version) || !strings.HasPrefix(p.Version, p.Line+".") {
			t.Errorf("pin = %+v", p)
		}
	}
}

func TestReadRuntimesRefusesBadManifests(t *testing.T) {
	tests := []struct{ name, text, want string }{
		{"bad json", `{`, "unexpected"},
		{"unknown kind", `{"runtime":"x","upstream":{"kind":"ftp"},"versions":[{"line":"1","version":"1.0.0"}]}`, "kind"},
		{"github with no repo", `{"runtime":"x","upstream":{"kind":"github-release"},"versions":[{"line":"1","version":"1.0.0"}]}`, "repo"},
		{"hostile repo", `{"runtime":"x","upstream":{"kind":"github-release","repo":"a/b/../../c"},"versions":[{"line":"1","version":"1.0.0"}]}`, "repo"},
		{"hostile runtime", `{"runtime":"../x","upstream":{"kind":"pypi"},"versions":[{"line":"1","version":"1.0.0"}]}`, "runtime"},
		{"hostile line", `{"runtime":"x","upstream":{"kind":"pypi"},"versions":[{"line":"1/../2","version":"1.0.0"}]}`, "line"},
		{"bad version", `{"runtime":"x","upstream":{"kind":"pypi"},"versions":[{"line":"1","version":"1.0.0-rc1"}]}`, "version"},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			root := t.TempDir()
			writeFile(t, root, "MODULE.bazel", moduleText)
			writeFile(t, root, "runtimes/x/manifest.json", tt.text)
			_, err := readRuntimes(root)
			if err == nil || !strings.Contains(err.Error(), tt.want) {
				t.Fatalf("err = %v, want text %q", err, tt.want)
			}
		})
	}
}
