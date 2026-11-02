package main

import (
	"encoding/json"
	"errors"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

const newSum = "b57d0281c1647effc735f242ba52bc409747fc6ed6ad4946e76f062c67e113eb"

func TestEditManifestBumpsOneLine(t *testing.T) {
	const text = `{
  "runtime": "jdk",
  "versions": [
    {"line": "25", "version": "25.0.2+1", "sha256": "aa", "target": "//runtimes/jdk:t_25"},
    {
      "line": "21",
      "version": "21.0.12+9",
      "sha256": "bb",
      "target": "//runtimes/jdk:t_21"
    }
  ]
}
`
	got, err := editManifest(text, update{Line: "21", Old: "21.0.12+9", New: "21.0.13+9", SHA256: newSum})
	if err != nil {
		t.Fatal(err)
	}
	want := strings.NewReplacer(`"21.0.12+9"`, `"21.0.13+9"`, `"bb"`, `"`+newSum+`"`).Replace(text)
	if got != want {
		t.Fatalf("got:\n%s\nwant:\n%s", got, want)
	}
}

func TestEditManifestKeepsFieldOrderAndSpacing(t *testing.T) {
	const text = `{"versions":[{"version":"1.0.0","sha256":"a","line":"1"}]}`
	got, err := editManifest(text, update{Line: "1", New: "1.0.1", SHA256: newSum})
	if err != nil {
		t.Fatal(err)
	}
	want := `{"versions":[{"version":"1.0.1","sha256":"` + newSum + `","line":"1"}]}`
	if got != want {
		t.Fatalf("got:\n%s\nwant:\n%s", got, want)
	}
}

func TestEditManifestAddsANewLineFromTheTopEntry(t *testing.T) {
	got, err := editManifest(denoManifest, update{Line: "3", New: "3.0.0", NewLine: true, SHA256: newSum})
	if err != nil {
		t.Fatal(err)
	}
	var parsed struct {
		Versions []map[string]string `json:"versions"`
	}
	if err := json.Unmarshal([]byte(got), &parsed); err != nil {
		t.Fatalf("%v\n%s", err, got)
	}
	if len(parsed.Versions) != 2 {
		t.Fatalf("got:\n%s", got)
	}
	added, kept := parsed.Versions[0], parsed.Versions[1]
	want := map[string]string{
		"line": "3", "version": "3.0.0", "sha256": newSum,
		"target": "//runtimes/deno:deno_3", "test": "//runtimes/deno/tests:hermetic_3_test", "artifact": "bazel-bin/runtimes/deno/deno_3",
	}
	for key, value := range want {
		if added[key] != value {
			t.Errorf("%s = %q, want %q", key, added[key], value)
		}
	}
	if kept["version"] != "2.9.7" || kept["target"] != "//runtimes/deno:deno_2" {
		t.Errorf("kept = %v", kept)
	}
}

func TestEditManifestRefusesWhatItCannotEdit(t *testing.T) {
	tests := []struct {
		name, text string
		change     update
	}{
		{"no such line", denoManifest, update{Line: "9", New: "9.0.0", SHA256: newSum}},
		{"new line with no entry", `{"versions":[]}`, update{Line: "1", New: "1.0.0", NewLine: true, SHA256: newSum}},
		{"not json", `{`, update{Line: "1", New: "1.0.1", SHA256: newSum}},
		{"a nested object hides the entry end", `{"versions":[{"line":"1","x":{"a":"b"},"version":"1.0.0","sha256":"a"}]}`, update{Line: "1", New: "1.0.1", SHA256: newSum}},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			if got, err := editManifest(tt.text, tt.change); err == nil {
				t.Fatalf("want an error, got:\n%s", got)
			}
		})
	}
}

// fakeNodeTool plays add_node_version.py: it edits files, and it can refuse without --no-patches.
type fakeNodeTool struct {
	root        string
	failPatches bool
	calls       [][]string
}

