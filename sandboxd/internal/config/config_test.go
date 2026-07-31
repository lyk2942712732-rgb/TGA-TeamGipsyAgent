package config

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func validConfig(runRoot string) string {
	digest := strings.Repeat("a", 64)
	return `{
		"version":1,
		"runtime":"enforced",
		"terminal_grace_seconds":900,
		"reconcile_interval_seconds":60,
		"docker_sandbox":{},
		"sandboxd":{
			"socket_path":"/run/tga-sandboxd/sandboxd.sock",
			"rpc_timeout_seconds":10,
			"protocol_major":1,
			"run_root":"` + filepath.ToSlash(runRoot) + `",
			"allowed_client_uids":[1001]
		},
		"profiles":{
			"raw-network":{
				"id":"raw-network",
				"provider":"sandboxd",
				"image":"example.invalid/network@sha256:` + digest + `",
				"network_mode":"target_allowlist",
				"allow_net_raw":true,
				"limits":{}
			}
		},
		"tools":{
			"nmap-raw":{
				"profile_id":"raw-network",
				"image":"example.invalid/nmap@sha256:` + digest + `",
				"args":["--mode","raw"]
			}
		}
	}`
}

func loadFixture(t *testing.T, content string) (*Config, error) {
	t.Helper()
	path := filepath.Join(t.TempDir(), "sandbox.json")
	if err := os.WriteFile(path, []byte(content), 0o600); err != nil {
		t.Fatal(err)
	}
	return Load(path)
}

func TestLoadRejectsMissingClientUIDPolicy(t *testing.T) {
	content := strings.Replace(validConfig(t.TempDir()), `"allowed_client_uids":[1001]`, `"allowed_client_uids":[]`, 1)
	if _, err := loadFixture(t, content); err == nil {
		t.Fatal("expected empty client UID policy to fail")
	}
}

func TestLoadRejectsMutableToolImage(t *testing.T) {
	content := strings.Replace(
		validConfig(t.TempDir()),
		`example.invalid/nmap@sha256:`+strings.Repeat("a", 64),
		`example.invalid/nmap:latest`,
		1,
	)
	if _, err := loadFixture(t, content); err == nil {
		t.Fatal("expected mutable image to fail")
	}
}

func TestWorkspaceRejectsInvalidTaskIdentity(t *testing.T) {
	config, err := loadFixture(t, validConfig(t.TempDir()))
	if err != nil {
		t.Fatal(err)
	}
	if _, err := config.Workspace("../escape"); err == nil {
		t.Fatal("expected path traversal to fail")
	}
}
