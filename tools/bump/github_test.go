package main

import (
	"encoding/json"
	"net/http"
	"strings"
	"testing"
)

const (
	api       = "https://api.github.com/repos/o/r"
	pullQuery = api + "/pulls?head=o%3Abump%2Fdeno-2-2.9.8&per_page=1&state=all"
	refURL    = api + "/git/ref/heads/bump/deno-2-2.9.8"
)

func requestsSeen(f *fakeTransport) string {
	var lines []string
	for _, r := range f.requests {
		lines = append(lines, r.method+" "+strings.TrimPrefix(r.url, api))
	}
	return strings.Join(lines, "\n")
}

func newGitHub(responses map[string]fakeResponse) (*gitHub, *fakeTransport) {
	transport := &fakeTransport{responses: responses}
	return &gitHub{client: &http.Client{Transport: transport}, repo: "o/r", token: "test-token"}, transport
}

func createResponses() map[string]fakeResponse {
	return map[string]fakeResponse{
		"GET " + pullQuery:                 {200, `[]`},
		"GET " + refURL:                    {404, `{}`},
		"GET " + api + "/git/commits/base": {200, `{"tree":{"sha":"basetree"}}`},
		"POST " + api + "/git/blobs":       {201, `{"sha":"blob1"}`},
		"POST " + api + "/git/trees":       {201, `{"sha":"tree1"}`},
		"POST " + api + "/git/commits":     {201, `{"sha":"commit1","verification":{"verified":true,"reason":"valid"}}`},
		"POST " + api + "/git/refs":        {201, `{}`},
		"POST " + api + "/pulls":           {201, `{"html_url":"https://github.com/o/r/pull/7"}`},
	}
}

func proposalForDeno() proposal {
	return proposal{
		Branch:  "bump/deno-2-2.9.8",
		Title:   "deno 2: 2.9.7 to 2.9.8",
		Body:    "body",
		BaseSHA: "base",
		Changes: []fileChange{
			{Path: "runtimes/deno/manifest.json", Mode: "100644", Content: []byte("new")},
			{Path: "old.patch", Deleted: true},
		},
	}
}

func bodyOf(t *testing.T, f *fakeTransport, method, url string) map[string]any {
	t.Helper()
	for _, r := range f.requests {
		if r.method == method && r.url == url {
			var parsed map[string]any
			if err := json.Unmarshal([]byte(r.body), &parsed); err != nil {
				t.Fatal(err)
			}
			return parsed
		}
	}
	t.Fatalf("no request %s %s", method, url)
	return nil
}

func TestStateOfABranch(t *testing.T) {
	tests := []struct {
		name  string
		pulls fakeResponse
		ref   fakeResponse
		want  branchState
	}{
		{"nothing exists", fakeResponse{200, `[]`}, fakeResponse{404, `{}`}, stateNew},
		{"an open pull request", fakeResponse{200, `[{"state":"open"}]`}, fakeResponse{200, `{}`}, stateHasPull},
		{"a closed pull request: a human said no", fakeResponse{200, `[{"state":"closed"}]`}, fakeResponse{404, `{}`}, stateHasPull},
		{"a branch from a run that could not open the pull request", fakeResponse{200, `[]`}, fakeResponse{200, `{}`}, stateHasBranch},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			g, _ := newGitHub(map[string]fakeResponse{"GET " + pullQuery: tt.pulls, "GET " + refURL: tt.ref})
			got, err := g.state("bump/deno-2-2.9.8")
			if err != nil {
				t.Fatal(err)
			}
			if got != tt.want {
				t.Fatalf("state = %v, want %v", got, tt.want)
			}
		})
	}
}

func TestStateFailsClosed(t *testing.T) {
	g, _ := newGitHub(map[string]fakeResponse{"GET " + pullQuery: {500, `{}`}})
	if _, err := g.state("bump/deno-2-2.9.8"); err == nil {
		t.Fatal("an unknown state must not lead to a second pull request")
	}
}