func (f *fakeNodeTool) run(_ string, args ...string) error {
	f.calls = append(f.calls, args)
	withPatches := !strings.Contains(strings.Join(args, " "), "--no-patches")
	if withPatches && f.failPatches {
		os.WriteFile(filepath.Join(f.root, "half-edit.txt"), []byte("left by a failed run"), 0o644)
		return errors.New("no patches")
	}
	os.WriteFile(filepath.Join(f.root, "MODULE.bazel"), []byte("new module"), 0o644)
	os.WriteFile(filepath.Join(f.root, "STATUS.md"), []byte("new status"), 0o644)
	if strings.Contains(strings.Join(args, " "), "27.0.0") {
		os.WriteFile(filepath.Join(f.root, ".github/workflows/ci.yml"), []byte("new matrix"), 0o644)
	}
	if withPatches {
		os.Remove(filepath.Join(f.root, "patches/26/0001-old.patch"))
		os.WriteFile(filepath.Join(f.root, "patches/26/0001-new.patch"), []byte("new patch"), 0o644)
	}
	return nil
}

func nodeTree(t *testing.T) string {
	t.Helper()
	root := t.TempDir()
	for _, name := range []string{"MODULE.bazel", "STATUS.md", ".github/workflows/ci.yml", "patches/26/0001-old.patch", "tools/run.sh"} {
		writeFile(t, root, name, "old "+name)
	}
	if err := os.Chmod(filepath.Join(root, "tools/run.sh"), 0o755); err != nil {
		t.Fatal(err)
	}
	writeFile(t, root, ".git/config", "not part of the tree")
	return root
}

func changePaths(changes []fileChange) string {
	var paths []string
	for _, change := range changes {
		state := "M"
		if change.Deleted {
			state = "D"
		}
		paths = append(paths, state+" "+change.Path)
	}
	return strings.Join(paths, ", ")
}

func TestApplyNodeBump(t *testing.T) {
	root := nodeTree(t)
	tool := &fakeNodeTool{root: root}
	node := runtime{Name: "node", Kind: kindNode, File: "MODULE.bazel"}
	applied, err := apply(root, tool.run, node, update{Line: "26", Old: "26.12.1", New: "26.13.0", Shasums: "sums"})
	if err != nil {
		t.Fatal(err)
	}
	const want = "M MODULE.bazel, M STATUS.md, A patches/26/0001-new.patch, D patches/26/0001-old.patch"
	if got := strings.Replace(changePaths(applied.Changes), "M patches/26/0001-new", "A patches/26/0001-new", 1); got != want {
		t.Fatalf("changes = %s", got)
	}
	if len(applied.Flags) != 0 {
		t.Fatalf("flags = %v", applied.Flags)
	}
	if len(tool.calls) != 1 || !strings.Contains(strings.Join(tool.calls[0], " "), "tools/add_node_version.py 26.13.0 --shasums ") {
		t.Fatalf("calls = %v", tool.calls)
	}
	if text, _ := os.ReadFile(filepath.Join(root, "MODULE.bazel")); string(text) != "old MODULE.bazel" {
		t.Fatalf("apply must restore the tree, MODULE.bazel = %q", text)
	}
	if _, err := os.Stat(filepath.Join(root, "patches/26/0001-new.patch")); err == nil {
		t.Fatal("apply must remove new files")
	}
	if _, err := os.Stat(filepath.Join(root, "patches/26/0001-old.patch")); err != nil {
		t.Fatal("apply must restore deleted files")
	}
}

func TestApplyFlagsAPatchFailureAndKeepsThePullRequest(t *testing.T) {
	root := nodeTree(t)
	tool := &fakeNodeTool{root: root, failPatches: true}
	node := runtime{Name: "node", Kind: kindNode, File: "MODULE.bazel"}
	applied, err := apply(root, tool.run, node, update{Line: "26", Old: "26.12.1", New: "26.13.0", Shasums: "sums"})
	if err != nil {
		t.Fatal(err)
	}
	if len(tool.calls) != 2 {
		t.Fatalf("calls = %v", tool.calls)
	}
	if got := changePaths(applied.Changes); got != "M MODULE.bazel, M STATUS.md" {
		t.Fatalf("a half edit must not reach the commit: %s", got)
	}
	if !strings.Contains(strings.Join(applied.Flags, " "), "make_patches") {
		t.Fatalf("flags = %v", applied.Flags)
	}
}

