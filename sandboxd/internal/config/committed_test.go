package config

import (
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

// committedConfig is the file provisioning copies to /etc/tga/sandbox.json.
func committedConfig(t *testing.T) map[string]any {
	t.Helper()
	raw, err := os.ReadFile(filepath.Join("..", "..", "..", "config", "sandbox.json"))
	if err != nil {
		t.Fatalf("reading the committed config: %v", err)
	}
	var value map[string]any
	if err := json.Unmarshal(raw, &value); err != nil {
		t.Fatalf("the committed config is not JSON: %v", err)
	}
	return value
}

// TestCommittedConfigLoadsAfterHostBinding is the test whose absence let
// sandboxd ship unable to read its own configuration.
//
// The only other check on this file computed a digest of the raw bytes, which
// never exercises the decoder. So `supported_capabilities` and
// `session_executables` -- fields the control plane writes and this side had
// never declared -- made DisallowUnknownFields reject every profile, and
// tga-sandboxd exited 1 in a restart loop on a freshly provisioned host with
// `decode config: json: unknown field "supported_capabilities"`.
//
// allowed_client_uids is injected here because it is a host fact: the
// committed file ships it empty, and provisioning fills it in.
func TestCommittedConfigLoadsAfterHostBinding(t *testing.T) {
	value := committedConfig(t)
	sandboxd, ok := value["sandboxd"].(map[string]any)
	if !ok {
		t.Fatal("the committed config has no sandboxd section")
	}
	sandboxd["allowed_client_uids"] = []any{float64(999)}
	// run_root is bound too, and to a host-absolute path rather than the
	// committed "/var/lib/tga/runs": Load checks it with filepath.IsAbs, which
	// answers for the platform the test is compiled on, and a POSIX path is
	// not absolute on Windows.
	work := t.TempDir()
	sandboxd["run_root"] = filepath.Join(work, "runs")

	bound := filepath.Join(work, "sandbox.json")
	raw, err := json.Marshal(value)
	if err != nil {
		t.Fatalf("re-encoding: %v", err)
	}
	if err := os.WriteFile(bound, raw, 0o600); err != nil {
		t.Fatalf("writing the bound config: %v", err)
	}

	loaded, err := Load(bound)
	if err != nil {
		t.Fatalf("sandboxd cannot load the configuration it ships with: %v", err)
	}
	if len(loaded.Profiles) == 0 {
		t.Fatal("the committed config declares no profiles")
	}
}

// TestEveryProfileFieldIsDeclaredHere catches the same class before it becomes
// a runtime failure: a field the control plane starts writing, and this side
// has never heard of, stops the daemon dead.
func TestEveryProfileFieldIsDeclaredHere(t *testing.T) {
	value := committedConfig(t)
	profiles, ok := value["profiles"].(map[string]any)
	if !ok || len(profiles) == 0 {
		t.Fatal("the committed config declares no profiles")
	}

	known := map[string]struct{}{}
	for _, tag := range []string{
		"id", "provider", "image", "network_mode", "web_allow_hosts",
		"allow_net_raw", "allow_ptrace", "allowed_executables",
		"supported_capabilities", "session_executables", "toolset_digest",
		"limits",
	} {
		known[tag] = struct{}{}
	}

	unknown := map[string]struct{}{}
	for _, entry := range profiles {
		profile, ok := entry.(map[string]any)
		if !ok {
			t.Fatal("a profile is not an object")
		}
		for field := range profile {
			if _, declared := known[field]; !declared {
				unknown[field] = struct{}{}
			}
		}
	}
	if len(unknown) > 0 {
		names := make([]string, 0, len(unknown))
		for field := range unknown {
			names = append(names, field)
		}
		t.Fatalf("profiles carry fields sandboxd does not declare: %s",
			strings.Join(names, ", "))
	}
}
