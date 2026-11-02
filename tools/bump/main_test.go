package main

import (
	"errors"
	"net/http"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

const (
	spineIndex = "https://nodejs.org/dist/index.json"
	spineSums  = "https://nodejs.org/dist/v26.13.0/SHASUMS256.txt"
	denoFeed   = "https://api.github.com/repos/denoland/deno/releases?per_page=100"
)

func spineResponses(t *testing.T) map[string]fakeResponse {
	return map[string]fakeResponse{
		"GET " + spineIndex: {200, fixture(t, "node_index.json")},
		"GET " + spineSums:  {200, fixture(t, "node_shasums.txt")},
		"GET " + denoFeed:   {200, fixture(t, "gh_deno.json")},
	}
}

// createAnswers makes one branch look new, with every write accepted.
func createAnswers(responses map[string]fakeResponse, branch string) {
	escaped := strings.ReplaceAll(branch, "/", "%2F")
	responses["GET "+api+"/pulls?head=o%3A"+escaped+"&per_page=1&state=all"] = fakeResponse{200, `[]`}
	responses["GET "+api+"/git/ref/heads/"+branch] = fakeResponse{404, `{}`}
	for key, value := range createResponses() {
		if strings.HasPrefix(key, "POST ") || strings.Contains(key, "/git/commits/") {
			responses[key] = value
		}
	}
}

func spine(t *testing.T, responses map[string]fakeResponse) (*bumper, *fakeTransport, string) {
	t.Helper()
	root := t.TempDir()
	writeFile(t, root, "MODULE.bazel", moduleText)
	writeFile(t, root, "runtimes/deno/manifest.json", denoManifest)
	transport := &fakeTransport{responses: responses}
	client := &http.Client{Transport: transport}
	return &bumper{
		root:    root,
		baseSHA: "base",
		run: func(string, ...string) error {
			return os.WriteFile(filepath.Join(root, "MODULE.bazel"), []byte("new"), 0o644)
		},
		upstream: &upstream{client: client},
		github:   &gitHub{client: client, repo: "o/r", token: "t"},
	}, transport, root
}

func pullsOpened(f *fakeTransport) int {
	return strings.Count(requestsSeen(f), "POST /pulls")
}

func TestRunOpensOnePullRequestForEachUpdate(t *testing.T) {
	responses := spineResponses(t)
	// The fixture has a 24 and a 26 above the pins in moduleText, so Node gives one update for 26.
	createAnswers(responses, "bump/node-26-26.13.0")
	createAnswers(responses, "bump/deno-2-2.9.8")
	b, f, _ := spine(t, responses)
	if err := b.runAll(); err != nil {
		t.Fatal(err)
	}
	if got := pullsOpened(f); got != 2 {
		t.Fatalf("pull requests = %d\n%s", got, requestsSeen(f))
	}
}

func TestRunSkipsAnExistingPullRequest(t *testing.T) {
	responses := spineResponses(t)
	createAnswers(responses, "bump/node-26-26.13.0")
	createAnswers(responses, "bump/deno-2-2.9.8")
	responses["GET "+pullQuery] = fakeResponse{200, `[{"state":"open"}]`}
	b, f, _ := spine(t, responses)
	if err := b.runAll(); err != nil {
		t.Fatal(err)
	}
	if got := pullsOpened(f); got != 1 {
		t.Fatalf("pull requests = %d", got)
	}
}

func TestRunResumesABranchThatHasNoPullRequest(t *testing.T) {
	responses := spineResponses(t)
	createAnswers(responses, "bump/node-26-26.13.0")
	createAnswers(responses, "bump/deno-2-2.9.8")
	responses["GET "+refURL] = fakeResponse{200, `{}`}
	b, f, _ := spine(t, responses)
	if err := b.runAll(); err != nil {
		t.Fatal(err)
	}
	if got := pullsOpened(f); got != 2 {
		t.Fatalf("the resumed branch must get its pull request: %d", got)
	}
	if got := strings.Count(requestsSeen(f), "POST /git/commits"); got != 1 {
		t.Fatalf("the resumed branch must get no second commit: %d commits", got)
	}
}

func TestRunContinuesAfterAFailure(t *testing.T) {
	responses := spineResponses(t)
	delete(responses, "GET "+spineIndex)
	createAnswers(responses, "bump/deno-2-2.9.8")
	b, f, _ := spine(t, responses)
	err := b.runAll()
	if err == nil {
		t.Fatal("the run must end with an error")
	}
	if got := pullsOpened(f); got != 1 {
		t.Fatalf("Deno must still get its pull request: %d", got)
	}
}

func TestRunRestoresTheTreeBetweenUpdates(t *testing.T) {
	responses := spineResponses(t)
	createAnswers(responses, "bump/node-26-26.13.0")
	createAnswers(responses, "bump/deno-2-2.9.8")
	b, _, root := spine(t, responses)
	if err := b.runAll(); err != nil {
		t.Fatal(err)
	}
	text, err := os.ReadFile(filepath.Join(root, "MODULE.bazel"))
	if err != nil || string(text) != moduleText {
		t.Fatalf("MODULE.bazel = %q, %v", text, err)
	}
}

func TestDryRunWritesNothing(t *testing.T) {
	b, f, _ := spine(t, spineResponses(t))
	b.dryRun = true
	b.run = func(string, ...string) error { return errors.New("a dry run runs no command") }
	if err := b.runAll(); err != nil {
		t.Fatal(err)
	}
	for _, r := range f.requests {
		if r.method != http.MethodGet {
			t.Fatalf("a dry run must only read: %+v", r)
		}
		if r.auth != "" {
			t.Fatalf("no token was given: %+v", r)
		}
	}
}

func TestTheTokenReachesOnlyGitHub(t *testing.T) {
	transport := &fakeTransport{responses: map[string]fakeResponse{
		"GET https://pypi.org/pypi/certifi/json":                     {200, `{"info":{"version":"1.0.0"}}`},
		"GET https://api.github.com/repos/a/b/releases?per_page=100": {200, `[]`},
	}}
	u := &upstream{client: &http.Client{Transport: transport}, token: "secret"}
	if _, err := u.check(runtime{Name: "certifi", Kind: kindPyPI, Repo: "certifi"}); err != nil {
		t.Fatal(err)
	}
	if _, err := u.check(runtime{Name: "b", Kind: kindGitHubRelease, Repo: "a/b"}); err != nil {
		t.Fatal(err)
	}
	if transport.requests[0].auth != "" {
		t.Fatalf("PyPI got the token: %+v", transport.requests[0])
	}
	if transport.requests[1].auth != "Bearer secret" {
		t.Fatalf("api.github.com needs the token for its rate limit: %+v", transport.requests[1])
	}
}