func TestApplyNewNodeMajorOmitsTheWorkflowAndFlagsIt(t *testing.T) {
	root := nodeTree(t)
	tool := &fakeNodeTool{root: root}
	node := runtime{Name: "node", Kind: kindNode, File: "MODULE.bazel"}
	applied, err := apply(root, tool.run, node, update{Line: "27", New: "27.0.0", NewLine: true, Shasums: "sums"})
	if err != nil {
		t.Fatal(err)
	}
	if strings.Contains(changePaths(applied.Changes), ".github/workflows") {
		t.Fatalf("GITHUB_TOKEN cannot write a workflow: %s", changePaths(applied.Changes))
	}
	joined := strings.Join(applied.Flags, " ")
	if !strings.Contains(joined, "ci.yml") || !strings.Contains(joined, "new line") {
		t.Fatalf("flags = %v", applied.Flags)
	}
}

// After PR #30 the Node tool edits runtimes/node/manifest.json and not ci.yml. The file reaches the commit.
func TestApplyNewNodeMajorWithTheNodeManifest(t *testing.T) {
	root := nodeTree(t)
	writeFile(t, root, "runtimes/node/manifest.json", "{}")
	edit := func(string, ...string) error {
		return os.WriteFile(filepath.Join(root, "runtimes/node/manifest.json"), []byte(`{"versions":[]}`), 0o644)
	}
	node := runtime{Name: "node", Kind: kindNode, File: "MODULE.bazel"}
	applied, err := apply(root, edit, node, update{Line: "27", New: "27.0.0", NewLine: true, Shasums: "sums"})
	if err != nil {
		t.Fatal(err)
	}
	if got := changePaths(applied.Changes); got != "M runtimes/node/manifest.json" {
		t.Fatalf("changes = %s", got)
	}
	joined := strings.Join(applied.Flags, " ")
	if strings.Contains(joined, "ci.yml") || !strings.Contains(joined, "new line") {
		t.Fatalf("flags = %v", applied.Flags)
	}
}

func TestApplyManifest(t *testing.T) {
	root := nodeTree(t)
	writeFile(t, root, "runtimes/deno/manifest.json", denoManifest)
	deno := runtime{Name: "deno", Kind: kindGitHubRelease, File: "runtimes/deno/manifest.json"}
	refuse := func(string, ...string) error { return errors.New("a manifest needs no command") }
	applied, err := apply(root, refuse, deno, update{Line: "2", Old: "2.9.7", New: "2.9.8", SHA256: newSum})
	if err != nil {
		t.Fatal(err)
	}
	if got := changePaths(applied.Changes); got != "M runtimes/deno/manifest.json" {
		t.Fatalf("changes = %s", got)
	}
	if !strings.Contains(string(applied.Changes[0].Content), `"version": "2.9.8"`) {
		t.Fatalf("content = %s", applied.Changes[0].Content)
	}
	if text, _ := os.ReadFile(filepath.Join(root, "runtimes/deno/manifest.json")); string(text) != denoManifest {
		t.Fatal("apply must not leave the edit in the tree")
	}
}

func TestApplyPreservesTheExecutableMode(t *testing.T) {
	root := nodeTree(t)
	edit := func(string, ...string) error {
		return os.WriteFile(filepath.Join(root, "tools/run.sh"), []byte("new"), 0o755)
	}
	applied, err := apply(root, edit, runtime{Name: "node", Kind: kindNode}, update{Line: "26", New: "26.13.0", Shasums: "s"})
	if err != nil {
		t.Fatal(err)
	}
	if len(applied.Changes) != 1 || applied.Changes[0].Mode != "100755" {
		t.Fatalf("changes = %+v", applied.Changes)
	}
}
