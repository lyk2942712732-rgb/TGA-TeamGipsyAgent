package runtime

import (
	"crypto/sha256"
	"encoding/hex"
	"fmt"
	"io"
	"net/http"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"strings"
	"time"

	"github.com/team-gipsy/tga/launcher/internal/protocol"
)

// ReleaseRepository is where the runtime filesystem is published. A fork can
// point the launcher at its own releases without rebuilding it.
const ReleaseRepository = "lyk2942712732-rgb/TGA-TeamGipsyAgent"

// RepositoryEnv overrides ReleaseRepository.
const RepositoryEnv = "TGA_RELEASE_REPOSITORY"

// DistroDirEnv overrides where the imported distribution's disk is written.
const DistroDirEnv = "TGA_DISTRO_DIR"

// downloadTimeout bounds the whole fetch. The runtime filesystem is hundreds
// of megabytes, so this is generous rather than tight -- the point is that a
// hung connection eventually fails instead of leaving `tga up` waiting forever.
const downloadTimeout = 60 * time.Minute

// InstallOptions describes which runtime to fetch and where to put it.
type InstallOptions struct {
	// Version is the launcher's own version, without the `tga-` tag prefix.
	// Launcher and runtime ship in one release, so a binary always knows which
	// filesystem it was built to talk to.
	Version string
	// Dir is where WSL writes the distribution's disk. Empty means the
	// per-user default.
	Dir string
	// Out receives progress. A download of this size with no output looks
	// like a hang.
	Out io.Writer
}

// EnsureRuntimeInstalled imports the TGA-Runtime distribution if it is absent.
//
// This is what makes `tga up` the only command a new machine needs. It runs
// only on Windows, only when there is no usable distribution, and only when
// there is no development checkout to prefer -- a contributor with a source
// tree should never have a rootfs downloaded behind their back.
func EnsureRuntimeInstalled(opts InstallOptions) error {
	if runtime.GOOS != "windows" {
		return nil
	}
	if DistroProvisioned(DistroName) {
		return nil
	}
	if _, _, ok := developmentCheckout(); ok {
		return nil
	}
	if !WSLAvailable() {
		return &protocol.Error{
			Code:   "WSL_NOT_AVAILABLE",
			Detail: "wsl.exe is not usable on this system",
			Remediation: "Enable WSL2 with `wsl --install` from an elevated " +
				"PowerShell, reboot, then run `tga up` again.",
		}
	}
	if DistroRegistered(DistroName) {
		// Registered but not provisioned: importing again would fail, and
		// silently replacing a distribution that may hold task data is not a
		// decision to make on the user's behalf.
		return &protocol.Error{
			Code:   "WSL_DISTRO_MISSING",
			Detail: DistroName + " is registered but carries no runtime at " + InternalBinary,
			Remediation: "Remove it with `wsl --unregister " + DistroName +
				"` and run `tga up` again to reimport it.",
		}
	}
	if err := checkReleaseVersion(opts.Version); err != nil {
		return err
	}
	return importRuntime(opts)
}

// checkReleaseVersion refuses to guess at a release.
//
// A launcher built from source has no matching runtime published anywhere, and
// picking some other version would download a filesystem this binary was never
// tested against.
func checkReleaseVersion(version string) error {
	if version != "" && version != "dev" {
		return nil
	}
	return &protocol.Error{
		Code:   "WSL_IMPORT_FAILED",
		Detail: "this launcher was built from source and has no release to fetch",
		Remediation: "Use a released `tga` binary, or import a runtime " +
			"yourself with `wsl --import " + DistroName + " <dir> <rootfs.tar.zst>`.",
	}
}

