package runtime

import (
	"crypto/sha256"
	"encoding/hex"
	"strings"
	"testing"

	"github.com/team-gipsy/tga-sandboxd/internal/config"
)

func TestValidateToolset(t *testing.T) {
	valid := []byte(`{"profile_id":"offline-analysis","tools":{"file":"1.0","python3":"3.13"}}`)
	digest := sha256.Sum256(valid)
	profile := config.Profile{
		ID:                 "offline-analysis",
		AllowedExecutables: []string{"file", "python3"},
		ToolsetDigest:      hex.EncodeToString(digest[:]),
	}

	tests := []struct {
		name    string
		raw     []byte
		profile config.Profile
		wantErr string
	}{
		{name: "valid", raw: valid, profile: profile},
		{
			name: "digest mismatch", raw: valid,
			profile: func() config.Profile {
				value := profile
				value.ToolsetDigest = strings.Repeat("0", 64)
				return value
			}(),
			wantErr: "digest does not match",
		},
		{
			name: "profile mismatch",
			raw:  []byte(`{"profile_id":"another-profile","tools":{"file":"1.0","python3":"3.13"}}`),
			profile: profileWithDigest(
				profile,
				[]byte(`{"profile_id":"another-profile","tools":{"file":"1.0","python3":"3.13"}}`),
			),
			wantErr: "profile id does not match",
		},
		{
			name: "missing executable",
			raw:  []byte(`{"profile_id":"offline-analysis","tools":{"file":"1.0"}}`),
			profile: profileWithDigest(
				profile,
				[]byte(`{"profile_id":"offline-analysis","tools":{"file":"1.0"}}`),
			),
			wantErr: `missing allowed executable "python3"`,
		},
		{
			name: "trailing object",
			raw:  []byte(`{"profile_id":"offline-analysis","tools":{"file":"1.0","python3":"3.13"}} {}`),
			profile: profileWithDigest(
				profile,
				[]byte(`{"profile_id":"offline-analysis","tools":{"file":"1.0","python3":"3.13"}} {}`),
			),
			wantErr: "exactly one JSON object",
		},
	}

	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			err := validateToolset(test.raw, test.profile)
			if test.wantErr == "" {
				if err != nil {
					t.Fatalf("validateToolset() error = %v", err)
				}
				return
			}
			if err == nil || !strings.Contains(err.Error(), test.wantErr) {
				t.Fatalf("validateToolset() error = %v, want substring %q", err, test.wantErr)
			}
		})
	}
}

func profileWithDigest(profile config.Profile, raw []byte) config.Profile {
	sum := sha256.Sum256(raw)
	profile.ToolsetDigest = hex.EncodeToString(sum[:])
	return profile
}

func TestFencingTokenPersistence(t *testing.T) {
	runtime := &Runtime{config: &config.Config{
		Sandboxd: config.Sandboxd{RunRoot: t.TempDir()},
	}}

	if err := runtime.writeFencingToken("task-1", "run-1", 7); err != nil {
		t.Fatal(err)
	}
	value, err := runtime.readFencingToken("task-1", "run-1")
	if err != nil {
		t.Fatal(err)
	}
	if value != 7 {
		t.Fatalf("readFencingToken() = %d, want 7", value)
	}
	if err := runtime.removeFencingToken("task-1", "run-1"); err != nil {
		t.Fatal(err)
	}
	if _, err := runtime.readFencingToken("task-1", "run-1"); err == nil {
		t.Fatal("expected removed fencing token to be unreadable")
	}
}
