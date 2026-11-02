package main

import (
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"regexp"
)

const (
	kindNode          = "nodejs-dist"
	kindGitHubRelease = "github-release"
	kindPyPI          = "pypi"
	kindAdoptium      = "adoptium"
)

// A runtime is one program that the repository pins, with one pin for each line.
type runtime struct {
	Name, Kind string
	Repo       string // github-release and pypi only: the upstream project.
	File       string // The file that holds the pins, relative to the repository root.
	Pins       []pin
}

type pin struct {
	Line, Version, SHA256 string
}

var (
	nodeTagPattern = regexp.MustCompile(`(?m)^node\.version\(\n    major = "(\d+)",\n    version = "([^"]+)",\n    sha256 = "([^"]+)",`)
	namePattern    = regexp.MustCompile(`^[a-z0-9][a-z0-9_-]*$`)
	linePattern    = regexp.MustCompile(`^[a-z0-9][a-z0-9.]*$`)
	repoPattern    = regexp.MustCompile(`^[A-Za-z0-9][A-Za-z0-9_-]*/[A-Za-z0-9][A-Za-z0-9._-]*$`)
)

// readRuntimes gives Node from MODULE.bazel, then one runtime for each runtimes/*/manifest.json file.
// A missing runtimes directory is no error. Each runtime lands in its own pull request.
func readRuntimes(root string) ([]runtime, error) {
	module, err := os.ReadFile(filepath.Join(root, "MODULE.bazel"))
	if err != nil {
		return nil, err
	}
	pins, err := nodePins(string(module))
	if err != nil {
		return nil, err
	}
	runtimes := []runtime{{Name: "node", Kind: kindNode, File: "MODULE.bazel", Pins: pins}}

	manifests, err := filepath.Glob(filepath.Join(root, "runtimes", "*", "manifest.json"))
	if err != nil {
		return nil, err
	}
	for _, path := range manifests {
		parsed, err := readManifest(path)
		if err != nil {
			return nil, fmt.Errorf("%s: %w", path, err)
		}
		if parsed.Kind == kindNode {
			continue // MODULE.bazel holds the Node pins, and add_node_version.py edits both files.
		}
		parsed.File = filepath.Join("runtimes", filepath.Base(filepath.Dir(path)), "manifest.json")
		runtimes = append(runtimes, parsed)
	}
	return runtimes, nil
}

func nodePins(module string) ([]pin, error) {
	var pins []pin
	for _, tag := range nodeTagPattern.FindAllStringSubmatch(module, -1) {
		pins = append(pins, pin{Line: tag[1], Version: tag[2], SHA256: tag[3]})
	}
	if len(pins) == 0 {
		return nil, fmt.Errorf("MODULE.bazel has no node.version tag")
	}
	return pins, nil
}

func readManifest(path string) (runtime, error) {
	text, err := os.ReadFile(path)
	if err != nil {
		return runtime{}, err
	}
	var manifest struct {
		Runtime  string `json:"runtime"`
		Upstream struct {
			Kind string `json:"kind"`
			Repo string `json:"repo"`
		} `json:"upstream"`
		Versions []struct {
			Line    string `json:"line"`
			Version string `json:"version"`
			SHA256  string `json:"sha256"`
		} `json:"versions"`
	}
	if err := json.Unmarshal(text, &manifest); err != nil {
		return runtime{}, err
	}
	parsed := runtime{Name: manifest.Runtime, Kind: manifest.Upstream.Kind, Repo: manifest.Upstream.Repo}
	if !namePattern.MatchString(parsed.Name) {
		return runtime{}, fmt.Errorf("bad runtime name %q", parsed.Name)
	}
	switch parsed.Kind {
	case kindGitHubRelease:
		if !repoPattern.MatchString(parsed.Repo) {
			return runtime{}, fmt.Errorf("bad upstream repo %q", parsed.Repo)
		}
	case kindPyPI:
		if parsed.Repo == "" {
			parsed.Repo = parsed.Name
		}
		if !namePattern.MatchString(parsed.Repo) {
			return runtime{}, fmt.Errorf("bad upstream repo %q", parsed.Repo)
		}
	case kindAdoptium, kindNode:
	default:
		return runtime{}, fmt.Errorf("unknown upstream kind %q", parsed.Kind)
	}
	for _, version := range manifest.Versions {
		if !linePattern.MatchString(version.Line) {
			return runtime{}, fmt.Errorf("bad line %q", version.Line)
		}
		if !validVersion(version.Version) {
			return runtime{}, fmt.Errorf("bad version %q", version.Version)
		}
		parsed.Pins = append(parsed.Pins, pin{version.Line, version.Version, version.SHA256})
	}
	return parsed, nil
}
