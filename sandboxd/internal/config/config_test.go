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
				"toolset_digest":"` + digest + `",
				"network_mode":"target_allowlist",
				"allow_net_raw":true,
				"allowed_executables":["nmap"],
				"limits":{}
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

func TestLoadAcceptsConfigWithoutTools(t *testing.T) {
	config, err := loadFixture(t, validConfig(t.TempDir()))
	if err != nil {
		t.Fatal(err)
	}
	profile, err := config.Profile("raw-network")
	if err != nil {
		t.Fatal(err)
	}
	if len(profile.AllowedExecutables) != 1 || profile.AllowedExecutables[0] != "nmap" {
		t.Fatalf("expected the profile executable allowlist to survive, got %v", profile.AllowedExecutables)
	}
}

func TestLoadRejectsTopLevelToolsField(t *testing.T) {
	digest := strings.Repeat("a", 64)
	content := strings.Replace(
		validConfig(t.TempDir()),
		`"profiles":{`,
		`"tools":{"nmap-raw":{"profile_id":"raw-network","image":"example.invalid/nmap@sha256:`+digest+`"}},
		"profiles":{`,
		1,
	)
	_, err := loadFixture(t, content)
	if err == nil {
		t.Fatal("expected a top-level tools field to be rejected")
	}
	if !strings.Contains(err.Error(), "unknown field") {
		t.Fatalf("expected an unknown field error, got %v", err)
	}
}

func TestLoadRejectsMutableProfileImage(t *testing.T) {
	content := strings.Replace(
		validConfig(t.TempDir()),
		`example.invalid/network@sha256:`+strings.Repeat("a", 64),
		`example.invalid/network:latest`,
		1,
	)
	if _, err := loadFixture(t, content); err == nil {
		t.Fatal("expected mutable image to fail")
	}
}

func TestLoadRejectsInvalidAllowedExecutable(t *testing.T) {
	content := strings.Replace(
		validConfig(t.TempDir()),
		`"allowed_executables":["nmap"]`,
		`"allowed_executables":["/usr/bin/nmap"]`,
		1,
	)
	if _, err := loadFixture(t, content); err == nil {
		t.Fatal("expected an invalid allowed executable to fail")
	}
}

func TestLoadRejectsRepeatedAllowedExecutable(t *testing.T) {
	content := strings.Replace(
		validConfig(t.TempDir()),
		`"allowed_executables":["nmap"]`,
		`"allowed_executables":["nmap","nmap"]`,
		1,
	)
	if _, err := loadFixture(t, content); err == nil {
		t.Fatal("expected a repeated allowed executable to fail")
	}
}

func TestLoadRejectsMissingToolsetDigest(t *testing.T) {
	content := strings.Replace(
		validConfig(t.TempDir()),
		`"toolset_digest":"`+strings.Repeat("a", 64)+`",`,
		``,
		1,
	)
	if _, err := loadFixture(t, content); err == nil {
		t.Fatal("expected missing toolset digest to fail")
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

// The committed configuration contains release digest placeholders, so Load
// rejects it until images are published. Digest parity still applies.
func TestCommittedConfigDigestMatchesControlPlane(t *testing.T) {
	raw, err := os.ReadFile(filepath.Join("..", "..", "..", "config", "sandbox.json"))
	if err != nil {
		t.Fatal(err)
	}
	if strings.Contains(string(raw), `"tools"`) {
		t.Fatal("committed sandbox config must not declare a top-level tools mapping")
	}
	digest, err := Digest(raw)
	if err != nil {
		t.Fatal(err)
	}
	expected := os.Getenv("TGA_EXPECTED_CONFIG_DIGEST")
	if expected == "" {
		t.Skip("TGA_EXPECTED_CONFIG_DIGEST is not set; parity is asserted by the Python suite")
	}
	if digest != expected {
		t.Fatalf("config digest %s does not match the control plane digest %s", digest, expected)
	}
}
