package config

import (
	"bytes"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/netip"
	"os"
	"path/filepath"
	"regexp"
	"strings"
)

var identifier = regexp.MustCompile(`^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$`)
var pinnedImage = regexp.MustCompile(`@sha256:[a-f0-9]{64}$`)

type Limits struct {
	TimeoutSeconds int     `json:"timeout_seconds"`
	MaxOutputBytes int64   `json:"max_output_bytes"`
	MemoryBytes    int64   `json:"memory_bytes"`
	CPUCount       float64 `json:"cpu_count"`
	PidsLimit      int64   `json:"pids_limit"`
}

type Profile struct {
	ID                 string   `json:"id"`
	Provider           string   `json:"provider"`
	Image              string   `json:"image"`
	NetworkMode        string   `json:"network_mode"`
	WebAllowHosts      []string `json:"web_allow_hosts"`
	AllowNetRaw        bool     `json:"allow_net_raw"`
	AllowPtrace        bool     `json:"allow_ptrace"`
	AllowedExecutables []string `json:"allowed_executables"`
	ToolsetDigest      string   `json:"toolset_digest"`
	Limits             Limits   `json:"limits"`
}

type Tool struct {
	ProfileID string   `json:"profile_id"`
	Image     string   `json:"image"`
	Args      []string `json:"args"`
}

type Sandboxd struct {
	SocketPath        string   `json:"socket_path"`
	RPCTimeoutSeconds int      `json:"rpc_timeout_seconds"`
	ProtocolMajor     uint32   `json:"protocol_major"`
	RunRoot           string   `json:"run_root"`
	AllowedClientUIDs []uint32 `json:"allowed_client_uids"`
}

type Config struct {
	Version                  int                `json:"version"`
	Runtime                  string             `json:"runtime"`
	TerminalGraceSeconds     int                `json:"terminal_grace_seconds"`
	ReconcileIntervalSeconds int                `json:"reconcile_interval_seconds"`
	Sandboxd                 Sandboxd           `json:"sandboxd"`
	DockerSandbox            json.RawMessage    `json:"docker_sandbox"`
	Profiles                 map[string]Profile `json:"profiles"`
	Tools                    map[string]Tool    `json:"tools"`
	Digest                   string             `json:"-"`
}

