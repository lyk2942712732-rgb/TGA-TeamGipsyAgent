// Package runtime locates the internal worker and runs it.
//
// The launcher's whole job is to make one command mean the same thing on both
// platforms. It does that by resolving a Runner: on Linux the worker is a
// local process, on Windows it lives inside the TGA-Runtime WSL2 distribution
// and every invocation is forwarded through wsl.exe. Callers above this
// package never branch on the operating system.
package runtime

import (
	"bytes"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"strings"

	"github.com/team-gipsy/tga/launcher/internal/protocol"
)

// DistroName is the dedicated WSL2 distribution the Windows launcher manages.
// A dedicated distro keeps TGA's Docker, gVisor and nftables configuration
// away from whatever the user already runs in Ubuntu.
const DistroName = "TGA-Runtime"

// InternalBinary is where the worker lives inside a provisioned runtime.
const InternalBinary = "/opt/tga/bin/tga-internal"

// Runner executes internal worker commands and returns their raw stdout.
type Runner interface {
	// Run executes the worker with args, returning stdout.
	Run(args ...string) ([]byte, error)
	// Describe names the execution surface, for diagnostics.
	Describe() string
	// Streaming runs the worker with stdio attached, for long-lived commands.
	Streaming(args ...string) error
}

// Resolve picks the correct Runner for this host.
func Resolve() (Runner, error) {
	if runtime.GOOS == "windows" {
		return resolveWindows()
	}
	return resolveNative()
}

// --- native (Linux, and Windows development checkouts) ---

type nativeRunner struct {
	command string
	prefix  []string
	label   string
}

func (r nativeRunner) Describe() string { return r.label }

func (r nativeRunner) argv(args []string) []string {
	return append(append([]string{}, r.prefix...), args...)
}

// progressWriter keeps the worker's stderr for the error path and shows it to
// the operator as it arrives.
//
// The worker splits its two audiences: the result goes to stdout as one JSON
// object, human progress to stderr. Buffering both meant `tga up
// --pull-images` printed nothing at all while it fetched tens of gigabytes --
// no image name, no rate, and no failure reason until the whole command had
// finished, which is indistinguishable from a hang.
func progressWriter(keep *bytes.Buffer) io.Writer {
	return io.MultiWriter(keep, os.Stderr)
}

func (r nativeRunner) Run(args ...string) ([]byte, error) {
	cmd := exec.Command(r.command, r.argv(args)...)
	var stdout, stderr bytes.Buffer
	cmd.Stdout = &stdout
	cmd.Stderr = progressWriter(&stderr)
	err := cmd.Run()
	if err != nil && stdout.Len() == 0 {
		return nil, fmt.Errorf("%s: %w: %s", r.command, err, strings.TrimSpace(stderr.String()))
	}
	return stdout.Bytes(), nil
}

func (r nativeRunner) Streaming(args ...string) error {
	cmd := exec.Command(r.command, r.argv(args)...)
	cmd.Stdout = os.Stdout
	cmd.Stderr = os.Stderr
	cmd.Stdin = os.Stdin
	return cmd.Run()
}

func resolveNative() (Runner, error) {
	if path, err := exec.LookPath("tga-internal"); err == nil {
		return nativeRunner{command: path, label: "linux/native"}, nil
	}
	if _, err := os.Stat(InternalBinary); err == nil {
		return nativeRunner{command: InternalBinary, label: "linux/native"}, nil
	}
	// Development checkout: run the module out of the repository.
	if python, root, ok := developmentCheckout(); ok {
		return nativeRunner{
			command: python,
			prefix:  []string{"-m", "tga.cli.internal"},
			label:   "development (" + root + ")",
		}, nil
	}
	return nil, errors.New("tga-internal was not found; is TGA installed?")
}

// developmentCheckout finds a repository-local interpreter so the launcher can
// be exercised before any packaging exists.
func developmentCheckout() (python string, root string, ok bool) {
	start, err := os.Getwd()
	if err != nil {
		return "", "", false
	}
	if fromEnv := os.Getenv("TGA_DEV_ROOT"); fromEnv != "" {
		start = fromEnv
	}
	dir := start
	for i := 0; i < 6; i++ {
		if _, err := os.Stat(filepath.Join(dir, "tga", "cli", "internal.py")); err == nil {
			for _, candidate := range []string{
				filepath.Join(dir, ".venv", "Scripts", "python.exe"),
				filepath.Join(dir, ".venv", "bin", "python"),
			} {
				if _, err := os.Stat(candidate); err == nil {
					return candidate, dir, true
				}
			}
			if path, err := exec.LookPath("python"); err == nil {
				return path, dir, true
			}
		}
		parent := filepath.Dir(dir)
		if parent == dir {
			break
		}
		dir = parent
	}
	return "", "", false
}

// --- Windows (WSL2 forwarding) ---

type wslRunner struct {
	distro string
}

func (r wslRunner) Describe() string { return "windows -> wsl:" + r.distro }

func (r wslRunner) argv(args []string) []string {
	return append([]string{"-d", r.distro, "--", InternalBinary}, args...)
}