func TestCreateMakesAnUnsignedPayloadSoGitHubSigns(t *testing.T) {
	g, f := newGitHub(createResponses())
	url, err := g.create(proposalForDeno())
	if err != nil {
		t.Fatal(err)
	}
	if url != "https://github.com/o/r/pull/7" {
		t.Fatalf("url = %q", url)
	}
	const want = "GET /git/commits/base\nPOST /git/blobs\nPOST /git/trees\nPOST /git/commits\nPOST /git/refs\nPOST /pulls"
	if got := requestsSeen(f); got != want {
		t.Fatalf("requests:\n%s", got)
	}
	commit := bodyOf(t, f, "POST", api+"/git/commits")
	for _, forbidden := range []string{"author", "committer", "signature"} {
		if _, found := commit[forbidden]; found {
			t.Errorf("the commit payload must not hold %s, because GitHub then does not sign", forbidden)
		}
	}
	if parents := commit["parents"].([]any); len(parents) != 1 || parents[0] != "base" {
		t.Errorf("parents = %v", parents)
	}
	ref := bodyOf(t, f, "POST", api+"/git/refs")
	if ref["ref"] != "refs/heads/bump/deno-2-2.9.8" || ref["sha"] != "commit1" {
		t.Errorf("ref = %v", ref)
	}
	pull := bodyOf(t, f, "POST", api+"/pulls")
	if pull["head"] != "bump/deno-2-2.9.8" || pull["base"] != "main" || pull["draft"] != false {
		t.Errorf("pull = %v", pull)
	}
	for _, r := range f.requests {
		if r.auth != "Bearer test-token" {
			t.Errorf("auth = %q on %s", r.auth, r.url)
		}
	}
}

func TestCreateDeletesAFileWithANullSHA(t *testing.T) {
	g, f := newGitHub(createResponses())
	if _, err := g.create(proposalForDeno()); err != nil {
		t.Fatal(err)
	}
	tree := bodyOf(t, f, "POST", api+"/git/trees")
	if tree["base_tree"] != "basetree" {
		t.Errorf("base_tree = %v", tree["base_tree"])
	}
	entries := tree["tree"].([]any)
	deleted := entries[1].(map[string]any)
	if sha, found := deleted["sha"]; !found || sha != nil {
		t.Errorf("deleted = %v", deleted)
	}
}

func TestCreateRefusesAnUnverifiedCommit(t *testing.T) {
	responses := createResponses()
	responses["POST "+api+"/git/commits"] = fakeResponse{201, `{"sha":"commit1","verification":{"verified":false,"reason":"unsigned"}}`}
	g, f := newGitHub(responses)
	_, err := g.create(proposalForDeno())
	if err == nil || !strings.Contains(err.Error(), "unsigned") {
		t.Fatalf("err = %v", err)
	}
	if strings.Contains(requestsSeen(f), "git/refs") {
		t.Fatal("an unverified commit must not reach a branch")
	}
}

func TestCreateNamesTheRepoSettingOnA403(t *testing.T) {
	responses := createResponses()
	responses["POST "+api+"/pulls"] = fakeResponse{403, `{"message":"GitHub Actions is not permitted to create or approve pull requests."}`}
	g, _ := newGitHub(responses)
	_, err := g.create(proposalForDeno())
	if err == nil || !strings.Contains(err.Error(), "Allow GitHub Actions to create and approve pull requests") {
		t.Fatalf("err = %v", err)
	}
}

func TestCreateAcceptsARefThatARaceMade(t *testing.T) {
	responses := createResponses()
	responses["POST "+api+"/git/refs"] = fakeResponse{422, `{"message":"Reference already exists"}`}
	g, f := newGitHub(responses)
	if _, err := g.create(proposalForDeno()); err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(requestsSeen(f), "POST /pulls") {
		t.Fatal("want the pull request")
	}
}

func TestOpenPullOnlyMakesNoCommit(t *testing.T) {
	p := proposalForDeno()
	p.Flags = []string{"human work"}
	g, f := newGitHub(createResponses())
	if _, err := g.openPull(p); err != nil {
		t.Fatal(err)
	}
	if got := requestsSeen(f); got != "POST /pulls" {
		t.Fatalf("requests:\n%s", got)
	}
	pull := bodyOf(t, f, "POST", api+"/pulls")
	if pull["draft"] != true || !strings.HasPrefix(pull["title"].(string), "[human work] ") || !strings.Contains(pull["body"].(string), "human work") {
		t.Errorf("a flagged pull request = %v", pull)
	}
}
