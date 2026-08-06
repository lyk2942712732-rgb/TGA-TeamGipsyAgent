package runtime

import (
	"crypto/sha256"
	"encoding/hex"
	"io"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestPublishedDigestFindsTheRequestedArtifact(t *testing.T) {
	body := strings.Join([]string{
		"aaaa  tga-v0.1.0-linux-amd64",
		"bbbb  TGA-Runtime-v0.1.0.tar.zst",
		"cccc  tga-v0.1.0-windows-amd64.exe",
	}, "\n")
	server := serving(t, map[string]string{"/SHA256SUMS.txt": body})

	digest, err := publishedDigest(server.URL+"/SHA256SUMS.txt", "TGA-Runtime-v0.1.0.tar.zst")
	if err != nil {
		t.Fatalf("publishedDigest: %v", err)
	}
	if digest != "bbbb" {
		t.Fatalf("got %q, want the line for the runtime archive", digest)
	}
}

func TestPublishedDigestAcceptsBinaryMarkedEntries(t *testing.T) {
	// sha256sum writes `*name` for entries it read in binary mode; a checksum
	// file produced that way must not read as "artifact not listed".
	server := serving(t, map[string]string{"/S": "dddd *TGA-Runtime-v0.1.0.tar.zst\n"})

	digest, err := publishedDigest(server.URL+"/S", "TGA-Runtime-v0.1.0.tar.zst")
	if err != nil {
		t.Fatalf("publishedDigest: %v", err)
	}
	if digest != "dddd" {
		t.Fatalf("got %q", digest)
	}
}

func TestPublishedDigestReportsAnAbsentArtifact(t *testing.T) {
	server := serving(t, map[string]string{"/S": "aaaa  something-else\n"})

	if _, err := publishedDigest(server.URL+"/S", "TGA-Runtime-v0.1.0.tar.zst"); err == nil {
		t.Fatal("expected an error when the artifact is not listed")
	}
}

func TestPublishedDigestReportsAMissingChecksumFile(t *testing.T) {
	server := serving(t, nil)

	_, err := publishedDigest(server.URL+"/absent", "anything")
	if err == nil || !strings.Contains(err.Error(), "404") {
		t.Fatalf("expected the HTTP status in the error, got %v", err)
	}
}

func TestDownloadReturnsTheDigestOfWhatItWrote(t *testing.T) {
	payload := strings.Repeat("runtime filesystem ", 5000)
	server := serving(t, map[string]string{"/rootfs": payload})
	target := filepath.Join(t.TempDir(), "rootfs.tar.zst")

	measured, err := download(server.URL+"/rootfs", target, io.Discard)
	if err != nil {
		t.Fatalf("download: %v", err)
	}

	sum := sha256.Sum256([]byte(payload))
	if measured != hex.EncodeToString(sum[:]) {
		t.Fatal("the reported digest does not describe the payload")
	}
	// The digest must describe the bytes on disk, not merely the bytes seen.
	stored, err := os.ReadFile(target)
	if err != nil {
		t.Fatalf("reading back: %v", err)
	}
	if string(stored) != payload {
		t.Fatal("the stored file does not match what was served")
	}
}

func TestDownloadSurfacesAServerError(t *testing.T) {
	server := serving(t, nil)

	if _, err := download(server.URL+"/absent", filepath.Join(t.TempDir(), "x"), io.Discard); err == nil {
		t.Fatal("expected an error for a 404")
	}
}

func TestProgressReportsWithoutSwallowingBytes(t *testing.T) {
	var out strings.Builder
	payload := strings.Repeat("x", 96<<20)
	reader := &progress{reader: strings.NewReader(payload), out: &out}

	read, err := io.ReadAll(reader)
	if err != nil {
		t.Fatalf("reading: %v", err)
	}
	if len(read) != len(payload) {
		t.Fatalf("progress dropped bytes: got %d of %d", len(read), len(payload))
	}
	if !strings.Contains(out.String(), "MB") {
		t.Fatal("a download of this size must report progress")
	}
}

func TestDistroDirectoryCanBeOverridden(t *testing.T) {
	t.Setenv(DistroDirEnv, filepath.Join("D:", "tga-distro"))

	if got := defaultDistroDir(); got != filepath.Join("D:", "tga-distro") {
		t.Fatalf("got %q", got)
	}
}

func TestAnUnreleasedLauncherRefusesToGuessAtARuntime(t *testing.T) {
	// Tested here rather than through EnsureRuntimeInstalled: that function
	// probes WSL first, so on a machine that already has the distribution it
	// returns before ever reaching this decision, and the test would pass
	// without exercising it.
	for _, version := range []string{"", "dev"} {
		if err := checkReleaseVersion(version); err == nil {
			t.Fatalf("version %q must not be treated as a release", version)
		} else if !strings.Contains(err.Error(), "built from source") {
			t.Fatalf("unhelpful error for %q: %v", version, err)
		}
	}
	if err := checkReleaseVersion("v0.1.0"); err != nil {
		t.Fatalf("a real version must be accepted: %v", err)
	}
}

func serving(t *testing.T, routes map[string]string) *httptest.Server {
	t.Helper()
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		body, ok := routes[r.URL.Path]
		if !ok {
			http.NotFound(w, r)
			return
		}
		io.WriteString(w, body)
	}))
	t.Cleanup(server.Close)
	return server
}