func (r wslRunner) Run(args ...string) ([]byte, error) {
	cmd := exec.Command("wsl.exe", r.argv(args)...)
	var stdout, stderr bytes.Buffer
	cmd.Stdout = &stdout
	cmd.Stderr = progressWriter(&stderr)
	err := cmd.Run()
	if err != nil && stdout.Len() == 0 {
		return nil, fmt.Errorf("wsl -d %s: %w: %s", r.distro, err, strings.TrimSpace(stderr.String()))
	}
	return stdout.Bytes(), nil
}

func (r wslRunner) Streaming(args ...string) error {
	cmd := exec.Command("wsl.exe", r.argv(args)...)
	cmd.Stdout = os.Stdout
	cmd.Stderr = os.Stderr
	cmd.Stdin = os.Stdin
	return cmd.Run()
}

// ModeEnv forces a specific execution surface, overriding auto-detection.
// Values: "wsl" (always forward to the distribution) or "native" (always run
// the local checkout). Unset means: prefer a provisioned distribution, and
// fall back to a development checkout.
const ModeEnv = "TGA_RUNTIME_MODE"

func resolveWindows() (Runner, error) {
	mode := strings.ToLower(strings.TrimSpace(os.Getenv(ModeEnv)))

	if mode != "wsl" {
		// A provisioned distribution is the real deployment and outranks a
		// checkout; without one, a development tree still lets the whole
		// stack be exercised before packaging exists.
		provisioned := mode == "native" || !(WSLAvailable() && DistroProvisioned(DistroName))
		if provisioned {
			if python, root, ok := developmentCheckout(); ok {
				return nativeRunner{
					command: python,
					prefix:  []string{"-m", "tga.cli.internal"},
					label:   "windows/development (" + root + ")",
				}, nil
			}
		}
	}
	if mode == "native" {
		return nil, errors.New("TGA_RUNTIME_MODE=native but no development checkout was found")
	}
	if !WSLAvailable() {
		return nil, &protocol.Error{
			Code:   "WSL_NOT_AVAILABLE",
			Detail: "wsl.exe is not usable on this system",
			Remediation: "Enable WSL2 with `wsl --install` from an elevated " +
				"PowerShell, reboot, then run `tga up` again.",
		}
	}
	if !DistroRegistered(DistroName) {
		return nil, &protocol.Error{
			Code:   "WSL_DISTRO_MISSING",
			Detail: DistroName + " is not registered",
			// `tga up` imports it; there is no separate install command, and
			// this used to point at one that never existed.
			Remediation: "Run `tga up`, which imports the TGA-Runtime " +
				"distribution on first use.",
		}
	}
	return wslRunner{distro: DistroName}, nil
}

// WSLAvailable reports whether wsl.exe answers at all.
func WSLAvailable() bool {
	if _, err := exec.LookPath("wsl.exe"); err != nil {
		return false
	}
	return exec.Command("wsl.exe", "--status").Run() == nil
}

// DistroProvisioned reports whether a distribution is not merely registered
// but actually carries a usable TGA runtime. A distro imported but never
// provisioned would otherwise be preferred over a working checkout.
func DistroProvisioned(name string) bool {
	if !DistroRegistered(name) {
		return false
	}
	return exec.Command("wsl.exe", "-d", name, "--", "test", "-x", InternalBinary).Run() == nil
}

// DistroRegistered reports whether a distribution exists locally.
func DistroRegistered(name string) bool {
	output, err := exec.Command("wsl.exe", "--list", "--quiet").Output()
	if err != nil {
		return false
	}
	for _, line := range strings.Split(decodeWSLOutput(output), "\n") {
		if strings.EqualFold(strings.TrimSpace(line), name) {
			return true
		}
	}
	return false
}

// decodeWSLOutput converts wsl.exe's UTF-16LE output to UTF-8.
//
// wsl.exe writes UTF-16 regardless of the console code page, so a naive
// string() yields text interleaved with NUL bytes and no comparison matches.
func decodeWSLOutput(raw []byte) string {
	if len(raw) >= 2 && raw[0] == 0xFF && raw[1] == 0xFE {
		raw = raw[2:]
	}
	nulls := 0
	for _, b := range raw {
		if b == 0 {
			nulls++
		}
	}
	if nulls*3 < len(raw) {
		return string(raw)
	}
	decoded := make([]rune, 0, len(raw)/2)
	for i := 0; i+1 < len(raw); i += 2 {
		decoded = append(decoded, rune(uint16(raw[i])|uint16(raw[i+1])<<8))
	}
	return strings.TrimRight(string(decoded), "\x00")
}

// Invoke runs a worker command in JSON mode and decodes the result.
func Invoke(r Runner, args ...string) (*protocol.Result, error) {
	raw, err := r.Run(append(args, "--json")...)
	if err != nil {
		return nil, err
	}
	trimmed := bytes.TrimSpace(raw)
	if len(trimmed) == 0 {
		return nil, errors.New("the internal runtime returned no output")
	}
	// Tolerate leading log noise by decoding the last JSON line.
	if idx := bytes.LastIndexByte(trimmed, '\n'); idx >= 0 {
		if json.Valid(trimmed[idx+1:]) {
			trimmed = trimmed[idx+1:]
		}
	}
	return protocol.Parse(trimmed)
}