func Load(path string) (*Config, error) {
	raw, err := os.ReadFile(path)
	if err != nil {
		return nil, err
	}
	var value Config
	decoder := json.NewDecoder(bytes.NewReader(raw))
	decoder.DisallowUnknownFields()
	if err := decoder.Decode(&value); err != nil {
		return nil, fmt.Errorf("decode config: %w", err)
	}
	if err := decoder.Decode(&struct{}{}); !errors.Is(err, io.EOF) {
		return nil, errors.New("config must contain exactly one JSON object")
	}
	var canonical any
	if err := json.Unmarshal(raw, &canonical); err != nil {
		return nil, err
	}
	canonicalRaw, err := json.Marshal(canonical)
	if err != nil {
		return nil, err
	}
	sum := sha256.Sum256(canonicalRaw)
	value.Digest = hex.EncodeToString(sum[:])
	if value.Version != 1 || value.Runtime != "enforced" {
		return nil, errors.New("sandboxd requires version 1 with runtime=enforced")
	}
	if value.Sandboxd.ProtocolMajor != 1 {
		return nil, errors.New("unsupported protocol major")
	}
	if !filepath.IsAbs(value.Sandboxd.RunRoot) {
		return nil, errors.New("sandboxd.run_root must be absolute")
	}
	if len(value.Sandboxd.AllowedClientUIDs) == 0 {
		return nil, errors.New("sandboxd.allowed_client_uids must not be empty")
	}
	seenUIDs := map[uint32]struct{}{}
	for _, uid := range value.Sandboxd.AllowedClientUIDs {
		if _, exists := seenUIDs[uid]; exists {
			return nil, errors.New("sandboxd.allowed_client_uids contains duplicates")
		}
		seenUIDs[uid] = struct{}{}
	}
	for key, profile := range value.Profiles {
		if key != profile.ID || !identifier.MatchString(key) {
			return nil, fmt.Errorf("invalid profile %q", key)
		}
		if profile.Provider == "sandboxd" {
			if !pinnedImage.MatchString(profile.Image) || (profile.NetworkMode != "none" && profile.NetworkMode != "target_allowlist") {
				return nil, fmt.Errorf("sandboxd profile %q is not pinned or isolated", key)
			}
			if !regexp.MustCompile(`^[a-f0-9]{64}$`).MatchString(profile.ToolsetDigest) {
				return nil, fmt.Errorf("sandboxd profile %q requires a toolset digest", key)
			}
		}
		if profile.AllowNetRaw && profile.Provider != "sandboxd" {
			return nil, fmt.Errorf("profile %q grants NET_RAW outside sandboxd", key)
		}
		if profile.AllowPtrace && profile.Provider != "sandboxd" {
			return nil, fmt.Errorf("profile %q grants SYS_PTRACE outside sandboxd", key)
		}
		seenExecutables := map[string]struct{}{}
		for _, executable := range profile.AllowedExecutables {
			if !identifier.MatchString(executable) {
				return nil, fmt.Errorf("profile %q has invalid allowed executable", key)
			}
			if _, exists := seenExecutables[executable]; exists {
				return nil, fmt.Errorf("profile %q repeats an allowed executable", key)
			}
			seenExecutables[executable] = struct{}{}
		}
		if profile.Limits.TimeoutSeconds == 0 {
			profile.Limits.TimeoutSeconds = 300
		}
		if profile.Limits.MaxOutputBytes == 0 {
			profile.Limits.MaxOutputBytes = 262144
		}
		if profile.Limits.MemoryBytes == 0 {
			profile.Limits.MemoryBytes = 512 * 1024 * 1024
		}
		if profile.Limits.CPUCount == 0 {
			profile.Limits.CPUCount = 1
		}
		if profile.Limits.PidsLimit == 0 {
			profile.Limits.PidsLimit = 256
		}
		value.Profiles[key] = profile
	}
	for key, tool := range value.Tools {
		if !identifier.MatchString(key) || !pinnedImage.MatchString(tool.Image) {
			return nil, fmt.Errorf("tool %q is invalid or not digest pinned", key)
		}
		_, ok := value.Profiles[tool.ProfileID]
		if !ok {
			return nil, fmt.Errorf("tool %q references an unknown profile", key)
		}
		if len(tool.Args) > 32 {
			return nil, fmt.Errorf("tool %q has too many fixed args", key)
		}
		for _, argument := range tool.Args {
			if strings.ContainsRune(argument, '\x00') {
				return nil, fmt.Errorf("tool %q has an invalid fixed arg", key)
			}
		}
	}
	return &value, nil
}

func (c *Config) Profile(id string) (Profile, error) {
	value, ok := c.Profiles[id]
	if !ok || value.Provider != "sandboxd" {
		return Profile{}, errors.New("unknown sandboxd profile")
	}
	return value, nil
}

func (c *Config) Tool(id, profileID string) (Tool, error) {
	value, ok := c.Tools[id]
	if !ok || value.ProfileID != profileID {
		return Tool{}, errors.New("tool is not authorized for this profile")
	}
	return value, nil
}

func (c *Config) Workspace(taskID string) (string, error) {
	if !identifier.MatchString(taskID) {
		return "", errors.New("invalid task id")
	}
	root, err := filepath.Abs(c.Sandboxd.RunRoot)
	if err != nil {
		return "", err
	}
	value := filepath.Join(root, taskID, "workspace")
	rel, err := filepath.Rel(root, value)
	if err != nil || rel == "." || rel == ".." || filepath.IsAbs(rel) {
		return "", errors.New("workspace escapes run root")
	}
	return value, nil
}

func ValidIdentifier(value string) bool { return identifier.MatchString(value) }

func ValidateCIDR(value string) error {
	prefix, err := netip.ParsePrefix(value)
	if err != nil || prefix != prefix.Masked() || prefix.String() != value {
		return errors.New("CIDR must be canonical")
	}
	return nil
}