func importRuntime(opts InstallOptions) error {
	out := opts.Out
	if out == nil {
		out = io.Discard
	}
	repository := ReleaseRepository
	if override := strings.TrimSpace(os.Getenv(RepositoryEnv)); override != "" {
		repository = override
	}
	target := opts.Dir
	if target == "" {
		target = defaultDistroDir()
	}

	archive := fmt.Sprintf("TGA-Runtime-%s.tar.zst", opts.Version)
	base := fmt.Sprintf("https://github.com/%s/releases/download/tga-%s",
		repository, opts.Version)

	fmt.Fprintf(out, "TGA-Runtime is not installed. Fetching %s\n", opts.Version)

	expected, err := publishedDigest(base+"/SHA256SUMS.txt", archive)
	if err != nil {
		return &protocol.Error{
			Code:        "WSL_IMPORT_FAILED",
			Detail:      "could not read the published checksums: " + err.Error(),
			Remediation: "Check network access to github.com and retry `tga up`.",
		}
	}

	staging, err := os.MkdirTemp("", "tga-runtime-")
	if err != nil {
		return err
	}
	defer os.RemoveAll(staging)
	local := filepath.Join(staging, archive)

	measured, err := download(base+"/"+archive, local, out)
	if err != nil {
		return &protocol.Error{
			Code:        "WSL_IMPORT_FAILED",
			Detail:      "downloading the runtime filesystem failed: " + err.Error(),
			Remediation: "Check network access to github.com and retry `tga up`.",
		}
	}
	if measured != expected {
		// Not a warning: importing a filesystem that is not the published one
		// would hand the machine to whatever produced it.
		return &protocol.Error{
			Code: "WSL_IMPORT_FAILED",
			Detail: fmt.Sprintf("checksum mismatch for %s: got %s, expected %s",
				archive, measured, expected),
			Remediation: "Delete any proxy cache and retry; if it persists, " +
				"report it -- the published artifact and its checksum disagree.",
		}
	}

	if err := os.MkdirAll(target, 0o755); err != nil {
		return err
	}
	fmt.Fprintf(out, "Importing %s into %s\n", DistroName, target)
	cmd := exec.Command("wsl.exe", "--import", DistroName, target, local, "--version", "2")
	if output, err := cmd.CombinedOutput(); err != nil {
		return &protocol.Error{
			Code:   "WSL_IMPORT_FAILED",
			Detail: strings.TrimSpace(decodeWSLOutput(output)),
			Remediation: "Ensure WSL2 is up to date with `wsl --update`, then " +
				"run `tga up` again.",
		}
	}
	fmt.Fprintf(out, "%s is ready.\n", DistroName)
	return nil
}

// publishedDigest reads one entry out of a SHA256SUMS.txt.
//
// The checksum is fetched over the same TLS connection as the artifact, so it
// proves integrity of the transfer rather than provenance. Release artifacts
// are also cosign-signed for anyone who wants the stronger claim; verifying a
// Sigstore bundle is not something to do silently inside `tga up`.
func publishedDigest(url, artifact string) (string, error) {
	body, err := fetch(url)
	if err != nil {
		return "", err
	}
	defer body.Close()
	raw, err := io.ReadAll(io.LimitReader(body, 1<<20))
	if err != nil {
		return "", err
	}
	for _, line := range strings.Split(string(raw), "\n") {
		fields := strings.Fields(line)
		if len(fields) == 2 && strings.TrimPrefix(fields[1], "*") == artifact {
			return strings.ToLower(fields[0]), nil
		}
	}
	return "", fmt.Errorf("%s is not listed in SHA256SUMS.txt", artifact)
}

// download streams url to path, returning the sha256 it actually received.
func download(url, path string, out io.Writer) (string, error) {
	body, err := fetch(url)
	if err != nil {
		return "", err
	}
	defer body.Close()

	file, err := os.Create(path)
	if err != nil {
		return "", err
	}
	defer file.Close()

	digest := sha256.New()
	// Hashing what was written, rather than re-reading the file afterwards,
	// means the bytes checked are exactly the bytes stored.
	if _, err := io.Copy(io.MultiWriter(file, digest), &progress{reader: body, out: out}); err != nil {
		return "", err
	}
	if err := file.Sync(); err != nil {
		return "", err
	}
	return hex.EncodeToString(digest.Sum(nil)), nil
}

func fetch(url string) (io.ReadCloser, error) {
	client := &http.Client{Timeout: downloadTimeout}
	response, err := client.Get(url)
	if err != nil {
		return nil, err
	}
	if response.StatusCode != http.StatusOK {
		response.Body.Close()
		return nil, fmt.Errorf("%s returned %s", url, response.Status)
	}
	return response.Body, nil
}

// progress reports megabytes as they arrive. Several hundred megabytes with no
// output is indistinguishable from a hang.
type progress struct {
	reader   io.Reader
	out      io.Writer
	total    int64
	reported int64
}

func (p *progress) Read(buf []byte) (int, error) {
	n, err := p.reader.Read(buf)
	p.total += int64(n)
	const step = 32 << 20
	if p.total-p.reported >= step {
		p.reported = p.total
		fmt.Fprintf(p.out, "  %d MB\n", p.total>>20)
	}
	return n, err
}

func defaultDistroDir() string {
	if override := strings.TrimSpace(os.Getenv(DistroDirEnv)); override != "" {
		return override
	}
	// LOCALAPPDATA rather than APPDATA: the distribution's disk is machine
	// state, not something to sync into a roaming profile.
	if local := os.Getenv("LOCALAPPDATA"); local != "" {
		return filepath.Join(local, "TGA", "distro")
	}
	return filepath.Join(os.TempDir(), "TGA", "distro")
}
