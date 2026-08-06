package runtime

import (
	"bytes"
	"errors"
	"io"
	"os"
	"strings"
	"testing"

	"github.com/team-gipsy/tga/launcher/internal/protocol"
)

// wsl.exe emits UTF-16LE whatever the console code page is, so a naive
// string() sees text interleaved with NUL bytes and no distro ever matches.
func TestDecodeWSLOutputHandlesUTF16LEWithBOM(t *testing.T) {
	raw := []byte{0xFF, 0xFE}
	for _, r := range "TGA-Runtime\n" {
		raw = append(raw, byte(r), 0x00)
	}
	if got := decodeWSLOutput(raw); strings.TrimSpace(got) != "TGA-Runtime" {
		t.Fatalf("got %q, want %q", got, "TGA-Runtime")
	}
}

func TestDecodeWSLOutputHandlesUTF16LEWithoutBOM(t *testing.T) {
	raw := []byte{}
	for _, r := range "Ubuntu-22.04\nTGA-Runtime\n" {
		raw = append(raw, byte(r), 0x00)
	}
	got := decodeWSLOutput(raw)
	if !strings.Contains(got, "TGA-Runtime") || !strings.Contains(got, "Ubuntu-22.04") {
		t.Fatalf("got %q, want both distro names", got)
	}
}

func TestDecodeWSLOutputLeavesPlainUTF8Alone(t *testing.T) {
	if got := decodeWSLOutput([]byte("TGA-Runtime\n")); strings.TrimSpace(got) != "TGA-Runtime" {
		t.Fatalf("got %q", got)
	}
}

func TestDecodeWSLOutputOnEmptyInput(t *testing.T) {
	if got := decodeWSLOutput(nil); got != "" {
		t.Fatalf("got %q, want empty", got)
	}
}

// --- Invoke ---

type fakeRunner struct {
	stdout []byte
	err    error
	args   []string
}

func (f *fakeRunner) Run(args ...string) ([]byte, error) {
	f.args = args
	return f.stdout, f.err
}
func (f *fakeRunner) Describe() string            { return "fake" }
func (f *fakeRunner) Streaming(_ ...string) error { return nil }

func TestInvokeAppendsJSONFlag(t *testing.T) {
	runner := &fakeRunner{stdout: []byte(`{"ok":true,"status":"ready"}`)}
	if _, err := Invoke(runner, "up"); err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if len(runner.args) != 2 || runner.args[1] != "--json" {
		t.Fatalf("got args %v, want [up --json]", runner.args)
	}
}

func TestInvokeParsesResult(t *testing.T) {
	runner := &fakeRunner{stdout: []byte(
		`{"ok":true,"status":"degraded","url":"http://127.0.0.1:8123",` +
			`"steps":[{"name":"start_api","ok":true,"detail":"pid 1"}]}`)}
	result, err := Invoke(runner, "up")
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if result.Status != "degraded" || result.URL != "http://127.0.0.1:8123" {
		t.Fatalf("unexpected result %+v", result)
	}
	if len(result.Steps) != 1 || result.Steps[0].Name != "start_api" {
		t.Fatalf("unexpected steps %+v", result.Steps)
	}
}

// A worker run through WSL may emit warnings before its JSON; the launcher
// must still find the payload rather than failing to parse.
func TestInvokeToleratesLeadingNoise(t *testing.T) {
	runner := &fakeRunner{stdout: []byte("warning: something\n{\"ok\":true,\"status\":\"ready\"}")}
	result, err := Invoke(runner, "status")
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if !result.OK || result.Status != "ready" {
		t.Fatalf("unexpected result %+v", result)
	}
}

func TestInvokeRejectsEmptyOutput(t *testing.T) {
	runner := &fakeRunner{stdout: []byte("   \n")}
	if _, err := Invoke(runner, "status"); err == nil {
		t.Fatal("expected an error for empty worker output")
	}
}

func TestInvokePropagatesRunnerFailure(t *testing.T) {
	runner := &fakeRunner{err: errors.New("boom")}
	if _, err := Invoke(runner, "status"); err == nil {
		t.Fatal("expected the runner error to propagate")
	}
}

func TestInvokeDecodesCodedError(t *testing.T) {
	runner := &fakeRunner{stdout: []byte(
		`{"ok":false,"error":{"code":"READINESS_TIMEOUT","detail":"timed out",` +
			`"remediation":"run tga doctor"}}`)}
	result, err := Invoke(runner, "up")
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if result.Error == nil || result.Error.Code != "READINESS_TIMEOUT" {
		t.Fatalf("unexpected error payload %+v", result.Error)
	}
}

// A coded failure must keep its identity when it travels as a Go error, so the
// top-level renderer can still print the remediation.
func TestProtocolErrorSurvivesErrorsAs(t *testing.T) {
	var err error = &protocol.Error{Code: "WSL_NOT_AVAILABLE", Detail: "no wsl"}
	var coded *protocol.Error
	if !errors.As(err, &coded) {
		t.Fatal("errors.As failed to recover the coded error")
	}
	if coded.Code != "WSL_NOT_AVAILABLE" {
		t.Fatalf("got code %q", coded.Code)
	}
	if !strings.Contains(err.Error(), "WSL_NOT_AVAILABLE") {
		t.Fatalf("message lost its code: %q", err.Error())
	}
}

// The worker splits its audiences: the result is one JSON object on stdout,
// human progress goes to stderr. Buffering both is what made `tga up
// --pull-images` print nothing while it fetched tens of gigabytes.
func TestProgressWriterShowsTheWorkerWhileItRuns(t *testing.T) {
	read, write, err := os.Pipe()
	if err != nil {
		t.Fatalf("pipe: %v", err)
	}
	original := os.Stderr
	os.Stderr = write
	defer func() { os.Stderr = original }()

	var kept bytes.Buffer
	if _, err := io.WriteString(progressWriter(&kept), "  [3/22] ctf-web-v1: pulling\n"); err != nil {
		t.Fatalf("write: %v", err)
	}
	if err := write.Close(); err != nil {
		t.Fatalf("close: %v", err)
	}

	shown, err := io.ReadAll(read)
	if err != nil {
		t.Fatalf("read: %v", err)
	}
	if !strings.Contains(string(shown), "ctf-web-v1") {
		t.Fatalf("progress never reached the terminal, got %q", shown)
	}
	// The error path still needs the text, so keeping it is not optional.
	if !strings.Contains(kept.String(), "ctf-web-v1") {
		t.Fatalf("progress was not kept for the error path, got %q", kept.String())
	}
}
